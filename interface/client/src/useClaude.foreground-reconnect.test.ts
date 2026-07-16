import type { ForegroundRecoveryScheduler } from './useClaude';

Object.defineProperty(globalThis, 'window', {
  configurable: true,
  value: { location: { protocol: 'http:', host: 'test.local' } },
});

const {
  FOREGROUND_STATE_RESPONSE_TIMEOUT_MS,
  ForegroundSocketRecovery,
  detachWebSocketHandlers,
  isActiveWebSocket,
} = await import('./useClaude');

class ManualScheduler implements ForegroundRecoveryScheduler {
  private now = 0;
  private nextId = 1;
  private tasks = new Map<number, { due: number; callback: () => void }>();

  setTimeout = (callback: () => void, delay: number): ReturnType<typeof setTimeout> => {
    const id = this.nextId++;
    this.tasks.set(id, { due: this.now + delay, callback });
    return id;
  };

  clearTimeout = (timeout: ReturnType<typeof setTimeout>): void => {
    this.tasks.delete(Number(timeout));
  };

  advance(milliseconds: number): void {
    const target = this.now + milliseconds;
    while (true) {
      const next = [...this.tasks.entries()]
        .filter(([, task]) => task.due <= target)
        .sort((left, right) => left[1].due - right[1].due || left[0] - right[0])[0];
      if (!next) break;
      const [id, task] = next;
      this.tasks.delete(id);
      this.now = task.due;
      task.callback();
    }
    this.now = target;
  }

  pendingCount(): number {
    return this.tasks.size;
  }
}

class FakeSocket {
  onopen: (() => void) | null = null;
  onmessage: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  readonly sent: string[] = [];
  closeCount = 0;

  constructor(public readyState: number) {}

  send(payload: string): void {
    this.sent.push(payload);
  }

  close(): void {
    this.closeCount++;
    this.readyState = 3;
  }
}

class RecoveryHarness {
  readonly scheduler = new ManualScheduler();
  readonly recovery = new ForegroundSocketRecovery<FakeSocket>(this.scheduler);
  activeSocket: FakeSocket | null;
  enabled = true;
  visible = true;
  sessionId = 'session-a';
  connectCount = 0;
  replacementCount = 0;
  subscribeCount = 0;
  throwOnSubscribe = false;

  constructor(readyState: number | null = 1) {
    this.activeSocket = readyState === null ? null : new FakeSocket(readyState);
  }

  connect = (): void => {
    if (!this.enabled) return;
    if (this.activeSocket?.readyState === 0 || this.activeSocket?.readyState === 1) return;
    this.connectCount++;
    this.activeSocket = new FakeSocket(0);
  };

  replaceSocket = (socket: FakeSocket): void => {
    if (this.activeSocket !== socket || !this.enabled) return;
    this.replacementCount++;
    socket.close();
    this.activeSocket = null;
    this.connect();
  };

  foreground(): string {
    const socket = this.activeSocket;
    const sessionId = this.sessionId;
    return this.recovery.handleForeground({
      enabled: this.enabled,
      visible: this.visible,
      socket,
      sessionId,
      isStillCurrent: () => (
        this.enabled &&
        this.visible &&
        this.activeSocket === socket &&
        this.sessionId === sessionId
      ),
      sendSubscribe: (target, targetSessionId) => {
        if (this.throwOnSubscribe) throw new Error('send failed');
        this.subscribeCount++;
        target.send(JSON.stringify({ action: 'subscribe', sessionId: targetSessionId }));
      },
      connect: this.connect,
      replaceSocket: this.replaceSocket,
    });
  }
}

const assert = (condition: unknown, message: string): void => {
  if (!condition) throw new Error(message);
};

const run = (name: string, scenario: () => void): void => {
  scenario();
  console.log(`PASS ${name}`);
};

run('zombie OPEN socket is replaced once at the bounded deadline', () => {
  const harness = new RecoveryHarness(1);
  const zombie = harness.activeSocket;
  assert(harness.foreground() === 'probe', 'foreground should probe an OPEN socket');
  assert(harness.subscribeCount === 1, 'probe should subscribe immediately');
  assert(harness.recovery.hasForegroundProbe(zombie ?? undefined, harness.sessionId), 'probe timer should be owned by the exact socket/session');

  harness.scheduler.advance(FOREGROUND_STATE_RESPONSE_TIMEOUT_MS - 1);
  assert(harness.replacementCount === 0, 'socket must survive before the deadline');
  harness.scheduler.advance(1);
  assert(harness.replacementCount === 1, 'deadline should replace the zombie exactly once');
  assert(zombie?.closeCount === 1, 'only the zombie should be closed');
  assert(harness.connectCount === 1 && harness.activeSocket?.readyState === 0, 'one fresh connection should follow');
  assert(!harness.recovery.hasForegroundProbe(), 'fired probe must be nulled');
  harness.scheduler.advance(FOREGROUND_STATE_RESPONSE_TIMEOUT_MS * 2);
  assert(harness.replacementCount === 1 && harness.connectCount === 1, 'expired timer must not fire twice');
});

run('healthy foreground state clears the deadline and repeated signals coalesce', () => {
  const harness = new RecoveryHarness(1);
  const healthy = harness.activeSocket;
  harness.foreground();
  assert(harness.foreground() === 'probe-pending', 'a repeated visible signal should reuse the pending probe');
  assert(harness.subscribeCount === 1, 'coalesced foreground signals should send one subscribe');
  assert(healthy !== null && harness.recovery.acknowledgeForegroundState(healthy, harness.sessionId), 'matching state should acknowledge the probe');
  harness.scheduler.advance(FOREGROUND_STATE_RESPONSE_TIMEOUT_MS);
  assert(harness.replacementCount === 0 && harness.connectCount === 0, 'healthy socket must not reconnect');
  assert(harness.scheduler.pendingCount() === 0, 'acknowledged deadline must be cleared');
});

run('non-matching state cannot clear another session probe', () => {
  const harness = new RecoveryHarness(1);
  const socket = harness.activeSocket;
  harness.foreground();
  assert(socket !== null && !harness.recovery.acknowledgeForegroundState(socket, 'session-b'), 'wrong session must not acknowledge');
  harness.scheduler.advance(FOREGROUND_STATE_RESPONSE_TIMEOUT_MS);
  assert(harness.replacementCount === 1, 'unacknowledged exact-session probe should still recover');
});

run('closing, closed, or missing sockets connect immediately and cancel pending backoff', () => {
  for (const initialState of [2, 3, null]) {
    const harness = new RecoveryHarness(initialState);
    let backoffCalls = 0;
    harness.recovery.scheduleReconnect(3000, () => { backoffCalls++; });
    assert(harness.foreground() === 'connect', 'closing/closed/missing socket should connect on foreground');
    assert(harness.connectCount === 1, 'foreground should create one connection immediately');
    assert(!harness.recovery.hasReconnectTimer(), 'immediate connect should clear backoff ownership');
    harness.scheduler.advance(3000);
    assert(backoffCalls === 0 && harness.connectCount === 1, 'cleared backoff must not create another socket');
  }
});

run('connecting socket wins over a pending backoff without churn', () => {
  const harness = new RecoveryHarness(0);
  let backoffCalls = 0;
  harness.recovery.scheduleReconnect(3000, () => { backoffCalls++; });
  assert(harness.foreground() === 'wait', 'foreground should wait for CONNECTING');
  assert(!harness.recovery.hasReconnectTimer(), 'obsolete backoff should be cleared');
  harness.scheduler.advance(3000);
  assert(backoffCalls === 0 && harness.connectCount === 0 && harness.replacementCount === 0, 'CONNECTING socket must not be replaced');
});

run('stale socket guards and handler detachment block superseded callbacks', () => {
  const active = new FakeSocket(1);
  const stale = new FakeSocket(1);
  stale.onopen = () => undefined;
  stale.onmessage = () => undefined;
  stale.onerror = () => undefined;
  stale.onclose = () => undefined;
  assert(!isActiveWebSocket(active as unknown as WebSocket, stale as unknown as WebSocket, true), 'stale socket must fail identity guard');
  assert(!isActiveWebSocket(active as unknown as WebSocket, active as unknown as WebSocket, false), 'disabled hook must fail identity guard');
  detachWebSocketHandlers(stale as unknown as WebSocket);
  assert(stale.onopen === null && stale.onmessage === null && stale.onerror === null && stale.onclose === null, 'all four stale handlers must detach');
});

run('cleanup clears both timer owners and prevents later work', () => {
  const harness = new RecoveryHarness(1);
  harness.foreground();
  let backoffCalls = 0;
  harness.recovery.scheduleReconnect(3000, () => { backoffCalls++; });
  assert(harness.scheduler.pendingCount() === 2, 'probe and backoff should be independently owned');
  harness.recovery.cleanup();
  assert(!harness.recovery.hasForegroundProbe() && !harness.recovery.hasReconnectTimer(), 'cleanup should null both timer refs');
  harness.scheduler.advance(5000);
  assert(harness.replacementCount === 0 && backoffCalls === 0, 'cleaned timers must not perform recovery');
});

run('disabled hooks and stale-session deadlines perform no recovery work', () => {
  const disabled = new RecoveryHarness(1);
  disabled.enabled = false;
  assert(disabled.foreground() === 'ignored', 'disabled foreground should be ignored');
  assert(disabled.subscribeCount === 0 && disabled.scheduler.pendingCount() === 0, 'disabled hook must own no recovery timer');

  const switched = new RecoveryHarness(1);
  switched.foreground();
  switched.sessionId = 'session-b';
  switched.scheduler.advance(FOREGROUND_STATE_RESPONSE_TIMEOUT_MS);
  assert(switched.replacementCount === 0, 'deadline for a superseded session must not replace the shared socket');
});

run('split instances recover independently', () => {
  const primary = new RecoveryHarness(1);
  const secondary = new RecoveryHarness(1);
  secondary.sessionId = 'session-b';
  primary.foreground();
  secondary.foreground();
  const secondarySocket = secondary.activeSocket;
  assert(secondarySocket !== null && secondary.recovery.acknowledgeForegroundState(secondarySocket, secondary.sessionId), 'secondary should acknowledge its own state');
  primary.scheduler.advance(FOREGROUND_STATE_RESPONSE_TIMEOUT_MS);
  secondary.scheduler.advance(FOREGROUND_STATE_RESPONSE_TIMEOUT_MS);
  assert(primary.replacementCount === 1, 'primary zombie should recover');
  assert(secondary.replacementCount === 0, 'healthy secondary should remain connected');
});

run('synchronous foreground send failure replaces only the current socket once', () => {
  const harness = new RecoveryHarness(1);
  const failed = harness.activeSocket;
  harness.throwOnSubscribe = true;
  harness.foreground();
  assert(harness.replacementCount === 1 && failed?.closeCount === 1, 'failed send should retire the exact socket');
  assert(harness.connectCount === 1 && harness.scheduler.pendingCount() === 0, 'failed send should create one replacement without a leaked probe');
});

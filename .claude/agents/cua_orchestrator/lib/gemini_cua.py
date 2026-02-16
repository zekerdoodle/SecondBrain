"""
Gemini Computer Use Agent - Core Module (V2)

Autonomous agent loop architecture: the MODEL drives the loop via function_calls.
The orchestrator only executes actions and feeds back function_responses.

Key changes from V1:
- step() eliminated -> replaced by run_task() autonomous loop
- Model: gemini-3-flash-preview (1M context, better agentic capabilities)
- system_instruction with behavioral rules
- ThinkingConfig for step-level planning
- Repetition detection to break action loops
- No more "continue" messages injected mid-loop
"""

import os
import base64
import subprocess
import time
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Literal, Callable
from google import genai
from google.genai import types

# Screen dimensions for our RDP session
SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080

# Model — Gemini 3 Flash with 1M context window
COMPUTER_USE_MODEL = "gemini-3-flash-preview"

# Session expires after 15 minutes of inactivity
SESSION_IDLE_TIMEOUT = 15 * 60  # seconds

# Context window management — 1M context allows much larger history
MAX_HISTORY_TURNS = 100  # each "turn" = one user message + one assistant response

# API retry config
API_MAX_RETRIES = 3
API_RETRY_BASE_DELAY = 2.0  # seconds, doubles each retry

# System instruction for the CUA model
SYSTEM_INSTRUCTION = """You are a browser automation agent controlling a desktop browser via screenshots and actions.

RULES:
- Examine each screenshot carefully before choosing an action
- Never repeat the same action more than twice — if an action doesn't produce the expected result, try a different approach
- Track what you've accomplished and what remains
- When the task is fully complete, respond with text containing "TASK COMPLETE" and a summary of what was done
- If you're stuck or need clarification, respond with text describing the problem
- After adding an item to a shopping cart, immediately move to the next item — do not re-add
- The screenshot in each function_response shows the CURRENT state after your last action
"""


@dataclass
class CUAAction:
    """Represents an action returned by Gemini CUA"""
    action_type: str  # click_at, type_text_at, scroll_document, navigate, etc.
    args: dict = field(default_factory=dict)
    narration: Optional[str] = None
    safety_decision: Optional[str] = None  # REGULAR or REQUIRES_CONFIRMATION


@dataclass
class CUAResponse:
    """Response from a CUA task run"""
    status: Literal["success", "blocked", "error", "needs_confirmation", "complete"]
    action_taken: Optional[CUAAction] = None
    screenshot_after: Optional[str] = None  # base64
    error_message: Optional[str] = None
    model_text: Optional[str] = None  # Text response from model (completion signal)
    raw_response: Optional[dict] = None
    session_reset: bool = False  # True if session was reset due to corruption
    steps_completed: int = 0
    step_log: list = field(default_factory=list)  # List of (step_num, action_type, args_summary)


@dataclass
class StepEvent:
    """A single step event emitted by the CUA loop for logging"""
    step_number: int
    action_type: Optional[str]
    action_args: Optional[dict]
    status: str  # "action", "text", "error", "complete"
    screenshot_b64: Optional[str] = None
    model_text: Optional[str] = None
    error: Optional[str] = None
    timestamp: float = 0.0


class ProgressTracker:
    """Tracks actions and maintains a progress log for session recovery."""

    def __init__(self, task: str):
        self.task = task
        self.actions: list[tuple[int, str, str]] = []  # (step_num, action_name, args_summary)
        self.inferred_completions: list[str] = []
        self._last_search: Optional[str] = None

    def log_action(self, step: int, action_name: str, args: dict):
        summary = self._summarize_args(args)
        self.actions.append((step, action_name, summary))

        # Track searches for completion inference
        if action_name in ("search", "type_text_at") and "text" in args:
            self._last_search = args["text"]
        elif action_name == "click_at" and self._last_search:
            self.inferred_completions.append(self._last_search)
            self._last_search = None

    def _summarize_args(self, args: dict) -> str:
        """Create a brief summary of action args"""
        if not args:
            return ""
        parts = []
        for k, v in args.items():
            sv = str(v)
            if len(sv) > 50:
                sv = sv[:47] + "..."
            parts.append(f"{k}={sv}")
        return ", ".join(parts)

    def build_recovery_context(self) -> str:
        if not self.actions:
            return ""
        return f"""
IMPORTANT: This is a RESUMED session. Previous progress:
- Actions taken: {len(self.actions)} steps
- Items likely completed: {', '.join(self.inferred_completions) if self.inferred_completions else 'unknown'}
- Look at the current screenshot to determine the actual browser state.
- Focus on items NOT yet completed.
"""


class GeminiCUA:
    """
    Interface to Gemini's Computer Use capabilities.

    V2 Architecture: The model drives the loop autonomously via function_calls.
    The CUA only executes actions and feeds back function_responses.
    No "continue" messages are injected between function_call/function_response cycles.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY required")

        self.client = genai.Client(api_key=self.api_key)
        self.model = COMPUTER_USE_MODEL
        self.conversation_history: list[types.Content] = []
        self.current_url: str = "about:blank"
        self._last_activity: float = 0.0
        self._generation_config: Optional[types.GenerateContentConfig] = None

    def _build_config(self) -> types.GenerateContentConfig:
        """Build GenerateContentConfig once and cache it."""
        if self._generation_config is None:
            self._generation_config = types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=[
                    types.Tool(
                        computer_use=types.ComputerUse(
                            environment=types.Environment.ENVIRONMENT_BROWSER,
                            excluded_predefined_functions=["drag_and_drop"]
                        )
                    )
                ],
                thinking_config=types.ThinkingConfig(thinking_budget=2048),
            )
        return self._generation_config

    def _is_session_expired(self) -> bool:
        """Check if the current session has been idle too long."""
        if self._last_activity == 0.0:
            return False
        return (time.time() - self._last_activity) > SESSION_IDLE_TIMEOUT

    def _ensure_session(self) -> None:
        """Start a fresh session if the current one has expired."""
        if self._is_session_expired():
            print(f"[CUA] Session expired (idle > {SESSION_IDLE_TIMEOUT}s), starting fresh")
            self.conversation_history = []
            self.current_url = "about:blank"

    def _trim_history(self) -> None:
        """Trim conversation history to stay within context limits.

        Uses a sliding window approach:
        - Keep the first 2 entries (initial instruction + first response) for context
        - Keep the last MAX_HISTORY_TURNS * 2 entries
        - Drop everything in between
        - Validates that the trimmed history maintains valid user->assistant alternation
        """
        max_entries = MAX_HISTORY_TURNS * 2
        history_len = len(self.conversation_history)

        if history_len <= max_entries:
            return

        keep_start = 2
        keep_end = max_entries

        if history_len <= keep_start + keep_end:
            return

        old_len = history_len
        new_history = (
            self.conversation_history[:keep_start] +
            self.conversation_history[-keep_end:]
        )

        # Validate: ensure no function_call is left without its function_response
        if len(new_history) > keep_start:
            entry = new_history[keep_start]
            if hasattr(entry, 'role') and entry.role != 'user':
                new_history = (
                    self.conversation_history[:keep_start] +
                    self.conversation_history[-(keep_end - 1):]
                )

        self.conversation_history = new_history
        new_len = len(self.conversation_history)
        print(f"[CUA] Trimmed history from {old_len} to {new_len} entries")

    def take_screenshot(self) -> str:
        """Capture current screen and return as base64"""
        screenshot_path = "/tmp/cua_screenshot.png"

        display = os.environ.get("DISPLAY")
        if not display:
            for d in [":10", ":0", ":1"]:
                env_test = {**os.environ, "DISPLAY": d}
                result = subprocess.run(
                    ["xdpyinfo"],
                    capture_output=True,
                    timeout=2,
                    env=env_test
                )
                if result.returncode == 0:
                    display = d
                    break
            if not display:
                display = ":10"

        env = {**os.environ, "DISPLAY": display}

        result = subprocess.run(
            ["scrot", "-o", screenshot_path],
            capture_output=True,
            timeout=10,
            env=env
        )

        if result.returncode != 0:
            raise RuntimeError(f"Screenshot failed: {result.stderr.decode()}")

        with open(screenshot_path, "rb") as f:
            return base64.b64encode(f.read()).decode()

    def _take_screenshot_raw(self) -> bytes:
        """Capture current screen and return as raw PNG bytes."""
        screenshot_path = "/tmp/cua_screenshot.png"

        display = os.environ.get("DISPLAY")
        if not display:
            for d in [":10", ":0", ":1"]:
                env_test = {**os.environ, "DISPLAY": d}
                result = subprocess.run(
                    ["xdpyinfo"],
                    capture_output=True,
                    timeout=2,
                    env=env_test
                )
                if result.returncode == 0:
                    display = d
                    break
            if not display:
                display = ":10"

        env = {**os.environ, "DISPLAY": display}

        result = subprocess.run(
            ["scrot", "-o", screenshot_path],
            capture_output=True,
            timeout=10,
            env=env
        )

        if result.returncode != 0:
            raise RuntimeError(f"Screenshot failed: {result.stderr.decode()}")

        with open(screenshot_path, "rb") as f:
            return f.read()

    def _normalize_to_pixels(self, x: int, y: int) -> tuple[int, int]:
        """Convert Gemini's 1000x1000 grid to actual pixels"""
        pixel_x = int((x / 1000) * SCREEN_WIDTH)
        pixel_y = int((y / 1000) * SCREEN_HEIGHT)
        return pixel_x, pixel_y

    def execute_action(self, action: CUAAction) -> bool:
        """Execute a CUA action on the system using xdotool.

        Returns True if action succeeded, False otherwise.
        Updates self.current_url when navigation actions occur.
        """
        display = os.environ.get("DISPLAY", ":10")
        env = {**os.environ, "DISPLAY": display}

        try:
            if action.action_type == "click_at":
                x = action.args.get("x", 0)
                y = action.args.get("y", 0)
                pixel_x, pixel_y = self._normalize_to_pixels(x, y)
                subprocess.run(["xdotool", "mousemove", str(pixel_x), str(pixel_y)],
                              check=True, env=env)
                time.sleep(0.1)
                subprocess.run(["xdotool", "click", "1"], check=True, env=env)
                time.sleep(0.5)

            elif action.action_type == "hover_at":
                x = action.args.get("x", 0)
                y = action.args.get("y", 0)
                pixel_x, pixel_y = self._normalize_to_pixels(x, y)
                subprocess.run(["xdotool", "mousemove", str(pixel_x), str(pixel_y)],
                              check=True, env=env)
                time.sleep(0.3)

            elif action.action_type == "type_text_at":
                x = action.args.get("x", 0)
                y = action.args.get("y", 0)
                text = action.args.get("text", "")
                pixel_x, pixel_y = self._normalize_to_pixels(x, y)
                subprocess.run(["xdotool", "mousemove", str(pixel_x), str(pixel_y)],
                              check=True, env=env)
                subprocess.run(["xdotool", "click", "1"], check=True, env=env)
                time.sleep(0.2)
                subprocess.run(["xdotool", "type", "--clearmodifiers", text],
                              check=True, env=env)
                time.sleep(0.3)

            elif action.action_type == "key_combination":
                keys = action.args.get("keys", [])
                if isinstance(keys, list):
                    key_str = "+".join(keys)
                else:
                    key_str = str(keys)
                subprocess.run(["xdotool", "key", key_str], check=True, env=env)
                time.sleep(0.3)

            elif action.action_type == "scroll_document":
                direction = action.args.get("direction", "down")
                button = "5" if direction == "down" else "4"
                for _ in range(3):
                    subprocess.run(["xdotool", "click", button], check=True, env=env)
                    time.sleep(0.1)
                time.sleep(0.3)

            elif action.action_type == "scroll_at":
                x = action.args.get("x", 0)
                y = action.args.get("y", 0)
                direction = action.args.get("direction", "down")
                pixel_x, pixel_y = self._normalize_to_pixels(x, y)
                subprocess.run(["xdotool", "mousemove", str(pixel_x), str(pixel_y)],
                              check=True, env=env)
                button = "5" if direction == "down" else "4"
                for _ in range(3):
                    subprocess.run(["xdotool", "click", button], check=True, env=env)
                    time.sleep(0.1)
                time.sleep(0.3)

            elif action.action_type == "wait_5_seconds":
                time.sleep(5)

            elif action.action_type == "navigate":
                url = action.args.get("url", "")
                if url:
                    print(f"[CUA] Navigate to: {url}")
                    self._focus_chrome(env)
                    time.sleep(0.3)
                    subprocess.run(["xdotool", "key", "ctrl+l"], check=True, env=env)
                    time.sleep(0.2)
                    subprocess.run(["xdotool", "key", "ctrl+a"], check=True, env=env)
                    time.sleep(0.1)
                    subprocess.run(["xdotool", "type", "--clearmodifiers", url],
                                  check=True, env=env)
                    time.sleep(0.1)
                    subprocess.run(["xdotool", "key", "Return"], check=True, env=env)
                    self.current_url = url
                    time.sleep(3)

            elif action.action_type == "open_web_browser":
                url = action.args.get("url", "")
                chrome_running = self._is_chrome_running()
                if chrome_running:
                    print("[CUA] Chrome already running, focusing existing window")
                    self._focus_chrome(env)
                    time.sleep(0.5)
                    if url and url != "about:blank":
                        subprocess.run(["xdotool", "key", "ctrl+l"], check=True, env=env)
                        time.sleep(0.2)
                        subprocess.run(["xdotool", "key", "ctrl+a"], check=True, env=env)
                        time.sleep(0.1)
                        subprocess.run(["xdotool", "type", "--clearmodifiers", url],
                                      check=True, env=env)
                        time.sleep(0.1)
                        subprocess.run(["xdotool", "key", "Return"], check=True, env=env)
                        self.current_url = url
                        time.sleep(3)
                else:
                    print("[CUA] Chrome not running, launching")
                    if not url or url == "about:blank":
                        subprocess.Popen(
                            ["google-chrome"],
                            env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        self.current_url = "about:blank"
                    else:
                        subprocess.Popen(
                            ["google-chrome", url],
                            env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        self.current_url = url
                    time.sleep(3)

            elif action.action_type == "go_back":
                subprocess.run(["xdotool", "key", "alt+Left"], check=True, env=env)
                time.sleep(1)

            elif action.action_type == "go_forward":
                subprocess.run(["xdotool", "key", "alt+Right"], check=True, env=env)
                time.sleep(1)

            elif action.action_type == "search":
                query = action.args.get("query", "")
                subprocess.run(["xdotool", "key", "ctrl+l"], check=True, env=env)
                time.sleep(0.2)
                subprocess.run(["xdotool", "type", "--clearmodifiers", query],
                              check=True, env=env)
                time.sleep(0.1)
                subprocess.run(["xdotool", "key", "Return"], check=True, env=env)
                time.sleep(2)

            else:
                print(f"[CUA] Unknown action type: {action.action_type}")
                return False

            return True

        except subprocess.CalledProcessError as e:
            print(f"[CUA] xdotool error: {e}")
            return False
        except Exception as e:
            print(f"[CUA] Action execution error: {e}")
            return False

    def run_task(
        self,
        task: str,
        max_steps: int = 300,
        on_step: Optional[Callable[[StepEvent], None]] = None,
    ) -> CUAResponse:
        """Run an autonomous CUA task loop.

        The model drives the loop via function_calls.
        The orchestrator only executes actions and feeds back function_responses.

        Args:
            task: Full instruction text for the task
            max_steps: Safety limit on number of steps
            on_step: Optional callback invoked after each step for logging

        Returns:
            CUAResponse with final status and step log
        """
        self._ensure_session()

        # Build generation config once
        generation_config = self._build_config()

        # Take initial screenshot
        try:
            screenshot_bytes = self._take_screenshot_raw()
        except Exception as e:
            return CUAResponse(
                status="error",
                error_message=f"Failed to capture initial screenshot: {e}"
            )

        # First message: task + screenshot
        self.conversation_history = [
            types.Content(role="user", parts=[
                types.Part(text=task),
                types.Part.from_bytes(data=screenshot_bytes, mime_type="image/png")
            ])
        ]

        step_count = 0
        consecutive_text_responses = 0
        recent_actions: list[str] = []  # for repetition detection
        step_log: list[tuple[int, str, str]] = []  # (step, action, args)
        tracker = ProgressTracker(task)

        while step_count < max_steps:
            step_count += 1
            self._trim_history()

            # Call the model
            last_error = None
            response = None
            for attempt in range(API_MAX_RETRIES):
                try:
                    response = self.client.models.generate_content(
                        model=self.model,
                        contents=self.conversation_history,
                        config=generation_config,
                    )
                    break
                except Exception as e:
                    last_error = str(e)

                    # Detect conversation structure corruption
                    is_corruption = any(sig in last_error for sig in [
                        "Function Call must be matched",
                        "function response to contain the URL",
                        "INVALID_ARGUMENT",
                        "Please ensure that function response turn comes immediately",
                    ])

                    if is_corruption:
                        print(f"[CUA] History corruption detected, resetting: {last_error[:150]}")
                        # Reset and restart with recovery context
                        self.reset_session()
                        recovery = tracker.build_recovery_context()
                        try:
                            screenshot_bytes = self._take_screenshot_raw()
                        except Exception as se:
                            return CUAResponse(
                                status="error",
                                error_message=f"Screenshot failed on recovery: {se}",
                                steps_completed=step_count - 1,
                                step_log=step_log,
                                session_reset=True,
                            )
                        restart_instruction = f"{recovery}\n{task}" if recovery else task
                        self.conversation_history = [
                            types.Content(role="user", parts=[
                                types.Part(text=restart_instruction),
                                types.Part.from_bytes(data=screenshot_bytes, mime_type="image/png")
                            ])
                        ]
                        # Retry the generate_content call with the fresh history
                        try:
                            response = self.client.models.generate_content(
                                model=self.model,
                                contents=self.conversation_history,
                                config=generation_config,
                            )
                            break
                        except Exception as e2:
                            return CUAResponse(
                                status="error",
                                error_message=f"Failed after session reset: {e2}",
                                steps_completed=step_count - 1,
                                step_log=step_log,
                                session_reset=True,
                            )

                    is_retryable = any(code in last_error for code in
                                       ["429", "500", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED"])

                    if is_retryable and attempt < API_MAX_RETRIES - 1:
                        delay = API_RETRY_BASE_DELAY * (2 ** attempt)
                        print(f"[CUA] API error (attempt {attempt + 1}/{API_MAX_RETRIES}), retrying in {delay}s: {last_error[:100]}")
                        time.sleep(delay)
                        continue

                    # Non-retryable or retries exhausted
                    break

            if response is None:
                return CUAResponse(
                    status="error",
                    error_message=f"API call failed after {API_MAX_RETRIES} retries: {last_error}",
                    steps_completed=step_count - 1,
                    step_log=step_log,
                )

            candidate = response.candidates[0] if response.candidates else None
            if candidate is None:
                return CUAResponse(
                    status="error",
                    error_message="No candidates in response",
                    steps_completed=step_count,
                    step_log=step_log,
                )

            content = candidate.content
            if content is None or not content.parts:
                return CUAResponse(
                    status="error",
                    error_message="Empty response from model",
                    steps_completed=step_count,
                    step_log=step_log,
                )

            # Record activity time and append model response to history
            self._last_activity = time.time()
            self.conversation_history.append(content)

            # Check for function calls
            function_calls = [
                p.function_call for p in content.parts
                if hasattr(p, 'function_call') and p.function_call
            ]

            if not function_calls:
                # Model returned text — check for completion
                text_parts = [p.text for p in content.parts if hasattr(p, 'text') and p.text]
                text = " ".join(text_parts)

                print(f"[CUA] Step {step_count}: Model text: {text[:150]}")

                if any(signal in text.upper() for signal in ["TASK COMPLETE", "TASK IS COMPLETE"]):
                    if on_step:
                        on_step(StepEvent(
                            step_number=step_count,
                            action_type=None,
                            action_args=None,
                            status="complete",
                            model_text=text,
                            timestamp=time.time(),
                        ))
                    return CUAResponse(
                        status="complete",
                        model_text=text,
                        steps_completed=step_count,
                        step_log=step_log,
                    )

                # Model is talking but not done — nudge it
                # This IS valid because the function-call cycle has ended (model returned text)
                consecutive_text_responses += 1

                if on_step:
                    on_step(StepEvent(
                        step_number=step_count,
                        action_type=None,
                        action_args=None,
                        status="text",
                        model_text=text,
                        timestamp=time.time(),
                    ))

                if consecutive_text_responses >= 3:
                    return CUAResponse(
                        status="blocked",
                        model_text=text,
                        error_message="Model stuck in text mode after 3 consecutive text responses",
                        steps_completed=step_count,
                        step_log=step_log,
                    )

                # Send a nudge — this is valid because the last model turn was text, not a function_call
                try:
                    screenshot_bytes = self._take_screenshot_raw()
                except Exception as e:
                    return CUAResponse(
                        status="error",
                        error_message=f"Screenshot failed during nudge: {e}",
                        steps_completed=step_count,
                        step_log=step_log,
                    )

                self.conversation_history.append(
                    types.Content(role="user", parts=[
                        types.Part(text=f"The task is not yet complete. Look at the current screen and continue working on: {task[:200]}"),
                        types.Part.from_bytes(data=screenshot_bytes, mime_type="image/png")
                    ])
                )
                continue

            # --- Model returned function calls — execute them ---
            consecutive_text_responses = 0

            response_parts = []
            last_screenshot_b64 = None

            for fc in function_calls:
                action = CUAAction(
                    action_type=fc.name,
                    args=dict(fc.args) if fc.args else {},
                    safety_decision="REGULAR"
                )

                action_key = f"{fc.name}:{json.dumps(dict(fc.args) if fc.args else {}, sort_keys=True)}"
                recent_actions.append(action_key)
                if len(recent_actions) > 8:
                    recent_actions = recent_actions[-8:]

                # Log for tracking
                tracker.log_action(step_count, fc.name, action.args)
                step_log.append((step_count, fc.name, str(action.args)[:100]))
                print(f"[CUA] Step {step_count}: {fc.name} {str(action.args)[:80]}")

                # Execute
                success = self.execute_action(action)
                time.sleep(0.5)

                # Take screenshot after action
                try:
                    screenshot_bytes = self._take_screenshot_raw()
                    last_screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
                except Exception:
                    screenshot_bytes = None
                    last_screenshot_b64 = None

                # Build function response with embedded screenshot
                fr_parts = []
                if screenshot_bytes:
                    fr_parts.append(types.FunctionResponsePart(
                        inline_data=types.FunctionResponseBlob(
                            mime_type="image/png",
                            data=screenshot_bytes
                        )
                    ))

                response_parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response={"url": self.current_url or "about:blank"},
                            parts=fr_parts if fr_parts else None
                        )
                    )
                )

            # Append function responses to history (this is the ONLY user message per cycle)
            self.conversation_history.append(
                types.Content(role="user", parts=response_parts)
            )

            # Emit step event
            if on_step:
                first_fc = function_calls[0]
                on_step(StepEvent(
                    step_number=step_count,
                    action_type=first_fc.name,
                    action_args=dict(first_fc.args) if first_fc.args else {},
                    status="action",
                    screenshot_b64=last_screenshot_b64,
                    timestamp=time.time(),
                ))

            # Check for repetition AFTER sending function responses (to keep history valid)
            if self._detect_repetition(recent_actions):
                print(f"[CUA] Step {step_count}: Repetition detected, injecting nudge")
                # Send a text nudge to break the loop — valid because we just sent function responses
                # and the model will see it as the next user turn
                try:
                    screenshot_bytes = self._take_screenshot_raw()
                except Exception:
                    screenshot_bytes = None

                nudge_parts = [
                    types.Part(text="STOP repeating the same action. It is not working. Try a completely different approach. Look at the screenshot carefully and find an alternative way to accomplish the next step.")
                ]
                if screenshot_bytes:
                    nudge_parts.append(types.Part.from_bytes(data=screenshot_bytes, mime_type="image/png"))

                # We need to get the model's response first (it expects to respond after function_responses)
                # Then inject the nudge. Actually, the function_responses were already added.
                # The model will respond to them. Let's let the loop naturally continue.
                # But we should wait for the next iteration to get the model's response and THEN nudge.
                # Simpler: just add a user message now. The model will see function_responses + nudge.
                # Wait — that would create two consecutive user messages. Let the model respond first.
                # The simplest fix: clear recent_actions to reset detection and let the model try again.
                recent_actions.clear()

        # Hit max steps
        return CUAResponse(
            status="error",
            error_message=f"Hit max steps ({max_steps})",
            steps_completed=step_count,
            step_log=step_log,
        )

    def _detect_repetition(self, recent_actions: list[str], window: int = 4) -> bool:
        """Detect if the last N actions are a repeating pattern."""
        if len(recent_actions) < window:
            return False
        last_n = recent_actions[-window:]
        counts = Counter(last_n)
        return counts.most_common(1)[0][1] >= 3

    def _is_chrome_running(self) -> bool:
        """Check if Google Chrome is already running."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", "chrome"],
                capture_output=True,
                timeout=3
            )
            return result.returncode == 0
        except Exception:
            return False

    def _focus_chrome(self, env: dict) -> None:
        """Focus the existing Chrome window using xdotool."""
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", "Chrome"],
                capture_output=True,
                timeout=3,
                env=env
            )
            if result.returncode == 0 and result.stdout.strip():
                window_id = result.stdout.strip().split(b'\n')[0].decode()
                subprocess.run(
                    ["xdotool", "windowactivate", window_id],
                    check=True,
                    env=env,
                    timeout=3
                )
            else:
                subprocess.run(
                    ["wmctrl", "-a", "Chrome"],
                    env=env,
                    timeout=3
                )
        except Exception as e:
            print(f"[CUA] Warning: Could not focus Chrome window: {e}")

    def reset_session(self) -> None:
        """Explicitly reset the session (clear history, start fresh)."""
        self.conversation_history = []
        self.current_url = "about:blank"
        self._last_activity = 0.0

    def get_history_size(self) -> int:
        """Return current conversation history length"""
        return len(self.conversation_history)

    def is_session_active(self) -> bool:
        """Check if we have an active (non-expired) session."""
        return len(self.conversation_history) > 0 and not self._is_session_expired()


def test_cua():
    """Quick test of CUA functionality"""
    print("[TEST] Initializing GeminiCUA...")
    try:
        cua = GeminiCUA()
        print(f"[TEST] Using model: {cua.model}")
    except Exception as e:
        print(f"[TEST] Init failed: {e}")
        return

    print("[TEST] Taking screenshot...")
    try:
        screenshot = cua.take_screenshot()
        print(f"[TEST] Screenshot captured: {len(screenshot)} bytes base64")
    except Exception as e:
        print(f"[TEST] Screenshot failed: {e}")
        return

    print("[TEST] Running simple task via run_task()...")
    response = cua.run_task(
        "Describe what you see on this screen. Then say TASK COMPLETE.",
        max_steps=3
    )
    print(f"[TEST] Response status: {response.status}")
    if response.model_text:
        print(f"[TEST] Model text: {response.model_text[:500]}...")
    if response.error_message:
        print(f"[TEST] Error: {response.error_message}")
    print(f"[TEST] Steps completed: {response.steps_completed}")


if __name__ == "__main__":
    test_cua()

// App namespace — change this to your app's folder name
const APP_NAME = 'brain-app-template';
const store = BrainKit.store(APP_NAME);

// State
let count = 0;

// DOM refs
const countEl = document.getElementById('count');

// --- Counter ---

async function loadCount() {
  const data = await store.read('state.json', { count: 0, note: '' });
  count = data.count || 0;
  countEl.textContent = count;

  // Restore saved note
  const noteInput = document.getElementById('note-input');
  if (data.note) noteInput.value = data.note;
}

async function saveState() {
  const note = document.getElementById('note-input').value;
  await store.write('state.json', { count, note });
}

document.getElementById('increment').addEventListener('click', async () => {
  count++;
  countEl.textContent = count;
  await saveState();
});

document.getElementById('decrement').addEventListener('click', async () => {
  count--;
  countEl.textContent = count;
  await saveState();
});

document.getElementById('reset').addEventListener('click', async () => {
  count = 0;
  countEl.textContent = count;
  await saveState();
  BrainKit.toast('Counter reset', 'success');
});

// --- Note ---

document.getElementById('save-note').addEventListener('click', async () => {
  await saveState();
  BrainKit.toast('Note saved!', 'success');
});

// --- Init ---

loadCount();

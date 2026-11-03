export function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

export function formatTime(seconds) {
  if (isNaN(seconds) || seconds < 0) seconds = 0;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.floor((seconds % 1) * 10);
  const pad = (n) => String(n).padStart(2, '0');
  if (h > 0) {
    return `${pad(h)}:${pad(m)}:${pad(s)}.${ms}`;
  }
  return `${pad(m)}:${pad(s)}.${ms}`;
}

export function countOf(n, noun) {
  return `${n} ${noun}${n === 1 ? '' : 's'}`;
}

export function showMessage(container, text, isError = false) {
  const p = document.createElement('p');
  p.className = isError ? 'empty empty-error' : 'empty';
  p.textContent = text;
  container.replaceChildren(p);
}

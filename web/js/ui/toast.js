/** Non-blocking notifications. Text is always translated by the caller. */
const MAX = 3;

export function toast(message, kind = 'info', ms = 3600) {
  const host = document.getElementById('toasts');
  if (!host) return;
  const el = document.createElement('div');
  el.className = 'toast toast-' + kind;
  el.textContent = message;
  host.appendChild(el);
  while (host.children.length > MAX) host.removeChild(host.firstChild);
  setTimeout(() => {
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 220);
  }, ms);
}

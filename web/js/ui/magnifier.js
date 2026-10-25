/**
 * Magnifier — redesigned for V2.
 *
 * Was: a tall vertical triangle floating over the stage.
 * Now: a compact HORIZONTAL wedge docked under the main menu on the left,
 * out of the way of the rings, the face and every button.
 *
 * Function is unchanged: it magnifies the interface. The wedge widens to the
 * right, so the triangle itself shows how much magnification is applied.
 */
import * as state from '../core/state.js';

const MIN = 85;
const MAX = 150;

export default class Magnifier {
  constructor() {
    this.root = document.getElementById('magnifier');
    this.range = document.getElementById('mag-range');
    this.value = document.getElementById('mag-value');
    this.reset = document.getElementById('mag-reset');
    if (!this.root || !this.range) return;

    const saved = state.loadLocal().zoom;
    const initial = clamp(Math.round((saved || 1) * 100));
    this.range.value = String(initial);
    this.apply(initial, false);

    this.range.addEventListener('input', () => this.apply(Number(this.range.value), false));
    this.range.addEventListener('change', () => this.apply(Number(this.range.value), true));
    this.reset?.addEventListener('click', () => {
      this.range.value = '100';
      this.apply(100, true);
    });
  }

  apply(percent, persist) {
    const pct = clamp(percent);
    const zoom = pct / 100;
    document.documentElement.style.setProperty('--zoom', String(zoom));
    if (this.value) this.value.textContent = pct + '%';
    state.set({ zoom });
    if (persist) state.saveLocal({ zoom });
  }

  setVisible(visible) {
    if (this.root) this.root.hidden = !visible;
  }
}

function clamp(value) {
  if (!Number.isFinite(value)) return 100;
  return Math.min(MAX, Math.max(MIN, Math.round(value / 5) * 5));
}

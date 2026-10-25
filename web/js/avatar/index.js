/**
 * Avatar manager.
 *
 * V2 has exactly two modes: 'rings' and 'face'. The old 'cube' mode and its
 * WebGL renderer are gone; setMode() maps any legacy value back to 'rings' so
 * an old saved preference cannot leave the stage blank.
 *
 * The render loop stops completely when the tab is hidden, when JARVIS is in
 * standby, or when the stage is not visible — an idle phone must not burn
 * battery on an animation nobody is looking at.
 */
import RingsAvatar from './rings.js';
import FaceAvatar from './face.js';
import { emit } from '../core/bus.js';

const MODES = ['rings', 'face'];
const LEGACY_MODES = ['cube', '3d', 'box'];

export default class Avatar {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d', { alpha: true, desynchronized: true });
    this.renderers = { rings: new RingsAvatar(), face: new FaceAvatar() };
    this.mode = 'rings';
    this.activity = 'idle';
    this.level = 0;
    this.motion = 1;          // 0 = reduced motion
    this.running = false;
    this.paused = false;
    this._raf = 0;
    this._last = 0;
    this._dpr = 1;

    this._onResize = () => this.resize();
    window.addEventListener('resize', this._onResize, { passive: true });
    window.addEventListener('orientationchange', this._onResize, { passive: true });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) this.pause();
      else this.resume();
    });
    if (window.ResizeObserver) {
      this._ro = new ResizeObserver(() => this.resize());
      this._ro.observe(canvas.parentElement || canvas);
    }
    this.resize();
  }

  static modes() {
    return MODES.slice();
  }

  setMode(mode) {
    if (LEGACY_MODES.includes(mode)) {
      console.info('[avatar] mode "%s" was removed in V2 — using rings', mode);
      mode = 'rings';
    }
    if (!MODES.includes(mode)) mode = 'rings';
    if (mode === this.mode) return this.mode;
    this.mode = mode;
    this._clear();
    emit('avatar:mode', mode);
    return mode;
  }

  setActivity(activity) {
    this.activity = activity;
  }

  setLevel(level) {
    this.level = Math.max(0, Math.min(1, level || 0));
  }

  setReducedMotion(reduced) {
    this.motion = reduced ? 0.25 : 1;
  }

  resize() {
    // Measure the stage, never the canvas itself, and set only the backing
    // store. Writing an inline pixel size onto the canvas would make it push
    // the stage wider, which the ResizeObserver would then measure again —
    // a loop that grows the canvas until the interface is unusable.
    const parent = this.canvas.parentElement || this.canvas;
    const rect = parent.getBoundingClientRect();
    const width = Math.max(120, Math.floor(rect.width));
    const height = Math.max(96, Math.floor(rect.height));
    // Cap the pixel ratio: a 3x buffer on a phone triples the fill cost.
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.w = width;
    this.h = height;
    if (this.canvas.width === width * dpr && this.canvas.height === height * dpr) return;
    this._dpr = dpr;
    this.canvas.width = width * dpr;
    this.canvas.height = height * dpr;
    if (!this.running) this._drawOnce();
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.paused = false;
    this._last = performance.now();
    this._loop(this._last);
  }

  pause() {
    this.paused = true;
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = 0;
  }

  resume() {
    if (!this.running || !this.paused) return;
    this.paused = false;
    this._last = performance.now();
    this._loop(this._last);
  }

  stop() {
    this.running = false;
    this.pause();
    this._clear();
  }

  _clear() {
    if (!this.ctx) return;
    this.ctx.setTransform(1, 0, 0, 1, 0, 0);
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }

  _drawOnce() {
    this._render(performance.now(), 0);
  }

  _loop(now) {
    if (!this.running || this.paused) return;
    const dt = Math.min(0.05, (now - this._last) / 1000);
    this._last = now;
    this._render(now, dt);
    this._raf = requestAnimationFrame((t) => this._loop(t));
  }

  _render(now, dt) {
    const ctx = this.ctx;
    if (!ctx || !this.w) return;
    ctx.setTransform(this._dpr, 0, 0, this._dpr, 0, 0);
    const renderer = this.renderers[this.mode] || this.renderers.rings;
    try {
      renderer.draw(ctx, {
        w: this.w,
        h: this.h,
        time: now / 1000,
        dt,
        activity: this.activity,
        level: this.level,
        motion: this.motion,
      });
    } catch (err) {
      console.error('[avatar] render failed', err);
      this.stop();
    }
  }

  dispose() {
    this.stop();
    window.removeEventListener('resize', this._onResize);
    window.removeEventListener('orientationchange', this._onResize);
    if (this._ro) this._ro.disconnect();
  }
}

/**
 * Avatar manager.
 *
 * V2 has exactly two modes: 'rings' and 'face'. The old 'cube' mode and its
 * renderer are gone; setMode() maps any legacy value back to 'rings' so an old
 * saved preference cannot leave the stage blank.
 *
 * The two modes do not share a canvas, because a canvas can only ever have one
 * kind of context: RINGS is Canvas 2D, FACE is WebGL. Both canvases sit in the
 * stage, and only the active one is shown.
 *
 * FACE falls back to the Canvas 2D wireframe head if WebGL is unavailable or
 * the context is refused — on a phone that is rare, but a blank stage would be
 * much worse than a simpler face.
 *
 * The render loop stops completely when the tab is hidden, when JARVIS is in
 * standby, or when the stage is not visible — an idle phone must not burn
 * battery on an animation nobody is looking at.
 */
import RingsAvatar from './rings.js';
import FaceAvatar from './face.js';
import Face3D from './face3d.js';
import { emit } from '../core/bus.js';

const MODES = ['rings', 'face'];
const LEGACY_MODES = ['cube', '3d', 'box'];

export default class Avatar {
  constructor(canvas2d, canvasGl, canvasCard) {
    this.canvas2d = canvas2d;
    this.canvasGl = canvasGl;
    // The small hologram card above the dial. It is a second WebGL surface
    // rather than a second view of the first, because a canvas cannot be
    // presented in two places — and the reference shows the face and the
    // rings on screen at the same time, not one instead of the other.
    this.canvasCard = canvasCard || null;
    this.ctx = canvas2d.getContext('2d', { alpha: true, desynchronized: true });

    this.rings = new RingsAvatar();
    this.face3d = canvasGl ? new Face3D() : null;
    this.cardFace = canvasCard ? new Face3D() : null;
    this.faceFallback = new FaceAvatar();
    this.renderers = { rings: this.rings, face: this.faceFallback };
    this._cardReady = false;
    this._cardWanted = true;
    this._cardRetried = false;

    this.mode = 'rings';
    this.activity = 'idle';
    this.level = 0;
    this.motion = 1;          // 0 = reduced motion
    this.tiltX = 0;           // HOLO: horizontal parallax, -1.1 .. 1.1
    this.tiltY = 0;           // HOLO: vertical parallax, -0.6 .. 0.6
    this.running = false;
    this.paused = false;
    this._raf = 0;
    this._last = 0;
    this._dpr = 1;
    this._glReady = false;
    // Frame-time watchdog: see _watch().
    this._frames = 0;
    this._accum = 0;
    this._quality = 2;

    this._onResize = () => this.resize();
    window.addEventListener('resize', this._onResize, { passive: true });
    window.addEventListener('orientationchange', this._onResize, { passive: true });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) this.pause();
      else this.resume();
    });
    if (window.ResizeObserver) {
      this._ro = new ResizeObserver(() => this.resize());
      this._ro.observe(canvas2d.parentElement || canvas2d);
    }
    this._applyMode();
    this.resize();
  }

  static modes() {
    return MODES.slice();
  }

  /** True once the WebGL bust is actually running, so the UI can say so. */
  usesWebGL() {
    return this._glReady;
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
    this._applyMode();
    this.resize();
    emit('avatar:mode', mode);
    return mode;
  }

  /**
   * Choose the renderer for the current mode, bringing WebGL up the first time
   * FACE is selected rather than at boot: most sessions never open it, and a
   * GL context costs memory.
   */
  _applyMode() {
    if (this.mode === 'face' && this.face3d && !this.face3d.failed) {
      if (!this.face3d.ready) {
        const reduced = this.motion < 1;
        this._glReady = this.face3d.init(this.canvasGl, { quality: reduced ? 'low' : 'high' });
        if (!this._glReady) {
          console.warn('[avatar] WebGL unavailable — using the 2D face');
        }
      } else {
        this._glReady = true;
      }
      this.renderers.face = this._glReady ? this.face3d : this.faceFallback;
    }
    const gl = this.mode === 'face' && this._glReady;
    if (this.canvasGl) this.canvasGl.hidden = !gl;
    this.canvas2d.hidden = gl;
    // With FACE on the main stage the card would be the same head twice, so
    // it stands down and gives its space back.
    this._applyCard();
  }

  /** Bring the card's own GL context up the first time it is actually shown. */
  _applyCard() {
    const card = document.getElementById('avatar-card');
    const wanted = this._cardWanted && this.mode !== 'face';
    if (card) card.hidden = !wanted;
    if (!wanted || !this.cardFace || this.cardFace.failed) return;
    if (!this.cardFace.ready) {
      this._cardReady = this.cardFace.init(this.canvasCard, {
        quality: this.motion < 1 ? 'low' : 'high',
      });
      if (!this._cardReady && card) {
        // Nothing to show. A context can also be refused transiently while
        // another one is being created, so try once more on the next frame
        // before giving the space back.
        card.hidden = true;
        if (!this._cardRetried) {
          this._cardRetried = true;
          this.cardFace.failed = false;
          requestAnimationFrame(() => { this._applyCard(); this.resize(); });
        } else {
          console.warn('[avatar] card WebGL unavailable — showing rings only');
        }
      }
    } else {
      this._cardReady = true;
    }
  }

  /** Let the interface turn the card off entirely (e.g. reduced motion). */
  setCardVisible(on) {
    this._cardWanted = !!on;
    this._applyCard();
    this.resize();
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

  /** The text in the middle of the rings, from settings. */
  setLabel(text) {
    for (const renderer of [this.rings, this.faceFallback, this.face3d]) {
      renderer?.setLabel?.(text);
    }
    if (!this.running || this.paused) this._drawOnce();
  }

  /** HOLO: turn the projection with a drag or with the phone's own tilt. */
  setTilt(x, y) {
    this.tiltX = Number.isFinite(x) ? x : 0;
    this.tiltY = Number.isFinite(y) ? y : 0;
    if (!this.running || this.paused) this._drawOnce();
  }

  resize() {
    // Measure the stage, never a canvas itself, and set only the backing
    // store. Writing an inline pixel size onto a canvas would make it push the
    // stage wider, which the ResizeObserver would then measure again — a loop
    // that grows the canvas until the interface is unusable.
    const parent = this.canvas2d.parentElement || this.canvas2d;
    const rect = parent.getBoundingClientRect();
    const width = Math.max(120, Math.floor(rect.width));
    const height = Math.max(96, Math.floor(rect.height));
    this.w = width;
    this.h = height;

    // Cap the pixel ratio. A Galaxy S25 Ultra reports 3.5: a full-ratio
    // backing store would be 12× the fill cost of a 1× one for a difference
    // nobody can see on a 6.9" panel, and it is the single biggest thing the
    // visualiser can do to a battery.
    const dpr2d = Math.min(window.devicePixelRatio || 1, 2);
    if (this.canvas2d.width !== width * dpr2d || this.canvas2d.height !== height * dpr2d) {
      this._dpr = dpr2d;
      this.canvas2d.width = width * dpr2d;
      this.canvas2d.height = height * dpr2d;
    }
    if (this.canvasGl) {
      // GL shades every pixel of the bust, so it gets a tighter cap again.
      const dprGl = Math.min(window.devicePixelRatio || 1, 1.75);
      const gw = Math.round(width * dprGl);
      const gh = Math.round(height * dprGl);
      if (this.canvasGl.width !== gw || this.canvasGl.height !== gh) {
        this.canvasGl.width = gw;
        this.canvasGl.height = gh;
      }
    }
    if (this.canvasCard) {
      const card = this.canvasCard.parentElement || this.canvasCard;
      const box = card.getBoundingClientRect();
      this.cardW = Math.max(60, Math.floor(box.width));
      this.cardH = Math.max(60, Math.floor(box.height));
      // The card is small, so it gets a tighter ratio again: at this size
      // nobody can tell, and it is drawn alongside the dial every frame.
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      const cw = Math.round(this.cardW * dpr);
      const ch = Math.round(this.cardH * dpr);
      if (this.canvasCard.width !== cw || this.canvasCard.height !== ch) {
        this.canvasCard.width = cw;
        this.canvasCard.height = ch;
      }
    }
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
    if (this.ctx) {
      this.ctx.setTransform(1, 0, 0, 1, 0, 0);
      this.ctx.clearRect(0, 0, this.canvas2d.width, this.canvas2d.height);
    }
    for (const renderer of [this.face3d, this.cardFace]) {
      const gl = renderer?.gl;
      if (!gl) continue;
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
    }
  }

  _drawOnce() {
    this._render(performance.now(), 0);
  }

  _loop(now) {
    if (!this.running || this.paused) return;
    const raw = (now - this._last) / 1000;
    const dt = Math.min(0.05, raw);
    this._last = now;
    this._render(now, dt);
    this._watch(raw);
    this._raf = requestAnimationFrame((t) => this._loop(t));
  }

  /**
   * Phones vary by more than an order of magnitude, and the bust is the one
   * thing here that can outrun a slow GPU. Rather than guessing a device
   * class, measure: if a second of frames averages worse than ~36fps, drop a
   * quality level; if it comfortably clears 55fps again, take one back.
   *
   * Hysteresis matters — a renderer that toggles quality every second is more
   * distracting than a slow one.
   */
  _watch(raw) {
    const renderer = this.renderers[this.mode];
    if (!renderer || typeof renderer.setQuality !== 'function') return;
    if (raw <= 0 || raw > 0.5) return;            // a tab switch, not a frame
    this._accum += raw;
    this._frames += 1;
    if (this._accum < 1) return;
    const fps = this._frames / this._accum;
    this._accum = 0;
    this._frames = 0;
    let next = this._quality;
    if (fps < 36 && next > 0) next -= 1;
    else if (fps > 55 && next < 2) next += 1;
    if (next === this._quality) return;
    this._quality = next;
    renderer.setQuality(next);
    // The card draws every frame too, so it sheds load with the main stage.
    this.cardFace?.setQuality?.(next);
    console.info('[avatar] %d fps — visualiser quality now %d', Math.round(fps), next);
  }

  _render(now, dt) {
    if (!this.w) return;
    const renderer = this.renderers[this.mode] || this.rings;
    const env = {
      w: this.w,
      h: this.h,
      time: now / 1000,
      dt,
      activity: this.activity,
      level: this.level,
      motion: this.motion,
      tiltX: this.tiltX,
      tiltY: this.tiltY,
    };
    try {
      if (renderer.type === 'gl') {
        renderer.render(this.canvasGl, env);
      } else {
        if (!this.ctx) return;
        this.ctx.setTransform(this._dpr, 0, 0, this._dpr, 0, 0);
        renderer.draw(this.ctx, env);
      }
      // The hologram card, alongside whatever the main stage is showing.
      if (this._cardReady && this.cardFace?.ready && this.cardW) {
        this.cardFace.render(this.canvasCard, { ...env, w: this.cardW, h: this.cardH });
      }
    } catch (err) {
      console.error('[avatar] render failed', err);
      // A failing WebGL renderer drops back to the 2D head rather than
      // leaving the stage dead.
      if (renderer.type === 'gl') {
        this.face3d.failed = true;
        this._glReady = false;
        this.renderers.face = this.faceFallback;
        this._applyMode();
        this.resize();
        return;
      }
      this.stop();
    }
  }

  dispose() {
    this.stop();
    window.removeEventListener('resize', this._onResize);
    window.removeEventListener('orientationchange', this._onResize);
    if (this._ro) this._ro.disconnect();
    this.face3d?.dispose?.();
    this.cardFace?.dispose?.();
  }
}

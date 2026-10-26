/**
 * The ambient backdrop: suspended particles always, a cinematic scene in
 * full screen.
 *
 * Two layers, drawn on one canvas that sits behind the whole interface:
 *
 *   - **Particles.** Thousands of motes drifting in no particular direction,
 *     like matter suspended in gel. They do not fall and they do not stream;
 *     each one wanders on its own slow heading and turns gently as it goes.
 *     This layer runs in every mode.
 *
 *   - **The cinematic scene.** A nebula wash and a hooded figure, drawn only
 *     while full screen is on. The figure is drawn rather than photographed:
 *     the brief asks for a face that is not clearly visible, and a silhouette
 *     has none at all — and nothing has to be licensed or cached.
 *
 * Android is the target, so the particle layer is one batched path and one
 * fill per frame rather than a thousand draw calls, the canvas is capped well
 * below the panel's device pixel ratio, and the whole thing stands down under
 * reduced motion or when the frame watchdog asks for less.
 */

/** Particles per square CSS pixel, and the bounds that keeps it sane. */
const DENSITY = 1 / 170;
const MIN_PARTICLES = 600;
const MAX_PARTICLES = 3000;

/**
 * Depth bands, near to far.
 *
 * Each mote gets a depth once and keeps it: nothing moves toward or away
 * from the viewer. Depth sets how big it is, how bright, and how fast it
 * drifts — near motes cross the screen visibly quicker than far ones, which
 * is the parallax that makes a flat canvas read as a volume.
 *
 * The far bands are drawn as squares rather than circles. Under about a
 * pixel the two are indistinguishable, and a rect is far cheaper to path —
 * which is what lets the count run to thousands on a phone.
 */
const BANDS = [
  { z: 0.12, alpha: 0.10, round: false },
  { z: 0.30, alpha: 0.17, round: false },
  { z: 0.52, alpha: 0.26, round: true },
  { z: 0.76, alpha: 0.40, round: true },
  { z: 1.00, alpha: 0.62, round: true },
];

export default class Backdrop {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas ? canvas.getContext('2d') : null;
    this.running = false;
    this.cinematic = false;
    this.frame = 0;
    this.w = 0;
    this.h = 0;
    this.dpr = 1;
    this.motion = 1;
    this.quality = 1;
    this.particles = [];
    this.last = 0;
    this._loop = this._loop.bind(this);

    // Areas the backdrop must never paint into. The conversation is a solid
    // panel of its own; motes drifting behind it would still show through its
    // edges and corners, so the layer is cut out around it entirely.
    this.masked = [];
    this.maskRects = [];

    if (this.canvas) {
      this.observer = new ResizeObserver(() => this.resize());
      this.observer.observe(this.canvas.parentElement || this.canvas);
    }
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) this._pause();
      else if (this.running) this._resume();
    });
  }

  /**
   * Elements the backdrop is cut out around.
   *
   * Their rectangles are cached and refreshed by a ResizeObserver rather than
   * measured every frame: reading a layout box 120 times a second is how an
   * ambient layer turns into a janky one.
   */
  maskOut(selectors) {
    this.masked = selectors
      .map((sel) => document.querySelector(sel))
      .filter(Boolean);
    if (this.maskObserver) this.maskObserver.disconnect();
    this.maskObserver = new ResizeObserver(() => this._measureMasks());
    for (const el of this.masked) this.maskObserver.observe(el);
    this._measureMasks();
  }

  _measureMasks() {
    this.maskRects = this.masked.map((el) => {
      const r = el.getBoundingClientRect();
      return { x: r.left, y: r.top, w: r.width, h: r.height };
    });
  }

  /** The figure and the nebula belong to full screen; the motes do not. */
  setCinematic(on) {
    this.cinematic = !!on;
  }

  setReducedMotion(reduced) {
    this.motion = reduced ? 0 : 1;
    if (this.running) this._seed();
  }

  /**
   * Shed particles when the watchdog says frames are slipping.
   *
   * The avatar's watchdog speaks in levels 0/1/2; the names are accepted too
   * so the layer can be driven directly.
   */
  setQuality(level) {
    const scale = { 0: 0.3, 1: 0.6, 2: 1, low: 0.3, medium: 0.6, high: 1 };
    const next = scale[level] ?? 1;
    if (next === this.quality) return;
    this.quality = next;
    if (this.running) this._seed();
  }

  start() {
    if (this.running || !this.ctx) return;
    this.running = true;
    this.resize();
    this._seed();
    this._resume();
  }

  stop() {
    this.running = false;
    this._pause();
    if (this.ctx && this.w) this.ctx.clearRect(0, 0, this.w, this.h);
  }

  _resume() {
    if (this.frame) return;
    this.last = performance.now();
    this.frame = requestAnimationFrame(this._loop);
  }

  _pause() {
    if (this.frame) { cancelAnimationFrame(this.frame); this.frame = 0; }
  }

  resize() {
    if (!this.canvas) return;
    const box = (this.canvas.parentElement || this.canvas).getBoundingClientRect();
    const w = Math.max(1, Math.floor(box.width));
    const h = Math.max(1, Math.floor(box.height));
    // A soft backdrop gains nothing from a 3x panel and costs real fill rate
    // on a 1440p phone.
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    this._measureMasks();
    if (w === this.w && h === this.h && dpr === this.dpr) return;
    this.w = w; this.h = h; this.dpr = dpr;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (this.running) this._seed();
  }

  _count() {
    const raw = Math.round(this.w * this.h * DENSITY * this.quality);
    return Math.max(MIN_PARTICLES, Math.min(MAX_PARTICLES, raw));
  }

  _seed() {
    const count = this.motion ? this._count() : Math.round(this._count() * 0.5);
    this.particles = [];
    for (let i = 0; i < count; i++) {
      // Weighted towards the back, so the field has a deep haze with a few
      // bright motes near the glass rather than an even sheet.
      const band = Math.min(
        BANDS.length - 1,
        Math.floor(Math.pow(Math.random(), 0.75) * BANDS.length),
      );
      const z = BANDS[band].z;
      this.particles.push({
        x: Math.random() * this.w,
        y: Math.random() * this.h,
        band,
        // Its own heading and a slow turn rate: the field wanders rather
        // than drifting one way. Nothing here has a preferred direction.
        dir: Math.random() * Math.PI * 2,
        turn: (Math.random() - 0.5) * 0.9,
        // Parallax: near motes move further in the same second.
        speed: (0.8 + Math.random() * 3.2) * (0.35 + z * 1.5),
        r: (0.35 + Math.random() * 0.75) * (0.45 + z * 1.25),
        phase: Math.random() * Math.PI * 2,
        rate: 0.5 + Math.random() * 1.1,
      });
    }
  }

  _loop(now) {
    if (!this.running) { this.frame = 0; return; }
    // Time-based, so the drift looks the same at 60Hz and at 120Hz, and a
    // dropped frame does not make the field jump.
    const dt = Math.min(0.05, (now - this.last) / 1000);
    this.last = now;
    this._draw(now / 1000, dt);
    this.frame = requestAnimationFrame(this._loop);
  }

  _draw(time, dt) {
    const { ctx, w, h } = this;
    if (!ctx || !w) return;
    ctx.clearRect(0, 0, w, h);

    // Everything below is drawn inside a clip that excludes the conversation,
    // so not one mote is ever painted under it — clipping rather than
    // clearing afterwards also saves the fill in the first place.
    const masked = this.maskRects.length > 0;
    if (masked) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(0, 0, w, h);
      for (const m of this.maskRects) {
        // Wound the opposite way so even-odd takes it out of the region.
        ctx.rect(m.x, m.y + m.h, m.w, -m.h);
      }
      ctx.clip('evenodd');
    }

    if (this.cinematic) {
      this._nebula(ctx, w, h, time);
      this._figure(ctx, w, h);
    }

    this._motes(ctx, w, h, time, dt);

    if (this.cinematic) {
      // The scrim is what keeps the scene a backdrop: everything above is
      // drawn bright enough to read, then pushed back under this.
      const scrim = ctx.createLinearGradient(0, 0, 0, h);
      scrim.addColorStop(0, 'rgba(2, 7, 13, 0.58)');
      scrim.addColorStop(0.45, 'rgba(2, 7, 13, 0.46)');
      scrim.addColorStop(1, 'rgba(2, 7, 13, 0.70)');
      ctx.fillStyle = scrim;
      ctx.fillRect(0, 0, w, h);
    }

    if (masked) ctx.restore();
  }

  /**
   * The motes: one path, one fill.
   *
   * A thousand separate `arc()`+`fill()` pairs is a thousand draw calls and
   * will not hold 60fps on a phone. Batching them into a single Path2D and
   * filling once keeps it to one. They share an alpha, and the per-mote
   * variation comes from size and from how many are drawn in each of three
   * brightness bands.
   */
  _motes(ctx, w, h, time, dt) {
    if (!this.particles.length) return;
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';

    // One path per depth band: five fills for thousands of motes, instead of
    // one draw call each.
    const paths = BANDS.map(() => new Path2D());

    for (const p of this.particles) {
      if (this.motion) {
        // Wander: the heading turns slowly, so the mote curves instead of
        // travelling in a straight line.
        p.dir += p.turn * dt;
        const step = p.speed * dt;
        p.x += Math.cos(p.dir) * step;
        p.y += Math.sin(p.dir) * step;
        // Wrap rather than respawn, so the field never thins at an edge.
        if (p.x < -2) p.x = w + 2; else if (p.x > w + 2) p.x = -2;
        if (p.y < -2) p.y = h + 2; else if (p.y > h + 2) p.y = -2;
      }
      const path = paths[p.band];
      if (BANDS[p.band].round) {
        path.moveTo(p.x + p.r, p.y);
        path.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      } else {
        const d = p.r * 2;
        path.rect(p.x - p.r, p.y - p.r, d, d);
      }
    }

    for (let i = 0; i < BANDS.length; i++) {
      // The whole band breathes together rather than each mote on its own
      // timer: the same shimmer, at a fraction of the arithmetic.
      const breath = this.motion
        ? 0.72 + 0.28 * Math.sin(time * (0.5 + i * 0.17) + i)
        : 1;
      ctx.globalAlpha = BANDS[i].alpha * breath;
      ctx.fillStyle = '#8df3ec';
      ctx.fill(paths[i]);
    }
    ctx.restore();
  }

  /** A slow wash of colour, lobes breathing out of phase. */
  _nebula(ctx, w, h, time) {
    const drift = this.motion ? Math.sin(time * 0.06) : 0;
    const lobes = [
      { x: w * (0.30 + drift * 0.02), y: h * 0.34, r: Math.max(w, h) * 0.62,
        c: 'rgba(18, 92, 126, 0.50)' },
      { x: w * (0.74 - drift * 0.03), y: h * 0.62, r: Math.max(w, h) * 0.52,
        c: 'rgba(32, 62, 140, 0.38)' },
      { x: w * 0.52, y: h * 0.12, r: Math.max(w, h) * 0.40,
        c: 'rgba(60, 224, 255, 0.14)' },
    ];
    for (const lobe of lobes) {
      const g = ctx.createRadialGradient(lobe.x, lobe.y, 0, lobe.x, lobe.y, lobe.r);
      g.addColorStop(0, lobe.c);
      g.addColorStop(1, 'rgba(0, 0, 0, 0)');
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);
    }
  }

  /**
   * The figure: head, hood and shoulders as one filled path.
   *
   * No eyes, no mouth, no edge light on the face — the hood's opening is
   * filled darker than the cloth, so what reads is a covered head. Present
   * and mysterious, not a portrait.
   */
  _figure(ctx, w, h) {
    // Composed for a portrait phone: the crown sits under the dial and the
    // shoulders run off the bottom, so she fills the middle of the screen
    // without competing with the rings for the top band.
    const cx = w * 0.5;
    const headR = Math.min(w * 0.165, h * 0.082);
    const headY = h * 0.545;
    const base = h * 1.03;
    const spread = Math.min(w * 0.56, h * 0.30);

    ctx.save();

    ctx.beginPath();
    ctx.moveTo(cx - spread, base);
    ctx.bezierCurveTo(
      cx - spread * 0.86, headY + headR * 1.9,
      cx - headR * 2.05, headY + headR * 0.80,
      cx - headR * 1.46, headY - headR * 0.30,
    );
    // over the crown of the hood
    ctx.bezierCurveTo(
      cx - headR * 1.40, headY - headR * 2.12,
      cx + headR * 1.40, headY - headR * 2.12,
      cx + headR * 1.46, headY - headR * 0.30,
    );
    ctx.bezierCurveTo(
      cx + headR * 2.05, headY + headR * 0.80,
      cx + spread * 0.86, headY + headR * 1.9,
      cx + spread, base,
    );
    ctx.closePath();

    const cloth = ctx.createLinearGradient(0, headY - headR * 2, 0, base);
    cloth.addColorStop(0, 'rgba(38, 112, 148, 0.95)');
    cloth.addColorStop(0.42, 'rgba(14, 52, 80, 0.96)');
    cloth.addColorStop(1, 'rgba(4, 16, 26, 0.98)');
    ctx.fillStyle = cloth;
    ctx.fill();

    // A thin rim down the edge of the hood: what makes a flat silhouette
    // read as a figure rather than a hole.
    ctx.lineWidth = Math.max(1, headR * 0.035);
    ctx.strokeStyle = 'rgba(140, 240, 248, 0.42)';
    ctx.stroke();

    // The opening of the hood: darker than the cloth, and empty.
    ctx.beginPath();
    ctx.ellipse(cx, headY + headR * 0.10, headR * 0.80, headR * 1.03, 0, 0, Math.PI * 2);
    const inside = ctx.createRadialGradient(
      cx, headY + headR * 0.35, headR * 0.10,
      cx, headY + headR * 0.10, headR * 1.05,
    );
    inside.addColorStop(0, 'rgba(10, 30, 44, 0.96)');
    inside.addColorStop(1, 'rgba(2, 8, 14, 0.99)');
    ctx.fillStyle = inside;
    ctx.fill();

    // The one hint of a face: a glow where the eyes would be, far too diffuse
    // to resolve into features.
    const eyes = ctx.createRadialGradient(
      cx, headY + headR * 0.05, 0, cx, headY + headR * 0.05, headR * 0.62,
    );
    eyes.addColorStop(0, 'rgba(96, 226, 255, 0.17)');
    eyes.addColorStop(1, 'rgba(96, 226, 255, 0)');
    ctx.fillStyle = eyes;
    ctx.fill();

    ctx.restore();
  }

  dispose() {
    this.stop();
    this.observer?.disconnect();
    this.maskObserver?.disconnect();
  }
}

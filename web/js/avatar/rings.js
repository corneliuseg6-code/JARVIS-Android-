/**
 * RINGS visualiser — a layered holographic instrument dial.
 *
 * The stage is built from eight concentric layers that each move at their own
 * speed and direction, so the whole thing reads as machinery rather than as a
 * spinning circle:
 *
 * There is deliberately no background layer. The dial used to sit on a soft
 * disc of radius R * 1.25 — wider than the canvas's half-size of R * 1.087 —
 * so the glow was clipped by the canvas edge and read as a square box behind
 * the rings. The rings now sit straight on the interface background.
 *
 *   1  outer tick crown      fine radial graduations, every 5th one long
 *   2  segment ring          four bright arcs with gaps, slow clockwise
 *   3  dense graduation ring counter-clockwise, brightness varies with energy
 *   4  orbit ring            thin circle carrying small satellites
 *   5  sweep arc             one bright arc that accelerates while thinking
 *   6  dashed ring           short dashes, counter-clockwise
 *   7  gyro rings            two real 3D circles for depth
 *   8  core                  glowing disc, reactive, with the centre label
 *
 * Everything is Canvas 2D: a phone draws it at 60fps for a fraction of the
 * battery a WebGL context would cost, and it needs no external library.
 *
 * Performance note: each layer batches its geometry into ONE path and strokes
 * it once. Stroking a hundred separate two-point paths per frame is what makes
 * this kind of dial stutter on a mid-range phone.
 */

const TAU = Math.PI * 2;

function rotateX(p, a) {
  const c = Math.cos(a), s = Math.sin(a);
  return { x: p.x, y: p.y * c - p.z * s, z: p.y * s + p.z * c };
}
function rotateY(p, a) {
  const c = Math.cos(a), s = Math.sin(a);
  return { x: p.x * c + p.z * s, y: p.y, z: -p.x * s + p.z * c };
}
function rotateZ(p, a) {
  const c = Math.cos(a), s = Math.sin(a);
  return { x: p.x * c - p.y * s, y: p.x * s + p.y * c, z: p.z };
}

export default class RingsAvatar {
  constructor() {
    this.name = 'rings';
    this.phase = 0;
    this.sweep = 0;
    this.label = 'J.A.R.V.I.S.';
    // One wave history shared by the listening and speaking states.
    this.wave = new Float32Array(64);
  }

  setLabel(text) {
    this.label = String(text || '').trim() || 'J.A.R.V.I.S.';
  }

  /**
   * @param {CanvasRenderingContext2D} ctx
   * @param {{w:number,h:number,time:number,dt:number,activity:string,level:number,motion:number}} env
   */
  draw(ctx, env) {
    const { w, h, dt, activity, level, motion } = env;
    // HOLO parallax: the dial leans with the phone, and the whole instrument
    // shifts a little so it reads as something floating in front of the glass.
    const tiltX = env.tiltX || 0;
    const tiltY = env.tiltY || 0;
    const cx = w / 2 + tiltX * w * 0.045;
    const cy = h / 2 + tiltY * h * 0.045;
    const R = Math.min(w, h) * 0.46;

    const speed = activity === 'thinking' ? 2.1
      : activity === 'speaking' ? 1.45
      : activity === 'listening' ? 1.15
      : 0.55;
    this.phase += dt * speed * motion;
    this.sweep += dt * (activity === 'thinking' ? 2.8 : 0.9) * motion;

    const energy = activity === 'listening' ? 0.38 + level * 0.9
      : activity === 'speaking' ? 0.52 + level * 0.8
      : activity === 'thinking' ? 0.62
      : 0.26;

    // Push the newest level into the wave history ring.
    this.wave.copyWithin(0, 1);
    this.wave[this.wave.length - 1] = activity === 'listening' || activity === 'speaking'
      ? level
      : this.wave[this.wave.length - 1] * 0.9;

    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(cx, cy);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    const p = this.phase;

    // 1 — outer tick crown
    this._tickRing(ctx, {
      radius: R * 0.98, count: 120, length: R * 0.055, longEvery: 5,
      longLength: R * 0.095, rotation: p * 0.10, width: 1,
      colour: `rgba(126, 232, 226, ${0.22 + energy * 0.30})`,
      longColour: `rgba(176, 248, 244, ${0.38 + energy * 0.36})`,
    });

    // 2 — four bright arc segments with gaps
    this._arcRing(ctx, {
      radius: R * 0.88, width: Math.max(1.8, R * 0.028),
      arcs: [[0.02, 0.21], [0.27, 0.44], [0.52, 0.71], [0.77, 0.96]],
      rotation: -p * 0.22,
      colour: `rgba(206, 255, 252, ${0.55 + energy * 0.40})`,
      glow: 14 + energy * 16,
    });

    // 3 — dense graduation ring, the opposite way
    this._tickRing(ctx, {
      radius: R * 0.80, count: 72, length: R * 0.042, longEvery: 6,
      longLength: R * 0.068, rotation: -p * 0.26, width: 1, inward: true,
      colour: `rgba(104, 214, 214, ${0.20 + energy * 0.28})`,
      longColour: `rgba(150, 240, 238, ${0.34 + energy * 0.34})`,
    });

    // 4 — orbit ring with satellites
    this._orbitRing(ctx, R * 0.70, p * 0.55, energy);

    // 5 — the sweep: one bright arc that chases the dial
    this._arcRing(ctx, {
      radius: R * 0.60, width: Math.max(2, R * 0.034),
      arcs: [[0, 0.30]], rotation: this.sweep * 0.5,
      colour: `rgba(236, 255, 255, ${0.62 + energy * 0.34})`,
      glow: 18 + energy * 20,
      fade: true,
    });

    // 6 — inner dashed ring
    this._tickRing(ctx, {
      radius: R * 0.50, count: 48, length: R * 0.030, longEvery: 4,
      longLength: R * 0.030, rotation: p * 0.42, width: 1.4,
      colour: `rgba(118, 226, 222, ${0.26 + energy * 0.26})`,
      longColour: `rgba(118, 226, 222, ${0.26 + energy * 0.26})`,
    });

    // 7 — two real 3D rings, for depth
    this._gyro(ctx, R * 0.78, 1.14 + Math.sin(p * 0.3) * 0.06 + tiltY, -p * 0.45, p * 0.72 + tiltX, energy, 190);
    this._gyro(ctx, R * 0.56, -0.98 + Math.cos(p * 0.27) * 0.07 - tiltY, p * 0.6, -p * 1.1 + tiltX, energy * 0.85, 176);

    // 8 — core
    this._core(ctx, R, energy, activity, level);
    this._label(ctx, R, energy);

    if (activity === 'listening' || activity === 'speaking') {
      this._waveform(ctx, R * 0.40, energy);
      this._pulse(ctx, R, level);
    }

    ctx.restore();
  }

  // ------------------------------------------------------------------ layers

  /** Wide, very soft wash behind everything so the dial sits in its own light. */

  /**
   * A crown of radial graduations. Short ticks go in one batched path, long
   * ticks in a second — two strokes per ring instead of `count` strokes.
   */
  _tickRing(ctx, o) {
    const short = new Path2D();
    const long = new Path2D();
    for (let i = 0; i < o.count; i++) {
      const a = o.rotation + (i / o.count) * TAU;
      const isLong = i % o.longEvery === 0;
      const len = isLong ? o.longLength : o.length;
      const r0 = o.inward ? o.radius - len : o.radius;
      const r1 = o.inward ? o.radius : o.radius + len;
      const c = Math.cos(a), s = Math.sin(a);
      const path = isLong ? long : short;
      path.moveTo(c * r0, s * r0);
      path.lineTo(c * r1, s * r1);
    }
    ctx.lineWidth = o.width;
    ctx.strokeStyle = o.colour;
    ctx.stroke(short);
    ctx.lineWidth = o.width * 1.5;
    ctx.strokeStyle = o.longColour;
    ctx.stroke(long);
  }

  /** Bright arc segments with gaps between them. */
  _arcRing(ctx, o) {
    ctx.lineWidth = o.width;
    ctx.shadowBlur = o.glow || 0;
    ctx.shadowColor = 'rgba(120, 236, 230, 0.75)';
    for (const [from, to] of o.arcs) {
      if (o.fade) {
        // A gradient along the arc makes the sweep read as a comet tail.
        const a0 = o.rotation + from * TAU;
        const a1 = o.rotation + to * TAU;
        const grad = ctx.createLinearGradient(
          Math.cos(a0) * o.radius, Math.sin(a0) * o.radius,
          Math.cos(a1) * o.radius, Math.sin(a1) * o.radius,
        );
        grad.addColorStop(0, 'rgba(120, 236, 230, 0)');
        grad.addColorStop(1, o.colour);
        ctx.strokeStyle = grad;
      } else {
        ctx.strokeStyle = o.colour;
      }
      ctx.beginPath();
      ctx.arc(0, 0, o.radius, o.rotation + from * TAU, o.rotation + to * TAU);
      ctx.stroke();
    }
    ctx.shadowBlur = 0;
  }

  /** A thin circle with small satellites riding on it. */
  _orbitRing(ctx, radius, rotation, energy) {
    ctx.lineWidth = 1;
    ctx.strokeStyle = `rgba(96, 200, 206, ${0.18 + energy * 0.18})`;
    ctx.beginPath();
    ctx.arc(0, 0, radius, 0, TAU);
    ctx.stroke();

    const dots = new Path2D();
    for (let i = 0; i < 6; i++) {
      const a = rotation + (i / 6) * TAU;
      const r = Math.max(1.4, radius * 0.022);
      dots.moveTo(Math.cos(a) * radius + r, Math.sin(a) * radius);
      dots.arc(Math.cos(a) * radius, Math.sin(a) * radius, r, 0, TAU);
    }
    ctx.fillStyle = `rgba(196, 250, 246, ${0.5 + energy * 0.4})`;
    ctx.shadowBlur = 10;
    ctx.shadowColor = 'rgba(140, 240, 236, 0.8)';
    ctx.fill(dots);
    ctx.shadowBlur = 0;
  }

  /**
   * A real 3D circle: points on a plane, rotated in space, projected flat.
   * Drawn in three passes (whole ring faint, near half brighter, nearest
   * quarter brightest) so the far side recedes instead of overlapping.
   */
  _gyro(ctx, radius, tilt, roll, spin, energy, hue) {
    const segments = 120;
    const pts = [];
    for (let i = 0; i <= segments; i++) {
      const a = (i / segments) * TAU;
      let p = { x: Math.cos(a) * radius, y: Math.sin(a) * radius, z: 0 };
      p = rotateX(p, tilt);
      p = rotateZ(p, roll);
      p = rotateY(p, spin);
      pts.push(p);
    }

    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
    ctx.strokeStyle = `hsla(${hue}, 80%, 60%, ${0.08 + energy * 0.08})`;
    ctx.lineWidth = 1;
    ctx.stroke();

    for (const [threshold, width, light, alpha, blur] of [
      [0, 1.4, 62, 0.26 + energy * 0.26, 8],
      [radius * 0.6, 2.0, 80, 0.42 + energy * 0.38, 16],
    ]) {
      ctx.strokeStyle = `hsla(${hue}, 92%, ${light}%, ${Math.min(1, alpha)})`;
      ctx.lineWidth = width;
      ctx.shadowBlur = blur * (0.5 + energy);
      ctx.shadowColor = `hsla(${hue}, 92%, 66%, 0.7)`;
      let drawing = false;
      ctx.beginPath();
      for (const p of pts) {
        if (p.z >= threshold) {
          if (drawing) ctx.lineTo(p.x, p.y);
          else { ctx.moveTo(p.x, p.y); drawing = true; }
        } else {
          drawing = false;
        }
      }
      ctx.stroke();
    }
    ctx.shadowBlur = 0;
  }

  _core(ctx, R, energy, activity, level) {
    const r = R * (0.34 + energy * 0.02);

    const fill = ctx.createRadialGradient(0, 0, 0, 0, 0, r);
    fill.addColorStop(0, `rgba(72, 206, 200, ${0.26 + energy * 0.20})`);
    fill.addColorStop(0.7, 'rgba(24, 96, 118, 0.14)');
    fill.addColorStop(1, 'rgba(10, 36, 58, 0.04)');
    ctx.fillStyle = fill;
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, TAU);
    ctx.fill();

    ctx.strokeStyle = `rgba(130, 232, 228, ${0.40 + energy * 0.34})`;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, TAU);
    ctx.stroke();

    // A second, tighter outline that breathes with the voice level.
    const inner = r * (0.72 + level * 0.06);
    ctx.strokeStyle = `rgba(166, 244, 240, ${0.22 + energy * 0.30})`;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(0, 0, inner, 0, TAU);
    ctx.stroke();

    if (activity === 'thinking') {
      // Three short arcs chasing each other inside the core.
      ctx.lineWidth = 2;
      ctx.strokeStyle = 'rgba(210, 255, 252, 0.7)';
      for (let i = 0; i < 3; i++) {
        const a = this.sweep * 1.6 + (i / 3) * TAU;
        ctx.beginPath();
        ctx.arc(0, 0, inner * 0.78, a, a + 0.5);
        ctx.stroke();
      }
    }
  }

  /** The centre label, letter-spaced by hand because canvas has no tracking. */
  _label(ctx, R, energy) {
    const text = this.label;
    const size = Math.max(7, Math.min(R * 0.115, 16));
    const tracking = size * 0.34;
    ctx.font = `600 ${size}px "Rajdhani", "Segoe UI", system-ui, sans-serif`;
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';

    let width = 0;
    for (const ch of text) width += ctx.measureText(ch).width + tracking;
    width -= tracking;
    if (width > R * 1.5) return;             // too cramped: leave the core clean

    ctx.shadowBlur = 12;
    ctx.shadowColor = 'rgba(140, 240, 236, 0.9)';
    ctx.fillStyle = `rgba(226, 255, 253, ${0.72 + energy * 0.28})`;
    let x = -width / 2;
    for (const ch of text) {
      ctx.fillText(ch, x, 0);
      x += ctx.measureText(ch).width + tracking;
    }
    ctx.shadowBlur = 0;
  }

  /** Level history drawn as a closed radial waveform inside the core. */
  _waveform(ctx, radius, energy) {
    const n = this.wave.length;
    ctx.beginPath();
    for (let i = 0; i <= n; i++) {
      const v = this.wave[i % n];
      const a = (i / n) * TAU - Math.PI / 2;
      const r = radius * (0.56 + v * 0.42);
      const x = Math.cos(a) * r;
      const y = Math.sin(a) * r;
      if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    }
    ctx.closePath();
    ctx.strokeStyle = `rgba(190, 250, 246, ${0.35 + energy * 0.35})`;
    ctx.lineWidth = 1.2;
    ctx.stroke();
  }

  _pulse(ctx, R, level) {
    for (let i = 0; i < 3; i++) {
      const phase = (this.phase * 0.45 + i / 3) % 1;
      const r = R * (0.3 + phase * 0.95);
      const alpha = (1 - phase) * (0.10 + level * 0.32);
      if (alpha <= 0.01) continue;
      ctx.strokeStyle = `rgba(120, 236, 230, ${alpha})`;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(0, 0, r, 0, TAU);
      ctx.stroke();
    }
  }
}

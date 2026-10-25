/**
 * FACE avatar — a holographic wireframe head.
 *
 * The mesh is a real surface: every point is lifted off the plane by a
 * hemispherical depth function and rotated in 3D, which is what makes the
 * contour lines read as a face instead of as flat ovals.
 */

const TAU = Math.PI * 2;

/**
 * Half-width of the head at height y (-1 top .. 1 chin).
 * The taper is what turns an egg into a face: a wide brow and a narrow jaw.
 */
function halfWidth(y) {
  return 0.90 * (1 - 0.10 * y - 0.20 * y * y);
}

/** Horizontal half-extent of the head outline at height y. */
function outline(y) {
  if (y <= -1 || y >= 1) return 0;
  return halfWidth(y) * Math.sqrt(1 - y * y);
}

/** Depth of the face surface at normalised (x, y) in [-1, 1]. */
function depth(x, y) {
  if (y <= -1 || y >= 1) return 0;
  const u = x / halfWidth(y);
  // One ellipsoidal support (u² + y² < 1), so the outline closes to a point at
  // the crown and the chin instead of ending in a flat edge.
  const r2 = u * u + y * y;
  if (r2 >= 1) return 0;
  const base = Math.sqrt(1 - r2);
  // Nose ridge: a soft gaussian bump along the centre line.
  const nose = Math.exp(-((x * x) / 0.010 + ((y - 0.10) * (y - 0.10)) / 0.055)) * 0.26;
  // Brow shelf and cheekbones, kept subtle so the mesh stays readable.
  const brow = Math.exp(-((Math.abs(x) - 0.33) ** 2 / 0.02 + (y + 0.30) ** 2 / 0.006)) * 0.09;
  const cheek = Math.exp(-((Math.abs(x) - 0.45) ** 2 / 0.03 + (y - 0.08) ** 2 / 0.05)) * 0.06;
  return base * 0.60 + nose + brow + cheek;
}

export default class FaceAvatar {
  constructor() {
    this.name = 'face';
    this.phase = 0;
    this.blink = 0;
    this.nextBlink = 2 + Math.random() * 3;
  }

  draw(ctx, env) {
    const { w, h, time, dt, activity, level, motion } = env;
    const scale = Math.min(w, h) * 0.46;
    const cx = w / 2;
    const cy = h / 2;

    this.phase += dt * motion;
    const yaw = Math.sin(this.phase * 0.35) * 0.26 * motion;
    const pitch = Math.sin(this.phase * 0.23 + 1) * 0.1 * motion;

    // Blinking: an idle tic that makes the face feel alive.
    this.nextBlink -= dt * motion;
    if (this.nextBlink <= 0) {
      this.blink = 0.22;
      this.nextBlink = 2.5 + Math.random() * 4;
    }
    if (this.blink > 0) this.blink = Math.max(0, this.blink - dt);
    const eyeOpen = this.blink > 0 ? Math.max(0.08, 1 - this.blink / 0.11) : 1;

    const energy = activity === 'listening' ? 0.4 + level * 0.7
      : activity === 'speaking' ? 0.55 + level * 0.6
      : activity === 'thinking' ? 0.5 : 0.28;

    const project = (x, y) => {
      const z = depth(x, y);
      // yaw around Y, then pitch around X
      const x1 = x * Math.cos(yaw) + z * Math.sin(yaw);
      const z1 = -x * Math.sin(yaw) + z * Math.cos(yaw);
      const y1 = y * Math.cos(pitch) - z1 * Math.sin(pitch);
      const z2 = y * Math.sin(pitch) + z1 * Math.cos(pitch);
      const persp = 1 / (1 - z2 * 0.18);
      return { x: cx + x1 * scale * persp, y: cy + y1 * scale * persp, z: z2, inside: z > 0.001 };
    };

    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    this._halo(ctx, cx, cy, scale, energy);
    this._contours(ctx, project, energy);
    this._silhouette(ctx, project, energy);
    this._features(ctx, project, energy);
    this._eyes(ctx, project, energy, eyeOpen, activity, time);
    this._mouth(ctx, project, activity, level, time, motion);
    this._scanline(ctx, project, cx, cy, scale, time, motion);

    ctx.restore();
  }

  _halo(ctx, cx, cy, scale, energy) {
    const glow = ctx.createRadialGradient(cx, cy, scale * 0.1, cx, cy, scale * 1.5);
    glow.addColorStop(0, `rgba(60, 190, 255, ${0.14 + energy * 0.1})`);
    glow.addColorStop(1, 'rgba(5, 20, 45, 0)');
    ctx.fillStyle = glow;
    ctx.fillRect(cx - scale * 1.6, cy - scale * 1.6, scale * 3.2, scale * 3.2);
  }

  /** Horizontal + vertical mesh lines following the surface. */
  _contours(ctx, project, energy) {
    const rows = 15;
    const cols = 13;

    for (let r = 1; r < rows; r++) {
      const y = -1 + (2 * r) / rows;
      const pts = [];
      for (let c = 0; c <= 80; c++) {
        const x = -1 + (2 * c) / 80;
        if (depth(x, y) <= 0.001) continue;
        pts.push(project(x, y));
      }
      this._stroke(ctx, pts, `rgba(90, 215, 255, ${0.1 + energy * 0.16})`, 1);
    }

    for (let c = 1; c < cols; c++) {
      const x = -1 + (2 * c) / cols;
      const pts = [];
      for (let r = 0; r <= 80; r++) {
        const y = -1 + (2 * r) / 80;
        if (depth(x, y) <= 0.001) continue;
        pts.push(project(x, y));
      }
      this._stroke(ctx, pts, `rgba(70, 185, 255, ${0.08 + energy * 0.13})`, 1);
    }
  }

  /** Outline traced along the edge of the depth support, so it tapers too. */
  _silhouette(ctx, project, energy) {
    const pts = [];
    const steps = 80;
    for (let i = 0; i <= steps; i++) {
      const y = -0.999 + (1.998 * i) / steps;
      pts.push(project(outline(y) * 0.998, y));
    }
    for (let i = steps; i >= 0; i--) {
      const y = -0.999 + (1.998 * i) / steps;
      pts.push(project(-outline(y) * 0.998, y));
    }
    ctx.shadowBlur = 18;
    ctx.shadowColor = 'rgba(70, 215, 255, 0.55)';
    this._stroke(ctx, pts, `rgba(150, 245, 255, ${0.55 + energy * 0.35})`, 2, true);
    ctx.shadowBlur = 0;
  }

  /** Brows, nose ridge and jaw accents — the lines that read as a face. */
  _features(ctx, project, energy) {
    const colour = `rgba(135, 240, 255, ${0.4 + energy * 0.3})`;

    for (const side of [-1, 1]) {
      // brow arc
      const brow = [];
      for (let i = 0; i <= 18; i++) {
        const k = i / 18;
        const x = side * (0.14 + k * 0.42);
        const y = -0.34 - Math.sin(k * Math.PI) * 0.055;
        brow.push(project(x, y));
      }
      this._stroke(ctx, brow, colour, 1.6);

    }

    // nose: ridge down the centre, then a small base curve with two nostrils
    const ridge = [];
    for (let i = 0; i <= 10; i++) {
      const k = i / 10;
      ridge.push(project(0, -0.20 + k * 0.40));
    }
    this._stroke(ctx, ridge, colour, 1.3);
    const base = [];
    for (let i = 0; i <= 16; i++) {
      const k = -1 + (2 * i) / 16;
      base.push(project(k * 0.10, 0.205 + (1 - Math.cos(k * Math.PI)) * 0.010));
    }
    this._stroke(ctx, base, colour, 1.2);
  }

  _eyes(ctx, project, energy, open, activity, time) {
    for (const side of [-1, 1]) {
      const ex = side * 0.36;
      const ey = -0.17;
      const centre = project(ex, ey);
      const half = 0.17;
      const pts = [];
      for (let i = 0; i <= 40; i++) {
        const t = (i / 40) * TAU;
        const x = ex + Math.cos(t) * half;
        const y = ey + Math.sin(t) * half * 0.46 * open;
        pts.push(project(x, y));
      }
      ctx.shadowBlur = 14;
      ctx.shadowColor = 'rgba(120, 240, 255, 0.8)';
      this._stroke(ctx, pts, `rgba(180, 250, 255, ${0.7 + energy * 0.3})`, 1.6, true);
      ctx.shadowBlur = 0;

      // Iris: brightens while listening, drifts slowly while thinking.
      const drift = activity === 'thinking' ? Math.sin(time * 1.5 + side) * 0.03 : 0;
      const iris = project(ex + drift, ey);
      const r = Math.abs(centre.x - project(ex + 0.06, ey).x) * (activity === 'listening' ? 1.25 : 1);
      const g = ctx.createRadialGradient(iris.x, iris.y, 0, iris.x, iris.y, Math.max(2, r * 1.6));
      g.addColorStop(0, `rgba(210, 255, 255, ${0.85 * open})`);
      g.addColorStop(1, 'rgba(60, 190, 255, 0)');
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(iris.x, iris.y, Math.max(2, r * 1.6), 0, TAU);
      ctx.fill();
    }
  }

  _mouth(ctx, project, activity, level, time, motion) {
    const openness = activity === 'speaking'
      ? 0.035 + Math.abs(Math.sin(time * 9 * motion)) * (0.05 + level * 0.09)
      : 0.012;
    const pts = [];
    for (let i = 0; i <= 36; i++) {
      const t = (i / 36) * TAU;
      const c = Math.cos(t);
      const x = c * 0.25;
      // Upper lip dips slightly in the middle; the lower one hangs with speech.
      const lift = Math.sin(t) > 0 ? -0.018 * (1 - c * c) : 0;
      const y = 0.44 + Math.sin(t) * openness + lift;
      pts.push(project(x, y));
    }
    ctx.shadowBlur = 10;
    ctx.shadowColor = 'rgba(90, 225, 255, 0.6)';
    this._stroke(ctx, pts, 'rgba(165, 245, 255, 0.8)', 1.5, true);
    ctx.shadowBlur = 0;
  }

  /** A scan sweep clipped to the head, so it reads as a hologram refresh. */
  _scanline(ctx, project, cx, cy, scale, time, motion) {
    const t = (time * 0.22 * motion) % 1.4;        // a pause between sweeps
    if (t > 1) return;
    const yNorm = -1 + t * 2;
    const hw = outline(yNorm);
    if (hw <= 0.02) return;
    const left = project(-hw, yNorm);
    const right = project(hw, yNorm);
    const mid = (left.y + right.y) / 2;
    const grad = ctx.createLinearGradient(0, mid - 10, 0, mid + 10);
    grad.addColorStop(0, 'rgba(90, 230, 255, 0)');
    grad.addColorStop(0.5, 'rgba(150, 245, 255, 0.22)');
    grad.addColorStop(1, 'rgba(90, 230, 255, 0)');
    ctx.fillStyle = grad;
    ctx.fillRect(left.x, mid - 10, Math.max(2, right.x - left.x), 20);
    this._stroke(ctx, [left, right], 'rgba(170, 250, 255, 0.35)', 1);
  }

  _stroke(ctx, pts, colour, width, close) {
    if (pts.length < 2) return;
    ctx.strokeStyle = colour;
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
    if (close) ctx.closePath();
    ctx.stroke();
  }
}

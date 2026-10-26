/**
 * FACE visualiser — a holographic head.
 *
 * This is a real volumetric surface, not an outline with features painted on
 * it. A sphere is sculpted into a head by a set of smooth deformations (jaw
 * taper, brow ridge, eye sockets, nose, cheekbones, lips, chin), the mesh is
 * built once with per-vertex normals, and every frame only rotates and
 * projects it. That is what makes the contours wrap around the volume instead
 * of reading as a flat geometric mask.
 *
 * Shading is rim-weighted: a segment whose normal points away from the camera
 * is drawn brightest, the ones facing straight at you are dimmest. That single
 * rule is what gives a wireframe the glassy, back-lit look of a projection.
 *
 * Cost control: the mesh is cached (positions + normals never change), and the
 * thousands of segments are batched into four brightness buckets — four
 * stroked paths per frame rather than several thousand.
 */

const TAU = Math.PI * 2;

const ROWS = 32;        // latitude lines, crown to chin
const COLS = 52;        // longitude lines, all the way round
const FOCAL = 3.4;      // perspective distance
const MOUTH_Y = -0.60;  // lip line: between the base of the nose and the chin

/**
 * The head is a stack of cross-sections, not a tapered sphere: a sphere gives
 * an egg, and an egg does not read as a face. These are the measurements that
 * matter — half-width and half-depth at each height from the crown (y = +1) to
 * the chin (y = -1) — with the widest point at the cheekbones and a distinct
 * jaw angle a third of the way down the face.
 */
const PROFILE = [
  //  y     half-width  half-depth  z-centre (skull bulges backwards)
  [ 1.00,   0.090,      0.120,      -0.030],
  [ 0.92,   0.390,      0.480,      -0.055],
  [ 0.78,   0.590,      0.720,      -0.070],
  [ 0.58,   0.690,      0.830,      -0.072],
  [ 0.34,   0.740,      0.880,      -0.060],
  [ 0.10,   0.762,      0.900,      -0.038],
  [-0.14,   0.752,      0.880,      -0.016],
  [-0.34,   0.712,      0.820,       0.000],
  [-0.52,   0.648,      0.736,       0.008],   // jaw angle
  [-0.70,   0.516,      0.600,       0.012],
  [-0.86,   0.310,      0.396,       0.010],
  [-1.00,   0.075,      0.120,       0.004],
];

/** Catmull-Rom through the profile table: smooth, and it hits every control point. */
function profileAt(y) {
  const n = PROFILE.length;
  let i = 1;
  while (i < n - 1 && PROFILE[i][0] > y) i++;
  const p1 = PROFILE[i - 1];
  const p2 = PROFILE[i];
  const p0 = PROFILE[Math.max(0, i - 2)];
  const p3 = PROFILE[Math.min(n - 1, i + 1)];
  const span = p1[0] - p2[0] || 1;
  const t = Math.min(1, Math.max(0, (p1[0] - y) / span));
  const t2 = t * t, t3 = t2 * t;
  const out = [0, 0, 0];
  for (let k = 1; k <= 3; k++) {
    out[k - 1] = 0.5 * (
      (2 * p1[k])
      + (-p0[k] + p2[k]) * t
      + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
      + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3
    );
  }
  return { w: Math.max(0.01, out[0]), d: Math.max(0.01, out[1]), zc: out[2] };
}

/**
 * How far the facial features push the surface forward at (x, y).
 * `front` is cos(azimuth): 1 dead centre, 0 at the ears — so nothing sculpted
 * here bleeds around the side of the head.
 */
function features(x, y, front) {
  if (front <= 0) return 0;
  const ax = Math.abs(x);
  const g = (v, c, s) => Math.exp(-((v - c) * (v - c)) / s);

  // Vertical placement follows the usual proportions of a head: the eye line
  // sits at mid height, the base of the nose two thirds down the face, and the
  // mouth between that and the chin.
  const brow     =  0.055 * g(y, 0.16, 0.005)   * g(ax, 0.24, 0.070);
  const socket   = -0.070 * g(ax, 0.30, 0.014)  * g(y, 0.00, 0.009);
  const bridge   =  0.055 * g(x, 0, 0.009)      * g(y, 0.00, 0.030);
  const nose     =  0.115 * g(x, 0, 0.008)      * g(y, -0.34, 0.020);
  const wings    =  0.034 * g(ax, 0.090, 0.002) * g(y, -0.41, 0.004);
  const philtrum = -0.018 * g(x, 0, 0.003)      * g(y, -0.50, 0.005);
  const lips     =  0.048 * g(x, 0, 0.034)      * g(y, -0.60, 0.006);
  const cheek    =  0.040 * g(ax, 0.42, 0.022)  * g(y, -0.22, 0.030);
  const chin     =  0.046 * g(x, 0, 0.026)      * g(y, -0.84, 0.012);
  const forehead = -0.030 * g(y, 0.46, 0.060)   * g(x, 0, 0.240);

  const sum = brow + socket + bridge + nose + wings + philtrum + lips + cheek + chin + forehead;
  return sum * Math.pow(front, 1.5);
}

/**
 * The cross-section is a flattened circle, not a round one: a real face is
 * broad and shallow at the front, so |cos| is raised to a power below 1.
 */
function flatten(c) {
  return Math.sign(c) * Math.pow(Math.abs(c), 0.78);
}

/** Front-surface depth at (x, y) — used to sit the feature lines on the skin. */
function frontZ(x, y) {
  const { w, d, zc } = profileAt(y);
  const sinPhi = x / w;
  if (Math.abs(sinPhi) >= 1) return zc;
  const cosPhi = Math.sqrt(1 - sinPhi * sinPhi);
  return zc + d * flatten(cosPhi) + features(x, y, cosPhi);
}

function buildMesh() {
  const grid = [];
  for (let i = 0; i < ROWS; i++) {
    // Nudge off the exact poles: a pole row collapses to a single point and
    // its normal is undefined.
    const y = 1 - ((i + 0.5) / ROWS) * 2;
    const { w, d, zc } = profileAt(y);
    const row = [];
    for (let j = 0; j < COLS; j++) {
      const phi = (j / COLS) * TAU;
      const cosPhi = Math.cos(phi);
      const x = w * Math.sin(phi);
      const z = zc + d * flatten(cosPhi) + features(x, y, Math.max(0, cosPhi));
      row.push({ x, y, z, nx: 0, ny: 0, nz: 0 });
    }
    grid.push(row);
  }

  // Normals from the grid itself: the cross product of the two tangents.
  for (let i = 0; i < ROWS; i++) {
    for (let j = 0; j < COLS; j++) {
      const p = grid[i][j];
      const a = grid[i][(j + 1) % COLS];
      const b = grid[i][(j - 1 + COLS) % COLS];
      const c = grid[Math.min(ROWS - 1, i + 1)][j];
      const d = grid[Math.max(0, i - 1)][j];
      const ux = a.x - b.x, uy = a.y - b.y, uz = a.z - b.z;
      const vx = c.x - d.x, vy = c.y - d.y, vz = c.z - d.z;
      let nx = uy * vz - uz * vy;
      let ny = uz * vx - ux * vz;
      let nz = ux * vy - uy * vx;
      const len = Math.hypot(nx, ny, nz) || 1;
      nx /= len; ny /= len; nz /= len;
      // The blob is convex enough that an outward normal points away from the
      // centre; flip the ones the cross-product order got backwards.
      if (nx * p.x + ny * p.y + nz * p.z < 0) { nx = -nx; ny = -ny; nz = -nz; }
      p.nx = nx; p.ny = ny; p.nz = nz;
    }
  }
  return grid;
}

const MESH = buildMesh();

export default class FaceAvatar {
  constructor() {
    this.name = 'face';
    this.phase = 0;
    this.blink = 0;
    this.nextBlink = 2 + Math.random() * 3;
    this.jaw = 0;
    // Scratch buffers, reused every frame so the loop allocates nothing.
    this._px = new Float32Array(ROWS * COLS);
    this._py = new Float32Array(ROWS * COLS);
    this._pf = new Float32Array(ROWS * COLS);   // facing: rotated normal z
  }

  draw(ctx, env) {
    const { w, h, time, dt, activity, level, motion } = env;
    // Leave a margin on both axes: perspective makes the near side of the head
    // larger than its model size, and a clipped chin is very obvious.
    const scale = Math.min(w * 0.58, h * 0.42);
    const cx = w / 2;
    const cy = h / 2;

    this.phase += dt * motion;
    // HOLO parallax rides on top of the idle drift, so a drag turns the head.
    const yaw = Math.sin(this.phase * 0.33) * 0.30 * motion + (env.tiltX || 0) * 0.7;
    const pitch = Math.sin(this.phase * 0.21 + 1) * 0.09 * motion + (env.tiltY || 0) * 0.5;

    this.nextBlink -= dt * motion;
    if (this.nextBlink <= 0) {
      this.blink = 0.16;
      this.nextBlink = 2.5 + Math.random() * 4.5;
    }
    if (this.blink > 0) this.blink = Math.max(0, this.blink - dt);
    const eyeOpen = this.blink > 0 ? Math.max(0.06, Math.abs(1 - this.blink / 0.08)) : 1;

    // The jaw eases instead of snapping, so speech does not look like a hinge.
    const target = activity === 'speaking'
      ? 0.25 + Math.abs(Math.sin(time * 8.5 * motion)) * (0.35 + level * 0.65)
      : 0.04;
    this.jaw += (target - this.jaw) * Math.min(1, dt * 14);

    const energy = activity === 'listening' ? 0.42 + level * 0.65
      : activity === 'speaking' ? 0.56 + level * 0.55
      : activity === 'thinking' ? 0.52
      : 0.30;

    // One rotation matrix for the whole frame: yaw around Y, then pitch around X.
    const cya = Math.cos(yaw), sya = Math.sin(yaw);
    const cpi = Math.cos(pitch), spi = Math.sin(pitch);
    const rot = (x, y, z) => {
      const x1 = x * cya + z * sya;
      const z1 = -x * sya + z * cya;
      const y1 = y * cpi - z1 * spi;
      const z2 = y * spi + z1 * cpi;
      return { x: x1, y: y1, z: z2 };
    };
    const project = (x, y, z) => {
      const p = rot(x, y, z);
      const persp = FOCAL / (FOCAL - p.z);
      return { x: cx + p.x * scale * persp, y: cy - p.y * scale * persp, z: p.z };
    };

    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    this._halo(ctx, cx, cy, scale, energy);

    const { bounds, left, right } = this._mesh(ctx, rot, cx, cy, scale, energy);
    this._silhouette(ctx, left, right, energy);
    this._featureLines(ctx, project, energy);
    this._eyes(ctx, project, energy, eyeOpen, activity, time, motion);
    this._mouth(ctx, project, energy);
    this._scan(ctx, bounds, cy, scale, time, motion);

    ctx.restore();
  }

  // ------------------------------------------------------------------ layers

  _halo(ctx, cx, cy, scale, energy) {
    const g = ctx.createRadialGradient(cx, cy, scale * 0.12, cx, cy, scale * 1.6);
    g.addColorStop(0, `rgba(44, 160, 190, ${0.17 + energy * 0.12})`);
    g.addColorStop(0.6, 'rgba(20, 80, 120, 0.06)');
    g.addColorStop(1, 'rgba(5, 18, 40, 0)');
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(cx, cy, scale * 1.6, 0, TAU);
    ctx.fill();
  }

  /**
   * The wireframe itself. Returns the projected silhouette bounds per screen
   * row band, which the scan sweep uses to stay inside the head.
   */
  _mesh(ctx, rot, cx, cy, scale, energy) {
    const px = this._px, py = this._py, pf = this._pf;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    const left = [];
    const right = [];

    for (let i = 0; i < ROWS; i++) {
      for (let j = 0; j < COLS; j++) {
        const v = MESH[i][j];
        const p = rot(v.x, v.y, v.z);
        const n = rot(v.nx, v.ny, v.nz);
        const persp = FOCAL / (FOCAL - p.z);
        const k = i * COLS + j;
        px[k] = cx + p.x * scale * persp;
        py[k] = cy - p.y * scale * persp;
        pf[k] = n.z;
        if (n.z > 0.02) {
          if (px[k] < minX) minX = px[k];
          if (px[k] > maxX) maxX = px[k];
          if (py[k] < minY) minY = py[k];
          if (py[k] > maxY) maxY = py[k];
        }
      }

      // Where the facing crosses zero, the surface has turned away from the
      // camera: that point is on the visible edge. Interpolating between the
      // two neighbours puts it exactly on the crossing rather than on the
      // nearest vertex.
      const hits = [];
      for (let j = 0; j < COLS; j++) {
        const k1 = i * COLS + j;
        const k2 = i * COLS + ((j + 1) % COLS);
        if ((pf[k1] > 0) === (pf[k2] > 0)) continue;
        const t = pf[k1] / (pf[k1] - pf[k2]);
        hits.push({ x: px[k1] + (px[k2] - px[k1]) * t, y: py[k1] + (py[k2] - py[k1]) * t });
      }
      if (hits.length === 2) {
        const [a, b] = hits[0].x <= hits[1].x ? hits : [hits[1], hits[0]];
        left.push(a);
        right.push(b);
      }
    }

    // Four brightness buckets, one batched path each.
    const buckets = [new Path2D(), new Path2D(), new Path2D(), new Path2D()];
    const add = (k1, k2) => {
      const f1 = pf[k1], f2 = pf[k2];
      if (f1 <= 0.02 || f2 <= 0.02) return;          // back of the head
      const f = (f1 + f2) * 0.5;
      // Rim weighting: grazing angles are the bright ones.
      const alpha = 0.07 + 0.46 * Math.pow(1 - f, 2.2) + 0.09 * f;
      const bucket = Math.min(3, Math.max(0, Math.floor(alpha * 6)));
      const path = buckets[bucket];
      path.moveTo(px[k1], py[k1]);
      path.lineTo(px[k2], py[k2]);
    };

    for (let i = 0; i < ROWS; i++) {
      // The rows nearest the poles collapse towards a point; drawing them puts
      // a seam across the crown, so they are left to the silhouette.
      if (Math.abs(MESH[i][0].y) > 0.95) continue;
      for (let j = 0; j < COLS; j++) {
        add(i * COLS + j, i * COLS + ((j + 1) % COLS));          // latitude
        // Every second longitude only: a full grid reads as a net thrown over
        // the head, and costs twice the segments for it.
        if (j % 2 === 0 && i + 1 < ROWS && Math.abs(MESH[i + 1][0].y) <= 0.95) {
          add(i * COLS + j, (i + 1) * COLS + j);
        }
      }
    }

    const tints = [
      [0.14, 'rgba(78, 190, 232, A)', 1],
      [0.30, 'rgba(104, 214, 244, A)', 1],
      [0.52, 'rgba(148, 238, 252, A)', 1.1],
      [0.82, 'rgba(206, 250, 255, A)', 1.3],
    ];
    for (let b = 0; b < 4; b++) {
      const [alpha, colour, width] = tints[b];
      ctx.strokeStyle = colour.replace('A', String(alpha * (0.72 + energy * 0.5)));
      ctx.lineWidth = width;
      if (b === 3) {
        ctx.shadowBlur = 12 + energy * 10;
        ctx.shadowColor = 'rgba(120, 230, 255, 0.6)';
      }
      ctx.stroke(buckets[b]);
      ctx.shadowBlur = 0;
    }

    // Vertex sparkle: a thin scatter of points on the brightest, most grazing
    // part of the surface. It costs one fill and sells the "projection".
    const dots = new Path2D();
    for (let i = 1; i < ROWS; i += 2) {
      for (let j = 0; j < COLS; j += 3) {
        const k = i * COLS + j;
        if (pf[k] > 0.08 && pf[k] < 0.42) {
          dots.moveTo(px[k] + 0.9, py[k]);
          dots.arc(px[k], py[k], 0.9, 0, TAU);
        }
      }
    }
    ctx.fillStyle = `rgba(216, 250, 255, ${0.30 + energy * 0.30})`;
    ctx.fill(dots);

    return { bounds: { minX, maxX, minY, maxY }, left, right };
  }

  /**
   * The outline. It is taken from the mesh itself — the exact points where a
   * normal stops facing the camera — rather than from a fixed profile curve,
   * because the visible edge of a head moves as the head turns.
   */
  _silhouette(ctx, left, right, energy) {
    ctx.shadowBlur = 16 + energy * 12;
    ctx.shadowColor = 'rgba(90, 220, 255, 0.65)';
    const colour = `rgba(198, 248, 255, ${0.50 + energy * 0.34})`;
    this._stroke(ctx, left, colour, 1.8);
    this._stroke(ctx, right, colour, 1.8);
    ctx.shadowBlur = 0;
  }

  /** Brows, nose, cheeks and jawline, drawn on the skin so they catch the light. */
  _featureLines(ctx, project, energy) {
    const colour = `rgba(170, 244, 255, ${0.42 + energy * 0.34})`;
    const faint = `rgba(140, 230, 250, ${0.24 + energy * 0.22})`;
    const lift = 0.012;                        // keep the line off the mesh

    for (const side of [-1, 1]) {
      const brow = [];
      for (let i = 0; i <= 16; i++) {
        const k = i / 16;
        const x = side * (0.09 + k * 0.38);
        const y = 0.19 + Math.sin(k * Math.PI) * 0.038 - k * 0.075;
        brow.push(project(x, y, frontZ(x, y) + lift));
      }
      this._stroke(ctx, brow, colour, 1.5);

      // Jawline: from in front of the ear down to the point of the chin.
      const jaw = [];
      for (let i = 0; i <= 20; i++) {
        const k = i / 20;
        const y = -0.20 - k * 0.72;
        const { w } = profileAt(y);
        const x = side * w * (0.90 - k * 0.14);
        jaw.push(project(x, y, frontZ(x, y) + lift * 0.5));
      }
      this._stroke(ctx, jaw, faint, 1.2);

      // Cheek line, from the outer eye corner down across the cheekbone.
      const cheek = [];
      for (let i = 0; i <= 10; i++) {
        const k = i / 10;
        const x = side * (0.44 - k * 0.10);
        const y = -0.12 - k * 0.26;
        cheek.push(project(x, y, frontZ(x, y) + lift * 0.5));
      }
      this._stroke(ctx, cheek, faint, 1);

      // The bridge of the nose reads as its two shadowed sides, never as one
      // line down the middle — a centre line looks like a seam, not a nose.
      const bridgeLine = [];
      for (let i = 0; i <= 12; i++) {
        const k = i / 12;
        const x = side * (0.070 - k * k * 0.016);
        const y = 0.04 - k * 0.42;
        bridgeLine.push(project(x, y, frontZ(x, y) + lift));
      }
      this._stroke(ctx, bridgeLine, faint, 1.1);
    }

    // The base of the nose: tip, then a wing curling back on each side.
    const tip = [];
    for (let i = 0; i <= 20; i++) {
      const k = -1 + (2 * i) / 20;
      const x = k * 0.105;
      const y = -0.415 + Math.cos(k * Math.PI * 0.5) * 0.034;
      tip.push(project(x, y, frontZ(x, y) + lift));
    }
    this._stroke(ctx, tip, colour, 1.3);
  }

  _eyes(ctx, project, energy, open, activity, time, motion) {
    for (const side of [-1, 1]) {
      const ex = side * 0.30;
      const ey = 0.00;

      const lid = [];
      for (let i = 0; i <= 36; i++) {
        const a = (i / 36) * TAU;
        // An almond, not a circle: the upper lid arcs higher than the lower,
        // and both corners are pulled to a point by the cos² narrowing.
        const c = Math.cos(a);
        const x = ex + c * 0.135;
        const taper = 1 - c * c * 0.25;
        const upper = Math.sin(a) > 0 ? 1.0 : 0.66;
        const y = ey + Math.sin(a) * 0.050 * upper * taper * open;
        lid.push(project(x, y, frontZ(x, y) + 0.015));
      }
      ctx.shadowBlur = 12;
      ctx.shadowColor = 'rgba(130, 240, 255, 0.8)';
      this._stroke(ctx, lid, `rgba(205, 252, 255, ${0.70 + energy * 0.30})`, 1.5, true);
      ctx.shadowBlur = 0;

      // Lid crease: the line that stops an eye looking like a pasted-on oval.
      const crease = [];
      for (let i = 0; i <= 12; i++) {
        const k = -1 + (2 * i) / 12;
        const x = ex + k * 0.115;
        const y = ey + 0.080 - k * k * 0.020;
        crease.push(project(x, y, frontZ(x, y) + 0.012));
      }
      this._stroke(ctx, crease, `rgba(150, 236, 255, ${0.26 + energy * 0.22})`, 1);

      // The iris drifts while thinking and widens while listening.
      const drift = activity === 'thinking' ? Math.sin(time * 1.4 * motion + side) * 0.035 : 0;
      const centre = project(ex + drift, ey, frontZ(ex + drift, ey) + 0.02);
      const edge = project(ex + drift + 0.05, ey, frontZ(ex + drift + 0.05, ey));
      const r = Math.max(2, Math.abs(edge.x - centre.x)) * (activity === 'listening' ? 1.3 : 1) * open;

      const g = ctx.createRadialGradient(centre.x, centre.y, 0, centre.x, centre.y, r * 1.9);
      g.addColorStop(0, `rgba(235, 255, 255, ${0.9 * open})`);
      g.addColorStop(0.45, `rgba(110, 225, 255, ${0.45 * open})`);
      g.addColorStop(1, 'rgba(50, 170, 255, 0)');
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(centre.x, centre.y, r * 1.9, 0, TAU);
      ctx.fill();
    }
  }

  _mouth(ctx, project, energy) {
    const open = this.jaw * 0.085;
    const halfWidth = 0.20 + this.jaw * 0.02;

    // Upper and lower lip as two separate curves, so an open mouth shows a gap
    // instead of one stretched ellipse.
    for (const [sign, offset, width] of [[-1, -0.012, 1.5], [1, 0.004, 1.3]]) {
      const pts = [];
      for (let i = 0; i <= 24; i++) {
        const k = -1 + (2 * i) / 24;
        const x = k * halfWidth;
        const bow = sign < 0 ? Math.cos(k * Math.PI * 1.5) * 0.010 : 0;
        const y = MOUTH_Y + offset + sign * open * (1 - k * k) - bow;
        pts.push(project(x, y, frontZ(x, y) + 0.014));
      }
      ctx.shadowBlur = 9;
      ctx.shadowColor = 'rgba(100, 230, 255, 0.55)';
      this._stroke(ctx, pts, `rgba(190, 248, 255, ${0.62 + energy * 0.3})`, width);
      ctx.shadowBlur = 0;
    }

    if (open > 0.012) {
      // The dark of the open mouth, so speech has depth.
      const pts = [];
      for (let i = 0; i <= 20; i++) {
        const k = -1 + (2 * i) / 20;
        const x = k * halfWidth * 0.92;
        const y = MOUTH_Y - open * (1 - k * k) * 0.92;
        pts.push(project(x, y, frontZ(x, y) + 0.012));
      }
      for (let i = 20; i >= 0; i--) {
        const k = -1 + (2 * i) / 20;
        const x = k * halfWidth * 0.92;
        const y = MOUTH_Y + open * (1 - k * k) * 0.92;
        pts.push(project(x, y, frontZ(x, y) + 0.012));
      }
      ctx.beginPath();
      pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.closePath();
      ctx.fillStyle = 'rgba(8, 40, 66, 0.55)';
      ctx.fill();
    }
  }

  /** A sweep that crosses the head, clipped to its silhouette. */
  _scan(ctx, bounds, cy, scale, time, motion) {
    if (!Number.isFinite(bounds.minX)) return;
    const t = (time * 0.2 * motion) % 1.6;
    if (t > 1) return;                            // a pause between sweeps
    const y = bounds.minY + (bounds.maxY - bounds.minY) * t;
    // Narrow the band towards the crown and the chin so it hugs the outline.
    const k = Math.sin(t * Math.PI);
    const half = ((bounds.maxX - bounds.minX) / 2) * (0.35 + 0.65 * k);
    const mid = (bounds.minX + bounds.maxX) / 2;
    const band = Math.max(6, scale * 0.07);

    const g = ctx.createLinearGradient(0, y - band, 0, y + band);
    g.addColorStop(0, 'rgba(90, 225, 255, 0)');
    g.addColorStop(0.5, 'rgba(165, 245, 255, 0.20)');
    g.addColorStop(1, 'rgba(90, 225, 255, 0)');
    ctx.fillStyle = g;
    ctx.fillRect(mid - half, y - band, half * 2, band * 2);

    ctx.strokeStyle = 'rgba(190, 250, 255, 0.34)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(mid - half, y);
    ctx.lineTo(mid + half, y);
    ctx.stroke();
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

/**
 * Procedural anatomy for the holographic bust.
 *
 * This builds the geometry once, at load, and hands the renderer plain typed
 * arrays. Nothing here touches WebGL, so it can be unit-tested and reused.
 *
 * The bust is two swept surfaces rather than one:
 *
 *   head   crown → chin, with the facial anatomy sculpted onto it
 *   torso  under the jaw → neck → shoulders → chest cut
 *
 * Two surfaces, because a single sweep cannot put the chin *in front of* the
 * neck: both would have to occupy the same height. They overlap inside the
 * jaw, which is invisible — the hologram is additive, so an interior surface
 * only adds a little glow where the jaw meets the throat.
 *
 * Coordinates: y up, x right, z towards the viewer. The head spans y = +1
 * (crown) to y = -1 (chin), so every measurement below is a fraction of half
 * the head height.
 *
 * The model is an original construction: a cross-section profile plus a list
 * of smooth anatomical deformations. It is not a scan and not a likeness.
 */

const TAU = Math.PI * 2;

// ---------------------------------------------------------------- profiles

/**
 * Head cross-sections: [y, half-width, half-depth, z-centre, flatten].
 * The widest point is the cheekbone, there is a real jaw angle a third of the
 * way down the face, and the skull is deeper than it is wide.
 */
const HEAD_PROFILE = [
  //  A male skull: the crown is flatter, the brow shelf wider, and the jaw
  //  keeps its width most of the way to the chin instead of tapering to a
  //  point. Those three things are most of what reads as "male" in a
  //  silhouette, before a single feature is sculpted.
  [ 1.06,  0.190, 0.230, -0.030, 0.94],
  [ 1.00,  0.380, 0.440, -0.048, 0.90],
  [ 0.92,  0.520, 0.600, -0.064, 0.86],
  [ 0.80,  0.640, 0.748, -0.076, 0.83],
  [ 0.62,  0.706, 0.836, -0.082, 0.80],
  [ 0.40,  0.746, 0.880, -0.076, 0.78],
  [ 0.18,  0.760, 0.900, -0.058, 0.76],
  [ 0.00,  0.762, 0.902, -0.034, 0.76],
  [-0.20,  0.752, 0.884, -0.012, 0.76],
  [-0.40,  0.724, 0.822,  0.004, 0.77],
  [-0.58,  0.672, 0.738,  0.014, 0.78],   // gonion — the corner of the jaw
  [-0.76,  0.556, 0.616,  0.020, 0.80],
  [-0.90,  0.392, 0.438,  0.018, 0.83],
  [-1.02,  0.168, 0.230,  0.012, 0.88],
];

/** Neck and shoulders: [y, half-width, half-depth, z-centre, flatten]. */
const TORSO_PROFILE = [
  [-0.50,  0.300, 0.330, -0.180, 0.85],   // inside the jaw, never seen
  [-0.80,  0.318, 0.345, -0.185, 0.85],
  [-1.10,  0.428, 0.392, -0.180, 0.85],
  [-1.34,  0.528, 0.428, -0.170, 0.82],   // the neck starts to flare
  [-1.52,  0.712, 0.470, -0.150, 0.78],   // trapezius
  [-1.68,  1.090, 0.516, -0.130, 0.70],
  [-1.82,  1.470, 0.552, -0.115, 0.62],
  [-1.95,  1.720, 0.580, -0.105, 0.58],
  [-2.10,  1.820, 0.596, -0.100, 0.56],   // chest cut
];

/** Catmull-Rom through a profile table: smooth, and it hits every control point. */
function sample(table, y) {
  const n = table.length;
  let i = 1;
  while (i < n - 1 && table[i][0] > y) i++;
  const p1 = table[i - 1];
  const p2 = table[i];
  const p0 = table[Math.max(0, i - 2)];
  const p3 = table[Math.min(n - 1, i + 1)];
  const span = p1[0] - p2[0] || 1;
  const t = Math.min(1, Math.max(0, (p1[0] - y) / span));
  const t2 = t * t;
  const t3 = t2 * t;
  const out = [0, 0, 0, 0];
  for (let k = 1; k <= 4; k++) {
    out[k - 1] = 0.5 * (
      (2 * p1[k])
      + (-p0[k] + p2[k]) * t
      + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
      + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3
    );
  }
  return { w: Math.max(0.004, out[0]), d: Math.max(0.004, out[1]), zc: out[2], flat: out[3] };
}

/** The cross-section is broad and shallow at the front, not a circle. */
function flatten(c, power) {
  return Math.sign(c) * Math.pow(Math.abs(c), power);
}

// ---------------------------------------------------------------- anatomy

const g1 = (v, c, s) => Math.exp(-((v - c) * (v - c)) / s);
const g2 = (a, ca, sa, b, cb, sb) => Math.exp(-(((a - ca) * (a - ca)) / sa + ((b - cb) * (b - cb)) / sb));

/**
 * How far the face pushes forward at (x, y), in units of head half-height.
 * `front` is cos(azimuth): 1 dead centre, 0 at the ears, so nothing sculpted
 * here wraps around the side of the skull.
 *
 * Vertical placement follows the proportions of an adult male head: the eye
 * line at mid height, the base of the nose two thirds down the face, the lip
 * line between that and the chin.
 */
function faceRelief(x, y, front) {
  if (front <= 0) return 0;
  const ax = Math.abs(x);

  // --- brow and forehead -------------------------------------------------
  const brow      =  0.078 * g2(ax, 0.26, 0.060, y,  0.158, 0.0042);
  const glabella  =  0.030 * g2(x,  0.00, 0.014, y,  0.186, 0.0058);
  const forehead  = -0.022 * g2(x,  0.00, 0.260, y,  0.470, 0.0550);
  const temple    = -0.030 * g2(ax, 0.60, 0.022, y,  0.230, 0.0260);

  // --- eyes --------------------------------------------------------------
  const socket    = -0.090 * g2(ax, 0.300, 0.0170, y, -0.004, 0.0082);
  const eyeball   =  0.048 * g2(ax, 0.300, 0.0070, y, -0.008, 0.0036);
  const upperLid  =  0.014 * g2(ax, 0.300, 0.0095, y,  0.052, 0.0016);
  const lowerLid  =  0.010 * g2(ax, 0.300, 0.0095, y, -0.062, 0.0014);

  // --- nose --------------------------------------------------------------
  const nasion    = -0.016 * g2(x, 0.00, 0.0070, y,  0.120, 0.0030);
  const dorsum    =  0.092 * g2(x, 0.00, 0.0070, y, -0.120, 0.0400);
  const tip       =  0.140 * g2(x, 0.00, 0.0058, y, -0.350, 0.0050);
  const alar      =  0.056 * g2(ax, 0.098, 0.0024, y, -0.388, 0.0040);
  const sill      = -0.026 * g2(ax, 0.052, 0.0016, y, -0.436, 0.0014);

  // --- mouth -------------------------------------------------------------
  const philtrum  = -0.020 * g2(x, 0.000, 0.0030, y, -0.500, 0.0042);
  const upperLip  =  0.042 * g2(x, 0.000, 0.0280, y, -0.566, 0.0028);
  const lipLine   = -0.020 * g2(x, 0.000, 0.0330, y, -0.602, 0.0009);
  const lowerLip  =  0.046 * g2(x, 0.000, 0.0260, y, -0.645, 0.0030);

  // --- cheeks and jaw ----------------------------------------------------
  const zygomatic =  0.056 * g2(ax, 0.448, 0.0175, y, -0.095, 0.0200);
  const buccal    = -0.040 * g2(ax, 0.378, 0.0145, y, -0.350, 0.0195);
  // The nasolabial fold runs from beside the nostril down past the mouth.
  const foldT     = Math.max(0, Math.min(1, (-0.42 - y) / 0.24));
  const fold      = -0.022 * g2(ax, 0.132 + foldT * 0.115, 0.0022, y, -0.52, 0.0180);
  const masseter  =  0.040 * g2(ax, 0.530, 0.0230, y, -0.440, 0.0270);

  // --- chin: square, not pointed -----------------------------------------
  const sulcus    = -0.028 * g2(x, 0.000, 0.0340, y, -0.735, 0.0022);
  const mental    =  0.064 * g2(x, 0.000, 0.0420, y, -0.840, 0.0105);

  const sum = brow + glabella + forehead + temple
    + socket + eyeball + upperLid + lowerLid
    + nasion + dorsum + tip + alar + sill
    + philtrum + upperLip + lipLine + lowerLip
    + zygomatic + buccal + fold + masseter
    + sulcus + mental;

  return sum * Math.pow(front, 1.45);
}

/** Sideways relief: the ears and the flare of the mandible. */
function sideRelief(y, z, side) {
  if (side <= 0) return 0;
  const ear = 0.072 * g2(y, -0.030, 0.0180, z, -0.230, 0.0220);
  return ear * Math.pow(side, 2.2);
}

/**
 * Short hair: a shell lifted off the skull above the hairline, with the
 * hairline itself dipping at the temples the way a male one does.
 */
function hairLift(x, y, front, radial) {
  const ax = Math.abs(x);
  // Hairline: high at the centre, lower over the temples.
  const line = 0.400 - 0.085 * Math.pow(Math.max(0, front), 2) + 0.090 * g1(ax, 0.58, 0.030);
  const above = (y - line) / 0.12;
  const edge = 1 / (1 + Math.exp(-above * 2.6));           // soft logistic edge
  // Behind the ears the hair runs lower down the skull.
  const back = 1 - Math.max(0, front);
  const lowered = 1 / (1 + Math.exp(-((y + 0.18) / 0.10) * 2.0));
  const mask = Math.max(edge, back * lowered * 0.92);
  // A little texture so it does not read as a moulded helmet.
  const texture = 1 + 0.22 * Math.sin(radial * 17.0 + y * 9.0) * Math.sin(y * 31.0);
  return 0.052 * mask * texture;
}

// ---------------------------------------------------------------- building

const JAW_HINGE = { y: -0.235, z: -0.430 };

/**
 * One swept surface, as an indexed triangle mesh with per-vertex normals and
 * the two animation weights the shader needs.
 *
 * @returns {{position:Float32Array, normal:Float32Array, weight:Float32Array,
 *            index:Uint16Array|Uint32Array, line:Uint16Array|Uint32Array,
 *            rows:number, cols:number}}
 */
function sweep(profile, rows, cols, { face = false } = {}) {
  const top = profile[0][0];
  const bottom = profile[profile.length - 1][0];
  const count = rows * cols;
  const position = new Float32Array(count * 3);
  const normal = new Float32Array(count * 3);
  const weight = new Float32Array(count * 2);

  for (let i = 0; i < rows; i++) {
    const y = top + ((bottom - top) * i) / (rows - 1);
    const { w, d, zc, flat } = sample(profile, y);
    for (let j = 0; j < cols; j++) {
      const phi = (j / cols) * TAU;
      const sinPhi = Math.sin(phi);
      const cosPhi = Math.cos(phi);
      let x = w * sinPhi;
      let z = zc + d * flatten(cosPhi, flat);

      if (face) {
        const front = Math.max(0, cosPhi);
        const side = Math.abs(sinPhi);
        z += faceRelief(x, y, front);
        x += Math.sign(sinPhi || 1) * sideRelief(y, z, side);
        // Hair inflates along the outward radial direction, not along z.
        const lift = hairLift(x, y, front, phi);
        if (lift > 0.0005) {
          const len = Math.hypot(x, z - zc) || 1;
          x += (x / len) * lift;
          z += ((z - zc) / len) * lift;
        }
      }

      const k = (i * cols + j) * 3;
      position[k] = x;
      position[k + 1] = y;
      position[k + 2] = z;
    }
  }

  // Normals from the grid itself: the cross product of the two tangents.
  for (let i = 0; i < rows; i++) {
    for (let j = 0; j < cols; j++) {
      const at = (r, c) => ((r * cols) + ((c + cols) % cols)) * 3;
      const k = at(i, j);
      const a = at(i, j + 1);
      const b = at(i, j - 1);
      const c = at(Math.min(rows - 1, i + 1), j);
      const e = at(Math.max(0, i - 1), j);
      const ux = position[a] - position[b];
      const uy = position[a + 1] - position[b + 1];
      const uz = position[a + 2] - position[b + 2];
      const vx = position[c] - position[e];
      const vy = position[c + 1] - position[e + 1];
      const vz = position[c + 2] - position[e + 2];
      let nx = uy * vz - uz * vy;
      let ny = uz * vx - ux * vz;
      let nz = ux * vy - uy * vx;
      const len = Math.hypot(nx, ny, nz) || 1;
      nx /= len; ny /= len; nz /= len;
      // Point every normal outward: the surface is convex enough around its
      // own axis that the radial direction is a reliable reference.
      const rx = position[k];
      const rz = position[k + 2] - sample(profile, position[k + 1]).zc;
      if (nx * rx + nz * rz < 0) { nx = -nx; ny = -ny; nz = -nz; }
      normal[k] = nx;
      normal[k + 1] = ny;
      normal[k + 2] = nz;
    }
  }

  // Animation weights.
  for (let i = 0; i < rows; i++) {
    for (let j = 0; j < cols; j++) {
      const k = i * cols + j;
      const x = position[k * 3];
      const y = position[k * 3 + 1];
      const phi = (j / cols) * TAU;
      const front = Math.cos(phi);
      let jaw = 0;
      let lid = 0;
      if (face) {
        // The jaw carries everything below the hinge, fading out towards the
        // back of the skull so the cranium stays still.
        const down = Math.max(0, Math.min(1, (-0.26 - y) / 0.52));
        jaw = down * down * (3 - 2 * down) * Math.max(0, Math.min(1, front * 0.6 + 0.62));
        lid = g2(Math.abs(x), 0.300, 0.0130, y, 0.048, 0.0042) * Math.max(0, front);
      }
      weight[k * 2] = jaw;
      weight[k * 2 + 1] = Math.min(1, lid);
    }
  }

  // Triangles, and a sparse set of latitude lines for the mesh accent.
  const Index = count > 65535 ? Uint32Array : Uint16Array;
  const index = new Index((rows - 1) * cols * 6);
  let t = 0;
  for (let i = 0; i < rows - 1; i++) {
    for (let j = 0; j < cols; j++) {
      const a = i * cols + j;
      const b = i * cols + ((j + 1) % cols);
      const c = (i + 1) * cols + j;
      const d = (i + 1) * cols + ((j + 1) % cols);
      index[t++] = a; index[t++] = c; index[t++] = b;
      index[t++] = b; index[t++] = c; index[t++] = d;
    }
  }

  const lineRows = [];
  for (let i = 0; i < rows; i += 4) lineRows.push(i);
  const line = new Index(lineRows.length * cols * 2);
  let l = 0;
  for (const i of lineRows) {
    for (let j = 0; j < cols; j++) {
      line[l++] = i * cols + j;
      line[l++] = i * cols + ((j + 1) % cols);
    }
  }

  return { position, normal, weight, index, line, rows, cols };
}

/**
 * The whole bust.
 * @param {'high'|'low'} quality
 */
export function buildBust(quality = 'high') {
  const rows = quality === 'low' ? 72 : 112;
  const cols = quality === 'low' ? 84 : 128;
  return {
    head: sweep(HEAD_PROFILE, rows, cols, { face: true }),
    torso: sweep(TORSO_PROFILE, Math.round(rows * 0.45), Math.round(cols * 0.75)),
    hinge: JAW_HINGE,
    // Where the glowing irises sit, in model space.
    eyes: [
      { x: -0.300, y: -0.004, z: frontDepth(-0.300, -0.004) },
      { x: 0.300, y: -0.004, z: frontDepth(0.300, -0.004) },
    ],
  };
}

/** Front-surface depth at (x, y) — used to place the irises on the eyeballs. */
export function frontDepth(x, y) {
  const { w, d, zc, flat } = sample(HEAD_PROFILE, y);
  const sinPhi = Math.max(-0.999, Math.min(0.999, x / w));
  const cosPhi = Math.sqrt(1 - sinPhi * sinPhi);
  return zc + d * flatten(cosPhi, flat) + faceRelief(x, y, cosPhi);
}

/** A point on the head surface in sweep coordinates, hair included. */
export function headPoint(y, phi) {
  const { w, d, zc, flat } = sample(HEAD_PROFILE, y);
  const sinPhi = Math.sin(phi);
  const cosPhi = Math.cos(phi);
  let x = w * sinPhi;
  let z = zc + d * flatten(cosPhi, flat);
  const front = Math.max(0, cosPhi);
  z += faceRelief(x, y, front);
  x += Math.sign(sinPhi || 1) * sideRelief(y, z, Math.abs(sinPhi));
  const lift = hairLift(x, y, front, phi);
  if (lift > 0.0005) {
    const len = Math.hypot(x, z - zc) || 1;
    x += (x / len) * lift;
    z += ((z - zc) / len) * lift;
  }
  return { x, y, z };
}

/** A point on the neck/shoulder surface. */
export function torsoPoint(y, phi) {
  const { w, d, zc, flat } = sample(TORSO_PROFILE, y);
  return { x: w * Math.sin(phi), y, z: zc + d * flatten(Math.cos(phi), flat) };
}

// ------------------------------------------------------- feature curves

/**
 * The bright lines drawn over the surface: brows, eyes, nose, lips, the folds
 * of the face, the cords of the neck, the collarbones, and the strands of the
 * hair.
 *
 * A shaded surface alone reads as a mannequin. These curves are what make it
 * read as a *face* — they are the lines a person's eye uses to recognise one,
 * and in a hologram they are the part that glows.
 *
 * Returned as GL_LINES: consecutive pairs of vertices.
 */
export function buildCurves() {
  const position = [];
  const weight = [];
  const bright = [];

  /** Push one polyline, as segments. */
  const strip = (points, brightness, lid = 0) => {
    for (let i = 0; i < points.length - 1; i++) {
      for (const p of [points[i], points[i + 1]]) {
        position.push(p.x, p.y, p.z);
        const down = Math.max(0, Math.min(1, (-0.26 - p.y) / 0.52));
        const jaw = p.jawless ? 0 : down * down * (3 - 2 * down);
        weight.push(jaw, lid);
        bright.push(brightness);
      }
    }
  };

  /** A curve on the front of the face, sampled from a parametric function. */
  const face = (steps, fn, lift = 0.016) => {
    const out = [];
    for (let i = 0; i <= steps; i++) {
      const [x, y] = fn(i / steps);
      out.push({ x, y, z: frontDepth(x, y) + lift });
    }
    return out;
  };

  for (const s of [-1, 1]) {
    // Eyebrow: heavy, low over the eye, tapering towards the temple. Four
    // strands close together read as a brow; one line reads as a scratch.
    for (const off of [0, 0.017, -0.015, 0.032]) {
      strip(face(16, (t) => [
        s * (0.080 + t * 0.390),
        0.136 + off * (1 - t * 0.45) + Math.sin(t * Math.PI) * 0.026 - t * 0.050,
      ]), off === 0 ? 1.0 : 0.70);
    }

    // Eye: the almond, plus the crease above it.
    strip(face(28, (t) => {
      const a = t * TAU;
      const c = Math.cos(a);
      const taper = 1 - c * c * 0.20;
      const upper = Math.sin(a) > 0 ? 1 : 0.60;
      return [s * 0.294 + c * 0.138, -0.012 + Math.sin(a) * 0.050 * upper * taper];
    }), 1.0, 1);
    strip(face(12, (t) => [s * (0.292 + (t - 0.5) * 0.235), 0.072 - (t - 0.5) * (t - 0.5) * 0.070]), 0.46, 1);

    // Nose: the shadowed side of the bridge. Short — a long one reads as a
    // scar running down the cheek.
    strip(face(12, (t) => [s * (0.066 - t * t * 0.012), -0.070 - t * 0.270]), 0.34);
    // Nostril wing, curling back from the tip.
    strip(face(14, (t) => {
      const a = (t - 0.5) * Math.PI * 1.25;
      return [s * (0.058 + Math.cos(a) * 0.052), -0.398 - Math.sin(a) * 0.044];
    }), 0.85);

    // Nasolabial fold.
    strip(face(10, (t) => [s * (0.132 + t * 0.115), -0.420 - t * 0.230]), 0.40);

    // Cheekbone, and the plane change below it down towards the jaw.
    strip(face(10, (t) => [s * (0.455 - t * 0.095), -0.120 - t * 0.250]), 0.40);
    strip(face(10, (t) => [s * (0.400 - t * 0.060), -0.330 - t * 0.260]), 0.26);

    // The side plane of the forehead, from the brow up into the temple.
    strip(face(10, (t) => [s * (0.470 + t * 0.075), 0.195 + t * 0.235]), 0.30);

    // Orbital rim under the eye.
    strip(face(12, (t) => [
      s * (0.175 + t * 0.255),
      -0.090 - Math.sin(t * Math.PI) * 0.030,
    ]), 0.34);

    // Jawline, ear to chin.
    strip(face(18, (t) => {
      const y = -0.200 - t * 0.690;
      return [s * sample(HEAD_PROFILE, y).w * (0.90 - t * 0.14), y];
    }), 0.46);
  }

  // Nose tip and the base between the wings.
  strip(face(18, (t) => {
    const k = (t - 0.5) * 2;
    return [k * 0.062, -0.418 + Math.cos(k * Math.PI * 0.5) * 0.030];
  }), 0.9);

  // Philtrum.
  for (const s of [-1, 1]) {
    strip(face(8, (t) => [s * 0.030, -0.470 - t * 0.088]), 0.42);
  }

  // Lips: the bow of the upper lip, the line between, the lower lip.
  strip(face(24, (t) => {
    const k = (t - 0.5) * 2;
    const bow = Math.cos(k * Math.PI * 1.6) * 0.012;
    return [k * 0.175, -0.556 - bow * (1 - k * k)];
  }), 0.9);
  strip(face(24, (t) => {
    const k = (t - 0.5) * 2;
    return [k * 0.182, -0.600 + k * k * 0.014];
  }), 1.0);
  strip(face(24, (t) => {
    const k = (t - 0.5) * 2;
    return [k * 0.160, -0.648 - (1 - k * k) * 0.020];
  }), 0.82);

  // Chin crease, and the square front of the chin itself.
  strip(face(12, (t) => [(t - 0.5) * 0.210, -0.742 + (t - 0.5) * (t - 0.5) * 0.050]), 0.34);
  strip(face(16, (t) => {
    const k = (t - 0.5) * 2;
    return [k * 0.195, -0.872 - (1 - k * k) * 0.038];
  }), 0.42);

  // Forehead: the brow shelf running across, and the frontal eminences.
  strip(face(20, (t) => {
    const k = (t - 0.5) * 2;
    return [k * 0.495, 0.258 + (1 - k * k) * 0.022];
  }), 0.30);
  for (const s of [-1, 1]) {
    strip(face(8, (t) => [s * (0.185 + t * 0.045), 0.330 + t * 0.130]), 0.22);
  }

  // --- neck and shoulders ------------------------------------------------
  // The two cords running from behind the ear to the hollow of the throat.
  for (const s of [-1, 1]) {
    const cord = [];
    for (let i = 0; i <= 16; i++) {
      const t = i / 16;
      const y = -0.92 - t * 0.62;
      const phi = s * (0.95 - t * 0.62);
      const p = torsoPoint(y, phi);
      cord.push({ x: p.x, y: p.y, z: p.z + 0.012, jawless: true });
    }
    strip(cord, 0.52);

    // Trapezius, running out to the shoulder.
    const trap = [];
    for (let i = 0; i <= 14; i++) {
      const t = i / 14;
      const y = -1.34 - t * 0.56;
      const phi = s * (1.05 - t * 0.28);
      const p = torsoPoint(y, phi);
      trap.push({ x: p.x, y: p.y, z: p.z + 0.012, jawless: true });
    }
    strip(trap, 0.42);

    // Collarbone.
    const clav = [];
    for (let i = 0; i <= 14; i++) {
      const t = i / 14;
      const y = -1.84 + Math.sin(t * Math.PI) * 0.055;
      const phi = s * (t * 0.95);
      const p = torsoPoint(y, phi);
      clav.push({ x: p.x, y: p.y, z: p.z + 0.014, jawless: true });
    }
    strip(clav, 0.50);
  }

  // The hollow of the throat.
  strip([
    { ...torsoPoint(-1.80, -0.16), jawless: true },
    { ...torsoPoint(-1.86, 0), jawless: true },
    { ...torsoPoint(-1.80, 0.16), jawless: true },
  ].map((p) => ({ ...p, z: p.z + 0.016 })), 0.5);

  // --- hair --------------------------------------------------------------
  // Short hair, drawn as strands sweeping back from a parting rather than as
  // a moulded shell: strands are what the eye reads as hair.
  const STRANDS = 96;
  for (let s = 0; s < STRANDS; s++) {
    // Spread the roots across the scalp, denser towards the front.
    const u = s / (STRANDS - 1);
    const phi0 = (u - 0.5) * Math.PI * 2.05;
    const parting = Math.cos(phi0) * 0.10;                 // swept to one side
    const points = [];
    const steps = 13;
    // A strand stops at the hairline in front and runs on down behind the ear,
    // which is what makes it read as short hair instead of a fringe.
    const front01 = (Math.cos(phi0) + 1) / 2;
    const drop = 0.630 + 0.670 * (1 - front01);
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      const y = 1.045 - t * drop;
      const phi = phi0 + parting * t + Math.sin(u * 37.0) * 0.055 * t;
      const p = headPoint(y, phi);
      // Lift each strand clear of the scalp, most at the crown.
      const lift = 0.012 + 0.020 * Math.sin(t * Math.PI);
      const len = Math.hypot(p.x, p.z) || 1;
      points.push({
        x: p.x + (p.x / len) * lift,
        y: p.y,
        z: p.z + (p.z / len) * lift,
        jawless: true,
      });
    }
    strip(points, 0.55 + 0.45 * Math.abs(Math.sin(u * 23.0)));
  }

  return {
    position: new Float32Array(position),
    weight: new Float32Array(weight),
    bright: new Float32Array(bright),
    count: position.length / 3,
  };
}

export { JAW_HINGE };

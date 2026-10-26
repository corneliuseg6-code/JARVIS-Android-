/**
 * A very small GLB reader.
 *
 * JARVIS needs exactly one thing out of a .glb: the positions, normals and
 * indices of its mesh. That is a container header, two chunks and a handful
 * of accessors — about a hundred lines — so it is written here rather than
 * pulling in a 600KB 3D library that would also have to be vendored, cached
 * by the service worker and kept up to date.
 *
 * What it deliberately does NOT do: materials, textures, animation, skins,
 * cameras, sparse accessors, or the separate-.bin flavour of glTF. If a file
 * needs any of that, this returns null and the caller falls back.
 *
 * Container format (glTF 2.0 binary):
 *   12-byte header   magic 'glTF', version, total length
 *   chunk 0          JSON   — the glTF document
 *   chunk 1          BIN    — the buffer the accessors point into
 */

const MAGIC = 0x46546c67;            // 'glTF' little-endian
const COMPONENT = {
  5120: { array: Int8Array, size: 1 },
  5121: { array: Uint8Array, size: 1 },
  5122: { array: Int16Array, size: 2 },
  5123: { array: Uint16Array, size: 2 },
  5125: { array: Uint32Array, size: 4 },
  5126: { array: Float32Array, size: 4 },
};
const ELEMENTS = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 };

/** Read one accessor into a typed array, honouring interleaving. */
function readAccessor(json, bin, index) {
  const accessor = json.accessors[index];
  if (!accessor || accessor.sparse) return null;
  const comp = COMPONENT[accessor.componentType];
  const n = ELEMENTS[accessor.type];
  if (!comp || !n) return null;

  const out = new comp.array(accessor.count * n);
  if (accessor.bufferView === undefined) return out;      // all zeroes, per spec

  const view = json.bufferViews[accessor.bufferView];
  const base = (view.byteOffset || 0) + (accessor.byteOffset || 0);
  const stride = view.byteStride || comp.size * n;
  const packed = stride === comp.size * n;

  if (packed && base % comp.size === 0) {
    // The common case: one contiguous, aligned block.
    return new comp.array(bin.buffer, bin.byteOffset + base, accessor.count * n);
  }
  // Interleaved, or misaligned for this component type: copy element by element.
  const dv = new DataView(bin.buffer, bin.byteOffset);
  const little = true;
  for (let i = 0; i < accessor.count; i++) {
    for (let k = 0; k < n; k++) {
      const at = base + i * stride + k * comp.size;
      switch (accessor.componentType) {
        case 5120: out[i * n + k] = dv.getInt8(at); break;
        case 5121: out[i * n + k] = dv.getUint8(at); break;
        case 5122: out[i * n + k] = dv.getInt16(at, little); break;
        case 5123: out[i * n + k] = dv.getUint16(at, little); break;
        case 5125: out[i * n + k] = dv.getUint32(at, little); break;
        default:   out[i * n + k] = dv.getFloat32(at, little); break;
      }
    }
  }
  return out;
}

/** Normals computed from the triangles, for a file that ships without them. */
function deriveNormals(position, index) {
  const normal = new Float32Array(position.length);
  for (let i = 0; i < index.length; i += 3) {
    const a = index[i] * 3;
    const b = index[i + 1] * 3;
    const c = index[i + 2] * 3;
    const ux = position[b] - position[a];
    const uy = position[b + 1] - position[a + 1];
    const uz = position[b + 2] - position[a + 2];
    const vx = position[c] - position[a];
    const vy = position[c + 1] - position[a + 1];
    const vz = position[c + 2] - position[a + 2];
    const nx = uy * vz - uz * vy;
    const ny = uz * vx - ux * vz;
    const nz = ux * vy - uy * vx;
    for (const o of [a, b, c]) {
      normal[o] += nx;
      normal[o + 1] += ny;
      normal[o + 2] += nz;
    }
  }
  for (let i = 0; i < normal.length; i += 3) {
    const len = Math.hypot(normal[i], normal[i + 1], normal[i + 2]) || 1;
    normal[i] /= len;
    normal[i + 1] /= len;
    normal[i + 2] /= len;
  }
  return normal;
}

/**
 * Parse a .glb and return its first triangle mesh.
 *
 * @param {ArrayBuffer} buffer
 * @returns {{position:Float32Array, normal:Float32Array,
 *            index:Uint16Array|Uint32Array}|null}
 */
export function parseGLB(buffer) {
  const dv = new DataView(buffer);
  if (buffer.byteLength < 20 || dv.getUint32(0, true) !== MAGIC) return null;
  if (dv.getUint32(4, true) !== 2) return null;                 // glTF 2.0 only
  const total = Math.min(dv.getUint32(8, true), buffer.byteLength);

  let json = null;
  let bin = null;
  let at = 12;
  while (at + 8 <= total) {
    const length = dv.getUint32(at, true);
    const type = dv.getUint32(at + 4, true);
    const body = at + 8;
    if (body + length > buffer.byteLength) break;
    if (type === 0x4e4f534a) {                                   // 'JSON'
      json = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, body, length)));
    } else if (type === 0x004e4942) {                            // 'BIN\0'
      bin = new Uint8Array(buffer, body, length);
    }
    at = body + length + ((4 - (length % 4)) % 4);
  }
  if (!json || !bin || !json.meshes || !json.meshes.length) return null;

  // The first primitive of the first mesh that is made of triangles.
  let primitive = null;
  for (const mesh of json.meshes) {
    for (const p of mesh.primitives || []) {
      if ((p.mode === undefined || p.mode === 4) && p.attributes?.POSITION !== undefined) {
        primitive = p;
        break;
      }
    }
    if (primitive) break;
  }
  if (!primitive) return null;

  const position = readAccessor(json, bin, primitive.attributes.POSITION);
  if (!position) return null;

  let index;
  if (primitive.indices !== undefined) {
    const raw = readAccessor(json, bin, primitive.indices);
    if (!raw) return null;
    index = raw instanceof Uint16Array || raw instanceof Uint32Array
      ? raw
      : Uint32Array.from(raw);
  } else {
    const count = position.length / 3;
    index = count > 65535 ? new Uint32Array(count) : new Uint16Array(count);
    for (let i = 0; i < count; i++) index[i] = i;
  }

  const normal = primitive.attributes.NORMAL !== undefined
    ? readAccessor(json, bin, primitive.attributes.NORMAL)
    : null;

  return {
    position: position instanceof Float32Array ? position : Float32Array.from(position),
    normal: normal instanceof Float32Array ? normal : deriveNormals(position, index),
    index,
  };
}

/**
 * Fit a mesh into JARVIS's own frame and rig it for the hologram.
 *
 * The visualiser works in a space where the crown of the head is y = +1 and
 * the chin y = -1, with the face looking down +z. A scan arrives in whatever
 * units and position its author used, so it is measured and mapped: the top
 * of the bounding box is the crown, and the chin is found as the point where
 * the silhouette stops narrowing and starts widening again into the neck.
 *
 * Two weights are then derived per vertex, which is rigging, not sculpting —
 * the geometry itself is untouched:
 *   jaw  everything from under the ear to the chin, and nothing below it, so
 *        the mouth opens without dragging the throat with it
 *   lid  a patch over each eye
 *
 * @returns {{position:Float32Array, normal:Float32Array, weight:Float32Array,
 *            index:Uint16Array|Uint32Array, eyes:Array, scale:number}}
 */
export function fitBust(mesh, { eyeSpacing = 0.30 } = {}) {
  const { position, normal, index } = mesh;
  const count = position.length / 3;

  let minY = Infinity;
  let maxY = -Infinity;
  let minX = Infinity;
  let maxX = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;
  for (let i = 0; i < count; i++) {
    const x = position[i * 3];
    const y = position[i * 3 + 1];
    const z = position[i * 3 + 2];
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (z < minZ) minZ = z;
    if (z > maxZ) maxZ = z;
  }

  // Width profile, to find where the jaw ends and the neck begins.
  const SLICES = 48;
  const width = new Float32Array(SLICES);
  const span = maxY - minY;
  for (let i = 0; i < count; i++) {
    const s = Math.min(SLICES - 1, Math.floor(((position[i * 3 + 1] - minY) / span) * SLICES));
    width[s] = Math.max(width[s], Math.abs(position[i * 3]));
  }

  // Find the skull first — the widest slice in the upper half. Searching for
  // "the narrowest slice" from the top instead finds the crown, which is also
  // narrow, and collapses the whole fit.
  let widest = Math.floor(SLICES * 0.5);
  for (let s = Math.floor(SLICES * 0.45); s < SLICES; s++) {
    if (width[s] > width[widest]) widest = s;
  }
  // From the skull downwards, the profile narrows through the jaw and then
  // runs flat along the neck before flaring into the shoulders.
  let neck = widest;
  for (let s = widest - 1; s >= 1; s--) {
    if (width[s] <= width[neck]) neck = s;
    else if (width[s] > width[neck] * 1.25) break;     // shoulders
  }
  // The chin is the top of that flat run, not its bottom.
  let chin = neck;
  for (let s = neck + 1; s < widest; s++) {
    if (width[s] <= width[neck] * 1.12) chin = s;
    else break;
  }

  const chinY = minY + ((chin + 0.5) / SLICES) * span;
  const crownY = maxY;
  const headHeight = Math.max(1e-6, crownY - chinY);

  const scale = 2 / headHeight;
  const centreY = (crownY + chinY) / 2;
  const centreX = (minX + maxX) / 2;
  const centreZ = (minZ + maxZ) / 2;

  const out = new Float32Array(position.length);
  for (let i = 0; i < count; i++) {
    out[i * 3] = (position[i * 3] - centreX) * scale;
    out[i * 3 + 1] = (position[i * 3 + 1] - centreY) * scale;
    out[i * 3 + 2] = (position[i * 3 + 2] - centreZ) * scale;
  }

  // The nose is the front-most point of the face, and everything else on the
  // face can be placed relative to it — which beats assuming the scan uses
  // the same proportions as the procedural head it replaces.
  let noseY = 0;
  let bestZ = -Infinity;
  for (let i = 0; i < count; i++) {
    const z = out[i * 3 + 2];
    if (z > bestZ && Math.abs(out[i * 3]) < 0.12) {
      bestZ = z;
      noseY = out[i * 3 + 1];
    }
  }
  const eyeY = noseY + 0.32;
  const jawTop = noseY + 0.10;

  // Weights.
  const weight = new Float32Array(count * 2);
  const smooth = (e0, e1, x) => {
    const t = Math.max(0, Math.min(1, (x - e0) / (e1 - e0)));
    return t * t * (3 - 2 * t);
  };
  for (let i = 0; i < count; i++) {
    const x = out[i * 3];
    const y = out[i * 3 + 1];
    const z = out[i * 3 + 2];
    // Rises under the ear, full at the chin, and back to nothing below it so
    // the neck and the shoulders never move with the mouth.
    const open = smooth(jawTop, -0.92, y);
    const close = 1 - smooth(-1.00, -1.28, y);
    const front = Math.max(0, Math.min(1, z * 1.1 + 0.55));
    weight[i * 2] = open * close * front;
    // A soft patch over each eye.
    const dx = Math.abs(x) - eyeSpacing;
    const dy = y - eyeY;
    weight[i * 2 + 1] = Math.exp(-((dx * dx) / 0.013 + (dy * dy) / 0.0045)) * front;
  }

  // Where the irises go: the front-most surface point near each eye centre.
  const eyes = [];
  for (const side of [-1, 1]) {
    let best = null;
    for (let i = 0; i < count; i++) {
      const x = out[i * 3];
      const y = out[i * 3 + 1];
      if (Math.abs(x - side * eyeSpacing) > 0.075 || Math.abs(y - eyeY) > 0.055) continue;
      const z = out[i * 3 + 2];
      if (!best || z > best.z) best = { x: side * eyeSpacing, y: eyeY, z };
    }
    eyes.push(best || { x: side * eyeSpacing, y: eyeY, z: 0.9 });
  }

  return { position: out, normal, weight, index, eyes, scale, eyeY, noseY };
}

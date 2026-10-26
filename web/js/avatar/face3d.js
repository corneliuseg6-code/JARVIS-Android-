/**
 * FACE — a volumetric holographic bust, rendered with WebGL.
 *
 * The old face was a Canvas 2D wireframe, which is why it read as a flat mask
 * however much the geometry was improved: a wireframe has no surface, so it
 * has no shading, and shading is what makes a head look like a head.
 *
 * This renders a real shaded surface — head, neck and shoulders, built by
 * head-mesh.js — with a hologram material:
 *
 *   fresnel rim      the surface glows where it curves away from you, which is
 *                    what gives the volume away
 *   additive, no     front and back of the head both draw and add together, so
 *   depth buffer     the bust is translucent rather than solid
 *   scan lines       fine horizontal lines, drifting
 *   scan band        one bright sweep travelling up the body
 *   interference     a slow flicker and a rare horizontal tear
 *   depth tint       far surfaces fall towards deep blue, near ones to cyan
 *
 * Plus a sparse mesh overlay, glowing irises, drifting particles and a
 * volumetric wash behind the bust.
 *
 * The jaw is a real rotation about a hinge behind the ear, driven by a
 * per-vertex weight, and the lids close over the eyeballs the same way — both
 * happen in the vertex shader, so animating them costs nothing per frame.
 *
 * Everything is WebGL 1 with no extensions, which is what gets it running on
 * every Android phone that can run Chrome. Three draw calls for the bust, one
 * for the particles, one for the wash.
 */
import { buildBust, buildCurves } from './head-mesh.js';

// ---------------------------------------------------------------- matrices

function perspective(out, fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2);
  out.fill(0);
  out[0] = f / aspect;
  out[5] = f;
  out[10] = (far + near) / (near - far);
  out[11] = -1;
  out[14] = (2 * far * near) / (near - far);
  return out;
}

/** Yaw about Y, then pitch about X, then a translation. Rotation only, so the
 *  same 3×3 works for normals without inverting anything. */
function modelView(out, yaw, pitch, tx, ty, tz) {
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  out[0] = cy;        out[1] = sp * sy;   out[2] = -cp * sy;  out[3] = 0;
  out[4] = 0;         out[5] = cp;        out[6] = sp;        out[7] = 0;
  out[8] = sy;        out[9] = -sp * cy;  out[10] = cp * cy;  out[11] = 0;
  out[12] = tx;       out[13] = ty;       out[14] = tz;       out[15] = 1;
  return out;
}

function mat3FromMat4(out, m) {
  out[0] = m[0]; out[1] = m[1]; out[2] = m[2];
  out[3] = m[4]; out[4] = m[5]; out[5] = m[6];
  out[6] = m[8]; out[7] = m[9]; out[8] = m[10];
  return out;
}

// ---------------------------------------------------------------- shaders

const BUST_VS = `
precision highp float;
attribute vec3 aPos;
attribute vec3 aNor;
attribute vec2 aWeight;          // x: jaw, y: eyelid

uniform mat4 uProj;
uniform mat4 uMV;
uniform mat3 uNor;
uniform vec2 uHinge;             // jaw hinge (y, z)
uniform float uJaw;              // radians
uniform float uBlink;            // 0..1

varying vec3 vNormal;
varying vec3 vEye;
varying vec3 vModel;
varying float vLid;

void main() {
  vec3 p = aPos;
  vec3 n = aNor;

  // The jaw swings about a hinge behind the ear.
  float a = aWeight.x * uJaw;
  if (a > 0.0001) {
    float s = sin(a);
    float c = cos(a);
    vec2 q = vec2(p.y - uHinge.x, p.z - uHinge.y);
    p.y = uHinge.x + q.x * c - q.y * s;
    p.z = uHinge.y + q.x * s + q.y * c;
    vec2 qn = vec2(n.y, n.z);
    n.y = qn.x * c - qn.y * s;
    n.z = qn.x * s + qn.y * c;
  }

  // The lids drop over the eyeballs.
  p.y -= aWeight.y * uBlink * 0.062;
  p.z += aWeight.y * uBlink * 0.010;

  vModel = p;
  vLid = aWeight.y;
  vNormal = normalize(uNor * n);
  vec4 eye = uMV * vec4(p, 1.0);
  vEye = eye.xyz;
  gl_Position = uProj * eye;
}`;

const BUST_FS = `
precision highp float;

varying vec3 vNormal;
varying vec3 vEye;
varying vec3 vModel;
varying float vLid;

uniform float uTime;
uniform float uScanY;            // travelling band, in model space
uniform float uEnergy;           // 0..1, from activity and voice level
uniform float uTear;             // horizontal interference, model y
uniform vec3 uDeep;
uniform vec3 uBright;
uniform float uLineMode;         // 1.0 when drawing the mesh overlay
uniform float uTopFade;          // 1.0 for the torso: fade out under the jaw

void main() {
  vec3 N = normalize(vNormal);
  vec3 V = normalize(-vEye);
  float facing = abs(dot(N, V));

  // Fresnel: bright exactly where the surface turns away. It is what makes a
  // translucent shell read as a solid volume — but on its own it hides the
  // middle of the face, because a nose pointing at you is not grazing.
  float fres = pow(1.0 - facing, 3.4);

  // So the relief gets a real light as well: a key from the upper left and a
  // cool fill from the lower right. This is what makes the brow, the nose,
  // the lips and the cheekbones readable from straight on.
  vec3 key = normalize(vec3(-0.52, 0.62, 0.78));
  vec3 fill = normalize(vec3(0.70, -0.35, 0.55));
  float lam = pow(max(0.0, dot(N, key)), 1.25);
  float rim2 = pow(max(0.0, dot(N, fill)), 2.0);
  float shade = lam * 0.92 + rim2 * 0.40;

  // Scan lines, drifting slowly down the body. Kept faint and coarse: at a
  // fine pitch they alias into banding that buries the feature curves, which
  // are the part that actually reads as a face.
  float lines = 0.5 + 0.5 * sin(vModel.y * 70.0 - uTime * 1.6);

  // One band sweeping up the bust. Narrow: a wide one washes the face out.
  float band = exp(-pow((vModel.y - uScanY) * 19.0, 2.0));

  // Interference: a slow global flicker, plus a rare horizontal tear.
  float flicker = 0.94 + 0.06 * sin(uTime * 7.3) * sin(uTime * 2.1);
  float tear = exp(-pow((vModel.y - uTear) * 90.0, 2.0));

  // Depth tint: what is further away falls towards deep blue.
  float depth = clamp((vEye.z + 4.6) / 2.2, 0.0, 1.0);

  vec3 colour = mix(uDeep, uBright,
    clamp(fres * 1.15 + shade * 0.55 + band * 0.45 + depth * 0.18, 0.0, 1.0));
  colour += vec3(0.16, 0.52, 0.78) * band * 1.4;
  colour += vec3(0.22, 0.55, 0.85) * tear;

  float alpha =
      0.105                       // the body of the hologram, kept dark
    + fres * 1.30                 // the rim — this is what shows the volume
    + shade * 0.88                // the modelled light on the face itself
    + band * 0.30                 // the sweep
    + lines * 0.028               // the scan lines
    + tear * 0.30;

  alpha *= mix(0.42, 1.0, depth);
  alpha *= flicker;
  alpha *= 0.86 + uEnergy * 0.48;

  // The mesh overlay is the same material, thinner and cooler.
  alpha *= mix(1.0, 0.40, uLineMode);

  // Closed lids read as a soft shadow rather than a bright seam.
  alpha *= 1.0 - vLid * 0.35;

  // The top of the neck sits inside the head, and the hologram is additive,
  // so without this its rim would glow straight through the jaw.
  alpha *= mix(1.0, smoothstep(-0.78, -1.22, vModel.y), uTopFade);

  gl_FragColor = vec4(colour * (1.05 + fres * 1.7 + shade * 0.7 + band * 1.3), alpha);
}`;

const POINT_VS = `
precision highp float;
attribute vec3 aPos;
attribute vec2 aSeed;            // x: phase, y: size

uniform mat4 uProj;
uniform mat4 uMV;
uniform float uTime;
uniform float uScale;
uniform float uDrift;            // 0 for the irises, 1 for the particles

varying float vFade;

void main() {
  vec3 p = aPos;
  if (uDrift > 0.5) {
    float t = fract(aSeed.x + uTime * 0.045);
    p.y = mix(-2.2, 1.5, t);
    p.x += sin(uTime * 0.5 + aSeed.x * 31.0) * 0.08;
    p.z += cos(uTime * 0.4 + aSeed.x * 17.0) * 0.08;
    vFade = sin(t * 3.14159) * 0.9;
  } else {
    vFade = 1.0;
  }
  vec4 eye = uMV * vec4(p, 1.0);
  gl_Position = uProj * eye;
  gl_PointSize = clamp(aSeed.y * uScale / max(0.4, -eye.z), 1.0, 96.0);
}`;

const POINT_FS = `
precision highp float;
varying float vFade;
uniform vec3 uColour;
uniform float uSoft;             // 1 = soft blob (iris), 0 = tight dot
void main() {
  vec2 d = gl_PointCoord - vec2(0.5);
  float r = length(d) * 2.0;
  if (r > 1.0) discard;
  float a = pow(1.0 - r, mix(1.6, 2.6, uSoft));
  gl_FragColor = vec4(uColour * (1.0 + uSoft * 1.4), a * vFade);
}`;

/**
 * The feature curves: brows, eyes, nose, lips, the cords of the neck and the
 * strands of the hair. They follow the same jaw and lid animation as the
 * surface, but they are lit flat and bright — these are the lines the eye
 * reads a face by, so they must not fade out when the surface faces you.
 */
const CURVE_VS = `
precision highp float;
attribute vec3 aPos;
attribute vec2 aWeight;
attribute float aBright;

uniform mat4 uProj;
uniform mat4 uMV;
uniform vec2 uHinge;
uniform float uJaw;
uniform float uBlink;
uniform vec2 uOffset;            // clip-space nudge: see the thickness pass

varying float vBright;
varying float vDepth;
varying float vY;

void main() {
  vec3 p = aPos;
  float a = aWeight.x * uJaw;
  if (a > 0.0001) {
    float s = sin(a);
    float c = cos(a);
    vec2 q = vec2(p.y - uHinge.x, p.z - uHinge.y);
    p.y = uHinge.x + q.x * c - q.y * s;
    p.z = uHinge.y + q.x * s + q.y * c;
  }
  p.y -= aWeight.y * uBlink * 0.062;
  p.z += aWeight.y * uBlink * 0.010;

  vBright = aBright;
  vY = p.y;
  vec4 eye = uMV * vec4(p, 1.0);
  vDepth = clamp((eye.z + 4.6) / 2.2, 0.0, 1.0);
  vec4 clip = uProj * eye;
  clip.xy += uOffset * clip.w;
  gl_Position = clip;
}`;

const CURVE_FS = `
precision highp float;
varying float vBright;
varying float vDepth;
varying float vY;

uniform float uTime;
uniform float uScanY;
uniform float uEnergy;
uniform vec3 uBright;
uniform float uGain;             // lower on the outer thickness passes

void main() {
  float band = exp(-pow((vY - uScanY) * 19.0, 2.0));
  float flicker = 0.93 + 0.07 * sin(uTime * 6.1 + vY * 4.0);
  float a = vBright * (1.35 + uEnergy * 0.65) * mix(0.22, 1.0, vDepth) * flicker;
  a += band * 0.60 * vBright;
  gl_FragColor = vec4(uBright * (1.45 + band * 1.4), a * uGain);
}`;

const WASH_VS = `
precision highp float;
attribute vec2 aPos;
varying vec2 vUv;
void main() { vUv = aPos; gl_Position = vec4(aPos, 0.0, 1.0); }`;

const WASH_FS = `
precision highp float;
varying vec2 vUv;
uniform float uEnergy;
uniform float uAspect;
void main() {
  vec2 p = vec2(vUv.x * uAspect, vUv.y + 0.08);
  float r = length(p);
  float a = exp(-r * r * 2.1) * (0.16 + uEnergy * 0.14);
  gl_FragColor = vec4(vec3(0.10, 0.52, 0.72) * a, a);
}`;

// ---------------------------------------------------------------- renderer

function compile(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader);
    gl.deleteShader(shader);
    throw new Error('shader: ' + log);
  }
  return shader;
}

function program(gl, vs, fs) {
  const p = gl.createProgram();
  gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, vs));
  gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
    throw new Error('link: ' + gl.getProgramInfoLog(p));
  }
  const uniforms = {};
  const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < n; i++) {
    const name = gl.getActiveUniform(p, i).name.replace(/\[0\]$/, '');
    uniforms[name] = gl.getUniformLocation(p, name);
  }
  const attribs = {};
  const m = gl.getProgramParameter(p, gl.ACTIVE_ATTRIBUTES);
  for (let i = 0; i < m; i++) {
    const name = gl.getActiveAttrib(p, i).name;
    attribs[name] = gl.getAttribLocation(p, name);
  }
  return { handle: p, u: uniforms, a: attribs };
}

export default class Face3D {
  constructor() {
    this.name = 'face';
    this.type = 'gl';
    this.gl = null;
    this.ready = false;
    this.failed = false;

    this.phase = 0;
    this.blink = 0;
    this.nextBlink = 1.5 + Math.random() * 3;
    this.jaw = 0;
    this.scan = -2.2;
    this.tear = -9;
    this.nextTear = 4 + Math.random() * 8;

    this._proj = new Float32Array(16);
    this._mv = new Float32Array(16);
    this._nor = new Float32Array(9);
    // 2 = everything, 1 = fewer glow passes, 0 = the cheapest that still
    // looks like the same bust. The avatar drops this if frames get slow.
    this.quality = 2;
  }

  setQuality(level) {
    this.quality = Math.max(0, Math.min(2, level | 0));
  }

  /** @param {HTMLCanvasElement} canvas */
  init(canvas, { quality = 'high' } = {}) {
    if (this.gl || this.failed) return !this.failed;
    const opts = {
      alpha: true, antialias: true, depth: false, stencil: false,
      premultipliedAlpha: false, powerPreference: 'low-power',
      preserveDrawingBuffer: false, failIfMajorPerformanceCaveat: false,
    };
    const gl = canvas.getContext('webgl', opts) || canvas.getContext('experimental-webgl', opts);
    if (!gl) {
      this.failed = true;
      return false;
    }
    try {
      this._build(gl, quality);
    } catch (err) {
      console.error('[face3d] setup failed', err);
      this.failed = true;
      return false;
    }
    this.gl = gl;
    this.ready = true;
    canvas.addEventListener('webglcontextlost', (event) => {
      event.preventDefault();
      this.ready = false;
    });
    canvas.addEventListener('webglcontextrestored', () => {
      this.gl = null;
      this.ready = false;
      this.failed = false;
      this.init(canvas, { quality });
    });
    return true;
  }

  _build(gl, quality) {
    const bust = buildBust(quality);
    this.hinge = bust.hinge;

    this.bustProgram = program(gl, BUST_VS, BUST_FS);
    this.curveProgram = program(gl, CURVE_VS, CURVE_FS);
    this.pointProgram = program(gl, POINT_VS, POINT_FS);
    this.washProgram = program(gl, WASH_VS, WASH_FS);

    const buffer = (data, target = gl.ARRAY_BUFFER) => {
      const b = gl.createBuffer();
      gl.bindBuffer(target, b);
      gl.bufferData(target, data, gl.STATIC_DRAW);
      return b;
    };

    this.parts = [];
    for (const mesh of [bust.torso, bust.head]) {
      this.parts.push({
        pos: buffer(mesh.position),
        nor: buffer(mesh.normal),
        wgt: buffer(mesh.weight),
        idx: buffer(mesh.index, gl.ELEMENT_ARRAY_BUFFER),
        lin: buffer(mesh.line, gl.ELEMENT_ARRAY_BUFFER),
        count: mesh.index.length,
        lineCount: mesh.line.length,
      });
    }

    const curves = buildCurves();
    this.curves = {
      pos: buffer(curves.position),
      wgt: buffer(curves.weight),
      brt: buffer(curves.bright),
      count: curves.count,
    };

    // Irises.
    const eyes = new Float32Array(6);
    const eyeSeed = new Float32Array(4);
    bust.eyes.forEach((e, i) => {
      eyes[i * 3] = e.x;
      eyes[i * 3 + 1] = e.y;
      eyes[i * 3 + 2] = e.z + 0.02;
      eyeSeed[i * 2] = 0;
      eyeSeed[i * 2 + 1] = 0.16;
    });
    this.eyeBuffer = buffer(eyes);
    this.eyeSeedBuffer = buffer(eyeSeed);

    // Particles drifting through the volume of the bust.
    const n = quality === 'low' ? 160 : 420;
    const pos = new Float32Array(n * 3);
    const seed = new Float32Array(n * 2);
    for (let i = 0; i < n; i++) {
      const a = Math.random() * Math.PI * 2;
      const r = 0.35 + Math.pow(Math.random(), 0.6) * 1.5;
      pos[i * 3] = Math.cos(a) * r;
      pos[i * 3 + 1] = 0;
      pos[i * 3 + 2] = Math.sin(a) * r * 0.7 - 0.1;
      seed[i * 2] = Math.random();
      seed[i * 2 + 1] = 0.012 + Math.random() * 0.028;
    }
    this.particleBuffer = buffer(pos);
    this.particleSeedBuffer = buffer(seed);
    this.particleCount = n;

    this.washBuffer = buffer(new Float32Array([-1, -1, 3, -1, -1, 3]));

    gl.disable(gl.DEPTH_TEST);
    gl.depthMask(false);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE);   // additive: the hologram adds light
  }

  setLabel() { /* the bust carries no label */ }

  /**
   * @param {HTMLCanvasElement} canvas
   * @param {{w:number,h:number,time:number,dt:number,activity:string,
   *          level:number,motion:number,tiltX:number,tiltY:number}} env
   */
  render(canvas, env) {
    const gl = this.gl;
    if (!gl || !this.ready) return;
    const { w, h, dt, activity, level, motion } = env;

    this.phase += dt * motion;

    // --- idle motion ------------------------------------------------------
    const yaw = Math.sin(this.phase * 0.27) * 0.24 * motion + (env.tiltX || 0) * 0.75;
    const pitch = Math.sin(this.phase * 0.19 + 1.1) * 0.07 * motion + (env.tiltY || 0) * 0.5;

    // --- blinking ---------------------------------------------------------
    this.nextBlink -= dt * motion;
    if (this.nextBlink <= 0) {
      this.blink = 0.15;
      this.nextBlink = 2.2 + Math.random() * 4.5;
    }
    if (this.blink > 0) this.blink = Math.max(0, this.blink - dt);
    // Up then down over the blink, so the lid closes and opens.
    const blink = this.blink > 0 ? Math.sin((1 - this.blink / 0.15) * Math.PI) : 0;

    // --- speech -----------------------------------------------------------
    const target = activity === 'speaking'
      ? 0.035 + Math.abs(Math.sin(env.time * 8.0 * motion)) * (0.10 + level * 0.16)
      : 0.008;
    this.jaw += (target - this.jaw) * Math.min(1, dt * 13);

    // --- scanning and interference ---------------------------------------
    this.scan += dt * 0.55 * motion;
    if (this.scan > 1.5) this.scan = -2.3;
    this.nextTear -= dt;
    if (this.nextTear <= 0) {
      this.tear = -2.2 + Math.random() * 3.4;
      this.nextTear = 3 + Math.random() * 9;
      this._tearLife = 0.16;
    }
    if (this._tearLife > 0) {
      this._tearLife -= dt;
      if (this._tearLife <= 0) this.tear = -9;
    }

    const energy = activity === 'listening' ? 0.45 + level * 0.55
      : activity === 'speaking' ? 0.55 + level * 0.45
      : activity === 'thinking' ? 0.5
      : 0.26;

    // --- camera -----------------------------------------------------------
    const aspect = w / Math.max(1, h);
    // Frame the bust: the head fills a portrait screen, the shoulders are
    // allowed to run off the bottom edge the way a bust shot does.
    const distance = aspect < 1
      ? 4.15 / Math.min(1.25, Math.max(0.55, aspect * 1.45))
      : 5.2;
    perspective(this._proj, 0.72, aspect, 0.1, 20);
    // Lift the model so the frame is a bust: the crown clear of the top edge,
    // the shoulders running off the bottom.
    modelView(this._mv, yaw, pitch, 0, 0.22, -distance);
    mat3FromMat4(this._nor, this._mv);

    this._viewport = [canvas.width, canvas.height];
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);

    this._wash(gl, energy, aspect);
    this._bust(gl, energy, blink);
    this._featureCurves(gl, energy, blink);
    this._points(gl, energy, blink, canvas.height);
  }

  _wash(gl, energy, aspect) {
    const p = this.washProgram;
    gl.useProgram(p.handle);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.washBuffer);
    gl.enableVertexAttribArray(p.a.aPos);
    gl.vertexAttribPointer(p.a.aPos, 2, gl.FLOAT, false, 0, 0);
    gl.uniform1f(p.u.uEnergy, energy);
    gl.uniform1f(p.u.uAspect, aspect);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.disableVertexAttribArray(p.a.aPos);
  }

  _bust(gl, energy, blink) {
    const p = this.bustProgram;
    gl.useProgram(p.handle);
    gl.uniformMatrix4fv(p.u.uProj, false, this._proj);
    gl.uniformMatrix4fv(p.u.uMV, false, this._mv);
    gl.uniformMatrix3fv(p.u.uNor, false, this._nor);
    gl.uniform2f(p.u.uHinge, this.hinge.y, this.hinge.z);
    gl.uniform1f(p.u.uJaw, this.jaw);
    gl.uniform1f(p.u.uBlink, blink);
    gl.uniform1f(p.u.uTime, this.phase);
    gl.uniform1f(p.u.uScanY, this.scan);
    gl.uniform1f(p.u.uTear, this.tear);
    gl.uniform1f(p.u.uEnergy, energy);
    gl.uniform3f(p.u.uDeep, 0.030, 0.230, 0.640);
    gl.uniform3f(p.u.uBright, 0.360, 0.820, 1.000);

    this.parts.forEach((part, i) => {
      // parts[0] is the torso, whose top ring hides inside the head.
      gl.uniform1f(p.u.uTopFade, i === 0 ? 1 : 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, part.pos);
      gl.enableVertexAttribArray(p.a.aPos);
      gl.vertexAttribPointer(p.a.aPos, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, part.nor);
      gl.enableVertexAttribArray(p.a.aNor);
      gl.vertexAttribPointer(p.a.aNor, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, part.wgt);
      gl.enableVertexAttribArray(p.a.aWeight);
      gl.vertexAttribPointer(p.a.aWeight, 2, gl.FLOAT, false, 0, 0);

      gl.uniform1f(p.u.uLineMode, 0);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, part.idx);
      gl.drawElements(gl.TRIANGLES, part.count, gl.UNSIGNED_SHORT, 0);

      // The mesh accent: a few latitude rings, so it still reads as a
      // projection rather than as a solid object.
      // No latitude-grid overlay. A regular grid laid over a face is exactly
      // what reads as "wireframe mask" — the surface shading and the
      // anatomical curves carry the form instead.
    });
  }

  _featureCurves(gl, energy, blink) {
    const p = this.curveProgram;
    const c = this.curves;
    gl.useProgram(p.handle);
    gl.uniformMatrix4fv(p.u.uProj, false, this._proj);
    gl.uniformMatrix4fv(p.u.uMV, false, this._mv);
    gl.uniform2f(p.u.uHinge, this.hinge.y, this.hinge.z);
    gl.uniform1f(p.u.uJaw, this.jaw);
    gl.uniform1f(p.u.uBlink, blink);
    gl.uniform1f(p.u.uTime, this.phase);
    gl.uniform1f(p.u.uScanY, this.scan);
    gl.uniform1f(p.u.uEnergy, energy);
    gl.uniform3f(p.u.uBright, 0.240, 0.660, 1.000);

    gl.bindBuffer(gl.ARRAY_BUFFER, c.pos);
    gl.enableVertexAttribArray(p.a.aPos);
    gl.vertexAttribPointer(p.a.aPos, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, c.wgt);
    gl.enableVertexAttribArray(p.a.aWeight);
    gl.vertexAttribPointer(p.a.aWeight, 2, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, c.brt);
    gl.enableVertexAttribArray(p.a.aBright);
    gl.vertexAttribPointer(p.a.aBright, 1, gl.FLOAT, false, 0, 0);

    // WebGL will not give us a line wider than one pixel on most drivers, and
    // a one-pixel line disappears on a 3× phone screen. Drawing the same
    // curves a few times, nudged by a fraction of a pixel in clip space,
    // thickens them and leaves a soft glow around the core — which is the
    // look anyway.
    const px = 1.9 / this._viewport[0];
    const py = 1.9 / this._viewport[1];
    const all = [
      [0, 0, 1.0],
      [px, 0, 0.55], [-px, 0, 0.55],
      [0, py, 0.55], [0, -py, 0.55],
      [px, py, 0.26], [-px, -py, 0.26],
    ];
    // The glow around each curve is the first thing to go when frames tighten:
    // it is the most expensive part of the bust and the least load-bearing.
    const passes = this.quality >= 2 ? all : this.quality === 1 ? all.slice(0, 5) : all.slice(0, 1);
    for (const [ox, oy, gain] of passes) {
      gl.uniform2f(p.u.uOffset, ox, oy);
      gl.uniform1f(p.u.uGain, gain);
      gl.drawArrays(gl.LINES, 0, c.count);
    }

    gl.disableVertexAttribArray(p.a.aPos);
    gl.disableVertexAttribArray(p.a.aWeight);
    gl.disableVertexAttribArray(p.a.aBright);
  }

  _points(gl, energy, blink, pixelHeight) {
    const p = this.pointProgram;
    gl.useProgram(p.handle);
    gl.uniformMatrix4fv(p.u.uProj, false, this._proj);
    gl.uniformMatrix4fv(p.u.uMV, false, this._mv);
    gl.uniform1f(p.u.uTime, this.phase);
    gl.uniform1f(p.u.uScale, pixelHeight * 0.9);

    const bind = (posBuffer, seedBuffer) => {
      gl.bindBuffer(gl.ARRAY_BUFFER, posBuffer);
      gl.enableVertexAttribArray(p.a.aPos);
      gl.vertexAttribPointer(p.a.aPos, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, seedBuffer);
      gl.enableVertexAttribArray(p.a.aSeed);
      gl.vertexAttribPointer(p.a.aSeed, 2, gl.FLOAT, false, 0, 0);
    };

    // Particles.
    gl.uniform1f(p.u.uDrift, 1);
    gl.uniform1f(p.u.uSoft, 0);
    gl.uniform3f(p.u.uColour, 0.30 + energy * 0.25, 0.80, 0.92);
    bind(this.particleBuffer, this.particleSeedBuffer);
    const particles = this.quality >= 2 ? this.particleCount
      : this.quality === 1 ? Math.round(this.particleCount * 0.5)
      : 0;
    if (particles) gl.drawArrays(gl.POINTS, 0, particles);

    // Irises — they dim as the lids come down.
    const open = 1 - blink;
    if (open > 0.05) {
      gl.uniform1f(p.u.uDrift, 0);
      gl.uniform1f(p.u.uSoft, 1);
      const k = (0.45 + energy * 0.55) * open;
      gl.uniform3f(p.u.uColour, 0.42 * k, 0.86 * k, 1.0 * k);
      bind(this.eyeBuffer, this.eyeSeedBuffer);
      gl.drawArrays(gl.POINTS, 0, 2);
    }

    gl.disableVertexAttribArray(p.a.aPos);
    gl.disableVertexAttribArray(p.a.aSeed);
  }

  dispose() {
    this.gl = null;
    this.ready = false;
  }
}

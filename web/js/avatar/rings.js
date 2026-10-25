/**
 * RINGS avatar — two glowing rings drawn as real 3D circles projected to 2D.
 *
 * Canvas 2D rather than WebGL: a phone draws this at 60fps with a fraction of
 * the battery cost, and it needs no external library.
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
  }

  /**
   * @param {CanvasRenderingContext2D} ctx
   * @param {{w:number,h:number,time:number,dt:number,activity:string,level:number,motion:number}} env
   */
  draw(ctx, env) {
    const { w, h, time, activity, level, motion } = env;
    const cx = w / 2;
    const cy = h / 2;
    const radius = Math.min(w, h) * 0.42;

    const speed = activity === 'thinking' ? 1.9 : activity === 'listening' ? 1.1 : activity === 'speaking' ? 1.35 : 0.6;
    this.phase += env.dt * speed * motion;
    const p = this.phase;

    const energy = activity === 'listening' ? 0.35 + level * 0.9
      : activity === 'speaking' ? 0.5 + level * 0.8
      : activity === 'thinking' ? 0.55
      : 0.25;

    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(cx, cy);

    this._core(ctx, radius, time, energy, motion);

    // Outer ring: wide tilt, slow counter-rotation.
    this._ring(ctx, {
      radius: radius,
      tilt: 1.12 + Math.sin(time * 0.35 * motion) * 0.07,
      roll: -p * 0.45,
      spin: p * 0.8,
      width: Math.max(1.6, radius * 0.022),
      hue: 190,
      energy,
      segments: 160,
      ticks: 48,
      tickLength: radius * 0.06,
    });

    // Inner ring: opposite tilt and direction, tighter.
    this._ring(ctx, {
      radius: radius * 0.72,
      tilt: -0.95 + Math.cos(time * 0.3 * motion) * 0.08,
      roll: p * 0.6,
      spin: -p * 1.25,
      width: Math.max(1.2, radius * 0.016),
      hue: 205,
      energy: energy * 0.9,
      segments: 140,
      ticks: 32,
      tickLength: radius * 0.045,
    });

    if (activity === 'speaking' || activity === 'listening') {
      this._pulse(ctx, radius, time, level, activity);
    }

    ctx.restore();
  }

  _ring(ctx, o) {
    const pts = [];
    for (let i = 0; i <= o.segments; i++) {
      const t = (i / o.segments) * TAU;
      let p = { x: Math.cos(t) * o.radius, y: Math.sin(t) * o.radius, z: 0 };
      p = rotateX(p, o.tilt);
      p = rotateZ(p, o.roll);
      p = rotateY(p, o.spin);
      pts.push(p);
    }

    // Three continuous passes instead of one stroke per segment: the ring
    // reads as a smooth band rather than a dashed line, and a phone draws
    // three shadowed paths per ring instead of a hundred and sixty.
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    // pass 1 — the complete ring, faint, so the far side stays visible
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
    ctx.strokeStyle = `hsla(${o.hue}, 100%, 62%, ${0.10 + o.energy * 0.10})`;
    ctx.lineWidth = o.width * 0.55;
    ctx.stroke();

    // passes 2 and 3 — the near half, then the nearest quarter, each brighter
    for (const [threshold, widthScale, alpha, blur] of [
      [0, 1.0, 0.42 + o.energy * 0.40, 10],
      [o.radius * 0.55, 1.35, 0.55 + o.energy * 0.45, 20],
    ]) {
      ctx.strokeStyle = `hsla(${o.hue}, 100%, ${threshold ? 76 : 62}%, ${Math.min(1, alpha)})`;
      ctx.lineWidth = o.width * widthScale;
      ctx.shadowBlur = blur * (0.5 + o.energy);
      ctx.shadowColor = `hsla(${o.hue}, 100%, 65%, 0.75)`;
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

    // Tick marks riding on the ring.
    for (let i = 0; i < o.ticks; i++) {
      const t = (i / o.ticks) * TAU;
      let inner = { x: Math.cos(t) * o.radius, y: Math.sin(t) * o.radius, z: 0 };
      let outer = {
        x: Math.cos(t) * (o.radius + o.tickLength),
        y: Math.sin(t) * (o.radius + o.tickLength),
        z: 0,
      };
      for (const fn of [
        (p) => rotateX(p, o.tilt),
        (p) => rotateZ(p, o.roll),
        (p) => rotateY(p, o.spin),
      ]) {
        inner = fn(inner);
        outer = fn(outer);
      }
      const depth = (outer.z / o.radius + 1) / 2;
      if (depth < 0.35) continue;
      ctx.strokeStyle = `hsla(${o.hue - 10}, 100%, 70%, ${0.12 + depth * 0.55})`;
      ctx.lineWidth = Math.max(0.8, o.width * 0.5);
      ctx.beginPath();
      ctx.moveTo(inner.x, inner.y);
      ctx.lineTo(outer.x, outer.y);
      ctx.stroke();
    }
  }

  _core(ctx, radius, time, energy, motion) {
    const r = radius * (0.17 + Math.sin(time * 1.6 * motion) * 0.012 + energy * 0.05);
    const glow = ctx.createRadialGradient(0, 0, 0, 0, 0, r * 3.4);
    glow.addColorStop(0, `rgba(120, 240, 255, ${0.5 + energy * 0.35})`);
    glow.addColorStop(0.35, 'rgba(40, 170, 255, 0.18)');
    glow.addColorStop(1, 'rgba(10, 30, 60, 0)');
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(0, 0, r * 3.4, 0, TAU);
    ctx.fill();

    ctx.strokeStyle = `rgba(160, 250, 255, ${0.6 + energy * 0.3})`;
    ctx.lineWidth = Math.max(1, radius * 0.012);
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, TAU);
    ctx.stroke();
  }

  _pulse(ctx, radius, time, level, activity) {
    const rings = activity === 'speaking' ? 3 : 2;
    for (let i = 0; i < rings; i++) {
      const phase = (time * 0.6 + i / rings) % 1;
      const r = radius * (0.25 + phase * 0.95);
      const alpha = (1 - phase) * (0.14 + level * 0.4);
      if (alpha <= 0.01) continue;
      ctx.strokeStyle = `rgba(110, 230, 255, ${alpha})`;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.arc(0, 0, r, 0, TAU);
      ctx.stroke();
    }
  }
}

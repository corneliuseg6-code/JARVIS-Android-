/**
 * The holographic folder network.
 *
 * Every sphere is a real folder in JARVIS's workspace and every line is a
 * real parent-child relationship; nothing here is drawn for decoration. Tap a
 * sphere and it swells and lists the files actually inside it. When JARVIS
 * starts working in a folder, that sphere takes the highlight and the
 * previous one lets it go.
 *
 * Canvas 2D with hand-projected 3D positions rather than WebGL: the avatar
 * already owns a WebGL context, a canvas can only have one kind, and a few
 * dozen spheres do not need a GPU pipeline. Depth is carried by size,
 * brightness and draw order, which is also what keeps it cheap on a phone.
 */
import { api } from '../core/api.js';
import { t } from '../i18n/index.js';
import { toast } from './toast.js';

/**
 * The shell the network sits on, as a fraction of the canvas.
 *
 * Two radii rather than one. A single `min(w, h)` radius means a wide, short
 * panel — which is what a phone gives once the file list has taken its share
 * — draws a small network with most of the width empty beside it. Spreading
 * horizontally and vertically by their own fractions fills whatever shape
 * the panel happens to be.
 */
const SHELL_X = 0.30;
const SHELL_Y = 0.34;
const ROTATE = 0.055;          // radians a second, slow enough to read labels
const SPRING = 0.14;           // how fast a sphere settles into a new size

export default class Folders {
  constructor({ onOpenFile } = {}) {
    this.canvas = document.getElementById('folder-canvas');
    this.panel = document.getElementById('folders-panel');
    this.listEl = document.getElementById('folder-files');
    this.titleEl = document.getElementById('folder-title');
    this.emptyEl = document.getElementById('folder-empty');
    this.onOpenFile = onOpenFile;

    this.ctx = this.canvas?.getContext('2d') || null;
    this.nodes = [];
    this.links = [];
    this.selected = '';
    this.active = '';
    this.spin = 0;
    this.frame = 0;
    this.running = false;
    this.dpr = 1;
    this.quality = 1;
    this.lastTime = 0;

    this._loop = this._loop.bind(this);
    this._resize = this._resize.bind(this);

    if (this.canvas) {
      window.addEventListener('resize', this._resize, { passive: true });
      this.canvas.addEventListener('pointerdown', (e) => this._tap(e));
      // The panel is hidden at boot, so the canvas measures 0x0 and a single
      // reading here would leave the draw loop permanently bailing on a zero
      // width. A ResizeObserver catches the moment the FOLDERS tab gives it
      // a size.
      if ('ResizeObserver' in window) {
        this.observer = new ResizeObserver(() => this._resize());
        this.observer.observe(this.canvas);
      }
      this._resize();
    }
  }

  // ------------------------------------------------------------------ data

  /**
   * Fetch the real tree and lay it out.
   *
   * Positions are derived from each node's place in the tree, not random, so
   * a folder does not jump to a different corner every refresh.
   */
  async refresh() {
    let data;
    try {
      data = await api.workspace(3);
    } catch (err) {
      this._showError(String(err?.message || err));
      return;
    }
    const previous = new Map(this.nodes.map((n) => [n.id, n]));
    this.nodes = this._layout(data.nodes || []).map((node) => {
      const old = previous.get(node.id);
      // Keep whatever an existing sphere was doing: a refresh while one is
      // swelling should not reset it.
      return old ? { ...node, scale: old.scale, glow: old.glow } : node;
    });
    this.links = (data.links || []).filter((l) =>
      this.nodes.some((n) => n.id === l.from) && this.nodes.some((n) => n.id === l.to));
    this.root = data.root || '';
    const active = (data.active || {}).path || '';
    if (active !== this.active) this.setActive(active, { silent: true });
    if (this.emptyEl) this.emptyEl.hidden = this.nodes.length > 1;
    if (!this.selected && this.nodes.length) this.select('', { quiet: true });
    this.start();
  }

  /**
   * Place the nodes on a sphere.
   *
   * The root sits at the centre; the seven sections spread evenly around the
   * equator; a child orbits its parent. Deriving every angle from the node's
   * index means the same workspace always draws the same way.
   */
  _layout(raw) {
    const byParent = new Map();
    for (const node of raw) {
      if (node.parent === null) continue;
      if (!byParent.has(node.parent)) byParent.set(node.parent, []);
      byParent.get(node.parent).push(node);
    }

    const placed = [];
    const put = (node, x, y, z, depth) => {
      placed.push({
        ...node, x, y, z, depth,
        scale: 1, glow: 0,
        // Drawn radius before depth and selection are applied.
        // The root is a marker, not the subject: an oversized centre sphere
        // swallows the sections arranged around it.
        base: depth === 0 ? 13 : depth === 1 ? 15 : depth === 2 ? 11 : 8,
      });
    };

    const root = raw.find((n) => n.parent === null);
    if (root) put(root, 0, 0, 0, 0);

    const sections = byParent.get('') || [];
    sections.forEach((node, i) => {
      const angle = (i / Math.max(1, sections.length)) * Math.PI * 2;
      // A gentle tilt per section, so the ring reads as a sphere rather than
      // a flat dial.
      const lift = Math.sin(i * 1.7) * 0.52;
      put(node, Math.cos(angle), lift, Math.sin(angle), 1);

      const children = byParent.get(node.id) || [];
      children.forEach((child, j) => {
        const spread = 0.42;
        const a = angle + (j - (children.length - 1) / 2) * spread * 0.6;
        const r = 1.45;
        put(child, Math.cos(a) * r, lift + 0.3 + (j % 2) * 0.16, Math.sin(a) * r, 2);

        for (const grand of byParent.get(child.id) || []) {
          const g = a + 0.2;
          put(grand, Math.cos(g) * 1.9, lift + 0.6, Math.sin(g) * 1.9, 3);
        }
      });
    });
    return placed;
  }

  // ------------------------------------------------------------ selection

  /** Tap a sphere: swell it and list what is really inside. */
  async select(path, { quiet = false } = {}) {
    this.selected = path;
    if (this.titleEl) {
      this.titleEl.textContent = path || t('folders.workspace');
    }
    if (!this.listEl) return;
    this.listEl.innerHTML = '';
    let data;
    try {
      data = await api.workspaceList(path);
    } catch (err) {
      if (!quiet) toast(String(err?.message || err), 'error');
      return;
    }
    const entries = data.entries || [];
    if (!entries.length) {
      const empty = document.createElement('p');
      empty.className = 'empty';
      empty.textContent = t('folders.emptyFolder');
      this.listEl.appendChild(empty);
      return;
    }
    for (const entry of entries) {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'folder-file' + (entry.kind === 'folder' ? ' is-folder' : '');
      row.dataset.path = entry.path;

      const icon = document.createElement('span');
      icon.className = 'folder-file-ico';
      icon.setAttribute('aria-hidden', 'true');
      icon.textContent = entry.kind === 'folder' ? '◈' : '·';

      const name = document.createElement('span');
      name.className = 'folder-file-name';
      name.textContent = entry.name;

      const meta = document.createElement('span');
      meta.className = 'folder-file-meta';
      meta.textContent = entry.kind === 'folder' ? '' : this._size(entry.size);

      row.append(icon, name, meta);
      row.addEventListener('click', () => {
        if (entry.kind === 'folder') this.select(entry.path);
        else this.onOpenFile?.(entry.path);
      });
      this.listEl.appendChild(row);
    }
  }

  _size(bytes) {
    if (bytes < 1000) return `${bytes} B`;
    if (bytes < 1_000_000) return `${(bytes / 1000).toFixed(1)} kB`;
    return `${(bytes / 1_000_000).toFixed(1)} MB`;
  }

  /**
   * Move the highlight to the folder JARVIS is working in.
   *
   * The previous sphere is not snapped back: both animate, so the highlight
   * reads as moving from one to the other rather than blinking out and in.
   */
  setActive(path, { silent = false } = {}) {
    const next = path || '';
    if (next === this.active) return;
    this.active = next;
    if (!silent && next) {
      const node = this.nodes.find((n) => n.id === next);
      if (node) this.select(next, { quiet: true });
    }
    this.start();
  }

  _showError(detail) {
    if (!this.emptyEl) return;
    this.emptyEl.hidden = false;
    this.emptyEl.textContent = t('folders.unreachable', { detail: detail.slice(0, 90) });
  }

  // -------------------------------------------------------------- drawing

  _resize() {
    if (!this.canvas) return;
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.canvas.width = Math.round(rect.width * this.dpr);
    this.canvas.height = Math.round(rect.height * this.dpr);
    this.w = rect.width;
    this.h = rect.height;
  }

  /** Project a node's 3D position to the canvas, with depth for scaling. */
  _project(node) {
    const spin = this.spin;
    const x = node.x * Math.cos(spin) - node.z * Math.sin(spin);
    const z = node.x * Math.sin(spin) + node.z * Math.cos(spin);
    const shellX = this.w * SHELL_X;
    const shellY = this.h * SHELL_Y;
    // A mild perspective: far spheres shrink, near ones come forward.
    const perspective = 1 / (1 + z * 0.16);
    return {
      sx: this.w / 2 + x * shellX * perspective,
      sy: this.h / 2 + node.y * shellY * perspective,
      depth: perspective,
      z,
    };
  }

  start() {
    if (!this.ctx) return;
    // Re-measure on the way in: the panel may have just been shown.
    if (!this.w) this._resize();
    if (this.running) return;
    this.running = true;
    this.lastTime = performance.now();
    this.frame = requestAnimationFrame(this._loop);
  }

  stop() {
    this.running = false;
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = 0;
  }

  /** Shed detail when the device is struggling; the avatar does the same. */
  setQuality(quality) {
    this.quality = Math.max(0.4, Math.min(1, quality || 1));
  }

  _loop(now) {
    if (!this.running) { this.frame = 0; return; }
    const dt = Math.min(0.05, (now - this.lastTime) / 1000);
    this.lastTime = now;
    this.spin += ROTATE * dt * this.quality;
    this._draw(now / 1000, dt);
    this.frame = requestAnimationFrame(this._loop);
  }

  _draw(time, dt) {
    const { ctx } = this;
    if (!ctx || !this.w) return;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.w, this.h);

    // Ease each sphere toward the size its state calls for, rather than
    // snapping: this is what makes the highlight glide between projects.
    for (const node of this.nodes) {
      const wanted = node.id === this.active ? 1.85
        : node.id === this.selected ? 1.45 : 1;
      node.scale += (wanted - node.scale) * SPRING;
      const glowWanted = node.id === this.active ? 1 : node.id === this.selected ? 0.55 : 0;
      node.glow += (glowWanted - node.glow) * SPRING;
    }

    const points = new Map();
    for (const node of this.nodes) points.set(node.id, this._project(node));

    // Links first, so a sphere always sits on top of its own threads.
    ctx.lineCap = 'round';
    for (const link of this.links) {
      const a = points.get(link.from);
      const b = points.get(link.to);
      if (!a || !b) continue;
      const lit = link.from === this.active || link.to === this.active
        || link.from === this.selected || link.to === this.selected;
      const depth = (a.depth + b.depth) / 2;
      ctx.strokeStyle = lit
        ? `rgba(141, 243, 236, ${0.5 * depth})`
        : `rgba(67, 190, 210, ${0.17 * depth})`;
      ctx.lineWidth = (lit ? 1.5 : 0.8) * depth;
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      ctx.lineTo(b.sx, b.sy);
      ctx.stroke();
    }

    // Spheres back to front, so near ones overlap far ones correctly.
    const order = [...this.nodes].sort((p, q) => points.get(q.id).z - points.get(p.id).z);
    for (const node of order) {
      const p = points.get(node.id);
      const r = node.base * node.scale * p.depth
        * (Math.min(this.w, this.h * 1.9) / 400);
      const pulse = node.id === this.active
        ? 1 + Math.sin(time * 2.6) * 0.06
        : 1;
      this._sphere(ctx, p.sx, p.sy, r * pulse, node, p.depth);
      this._label(ctx, p.sx, p.sy + r * pulse + 9, node, p.depth);
      node.screen = { x: p.sx, y: p.sy, r: r * pulse };
    }
  }

  _sphere(ctx, x, y, r, node, depth) {
    if (r <= 0.5) return;
    const hot = node.glow;
    // A halo, so an active folder reads as lit from inside.
    if (hot > 0.02) {
      const halo = ctx.createRadialGradient(x, y, r * 0.5, x, y, r * 2.6);
      halo.addColorStop(0, `rgba(141, 243, 236, ${0.30 * hot})`);
      halo.addColorStop(1, 'rgba(141, 243, 236, 0)');
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(x, y, r * 2.6, 0, Math.PI * 2);
      ctx.fill();
    }

    const body = ctx.createRadialGradient(x - r * 0.35, y - r * 0.4, r * 0.1, x, y, r);
    const a = (0.5 + depth * 0.4) * (0.65 + hot * 0.35);
    body.addColorStop(0, `rgba(190, 252, 255, ${Math.min(1, a + 0.25)})`);
    body.addColorStop(0.45, `rgba(${hot > 0.3 ? '96, 226, 232' : '58, 170, 200'}, ${a})`);
    body.addColorStop(1, `rgba(12, 54, 82, ${a * 0.55})`);
    ctx.fillStyle = body;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = `rgba(141, 243, 236, ${(0.25 + hot * 0.5) * depth})`;
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  _label(ctx, x, y, node, depth) {
    const key = node.id === this.active || node.id === this.selected;
    // Sections are the map; deeper folders only name themselves when they
    // are the one being looked at. Labelling everything turned the middle of
    // the network into overlapping text.
    if (!key && node.kind !== 'section') return;
    if (depth < 0.55 && !key) return;
    const size = Math.max(8, 10 * depth);
    ctx.font = `600 ${size}px "Rajdhani", system-ui, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    const lit = node.id === this.active ? 1 : node.id === this.selected ? 0.85 : 0.5;
    ctx.fillStyle = `rgba(190, 252, 255, ${lit * depth})`;
    if (node.kind === 'root') return;   // the centre needs no caption
    ctx.fillText(node.name.slice(0, 18), x, y);
    if (node.files && (node.id === this.active || node.id === this.selected)) {
      ctx.fillStyle = `rgba(141, 243, 236, ${0.6 * depth})`;
      ctx.font = `500 ${size * 0.82}px "Rajdhani", system-ui, sans-serif`;
      ctx.fillText(t('folders.fileCount', { n: node.files }), x, y + size + 1);
    }
  }

  /** Which sphere was tapped, if any. Nearest first, so the top one wins. */
  _tap(event) {
    const rect = this.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    let best = null;
    let bestR = Infinity;
    for (const node of this.nodes) {
      if (!node.screen) continue;
      const dx = x - node.screen.x;
      const dy = y - node.screen.y;
      const distance = Math.hypot(dx, dy);
      // A generous target: a 12px sphere is hard to hit with a thumb.
      const reach = Math.max(node.screen.r, 18);
      if (distance <= reach && distance < bestR) {
        best = node;
        bestR = distance;
      }
    }
    if (best) this.select(best.id);
  }
}

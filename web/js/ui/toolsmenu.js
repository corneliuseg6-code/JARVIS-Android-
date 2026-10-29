/**
 * AI TOOLS — the slide-out menu behind the "AI ☰" button.
 *
 * One glance at every AI tool JARVIS can use, with its real state, and one
 * tap to the place that configures it. It does not duplicate configuration:
 * "Set up" opens the existing AI Connections or Tool Armory card, so there
 * is one source of truth for every setting.
 *
 * Status is real and costs nothing (see /api/tools/overview): the local model
 * is probed, the brain in use reports its health, and a paid brain that is
 * not in use is never contacted behind the user's back — it says "not
 * tested" and offers Test, which only lists models (unbilled).
 *
 * To add a future tool, add one entry to SECTIONS: an id, a title key, an
 * icon and a render(data) that returns rows. Nothing else needs to change.
 */
import { api } from '../core/api.js';
import { t } from '../i18n/index.js';
import { toast } from './toast.js';

/** ChatGPT is the name people know; the integration is the OpenAI API. */
const NAMES = { openai: 'ChatGPT', claude: 'Claude', gemini: 'Gemini', ollama: 'Ollama' };

/** Backend state → the dot's colour class and its label key. */
const DOT = {
  connected: ['ok', 'tools.st.connected'],
  untested: ['ready', 'tools.st.untested'],
  off: ['off', 'tools.st.off'],
  blocked: ['warn', 'tools.st.blocked'],
  disconnected: ['bad', 'tools.st.offline'],
  connecting: ['busy', 'tools.st.connecting'],
  error: ['bad', 'tools.st.error'],
  idle: ['off', 'tools.st.idle'],      // an MCP server that is simply not connected
};

const POLL_MS = 10000;

export default class ToolsMenu {
  constructor({ openView } = {}) {
    this.root = document.getElementById('app');
    this.panel = document.getElementById('tools-menu');
    this.body = document.getElementById('tools-body');
    this.scrim = document.getElementById('tools-scrim');
    this.button = document.getElementById('btn-tools');
    this.openView = openView;
    this.data = null;
    this.timer = 0;

    /** The registry. Order here is order on screen. */
    this.SECTIONS = [
      { id: 'models', title: 'tools.models', icon: '◇', render: (d) => this._models(d) },
      { id: 'code', title: 'tools.code', icon: '⌘', render: (d) => this._code(d) },
      { id: 'openart', title: 'tools.art', icon: '✦', render: (d) => this._openart(d) },
      { id: 'mcp', title: 'tools.mcp', icon: '⬡', render: (d) => this._mcp(d) },
    ];

    this.button?.addEventListener('click', () => (this.isOpen() ? this.close() : this.open()));
    document.getElementById('btn-tools-close')?.addEventListener('click', () => this.close());
    document.getElementById('btn-tools-refresh')?.addEventListener('click', () => this.refresh());
    this.scrim?.addEventListener('click', () => this.close());
    this._wireSwipe();
  }

  isOpen() { return this.root?.getAttribute('data-tools') === 'open'; }

  open() {
    this.root?.setAttribute('data-tools', 'open');
    this.panel?.setAttribute('aria-hidden', 'false');
    this.button?.setAttribute('aria-expanded', 'true');
    if (this.scrim) this.scrim.hidden = false;
    this.refresh();
    clearInterval(this.timer);
    // Poll only while open: a menu nobody is looking at costs nothing.
    this.timer = setInterval(() => this.refresh({ quiet: true }), POLL_MS);
  }

  close() {
    this.root?.setAttribute('data-tools', 'closed');
    this.panel?.setAttribute('aria-hidden', 'true');
    this.button?.setAttribute('aria-expanded', 'false');
    if (this.scrim) this.scrim.hidden = true;
    clearInterval(this.timer);
    this.timer = 0;
  }

  async refresh({ quiet = false } = {}) {
    if (!this.body) return;
    if (!quiet && !this.data) this.body.replaceChildren(this._empty(t('common.loading')));
    try {
      this.data = await api.toolsOverview();
    } catch {
      if (!quiet) this.body.replaceChildren(this._empty(t('error.network'), true));
      return;
    }
    this.render();
  }

  render() {
    if (!this.body || !this.data) return;
    const frag = document.createDocumentFragment();
    for (const section of this.SECTIONS) {
      const box = document.createElement('section');
      box.className = 'tm-section';
      box.dataset.section = section.id;
      const head = document.createElement('h3');
      head.className = 'tm-title';
      const ico = document.createElement('span');
      ico.className = 'tm-ico';
      ico.setAttribute('aria-hidden', 'true');
      ico.textContent = section.icon;
      const label = document.createElement('span');
      label.textContent = t(section.title);
      head.append(ico, label);
      box.appendChild(head);
      for (const row of section.render(this.data)) box.appendChild(row);
      frag.appendChild(box);
    }
    const foot = document.createElement('p');
    foot.className = 'tm-foot';
    foot.textContent = t('tools.more');
    frag.appendChild(foot);
    this.body.replaceChildren(frag);
  }

  // ------------------------------------------------------------- sections

  _models(d) {
    const rows = [];
    const spend = d.spend || {};
    const meta = document.createElement('p');
    meta.className = 'tm-meta';
    meta.textContent = t('tools.routing', { mode: t('conn.route.' + (d.routing || 'manual')) }) + ' · '
      + (spend.daily_limit
        ? t('tools.spend', { usd: Number(spend.today || 0).toFixed(2), limit: Number(spend.daily_limit).toFixed(2) })
        : t('tools.spendNoLimit', { usd: Number(spend.today || 0).toFixed(2) }));
    rows.push(meta);

    for (const m of d.models || []) {
      const actions = [];
      if (m.active) actions.push(this._chip(t('tools.inUse'), null, 'is-on'));
      else if (m.usable) {
        actions.push(this._chip(t('tools.use'), async () => {
          await api.setConfig({ llm_provider: m.provider });
          toast(t('conn.switched', { name: NAMES[m.provider] }), 'ok');
          await this.refresh({ quiet: true });
        }));
      }
      if (m.billed && m.state !== 'off') {
        actions.push(this._chip(t('tools.test'), async (chip) => {
          chip.textContent = t('conn.testing');
          const info = await api.llmTest(m.provider);
          const ok = info.state === 'connected';
          toast(ok ? t('conn.testOk', { model: info.model, count: (info.models || []).length })
            : t('conn.testFail', { reason: info.detail || info.error || info.state }), ok ? 'ok' : 'error');
          chip.textContent = t('tools.test');
        }));
      }
      actions.push(this._chip(t('tools.setup'), () => this._goto('connections', `[data-provider="${m.provider}"]`)));
      const sub = [m.model];
      if (m.provider === 'openai') sub.push(t('tools.viaApi'));
      if (m.detail) sub.push(m.detail);
      else if (!m.usable && m.state !== 'off' && m.reason) sub.push(m.reason);
      rows.push(this._row(NAMES[m.provider] || m.label, m.state, sub.join(' · '), actions, m.billed));
    }
    return rows;
  }

  _code(d) {
    const c = d.code || {};
    let state = 'off';
    let sub = '';
    if (!c.installed) { state = 'error'; sub = c.reason || t('conn.codeUnavailable'); }
    else if (!c.enabled) { state = 'off'; sub = c.version; }
    else if (!c.workdir_set) { state = 'blocked'; sub = t('conn.codeNeedsWorkdir'); }
    else if (c.auth_known && !c.signed_in) { state = 'blocked'; sub = t('conn.codeAuthNo'); }
    else {
      state = 'connected';
      sub = [c.version, c.auth_method].filter(Boolean).join(' · ');
    }
    const label = state === 'connected' ? 'tools.st.ready' : null;
    const extra = [];
    if (c.running) extra.push(t('tools.running', { count: c.running }));
    if (c.pending) extra.push(t('tools.pending', { count: c.pending }));
    const row = this._row('Claude Code', state, [sub, ...extra].filter(Boolean).join(' · '),
      [this._chip(c.pending ? t('tools.review') : t('tools.setup'),
        () => this._goto('connections', '[data-provider="claude-code"]'), c.pending ? 'is-hot' : '')],
      false, label);
    return [row];
  }

  _openart(d) {
    const a = d.openart || {};
    const auth = a.auth || {};
    let state = 'off';
    let sub = t('tools.credits');
    const actions = [];
    if (!a.configured) {
      actions.push(this._chip(t('conn.artSetup'), async () => {
        await api.openartSetup();
        this._goto('connections', '[data-provider="openart"]');
      }));
    } else if (!auth.signed_in) {
      state = 'blocked';
      sub = t('conn.artSignedOut') + ' · ' + sub;
      actions.push(this._chip(t('tools.signIn'), () => this._goto('connections', '[data-provider="openart"]')));
    } else {
      state = a.state === 'connected' ? 'connected' : (a.state === 'error' ? 'error' : 'disconnected');
      if (a.state === 'connected') sub = t('tools.toolsCount', { count: (a.tools || []).length }) + ' · ' + sub;
      else if (a.error) sub = a.error;
      if (a.state !== 'connected') {
        actions.push(this._chip(t('tools.connect'), async () => {
          const r = await api.armoryConnect(a.id);
          if (r.state !== 'connected') toast(r.error || t('state.error'), 'error');
          await this.refresh({ quiet: true });
        }));
      }
    }
    actions.push(this._chip(t('tools.open'), () => this._goto('connections', '[data-provider="openart"]')));
    return [this._row('OpenArt', state, sub, actions, true)];
  }

  _mcp(d) {
    const rows = [];
    const list = (d.mcp || []).filter((c) => !(d.openart?.id && c.id === d.openart.id));
    if (!list.length) rows.push(this._empty(t('tools.noMcp')));
    for (const c of list) {
      const connected = c.state === 'connected';
      const sub = connected ? t('tools.toolsCount', { count: c.tools }) : (c.error || c.transport);
      const toggle = this._chip(connected ? t('tools.disconnect') : t('tools.connect'), async () => {
        const r = connected ? await api.armoryDisconnect(c.id) : await api.armoryConnect(c.id);
        if (!connected && r.state !== 'connected') toast(r.error || t('state.error'), 'error');
        await this.refresh({ quiet: true });
      });
      rows.push(this._row(c.name, c.state === 'disconnected' ? 'idle' : c.state, sub, [toggle], c.billed));
    }
    const manage = this._chip(t('tools.manage'), () => this._goto('armory'));
    manage.classList.add('tm-wide');
    rows.push(manage);
    return rows;
  }

  // ------------------------------------------------------------- pieces

  _row(name, state, sub, actions, billed = false, labelKey = null) {
    const [cls, key] = DOT[state] || ['off', 'tools.st.unknown'];
    const row = document.createElement('div');
    row.className = 'tm-row';
    row.dataset.state = state;
    const dot = document.createElement('span');
    dot.className = 'tm-dot is-' + cls;
    dot.setAttribute('aria-hidden', 'true');
    const text = document.createElement('div');
    text.className = 'tm-text';
    const top = document.createElement('div');
    top.className = 'tm-name';
    top.textContent = name;
    if (billed) {
      const tag = document.createElement('span');
      tag.className = 'tm-tag';
      tag.textContent = '$';
      tag.title = t('tools.paid');
      top.appendChild(tag);
    }
    const status = document.createElement('div');
    status.className = 'tm-status';
    status.textContent = t(labelKey || key);
    text.append(top, status);
    if (sub) {
      const line = document.createElement('div');
      line.className = 'tm-sub';
      line.textContent = sub;
      text.appendChild(line);
    }
    const acts = document.createElement('div');
    acts.className = 'tm-actions';
    acts.append(...actions);
    row.append(dot, text, acts);
    return row;
  }

  _chip(label, onClick, extra = '') {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'tm-chip' + (extra ? ' ' + extra : '');
    b.textContent = label;
    if (!onClick) { b.disabled = true; return b; }
    b.addEventListener('click', async () => {
      b.disabled = true;
      try { await onClick(b); } catch (err) { toast(err?.message || t('error.network'), 'error'); }
      b.disabled = false;
    });
    return b;
  }

  _empty(text, bad = false) {
    const p = document.createElement('p');
    p.className = 'tm-empty' + (bad ? ' is-bad' : '');
    p.textContent = text;
    return p;
  }

  /** Close the menu, open a panel, and bring the relevant card into view. */
  _goto(view, selector = '') {
    this.close();
    this.openView?.(view);
    if (!selector) return;
    let tries = 0;
    const find = () => {
      const el = document.querySelector('#connections-body ' + selector);
      if (el) { el.scrollIntoView({ block: 'start', behavior: 'smooth' }); return; }
      if (++tries < 40) setTimeout(find, 125);
    };
    find();
  }

  /** A leftward swipe closes the menu, like the drawer it slides out of. */
  _wireSwipe() {
    if (!this.panel) return;
    let x0 = null;
    let y0 = null;
    this.panel.addEventListener('pointerdown', (e) => { x0 = e.clientX; y0 = e.clientY; });
    this.panel.addEventListener('pointerup', (e) => {
      if (x0 === null) return;
      const dx = e.clientX - x0;
      const dy = Math.abs(e.clientY - y0);
      x0 = null;
      if (dx < -60 && dy < 40) this.close();
    });
    this.panel.addEventListener('pointercancel', () => { x0 = null; });
  }
}

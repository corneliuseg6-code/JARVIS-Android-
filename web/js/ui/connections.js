/**
 * AI CONNECTIONS — one panel for every brain JARVIS can use.
 *
 * Three cards, in the order of what they cost you:
 *
 *   Ollama       free, local, offline. The default, and the fallback.
 *   OpenAI       paid API. Key on the backend, cost notice before use.
 *   Claude       paid API. Same, plus a thinking-effort control.
 *
 * …and below them Claude Code, which is not a chat provider at all but a
 * local CLI that can read and change files. It gets an approval queue rather
 * than a switch that just runs things.
 *
 * Two rules this file exists to keep:
 *
 *  - **No decorative controls.** Every switch writes real configuration and
 *    every Test button makes a real call. Where something cannot work — no
 *    key, no CLI, unsupported platform — the control is disabled and says
 *    why, rather than looking available and failing later.
 *  - **The key never comes back.** The browser posts a key and is told only
 *    whether one is now stored. There is no getter, here or in the backend.
 */
import { api } from '../core/api.js';
import * as state from '../core/state.js';
import { t } from '../i18n/index.js';
import { toast } from './toast.js';

const PAID = new Set(['openai', 'claude', 'gemini']);

/** Per paid provider: which settings it owns and how it is described. */
const PROVIDER = {
  openai: {
    enabled: 'openai_enabled', ack: 'openai_cost_ack', model: 'openai_model',
    placeholder: 'sk-…', cost: 'conn.costOpenai', subscription: 'conn.subOpenai',
    keyPage: 'https://platform.openai.com/api-keys',
  },
  claude: {
    enabled: 'claude_enabled', ack: 'claude_cost_ack', model: 'claude_model',
    placeholder: 'sk-ant-…', cost: 'conn.costClaude', subscription: 'conn.subClaude',
    keyPage: 'https://console.anthropic.com/settings/keys',
  },
  gemini: {
    enabled: 'gemini_enabled', ack: 'gemini_cost_ack', model: 'gemini_model',
    placeholder: 'AIza…', cost: 'conn.costGemini', subscription: 'conn.subGemini',
    keyPage: 'https://aistudio.google.com/apikey',
  },
};

export default class Connections {
  constructor({ onConfigChange } = {}) {
    this.host = document.getElementById('connections-body');
    this.onConfigChange = onConfigChange;
    this.data = null;
    this.code = null;
    this.busy = false;
    this.poll = 0;
  }

  /** Called when the panel opens. */
  async refresh() {
    if (!this.host) return;
    this._skeleton();
    try {
      this.data = await api.llmConnections();
      this.code = this.data.code || null;
    } catch {
      this.data = null;
      this.host.innerHTML = '';
      this.host.appendChild(this._error(t('error.network')));
      return;
    }
    this.render();
    this._watchRequests();
  }

  /** Leaving the panel: stop every poll. */
  stop() {
    this._stopRequestPoll();
    if (this._signInPoll) { clearInterval(this._signInPoll); this._signInPoll = 0; }
  }

  _stopRequestPoll() {
    if (this.poll) { clearInterval(this.poll); this.poll = 0; }
  }

  render() {
    if (!this.host || !this.data) return;
    const cfg = state.get('config') || {};
    this.host.innerHTML = '';
    this.host.appendChild(this._routingCard(cfg));
    this.host.appendChild(this._costsCard(cfg));
    for (const conn of this.data.connections) {
      this.host.appendChild(this._providerCard(conn, cfg));
    }
    this.host.appendChild(this._openartCard());
    this.host.appendChild(this._codeCard(cfg));
  }

  // ---------------------------------------------------------------- chrome

  _skeleton() {
    this.host.innerHTML = '';
    const p = document.createElement('p');
    p.className = 'conn-empty';
    p.textContent = t('common.loading');
    this.host.appendChild(p);
  }

  _error(text) {
    const p = document.createElement('p');
    p.className = 'conn-empty is-bad';
    p.textContent = text;
    return p;
  }

  _card(title, badgeState, badgeText) {
    const card = document.createElement('section');
    card.className = 'conn-card';
    const head = document.createElement('div');
    head.className = 'conn-head';
    const name = document.createElement('h3');
    name.textContent = title;
    const badge = document.createElement('span');
    badge.className = 'conn-badge';
    badge.dataset.state = badgeState;
    badge.textContent = badgeText;
    head.append(name, badge);
    card.appendChild(head);
    return card;
  }

  _row(label, control) {
    const row = document.createElement('label');
    row.className = 'conn-row';
    const text = document.createElement('span');
    text.textContent = label;
    row.append(text, control);
    return row;
  }

  _note(text, kind = '') {
    const p = document.createElement('p');
    p.className = 'conn-note' + (kind ? ' is-' + kind : '');
    p.textContent = text;
    return p;
  }

  _switch(label, checked, disabled, onChange) {
    const wrap = document.createElement('label');
    wrap.className = 'conn-switch' + (disabled ? ' is-disabled' : '');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = !!checked;
    input.disabled = !!disabled;
    input.addEventListener('change', () => onChange(input.checked, input));
    const text = document.createElement('span');
    text.textContent = label;
    wrap.append(input, text);
    return wrap;
  }

  async _save(patch) {
    if (this.busy) return null;
    this.busy = true;
    try {
      const cfg = await api.setConfig(patch);
      state.set({ config: cfg });
      this.onConfigChange?.(cfg);
      return cfg;
    } catch (err) {
      toast(err?.message || t('error.network'), 'error');
      return null;
    } finally {
      this.busy = false;
    }
  }

  // -------------------------------------------------------------- providers

  _providerCard(conn, cfg) {
    const paid = PAID.has(conn.provider);
    const usable = !paid || (conn.enabled && conn.key_set && conn.cost_ack);
    const card = this._card(
      conn.label,
      conn.active ? (conn.state === 'connected' ? 'connected' : conn.state) : 'idle',
      conn.active ? t('conn.active') : (conn.enabled ? t('conn.standby') : t('common.off')),
    );
    card.dataset.provider = conn.provider;

    if (conn.provider === 'ollama') {
      card.appendChild(this._note(t('conn.ollamaNote')));
    } else {
      // --- enable/disable -------------------------------------------------
      card.appendChild(this._switch(
        t('conn.enable'), conn.enabled, false,
        async (checked) => {
          const patch = { [PROVIDER[conn.provider].enabled]: checked };
          // Turning a connection off must not leave it selected as the brain.
          if (!checked && conn.active) patch.llm_provider = 'ollama';
          if (await this._save(patch)) await this.refresh();
        },
      ));

      // --- the cost notice, before anything can be billed -----------------
      card.appendChild(this._costNotice(conn, cfg));

      // --- the key --------------------------------------------------------
      card.appendChild(this._keyBlock(conn));

      // --- model ----------------------------------------------------------
      card.appendChild(this._modelBlock(conn, cfg));

      if (conn.provider === 'claude') card.appendChild(this._effortBlock(cfg));

      // A chat subscription is not an API account. Said plainly, because
      // assuming otherwise is the usual way this costs someone money.
      card.appendChild(this._note(t(PROVIDER[conn.provider].subscription), 'warn'));
      card.appendChild(this._link(t('conn.getKey'), PROVIDER[conn.provider].keyPage));
    }

    // --- status + test ----------------------------------------------------
    const actions = document.createElement('div');
    actions.className = 'conn-actions';

    const test = document.createElement('button');
    test.type = 'button';
    test.className = 'btn btn-ghost btn-sm';
    test.dataset.act = 'test';
    test.textContent = t('conn.test');
    test.disabled = paid && !conn.key_set;
    test.addEventListener('click', () => this._test(conn.provider, result));
    actions.appendChild(test);

    const use = document.createElement('button');
    use.type = 'button';
    use.className = 'btn btn-primary btn-sm';
    use.dataset.act = 'use';
    use.textContent = conn.active ? t('conn.inUse') : t('conn.use');
    use.disabled = conn.active || !usable;
    use.addEventListener('click', async () => {
      if (await this._save({ llm_provider: conn.provider })) {
        toast(t('conn.switched', { name: conn.label }), 'ok');
        await this.refresh();
      }
    });
    actions.appendChild(use);
    card.appendChild(actions);

    const result = document.createElement('p');
    result.className = 'conn-result';
    if (conn.active && conn.detail) result.textContent = conn.detail;
    card.appendChild(result);

    if (paid && conn.enabled && !usable) {
      card.appendChild(this._note(
        !conn.key_set ? t('conn.needsKey') : t('conn.needsAck'), 'warn',
      ));
    } else if (paid && usable && this.data.usable?.[conn.provider]?.ok === false) {
      // Configured, but stopped by a spending limit.
      card.appendChild(this._note(this.data.usable[conn.provider].reason, 'bad'));
    }
    return card;
  }

  _costNotice(conn, cfg) {
    const box = document.createElement('div');
    box.className = 'cost-note';
    const title = document.createElement('strong');
    title.textContent = t('conn.costTitle', { name: conn.label });
    box.appendChild(title);

    const body = document.createElement('p');
    body.textContent = t(PROVIDER[conn.provider].cost);
    box.appendChild(body);

    // Real prices for the models we offer, so "it costs money" is a number.
    if (conn.pricing) {
      const list = document.createElement('ul');
      list.className = 'cost-prices';
      for (const [model, price] of Object.entries(conn.pricing)) {
        if (!price) continue;
        const li = document.createElement('li');
        li.textContent = t('conn.price', {
          model, input: price.input.toFixed(2), output: price.output.toFixed(2),
        });
        list.appendChild(li);
      }
      if (list.children.length) box.appendChild(list);
    }

    const window = document.createElement('p');
    window.textContent = t('conn.costWindow', { count: cfg.chat_history_limit || 20 });
    box.appendChild(window);

    const ackKey = PROVIDER[conn.provider].ack;
    box.appendChild(this._switch(
      t('conn.costAck'), conn.cost_ack, false,
      async (checked) => {
        const patch = { [ackKey]: checked };
        if (!checked && conn.active) patch.llm_provider = 'ollama';
        if (await this._save(patch)) await this.refresh();
      },
    ));
    return box;
  }

  _keyBlock(conn) {
    const block = document.createElement('div');
    block.className = 'conn-key';

    const state_ = document.createElement('p');
    state_.className = 'conn-note' + (conn.key_set ? ' is-ok' : '');
    state_.textContent = conn.key_set ? t('conn.keySet') : t('conn.keyNone');
    block.appendChild(state_);

    const input = document.createElement('input');
    input.type = 'password';
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.placeholder = PROVIDER[conn.provider].placeholder;
    block.appendChild(this._row(t('conn.key'), input));

    block.appendChild(this._note(t('conn.keyHint')));

    const row = document.createElement('div');
    row.className = 'conn-actions';
    const save = document.createElement('button');
    save.type = 'button';
    save.className = 'btn btn-primary btn-sm';
    save.textContent = t('conn.keySave');
    save.addEventListener('click', async () => {
      const value = input.value.trim();
      if (!value) { toast(t('conn.keyEmpty'), 'error'); return; }
      try {
        await api.llmSetKey(value, conn.provider);
        input.value = '';
        toast(t('conn.keyStored'), 'ok');
        await this.refresh();
      } catch (err) {
        toast(err?.message || t('error.network'), 'error');
      }
    });
    row.appendChild(save);

    if (conn.key_set) {
      const clear = document.createElement('button');
      clear.type = 'button';
      clear.className = 'btn btn-danger btn-sm';
      clear.textContent = t('conn.keyClear');
      clear.addEventListener('click', async () => {
        if (!window.confirm(t('conn.keyClearConfirm', { name: conn.label }))) return;
        try {
          await api.llmClearKey(conn.provider);
          toast(t('conn.keyCleared'), 'ok');
          await this.refresh();
        } catch (err) {
          toast(err?.message || t('error.network'), 'error');
        }
      });
      row.appendChild(clear);
    }
    block.appendChild(row);
    return block;
  }

  _modelBlock(conn, cfg) {
    const input = document.createElement('input');
    input.type = 'text';
    input.spellcheck = false;
    const key = PROVIDER[conn.provider].model;
    input.value = cfg[key] || '';

    if (conn.suggested_models?.length) {
      const listId = 'models-' + conn.provider;
      const datalist = document.createElement('datalist');
      datalist.id = listId;
      for (const m of conn.suggested_models) {
        const option = document.createElement('option');
        option.value = m;
        datalist.appendChild(option);
      }
      input.setAttribute('list', listId);
      input.after?.(datalist);
      const wrap = document.createElement('div');
      wrap.appendChild(this._row(t('conn.model'), input));
      wrap.appendChild(datalist);
      input.addEventListener('change', () => this._save({ [key]: input.value.trim() }));
      return wrap;
    }
    input.addEventListener('change', () => this._save({ [key]: input.value.trim() }));
    return this._row(t('conn.model'), input);
  }

  _effortBlock(cfg) {
    const select = document.createElement('select');
    for (const [value, label] of [
      ['low', t('conn.effortLow')],
      ['medium', t('conn.effortMedium')],
      ['high', t('conn.effortHigh')],
    ]) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      select.appendChild(option);
    }
    select.value = cfg.claude_effort || 'low';
    select.addEventListener('change', () => this._save({ claude_effort: select.value }));
    const wrap = document.createElement('div');
    wrap.appendChild(this._row(t('conn.effort'), select));
    wrap.appendChild(this._note(t('conn.effortHint')));
    return wrap;
  }

  async _test(provider, target) {
    target.textContent = t('conn.testing');
    target.className = 'conn-result';
    try {
      const info = await api.llmTest(provider);
      const ok = info.state === 'connected';
      target.className = 'conn-result ' + (ok ? 'is-ok' : 'is-bad');
      if (ok && info.model_ready) {
        target.textContent = t('conn.testOk', { model: info.model, count: info.models.length });
      } else if (ok) {
        target.textContent = t('conn.testNoModel', { model: info.model });
      } else {
        target.textContent = t('conn.testFail', {
          reason: info.detail || info.error || info.state,
        });
      }
    } catch (err) {
      target.className = 'conn-result is-bad';
      target.textContent = err?.message || t('error.network');
    }
  }

  _link(text, href) {
    const a = document.createElement('a');
    a.className = 'conn-link';
    a.href = href;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.textContent = text;
    return a;
  }

  _number(value, step, onChange) {
    const input = document.createElement('input');
    input.type = 'number';
    input.inputMode = 'decimal';
    input.min = '0';
    input.step = String(step);
    input.value = value ?? 0;
    input.addEventListener('change', () => onChange(Number(input.value) || 0));
    return input;
  }

  // --------------------------------------------------------------- routing

  /**
   * Who answers: one brain by hand, local only, cloud only, or automatic.
   * Every answer in the chat repeats the choice and the reason, so this card
   * is a setting, not a promise.
   */
  _routingCard(cfg) {
    const routing = this.data.routing || {};
    const card = this._card(t('conn.routeTitle'), 'idle', t('conn.route.' + (routing.mode || 'manual')));
    card.dataset.provider = 'routing';

    const select = document.createElement('select');
    select.dataset.setting = 'routing_mode';
    for (const mode of ['manual', 'local', 'cloud', 'auto']) {
      const option = document.createElement('option');
      option.value = mode;
      option.textContent = t('conn.route.' + mode);
      select.appendChild(option);
    }
    select.value = routing.mode || 'manual';
    select.addEventListener('change', async () => {
      if (await this._save({ routing_mode: select.value })) await this.refresh();
    });
    card.appendChild(this._row(t('conn.routeMode'), select));
    card.appendChild(this._note(t('conn.routeHint.' + (routing.mode || 'manual'))));

    // Cloud preference: which paid brain is asked first.
    const order = (routing.cloud_order || ['claude', 'openai', 'gemini']).slice();
    const first = document.createElement('select');
    first.dataset.setting = 'cloud_order';
    for (const p of ['claude', 'openai', 'gemini']) {
      const option = document.createElement('option');
      option.value = p;
      option.textContent = this._label(p);
      first.appendChild(option);
    }
    first.value = order[0];
    first.addEventListener('change', () => {
      const next = [first.value, ...order.filter((p) => p !== first.value)];
      this._save({ cloud_order: next });
    });
    card.appendChild(this._row(t('conn.routeFirst'), first));

    card.appendChild(this._switch(
      t('conn.routeFallback'), routing.auto_fallback !== false, false,
      (checked) => this._save({ auto_fallback: checked }),
    ));

    // Which brains could answer right now, and why not the others.
    const usable = this.data.usable || {};
    const list = document.createElement('ul');
    list.className = 'cost-prices route-usable';
    for (const p of ['ollama', 'claude', 'openai', 'gemini']) {
      const u = usable[p] || {};
      const li = document.createElement('li');
      li.className = u.ok ? 'is-ok' : '';
      li.textContent = `${this._label(p)}: ${u.ok ? t('conn.routeReady') : (u.reason || t('common.off'))}`;
      list.appendChild(li);
    }
    card.appendChild(list);
    return card;
  }

  _label(provider) {
    return { ollama: 'Ollama', openai: 'OpenAI', claude: 'Claude', gemini: 'Gemini' }[provider] || provider;
  }

  // ----------------------------------------------------------------- costs

  /**
   * What the paid brains have cost, and the limits that stop them. The
   * numbers are estimates from each provider's own token counts; the card
   * says so rather than presenting them as a bill.
   */
  _costsCard(cfg) {
    const u = this.data.usage || {};
    const limits = u.limits || {};
    const reached = (limits.daily && u.today_total >= limits.daily)
      || (limits.monthly && u.month_total >= limits.monthly);
    const card = this._card(
      t('conn.costsTitle'), reached ? 'error' : 'idle',
      t('conn.costsToday', { usd: (u.today_total || 0).toFixed(4) }),
    );
    card.dataset.provider = 'costs';

    const sums = document.createElement('ul');
    sums.className = 'cost-prices';
    for (const p of ['claude', 'openai', 'gemini']) {
      const li = document.createElement('li');
      li.textContent = t('conn.costsLine', {
        name: this._label(p),
        today: ((u.today || {})[p] || 0).toFixed(4),
        month: ((u.month || {})[p] || 0).toFixed(4),
      });
      sums.appendChild(li);
    }
    card.appendChild(sums);
    card.appendChild(this._note(t('conn.costsMonth', { usd: (u.month_total || 0).toFixed(4) })));
    if (reached) card.appendChild(this._note(t('conn.costsReached'), 'bad'));

    const limit = (key, label, step = 0.5) => card.appendChild(this._row(label,
      this._number(cfg[key] ?? 0, step, async (value) => {
        if (await this._save({ [key]: value })) await this.refresh();
      })));
    limit('cost_daily_limit_usd', t('conn.limitDaily'));
    limit('cost_monthly_limit_usd', t('conn.limitMonthly'), 1);
    for (const p of ['claude', 'openai', 'gemini']) {
      limit(`${p}_daily_limit_usd`, t('conn.limitProvider', { name: this._label(p) }));
    }
    card.appendChild(this._row(t('conn.maxOutput'), this._number(cfg.max_output_tokens || 4096, 256,
      (value) => this._save({ max_output_tokens: value }))));
    card.appendChild(this._note(t('conn.costsHint')));

    if ((u.recent || []).length) {
      const details = document.createElement('details');
      details.className = 'cost-recent';
      const summary = document.createElement('summary');
      summary.textContent = t('conn.costsRecent');
      details.appendChild(summary);
      const ul = document.createElement('ul');
      ul.className = 'cost-prices';
      for (const r of u.recent) {
        const li = document.createElement('li');
        const when = new Date(r.at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        li.textContent = `${when} · ${this._label(r.provider)} ${r.model} · ${r.input}/${r.output} tok · $${Number(r.usd).toFixed(4)}`
          + (r.estimated_price ? ' *' : '');
        ul.appendChild(li);
      }
      details.appendChild(ul);
      card.appendChild(details);
    }

    const links = document.createElement('div');
    links.className = 'conn-actions';
    for (const [p, url] of Object.entries(u.pricing_pages || {})) {
      links.appendChild(this._link(t('conn.pricingOf', { name: this._label(p) }), url));
    }
    card.appendChild(links);
    return card;
  }

  // --------------------------------------------------------------- OpenArt

  /**
   * OpenArt through its official hosted MCP server. Sign-in happens on
   * OpenArt's own page; every generation spends the account's credits and is
   * approved one at a time in the chat, with the spend named on the card.
   */
  _openartCard() {
    const info = this.data.openart || {};
    const auth = info.auth || {};
    const connected = info.state === 'connected';
    const card = this._card(
      'OpenArt',
      connected ? 'connected' : (info.state === 'error' ? 'error' : 'idle'),
      connected ? t('state.connected')
        : (info.configured ? (auth.signed_in ? t('conn.artSignedIn') : t('conn.artSignedOut')) : t('common.off')),
    );
    card.dataset.provider = 'openart';
    card.appendChild(this._note(t('conn.artAbout')));

    const box = document.createElement('div');
    box.className = 'cost-note';
    const title = document.createElement('strong');
    title.textContent = t('conn.artCostTitle');
    const body = document.createElement('p');
    body.textContent = t('conn.artCost');
    box.append(title, body);
    card.appendChild(box);

    const actions = document.createElement('div');
    actions.className = 'conn-actions';
    const button = (label, act, kind, fn) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = `btn ${kind} btn-sm`;
      b.dataset.act = act;
      b.textContent = label;
      b.addEventListener('click', async () => {
        b.disabled = true;
        try { await fn(); } catch (err) { toast(err?.message || t('error.network'), 'error'); }
        b.disabled = false;
      });
      actions.appendChild(b);
      return b;
    };

    if (!info.configured) {
      button(t('conn.artSetup'), 'art-setup', 'btn-primary', async () => {
        await api.openartSetup();
        await this.refresh();
      });
    } else {
      if (!auth.signed_in) {
        button(t('conn.artSignIn'), 'art-signin', 'btn-primary', async () => {
          // Opened in the user's own browser, on OpenArt's own page. The
          // provider redirects back to 127.0.0.1, where the backend finishes.
          const tab = window.open('about:blank', '_blank');
          let url;
          try {
            ({ authorize_url: url } = await api.oauthStart(info.id));
          } catch (err) {
            tab?.close();
            throw err;
          }
          if (tab) tab.location.href = url; else window.location.assign(url);
          this._watchSignIn(info.id);
        });
      } else if (!connected) {
        button(t('conn.artConnect'), 'art-connect', 'btn-primary', async () => {
          const result = await api.armoryConnect(info.id);
          if (result.state !== 'connected') toast(result.error || t('state.error'), 'error');
          await this.refresh();
        });
      } else {
        button(t('conn.artDisconnect'), 'art-disconnect', 'btn-ghost', async () => {
          await api.armoryDisconnect(info.id);
          await this.refresh();
        });
      }
      if (auth.signed_in) {
        button(t('conn.artSignOut'), 'art-signout', 'btn-danger', async () => {
          if (!window.confirm(t('conn.artSignOutConfirm'))) return;
          await api.oauthSignOut(info.id);
          await this.refresh();
        });
      }
    }
    card.appendChild(actions);

    if (info.error) card.appendChild(this._note(info.error, 'bad'));
    if (connected && (info.tools || []).length) {
      card.appendChild(this._note(t('conn.artTools', { tools: info.tools.join(', ') }), 'ok'));
    }
    card.appendChild(this._note(t('conn.artNoSub')));
    return card;
  }

  /**
   * After Sign in: poll until the callback has finished *and* the connection
   * that follows it has settled. Stopping at "signed in" would leave the card
   * showing a Connect button while the backend is already connecting.
   */
  _watchSignIn(connId) {
    let tries = 0;
    let signedFor = 0;
    clearInterval(this._signInPoll);
    this._signInPoll = setInterval(async () => {
      tries += 1;
      try {
        const info = await api.openartStatus();
        if (info.auth?.signed_in) signedFor += 1;
        const settled = info.state === 'connected' || info.state === 'error';
        if ((info.auth?.signed_in && (settled || signedFor > 5)) || tries > 90) {
          clearInterval(this._signInPoll);
          if (info.auth?.signed_in) toast(t('conn.artSignedIn'), 'ok');
          await this.refresh();
        }
      } catch {
        if (tries > 90) clearInterval(this._signInPoll);
      }
    }, 2000);
  }

  // ------------------------------------------------------------ Claude Code

  _codeCard(cfg) {
    const info = this.code || {};
    const card = this._card(
      t('conn.codeTitle'),
      info.ready ? 'connected' : (info.installed ? 'idle' : 'error'),
      info.ready ? t('conn.codeReady')
        : (info.installed ? t('common.off') : t('conn.codeUnavailable')),
    );
    card.dataset.provider = 'claude-code';

    card.appendChild(this._note(t('conn.codeAbout')));

    // --- what the platform actually supports ------------------------------
    if (info.note) card.appendChild(this._note(info.note, 'warn'));
    if (!info.installed) {
      card.appendChild(this._note(info.reason || t('conn.codeUnavailable'), 'bad'));
      // No switch at all when there is nothing to switch on: a toggle that
      // cannot do anything is exactly the decorative button to avoid.
      return card;
    }

    const found = document.createElement('p');
    found.className = 'conn-note is-ok';
    found.textContent = t('conn.codeFound', { version: info.version || '?', path: info.path });
    card.appendChild(found);

    // The CLI's own answer to "signed in, and how". JARVIS never sees the
    // credentials; it only repeats what `claude auth status` says.
    const auth = info.auth || {};
    if (auth.known) {
      card.appendChild(this._note(
        auth.logged_in ? t('conn.codeAuthYes', { method: auth.method || '?' }) : t('conn.codeAuthNo'),
        auth.logged_in ? 'ok' : 'bad',
      ));
    } else {
      card.appendChild(this._note(t('conn.codeAuthUnknown', { detail: auth.detail || '' }), 'warn'));
    }
    card.appendChild(this._note(t('conn.codeBilling')));

    card.appendChild(this._switch(
      t('conn.enable'), info.enabled, false,
      async (checked) => {
        if (await this._save({ claude_code_enabled: checked })) await this.refresh();
      },
    ));

    // --- the one directory it may touch -----------------------------------
    const dir = document.createElement('input');
    dir.type = 'text';
    dir.spellcheck = false;
    dir.placeholder = '/data/data/com.termux/files/home/projects';
    dir.value = cfg.claude_code_workdir || '';
    dir.addEventListener('change', async () => {
      if (await this._save({ claude_code_workdir: dir.value.trim() })) await this.refresh();
    });
    card.appendChild(this._row(t('conn.codeWorkdir'), dir));
    card.appendChild(this._note(t('conn.codeWorkdirHint')));
    if (info.enabled && !info.workdir_set) {
      card.appendChild(this._note(t('conn.codeNeedsWorkdir'), 'warn'));
    }

    // --- edits: the second gate -------------------------------------------
    card.appendChild(this._switch(
      t('conn.codeAllowEdits'), info.allow_edits, !info.enabled,
      async (checked) => {
        if (checked && !window.confirm(t('conn.codeEditsConfirm'))) { await this.refresh(); return; }
        if (await this._save({ claude_code_allow_edits: checked })) await this.refresh();
      },
    ));
    card.appendChild(this._note(t('conn.codeEditsHint'), info.allow_edits ? 'warn' : ''));

    // --- limits on every run ------------------------------------------------
    card.appendChild(this._row(t('conn.codeBudget'), this._number(cfg.claude_code_max_budget_usd ?? 1, 0.25,
      (value) => this._save({ claude_code_max_budget_usd: value }))));
    card.appendChild(this._row(t('conn.codeTurns'), this._number(cfg.claude_code_max_turns ?? 6, 1,
      (value) => this._save({ claude_code_max_turns: value }))));
    const tools = document.createElement('input');
    tools.type = 'text';
    tools.spellcheck = false;
    tools.placeholder = 'Bash(npm test)';
    tools.value = cfg.claude_code_allowed_tools || '';
    tools.addEventListener('change', () => this._save({ claude_code_allowed_tools: tools.value.trim() }));
    card.appendChild(this._row(t('conn.codeTools'), tools));
    card.appendChild(this._note(t('conn.codeToolsHint')));

    // --- ask for something ------------------------------------------------
    if (info.ready) card.appendChild(this._askBlock(info));
    card.appendChild(this._queue());
    return card;
  }

  _askBlock(info) {
    const block = document.createElement('div');
    block.className = 'conn-ask';

    const area = document.createElement('textarea');
    area.rows = 3;
    area.placeholder = t('conn.codeAskPlaceholder');
    block.appendChild(area);

    const mode = document.createElement('select');
    for (const [value, label] of [['plan', t('conn.codePlan')], ['edit', t('conn.codeEdit')]]) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      option.disabled = value === 'edit' && !info.allow_edits;
      mode.appendChild(option);
    }
    mode.value = 'plan';
    block.appendChild(this._row(t('conn.codeMode'), mode));

    const dir = document.createElement('input');
    dir.type = 'text';
    dir.spellcheck = false;
    dir.placeholder = t('conn.codeDirPlaceholder');
    block.appendChild(this._row(t('conn.codeDir'), dir));

    const ask = document.createElement('button');
    ask.type = 'button';
    ask.className = 'btn btn-primary btn-sm';
    ask.dataset.act = 'ask';
    ask.textContent = t('conn.codeAsk');
    ask.addEventListener('click', async () => {
      const prompt = area.value.trim();
      if (!prompt) { toast(t('conn.codeAskEmpty'), 'error'); return; }
      try {
        await api.codePropose(prompt, dir.value.trim(), mode.value);
        area.value = '';
        await this._loadRequests();
        toast(t('conn.codeProposed'), 'ok');
      } catch (err) {
        toast(err?.message || t('error.network'), 'error');
      }
    });
    block.appendChild(ask);
    return block;
  }

  _queue() {
    const box = document.createElement('div');
    box.className = 'conn-queue';
    box.id = 'code-queue';
    this._renderQueue(box, this.requests || []);
    return box;
  }

  async _loadRequests() {
    try {
      const data = await api.codeRequests();
      this.requests = data.requests || [];
    } catch {
      this.requests = [];
    }
    const box = document.getElementById('code-queue');
    if (box) this._renderQueue(box, this.requests);
  }

  /** Poll only while something is actually in flight. */
  _watchRequests() {
    this._stopRequestPoll();
    this._loadRequests();
    this.poll = setInterval(async () => {
      await this._loadRequests();
      const live = (this.requests || []).some((r) => r.state === 'running');
      if (!live && !(this.requests || []).some((r) => r.state === 'pending')) this._stopRequestPoll();
    }, 2500);
  }

  /** What an edit run changed: file list, the diff, and a way back. */
  _changes(req) {
    const ch = req.changes || {};
    const wrap = document.createElement('div');
    wrap.className = 'code-changes';
    const files = ch.files || [];
    wrap.appendChild(this._note(
      files.length ? t('conn.codeChanged', { count: files.length, files: files.join(', ') })
        : t('conn.codeNoChanges'),
      files.length ? 'warn' : '',
    ));
    if ((ch.already_dirty || []).length) {
      wrap.appendChild(this._note(t('conn.codeDirtyBefore', { files: ch.already_dirty.join(', ') })));
    }
    if (ch.diff) {
      const details = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = t('conn.codeDiff');
      const pre = document.createElement('pre');
      pre.className = 'code-out code-diff';
      pre.textContent = ch.diff;
      details.append(summary, pre);
      wrap.appendChild(details);
    }
    if (ch.reverted) {
      wrap.appendChild(this._note(t('conn.codeReverted'), 'ok'));
    } else if (ch.revertible) {
      const undo = document.createElement('button');
      undo.type = 'button';
      undo.className = 'btn btn-danger btn-sm';
      undo.dataset.act = 'revert';
      undo.textContent = t('conn.codeRevert');
      undo.addEventListener('click', async () => {
        if (!window.confirm(t('conn.codeRevertConfirm', { files: files.join(', ') }))) return;
        try { await api.codeRevert(req.id); await this._loadRequests(); }
        catch (err) { toast(err?.message || t('error.network'), 'error'); }
      });
      wrap.appendChild(undo);
    } else if (!ch.git && files.length) {
      wrap.appendChild(this._note(t('conn.codeNoGit')));
    }
    return wrap;
  }

  _renderQueue(box, requests) {
    box.innerHTML = '';
    if (!requests.length) return;
    const title = document.createElement('h4');
    title.textContent = t('conn.codeQueue');
    box.appendChild(title);

    for (const req of requests) {
      const item = document.createElement('div');
      item.className = 'code-req';
      item.dataset.state = req.state;

      const head = document.createElement('div');
      head.className = 'code-req-head';
      const mode = document.createElement('span');
      mode.className = 'code-mode';
      mode.dataset.mode = req.mode;
      mode.textContent = req.mode === 'edit' ? t('conn.codeEdit') : t('conn.codePlan');
      const st = document.createElement('span');
      st.className = 'code-state';
      st.textContent = t('conn.codeState.' + req.state);
      head.append(mode, st);
      item.appendChild(head);

      const prompt = document.createElement('p');
      prompt.className = 'code-prompt';
      prompt.textContent = req.prompt;
      item.appendChild(prompt);

      if (req.state === 'pending') {
        // Show the real command. Approving something you cannot see is not
        // approving it.
        const cmd = document.createElement('pre');
        cmd.className = 'code-cmd';
        cmd.textContent = req.argv.join(' ');
        item.appendChild(cmd);

        const dir = document.createElement('p');
        dir.className = 'conn-note';
        dir.textContent = t('conn.codeIn', { dir: req.directory });
        item.appendChild(dir);

        if (req.mode === 'edit') {
          item.appendChild(this._note(t('conn.codeEditWarn'), 'warn'));
        }

        const actions = document.createElement('div');
        actions.className = 'conn-actions';
        const run = document.createElement('button');
        run.type = 'button';
        run.className = 'btn btn-primary btn-sm';
        run.dataset.act = 'approve';
        run.textContent = t('conn.codeApprove');
        run.addEventListener('click', async () => {
          if (req.mode === 'edit' && !window.confirm(t('conn.codeApproveConfirm'))) return;
          try {
            await api.codeApprove(req.id, req.mode === 'edit');
            this._watchRequests();
          } catch (err) {
            toast(err?.message || t('error.network'), 'error');
          }
        });
        const drop = document.createElement('button');
        drop.type = 'button';
        drop.className = 'btn btn-ghost btn-sm';
        drop.dataset.act = 'reject';
        drop.textContent = t('conn.codeReject');
        drop.addEventListener('click', async () => {
          try { await api.codeCancel(req.id); await this._loadRequests(); }
          catch (err) { toast(err?.message || t('error.network'), 'error'); }
        });
        actions.append(run, drop);
        item.appendChild(actions);
      }

      if (req.origin === 'chat') item.appendChild(this._note(t('conn.codeFromChat')));

      if (req.state === 'running') {
        const stop = document.createElement('button');
        stop.type = 'button';
        stop.className = 'btn btn-danger btn-sm';
        stop.dataset.act = 'stop';
        stop.textContent = t('conn.codeStop');
        stop.addEventListener('click', async () => {
          try { await api.codeStop(req.id); await this._loadRequests(); }
          catch (err) { toast(err?.message || t('error.network'), 'error'); }
        });
        item.appendChild(stop);
      }
      if ((req.progress || []).length) {
        const steps = document.createElement('ol');
        steps.className = 'code-steps';
        for (const line of req.progress.slice(-8)) {
          const li = document.createElement('li');
          li.textContent = line;
          steps.appendChild(li);
        }
        item.appendChild(steps);
      }
      if (req.result) {
        const out = document.createElement('pre');
        out.className = 'code-out';
        out.textContent = req.result;
        item.appendChild(out);
      }
      if (req.cost_usd != null) {
        item.appendChild(this._note(t('conn.codeCost', { usd: Number(req.cost_usd).toFixed(4) })));
      }
      if (req.changes) item.appendChild(this._changes(req));
      if (req.state === 'done' && req.session_id) {
        const more = document.createElement('button');
        more.type = 'button';
        more.className = 'btn btn-ghost btn-sm';
        more.dataset.act = 'continue';
        more.textContent = t('conn.codeContinue');
        more.addEventListener('click', async () => {
          const next = window.prompt(t('conn.codeContinuePrompt'));
          if (!next || !next.trim()) return;
          const edit = !!(this.code && this.code.allow_edits);
          try {
            await api.codePropose(next.trim(), '', edit ? 'edit' : 'plan', req.session_id);
            await this._loadRequests();
            toast(t('conn.codeProposed'), 'ok');
          } catch (err) { toast(err?.message || t('error.network'), 'error'); }
        });
        item.appendChild(more);
      }
      if (req.error) {
        const out = document.createElement('pre');
        out.className = 'code-out is-bad';
        out.textContent = req.error;
        item.appendChild(out);
      }
      box.appendChild(item);
    }
  }
}

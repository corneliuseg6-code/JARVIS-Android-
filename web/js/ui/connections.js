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

const PAID = new Set(['openai', 'claude']);

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

  stop() {
    if (this.poll) { clearInterval(this.poll); this.poll = 0; }
  }

  render() {
    if (!this.host || !this.data) return;
    const cfg = state.get('config') || {};
    this.host.innerHTML = '';
    this.host.appendChild(this._routingCard(cfg));
    for (const conn of this.data.connections) {
      this.host.appendChild(this._providerCard(conn, cfg));
    }
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

  // ---------------------------------------------------------------- routing

  /**
   * How the brain is chosen for each turn.
   *
   * The line under the selector is the backend's own verdict — which provider
   * the next turn goes to and the rule that picked it — not a restatement of
   * the setting, so "Automatic" never hides that it is about to bill.
   */
  _routingCard(cfg) {
    const data = this.data;
    const mode = data.mode || cfg.llm_mode || 'manual';
    const label = (p) => (data.connections.find((c) => c.provider === p) || {}).label || p;
    const card = this._card(t('conn.routeTitle'), 'idle', t('conn.route_' + mode));

    const select = document.createElement('select');
    for (const value of ['manual', 'local', 'cloud', 'auto']) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = t('conn.route_' + value);
      option.selected = value === mode;
      select.appendChild(option);
    }
    select.addEventListener('change', async () => {
      if (await this._save({ llm_mode: select.value })) await this.refresh();
    });
    card.appendChild(this._row(t('conn.routeMode'), select));
    card.appendChild(this._note(t('conn.routeHint_' + mode)));

    const reason = data.route_reason || 'manual';
    const billed = PAID.has(data.active);
    card.appendChild(this._note(
      t('conn.routeNow', { provider: label(data.active) }) + ' ' + t('conn.reason_' + reason),
      // Amber whenever the next turn is billed or the mode could not be honoured.
      billed || reason.startsWith('no_') || reason.endsWith('_no_cloud') ? 'warn' : '',
    ));

    card.appendChild(this._switch(
      t('conn.cloudTools'), data.cloud_tools_enabled !== false, false,
      async (checked) => { if (await this._save({ cloud_tools_enabled: checked })) await this.refresh(); },
    ));
    card.appendChild(this._note(t('conn.cloudToolsHint')));
    return card;
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
          const patch = { [conn.provider === 'openai' ? 'openai_enabled' : 'claude_enabled']: checked };
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
    body.textContent = conn.provider === 'claude' ? t('conn.costClaude') : t('conn.costOpenai');
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

    const ackKey = conn.provider === 'openai' ? 'openai_cost_ack' : 'claude_cost_ack';
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
    input.placeholder = conn.provider === 'claude' ? 'sk-ant-…' : 'sk-…';
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
    input.value = conn.provider === 'openai' ? (cfg.openai_model || '') : (cfg.claude_model || '');
    const key = conn.provider === 'openai' ? 'openai_model' : 'claude_model';

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

    const ask = document.createElement('button');
    ask.type = 'button';
    ask.className = 'btn btn-primary btn-sm';
    ask.dataset.act = 'ask';
    ask.textContent = t('conn.codeAsk');
    ask.addEventListener('click', async () => {
      const prompt = area.value.trim();
      if (!prompt) { toast(t('conn.codeAskEmpty'), 'error'); return; }
      try {
        await api.codePropose(prompt, '', mode.value);
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
    this.stop();
    this._loadRequests();
    this.poll = setInterval(async () => {
      await this._loadRequests();
      const live = (this.requests || []).some((r) => r.state === 'running');
      if (!live && !(this.requests || []).some((r) => r.state === 'pending')) this.stop();
    }, 2500);
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

      if (req.result) {
        const out = document.createElement('pre');
        out.className = 'code-out';
        out.textContent = req.result;
        item.appendChild(out);
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

/**
 * Settings panel — built from the config the backend returns, so there is no
 * hardcoded user-facing string anywhere in it.
 *
 * Interface language and conversation language are separate settings; a switch
 * ties them together for people who want one control instead of two.
 */
import { api } from '../core/api.js';
import * as state from '../core/state.js';
import { LANGUAGES, t, getLanguage } from '../i18n/index.js';
import { toast } from './toast.js';

export default class Settings {
  constructor({ onLanguageChange, onConfigChange }) {
    this.host = document.getElementById('settings-body');
    this.savedFlag = document.getElementById('saved-flag');
    this.onLanguageChange = onLanguageChange;
    this.onConfigChange = onConfigChange;
    this.voices = null;
    this.pollers = new Map();
  }

  async refresh() {
    try {
      this.voices = await api.voices();
    } catch {
      this.voices = null;
    }
    try {
      this.llm = await api.llmStatus();
    } catch {
      this.llm = null;
    }
    this.render();
  }

  async _save(patch) {
    try {
      const config = await api.setConfig(patch);
      state.set({ config });
      this.onConfigChange?.(config);
      this._flash();
      return config;
    } catch {
      toast(t('error.network'), 'error');
      return null;
    }
  }

  _flash() {
    if (!this.savedFlag) return;
    this.savedFlag.hidden = false;
    clearTimeout(this._flashTimer);
    this._flashTimer = setTimeout(() => { this.savedFlag.hidden = true; }, 1400);
  }

  render() {
    if (!this.host) return;
    const cfg = state.get('config') || {};
    this.host.innerHTML = '';
    this.host.append(
      this._interfaceGroup(cfg),
      this._assistantGroup(cfg),
      this._shortcutsGroup(cfg),
      this._brainGroup(cfg),
      this._voiceGroup(cfg),
      this._modelsGroup(cfg),
      this._creditsGroup(),
      this._diagnosticsGroup(),
    );
  }

  // ---------------------------------------------------------------- groups
  _interfaceGroup(cfg) {
    const group = this._group(t('settings.interface'));

    const uiSelect = this._langSelect(cfg.ui_language || getLanguage());
    uiSelect.addEventListener('change', async () => {
      const code = uiSelect.value;
      this.onLanguageChange?.(code);
      const patch = { ui_language: code };
      if (cfg.conversation_follows_ui !== false) patch.conversation_language = code;
      await this._save(patch);
      this.render();
    });
    group.appendChild(this._field(t('settings.uiLanguage'), uiSelect));

    const follow = this._switch(t('settings.followUi'), cfg.conversation_follows_ui !== false, async (checked) => {
      const patch = { conversation_follows_ui: checked };
      if (checked) patch.conversation_language = cfg.ui_language;
      await this._save(patch);
      this.render();
    });
    group.appendChild(follow);

    if (cfg.conversation_follows_ui === false) {
      const convSelect = this._langSelect(cfg.conversation_language || cfg.ui_language);
      convSelect.addEventListener('change', async () => {
        await this._save({ conversation_language: convSelect.value });
        this.render();
      });
      group.appendChild(this._field(t('settings.conversationLanguage'), convSelect));
    }

    group.appendChild(this._switch(t('settings.reducedMotion'), !!cfg.reduced_motion, (checked) =>
      this._save({ reduced_motion: checked })));
    group.appendChild(this._switch(t('settings.magnifier'), cfg.magnifier_enabled !== false, (checked) =>
      this._save({ magnifier_enabled: checked })));
    return group;
  }

  /**
   * The two shortcuts on the top bar that are yours.
   *
   * OpenAI and GitHub are fixed; these two take any name and any http(s)
   * address. A bad address is rejected by the backend and the slot goes back
   * to its "+" state, so a typo never leaves a link pointing nowhere.
   */
  _shortcutsGroup(cfg) {
    const group = this._group(t('settings.shortcuts'));
    for (const n of [1, 2]) {
      const name = document.createElement('input');
      name.type = 'text';
      name.maxLength = 18;
      name.placeholder = t('sc.custom', { n });
      name.value = cfg[`shortcut${n}_name`] || '';
      name.addEventListener('change', () =>
        this._save({ [`shortcut${n}_name`]: name.value.trim() }));
      group.appendChild(this._field(t('settings.shortcutName', { n }), name));

      const url = document.createElement('input');
      url.type = 'url';
      url.inputMode = 'url';
      url.spellcheck = false;
      url.placeholder = 'https://…';
      url.value = cfg[`shortcut${n}_url`] || '';
      url.addEventListener('change', async () => {
        const typed = url.value.trim();
        const next = await this._save({ [`shortcut${n}_url`]: typed });
        // The backend drops anything that is not http(s); if it did, say so
        // rather than leaving the box looking accepted.
        if (typed && next && !next[`shortcut${n}_url`]) {
          url.value = '';
          toast(t('settings.shortcutBadUrl'), 'error', 5000);
        }
      });
      const field = this._field(t('settings.shortcutUrl', { n }), url);
      if (n === 2) {
        const hint = document.createElement('span');
        hint.className = 'hint';
        hint.textContent = t('settings.shortcutHint');
        field.appendChild(hint);
      }
      group.appendChild(field);
    }
    return group;
  }

  /** What REFLEX listens for, how long FOCUS runs, what the rings say. */
  _assistantGroup(cfg) {
    const group = this._group(t('settings.assistant'));

    const wake = document.createElement('input');
    wake.type = 'text';
    wake.value = cfg.wake_word || 'Jarvis';
    wake.addEventListener('change', () => this._save({ wake_word: wake.value.trim() }));
    const wakeField = this._field(t('settings.wakeWord'), wake);
    const hint = document.createElement('span');
    hint.className = 'hint';
    hint.textContent = t('settings.wakeWordHint');
    wakeField.appendChild(hint);
    group.appendChild(wakeField);

    const minutes = document.createElement('input');
    minutes.type = 'number';
    minutes.min = '1';
    minutes.max = '240';
    minutes.value = String(cfg.focus_minutes || 30);
    minutes.addEventListener('change', () => this._save({ focus_minutes: Number(minutes.value) }));
    group.appendChild(this._field(t('settings.focusMinutes'), minutes));

    const label = document.createElement('input');
    label.type = 'text';
    label.maxLength = 16;
    label.value = cfg.hud_label || 'J.A.R.V.I.S.';
    label.addEventListener('change', () => this._save({ hud_label: label.value.trim() }));
    group.appendChild(this._field(t('settings.hudLabel'), label));
    return group;
  }

  /**
   * Which brain answers, and everything that choice drags with it.
   *
   * Two rules are enforced here as well as on the backend: a paid provider
   * cannot be selected until its cost notice has been acknowledged, and it
   * cannot be selected without a key. The browser never learns the key — it
   * can set one and is told whether one exists.
   */
  _brainGroup(cfg) {
    const group = this._group(t('settings.brain'));
    const provider = cfg.llm_provider || 'ollama';
    const keySet = !!(this.llm && this.llm.openai_key_set);
    const acked = !!cfg.openai_cost_ack;

    const select = document.createElement('select');
    for (const [value, label] of [
      ['ollama', t('settings.providerOllama')],
      ['openai', t('settings.providerOpenai')],
    ]) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      option.selected = value === provider;
      select.appendChild(option);
    }
    select.addEventListener('change', async () => {
      if (select.value === 'openai') {
        if (!acked) { select.value = provider; this.render(); return; }
        if (!keySet) { toast(t('settings.needsKey'), 'error', 4000); select.value = provider; return; }
      }
      await this._save({ llm_provider: select.value });
      this.render();
    });
    group.appendChild(this._field(t('settings.provider'), select));

    // --- the cost notice, before anything can be billed -------------------
    if (provider === 'openai' || !acked) {
      const notice = document.createElement('div');
      notice.className = 'cost-note';
      const title = document.createElement('strong');
      title.textContent = t('settings.costTitle');
      const body = document.createElement('p');
      body.textContent = t('settings.costBody');
      notice.append(title, body);
      group.appendChild(notice);
      group.appendChild(this._switch(t('settings.costAck'), acked, async (checked) => {
        // Turning the acknowledgement off also stands the paid provider down.
        const patch = { openai_cost_ack: checked };
        if (!checked) patch.llm_provider = 'ollama';
        await this._save(patch);
        this.render();
      }));
    }

    // --- the key ----------------------------------------------------------
    const state = document.createElement('div');
    state.className = 'conn-meta';
    state.textContent = keySet ? t('settings.openaiKeySet') : t('settings.openaiKeyNone');
    group.appendChild(state);

    const keyInput = document.createElement('input');
    keyInput.type = 'password';
    keyInput.autocomplete = 'off';
    keyInput.placeholder = t('settings.openaiKeyPlaceholder');
    const keyField = this._field(t('settings.openaiKey'), keyInput);
    const hint = document.createElement('span');
    hint.className = 'hint';
    hint.textContent = t('settings.openaiKeyHint');
    keyField.appendChild(hint);
    group.appendChild(keyField);

    const actions = document.createElement('div');
    actions.className = 'conn-actions';
    const save = document.createElement('button');
    save.type = 'button';
    save.className = 'btn btn-sm btn-primary';
    save.textContent = t('common.save');
    save.addEventListener('click', async () => {
      const value = keyInput.value.trim();
      if (!value) return;
      save.disabled = true;
      try {
        await api.llmSetKey(value);
        keyInput.value = '';            // never keep it in the DOM
        await this.refresh();
        this._flash();
      } catch (err) {
        toast(err && err.message ? err.message : t('error.generic'), 'error', 5000);
        save.disabled = false;
      }
    });
    actions.appendChild(save);

    if (keySet) {
      const clear = document.createElement('button');
      clear.type = 'button';
      clear.className = 'btn btn-sm btn-danger';
      clear.textContent = t('common.delete');
      clear.addEventListener('click', async () => {
        await api.llmClearKey().catch(() => {});
        await this.refresh();
      });
      actions.appendChild(clear);
    }
    group.appendChild(actions);

    // --- model and endpoint -----------------------------------------------
    const model = document.createElement('input');
    model.type = 'text';
    model.setAttribute('list', 'openai-models');
    model.value = cfg.openai_model || '';
    model.addEventListener('change', () => this._save({ openai_model: model.value.trim() }));
    const list = document.createElement('datalist');
    list.id = 'openai-models';
    for (const name of (this.llm && this.llm.provider === 'openai' ? this.llm.models : []) || []) {
      const option = document.createElement('option');
      option.value = name;
      list.appendChild(option);
    }
    group.append(this._field(t('settings.openaiModel'), model), list);

    const base = document.createElement('input');
    base.type = 'text';
    base.value = cfg.openai_base_url || '';
    base.addEventListener('change', () => this._save({ openai_base_url: base.value.trim() }));
    group.appendChild(this._field(t('settings.openaiBaseUrl'), base));

    // Applies to both providers, but only costs money on one of them.
    const window_ = document.createElement('input');
    window_.type = 'number';
    window_.min = '2';
    window_.max = '100';
    window_.value = String(cfg.chat_history_limit || 20);
    window_.addEventListener('change', () =>
      this._save({ chat_history_limit: Number(window_.value) }));
    const windowField = this._field(t('settings.historyLimit'), window_);
    const windowHint = document.createElement('span');
    windowHint.className = 'hint';
    windowHint.textContent = t('settings.historyHint');
    windowField.appendChild(windowHint);
    group.appendChild(windowField);

    return group;
  }

  _voiceGroup(cfg) {
    const group = this._group(t('settings.voice'));

    group.appendChild(this._switch(t('settings.ttsEnabled'), cfg.tts_enabled !== false, (checked) =>
      this._save({ tts_enabled: checked })));

    const engine = this.voices?.engine;
    const engineLine = document.createElement('div');
    engineLine.className = 'conn-meta';
    engineLine.textContent = engine
      ? `${t('settings.voiceEngine')}: sherpa-onnx ${engine.version || '—'}${engine.available ? '' : ' · ' + (engine.error || '')}${engine.note ? ' · ' + engine.note : ''}`
      : `${t('settings.voiceEngine')}: —`;
    group.appendChild(engineLine);

    const catalog = this.voices?.catalog || {};
    for (const lang of LANGUAGES) {
      const entries = catalog[lang.code] || [];
      const row = document.createElement('div');
      row.className = 'voice-row';

      const tag = document.createElement('span');
      tag.className = 'voice-lang';
      tag.textContent = lang.short;

      const select = document.createElement('select');
      const auto = document.createElement('option');
      auto.value = '';
      auto.textContent = t('settings.voiceAuto');
      select.appendChild(auto);
      for (const voice of entries) {
        const option = document.createElement('option');
        option.value = voice.id;
        option.textContent = `${voice.label} (${voice.quality || '—'})`;
        option.selected = (cfg.voice_by_language || {})[lang.code] === voice.id;
        select.appendChild(option);
      }
      select.addEventListener('change', async () => {
        await this._save({ voice_by_language: { [lang.code]: select.value } });
      });

      // With "Automatic" selected, the badge must describe the voice that would
      // actually be used — the first installed one — not the first in the list.
      const current = entries.find((v) => v.id === select.value)
        || entries.find((v) => v.installed)
        || entries[0];
      const badge = document.createElement('span');
      badge.className = 'badge ' + (current?.installed ? 'badge-ok' : 'badge-missing');
      badge.textContent = current?.installed ? t('settings.voiceInstalled') : t('settings.voiceMissing');

      row.append(tag, select, badge);

      if (current && !current.installed) {
        const download = document.createElement('button');
        download.type = 'button';
        download.className = 'btn btn-sm btn-ghost';
        download.textContent = t('settings.download');
        download.addEventListener('click', () => this._download(current, download, badge));
        row.appendChild(download);
      }
      group.appendChild(row);
    }

    const speed = document.createElement('input');
    speed.type = 'range';
    speed.min = '0.6';
    speed.max = '1.6';
    speed.step = '0.1';
    speed.value = String(cfg.tts_speed || 1);
    speed.addEventListener('change', () => this._save({ tts_speed: Number(speed.value) }));
    group.appendChild(this._field(t('settings.speed'), speed));
    return group;
  }

  /** Voice downloads are always an explicit, confirmed, per-voice action. */
  async _download(voice, button, badge) {
    const size = voice.size_mb || '?';
    if (!window.confirm(t('settings.downloadConfirm', { size }))) return;
    button.disabled = true;
    button.textContent = t('settings.downloading');
    try {
      await api.downloadVoice(voice.id);
    } catch {
      button.disabled = false;
      button.textContent = t('settings.download');
      toast(t('error.network'), 'error');
      return;
    }
    const poll = setInterval(async () => {
      let status;
      try {
        status = await api.downloadStatus(voice.id);
      } catch {
        return;
      }
      if (status.state === 'running') {
        button.textContent = `${status.progress || 0}%`;
      } else if (status.state === 'done') {
        clearInterval(poll);
        this.pollers.delete(voice.id);
        badge.className = 'badge badge-ok';
        badge.textContent = t('settings.voiceInstalled');
        button.remove();
        toast(`${voice.label}: ${t('settings.voiceInstalled')}`, 'ok');
        this.refresh();
      } else if (status.state === 'error') {
        clearInterval(poll);
        this.pollers.delete(voice.id);
        button.disabled = false;
        button.textContent = t('settings.download');
        toast(status.error || t('error.generic'), 'error', 5000);
      }
    }, 1500);
    this.pollers.set(voice.id, poll);
  }

  _modelsGroup(cfg) {
    const group = this._group(t('settings.models'));

    const url = document.createElement('input');
    url.type = 'text';
    url.value = cfg.ollama_url || '';
    url.addEventListener('change', () => this._save({ ollama_url: url.value.trim() }));
    group.appendChild(this._field(t('settings.ollamaUrl'), url));

    const health = state.get('health');
    const installed = health?.ollama?.models || [];
    const chat = document.createElement('input');
    chat.type = 'text';
    chat.setAttribute('list', 'ollama-models');
    chat.value = cfg.chat_model || '';
    chat.addEventListener('change', () => this._save({ chat_model: chat.value.trim() }));
    const datalist = document.createElement('datalist');
    datalist.id = 'ollama-models';
    for (const model of installed) {
      const option = document.createElement('option');
      option.value = model;
      datalist.appendChild(option);
    }
    group.append(this._field(t('settings.chatModel'), chat), datalist);

    const vision = document.createElement('input');
    vision.type = 'text';
    vision.value = cfg.vision_model || '';
    vision.addEventListener('change', () => this._save({ vision_model: vision.value.trim() }));
    group.appendChild(this._field(t('settings.visionModel'), vision));

    group.appendChild(this._switch(t('settings.visionEnabled'), !!cfg.vision_enabled, (checked) =>
      this._save({ vision_enabled: checked })));
    group.appendChild(this._switch(t('eyes.saveCaptures'), !!cfg.save_captures, (checked) =>
      this._save({ save_captures: checked })));

    const prompt = document.createElement('textarea');
    prompt.rows = 3;
    prompt.value = cfg.system_prompt || '';
    prompt.addEventListener('change', () => this._save({ system_prompt: prompt.value }));
    group.appendChild(this._field(t('settings.systemPrompt'), prompt));
    return group;
  }

  /**
   * Attribution for the third-party assets JARVIS ships.
   *
   * The FACE bust is under CC BY 3.0, which requires the credit to be given
   * to users — so this group is not decoration and must not be removed from a
   * build. The full licence travels with the model itself.
   */
  _creditsGroup() {
    const group = this._group(t('settings.credits'));
    const line = document.createElement('p');
    line.className = 'panel-sub';
    line.textContent = t('settings.modelCredit');
    const link = document.createElement('a');
    link.className = 'btn btn-sm btn-ghost';
    link.href = '/models/head-LICENSE.txt';
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = t('settings.viewLicence');
    link.style.display = 'inline-block';
    link.style.marginTop = '6px';
    link.style.textDecoration = 'none';
    group.append(line, link);
    return group;
  }

  _diagnosticsGroup() {
    const group = this._group(t('settings.diagnostics'));
    const pre = document.createElement('pre');
    pre.className = 'diag';
    pre.textContent = '—';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-sm btn-ghost';
    button.textContent = t('status.refresh');
    button.addEventListener('click', async () => {
      try {
        pre.textContent = JSON.stringify(await api.diagnostics(), null, 2);
      } catch {
        pre.textContent = t('error.network');
      }
    });
    group.append(button, pre);
    return group;
  }

  // ---------------------------------------------------------------- helpers
  _group(title) {
    const group = document.createElement('div');
    group.className = 'settings-group';
    const heading = document.createElement('h3');
    heading.textContent = title;
    group.appendChild(heading);
    return group;
  }

  _field(labelText, control) {
    const wrap = document.createElement('div');
    wrap.className = 'field';
    const label = document.createElement('label');
    label.textContent = labelText;
    wrap.append(label, control);
    return wrap;
  }

  _switch(labelText, checked, handler) {
    const row = document.createElement('label');
    row.className = 'switch-row';
    const text = document.createElement('span');
    text.textContent = labelText;
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = !!checked;
    box.addEventListener('change', () => handler(box.checked));
    row.append(text, box);
    return row;
  }

  _langSelect(selected) {
    const select = document.createElement('select');
    for (const lang of LANGUAGES) {
      const option = document.createElement('option');
      option.value = lang.code;
      option.textContent = `${lang.short} — ${lang.label}`;
      option.selected = lang.code === selected;
      select.appendChild(option);
    }
    return select;
  }

  dispose() {
    for (const timer of this.pollers.values()) clearInterval(timer);
    this.pollers.clear();
  }
}

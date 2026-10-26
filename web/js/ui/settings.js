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
      this._voiceGroup(cfg),
      this._modelsGroup(cfg),
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

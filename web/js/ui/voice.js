/**
 * Speech output.
 *
 * JARVIS speaks with its own sherpa-onnx voice (the Mihai / Alan models) or
 * it does not speak. The device's own Web Speech voice is only used when the
 * user has explicitly turned that on, and when it is used it says so.
 *
 * It used to substitute the device voice automatically on any backend
 * failure. That is how JARVIS ended up speaking Romanian in a female voice
 * nobody had chosen and nobody could change: the only installed Romanian
 * voice is Mihai, who is male, so the voice being heard was Android's, not
 * JARVIS's — and nothing on screen said so.
 */
import * as state from '../core/state.js';
import { api } from '../core/api.js';
import { t } from '../i18n/index.js';
import { toast } from './toast.js';

const SPEECH_LOCALE = {
  ro: 'ro-RO', en: 'en-US', fr: 'fr-FR', de: 'de-DE', es: 'es-ES', it: 'it-IT',
};

export default class Voice {
  constructor({ onActivity }) {
    this.onActivity = onActivity;
    this.audio = null;
    this.objectUrl = '';
    this.warned = new Set();
    this.lastBackendFailure = '';
  }

  enabled() {
    const cfg = state.get('config') || {};
    return cfg.tts_enabled !== false;
  }

  async speak(text, language) {
    if (!text || !this.enabled()) return 'skipped';
    if (state.get('power') === 'standby') return 'standby';
    const lang = language || (state.get('config') || {}).conversation_language || 'en';

    this.stop();
    this.onActivity?.('speaking');
    try {
      const response = await api.tts(text, lang);
      const blob = await response.blob();
      await this._play(blob);
      return 'backend';
    } catch (err) {
      const detail = String(err && err.message ? err.message : err);
      this.lastBackendFailure = detail;
      const cfg = state.get('config') || {};

      // Say what went wrong, once per language, in the words the backend
      // used — "no female Romanian voice is installed" is actionable,
      // "voice unavailable" is not.
      if (!this.warned.has(lang)) {
        this.warned.add(lang);
        toast(this._explain(detail), 'error', 6000);
      }

      if (cfg.tts_browser_fallback) {
        const used = await this._browserFallback(text, lang);
        if (used) {
          // Never pass this off as JARVIS's own voice.
          if (!this.warned.has('device:' + lang)) {
            this.warned.add('device:' + lang);
            toast(t('error.voiceDevice', { voice: used }), 'info', 5000);
          }
          return 'browser';
        }
      }
      this.onActivity?.('idle');
      return 'none';
    }
  }

  /** Turn a backend error code into something worth reading. */
  _explain(detail) {
    const text = detail || '';
    if (/voice_gender_unavailable:?(.*)/.test(text)) {
      const have = (text.split('voice_gender_unavailable:')[1] || '').trim();
      return have
        ? t('error.voiceGenderOnly', { have })
        : t('error.voiceGenderNone');
    }
    if (text.includes('voice_unavailable')) {
      const which = (text.split('voice_unavailable:')[1] || '').trim();
      return t('error.voiceMissing', { voice: which || '?' });
    }
    if (text.includes('tts_failed')) {
      return t('error.voiceEngineFailed', {
        detail: (text.split('tts_failed:')[1] || '').trim().slice(0, 120),
      });
    }
    if (text.includes('standby')) return t('error.standby');
    return t('error.voiceUnavailable');
  }

  _play(blob) {
    return new Promise((resolve) => {
      this._revoke();
      this.objectUrl = URL.createObjectURL(blob);
      const audio = new Audio(this.objectUrl);
      this.audio = audio;
      const finish = () => {
        this.onActivity?.('idle');
        this._revoke();
        resolve();
      };
      audio.onended = finish;
      audio.onerror = finish;
      audio.play().catch(finish);
    });
  }

  /** Resolves to the device voice's name if it spoke, otherwise false. */
  _browserFallback(text, lang) {
    return new Promise((resolve) => {
      if (!('speechSynthesis' in window)) return resolve(false);
      const locale = SPEECH_LOCALE[lang] || 'en-US';
      const voices = window.speechSynthesis.getVoices() || [];
      const match = voices.find((v) => v.lang && v.lang.toLowerCase().startsWith(lang));
      if (!voices.length || !match) return resolve(false);

      const utter = new SpeechSynthesisUtterance(text.slice(0, 1200));
      utter.voice = match;
      utter.lang = match.lang || locale;
      utter.rate = Number((state.get('config') || {}).tts_speed) || 1;
      const name = match.name || match.lang || 'device';
      utter.onend = () => { this.onActivity?.('idle'); resolve(name); };
      utter.onerror = () => { this.onActivity?.('idle'); resolve(false); };
      try {
        window.speechSynthesis.speak(utter);
      } catch {
        return resolve(false);
      }
    });
  }

  stop() {
    if (this.audio) {
      try { this.audio.pause(); } catch { /* noop */ }
      this.audio = null;
    }
    this._revoke();
    if ('speechSynthesis' in window) {
      try { window.speechSynthesis.cancel(); } catch { /* noop */ }
    }
  }

  _revoke() {
    if (this.objectUrl) {
      URL.revokeObjectURL(this.objectUrl);
      this.objectUrl = '';
    }
  }

  /** Called when the conversation language changes. */
  resetWarnings() {
    this.warned.clear();
  }
}

/**
 * Speech output.
 *
 * Order of preference:
 *   1. backend sherpa-onnx voice (offline, the Mihai / Alan models)
 *   2. a browser Web Speech voice for the same language, if the device has one
 *   3. text only, with one honest notice per language
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
      this.lastBackendFailure = String(err && err.message ? err.message : err);
      const fell = await this._browserFallback(text, lang);
      if (fell) return 'browser';
      if (!this.warned.has(lang)) {
        this.warned.add(lang);
        toast(t('error.voiceUnavailable'), 'info', 4200);
      }
      this.onActivity?.('idle');
      return 'none';
    }
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
      utter.onend = () => { this.onActivity?.('idle'); resolve(true); };
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

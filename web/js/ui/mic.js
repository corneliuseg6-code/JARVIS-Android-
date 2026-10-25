/**
 * Microphone: explicit ON / MUTE, muted at every startup.
 *
 * MUTE is a real mute, not a flag:
 *   - speech recognition is aborted and its auto-restart disabled
 *   - every MediaStreamTrack is stopped (the OS recording indicator goes out)
 *   - the AudioContext is closed so no analyser keeps running
 * Nothing re-enables the microphone on its own — not resuming from standby,
 * not returning to the tab, not restarting the app.
 */
import { emit } from '../core/bus.js';
import * as state from '../core/state.js';
import { t } from '../i18n/index.js';
import { toast } from './toast.js';

const RECOGNITION_LOCALE = {
  ro: 'ro-RO', en: 'en-US', fr: 'fr-FR', de: 'de-DE', es: 'es-ES', it: 'it-IT',
};

export default class Mic {
  constructor({ onTranscript, onLevel }) {
    this.onTranscript = onTranscript;
    this.onLevel = onLevel;
    this.stream = null;
    this.audioCtx = null;
    this.analyser = null;
    this.recognition = null;
    this.wantListening = false;   // the user's intent, the only thing that restarts recognition
    this.levelTimer = 0;
    this.button = document.getElementById('btn-mic');
    this.label = document.getElementById('mic-label');

    this.button?.addEventListener('click', () => this.toggle());

    // Leaving the page releases the microphone: JARVIS must not listen in the
    // background while the user is in another app.
    document.addEventListener('visibilitychange', () => {
      if (document.hidden && this.isOn()) this.mute('hidden');
    });
    window.addEventListener('pagehide', () => this.mute('pagehide'));

    this.render();
  }

  isOn() {
    return state.get('micOn');
  }

  static supportsRecognition() {
    return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  }

  async toggle() {
    if (this.isOn()) this.mute('user');
    else await this.enable();
  }

  async enable() {
    if (state.get('power') === 'standby') {
      toast(t('error.standby'), 'error');
      return false;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      toast(t('mic.unsupported'), 'error');
      return false;
    }
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (err) {
      const denied = err && (err.name === 'NotAllowedError' || err.name === 'SecurityError');
      toast(denied ? t('mic.denied') : t('mic.unsupported'), 'error');
      return false;
    }

    state.set({ micOn: true });
    this.wantListening = true;
    this._startLevelMeter();
    this._startRecognition();
    this.render();
    emit('mic:on');
    toast(t('mic.statusOn'), 'ok', 1800);
    return true;
  }

  /** Full teardown. Safe to call repeatedly. */
  mute(reason = 'user') {
    const wasOn = this.isOn();
    this.wantListening = false;

    if (this.recognition) {
      try { this.recognition.onend = null; this.recognition.abort(); } catch { /* already stopped */ }
      this.recognition = null;
    }
    if (this.levelTimer) { clearInterval(this.levelTimer); this.levelTimer = 0; }
    if (this.stream) {
      this.stream.getTracks().forEach((track) => { try { track.stop(); } catch { /* noop */ } });
      this.stream = null;
    }
    if (this.audioCtx) {
      const ctx = this.audioCtx;
      this.audioCtx = null;
      this.analyser = null;
      ctx.close().catch(() => {});
    }
    this.onLevel?.(0);
    state.set({ micOn: false });
    this.render();
    if (wasOn) {
      emit('mic:off', { reason });
      if (reason === 'user') toast(t('mic.statusMuted'), 'info', 1600);
    }
  }

  _startLevelMeter() {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx || !this.stream) return;
    try {
      this.audioCtx = new Ctx();
      const source = this.audioCtx.createMediaStreamSource(this.stream);
      this.analyser = this.audioCtx.createAnalyser();
      this.analyser.fftSize = 512;
      this.analyser.smoothingTimeConstant = 0.75;
      source.connect(this.analyser);
      const data = new Uint8Array(this.analyser.frequencyBinCount);
      // 20Hz is plenty to drive an animation and costs far less than rAF.
      this.levelTimer = setInterval(() => {
        if (!this.analyser) return;
        this.analyser.getByteFrequencyData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) sum += data[i];
        this.onLevel?.(Math.min(1, sum / data.length / 90));
      }, 50);
    } catch (err) {
      console.warn('[mic] level meter unavailable', err);
    }
  }

  _startRecognition() {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      toast(t('mic.unsupported'), 'info', 3000);
      return;
    }
    const cfg = state.get('config') || {};
    const recognition = new Recognition();
    recognition.lang = RECOGNITION_LOCALE[cfg.conversation_language] || 'en-US';
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    recognition.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (!result.isFinal) continue;
        const text = (result[0]?.transcript || '').trim();
        if (text) this.onTranscript?.(text);
      }
    };
    recognition.onerror = (event) => {
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        toast(t('mic.denied'), 'error');
        this.mute('denied');
      } else if (event.error === 'audio-capture') {
        toast(t('mic.unsupported'), 'error');
        this.mute('no-device');
      }
      // 'no-speech' / 'aborted' are normal; onend decides whether to restart.
    };
    recognition.onend = () => {
      // Restart only while the user still wants to listen. This is the guard
      // that keeps a muted microphone muted.
      if (this.wantListening && this.isOn()) {
        try { recognition.start(); } catch { /* already starting */ }
      }
    };

    try {
      recognition.start();
      this.recognition = recognition;
    } catch (err) {
      console.warn('[mic] recognition failed to start', err);
    }
  }

  /** Re-apply the recognition language after a language change. */
  refreshLanguage() {
    if (!this.isOn() || !this.recognition) return;
    const cfg = state.get('config') || {};
    const locale = RECOGNITION_LOCALE[cfg.conversation_language] || 'en-US';
    if (this.recognition.lang === locale) return;
    const recognition = this.recognition;
    this.recognition = null;
    try { recognition.onend = null; recognition.abort(); } catch { /* noop */ }
    this._startRecognition();
  }

  render() {
    if (!this.button) return;
    const on = this.isOn();
    this.button.classList.toggle('is-on', on);
    this.button.classList.toggle('is-muted', !on);
    this.button.setAttribute('aria-pressed', String(on));
    this.button.setAttribute('aria-label', on ? t('mic.statusOn') : t('mic.statusMuted'));
    if (this.label) {
      this.label.textContent = on ? t('mic.on') : t('mic.mute');
      this.label.removeAttribute('data-i18n');  // text is managed here
    }
  }
}

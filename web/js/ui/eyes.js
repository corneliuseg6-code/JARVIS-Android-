/**
 * EYES — camera + local vision.
 *
 * Privacy rules enforced here:
 *   - the camera starts only on an explicit tap
 *   - every track is stopped when it is turned off, when the page is hidden,
 *     when JARVIS goes to standby and when the view changes
 *   - frames are sent to the local backend only, and only when the user asks
 *     for a description
 */
import { api } from '../core/api.js';
import * as state from '../core/state.js';
import { t, tLLMError } from '../i18n/index.js';
import { toast } from './toast.js';

export default class Eyes {
  constructor() {
    this.video = document.getElementById('cam-video');
    this.indicator = document.getElementById('cam-indicator');
    this.note = document.getElementById('cam-note');
    this.result = document.getElementById('cam-result');
    this.startBtn = document.getElementById('btn-cam-start');
    this.stopBtn = document.getElementById('btn-cam-stop');
    this.switchBtn = document.getElementById('btn-cam-switch');
    this.describeBtn = document.getElementById('btn-cam-describe');
    this.stream = null;
    this.facing = 'environment';

    this.startBtn?.addEventListener('click', () => this.start());
    this.stopBtn?.addEventListener('click', () => this.stop('user'));
    this.switchBtn?.addEventListener('click', () => this.switchCamera());
    this.describeBtn?.addEventListener('click', () => this.describe());

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) this.stop('hidden');
    });
    window.addEventListener('pagehide', () => this.stop('pagehide'));
    this.render();
  }

  isOn() {
    return !!this.stream;
  }

  async start() {
    if (state.get('power') === 'standby') {
      toast(t('error.standby'), 'error');
      return;
    }
    if (!window.isSecureContext && !/^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname)) {
      this._note(t('eyes.insecure'));
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      this._note(t('eyes.noCamera'));
      return;
    }
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: this.facing, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,                       // EYES never opens the microphone
      });
    } catch (err) {
      const denied = err && (err.name === 'NotAllowedError' || err.name === 'SecurityError');
      this._note(denied ? t('eyes.permissionDenied') : t('eyes.noCamera'));
      return;
    }
    if (this.video) {
      this.video.srcObject = this.stream;
      try { await this.video.play(); } catch { /* autoplay guard */ }
    }
    state.set({ cameraOn: true });
    this._note('');
    this.render();
    await this._checkVision();
  }

  stop(reason = 'user') {
    if (this.stream) {
      this.stream.getTracks().forEach((track) => { try { track.stop(); } catch { /* noop */ } });
      this.stream = null;
    }
    if (this.video) this.video.srcObject = null;
    state.set({ cameraOn: false });
    this.render();
    if (reason === 'user') this._note('');
  }

  async switchCamera() {
    if (!this.isOn()) return;
    this.facing = this.facing === 'environment' ? 'user' : 'environment';
    this.stop('switch');
    await this.start();
  }

  async _checkVision() {
    try {
      const status = await api.visionStatus();
      if (!status.enabled) this._note(t('eyes.visionDisabled'));
      else if (!status.model_installed) this._note(t('eyes.modelMissing') + (status.note ? ' — ' + status.note : ''));
      else this._note('');
      if (this.describeBtn) this.describeBtn.disabled = !status.enabled || !status.model_installed;
    } catch {
      this._note(t('error.network'));
    }
  }

  _frame() {
    if (!this.video || !this.video.videoWidth) return '';
    const maxSide = 768;                    // keep the upload small: it is a phone
    const scale = Math.min(1, maxSide / Math.max(this.video.videoWidth, this.video.videoHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(this.video.videoWidth * scale);
    canvas.height = Math.round(this.video.videoHeight * scale);
    canvas.getContext('2d').drawImage(this.video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.75);
  }

  async describe() {
    if (!this.isOn()) return;
    const image = this._frame();
    if (!image) return;
    const language = (state.get('config') || {}).conversation_language || 'en';
    if (this.result) {
      this.result.hidden = false;
      this.result.textContent = t('eyes.describing');
    }
    if (this.describeBtn) this.describeBtn.disabled = true;
    try {
      const data = await api.visionDescribe(image, language, '');
      if (this.result) {
        this.result.textContent = data.ok
          ? data.text
          : (data.error === 'model_missing' ? t('eyes.modelMissing') + (data.detail ? ' — ' + data.detail : '')
            : data.error === 'vision_disabled' ? t('eyes.visionDisabled')
            // Vision goes through the same provider as chat, so it fails in
            // the same ways — a rejected key, no credit, a bad model name.
            : tLLMError(data.error, data.detail));
      }
    } catch (err) {
      if (this.result) this.result.textContent = t('error.network');
    }
    if (this.describeBtn) this.describeBtn.disabled = false;
  }

  _note(text) {
    if (this.note) this.note.textContent = text || '';
  }

  render() {
    const on = this.isOn();
    document.getElementById('app')?.setAttribute('data-camera', on ? 'on' : 'off');
    if (this.indicator) this.indicator.hidden = !on;
    if (this.startBtn) this.startBtn.hidden = on;
    if (this.stopBtn) this.stopBtn.hidden = !on;
    if (this.switchBtn) this.switchBtn.hidden = !on;
    if (this.describeBtn) this.describeBtn.hidden = !on;
    if (!on && this.result) this.result.hidden = true;
  }
}

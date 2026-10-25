/**
 * ACTIVE / STANDBY.
 *
 * STANDBY stops everything that costs power or privacy — microphone, speech,
 * camera, animation loop, health polling — while the page and the local server
 * keep running, so conversations and settings survive untouched.
 *
 * EXIT is a separate control (see app.js): standby pauses, exit closes.
 */
import { emit } from '../core/bus.js';
import * as state from '../core/state.js';
import { api } from '../core/api.js';
import { t } from '../i18n/index.js';

export default class Power {
  constructor({ avatar, mic, voice, eyes }) {
    this.avatar = avatar;
    this.mic = mic;
    this.voice = voice;
    this.eyes = eyes;
    this.button = document.getElementById('btn-power');
    this.label = document.getElementById('power-label');
    this.veil = document.getElementById('standby-veil');
    this.resumeBtn = document.getElementById('btn-resume');

    this.button?.addEventListener('click', () => this.toggle());
    this.resumeBtn?.addEventListener('click', () => this.setState('active'));
    this.render();
  }

  toggle() {
    this.setState(state.get('power') === 'active' ? 'standby' : 'active');
  }

  setState(next) {
    if (next === state.get('power')) return;
    if (next === 'standby') this._enterStandby();
    else this._leaveStandby();
    api.setPower(next).catch(() => { /* the UI state is authoritative locally */ });
    this.render();
    emit('power:' + next);
  }

  _enterStandby() {
    this.mic?.mute('standby');          // microphone off, tracks released
    this.voice?.stop();                 // stop any speech in progress
    this.eyes?.stop('standby');         // camera off
    this.avatar?.stop();                // animation loop stopped, canvas cleared
    state.set({ power: 'standby', activity: 'idle' });
  }

  _leaveStandby() {
    state.set({ power: 'active' });
    this.avatar?.start();
    // Deliberately NOT re-enabling the microphone: resuming never reopens it.
  }

  render() {
    const standby = state.get('power') === 'standby';
    document.getElementById('app')?.setAttribute('data-power', standby ? 'standby' : 'active');
    if (this.veil) this.veil.hidden = !standby;
    if (this.button) {
      this.button.classList.toggle('is-standby', standby);
      this.button.classList.toggle('is-active', !standby);
      this.button.setAttribute('aria-pressed', String(!standby));
    }
    if (this.label) {
      this.label.textContent = standby ? t('power.standby') : t('power.active');
      this.label.removeAttribute('data-i18n');
    }
  }
}

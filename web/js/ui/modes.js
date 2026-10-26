/**
 * The five function switches under the visualiser: EYES, WATCH, HOLO, FOCUS,
 * REFLEX.
 *
 * Each one reports honestly. A switch that cannot do its job on this device is
 * drawn dimmed, says why in its own label, and refuses to pretend it is on —
 * the alternative, a switch that lights up and does nothing, is worse than not
 * having it.
 *
 * What each one actually does on Android:
 *
 *   EYES    the camera, through the existing Eyes panel.            works
 *   WATCH   screen capture via getDisplayMedia.                     desktop only
 *   HOLO    tilt- and drag-driven parallax on the visualiser.       works
 *   FOCUS   a timed session that counts how often you leave JARVIS. works
 *   REFLEX  hands-free: the mic stays open, only the wake word acts. works
 *
 * WATCH is the one Android Chrome has no API for. It is detected, not guessed.
 */
import { api } from '../core/api.js';
import * as state from '../core/state.js';
import { emit } from '../core/bus.js';
import { t } from '../i18n/index.js';
import { toast } from './toast.js';

const FUNCTIONS = ['eyes', 'watch', 'holo', 'focus', 'reflex'];

/** Strip punctuation and accents so "Jarvis," and "jarvis" both match. */
function normalise(text) {
  return String(text || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9 ]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export default class Modes {
  constructor({ avatar, eyes, mic, voice, onOpenView, onNote }) {
    this.avatar = avatar;
    this.eyes = eyes;
    this.mic = mic;
    this.voice = voice;
    this.onOpenView = onOpenView;
    this.onNote = onNote;

    this.on = { eyes: false, watch: false, holo: false, focus: false, reflex: false };
    this.buttons = {};

    // WATCH
    this.screenStream = null;
    this.screenVideo = null;

    // HOLO
    this.tilt = { x: 0, y: 0 };
    this.drag = null;
    this._onOrientation = (event) => this._orientation(event);
    this._onPointerDown = (event) => this._pointerDown(event);
    this._onPointerMove = (event) => this._pointerMove(event);
    this._onPointerUp = () => { this.drag = null; };

    // FOCUS
    this.focus = null;
    this.focusTimer = 0;
    this.focusNoteTimer = 0;
    this.focusBlock = document.getElementById('focus-block');
    this.focusTimeEl = document.getElementById('focus-time');
    this.focusDriftEl = document.getElementById('focus-drift');
    this.focusPauseBtn = document.getElementById('btn-focus-pause');
    this.focusPauseBtn?.addEventListener('click', () => this.toggleFocusPause());
    document.getElementById('btn-focus-stop')
      ?.addEventListener('click', () => this.set('focus', false, 'user'));

    for (const name of FUNCTIONS) {
      const button = document.getElementById('fn-' + name);
      if (!button) continue;
      this.buttons[name] = button;
      button.addEventListener('click', () => this.toggle(name));
    }

    // Leaving the page is the drift signal FOCUS counts, and the moment every
    // capture has to stop.
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        this._driftStart();
        this.set('watch', false, 'hidden');
      } else {
        this._driftEnd();
      }
    });
    window.addEventListener('pagehide', () => this.stopAll('pagehide'));

    this.render();
  }

  // ------------------------------------------------------------------ public

  isOn(name) {
    return !!this.on[name];
  }

  toggle(name) {
    this.set(name, !this.on[name], 'user');
  }

  set(name, next, reason = 'user') {
    if (!FUNCTIONS.includes(name)) return;
    if (!!this.on[name] === !!next) return;
    if (next && state.get('power') === 'standby') {
      toast(t('error.standby'), 'error');
      return;
    }
    const handler = this['_' + name];
    Promise.resolve(handler.call(this, next, reason))
      .then((applied) => {
        this.on[name] = applied === undefined ? next : !!applied;
        this.render();
        emit('fn:' + name, this.on[name]);
      })
      .catch((err) => {
        console.error('[modes] %s failed', name, err);
        this.on[name] = false;
        this.render();
      });
  }

  /** Everything off — used by standby, exit and page hide. */
  stopAll(reason = 'standby') {
    for (const name of FUNCTIONS) this.set(name, false, reason);
  }

  /**
   * REFLEX gate. Returns the text to act on, or null to ignore the utterance.
   * With REFLEX off every transcript is acted on, exactly as before.
   */
  filterTranscript(text) {
    if (!this.on.reflex) return text;
    const words = normalise(text);
    const wake = normalise(this._wakeWord());
    if (!wake || !words.includes(wake)) return null;
    // Drop the wake word and anything before it: "Jarvis, what time is it"
    // should reach the model as "what time is it".
    const at = words.indexOf(wake);
    const rest = words.slice(at + wake.length).trim();
    // Use the original casing where we can, falling back to the stripped text.
    const index = String(text).toLowerCase().lastIndexOf(wake.split(' ')[0]);
    const tail = index >= 0 ? String(text).slice(index).replace(/^[^\s]+[\s,.:;!?-]*/, '').trim() : '';
    return tail || rest || text;
  }

  _wakeWord() {
    const cfg = state.get('config') || {};
    return (cfg.wake_word || 'jarvis').trim();
  }

  // ------------------------------------------------------------------ EYES

  async _eyes(next) {
    if (!next) {
      this.eyes?.stop('fn');
      return false;
    }
    // The camera lives in the Eyes panel; open it so the preview and the
    // Describe button are actually reachable.
    this.onOpenView?.('eyes');
    await this.eyes?.start();
    const on = !!this.eyes?.isOn();
    if (!on) this._note(t('eyes.noCamera'), true);
    return on;
  }

  // ------------------------------------------------------------------ WATCH

  watchSupported() {
    return typeof navigator.mediaDevices?.getDisplayMedia === 'function';
  }

  async _watch(next, reason) {
    if (!next) {
      if (this.screenStream) {
        this.screenStream.getTracks().forEach((track) => { try { track.stop(); } catch { /* noop */ } });
        this.screenStream = null;
      }
      if (this.screenVideo) {
        this.screenVideo.srcObject = null;
        this.screenVideo.remove();
        this.screenVideo = null;
      }
      if (reason === 'user') this._note('');
      return false;
    }

    if (!this.watchSupported()) {
      // Android Chrome has no screen-capture API for a web page. Saying so is
      // the whole point of this branch.
      this._note(t('fn.watchUnsupported'), true);
      toast(t('fn.watchUnsupported'), 'info', 5000);
      return false;
    }

    try {
      this.screenStream = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: { ideal: 2, max: 5 } },
        audio: false,
      });
    } catch (err) {
      const denied = err && (err.name === 'NotAllowedError' || err.name === 'SecurityError');
      this._note(denied ? t('fn.watchDenied') : t('fn.watchUnsupported'), true);
      return false;
    }

    const video = document.createElement('video');
    video.playsInline = true;
    video.muted = true;
    video.style.display = 'none';
    document.body.appendChild(video);
    video.srcObject = this.screenStream;
    try { await video.play(); } catch { /* autoplay guard */ }
    this.screenVideo = video;

    // The user can stop sharing from the browser's own bar; follow that.
    this.screenStream.getVideoTracks().forEach((track) => {
      track.addEventListener('ended', () => this.set('watch', false, 'ended'));
    });

    this._note(t('fn.watchOn'));
    return true;
  }

  /** A still of the shared screen, for the vision endpoint. */
  screenFrame() {
    const video = this.screenVideo;
    if (!video || !video.videoWidth) return '';
    const maxSide = 1024;
    const scale = Math.min(1, maxSide / Math.max(video.videoWidth, video.videoHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.7);
  }

  /** Ask the local vision model what is on the shared screen. */
  async describeScreen() {
    const image = this.screenFrame();
    if (!image) return null;
    const language = (state.get('config') || {}).conversation_language || 'en';
    try {
      return await api.visionDescribe(image, language, t('fn.watchPrompt'));
    } catch {
      return null;
    }
  }

  // ------------------------------------------------------------------ HOLO

  _holo(next) {
    const stage = document.getElementById('stage');
    if (!next) {
      window.removeEventListener('deviceorientation', this._onOrientation);
      stage?.removeEventListener('pointerdown', this._onPointerDown);
      window.removeEventListener('pointermove', this._onPointerMove);
      window.removeEventListener('pointerup', this._onPointerUp);
      window.removeEventListener('pointercancel', this._onPointerUp);
      this.tilt = { x: 0, y: 0 };
      this.avatar?.setTilt(0, 0);
      this._note('');
      return false;
    }

    // Drag always works. Device tilt is a bonus where the sensor exists.
    stage?.addEventListener('pointerdown', this._onPointerDown);
    window.addEventListener('pointermove', this._onPointerMove, { passive: true });
    window.addEventListener('pointerup', this._onPointerUp);
    window.addEventListener('pointercancel', this._onPointerUp);
    if (window.DeviceOrientationEvent) {
      window.addEventListener('deviceorientation', this._onOrientation, { passive: true });
    }
    this._note(t('fn.holoHint'));
    return true;
  }

  _orientation(event) {
    if (!this.on.holo || this.drag) return;
    // A device with no orientation sensor still fires the event, with null
    // readings. Treating those as zero would snap the projection sideways.
    if (event.beta === null && event.gamma === null) return;
    // gamma: left/right tilt, beta: front/back, measured against a phone held
    // at about 40° — the angle it is actually read at. Clamped so lying it
    // flat on a table does not throw the projection to a limit.
    const gamma = Math.max(-45, Math.min(45, event.gamma || 0));
    const beta = Math.max(-45, Math.min(45, (event.beta || 0) - 40));
    const targetX = (gamma / 45) * 0.6;
    const targetY = (beta / 45) * 0.35;
    // Ease towards the reading: raw sensor values jitter enough to be visible.
    this.tilt.x += (targetX - this.tilt.x) * 0.15;
    this.tilt.y += (targetY - this.tilt.y) * 0.15;
    this.avatar?.setTilt(this.tilt.x, this.tilt.y);
  }

  _pointerDown(event) {
    if (!this.on.holo) return;
    this.drag = { x: event.clientX, y: event.clientY, tx: this.tilt.x, ty: this.tilt.y };
  }

  _pointerMove(event) {
    if (!this.on.holo || !this.drag) return;
    const dx = (event.clientX - this.drag.x) / 160;
    const dy = (event.clientY - this.drag.y) / 220;
    this.tilt.x = Math.max(-1.1, Math.min(1.1, this.drag.tx + dx));
    this.tilt.y = Math.max(-0.6, Math.min(0.6, this.drag.ty + dy));
    this.avatar?.setTilt(this.tilt.x, this.tilt.y);
  }

  // ------------------------------------------------------------------ FOCUS

  _focus(next, reason) {
    if (!next) {
      const session = this.focus;
      this.focus = null;
      if (this.focusTimer) { clearInterval(this.focusTimer); this.focusTimer = 0; }
      if (this.focusBlock) this.focusBlock.hidden = true;
      document.getElementById('app')?.removeAttribute('data-focus');
      if (session && reason !== 'standby' && reason !== 'pagehide') {
        const paused = session.paused + (session.pausedAt ? Date.now() - session.pausedAt : 0);
        const minutes = Math.round((Date.now() - session.start - paused) / 60000);
        const line = t('fn.focusDone', { minutes, drifts: session.drifts });
        this._note(line);
        // The summary is news, not a permanent caption: it sits over the
        // visualiser, so take it away once it has been read.
        clearTimeout(this.focusNoteTimer);
        this.focusNoteTimer = setTimeout(() => {
          const note = document.getElementById('stage-note');
          if (!note || note.textContent === line) this._note('');
        }, 8000);
        this.voice?.speak(line, (state.get('config') || {}).conversation_language);
      } else {
        this._note('');
      }
      return false;
    }

    const cfg = state.get('config') || {};
    const minutes = Math.max(1, Math.min(240, Number(cfg.focus_minutes) || 30));
    this.focus = {
      start: Date.now(),
      until: Date.now() + minutes * 60000,
      drifts: 0,
      away: 0,
      pausedAt: 0,
      paused: 0,
      noteUntil: 0,
    };
    clearTimeout(this.focusNoteTimer);
    this._note('');
    this._focusPauseUI(false);
    this._tickFocus();
    this.focusTimer = setInterval(() => this._tickFocus(), 1000);
    const line = t('fn.focusOn', { minutes });
    this.voice?.speak(line, cfg.conversation_language);
    return true;
  }

  /**
   * Pause the clock without ending the session.
   *
   * The deadline is pushed out by however long the pause lasted, so the
   * session still runs for the full time it was asked for.
   */
  toggleFocusPause() {
    if (!this.focus) return;
    if (this.focus.pausedAt) {
      const held = Date.now() - this.focus.pausedAt;
      this.focus.until += held;
      this.focus.paused += held;
      this.focus.pausedAt = 0;
    } else {
      this.focus.pausedAt = Date.now();
      this.focus.away = 0;
    }
    this._focusPauseUI(!!this.focus.pausedAt);
    this._tickFocus();
  }

  _focusPauseUI(held) {
    document.getElementById('app')?.setAttribute('data-focus', held ? 'paused' : 'running');
    if (this.focusPauseBtn) {
      this.focusPauseBtn.textContent = held ? '▶' : '❚❚';
      const key = held ? 'fn.focusResume' : 'fn.focusPause';
      this.focusPauseBtn.dataset.i18nAria = key;
      this.focusPauseBtn.dataset.i18nTitle = key;
      this.focusPauseBtn.setAttribute('aria-label', t(key));
      this.focusPauseBtn.title = t(key);
    }
  }

  _tickFocus() {
    if (!this.focus) return;
    const now = this.focus.pausedAt || Date.now();
    const left = Math.max(0, this.focus.until - now);
    if (left <= 0) {
      this.set('focus', false, 'done');
      return;
    }
    const mm = String(Math.floor(left / 60000)).padStart(2, '0');
    const ss = String(Math.floor((left % 60000) / 1000)).padStart(2, '0');
    this._setState('focus', `${mm}:${ss}`);
    if (this.focusTimeEl) this.focusTimeEl.textContent = `${mm}:${ss}`;
    if (this.focusDriftEl) {
      this.focusDriftEl.textContent = this.focus.drifts
        ? t('fn.focusDrifts', { drifts: this.focus.drifts })
        : '';
    }
    if (this.focusBlock) this.focusBlock.hidden = false;
    // The block above the dial carries the clock, so the note under it is only
    // for the drift message — and only for as long as it is news.
    if (this.focus.noteUntil && Date.now() > this.focus.noteUntil) {
      this.focus.noteUntil = 0;
      this._note('');
    }
  }

  _driftStart() {
    // A pause is a deliberate break, so looking away during one is not a drift.
    if (!this.focus || this.focus.pausedAt) return;
    this.focus.away = Date.now();
  }

  _driftEnd() {
    if (!this.focus || !this.focus.away || this.focus.pausedAt) return;
    // Under two seconds is a notification or a rotation, not a distraction.
    const gone = Date.now() - this.focus.away;
    this.focus.away = 0;
    if (gone < 2000) return;
    this.focus.drifts += 1;
    const line = t('fn.focusDrift', { drifts: this.focus.drifts, seconds: Math.round(gone / 1000) });
    this.focus.noteUntil = Date.now() + 8000;
    this._note(line);
    this.voice?.speak(line, (state.get('config') || {}).conversation_language);
  }

  // ------------------------------------------------------------------ REFLEX

  async _reflex(next, reason) {
    if (!next) {
      // REFLEX owns the microphone only if it was the one that opened it.
      if (reason !== 'mic-off' && this._openedMic) this.mic?.mute('reflex-off');
      this._openedMic = false;
      this._note('');
      return false;
    }
    if (!this.mic?.isOn()) {
      const opened = await this.mic?.enable();
      if (!opened) {
        this._note(t('fn.reflexNeedsMic'), true);
        return false;
      }
      this._openedMic = true;
    }
    this._note(t('fn.reflexOn', { wake: this._wakeWord() }));
    return true;
  }

  /** Called when the microphone goes off for any reason: REFLEX cannot survive it. */
  micWentOff() {
    if (this.on.reflex) this.set('reflex', false, 'mic-off');
  }

  // ------------------------------------------------------------------ view

  _note(text, warn = false) {
    this.onNote?.(text, warn);
  }

  _setState(name, text) {
    const button = this.buttons[name];
    const label = button?.querySelector('.fn-state');
    if (label) label.textContent = text;
  }

  render() {
    for (const name of FUNCTIONS) {
      const button = this.buttons[name];
      if (!button) continue;
      const on = this.on[name];
      const unavailable = name === 'watch' && !this.watchSupported();
      button.classList.toggle('is-on', on);
      button.classList.toggle('is-unavailable', unavailable && !on);
      button.setAttribute('aria-pressed', String(on));
      const label = button.querySelector('.fn-state');
      if (label) {
        label.textContent = unavailable ? t('fn.unavailable')
          : on ? t('common.on')
          : t('common.off');
      }
      button.title = t('fn.' + name) + ': ' + (label ? label.textContent : '');
    }
    if (this.focus) this._tickFocus();
  }
}

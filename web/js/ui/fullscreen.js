/**
 * FULL SCREEN — hand the whole screen to JARVIS.
 *
 * This is the *browser's* chrome, not JARVIS's own. It is a different thing
 * from immersive mode, which folds away the rails and the conversation and is
 * still reached by tapping the dial; the two compose, and neither switches
 * the other on.
 *
 * The flow the interface promises:
 *
 *   1. a compact FULL SCREEN button sits at the top;
 *   2. tapping it asks the Fullscreen API for the whole screen;
 *   3. once there, the button goes away so nothing is left floating;
 *   4. a tap anywhere brings back an EXIT FULL SCREEN pill for a few seconds;
 *   5. tapping that — or Escape, or the system gesture — restores the view.
 *
 * Where the browser will not do it, the button says so instead of pretending.
 * Two cases are worth telling apart: a browser with no Fullscreen API at all
 * (iOS Safari on anything but a video), and one that has it but refused this
 * particular request — usually because the call did not come from a real tap.
 */
import { t } from '../i18n/index.js';
import { toast } from './toast.js';

/** How long the exit pill stays up after a tap. */
const EXIT_VISIBLE_MS = 3200;

function element() {
  return document.documentElement;
}

/** The current fullscreen element, across the two spellings still in the wild. */
function current() {
  return document.fullscreenElement || document.webkitFullscreenElement || null;
}

function supported() {
  const el = element();
  // fullscreenEnabled is the honest answer: it is false inside an iframe that
  // was not given permission, and on browsers that only do video fullscreen.
  if (document.fullscreenEnabled === false && document.webkitFullscreenEnabled === false) {
    return false;
  }
  return !!(
    el.requestFullscreen
    || el.webkitRequestFullscreen
    || document.fullscreenEnabled
    || document.webkitFullscreenEnabled
  );
}

/** True when the PWA is already running without browser chrome. */
function standalone() {
  return !!(
    window.matchMedia?.('(display-mode: standalone)').matches
    || window.matchMedia?.('(display-mode: fullscreen)').matches
    || window.navigator.standalone
  );
}

export default class Fullscreen {
  constructor() {
    this.root = document.getElementById('app');
    this.enterBtn = document.getElementById('btn-fullscreen');
    this.exitBtn = document.getElementById('btn-fullscreen-exit');
    this.hideTimer = 0;
    this.supported = supported();

    this.enterBtn?.addEventListener('click', (event) => {
      event.stopPropagation();
      this.enter();
    });
    this.exitBtn?.addEventListener('click', (event) => {
      event.stopPropagation();
      this.exit();
    });

    for (const name of ['fullscreenchange', 'webkitfullscreenchange']) {
      document.addEventListener(name, () => this.render());
    }
    // The pill is a reminder, not furniture: it appears on a tap and leaves
    // on its own, so a fullscreen JARVIS stays uncluttered.
    document.addEventListener('pointerdown', () => {
      if (this.isOn()) this._flashExit();
    }, { capture: true, passive: true });

    this.render();
  }

  isOn() {
    return !!current();
  }

  async enter() {
    if (!this.supported) {
      this._explain();
      return false;
    }
    const el = element();
    const request = el.requestFullscreen || el.webkitRequestFullscreen;
    if (!request) {
      this._explain();
      return false;
    }
    try {
      // navigationUI:'hide' is a hint; Android Chrome honours it, others ignore
      // it, and neither is a failure.
      await request.call(el, { navigationUI: 'hide' });
    } catch (err) {
      // A rejected promise here is almost always "not a user gesture" or a
      // permissions policy. Say which rather than failing silently.
      console.warn('[fullscreen] refused', err);
      toast(t('fs.denied'), 'error', 5000);
      this.render();
      return false;
    }
    this.render();
    this._flashExit();
    return true;
  }

  async exit() {
    const leave = document.exitFullscreen || document.webkitExitFullscreen;
    if (!leave || !this.isOn()) {
      this.render();
      return;
    }
    try {
      await leave.call(document);
    } catch (err) {
      console.warn('[fullscreen] could not exit', err);
    }
    this.render();
  }

  _explain() {
    // Two different problems, two different messages. In an installed PWA
    // that is already chromeless there is nothing to gain, and saying so is
    // more useful than an error.
    toast(standalone() ? t('fs.alreadyFull') : t('fs.unsupported'), 'info', 6000);
  }

  _flashExit() {
    if (!this.exitBtn) return;
    this.exitBtn.hidden = false;
    this.exitBtn.classList.add('is-visible');
    clearTimeout(this.hideTimer);
    this.hideTimer = setTimeout(() => {
      this.exitBtn.classList.remove('is-visible');
      // Leave it in the DOM until the fade is done, then take it out of the
      // accessibility tree too.
      this.hideTimer = setTimeout(() => {
        if (this.isOn()) this.exitBtn.hidden = true;
      }, 220);
    }, EXIT_VISIBLE_MS);
  }

  render() {
    const on = this.isOn();
    this.root?.setAttribute('data-fullscreen', on ? 'on' : 'off');
    if (this.enterBtn) {
      // Hidden while full screen — that is requirement 2 — and also when the
      // browser cannot do it at all, so there is no dead control. The PWA
      // keeps the button: a standalone window can still go properly full
      // screen, it just has less to gain.
      this.enterBtn.hidden = on || !this.supported;
    }
    if (this.exitBtn && !on) {
      clearTimeout(this.hideTimer);
      this.exitBtn.classList.remove('is-visible');
      this.exitBtn.hidden = true;
    }
  }
}

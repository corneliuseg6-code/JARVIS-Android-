/**
 * JARVIS Android V2 — application bootstrap.
 *
 * Every subsystem is started inside its own guard: if one of them throws, the
 * rest of the interface still comes up and the failure is reported, instead of
 * the whole page dying at the first error (the classic "buttons do nothing"
 * symptom).
 *
 * The shell is: top bar, status strip, visualiser, HUD, chat dock — with the
 * navigation as a drawer and the panels as a sheet, so neither can steal
 * vertical space from the visualiser.
 */
import { api } from './core/api.js';
import * as state from './core/state.js';
import { on } from './core/bus.js';
import {
  LANGUAGES, applyTranslations, isSupported, setLanguage, t, missingKeys,
} from './i18n/index.js';
import Avatar from './avatar/index.js';
import Mic from './ui/mic.js';
import Voice from './ui/voice.js';
import Power from './ui/power.js';
import Magnifier from './ui/magnifier.js';
import Status from './ui/status.js';
import Chat from './ui/chat.js';
import Armory from './ui/armory.js';
import Eyes from './ui/eyes.js';
import Modes from './ui/modes.js';
import Settings from './ui/settings.js';
import { toast } from './ui/toast.js';

const app = {
  avatar: null, mic: null, voice: null, power: null, magnifier: null,
  status: null, chat: null, armory: null, eyes: null, modes: null, settings: null,
};

const root = document.getElementById('app');
const SHEET_VIEWS = ['armory', 'eyes', 'settings'];

function guard(name, fn) {
  try {
    return fn();
  } catch (err) {
    console.error(`[jarvis] ${name} failed to start`, err);
    api.log('error', `${name}: ${err && err.message ? err.message : err}`);
    toast(`${name}: ${err && err.message ? err.message : 'error'}`, 'error', 6000);
    return null;
  }
}

// ---------------------------------------------------------------- activity
function setActivity(activity) {
  state.set({ activity });
  root?.setAttribute('data-activity', activity);
  app.avatar?.setActivity(activity);
  const label = document.getElementById('activity-label');
  if (label) {
    const key = state.get('power') === 'standby' ? 'activity.standby' : 'activity.' + activity;
    label.textContent = t(key);
    label.removeAttribute('data-i18n');
  }
}

// ---------------------------------------------------------------- shell
function setNav(open) {
  root?.setAttribute('data-nav', open ? 'open' : 'closed');
  const drawer = document.getElementById('drawer');
  const scrim = document.getElementById('scrim');
  const toggle = document.getElementById('btn-nav');
  if (drawer) drawer.setAttribute('aria-hidden', String(!open));
  if (toggle) toggle.setAttribute('aria-expanded', String(open));
  // The scrim covers the drawer and the sheet alike.
  if (scrim) scrim.hidden = !open && !SHEET_VIEWS.includes(state.get('view'));
}

function setDock(expanded) {
  root?.setAttribute('data-dock', expanded ? 'expanded' : 'collapsed');
  document.getElementById('btn-dock')?.setAttribute('aria-expanded', String(expanded));
  state.set({ dockOpen: expanded });
  requestAnimationFrame(() => app.avatar?.resize());
}

/** 'home' shows the visualiser alone; the rest open the bottom sheet. */
function setView(view) {
  if (!SHEET_VIEWS.includes(view)) view = 'home';
  state.set({ view });
  root?.setAttribute('data-view', view);

  document.querySelectorAll('.menu-btn').forEach((button) => {
    button.classList.toggle('is-active', button.dataset.view === view);
  });

  const sheet = document.getElementById('sheet');
  const title = document.getElementById('sheet-title');
  const scrim = document.getElementById('scrim');
  const isSheet = SHEET_VIEWS.includes(view);
  if (sheet) sheet.hidden = !isSheet;
  if (scrim) scrim.hidden = !isSheet && root?.getAttribute('data-nav') !== 'open';
  document.querySelectorAll('.panel').forEach((panel) => {
    panel.hidden = panel.dataset.panel !== view;
  });
  if (title && isSheet) {
    title.textContent = t('nav.' + view);
    title.removeAttribute('data-i18n');
  }

  // Leaving the Eyes panel releases the camera, and with it the EYES switch.
  if (view !== 'eyes' && app.eyes?.isOn()) {
    app.eyes.stop('view-change');
    app.modes?.set('eyes', false, 'view-change');
  }
  if (view === 'armory') app.armory?.refresh();
  if (view === 'settings') app.settings?.refresh();

  requestAnimationFrame(() => app.avatar?.resize());
}

function setNote(text, warn) {
  const note = document.getElementById('stage-note');
  if (!note) return;
  note.textContent = text || '';
  note.hidden = !text;
  note.classList.toggle('is-warn', !!warn);
}

// ---------------------------------------------------------------- language
function applyLanguage(code, { persist = false } = {}) {
  if (!isSupported(code)) code = 'ro';
  setLanguage(code);
  const quick = document.getElementById('lang-quick');
  if (quick && quick.value !== code) quick.value = code;
  // Components that render their own text must repaint.
  app.status?.render();
  app.mic?.render();
  app.power?.render();
  app.armory?.render();
  app.modes?.render();
  app.settings?.render();
  app.voice?.resetWarnings();
  app.mic?.refreshLanguage();
  setActivity(state.get('activity'));
  if (SHEET_VIEWS.includes(state.get('view'))) setView(state.get('view'));
  state.saveLocal({ ui_language: code });
  if (persist) {
    const cfg = state.get('config') || {};
    const patch = { ui_language: code };
    if (cfg.conversation_follows_ui !== false) patch.conversation_language = code;
    api.setConfig(patch)
      .then((config) => state.set({ config }))
      .catch(() => { /* language still applied locally */ });
  }
}

function buildLanguagePicker() {
  const select = document.getElementById('lang-quick');
  if (!select) return;
  select.innerHTML = '';
  for (const lang of LANGUAGES) {
    const option = document.createElement('option');
    option.value = lang.code;
    option.textContent = `${lang.short} — ${lang.label}`;
    option.title = lang.label;
    select.appendChild(option);
  }
  select.addEventListener('change', () => applyLanguage(select.value, { persist: true }));
}

// ---------------------------------------------------------------- avatar
function applyAvatarMode(mode, { persist = false } = {}) {
  const applied = app.avatar?.setMode(mode) || 'rings';
  document.querySelectorAll('.mode-btn').forEach((button) => {
    button.classList.toggle('is-active', button.dataset.mode === applied);
  });
  const tag = document.getElementById('stage-tag');
  if (tag) tag.textContent = applied.toUpperCase();
  state.set({ avatarMode: applied });
  state.saveLocal({ avatarMode: applied });
  if (persist) {
    api.setConfig({ avatar_mode: applied })
      .then((config) => state.set({ config }))
      .catch(() => {});
  }
}

function applyMotion(reduced) {
  root?.setAttribute('data-motion', reduced ? 'reduced' : 'full');
  app.avatar?.setReducedMotion(reduced);
}

// ---------------------------------------------------------------- boot
async function boot() {
  // 1. config (the interface works even if the backend is down)
  let config = null;
  try {
    config = await api.getConfig();
    state.set({ config });
  } catch {
    toast(t('error.network'), 'error', 5000);
  }

  const local = state.loadLocal();
  const language = (config && config.ui_language) || local.ui_language || 'ro';

  buildLanguagePicker();
  applyLanguage(language);
  applyTranslations();

  // 2. avatar
  app.avatar = guard('avatar', () => new Avatar(document.getElementById('avatar-canvas')));
  app.avatar?.start();
  applyAvatarMode((config && config.avatar_mode) || local.avatarMode || 'rings');
  applyMotion(!!(config && config.reduced_motion));
  app.avatar?.setLabel((config && config.hud_label) || 'J.A.R.V.I.S.');

  // 3. voice + microphone (muted) + power
  app.voice = guard('voice', () => new Voice({ onActivity: setActivity }));
  app.mic = guard('mic', () => new Mic({
    // REFLEX, when it is on, decides whether an utterance was meant for JARVIS.
    onTranscript: (text) => {
      const wanted = app.modes ? app.modes.filterTranscript(text) : text;
      if (wanted) app.chat?.send(wanted);
    },
    onLevel: (level) => app.avatar?.setLevel(level),
  }));
  app.eyes = guard('eyes', () => new Eyes());
  app.power = guard('power', () => new Power({
    avatar: app.avatar, mic: app.mic, voice: app.voice, eyes: app.eyes,
  }));

  // 4. panels
  app.chat = guard('chat', () => new Chat({
    voice: app.voice,
    onActivity: setActivity,
    onSend: () => setDock(true),
    onReply: (text) => {
      const ticker = document.getElementById('dock-ticker');
      if (ticker) ticker.textContent = text || '';
    },
  }));
  app.armory = guard('armory', () => new Armory());
  app.settings = guard('settings', () => new Settings({
    onLanguageChange: (code) => applyLanguage(code),
    onConfigChange: (next) => {
      state.set({ config: next });
      applyMotion(!!next.reduced_motion);
      app.avatar?.setLabel(next.hud_label || 'J.A.R.V.I.S.');
      app.magnifier?.setVisible(next.magnifier_enabled !== false);
      app.mic?.refreshLanguage();
      app.voice?.resetWarnings();
      app.modes?.render();
    },
  }));
  app.magnifier = guard('magnifier', () => new Magnifier());
  app.magnifier?.setVisible(!config || config.magnifier_enabled !== false);

  // 5. the five function switches
  app.modes = guard('modes', () => new Modes({
    avatar: app.avatar,
    eyes: app.eyes,
    mic: app.mic,
    voice: app.voice,
    onOpenView: setView,
    onNote: setNote,
  }));

  // 6. status polling
  app.status = guard('status', () => new Status());
  app.status?.start();

  // 7. wiring
  document.getElementById('btn-nav')?.addEventListener('click', () => {
    setNav(root?.getAttribute('data-nav') !== 'open');
  });
  document.getElementById('btn-nav-close')?.addEventListener('click', () => setNav(false));
  document.getElementById('scrim')?.addEventListener('click', () => {
    setNav(false);
    if (SHEET_VIEWS.includes(state.get('view'))) setView('home');
  });
  document.getElementById('btn-sheet-close')?.addEventListener('click', () => setView('home'));

  document.querySelectorAll('.menu-btn').forEach((button) => {
    button.addEventListener('click', () => {
      setView(button.dataset.view);
      setNav(false);
    });
  });
  document.getElementById('btn-open-settings')?.addEventListener('click', () => setView('settings'));

  document.querySelectorAll('.mode-btn').forEach((button) => {
    button.addEventListener('click', () => applyAvatarMode(button.dataset.mode, { persist: true }));
  });

  document.getElementById('btn-dock')?.addEventListener('click', () => {
    setDock(root?.getAttribute('data-dock') !== 'expanded');
  });
  document.getElementById('chat-input')?.addEventListener('focus', () => setDock(true));

  document.getElementById('btn-exit')?.addEventListener('click', onExit);

  window.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (root?.getAttribute('data-nav') === 'open') setNav(false);
    else if (SHEET_VIEWS.includes(state.get('view'))) setView('home');
    else if (root?.getAttribute('data-dock') === 'expanded') setDock(false);
  });

  on('power:standby', () => {
    app.modes?.stopAll('standby');
    setNote('');
    setActivity('idle');
    app.status?.schedule();
  });
  on('power:active', () => { setActivity('idle'); app.status?.refresh(); });
  on('mic:on', () => setActivity('listening'));
  on('mic:off', () => {
    app.modes?.micWentOff();
    if (state.get('activity') === 'listening') setActivity('idle');
  });

  setActivity('idle');
  setView('home');
  setNav(false);
  setDock(false);

  const problems = missingKeys();
  if (Object.keys(problems).length) console.warn('[i18n] incomplete translations', problems);

  console.info('[jarvis] ready — microphone muted, avatar:', state.get('avatarMode'));
}

/** EXIT is separate from STANDBY: it shuts the interface down, server keeps running. */
function onExit() {
  if (!window.confirm(t('power.exitConfirm'))) return;
  app.modes?.stopAll('exit');
  app.mic?.mute('exit');
  app.eyes?.stop('exit');
  app.voice?.stop();
  app.avatar?.stop();
  app.status?.stop();
  api.setPower('standby').catch(() => {});
  document.body.innerHTML =
    `<div style="display:grid;place-items:center;height:100dvh;color:#74a0a8;
      font-family:system-ui,sans-serif;text-align:center;padding:24px;font-size:14px">
      ${t('power.exitDone')}
     </div>`;
  window.setTimeout(() => { try { window.close(); } catch { /* PWA may refuse */ } }, 400);
}

// ---------------------------------------------------------------- errors
window.addEventListener('error', (event) => {
  console.error('[jarvis] uncaught', event.error || event.message);
  api.log('error', String(event.message || event.error));
});
window.addEventListener('unhandledrejection', (event) => {
  console.error('[jarvis] unhandled rejection', event.reason);
  api.log('error', 'unhandled rejection: ' + String(event.reason));
});

if ('serviceWorker' in navigator && location.protocol !== 'file:') {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch((err) => console.warn('[jarvis] sw', err));
  });
}

boot().catch((err) => {
  console.error('[jarvis] boot failed', err);
  toast('Boot error: ' + (err && err.message ? err.message : err), 'error', 8000);
});

// Exposed for debugging from the Chrome console on the phone.
window.JARVIS = { app, state, setView, setActivity, setDock, setNav, applyLanguage, applyAvatarMode };

/**
 * JARVIS Android V2 — application bootstrap.
 *
 * Every subsystem is started inside its own guard: if one of them throws, the
 * rest of the interface still comes up and the failure is reported, instead of
 * the whole page dying at the first error (the classic "buttons do nothing"
 * symptom).
 *
 * The shell is: top bar, the console band (rings between a status rail and a
 * control rail, hologram in the corner), the conversation, and the chat bar —
 * with the navigation as a drawer and the panels as a sheet, so neither can
 * steal space from the other two.
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
import Connections from './ui/connections.js';
import ToolsMenu from './ui/toolsmenu.js';
import Fullscreen from './ui/fullscreen.js';
import Shortcuts from './ui/shortcuts.js';
import Clock from './ui/clock.js';
import Backdrop from './ui/backdrop.js';
import Modes from './ui/modes.js';
import Folders from './ui/folders.js';
import Settings from './ui/settings.js';
import { toast } from './ui/toast.js';

// The live module instances, reachable from the console. Handy when
// something looks wrong on the phone and there is no debugger attached, and
// it is what lets a test assert against the real objects rather than
// re-deriving their state from the DOM.
window.__jarvis = undefined;

const app = {
  avatar: null, mic: null, voice: null, power: null, magnifier: null,
  status: null, chat: null, armory: null, eyes: null, modes: null, settings: null,
  connections: null, fullscreen: null, shortcuts: null, clock: null,
  backdrop: null,
};

const root = document.getElementById('app');
const SHEET_VIEWS = ['armory', 'eyes', 'connections', 'settings'];

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
/**
 * The edge pulse reports what JARVIS is doing.
 *
 * Green while it is listening, thinking or speaking; red while it is standing
 * by or in standby. It reads the same two pieces of state the activity pill
 * does, from the same function, so the border and the pill can never tell you
 * different things.
 */
function applyEdgeState() {
  const edge = document.getElementById('edge');
  if (!edge) return;
  const busy = state.get('power') !== 'standby'
    && ['listening', 'thinking', 'speaking'].includes(state.get('activity'));
  edge.dataset.state = busy ? 'busy' : 'rest';
}

function setActivity(activity) {
  state.set({ activity });
  root?.setAttribute('data-activity', activity);
  applyEdgeState();
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
  if (view === 'connections') app.connections?.refresh();
  // The connections panel polls while a Claude Code run is in flight; leaving
  // the panel stops it rather than letting it run behind a closed sheet.
  if (view !== 'connections') app.connections?.stop();

  requestAnimationFrame(() => app.avatar?.resize());
}

/**
 * Immersive mode: the visualiser takes the whole screen and every control
 * folds away. Tap the stage to enter, tap it again (or the ✕, or Escape) to
 * come back. The backend, the microphone, the voice and the conversation all
 * keep running — only the chrome goes.
 */
function setImmersive(on) {
  if (state.get('immersive') === on) return;
  state.set({ immersive: on });
  root?.setAttribute('data-immersive', on ? 'on' : 'off');
  const exit = document.getElementById('btn-immersive-exit');
  if (exit) exit.hidden = !on;
  if (on) {
    setNav(false);
    if (SHEET_VIEWS.includes(state.get('view'))) setView('home');
  }
  // Follow the rows while they fold, so the canvas grows with the stage
  // instead of snapping to its new size when the transition ends.
  const until = performance.now() + 420;
  const pump = () => {
    app.avatar?.resize();
    if (performance.now() < until) requestAnimationFrame(pump);
  };
  requestAnimationFrame(pump);
}

/**
 * A tap on the stage toggles immersive mode — but the stage is also where
 * HOLO is dragged, so only a press that barely moved and ended quickly counts
 * as a tap.
 */
function wireStageTap() {
  const stage = document.getElementById('stage');
  if (!stage) return;
  let start = null;
  stage.addEventListener('pointerdown', (event) => {
    if (event.target.closest('button')) return;      // the ✕ has its own job
    start = { x: event.clientX, y: event.clientY, t: performance.now() };
  });
  stage.addEventListener('pointerup', (event) => {
    if (!start) return;
    const moved = Math.hypot(event.clientX - start.x, event.clientY - start.y);
    const elapsed = performance.now() - start.t;
    start = null;
    if (moved < 12 && elapsed < 400) setImmersive(!state.get('immersive'));
  });
  stage.addEventListener('pointercancel', () => { start = null; });
}

/**
 * Keep the interface inside the part of the screen the user can actually see.
 *
 * `interactive-widget=resizes-content` in the viewport meta makes Chrome shrink
 * the layout viewport when the keyboard opens, which is all modern Android
 * needs. This covers the rest: older Chrome, and the moment between the
 * keyboard appearing and the layout settling.
 */
function trackViewport() {
  const vv = window.visualViewport;
  if (!vv) return;
  const apply = () => {
    const active = document.activeElement;
    const typing = !!active && (active.tagName === 'TEXTAREA' || active.tagName === 'INPUT');
    // Only while typing: a pinch-zoom also shrinks the visual viewport, and
    // treating that as a keyboard would squash the layout for no reason.
    const keyboard = typing
      ? Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop))
      : 0;
    document.documentElement.style.setProperty('--kb', keyboard + 'px');
    // The console sheds the hologram while the keyboard is up, so the
    // conversation does not shrink to a couple of lines.
    root?.setAttribute('data-kb', keyboard > 120 ? 'on' : 'off');
    app.avatar?.resize();
  };
  vv.addEventListener('resize', apply);
  vv.addEventListener('scroll', apply);
  window.addEventListener('orientationchange', () => setTimeout(apply, 150));
  document.addEventListener('focusin', apply);
  document.addEventListener('focusout', () => setTimeout(apply, 60));
  apply();
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
  app.connections?.render();
  app.fullscreen?.render();
  app.shortcuts?.render();
  app.clock?.setLanguage(code);
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
  // The dial no longer carries a "RINGS"/"FACE" caption: the RINGS/FACE bar
  // directly beneath it already says which one is showing, and the label was
  // the only text sitting on the artwork.
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
  app.backdrop?.setReducedMotion(reduced);
  document.getElementById('edge')?.classList.toggle('is-still', !!reduced);
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
  app.avatar = guard('avatar', () => new Avatar(
    document.getElementById('avatar-canvas'),
    document.getElementById('avatar-gl'),
    document.getElementById('avatar-card-gl'),
  ));
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
  }));
  // The folder network. Built after the chat so it can be handed to the
  // panel that shows it, and left stopped until the FOLDERS tab is opened —
  // an animation nobody is looking at is a battery leak.
  app.folders = guard('folders', () => new Folders({
    onOpenFile: (path) => app.chat?.openWorkspaceFile?.(path),
  }));
  if (app.chat && app.folders) {
    app.chat.attachFolders(app.folders);
    app.folders.stop();
  }
  window.__jarvis = app;
  app.armory = guard('armory', () => new Armory());
  app.connections = guard('connections', () => new Connections({
    onConfigChange: (next) => {
      state.set({ config: next });
      app.status?.refresh();
      app.settings?.refresh?.();
    },
  }));
  app.tools = guard('tools', () => new ToolsMenu({ openView: (view) => setView(view) }));
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
      app.shortcuts?.render();
    },
  }));
  // Ambient layers: the suspended particles and the travelling edge pulse
  // belong to every mode, so they start here rather than with full screen.
  app.backdrop = guard('backdrop', () => new Backdrop(
    document.getElementById('backdrop-canvas'),
  ));
  // The conversation is never painted into: it is a panel in its own right,
  // and motes behind it would still show at its edges.
  app.backdrop?.maskOut(['.chat-panel', '.dock']);
  app.backdrop?.start();
  // One watchdog for the whole screen: when the dial drops a quality level
  // because frames are slipping, the particle field sheds motes with it.
  if (app.avatar) app.avatar.onQuality = (level) => app.backdrop?.setQuality(level);
  app.fullscreen = guard('fullscreen', () => new Fullscreen());
  app.fullscreen?.setBackdrop(app.backdrop);
  app.shortcuts = guard('shortcuts', () => new Shortcuts({
    // An unconfigured slot is not a dead button: it opens the place where
    // you give it a name and an address.
    onConfigure: () => setView('settings'),
  }));
  app.clock = guard('clock', () => new Clock({ language }));
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

  document.getElementById('btn-exit')?.addEventListener('click', onExit);

  wireStageTap();
  trackViewport();
  document.getElementById('btn-immersive-exit')?.addEventListener('click', (event) => {
    event.stopPropagation();
    setImmersive(false);
  });

  window.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (state.get('immersive')) setImmersive(false);
    else if (app.tools?.isOpen()) app.tools.close();
    else if (root?.getAttribute('data-nav') === 'open') setNav(false);
    else if (SHEET_VIEWS.includes(state.get('view'))) setView('home');
  });

  on('power:standby', () => {
    applyEdgeState();
    app.modes?.stopAll('standby');
    setNote('');
    setActivity('idle');
    app.status?.schedule();
  });
  on('power:active', () => { applyEdgeState(); setActivity('idle'); app.status?.refresh(); });
  on('mic:on', () => setActivity('listening'));
  on('mic:off', () => {
    app.modes?.micWentOff();
    if (state.get('activity') === 'listening') setActivity('idle');
  });

  setActivity('idle');
  setView('home');
  setNav(false);
  root?.setAttribute('data-immersive', 'off');

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
// A card in the chat (a queued Claude Code task) can ask for a panel.
window.addEventListener('jarvis:open-view', (event) => setView(event.detail));

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
window.JARVIS = {
  app, state, setView, setActivity, setNav, setImmersive,
  applyLanguage, applyAvatarMode,
};

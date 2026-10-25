/**
 * Central runtime state.
 *
 * Two invariants that the rest of the app relies on:
 *   micOn      starts false on every load and is never restored from storage
 *   power      starts 'active'; standby is runtime-only
 */
import { emit } from './bus.js';

const LS_KEY = 'jarvis.ui.v2';

const state = {
  config: null,
  health: null,
  activity: 'idle',     // idle | listening | thinking | speaking
  power: 'active',      // active | standby
  micOn: false,         // ALWAYS false at startup
  cameraOn: false,
  view: 'chat',         // chat | armory | eyes | settings
  avatarMode: 'rings',  // rings | face
  zoom: 1,
  conversationId: '',
  busy: false,
};

export function get(key) {
  return key === undefined ? { ...state } : state[key];
}

export function set(patch) {
  let changed = false;
  for (const [key, value] of Object.entries(patch)) {
    if (state[key] !== value) {
      state[key] = value;
      changed = true;
      emit('state:' + key, value);
    }
  }
  if (changed) emit('state:change', { ...state });
  return { ...state };
}

/** Per-device preferences the backend does not need to know about. */
export function loadLocal() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return {};
    const data = JSON.parse(raw);
    return data && typeof data === 'object' ? data : {};
  } catch {
    return {};
  }
}

export function saveLocal(patch) {
  try {
    const data = { ...loadLocal(), ...patch };
    delete data.micOn;   // never persist microphone state
    delete data.power;   // never persist standby
    localStorage.setItem(LS_KEY, JSON.stringify(data));
  } catch {
    /* private mode or storage disabled: preferences simply do not persist */
  }
}

/**
 * Connection status chips.
 *
 * Every subsystem reports one of four states and all four are translated:
 * connected / connecting / disconnected / error. Polling stops in standby and
 * while the tab is hidden.
 */
import { api } from '../core/api.js';
import * as state from '../core/state.js';
import { emit } from '../core/bus.js';
import { t, tState } from '../i18n/index.js';

const SUBSYSTEMS = [
  { key: 'server', label: 'status.server' },
  // The brain chip names whichever provider is actually in use, so a glance
  // at it says both "is it up" and "what is answering".
  { key: 'llm', label: 'status.brain' },
  { key: 'voice', label: 'status.voice' },
  { key: 'armory', label: 'status.armory' },
  { key: 'vision', label: 'status.vision' },
];

const INTERVAL_ACTIVE = 12000;
const INTERVAL_IDLE = 45000;

export default class Status {
  constructor() {
    this.host = document.getElementById('status-chips');
    this.timer = 0;
    this.latest = null;
    this.render();
    document.addEventListener('visibilitychange', () => this.schedule());
  }

  start() {
    this.refresh();
    this.schedule();
  }

  stop() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = 0;
  }

  schedule() {
    this.stop();
    if (document.hidden) return;                       // no polling in the background
    const interval = state.get('power') === 'standby' ? INTERVAL_IDLE : INTERVAL_ACTIVE;
    this.timer = setTimeout(() => this.refresh().then(() => this.schedule()), interval);
  }

  async refresh() {
    try {
      const health = await api.health();
      this.latest = health;
      state.set({ health });
      emit('health', health);
    } catch {
      this.latest = null;
      state.set({ health: null });
      emit('health', null);
    }
    this.render();
    return this.latest;
  }

  /** The chip's own text: the provider's name when we know it. */
  _labelOf(item) {
    if (item.key !== 'llm') return t(item.label);
    return this.latest?.llm?.label || t(item.label);
  }

  _stateOf(key) {
    const health = this.latest;
    if (!health) return key === 'server' ? 'disconnected' : 'unknown';
    if (key === 'llm') {
      const brain = health.llm || {};
      if (brain.state !== 'connected') return brain.state || 'unknown';
      // Connected but pointed at a model that is not there is not "fine".
      return brain.model_ready === false ? 'error' : 'connected';
    }
    if (key === 'vision') {
      const vision = health.vision || {};
      if (!vision.enabled) return 'disconnected';
      return vision.model_installed ? 'connected' : 'error';
    }
    return (health[key] && health[key].state) || 'unknown';
  }

  render() {
    if (!this.host) return;
    this.host.innerHTML = '';
    for (const item of SUBSYSTEMS) {
      const chipState = this._stateOf(item.key);
      const label = this._labelOf(item);
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.dataset.state = chipState;
      const detail = item.key === 'llm'
        ? (this.latest?.llm?.detail || this.latest?.llm?.error || '')
        : '';
      chip.title = label + ': ' + tState(chipState) + (detail ? ' — ' + detail : '');
      const dot = document.createElement('span');
      dot.className = 'dot';
      const text = document.createElement('span');
      text.textContent = label;
      chip.append(dot, text);
      this.host.appendChild(chip);
    }
  }
}

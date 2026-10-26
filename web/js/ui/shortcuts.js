/**
 * Four compact shortcuts on the top bar's second line.
 *
 * Two are fixed (OpenAI and GitHub) and two are yours, named and pointed
 * wherever you like from Settings. They are real links — `<a target="_blank">`
 * — rather than buttons that call `window.open`, so Android Chrome treats
 * them as an ordinary navigation and a long press offers the usual menu.
 *
 * An unconfigured slot shows a "+" and opens Settings when tapped: there is
 * no dead button, and nothing pretends to point somewhere it does not.
 */
import * as state from '../core/state.js';
import { t } from '../i18n/index.js';

/** The two that are always there. */
const FIXED = [
  { id: 'openai', label: 'OpenAI', url: 'https://chatgpt.com/', glyph: '◎' },
  { id: 'github', label: 'GitHub', url: 'https://github.com/', glyph: '⌥' },
];

/** The two configurable ones, and the config keys behind them. */
export const CUSTOM = [
  { id: 'custom1', nameKey: 'shortcut1_name', urlKey: 'shortcut1_url' },
  { id: 'custom2', nameKey: 'shortcut2_name', urlKey: 'shortcut2_url' },
];

/** Only ever hand the browser an http(s) URL we recognise. */
export function safeUrl(raw) {
  const value = String(raw || '').trim();
  if (!value) return '';
  try {
    const url = new URL(value);
    return (url.protocol === 'http:' || url.protocol === 'https:') ? url.href : '';
  } catch {
    return '';
  }
}

export default class Shortcuts {
  constructor({ onConfigure } = {}) {
    this.host = document.getElementById('shortcuts');
    this.onConfigure = onConfigure;
    this.render();
  }

  render() {
    if (!this.host) return;
    const cfg = state.get('config') || {};
    this.host.innerHTML = '';

    for (const item of FIXED) {
      this.host.appendChild(this._link(item.glyph, item.label, item.url, item.label));
    }

    for (const [index, slot] of CUSTOM.entries()) {
      const url = safeUrl(cfg[slot.urlKey]);
      const name = String(cfg[slot.nameKey] || '').trim();
      if (url) {
        this.host.appendChild(this._link('▸', name || t('sc.custom', { n: index + 1 }), url, name || url));
      } else {
        this.host.appendChild(this._empty(index + 1));
      }
    }
  }

  _link(glyph, label, url, title) {
    const a = document.createElement('a');
    a.className = 'sc';
    a.href = url;
    a.target = '_blank';
    // noopener keeps the new tab from reaching back into JARVIS through
    // window.opener; noreferrer keeps the local address out of the referrer.
    a.rel = 'noopener noreferrer';
    a.title = title;
    a.setAttribute('aria-label', t('sc.open', { name: title }));
    const ico = document.createElement('span');
    ico.className = 'sc-ico';
    ico.setAttribute('aria-hidden', 'true');
    ico.textContent = glyph;
    const text = document.createElement('span');
    text.className = 'sc-label';
    text.textContent = label;
    a.append(ico, text);
    return a;
  }

  _empty(n) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'sc is-empty';
    button.title = t('sc.setUp', { n });
    button.setAttribute('aria-label', t('sc.setUp', { n }));
    const ico = document.createElement('span');
    ico.className = 'sc-ico';
    ico.setAttribute('aria-hidden', 'true');
    ico.textContent = '+';
    const text = document.createElement('span');
    text.className = 'sc-label';
    text.textContent = t('sc.custom', { n });
    button.append(ico, text);
    button.addEventListener('click', () => this.onConfigure?.());
    return button;
  }
}

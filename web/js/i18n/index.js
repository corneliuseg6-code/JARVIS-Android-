/**
 * Translation layer.
 *
 * Every user-visible string goes through t(). Markup carries data-i18n
 * attributes so applyTranslations() can re-render the whole interface the
 * instant the language changes — no reload, no page rebuild.
 */
import ro from './ro.js';
import en from './en.js';
import fr from './fr.js';
import de from './de.js';
import es from './es.js';
import it from './it.js';

export const LANGUAGES = [
  { code: 'ro', label: 'Română', short: 'RO' },
  { code: 'en', label: 'English', short: 'EN' },
  { code: 'fr', label: 'Français', short: 'FR' },
  { code: 'de', label: 'Deutsch', short: 'DE' },
  { code: 'es', label: 'Español', short: 'ES' },
  { code: 'it', label: 'Italiano', short: 'IT' },
];

const DICTS = { ro, en, fr, de, es, it };
const FALLBACK = 'en';

let current = 'ro';
const listeners = new Set();

export function availableLanguages() {
  return LANGUAGES.slice();
}

export function getLanguage() {
  return current;
}

export function isSupported(code) {
  return Object.prototype.hasOwnProperty.call(DICTS, code);
}

export function setLanguage(code) {
  if (!isSupported(code) || code === current) return current;
  current = code;
  document.documentElement.lang = code;
  applyTranslations();
  listeners.forEach((fn) => {
    try { fn(code); } catch (err) { console.error('[i18n] listener failed', err); }
  });
  return current;
}

export function onLanguageChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function t(key, vars) {
  const dict = DICTS[current] || DICTS[FALLBACK];
  let value = dict[key];
  if (value === undefined) value = DICTS[FALLBACK][key];
  if (value === undefined) {
    console.warn('[i18n] missing key:', key);
    return key;
  }
  if (vars) {
    for (const [name, replacement] of Object.entries(vars)) {
      value = value.split('{' + name + '}').join(String(replacement));
    }
  }
  return value;
}

/**
 * Translate a provider failure.
 *
 * The backend sends a stable code rather than a sentence, so the same failure
 * reads correctly in all six languages. `detail` is the provider's own words
 * and is appended when it adds anything — a rejected key is much easier to
 * fix when you can see what OpenAI said about it.
 */
const LLM_CODES = [
  'no_key', 'auth', 'quota', 'rate_limit', 'model_missing', 'forbidden', 'server', 'timeout',
  'budget_exceeded', 'no_provider', 'refusal', 'blocked', 'overloaded', 'invalid_request',
  'stream_error',
];

export function tLLMError(code, detail) {
  let message;
  if (code === 'ollama_error') message = t('error.ollama');
  else if (code === 'network') message = t('error.network');
  else if (LLM_CODES.includes(code)) message = t('error.llm.' + code);
  else message = t('error.generic');
  return detail ? `${message} — ${detail}` : message;
}

/** Translate a backend connection state ("connected", "error", ...). */
export function tState(state) {
  const key = 'state.' + String(state || 'unknown');
  const dict = DICTS[current] || DICTS[FALLBACK];
  return dict[key] !== undefined ? t(key) : t('state.unknown');
}

/**
 * Apply translations to a subtree.
 *   data-i18n="key"             -> textContent
 *   data-i18n-placeholder="key" -> placeholder
 *   data-i18n-title="key"       -> title
 *   data-i18n-aria="key"        -> aria-label
 */
export function applyTranslations(root) {
  const scope = root || document;
  scope.querySelectorAll('[data-i18n]').forEach((el) => {
    el.textContent = t(el.getAttribute('data-i18n'));
  });
  scope.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
    el.setAttribute('placeholder', t(el.getAttribute('data-i18n-placeholder')));
  });
  scope.querySelectorAll('[data-i18n-title]').forEach((el) => {
    el.setAttribute('title', t(el.getAttribute('data-i18n-title')));
  });
  scope.querySelectorAll('[data-i18n-aria]').forEach((el) => {
    el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria')));
  });
}

/** Development aid: report keys missing from any language. */
export function missingKeys() {
  const reference = Object.keys(DICTS[FALLBACK]);
  const report = {};
  for (const [code, dict] of Object.entries(DICTS)) {
    const missing = reference.filter((key) => dict[key] === undefined);
    const extra = Object.keys(dict).filter((key) => !reference.includes(key));
    if (missing.length || extra.length) report[code] = { missing, extra };
  }
  return report;
}

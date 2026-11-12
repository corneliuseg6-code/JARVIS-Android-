/**
 * Tool Armory panel.
 *
 * Credentials are write-only from the browser's point of view: you can set a
 * value, and the card tells you whether one is stored, but no value ever comes
 * back over the wire. A card shows CONNECTED only when the backend reports a
 * verified handshake.
 */
import { api } from '../core/api.js';
import { t, tState } from '../i18n/index.js';
import { toast } from './toast.js';

export default class Armory {
  constructor() {
    this.list = document.getElementById('conn-list');
    this.form = document.getElementById('conn-form');
    this.addBtn = document.getElementById('btn-add-conn');
    this.connections = [];
    this.presets = [];
    this.editing = null;
    this.addBtn?.addEventListener('click', () => this.openForm(null));
  }

  async refresh() {
    try {
      const data = await api.armory();
      this.connections = data.connections || [];
      this.presets = data.presets || [];
    } catch {
      this.connections = [];
      toast(t('error.network'), 'error');
    }
    this.render();
  }

  render() {
    if (!this.list) return;
    this.list.innerHTML = '';
    if (!this.connections.length) {
      const empty = document.createElement('p');
      empty.className = 'empty';
      empty.style.fontSize = '.7em';
      empty.style.color = 'var(--text-dim)';
      empty.textContent = t('armory.none');
      this.list.appendChild(empty);
      return;
    }
    for (const conn of this.connections) this.list.appendChild(this._card(conn));
  }

  _card(conn) {
    const card = document.createElement('div');
    card.className = 'conn-card';
    card.dataset.state = conn.state;

    const top = document.createElement('div');
    top.className = 'conn-top';
    const name = document.createElement('div');
    name.className = 'conn-name';
    name.textContent = conn.name;
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.dataset.state = conn.state;
    const dot = document.createElement('span');
    dot.className = 'dot';
    const label = document.createElement('span');
    label.textContent = tState(conn.state);
    chip.append(dot, label);
    top.append(name, chip);

    const meta = document.createElement('div');
    meta.className = 'conn-meta';
    meta.textContent = conn.transport === 'stdio'
      ? (conn.command || '—')
      : (conn.url || '—');

    card.append(top, meta);

    const secretNames = Object.keys(conn.secrets || {});
    if (secretNames.length) {
      const sec = document.createElement('div');
      sec.className = 'conn-meta';
      sec.textContent = secretNames
        .map((n) => `${n}: ${conn.secrets[n] ? t('armory.secretStored') : t('armory.secretMissing')}`)
        .join(' · ');
      card.appendChild(sec);
    }

    if (conn.state === 'connected') {
      const tools = document.createElement('div');
      tools.className = 'conn-tools';
      if (!conn.tools.length) {
        const none = document.createElement('span');
        none.className = 'tool-tag';
        none.textContent = t('armory.noTools');
        tools.appendChild(none);
      }
      for (const tool of conn.tools.slice(0, 24)) {
        const tag = document.createElement('span');
        tag.className = 'tool-tag';
        tag.textContent = tool.name;
        tag.title = tool.description || '';
        tools.appendChild(tag);
      }
      card.appendChild(tools);
      if (conn.verified_at) {
        const verified = document.createElement('div');
        verified.className = 'conn-meta';
        verified.textContent = `${t('armory.verifiedAt')} ${new Date(conn.verified_at * 1000).toLocaleTimeString()}`;
        card.appendChild(verified);
      }
    }

    if (conn.error) {
      const err = document.createElement('div');
      err.className = 'conn-error';
      err.textContent = conn.error;
      card.appendChild(err);
    }

    const actions = document.createElement('div');
    actions.className = 'conn-actions';
    const connected = conn.state === 'connected';
    actions.appendChild(this._button(
      connected ? t('armory.disconnect') : t('armory.connect'),
      connected ? 'btn-ghost' : 'btn-primary',
      async (button) => {
        button.disabled = true;
        button.textContent = connected ? '…' : t('armory.testing');
        try {
          const updated = connected
            ? await api.armoryDisconnect(conn.id)
            : await api.armoryConnect(conn.id);
          const index = this.connections.findIndex((c) => c.id === conn.id);
          if (index >= 0 && updated) this.connections[index] = updated;
          if (updated && updated.state === 'error') toast(updated.error || t('error.generic'), 'error', 5000);
        } catch {
          toast(t('error.network'), 'error');
        }
        this.render();
      },
    ));
    actions.appendChild(this._button(t('armory.edit'), 'btn-ghost', () => this.openForm(conn)));
    actions.appendChild(this._button(t('armory.delete'), 'btn-danger', async () => {
      if (!window.confirm(t('armory.confirmDelete'))) return;
      try {
        await api.armoryDelete(conn.id);
        this.connections = this.connections.filter((c) => c.id !== conn.id);
        this.render();
      } catch {
        toast(t('error.network'), 'error');
      }
    }));
    card.appendChild(actions);
    return card;
  }

  _button(text, cls, handler) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-sm ' + cls;
    button.textContent = text;
    button.addEventListener('click', () => handler(button));
    return button;
  }

  openForm(conn) {
    if (!this.form) return;
    this.editing = conn;
    this.form.innerHTML = '';
    const values = conn || { transport: 'stdio', name: '', command: '', url: '', cwd: '', env_keys: [], autoconnect: false };

    const nameField = field(t('armory.name'), input('text', values.name));

    // The servers that ship with JARVIS, filled in from the real install path.
    // Only offered for a new connection: tapping one while editing an existing
    // server would quietly overwrite what the user came here to change.
    let presetRow = null;
    if (!conn && this.presets.length) {
      presetRow = document.createElement('div');
      presetRow.className = 'field preset-row';
      const label = document.createElement('label');
      label.textContent = t('armory.presets');
      const hint = document.createElement('span');
      hint.className = 'hint';
      hint.textContent = t('armory.presetsHint');
      const buttons = document.createElement('div');
      buttons.className = 'form-actions';
      for (const preset of this.presets) {
        const button = this._button(preset.name, 'btn-ghost', () => {
          nameField.querySelector('input').value = preset.name;
          transport.value = preset.transport || 'stdio';
          commandInput.value = preset.command || '';
          applyTransport();
          toast(preset.summary || preset.name, 'info');
        });
        button.dataset.preset = preset.id;
        buttons.appendChild(button);
      }
      presetRow.append(label, hint, buttons);
    }
    const transport = document.createElement('select');
    for (const [value, key] of [['stdio', 'armory.transportStdio'], ['http', 'armory.transportHttp']]) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = t(key);
      if (values.transport === value) option.selected = true;
      transport.appendChild(option);
    }
    const transportField = field(t('armory.transport'), transport);

    const commandInput = input('text', values.command);
    const commandField = field(t('armory.command'), commandInput, t('armory.commandHint'));
    const cwdField = field(t('armory.cwd'), input('text', values.cwd || ''));
    const urlInput = input('text', values.url);
    const urlField = field(t('armory.url'), urlInput);

    const envInput = input('text', (values.env_keys || []).join(', '));
    const envField = field(t('armory.envKeys'), envInput, t('armory.envKeysHint'));

    const secretWrap = document.createElement('div');
    secretWrap.className = 'field';
    const secretLabel = document.createElement('label');
    secretLabel.textContent = t('armory.secretValues');
    const secretHint = document.createElement('span');
    secretHint.className = 'hint';
    secretHint.textContent = t('armory.secretHint');
    const secretInputs = document.createElement('div');
    secretInputs.className = 'field';
    secretWrap.append(secretLabel, secretHint, secretInputs);

    const renderSecrets = () => {
      secretInputs.innerHTML = '';
      const names = envInput.value.split(',').map((s) => s.trim()).filter(Boolean);
      for (const name of names) {
        const row = document.createElement('div');
        row.className = 'field-row';
        const tag = document.createElement('span');
        tag.className = 'badge ' + ((conn?.secrets || {})[name] ? 'badge-ok' : 'badge-missing');
        tag.textContent = name + ' · ' + ((conn?.secrets || {})[name] ? t('armory.secretStored') : t('armory.secretMissing'));
        const value = input('password', '');
        value.dataset.secretName = name;
        value.autocomplete = 'off';
        row.append(tag, value);
        secretInputs.appendChild(row);
      }
    };
    envInput.addEventListener('input', renderSecrets);
    renderSecrets();

    const autoRow = document.createElement('label');
    autoRow.className = 'switch-row';
    const autoText = document.createElement('span');
    autoText.textContent = t('armory.autoconnect');
    const autoBox = document.createElement('input');
    autoBox.type = 'checkbox';
    autoBox.checked = !!values.autoconnect;
    autoRow.append(autoText, autoBox);

    const applyTransport = () => {
      const stdio = transport.value === 'stdio';
      commandField.hidden = !stdio;
      cwdField.hidden = !stdio;
      urlField.hidden = stdio;
    };
    transport.addEventListener('change', applyTransport);
    applyTransport();

    const actions = document.createElement('div');
    actions.className = 'form-actions';
    const cancel = this._button(t('armory.cancel'), 'btn-ghost', () => this.closeForm());
    const save = this._button(t('armory.save'), 'btn-primary', async (button) => {
      const payload = {
        name: nameField.querySelector('input').value.trim() || 'MCP',
        transport: transport.value,
        command: commandInput.value.trim(),
        cwd: cwdField.querySelector('input').value.trim(),
        url: urlInput.value.trim(),
        env_keys: envInput.value.split(',').map((s) => s.trim()).filter(Boolean),
        autoconnect: autoBox.checked,
        secret_values: {},
      };
      secretInputs.querySelectorAll('input[data-secret-name]').forEach((el) => {
        if (el.value) payload.secret_values[el.dataset.secretName] = el.value;
      });
      button.disabled = true;
      try {
        if (this.editing) await api.armoryUpdate(this.editing.id, payload);
        else await api.armoryCreate(payload);
        this.closeForm();
        await this.refresh();
      } catch (err) {
        button.disabled = false;
        toast(String(err.message || t('error.generic')), 'error');
      }
    });
    actions.append(cancel, save);

    if (presetRow) this.form.appendChild(presetRow);
    this.form.append(nameField, transportField, commandField, cwdField, urlField, envField, secretWrap, autoRow, actions);
    this.form.hidden = false;
    if (this.list) this.list.hidden = true;
  }

  closeForm() {
    this.editing = null;
    if (this.form) {
      this.form.hidden = true;
      this.form.innerHTML = '';
    }
    if (this.list) this.list.hidden = false;
  }
}

function input(type, value) {
  const el = document.createElement('input');
  el.type = type;
  el.value = value || '';
  return el;
}

function field(labelText, control, hintText) {
  const wrap = document.createElement('div');
  wrap.className = 'field';
  const label = document.createElement('label');
  label.textContent = labelText;
  wrap.append(label, control);
  if (hintText) {
    const hint = document.createElement('span');
    hint.className = 'hint';
    hint.textContent = hintText;
    wrap.appendChild(hint);
  }
  return wrap;
}

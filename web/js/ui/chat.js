/** Conversation panel: streaming answers, history, replay of speech. */
import { api, chatStream } from '../core/api.js';
import * as state from '../core/state.js';
import { t } from '../i18n/index.js';
import { toast } from './toast.js';

export default class Chat {
  constructor({ voice, onActivity, onSend, onReply }) {
    this.voice = voice;
    this.onActivity = onActivity;
    this.onSend = onSend;      // the dock opens itself when a message goes out
    this.onReply = onReply;    // the collapsed dock shows the last answer
    this.messages = document.getElementById('messages');
    this.form = document.getElementById('composer');
    this.input = document.getElementById('chat-input');
    this.sendBtn = document.getElementById('btn-send');
    this.stopBtn = document.getElementById('btn-stop');
    this.historyBtn = document.getElementById('btn-history');
    this.newBtn = document.getElementById('btn-new-chat');
    this.historyList = document.getElementById('history-list');
    this.stream = null;
    this.pending = null;

    this.form?.addEventListener('submit', (event) => {
      event.preventDefault();
      this.send(this.input.value);
    });
    this.input?.addEventListener('input', () => this._autosize());
    this.input?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        this.send(this.input.value);
      }
    });
    this.stopBtn?.addEventListener('click', () => this.abort());
    this.newBtn?.addEventListener('click', () => this.newConversation());
    this.historyBtn?.addEventListener('click', () => this.toggleHistory());
  }

  _autosize() {
    if (!this.input) return;
    this.input.style.height = 'auto';
    this.input.style.height = Math.min(96, this.input.scrollHeight) + 'px';
  }

  async newConversation() {
    try {
      const conv = await api.newConversation((state.get('config') || {}).conversation_language);
      state.set({ conversationId: conv.id });
      this.clear();
    } catch {
      toast(t('error.network'), 'error');
    }
  }

  clear() {
    if (!this.messages) return;
    this.messages.innerHTML = '';
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = t('chat.empty');
    this.messages.appendChild(empty);
  }

  async toggleHistory() {
    if (!this.historyList) return;
    if (!this.historyList.hidden) {
      this.historyList.hidden = true;
      this.messages.hidden = false;
      return;
    }
    this.historyList.innerHTML = '';
    let data;
    try {
      data = await api.conversations();
    } catch {
      toast(t('error.network'), 'error');
      return;
    }
    const items = data.conversations || [];
    if (!items.length) {
      const empty = document.createElement('p');
      empty.className = 'empty';
      empty.textContent = t('chat.noHistory');
      this.historyList.appendChild(empty);
    }
    for (const conv of items) {
      const row = document.createElement('div');
      row.className = 'history-item';
      const title = document.createElement('span');
      title.textContent = conv.title || conv.id;
      const meta = document.createElement('span');
      meta.className = 'meta';
      meta.textContent = `${conv.message_count} · ${(conv.language || '').toUpperCase()}`;
      row.append(title, meta);
      row.addEventListener('click', () => this.open(conv.id));
      this.historyList.appendChild(row);
    }
    this.historyList.hidden = false;
    this.messages.hidden = true;
  }

  async open(id) {
    try {
      const conv = await api.conversation(id);
      state.set({ conversationId: conv.id });
      this.messages.innerHTML = '';
      for (const msg of conv.messages || []) {
        this.addMessage(msg.role === 'user' ? 'user' : 'jarvis', msg.content);
      }
      this.historyList.hidden = true;
      this.messages.hidden = false;
    } catch {
      toast(t('error.network'), 'error');
    }
  }

  addMessage(kind, text) {
    if (!this.messages) return null;
    this.messages.querySelector('.empty')?.remove();
    const el = document.createElement('div');
    el.className = 'msg msg-' + kind;
    const role = document.createElement('span');
    role.className = 'msg-role';
    role.textContent = kind === 'user' ? t('chat.you') : t('chat.jarvis');
    const body = document.createElement('span');
    body.className = 'msg-body';
    body.textContent = text;
    el.append(role, body);
    this.messages.appendChild(el);
    this.messages.scrollTop = this.messages.scrollHeight;
    return body;
  }

  send(raw) {
    const text = (raw || '').trim();
    if (!text) return;
    if (state.get('power') === 'standby') {
      toast(t('error.standby'), 'error');
      return;
    }
    if (this.stream) return;

    const cfg = state.get('config') || {};
    const language = cfg.conversation_language || 'ro';
    this.onSend?.(text);
    this.input.value = '';
    this._autosize();
    this.addMessage('user', text);

    const body = this.addMessage('jarvis', '');
    const bubble = body.parentElement;
    bubble.classList.add('is-streaming');
    let answer = '';

    this._setBusy(true);
    this.onActivity?.('thinking');

    this.stream = chatStream(
      { message: text, language, conversation_id: state.get('conversationId') || undefined },
      {
        onStart: (data) => {
          if (data.conversation_id) state.set({ conversationId: data.conversation_id });
        },
        onToken: (chunk) => {
          answer += chunk;
          body.textContent = answer;
          this.messages.scrollTop = this.messages.scrollHeight;
        },
        onError: (data) => {
          const message = data.code === 'ollama_error' ? t('error.ollama')
            : data.code === 'network' ? t('error.network')
            : t('error.generic');
          bubble.classList.add('msg-error');
          body.textContent = message + (data.detail ? ' — ' + data.detail : '');
        },
        onDone: (data) => {
          const finalText = (data.text || answer).trim();
          body.textContent = finalText;
          this.onReply?.(finalText);
          if (finalText) this._addReplay(bubble, finalText, language);
          this.voice?.speak(finalText, language);
        },
        onClose: () => {
          bubble.classList.remove('is-streaming');
          this.stream = null;
          this._setBusy(false);
          if (state.get('activity') === 'thinking') this.onActivity?.('idle');
        },
      },
    );
  }

  _addReplay(bubble, text, language) {
    const actions = document.createElement('div');
    actions.className = 'msg-actions';
    const replay = document.createElement('button');
    replay.type = 'button';
    replay.className = 'btn btn-ghost btn-sm';
    replay.textContent = t('chat.speakAgain');
    replay.addEventListener('click', () => this.voice?.speak(text, language));
    actions.appendChild(replay);
    bubble.appendChild(actions);
  }

  abort() {
    if (this.stream) {
      this.stream.abort();
      this.stream = null;
    }
    this.voice?.stop();
    this._setBusy(false);
    this.onActivity?.('idle');
  }

  _setBusy(busy) {
    state.set({ busy });
    if (this.sendBtn) this.sendBtn.hidden = busy;
    if (this.stopBtn) this.stopBtn.hidden = !busy;
    if (this.input) this.input.disabled = busy;
  }

  focus() {
    if (!state.get('busy')) this.input?.focus();
  }
}

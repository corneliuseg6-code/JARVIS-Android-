/** Conversation panel: streaming answers, history, replay of speech. */
import { api, chatStream } from '../core/api.js';
import * as state from '../core/state.js';
import { t, tLLMError } from '../i18n/index.js';
import { toast } from './toast.js';

export default class Chat {
  constructor({ voice, onActivity, onSend, onReply } = {}) {
    this.voice = voice;
    this.onActivity = onActivity;
    this.onSend = onSend;
    this.onReply = onReply;
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
    this._stick = true;        // follow a streaming answer until the reader scrolls up

    this.messages?.addEventListener('scroll', () => { this._stick = this._atBottom(); }, { passive: true });

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

  /**
   * Follow the conversation only when the reader is already at the end of it.
   *
   * The panel is tall enough now to read back through a long answer while the
   * next one streams in; yanking it to the bottom on every token would make
   * that impossible.
   */
  _atBottom() {
    const box = this.messages;
    if (!box) return false;
    return box.scrollHeight - box.scrollTop - box.clientHeight < 48;
  }

  _follow(force) {
    if (!this.messages) return;
    if (force || this._stick) this.messages.scrollTop = this.messages.scrollHeight;
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
    this._follow(true);
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
    this._stick = true;        // sending is asking to see the answer
    this.input.value = '';
    this._autosize();
    this.addMessage('user', text);

    // One turn can produce several bubbles: the model may say something,
    // call a tool, then carry on. Each run of text is its own bubble so the
    // transcript reads in the order things happened, instead of text jumping
    // around a tool card that arrived after it.
    let segment = this._newSegment();
    let answer = '';

    const appendText = (chunk) => {
      if (!segment) segment = this._newSegment();
      segment.text += chunk;
      segment.body.textContent = segment.text;
      answer += chunk;
    };

    // A tool card is about to land. Close off whatever has been said so far;
    // anything after the tool starts a fresh bubble.
    const closeSegment = () => {
      if (!segment) return;
      segment.bubble.classList.remove('is-streaming');
      if (!segment.text) segment.bubble.remove();
      segment = null;
    };

    this._setBusy(true);
    this.onActivity?.('thinking');

    this.stream = chatStream(
      { message: text, language, conversation_id: state.get('conversationId') || undefined },
      {
        onStart: (data) => {
          if (data.conversation_id) state.set({ conversationId: data.conversation_id });
        },
        onToken: (chunk) => {
          appendText(chunk);
          this._follow(false);
        },
        onToolCall: (data) => {
          closeSegment();
          this._toolCard(data);
        },
        onToolResult: (data) => {
          closeSegment();
          this._toolResult(data);
        },
        onToolApproval: (data) => {
          closeSegment();
          this._toolApproval(data);
        },
        onError: (data) => {
          if (!segment) segment = this._newSegment();
          segment.bubble.classList.add('msg-error');
          segment.body.textContent = tLLMError(data.code, data.detail);
          segment.text = ' ';     // keep the bubble: it carries the error
        },
        onDone: (data) => {
          const finalText = (data.text || answer).trim();
          // The text is already on screen, streamed. Only write it out again
          // if nothing arrived — a provider that answered in one piece.
          if (!answer && finalText) appendText(finalText);
          const last = segment || this._lastBubble();
          this.onReply?.(finalText);
          if (finalText && last) {
            this._addReplay(last.bubble, finalText, language);
            this._addTiming(last.bubble, data);
          }
          this.voice?.speak(finalText, language);
        },
        onClose: () => {
          closeSegment();
          this.stream = null;
          this._setBusy(false);
          if (state.get('activity') === 'thinking') this.onActivity?.('idle');
        },
      },
    );
  }

  /** A fresh, empty JARVIS bubble to stream the next run of text into. */
  _newSegment() {
    const body = this.addMessage('jarvis', '');
    const bubble = body.parentElement;
    bubble.classList.add('is-streaming');
    return { body, bubble, text: '' };
  }

  _lastBubble() {
    const bubbles = [...(this.messages?.querySelectorAll('.msg-jarvis') || [])];
    const bubble = bubbles[bubbles.length - 1];
    if (!bubble) return null;
    return { bubble, body: bubble.querySelector('.msg-body'), text: '' };
  }

  /**
   * How long the turn took, under the answer.
   *
   * Two numbers rather than one, because they mean different things: a large
   * time-to-first-word with a quick finish is the model loading or thinking,
   * while the reverse is JARVIS getting in the way. Without the split, a slow
   * answer is just "slow".
   */
  _addTiming(bubble, data) {
    if (!bubble || data.ms_total == null) return;
    const line = document.createElement('div');
    line.className = 'msg-timing';
    const first = data.ms_first_token;
    line.textContent = first == null
      ? t('chat.timingTotal', { total: (data.ms_total / 1000).toFixed(1) })
      : t('chat.timing', {
        first: (first / 1000).toFixed(1),
        total: (data.ms_total / 1000).toFixed(1),
      });
    bubble.appendChild(line);
  }

  /**
   * A card in the transcript for one tool call.
   *
   * It is created when the call starts and filled in when the result lands,
   * so the user watches the tool run rather than being told afterwards that
   * it did. A call that did not run says so, in its own colour.
   */
  _toolCard(data) {
    if (!this.messages) return null;
    this.messages.querySelector('.empty')?.remove();
    const card = document.createElement('div');
    card.className = 'tool-card is-running';
    card.dataset.tool = data.tool || data.name;

    const head = document.createElement('div');
    head.className = 'tool-head';
    const ico = document.createElement('span');
    ico.className = 'tool-ico';
    ico.setAttribute('aria-hidden', 'true');
    ico.textContent = data.writes ? '✎' : '⬡';
    const name = document.createElement('span');
    name.className = 'tool-name';
    name.textContent = data.tool || data.name;
    const state = document.createElement('span');
    state.className = 'tool-state';
    state.textContent = t('tool.running');
    head.append(ico, name, state);
    card.append(head);

    const args = data.arguments && Object.keys(data.arguments).length
      ? JSON.stringify(data.arguments)
      : '';
    if (args) {
      const pre = document.createElement('div');
      pre.className = 'tool-args';
      pre.textContent = args.slice(0, 300);
      card.appendChild(pre);
    }

    this.messages.appendChild(card);
    this._follow(true);
    return card;
  }

  _toolResult(data) {
    // Match the newest running card for this tool; fall back to a fresh one
    // so a result can never arrive with nowhere to show.
    const cards = [...this.messages.querySelectorAll('.tool-card.is-running')];
    const card = cards.reverse().find((c) => c.dataset.tool === (data.tool || data.name))
      || this._toolCard(data);
    if (!card) return;
    card.classList.remove('is-running');
    card.classList.add(data.ran ? 'is-done' : 'is-failed');
    const state = card.querySelector('.tool-state');
    if (state) {
      state.textContent = data.ran
        ? t('tool.ran', { ms: data.ms ?? 0 })
        : t('tool.didNotRun');
    }
    const out = document.createElement('pre');
    out.className = 'tool-out' + (data.ran ? '' : ' is-bad');
    out.textContent = (data.text || '').slice(0, 2000);
    card.appendChild(out);
    this._follow(false);
  }

  /** A mutating call: nothing has happened yet, and the user decides. */
  _toolApproval(data) {
    const card = [...this.messages.querySelectorAll('.tool-card')].pop();
    const box = document.createElement('div');
    box.className = 'tool-approve';

    const note = document.createElement('p');
    note.textContent = t('tool.approveNote', { tool: data.tool });
    box.appendChild(note);

    const row = document.createElement('div');
    row.className = 'tool-approve-actions';
    const yes = document.createElement('button');
    yes.type = 'button';
    yes.className = 'btn btn-primary btn-sm';
    yes.dataset.act = 'approve';
    yes.textContent = t('tool.approve');
    yes.addEventListener('click', async () => {
      yes.disabled = true;
      try {
        await api.chatToolApprove(data.pending.id, true);
        box.replaceChildren();
        const ok = document.createElement('p');
        ok.className = 'tool-approved';
        ok.textContent = t('tool.approved');
        box.appendChild(ok);
      } catch (err) {
        yes.disabled = false;
        toast(err?.message || t('error.network'), 'error');
      }
    });
    const no = document.createElement('button');
    no.type = 'button';
    no.className = 'btn btn-ghost btn-sm';
    no.dataset.act = 'reject';
    no.textContent = t('tool.reject');
    no.addEventListener('click', async () => {
      try { await api.chatToolApprove(data.pending.id, false); } catch { /* gone already */ }
      box.remove();
    });
    row.append(yes, no);
    box.appendChild(row);
    (card || this.messages).appendChild(box);
    this._follow(true);
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

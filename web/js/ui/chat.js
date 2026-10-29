/** Conversation panel: streaming answers, history, replay of speech. */
import { api, chatStream } from '../core/api.js';
import * as state from '../core/state.js';
import { t, tLLMError } from '../i18n/index.js';
import { toast } from './toast.js';

const PROVIDER_NAMES = { ollama: 'Ollama', openai: 'OpenAI', claude: 'Claude', gemini: 'Gemini' };

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

    this._initCollapse();
    this._initTabs();
  }

  /**
   * CHAT and FOLDERS share the panel.
   *
   * Switching views does not touch the collapse state or the input bar; it
   * swaps which child of the panel is showing, so the swipe-down gesture and
   * the dock behave identically in both.
   */
  _initTabs() {
    this.tabChat = document.getElementById('tab-chat');
    this.tabFolders = document.getElementById('tab-folders');
    this.foldersPanel = document.getElementById('folders-panel');
    if (!this.tabChat || !this.tabFolders) return;

    this.tabChat.addEventListener('click', () => this.showTab('chat'));
    this.tabFolders.addEventListener('click', () => this.showTab('folders'));
    document.getElementById('btn-folder-refresh')
      ?.addEventListener('click', () => this.folders?.refresh());
  }

  /** Give the panel a Folders view to show. Wired at boot. */
  attachFolders(folders) {
    this.folders = folders;
  }

  /**
   * Show a workspace file in the conversation.
   *
   * Tapping a file in the folder network drops its real contents into the
   * transcript rather than opening a separate viewer: it is already the
   * place JARVIS and the user read things together.
   */
  async openWorkspaceFile(path) {
    try {
      const file = await api.workspaceFile(path);
      this.showTab('chat');
      const body = this.addMessage('jarvis', '');
      body.textContent = `${file.path}\n\n${file.text}`;
      body.parentElement?.classList.add('msg-file');
      this._follow(true);
    } catch (err) {
      toast(String(err?.message || err), 'error');
    }
  }

  showTab(which) {
    const folders = which === 'folders';
    this.tab = which;
    if (this.messages) this.messages.hidden = folders || !this.historyList?.hidden;
    if (this.foldersPanel) this.foldersPanel.hidden = !folders;
    if (this.historyList && folders) this.historyList.hidden = true;
    this.tabChat?.classList.toggle('is-on', !folders);
    this.tabFolders?.classList.toggle('is-on', folders);
    this.tabChat?.setAttribute('aria-selected', String(!folders));
    this.tabFolders?.setAttribute('aria-selected', String(folders));
    // Only one of them needs to be animating.
    if (folders) {
      this.folders?.refresh();
      if (this.collapsed) this.setCollapsed(false);
    } else {
      this.folders?.stop();
    }
  }

  // ---------------------------------------------------------------- collapse

  /**
   * Swipe the conversation down to clear the screen, up to bring it back.
   *
   * Pointer events rather than touch events: the same code then answers to a
   * finger on Android and to a mouse, which is also what makes the gesture
   * testable. The grip carries `touch-action: none` so the browser does not
   * claim the vertical drag for a page scroll before we see it.
   */
  _initCollapse() {
    this.panel = document.getElementById('chat-panel');
    this.grip = document.getElementById('chat-grip');
    this.app = document.getElementById('app');
    if (!this.panel || !this.app) return;

    // The grip's height is the collapsed height; measure it rather than
    // hard-coding it twice.
    const gripHeight = () => Math.round(this.grip?.getBoundingClientRect().height || 22);

    let start = 0;
    let startHeight = 0;
    let dragging = false;
    let moved = 0;
    let fromList = false;

    const begin = (event, viaList) => {
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      dragging = true;
      fromList = viaList;
      moved = 0;
      start = event.clientY;
      startHeight = this.panel.getBoundingClientRect().height;
      this.panel.classList.add('is-dragging');
      try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* fine */ }
    };

    const move = (event) => {
      if (!dragging) return;
      const delta = event.clientY - start;
      moved = delta;
      // A drag that began in the message list only counts while the list is
      // at the very top and the finger is going down — otherwise it is a
      // scroll and must be left alone.
      if (fromList && (delta < 0 || this.messages.scrollTop > 0)) {
        this._endDrag();
        dragging = false;
        return;
      }
      event.preventDefault();
      const min = gripHeight();
      const max = this._maxPanelHeight();
      // One formula serves both directions: dragging down (positive delta)
      // shrinks the panel, dragging up grows it, whichever state it is in.
      const next = Math.max(min, Math.min(max, startHeight - delta));
      this.panel.style.maxHeight = next + 'px';
    };

    const end = () => {
      if (!dragging) return;
      dragging = false;
      this.panel.classList.remove('is-dragging');
      // A short drag is a tap or a wobble; leave the panel as it was, but
      // animate it back from where the finger left it.
      const THRESHOLD = 44;
      const target = Math.abs(moved) < THRESHOLD ? this.collapsed : moved > 0;
      // The inline height from the drag is the animation's starting point,
      // so it is left in place for setCollapsed to transition away from.
      if (target === this.collapsed) {
        const to = target ? (this.gripH || 22) : this._maxPanelHeight();
        requestAnimationFrame(() => {
          if (this.panel) this.panel.style.maxHeight = to + 'px';
        });
        clearTimeout(this._settle);
        this._settle = setTimeout(() => {
          if (this.panel) this.panel.style.maxHeight = '';
        }, 340);
        return;
      }
      this.setCollapsed(target);
    };

    this.grip?.addEventListener('pointerdown', (e) => begin(e, false));
    this.grip?.addEventListener('pointermove', move);
    this.grip?.addEventListener('pointerup', end);
    this.grip?.addEventListener('pointercancel', end);
    // A tap on the grip toggles. Guarded so the end of a drag is not also
    // read as a tap.
    this.grip?.addEventListener('click', () => {
      if (Math.abs(moved) >= 8) { moved = 0; return; }
      this.setCollapsed(!this.collapsed);
    });

    // Swiping within the conversation itself, but only from the top of it.
    this.messages?.addEventListener('pointerdown', (e) => {
      if (e.pointerType === 'mouse') return;      // a mouse scrolls with the wheel
      if (this.messages.scrollTop > 0) return;    // mid-scroll: not our gesture
      begin(e, true);
    });
    this.messages?.addEventListener('pointermove', move);
    this.messages?.addEventListener('pointerup', end);
    this.messages?.addEventListener('pointercancel', end);

    // While collapsed the panel is only the grip, so an upward swipe there
    // is the way back.
    this.panel.addEventListener('pointerdown', (e) => {
      if (!this.collapsed || e.target === this.grip || this.grip?.contains(e.target)) return;
      begin(e, false);
    });
    this.panel.addEventListener('pointermove', move);
    this.panel.addEventListener('pointerup', end);

    this.gripH = gripHeight();
    this.app.style.setProperty('--chat-grip-h', this.gripH + 'px');
    // The remembered state is applied without animating: a panel that slides
    // shut on every load would look like a glitch, not a preference.
    this.setCollapsed(this._remembered(), { silent: true, animate: false });
  }

  _endDrag() {
    this.panel?.classList.remove('is-dragging');
    if (this.panel) this.panel.style.maxHeight = '';
  }

  /** How tall the panel may grow: what the workspace can actually give it. */
  _maxPanelHeight() {
    const workspace = this.panel?.parentElement;
    if (!workspace) return 2000;
    const others = [...workspace.children]
      .filter((el) => el !== this.panel && !el.hidden)
      .reduce((total, el) => total + el.getBoundingClientRect().height, 0);
    return Math.max(80, workspace.getBoundingClientRect().height - others - 10);
  }

  get collapsed() {
    return this.app?.dataset.chat === 'collapsed';
  }

  setCollapsed(collapsed, { silent = false, animate = true } = {}) {
    if (!this.app) return;
    const wasCollapsed = this.collapsed;
    this.app.dataset.chat = collapsed ? 'collapsed' : 'open';

    // Animate between the panel's *real* heights.
    //
    // The stylesheet's open state is `max-height: 2000px`, because a
    // transition cannot start from `none`. Left alone, collapsing animates
    // 2000px down to 22px — and since the panel is only ~420px tall, nothing
    // moves for the first two thirds of the slide and then it drops all at
    // once. Pinning the start to the measured height makes every millisecond
    // of the transition visible movement.
    if (animate && this.panel && wasCollapsed !== collapsed) {
      const from = this.panel.getBoundingClientRect().height;
      const to = collapsed
        ? (this.gripH || 22)
        : this._maxPanelHeight();
      this.panel.style.maxHeight = from + 'px';
      void this.panel.offsetHeight;          // flush, so the next value animates
      requestAnimationFrame(() => {
        if (!this.panel) return;
        this.panel.style.maxHeight = to + 'px';
      });
      clearTimeout(this._settle);
      this._settle = setTimeout(() => {
        // Hand the panel back to the stylesheet: inline heights would stop
        // an open panel growing with the conversation.
        if (this.panel) this.panel.style.maxHeight = '';
      }, 340);
    }

    if (this.grip) {
      this.grip.setAttribute('aria-expanded', String(!collapsed));
      const key = collapsed ? 'chat.expand' : 'chat.collapse';
      this.grip.setAttribute('aria-label', t(key));
      this.grip.title = t(key);
    }
    if (!collapsed) this._follow(true);
    if (!silent) {
      try { localStorage.setItem('jarvis.chatCollapsed', collapsed ? '1' : '0'); } catch { /* private mode */ }
    }
  }

  _remembered() {
    try { return localStorage.getItem('jarvis.chatCollapsed') === '1'; } catch { return false; }
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
    // Asking a question is asking to see the answer: a collapsed panel comes
    // back rather than streaming a reply into a closed drawer.
    if (this.collapsed) this.setCollapsed(false);
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
    let turnCost = 0;

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
        onRoute: (data) => {
          // Who answers and why, above the answer; a fallback gets its own
          // line, so a switch of brain mid-turn is never silent.
          closeSegment();
          this._routeLine(data);
          segment = this._newSegment();     // the answer streams in below it
        },
        onCost: (data) => {
          turnCost += Number(data.usd || 0);
        },
        onCodeRequest: (data) => {
          closeSegment();
          this._codeRequestCard(data.request || {});
        },
        onWorkspace: (data) => {
          // Move the highlight to the folder this turn is about. Silent,
          // because the user did not ask to change folders — JARVIS did.
          this.folders?.setActive(data.active || '', { silent: true });
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
            this._addTiming(last.bubble, { ...data, usd: data.usd ?? turnCost });
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
    let text = first == null
      ? t('chat.timingTotal', { total: (data.ms_total / 1000).toFixed(1) })
      : t('chat.timing', {
        first: (first / 1000).toFixed(1),
        total: (data.ms_total / 1000).toFixed(1),
      });
    // Who answered, and on a paid brain what the turn cost (an estimate from
    // the provider's own token counts).
    if (data.provider) {
      const name = PROVIDER_NAMES[data.provider] || data.provider;
      text += ' · ' + name + (data.model ? ' ' + data.model : '');
      if (data.usd) text += ' · ' + t('chat.cost', { usd: Number(data.usd).toFixed(4) });
    }
    line.textContent = text;
    bubble.appendChild(line);
  }

  /** "Claude — chosen in AI Connections", or a fallback, above the answer. */
  _routeLine(data) {
    if (!this.messages || !data.provider) return;
    const line = document.createElement('div');
    line.className = 'msg-route' + (data.fallback ? ' is-fallback' : '');
    const name = PROVIDER_NAMES[data.provider] || data.provider;
    line.textContent = data.fallback
      ? t('chat.routeFallback', { name, reason: data.reason || '' })
      : t('chat.route', { name, reason: data.reason || '' });
    this.messages.appendChild(line);
    this._follow(false);
  }

  /** A coding task JARVIS queued: it has not run, and this says where to approve it. */
  _codeRequestCard(request) {
    if (!this.messages) return;
    const card = document.createElement('div');
    card.className = 'tool-card is-pending code-request-card';
    const head = document.createElement('div');
    head.className = 'tool-head';
    const ico = document.createElement('span');
    ico.className = 'tool-ico';
    ico.setAttribute('aria-hidden', 'true');
    ico.textContent = '⌘';
    const name = document.createElement('span');
    name.className = 'tool-name';
    name.textContent = 'Claude Code';
    const st = document.createElement('span');
    st.className = 'tool-state';
    st.textContent = t('chat.codeQueued', { mode: request.mode === 'edit' ? t('conn.codeEdit') : t('conn.codePlan') });
    head.append(ico, name, st);
    const prompt = document.createElement('div');
    prompt.className = 'tool-args';
    prompt.textContent = (request.prompt || '').slice(0, 300);
    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'btn btn-primary btn-sm';
    open.dataset.act = 'review-code';
    open.textContent = t('chat.codeReview');
    open.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('jarvis:open-view', { detail: 'connections' }));
    });
    card.append(head, prompt, open);
    this.messages.appendChild(card);
    this._follow(true);
  }

  /** Images and video a tool returned, saved in the workspace and shown here. */
  _mediaInto(target, media) {
    for (const item of media || []) {
      if (!item.url) continue;
      let el;
      if (item.kind === 'image') {
        el = document.createElement('img');
        el.alt = item.path;
        el.loading = 'lazy';
      } else if (item.kind === 'video' || item.kind === 'audio') {
        el = document.createElement(item.kind);
        el.controls = true;
        el.preload = 'metadata';
      } else continue;
      el.className = 'tool-media';
      el.src = item.url;
      target.appendChild(el);
      const where = document.createElement('div');
      where.className = 'tool-media-path';
      where.textContent = t('chat.savedTo', { path: item.path });
      target.appendChild(where);
    }
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
    this._mediaInto(card, data.media);
    this._follow(false);
  }

  /**
   * A held call: nothing has happened yet, and the user decides.
   *
   * "Approve and run" runs exactly this call, once, and shows what came
   * back. A call that spends credits (OpenArt) says so and asks again before
   * spending — the backend refuses it without that confirmation too.
   */
  _toolApproval(data) {
    const card = [...this.messages.querySelectorAll('.tool-card')].pop();
    const box = document.createElement('div');
    box.className = 'tool-approve' + (data.billed ? ' is-billed' : '');

    const note = document.createElement('p');
    note.textContent = data.billed
      ? t('tool.spendNote', { tool: data.tool, name: data.connection || '' })
      : t('tool.approveRunNote', { tool: data.tool });
    box.appendChild(note);

    const row = document.createElement('div');
    row.className = 'tool-approve-actions';
    const yes = document.createElement('button');
    yes.type = 'button';
    yes.className = 'btn btn-primary btn-sm';
    yes.dataset.act = 'approve';
    yes.textContent = data.billed ? t('tool.spendRun') : t('tool.approveRun');
    yes.addEventListener('click', async () => {
      if (data.billed && !window.confirm(t('tool.spendConfirm', { tool: data.tool, name: data.connection || '' }))) {
        return;
      }
      yes.disabled = true;
      no.disabled = true;
      const status = document.createElement('p');
      status.className = 'tool-approved';
      status.textContent = t('tool.runningNow');
      row.replaceWith(status);
      try {
        const result = await api.chatToolRun(data.pending.id, !!data.billed);
        status.textContent = result.ran ? t('tool.ran', { ms: result.ms ?? 0 }) : t('tool.didNotRun');
        status.className = result.ran ? 'tool-approved' : 'tool-failed';
        const out = document.createElement('pre');
        out.className = 'tool-out' + (result.ran ? '' : ' is-bad');
        out.textContent = (result.text || '').slice(0, 2000);
        box.appendChild(out);
        this._mediaInto(box, result.media);
        this._follow(true);
      } catch (err) {
        status.textContent = err?.message || t('error.network');
        status.className = 'tool-failed';
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

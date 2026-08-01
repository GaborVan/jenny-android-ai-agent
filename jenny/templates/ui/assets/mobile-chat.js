/** Mobile Chat Controller — full-featured chat with markdown, thinking, tool calls. */

import { wsManager } from './shared/ws-manager.js';
import { api } from './shared/api-client.js';
import { escapeHtml, showToast } from './shared/utils.js';
import { sessionManager } from './shared/session-manager.js';
import { ImageHandler } from './shared/image-handler.js';
import { openImageLightbox } from './shared/image-lightbox.js';
import { i18n } from './shared/i18n.js';
import { getProviderBrand } from './shared/provider-brand.js';

const TOOL_ICONS = {
  start: 'ti-loader-2',
  end: 'ti-check',
  error: 'ti-x',
};

function initMarked() {
  if (window._markedReady) return;
  if (typeof marked === 'undefined') return;

  const renderer = new marked.Renderer();

  renderer.code = function ({ text, lang }) {
    const hasHljs = typeof hljs !== 'undefined';
    const language = lang && hasHljs && hljs.getLanguage(lang) ? lang : null;
    let highlighted;
    try {
      if (hasHljs) {
        highlighted = language
          ? hljs.highlight(text, { language }).value
          : hljs.highlightAuto(text).value;
      } else {
        highlighted = escapeHtml(text);
      }
    } catch {
      highlighted = escapeHtml(text);
    }
    const langLabel = language || 'text';
    return `<div class="chat-code-block">` +
      `<div class="chat-code-header">` +
        `<span class="chat-code-lang">${langLabel}</span>` +
        `<button class="chat-code-copy" type="button">${i18n.t('chat.copy')}</button>` +
      `</div>` +
      `<pre><code class="hljs language-${langLabel}">${highlighted}</code></pre>` +
    `</div>`;
  };

  marked.setOptions({
    renderer,
    gfm: true,
    breaks: true,
  });

  window._markedReady = true;
}

// C1: copy handled via event-delegation (see setupEventListeners) instead of
// an inline onclick, which DOMPurify strips from the sanitized markdown.
function copyCodeFromButton(btn) {
  const pre = btn.closest('.chat-code-block')?.querySelector('pre code');
  if (!pre) return;
  const text = pre.textContent;
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = i18n.t('chat.copied');
    btn.classList.add('copied');
    setTimeout(() => {
      btn.textContent = i18n.t('chat.copy');
      btn.classList.remove('copied');
    }, 2000);
  });
}

function renderMarkdown(text) {
  initMarked();
  if (typeof marked !== 'undefined') {
    try {
      // C1: sanitize model-generated HTML before it reaches innerHTML.
      // DOMPurify defaults already preserve highlight.js `class`, GFM tables,
      // <a href> (http/https/relative), <img>, and <pre class="mermaid">,
      // so no ADD_TAGS/ADD_ATTR are required.
      // Fail SAFE, not open: if the sanitizer vendor failed to load, degrade to
      // escaped plain text rather than injecting unsanitized HTML.
      if (typeof DOMPurify === 'undefined') return escapeHtml(text);
      return DOMPurify.sanitize(marked.parse(text));
    } catch (e) {
      console.error('Markdown parse error:', e);
      return escapeHtml(text);
    }
  }
  return escapeHtml(text);
}

function renderKaTeX(container) {
  if (typeof renderMathInElement === 'function') {
    try {
      renderMathInElement(container, {
        delimiters: [
          { left: '$$', right: '$$', display: true },
          { left: '$', right: '$', display: false },
          { left: '\\[', right: '\\]', display: true },
          { left: '\\(', right: '\\)', display: false },
        ],
        throwOnError: false,
      });
    } catch (e) {
      console.warn('KaTeX error:', e);
    }
  }
}

export class ChatController {
  constructor() {
    this.chatArea = document.getElementById('chat-area');
    this.identityEl = null;
    this.identityStatus = null;
    this._ensureIdentity();
    this.input = document.getElementById('chat-input');
    this.sendBtn = document.getElementById('btn-send');
    this.secondaryActions = document.getElementById('secondary-actions');

    this._deltaBuffer = '';
    this._reasoningBuffer = '';
    // Rendering dello streaming coalizzato a un frame: i delta aggiornano
    // sempre il buffer, ma il re-parse markdown (costoso, O(n) sul buffer
    // intero) avviene al massimo una volta per requestAnimationFrame. Evita
    // di saturare il main thread — e quindi di affamare il setInterval che
    // anima la mascotte — quando l'agente scrive muri di testo.
    this._pendingFrame = null;
    this._deltaDirty = false;
    this._reasoningDirty = false;
    this._currentMsg = null;
    this._currentThinking = null;
    this._currentContent = null;
    this._toolStates = {};
    this._goalBanner = null;
    this._goalTimer = null;

    this.historyCursor = null;
    this.isLoadingHistory = false;
    this.hasMoreHistory = true;
    this._initialHistoryLoaded = false;

    this.imageHandler = new ImageHandler();
    this.imageHandler.onChange = (images) => this._renderAttachPreview(images);

    this._voiceTimerInterval = null;

    this._autoScroll = true;
    this._userTouching = false;
    this._scrollThreshold = 60;
    this._unreadCount = 0;
    this._fabEl = document.getElementById('chat-scroll-fab');
    this._wsListenersBound = false;
    this._active = false;

    this._runtimeModel = null;
    this._sessionInfoPopover = null;
    this._sessionInfoTimer = null;
    this._fileEditPaths = new Map();

    i18n.load(i18n.locale).then(() => this._updatePlaceholders());
    i18n.onLocaleChange(() => this._updatePlaceholders());

    this.setupEventListeners();
    this.setupInfiniteScroll();
    this.setupWebSocket();
    this.ready = this._initOnSessionReady();
    this._initSessionInfo();
  }

  /** Identity line — first scrollable element of the chat (replaces the fixed header). */
  _ensureIdentity() {
    if (this.identityEl && this.chatArea.contains(this.identityEl)) return;
    const el = document.createElement('div');
    el.className = 'chat-identity';
    el.innerHTML = '<span class="chat-identity-flower">✿</span>' +
      '<span class="chat-identity-name">' + i18n.t('chat.jenny') + '</span>' +
      '<span class="chat-identity-status"></span>' +
      '<span class="chat-identity-label"></span>';
    this.chatArea.insertBefore(el, this.chatArea.firstChild);
    this.identityEl = el;
    this.identityStatus = el.querySelector('.chat-identity-status');
    this.identityLabel = el.querySelector('.chat-identity-label');
  }

  _insertAtTop(node) {
    this._ensureIdentity();
    this.chatArea.insertBefore(node, this.identityEl.nextSibling);
  }

  _setConnectionStatus(connected) {
    this._ensureIdentity();
    this.identityStatus.classList.toggle('on', connected);
    this.identityStatus.classList.toggle('off', !connected);
    if (this.identityLabel) this.identityLabel.textContent = connected ? i18n.t('chat.online') : i18n.t('chat.offline');
  }

  _updatePlaceholders() {
    const input = document.getElementById('chat-input');
    if (input) input.placeholder = i18n.t('chat.placeholder');
    const attachBtn = document.getElementById('btn-attach');
    if (attachBtn) attachBtn.title = i18n.t('chat.attach');
  }

  setupEventListeners() {
    this.sendBtn.addEventListener('click', () => this.sendMessage());
    document.getElementById('btn-attach').addEventListener('click', () => {
      this.imageHandler.trigger();
    });


    // Textarea auto-resize + send enable/disable + hide secondary actions
    this.input.addEventListener('input', () => {
      this._autoResize();
      this._updateSendState();
      this._updateActions();
    });

    this.input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.sendMessage();
      }
    });

    // Sticky bottom stile WhatsApp: l'autoscroll segue lo stream solo se l'utente
    // è a fondo chat. Nessun guard sugli scroll programmatici: assegnano scrollTop
    // al fondo in modo istantaneo, quindi il loro evento ricalcola comunque
    // _autoScroll = true e non serve distinguerli da quelli dell'utente.
    this.chatArea.addEventListener('scroll', () => {
      this._autoScroll = this._isNearBottom();
      this._updateScrollFab();
    }, { passive: true });

    // Mentre il dito è giù non va MAI eseguito uno scroll programmatico
    // (combatterebbe il gesto: durante lo streaming il flush gira ogni frame).
    this.chatArea.addEventListener('touchstart', () => {
      this._userTouching = true;
    }, { passive: true });
    const onTouchDone = () => {
      this._userTouching = false;
      this._autoScroll = this._isNearBottom();
      this._updateScrollFab();
    };
    this.chatArea.addEventListener('touchend', onTouchDone, { passive: true });
    this.chatArea.addEventListener('touchcancel', onTouchDone, { passive: true });

    // Rotella/tastiera (Titan 2 emette wheel dalla rotella capacitiva): un colpo
    // verso l'alto stacca subito, senza aspettare che superi la soglia dei 60px.
    this.chatArea.addEventListener('wheel', (e) => {
      if (e.deltaY < 0) {
        this._autoScroll = false;
        this._updateScrollFab();
      }
    }, { passive: true });

    if (this._fabEl) {
      this._fabEl.addEventListener('click', () => {
        this._autoScroll = true;
        this._unreadCount = 0;
        this.scrollToBottom(true);
      });
    }

    // Type-ahead focus: su device con tastiera fisica (Titan 2) il focus si perde
    // facilmente dall'input (tap su bolla/link/pulsante, scroll, ritorno da un'altra
    // vista) e i caratteri digitati vanno persi. Se l'utente inizia a scrivere un
    // carattere stampabile mentre la chat è attiva e il focus non è già in un campo
    // editabile, riportiamo il focus sull'input così il tasto ci finisce dentro.
    // Nota: agisce SOLO in reazione a un tasto fisico premuto, quindi non forza mai
    // la tastiera virtuale a comparire (non c'è keydown senza input già a fuoco).
    document.addEventListener('keydown', (e) => this._maybeTypeAheadFocus(e));

    // C1: delegated copy handler for code-block buttons (replaces inline onclick).
    this.chatArea.addEventListener('click', (e) => {
      const btn = e.target.closest('.chat-code-copy');
      if (btn && this.chatArea.contains(btn)) { copyCodeFromButton(btn); return; }
      // Tap su un'immagine (media allegato o immagine markdown inline) → lightbox.
      const img = e.target.closest('img');
      if (img && this.chatArea.contains(img)) this._openLightbox(img.currentSrc || img.src, img.alt || '');
    });
  }

  /** Overlay fullscreen per un'immagine: tap-per-zoom, tap sullo sfondo / Esc per chiudere. */
  _openLightbox(src, alt) {
    openImageLightbox(src, { alt, closeLabel: i18n.t('chat.close') || 'Close' });
  }

  /** Renderer condiviso degli allegati media (live + history), per tipo:
      image → <img> (lightbox via delegazione), video → <video>, altro → chip
      file che si apre col viewer di sistema via bridge nativo. */
  _renderMediaAttachments(msgNode, entries) {
    if (!entries?.length) return;
    const media = document.createElement('div');
    media.className = 'chat-media';
    for (const raw of entries) {
      const entry = typeof raw === 'string' ? { url: raw } : raw;
      if (!entry.url) continue;
      const name = entry.name || '';
      const kind = entry.kind || this._mediaKindFromName(name);
      if (kind === 'image') {
        const img = document.createElement('img');
        img.src = entry.url;
        img.loading = 'lazy';
        img.alt = name;
        img.title = name;
        media.appendChild(img);
      } else if (kind === 'video') {
        const video = document.createElement('video');
        video.src = entry.url;
        video.controls = true;
        video.preload = 'metadata';
        video.title = name;
        media.appendChild(video);
      } else {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'chat-file-chip';
        chip.innerHTML = '<span class="chat-file-chip-icon">📄</span>' +
          '<span class="chat-file-chip-name"></span>';
        chip.querySelector('.chat-file-chip-name').textContent = name || 'file';
        chip.addEventListener('click', () => this._openMediaFile(entry));
        media.appendChild(chip);
      }
    }
    if (media.childElementCount) msgNode.appendChild(media);
  }

  _mediaKindFromName(name) {
    const ext = (name.match(/\.([a-z0-9]+)$/i) || [])[1]?.toLowerCase() || '';
    if (['png', 'jpg', 'jpeg', 'webp', 'gif', 'svg'].includes(ext)) return 'image';
    if (['mp4', 'mov', 'webm'].includes(ext)) return 'video';
    return 'file';
  }

  /** Apre un allegato non renderizzabile inline: prima il bridge nativo
      Android (ACTION_VIEW con viewer di sistema), altrimenti l'URL firmato
      in una nuova scheda (fallback browser desktop). */
  _openMediaFile(entry) {
    try {
      if (entry.path && window.JennyNative?.openFile && window.JennyNative.openFile(entry.path)) {
        return;
      }
    } catch (e) {
      console.warn('Native openFile failed:', e);
    }
    window.open(entry.url, '_blank');
  }

  _autoResize() {
    this.input.style.height = 'auto';
    this.input.style.height = this.input.scrollHeight + 'px';
  }

  _updateSendState() {
    const hasText = this.input.value.trim().length > 0;
    this.sendBtn.disabled = !hasText;
    if (hasText) {
      this.sendBtn.classList.add('enabled');
    } else {
      this.sendBtn.classList.remove('enabled');
    }
  }

  _updateActions() {
    const hasText = this.input.value.trim().length > 0;
    this.secondaryActions.classList.toggle('hidden', hasText);
  }

  /**
   * Riporta il focus sull'input di chat quando l'utente inizia a digitare "nel
   * vuoto". Chiamato dall'handler keydown globale (vedi setupEventListeners).
   */
  _maybeTypeAheadFocus(e) {
    if (!this._active) return;
    // Solo caratteri stampabili singoli. Le tastiere fisiche (Titan via bbkeyboard)
    // possono emettere keydown con e.key undefined: il guard length===1 li scarta,
    // come pure Enter/Escape/frecce/ecc.
    if (!e.key || e.key.length !== 1) return;
    // Lo spazio è escluso: non si inizia mai un messaggio con uno spazio (verrebbe
    // comunque trimmato) ed è riservato a interazioni future con la mascotte.
    if (e.key === ' ') return;
    // I combo con modificatori sono scorciatoie, non testo.
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    // Non rubare il focus se si sta già scrivendo altrove o c'è un dialog modale
    // aperto (i dialog nativi intrappolano comunque il focus, ma la guardia è esplicita).
    const el = document.activeElement;
    if (el === this.input) return;
    if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' ||
               el.tagName === 'SELECT' || el.isContentEditable)) return;
    if (document.querySelector('dialog[open]')) return;
    // focus() sincrono dentro il keydown: Chromium (WebView) recapita l'inserimento
    // del carattere sull'elemento appena messo a fuoco, quindi il tasto non va perso.
    this.input.focus();
  }

  setupWebSocket() {
    // Guard di registrazione singola: i listener vengono bindati una sola volta per tutta
    // la vita del controller (nessun teardown/re-setup ai cambi vista, vedi deactivate()).
    if (this._wsListenersBound) return;
    this._wsListenersBound = true;
    this._onChatMessage = (e) => this.handleMessage(e.detail);
    this._onChatOpen = () => this._setConnectionStatus(true);
    this._onChatClose = () => this._setConnectionStatus(false);
    wsManager.addEventListener('chat:message', this._onChatMessage);
    wsManager.addEventListener('chat:open', this._onChatOpen);
    wsManager.addEventListener('chat:close', this._onChatClose);
    wsManager.connectChat();
  }

  setupInfiniteScroll() {
    this.chatArea.addEventListener('scroll', () => {
      if (this.chatArea.scrollTop === 0 &&
          !this.isLoadingHistory &&
          this.hasMoreHistory) {
        this.loadMoreHistory();
      }
    });
  }

  async _initOnSessionReady() {
    try {
      await sessionManager.init();
      if (sessionManager.currentKey) {
        await this.loadInitialHistory();
      }
    } catch (err) {
      console.error('Session init failed:', err);
    }
  }

  /* Valore iniziale del modello runtime dal payload di bootstrap: senza
     questo la riga "Modello" del popover Info sessione resta "—" finché non
     avviene uno switch a runtime. Un runtime_model_updated successivo vince. */
  _initRuntimeModelFromBootstrap() {
    if (this._runtimeModel) return;
    const info = api.getBootstrapInfo();
    if (!info?.model_name) return;
    this._runtimeModel = {
      provider: info.provider || null,
      model: info.model_name,
      preset: null,
    };
    this._updateSessionInfoModel();
  }

  /* Butta la vista renderizzata e forza il reload dello storico al prossimo
     activate() — usato quando la sessione cambia fuori da questa vista
     (es. uno scambio nella minichat di Jenny). */
  invalidateHistory() {
    this.chatArea.innerHTML = '';
    this.identityEl = null;
    this._ensureIdentity();
    this.historyCursor = null;
    this.hasMoreHistory = true;
    this._initialHistoryLoaded = false;
  }

  async loadInitialHistory() {
    if (this._initialHistoryLoaded) return;
    this._initialHistoryLoaded = true;
    try {
      if (!sessionManager.currentKey) {
        this.hasMoreHistory = false;
        return;
      }
      await api.bootstrap();
      this._initRuntimeModelFromBootstrap();
      const thread = await sessionManager.loadThread(sessionManager.currentKey, 160);
      this._renderThreadMessages(thread.messages || []);
      this.historyCursor = thread.page?.before_cursor || null;
      this.hasMoreHistory = thread.page?.has_more_before !== false;
      this.scrollToBottom(true);
    } catch (err) {
      this._initialHistoryLoaded = false;
      console.error('Failed to load history:', err);
    }
  }

  async loadMoreHistory() {
    if (this.isLoadingHistory || !this.hasMoreHistory) return;
    if (!sessionManager.currentKey) return;
    this.isLoadingHistory = true;
    const scrollHeightBefore = this.chatArea.scrollHeight;
    try {
      const thread = await sessionManager.loadThread(sessionManager.currentKey, 120, this.historyCursor);
      const messages = thread.messages || [];
      this._renderThreadMessagesToTop(messages);
      this.historyCursor = thread.page?.before_cursor || null;
      this.hasMoreHistory = thread.page?.has_more_before !== false;
      const scrollHeightAfter = this.chatArea.scrollHeight;
      this.chatArea.scrollTop = scrollHeightAfter - scrollHeightBefore;
    } catch (err) {
      console.error('Failed to load more history:', err);
    } finally {
      this.isLoadingHistory = false;
    }
  }

  // Ricostruisce l'array di turni normalizzati dai messaggi persistiti.
  // Voce user: {user:true, text, origin}; turno assistant:
  // {turnId, toolEvents, reasoning, content, fileEdits, media, latencyMs?}.
  _buildTurns(messages) {
    const turns = [];
    let currentTurn = null;
    for (const msg of messages) {
      if (msg.session_boundary) {
        // Confine di contesto (/new): chiude il turno in corso e si rende da
        // sé, senza diventare una bolla dell'assistente.
        if (currentTurn) turns.push(currentTurn);
        currentTurn = null;
        turns.push({ boundary: true, text: msg.text || msg.content || '' });
        continue;
      }
      const role = msg.role || (msg.kind === 'user' ? 'user' : 'assistant');
      if (role === 'user') {
        if (currentTurn) turns.push(currentTurn);
        currentTurn = null;
        turns.push({
          user: true,
          text: msg.text || msg.content || '',
          origin: msg.origin,
          media: Array.isArray(msg.media) ? msg.media : [],
        });
        continue;
      }
      const turnId = msg.turnId || msg.turn_id;
      if (!currentTurn || currentTurn.turnId !== turnId) {
        if (currentTurn) turns.push(currentTurn);
        currentTurn = { turnId, toolEvents: [], reasoning: '', content: '', fileEdits: [], media: [] };
      }
      if (msg.toolEvents || msg.tool_events) {
        currentTurn.toolEvents.push(...(msg.toolEvents || msg.tool_events));
      }
      if (msg.fileEdits || msg.file_edits) {
        currentTurn.fileEdits.push(...(msg.fileEdits || msg.file_edits));
      }
      if (Array.isArray(msg.media) && msg.media.length) {
        currentTurn.media.push(...msg.media);
      }
      const text = msg.text || msg.content || '';
      if (text) {
        if (msg.kind === 'trace' || msg.role === 'tool') {
          if (!currentTurn.toolEvents.length) {
            currentTurn.content += (currentTurn.content ? '\n\n' : '') + text;
          }
        } else {
          currentTurn.content += (currentTurn.content ? '\n\n' : '') + text;
        }
      }
      if (msg.reasoning) {
        currentTurn.reasoning += (currentTurn.reasoning ? '\n\n' : '') + msg.reasoning;
      }
      if (msg.latencyMs != null) {
        currentTurn.latencyMs = msg.latencyMs;
      }
    }
    if (currentTurn) turns.push(currentTurn);
    return turns;
  }

  _renderThreadMessages(messages) {
    for (const turn of this._buildTurns(messages)) {
      if (turn.boundary) {
        this._appendSessionBoundary(turn.text);
      } else if (turn.user) {
        this.addCompletedMessage(turn.text, 'user', turn.origin, turn.media);
      } else {
        this._flushPersistedTurn(turn);
      }
    }
  }

  _renderThreadMessagesToTop(messages) {
    for (const turn of this._buildTurns(messages).reverse()) {
      if (turn.boundary) {
        this._appendSessionBoundary(turn.text, true);
      } else if (turn.user) {
        this.addCompletedMessageToTop(turn.text, 'user', turn.origin, turn.media);
      } else {
        this._flushPersistedTurn(turn, true);
      }
    }
  }

  /* Separatore di contesto reso da /new. Non è una bolla: segna il punto in
     cui il modello riparte da zero, mentre tutto ciò che sta sopra resta
     leggibile — /new azzera la sessione, non il transcript. textContent e non
     innerHTML: il testo arriva dal server e non deve poter iniettare markup. */
  _appendSessionBoundary(text, toTop = false) {
    const el = document.createElement('div');
    el.className = 'chat-session-boundary';
    const label = document.createElement('span');
    label.textContent = text || 'New session started.';
    el.appendChild(label);
    if (toTop) {
      this._insertAtTop(el);
    } else {
      this.chatArea.appendChild(el);
    }
  }

  _flushPersistedTurn(turn, toTop = false) {
    if (!turn) return;
    const node = this._createBaseMessage('assistant');
    let hasContent = false;

    if (turn.toolEvents.length) {
      this._renderToolEvents(turn.toolEvents, node);
      hasContent = true;
    }

    if (turn.fileEdits.length) {
      this._appendFileEdits(node, turn.fileEdits);
      hasContent = true;
    }

    if (turn.reasoning.trim()) {
      this._appendReasoningBlock(node, turn.reasoning.trim(), true);
      hasContent = true;
    }

    if (turn.content.trim()) {
      const content = node.querySelector('.chat-content');
      if (content) {
        content.innerHTML = renderMarkdown(turn.content.trim());
        renderKaTeX(content);
        this._makeFilePathsClickable(content);
      }
      hasContent = true;
    }

    if (turn.media?.length) {
      this._renderMediaAttachments(node, turn.media);
      hasContent = true;
    }

    if (!hasContent) return;

    this._appendLatency(node, turn.latencyMs);

    if (toTop) {
      this._insertAtTop(node);
    } else {
      this.chatArea.appendChild(node);
    }
  }

  _createBaseMessage(role) {
    const msg = document.createElement('div');
    msg.className = `chat-msg chat-msg-${role === 'user' ? 'user' : 'ai'}`;

    const content = document.createElement('div');
    content.className = 'chat-content';
    msg.appendChild(content);

    return msg;
  }

  /** Slim pill row hosting tool calls, thinking and file edits (AI turns). */
  _ensureMetaRow(msg) {
    let meta = msg.querySelector('.chat-turn-meta');
    if (!meta) {
      meta = document.createElement('div');
      meta.className = 'chat-turn-meta';
      msg.insertBefore(meta, msg.querySelector('.chat-content'));
    }
    return meta;
  }

  _appendLatency(msg, latencyMs) {
    if (!msg || latencyMs == null || msg.querySelector('.chat-meta')) return;
    const meta = document.createElement('div');
    meta.className = 'chat-meta';
    meta.textContent = (latencyMs / 1000).toFixed(1) + 's';
    msg.appendChild(meta);
  }

  _appendFileEdits(msg, edits) {
    if (!edits.length || !msg) return;

    const fileMap = new Map();
    for (const edit of edits) {
      for (const p of (edit.paths || [edit.path])) {
        const stats = fileMap.get(p) || { added: 0, deleted: 0 };
        stats.added += edit.added || 0;
        stats.deleted += edit.deleted || 0;
        fileMap.set(p, stats);
      }
    }

    this._renderCollapsibleFileEdits(msg, fileMap);
  }

  async _openFileInWorkspace(filePath) {
    try {
      window.mobileApp.switchMode('workspace');
      await window.mobileApp.controllers.workspace.ready;
      await window.mobileApp.controllers.workspace.openFile(filePath);
    } catch (err) {
      console.error('Failed to open file in workspace:', err);
      showToast(i18n.t('chat.couldNotOpen', { path: filePath }), 'error');
    }
  }

  /* Badge di provenienza per i messaggi entrati da un altro canale
     (es. Telegram): piccola etichetta sopra il contenuto della bolla. */
  _appendOriginBadge(msg, origin) {
    if (!origin || origin === 'websocket') return;
    const badge = document.createElement('div');
    badge.className = 'chat-origin-badge';
    const icon = origin === 'telegram' ? 'ti-brand-telegram' : 'ti-arrows-exchange';
    const label = origin.charAt(0).toUpperCase() + origin.slice(1);
    badge.innerHTML = `<i class="ti ${icon}"></i>${escapeHtml(label)}`;
    msg.insertBefore(badge, msg.firstChild);
  }

  _buildCompletedMessage(text, role, origin, media) {
    const msg = document.createElement('div');
    msg.className = `chat-msg chat-msg-${role === 'user' ? 'user' : 'ai'}`;

    const content = document.createElement('div');
    content.className = 'chat-content';
    if (role === 'user') {
      content.textContent = text;
    } else {
      content.innerHTML = renderMarkdown(String(text || ''));
      renderKaTeX(content);
      this._makeFilePathsClickable(content);
    }
    msg.appendChild(content);
    // Allegati dell'utente ripristinati dalla history (thumb immagini / chip
    // file), così la preview non si perde dopo un reload.
    if (media?.length) this._renderMediaAttachments(msg, media);
    if (role === 'user') this._appendOriginBadge(msg, origin);
    return msg;
  }

  addCompletedMessage(text, role, origin, media) {
    this.chatArea.appendChild(this._buildCompletedMessage(text, role, origin, media));
  }

  addCompletedMessageToTop(text, role, origin, media) {
    this._insertAtTop(this._buildCompletedMessage(text, role, origin, media));
  }

  activate() {
    this._active = true;
    sessionManager.ensureAttached();
    if (!this._initialHistoryLoaded) {
      this.loadInitialHistory();
    }

    this.input.focus();

    // I messaggi possono essere arrivati mentre la vista era nascosta (scrollHeight=0 rende
    // scrollToBottom() un no-op); riallinea lo scroll ora che la vista è di nuovo visibile.
    if (this._autoScroll) this.scrollToBottom(true);
  }

  deactivate() {
    // Spegne solo il type-ahead focus: fuori dalla vista chat non deve rubare i tasti.
    this._active = false;
    // Per il resto è un no-op intenzionale: i listener WS restano sempre attivi (vedi
    // setupWebSocket(), bindato una sola volta nel costruttore). Il socket è condiviso tra
    // le viste e gli eventi che arrivano a vista nascosta vanno comunque processati,
    // altrimenti si perdono per sempre (bug del banner "agent running" bloccato + risposta
    // mai renderizzata).
  }

  handleMessage(msg) {
    switch (msg.event) {
      case 'delta':
        this._handleDelta(msg.text || '');
        break;
      case 'reasoning_delta':
        this._handleReasoningDelta(msg.text || '');
        break;
      case 'reasoning_end':
        this._handleReasoningEnd();
        break;
      case 'stream_end':
        this._handleStreamEnd(msg.text);
        break;
      case 'turn_end':
        this._handleTurnEnd(msg.latency_ms);
        break;
      case 'message':
        this._handleMessage(msg);
        break;
      case 'user':
        this._handleExternalUser(msg);
        break;
      case 'file_edit':
        this._handleFileEdit(msg.edits || []);
        break;
      case 'goal_status':
        this._handleGoalStatus(msg.status, msg.started_at);
        break;
      case 'error':
        this._handleError(msg.detail || msg.reason || 'Unknown error');
        break;
      case 'runtime_model_updated':
        // Campi del payload backend (ws_sender.send_runtime_model_updated):
        // model_name obbligatorio, model_preset/provider opzionali.
        this._runtimeModel = {
          provider: msg.provider || this._runtimeModel?.provider || null,
          model: msg.model_name || null,
          preset: msg.model_preset || null,
        };
        this._updateSessionInfoModel();
        break;
    }
  }

  _ensureAiMessage() {
    if (this._currentMsg) return;

    const msg = document.createElement('div');
    msg.className = 'chat-msg chat-msg-ai';

    this.chatArea.appendChild(msg);
    this._currentMsg = msg;
    this._toolStates = {};
    this._fileEditPaths = new Map();
  }

  // TODO(stream-id): il server manda stream_id con ogni delta/stream_end;
  // chiavare le bolle per stream_id renderebbe il rendering robusto anche
  // senza turn_end (oggi coperto da _resetStreamState su send + turn_end
  // garantito dal backend anche su /stop).
  _handleDelta(text) {
    this._ensureAiMessage();

    if (!this._currentContent) {
      const content = document.createElement('div');
      content.className = 'chat-content';
      this._currentMsg.appendChild(content);
      this._currentContent = content;
      this._deltaBuffer = '';
    }

    this._deltaBuffer += text;
    this._deltaDirty = true;
    this._scheduleFlush();
  }

  /* Programma un flush coalizzato del rendering per il prossimo frame.
     Un solo rAF condiviso tra testo e reasoning: se arrivano insieme,
     costano comunque un solo re-parse per frame. */
  _scheduleFlush() {
    if (this._pendingFrame !== null) return;
    this._pendingFrame = requestAnimationFrame(() => this._flushRender());
  }

  _flushRender() {
    this._pendingFrame = null;
    if (this._deltaDirty && this._currentContent) {
      this._currentContent.innerHTML = renderMarkdown(this._deltaBuffer);
      this._deltaDirty = false;
    }
    if (this._reasoningDirty && this._currentThinking) {
      const body = this._currentThinking.querySelector('.chat-thinking-body');
      if (body) body.innerHTML = renderMarkdown(this._reasoningBuffer);
      this._reasoningDirty = false;
    }
    this.scrollToBottom();
  }

  _cancelPendingFrame() {
    if (this._pendingFrame !== null) {
      cancelAnimationFrame(this._pendingFrame);
      this._pendingFrame = null;
    }
  }

  _appendReasoningBlock(msg, text, collapsed = true) {
    if (!msg || !text) return;
    const thinking = document.createElement('div');
    thinking.className = `chat-thinking${collapsed ? ' collapsed' : ''}`;

    const header = document.createElement('div');
    header.className = 'chat-thinking-header';
    header.innerHTML = '<i class="ti ti-brain"></i><span class="chat-thinking-label">' + i18n.t('chat.showThinking') + '</span><i class="ti ti-chevron-down chat-thinking-chevron"></i>';
    thinking.appendChild(header);

    const body = document.createElement('div');
    body.className = 'chat-thinking-body';
    body.innerHTML = renderMarkdown(text);
    thinking.appendChild(body);

    header.addEventListener('click', () => {
      thinking.classList.toggle('collapsed');
    });

    this._ensureMetaRow(msg).appendChild(thinking);
  }

  _handleReasoningDelta(text) {
    this._ensureAiMessage();

    if (!this._currentThinking) {
      const thinking = document.createElement('div');
      thinking.className = 'chat-thinking collapsed';

      const header = document.createElement('div');
      header.className = 'chat-thinking-header';
      header.innerHTML = '<i class="ti ti-brain"></i><span class="chat-thinking-label">' + i18n.t('chat.showThinking') + '</span><i class="ti ti-chevron-down chat-thinking-chevron"></i>';
      thinking.appendChild(header);

      const body = document.createElement('div');
      body.className = 'chat-thinking-body';
      thinking.appendChild(body);

      header.addEventListener('click', () => {
        thinking.classList.toggle('collapsed');
      });

      this._ensureMetaRow(this._currentMsg).appendChild(thinking);

      this._currentThinking = thinking;
      this._reasoningBuffer = '';
    }

    this._reasoningBuffer += text;
    this._reasoningDirty = true;
    this._scheduleFlush();
  }

  _handleReasoningEnd() {
    // Flush finale del reasoning eventualmente in coda al frame corrente,
    // così l'ultimo delta non resta invisibile se lo stream chiude prima
    // che l'rAF pendente scatti.
    if (this._reasoningDirty && this._currentThinking) {
      const body = this._currentThinking.querySelector('.chat-thinking-body');
      if (body) body.innerHTML = renderMarkdown(this._reasoningBuffer);
      this._reasoningDirty = false;
    }
    if (this._currentThinking) {
      const header = this._currentThinking.querySelector('.chat-thinking-header');
      if (header) {
        const icon = header.querySelector('i');
        if (icon) {
          icon.className = 'ti ti-check';
          icon.style.color = 'var(--ok)';
        }
      }
    }
    this._reasoningBuffer = '';
  }

  _handleStreamEnd(fullText) {
    // Annulla il frame di rendering eventualmente pendente: sotto facciamo
    // comunque il render finale completo (con KaTeX + path cliccabili), un
    // rAF in ritardo sovrascriverebbe con una versione parziale.
    this._cancelPendingFrame();
    this._deltaDirty = false;
    // `text` è OPZIONALE in stream_end: il server lo omette quando l'ultimo
    // delta è vuoto e non c'è stata riscrittura di immagini (ws_sender.py,
    // send_stream_delta) — cioè quasi sempre, dato che il client i delta li
    // ha già. Gatare la passata finale sulla sua presenza la faceva saltare
    // sulla maggior parte delle risposte: niente KaTeX (le formule restavano
    // `$$...$$` fino a un riavvio) e niente path cliccabili. Il buffer locale
    // è la stessa cosa, quindi fa da fallback.
    const finalText = fullText || this._deltaBuffer;
    if (this._currentContent && finalText) {
      this._currentContent.innerHTML = renderMarkdown(finalText);
      renderKaTeX(this._currentContent);
      this._makeFilePathsClickable(this._currentContent);
    }
    // Chiude il segmento. Un turno con testo → tool → testo produce più
    // stream, ma `_resetStreamState` scatta solo a turn_end: senza questo
    // azzeramento i delta del segmento successivo si accodavano al buffer
    // del precedente e finivano nella stessa bolla, incollati e senza
    // stacco ("...setup right.Alright, let me build this..."). La cronologia
    // li tiene separati, ed è per questo che un reload "riparava" il testo.
    this._currentContent = null;
    this._deltaBuffer = '';
    this._bumpUnread();
    this.scrollToBottom();
  }

  _resetStreamState() {
    // Annulla un frame pendente prima di azzerare i riferimenti alle bolle,
    // altrimenti scriverebbe su un contenitore ormai orfano.
    this._cancelPendingFrame();
    this._deltaDirty = false;
    this._reasoningDirty = false;
    this._currentMsg = null;
    this._currentThinking = null;
    this._currentContent = null;
    this._deltaBuffer = '';
    this._reasoningBuffer = '';
    this._toolStates = {};
    this._fileEditPaths = new Map();
  }

  _handleTurnEnd(latencyMs) {
    this._appendLatency(this._currentMsg, latencyMs);

    this._resetStreamState();
    // Niente scroll forzato: se l'utente è risalito a leggere, a fine turno
    // resta dove si trova (comportamento WhatsApp/Telegram).
    this.scrollToBottom();
  }

  /* Messaggio utente entrato da un altro canale (es. Telegram): il backend
     lo proietta live sulla vista WebUI. Nuova bolla utente con badge di
     provenienza; chiude l'eventuale stream orfano perché sta iniziando un
     turno nuovo (stessa disciplina di sendMessage). */
  _handleExternalUser(msg) {
    const text = msg.text || '';
    if (!text.trim()) return;
    this._resetStreamState();
    this.addCompletedMessage(text, 'user', msg.origin);
    this._bumpUnread();
    this.scrollToBottom();
  }

  _handleMessage(msg) {
    if (msg.session_boundary) {
      this._resetStreamState();
      this._appendSessionBoundary(msg.text || '');
      this._bumpUnread();
      this.scrollToBottom();
      return;
    }
    if (msg.kind === 'tool_hint' || msg.tool_events) {
      this._ensureAiMessage();
      this._renderToolEvents(msg.tool_events || []);
    }

    if (msg.text && msg.kind !== 'tool_hint') {
      this._ensureAiMessage();
      if (!this._currentContent) {
        const content = document.createElement('div');
        content.className = 'chat-content';
        this._currentMsg.appendChild(content);
        this._currentContent = content;
      }
      this._currentContent.innerHTML = renderMarkdown(msg.text);
      renderKaTeX(this._currentContent);
      this._makeFilePathsClickable(this._currentContent);
    }

    if (msg.media_urls?.length) {
      this._ensureAiMessage();
      this._renderMediaAttachments(this._currentMsg, msg.media_urls);
    }

    if (msg.latencyMs != null && this._currentMsg) {
      this._appendLatency(this._currentMsg, msg.latencyMs);
    }

    if ((msg.text && msg.kind !== 'tool_hint') || msg.media_urls?.length) this._bumpUnread();
    this.scrollToBottom();
  }

  _renderToolEvents(events, targetMsg = null) {
    const msg = targetMsg || this._currentMsg;
    if (!msg) return;

    let toolsContainer = msg.querySelector('.chat-tools');
    if (!toolsContainer) {
      toolsContainer = document.createElement('div');
      toolsContainer.className = 'chat-tools';
      this._ensureMetaRow(msg).appendChild(toolsContainer);
    }

    for (const ev of events) {
      const callId = ev.call_id;
      if (!callId) continue;

      let existing = toolsContainer.querySelector(`[data-call-id="${callId}"]`);

      if (ev.phase === 'start') {
        this._toolStates[callId] = 'running';
        if (!existing) {
          existing = this._createToolElement(toolsContainer, ev, 'running');
        }
      } else if (ev.phase === 'end') {
        this._toolStates[callId] = 'done';
        if (!existing) {
          existing = this._createToolElement(toolsContainer, ev, 'done');
        }
        const icon = existing.querySelector('.chat-tool-icon');
        if (icon) {
          icon.className = `chat-tool-icon done ti ${TOOL_ICONS.end}`;
        }

        if (ev.result != null) {
          const resultStr = typeof ev.result === 'string' ? ev.result : JSON.stringify(ev.result, null, 2);
          if (resultStr && resultStr !== 'null') {
            existing.dataset.result = resultStr;
          }
        }
      } else if (ev.phase === 'error') {
        this._toolStates[callId] = 'error';
        if (!existing) {
          existing = this._createToolElement(toolsContainer, ev, 'error');
        }
        const icon = existing.querySelector('.chat-tool-icon');
        if (icon) {
          icon.className = `chat-tool-icon error ti ${TOOL_ICONS.error}`;
        }
        if (ev.error) {
          const errDiv = document.createElement('div');
          errDiv.className = 'chat-tool-error';
          errDiv.textContent = ev.error;
          existing.appendChild(errDiv);
        }
      }
    }
  }

  _createToolElement(container, ev, state) {
    const tool = document.createElement('div');
    tool.className = 'chat-tool';
    tool.dataset.callId = ev.call_id;
    tool.style.cursor = ev.result != null || ev.phase === 'end' ? 'pointer' : 'default';

    const header = document.createElement('div');
    header.className = 'chat-tool-header';

    const icon = document.createElement('i');
    icon.className = `chat-tool-icon ${state} ti ${state === 'running' ? TOOL_ICONS.start : (state === 'error' ? TOOL_ICONS.error : TOOL_ICONS.end)}`;
    header.appendChild(icon);

    const name = document.createElement('span');
    name.className = 'chat-tool-name';
    name.textContent = ev.name || 'tool';
    header.appendChild(name);

    tool.appendChild(header);

    tool.addEventListener('click', () => this._toggleToolResult(tool));

    container.appendChild(tool);
    return tool;
  }

  _toggleToolResult(tool) {
    let resultEl = tool.querySelector('.tool-result-text');
    if (resultEl) {
      resultEl.remove();
      return;
    }

    const resultStr = tool.dataset.result;
    if (!resultStr) return;

    resultEl = document.createElement('pre');
    resultEl.className = 'tool-result-text';
    resultEl.textContent = resultStr;
    tool.appendChild(resultEl);
  }

  _handleFileEdit(edits) {
    if (!edits.length) return;
    this._ensureAiMessage();

    for (const edit of edits) {
      if (edit.phase !== 'end') continue;
      for (const p of (edit.paths || [edit.path])) {
        const stats = this._fileEditPaths.get(p) || { added: 0, deleted: 0 };
        stats.added += edit.added || 0;
        stats.deleted += edit.deleted || 0;
        this._fileEditPaths.set(p, stats);
      }
    }

    this._renderCollapsibleFileEdits(this._currentMsg, this._fileEditPaths);
    this.scrollToBottom();
  }

  _renderCollapsibleFileEdits(msg, fileMap) {
    if (!fileMap.size || !msg) return;

    const paths = Array.from(fileMap.keys());

    let container = msg.querySelector('.tool-events-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'tool-events-container collapsed';

      const header = document.createElement('div');
      header.className = 'tool-events-header';
      header.innerHTML = `<i class="ti ti-file-code"></i>
        <span class="tool-events-label">${i18n.t('chat.filesModified', { count: String(paths.length) }).replace(/^\d+\s+/, '')}</span>
        <span class="tool-events-badge">${String(paths.length)}</span>
        <i class="ti ti-chevron-down tool-events-chevron"></i>`;
      header.addEventListener('click', () => {
        container.classList.toggle('collapsed');
      });

      const body = document.createElement('div');
      body.className = 'tool-events-body file-edits-body';

      container.appendChild(header);
      container.appendChild(body);

      this._ensureMetaRow(msg).appendChild(container);
    }

    const header = container.querySelector('.tool-events-header');
    const badgeEl = header.querySelector('.tool-events-badge');
    badgeEl.textContent = String(paths.length);

    const labelEl = header.querySelector('.tool-events-label');
    labelEl.textContent = i18n.t('chat.filesModified', { count: String(paths.length) }).replace(/^\d+\s+/, '');

    const body = container.querySelector('.tool-events-body');
    body.innerHTML = '';
    for (const path of paths) {
      const stats = fileMap.get(path) || { added: 0, deleted: 0 };

      let diffHtml = '';
      if (stats.added || stats.deleted) {
        const parts = [];
        if (stats.added > 0) parts.push(`<span class="file-diff-added">+${stats.added}</span>`);
        if (stats.deleted > 0) parts.push(`<span class="file-diff-deleted">–${stats.deleted}</span>`);
        diffHtml = `<span class="file-diff-stats">${parts.join('')}</span>`;
      }

      const item = document.createElement('div');
      item.className = 'chat-file-edit';
      item.innerHTML = `<i class="ti ti-file-code"></i><span class="chat-file-edit-name">${escapeHtml(path)}</span>${diffHtml}`;
      item.addEventListener('click', async (e) => {
        e.stopPropagation();
        await this._openFileInWorkspace(path);
      });
      body.appendChild(item);
    }
  }

  _handleGoalStatus(status, startedAt) {
    sessionManager.runStartedAt = status === 'running'
      ? (startedAt || Date.now() / 1000)
      : null;
    if (status === 'running') {
      if (!this._goalBanner) {
        this._goalBanner = document.createElement('div');
        this._goalBanner.className = 'chat-goal-banner';
        this._goalBanner.innerHTML = `<i class="ti ti-loader-2"></i><span>${i18n.t('chat.agentRunning')}</span><span class="chat-goal-timer"></span>`;
        this.chatArea.appendChild(this._goalBanner);
      }
      if (this._goalTimer) clearInterval(this._goalTimer);
      const timerEl = this._goalBanner.querySelector('.chat-goal-timer');
      const start = startedAt ? startedAt * 1000 : Date.now();
      this._goalTimer = setInterval(() => {
        const elapsed = Math.floor((Date.now() - start) / 1000);
        if (timerEl) timerEl.textContent = elapsed + 's';
      }, 1000);
      this.scrollToBottom();
    } else {
      if (this._goalTimer) {
        clearInterval(this._goalTimer);
        this._goalTimer = null;
      }
      if (this._goalBanner) {
        this._goalBanner.remove();
        this._goalBanner = null;
      }
    }
  }

  _handleError(detail) {
    const el = document.createElement('div');
    el.className = 'chat-error';
    el.textContent = i18n.t('chat.error') + ': ' + detail;
    this.chatArea.appendChild(el);
    this._autoScroll = true;
    this.scrollToBottom(true);

    this._resetStreamState();
  }

  _renderAttachPreview(items) {
    const preview = document.getElementById('attach-preview');
    if (!items.length) {
      preview.style.display = 'none';
      preview.innerHTML = '';
      return;
    }
    preview.style.display = 'flex';
    // Immagini → thumbnail; qualsiasi altro file → chip con icona e nome.
    preview.innerHTML = items.map((item, i) => {
      const remove = `<button class="attach-remove" data-idx="${i}"><i class="ti ti-x"></i></button>`;
      if (item.isImage) {
        return `<div class="attach-thumb" data-idx="${i}">
            <img src="${item.data_url}" alt="${escapeHtml(item.name)}">${remove}
          </div>`;
      }
      const name = escapeHtml(item.name || 'file');
      return `<div class="attach-thumb attach-file" data-idx="${i}" title="${name}">
          <i class="ti ti-file"></i>
          <span class="attach-file-name">${name}</span>${remove}
        </div>`;
    }).join('');
    preview.querySelectorAll('.attach-remove').forEach(btn => {
      btn.addEventListener('click', () => this.imageHandler.remove(Number(btn.dataset.idx)));
    });
  }

  async sendMessage() {
    const text = this.input.value.trim();
    const hasImages = this.imageHandler.count > 0;
    if (!text && !hasImages) return;

    if (text === '/clear') {
      this.chatArea.innerHTML = '';
      this.identityEl = null;
      this._ensureIdentity();
      this.historyCursor = null;
      this.hasMoreHistory = true;
      const el = document.createElement('div');
      el.className = 'chat-sys';
      el.textContent = i18n.t('chat.cleared');
      this.chatArea.appendChild(el);
      this.input.value = '';
      this.input.style.height = 'auto';
      this._updateSendState();
      this._updateActions();
      return;
    }

    sessionManager.ensureAttached();

    const msg = document.createElement('div');
    msg.className = 'chat-msg chat-msg-user';
    const content = document.createElement('div');
    content.className = 'chat-content';
    content.textContent = text;
    msg.appendChild(content);
    // Renderizza gli allegati nella bolla appena inviata (thumb immagini / chip
    // file): senza questo l'anteprima del composer sparirebbe al clear.
    const attachments = this.imageHandler.getAttachmentEntries();
    if (attachments.length) this._renderMediaAttachments(msg, attachments);
    this.chatArea.appendChild(msg);
    this._autoScroll = true;
    this.scrollToBottom(true);

    const media = this.imageHandler.getImages();
    this.imageHandler.clear();
    this.input.value = '';
    this.input.style.height = 'auto';
    this._updateSendState();
    this._updateActions();
    this.input.focus();

    // Ogni invio apre una bolla AI nuova: se il turn_end del turno precedente
    // è andato perso (turno cancellato, riconnessione), la risposta non deve
    // accodarsi alla bolla vecchia.
    this._resetStreamState();

    if (!wsManager.sendToChat(sessionManager.currentKey, text, media)) {
      const el = document.createElement('div');
      el.className = 'chat-error';
      el.textContent = i18n.t('chat.wsError');
      this.chatArea.appendChild(el);
    }
  }

  scrollToBottom(force = false) {
    if (!force && (!this._autoScroll || this._userTouching)) return;
    requestAnimationFrame(() => {
      this.chatArea.scrollTop = this.chatArea.scrollHeight;
    });
    this._unreadCount = 0;
    this._updateScrollFab();
  }

  /* Bottone flottante "vai in fondo": visibile solo quando l'utente è staccato
     dal fondo, con badge dei messaggi arrivati nel frattempo. */
  _updateScrollFab() {
    if (!this._fabEl) return;
    this._fabEl.classList.toggle('visible', !this._autoScroll);
    const badge = this._fabEl.querySelector('.chat-scroll-fab-badge');
    if (badge) {
      const show = !this._autoScroll && this._unreadCount > 0;
      badge.style.display = show ? '' : 'none';
      if (show) badge.textContent = this._unreadCount > 99 ? '99+' : String(this._unreadCount);
    }
  }

  /* Una tacca sul badge per ogni messaggio completato mentre si è staccati. */
  _bumpUnread() {
    if (this._autoScroll) return;
    this._unreadCount += 1;
    this._updateScrollFab();
  }

  _isNearBottom() {
    const { scrollTop, scrollHeight, clientHeight } = this.chatArea;
    return scrollHeight - scrollTop - clientHeight < this._scrollThreshold;
  }

  async _renderFilePreview(filePath, container) {
    let previewEl = container.querySelector('.file-preview');
    if (previewEl) {
      previewEl.remove();
      return;
    }

    previewEl = document.createElement('div');
    previewEl.className = 'file-preview';
    previewEl.innerHTML = `<div class="file-preview-header"><i class="ti ti-loader-2 spin"></i> ${i18n.t('common.loading')}</div>`;
    container.appendChild(previewEl);

    try {
      const data = await api.fetchFilePreview(sessionManager.currentKey, filePath);
      const content = data.content || '';
      const language = data.language || 'text';
      const size = data.size || 0;
      const sizeLabel = size > 1024 ? (size / 1024).toFixed(1) + ' KB' : size + ' B';

      let highlighted;
      if (typeof hljs !== 'undefined') {
        try {
          const lang = hljs.getLanguage(language) ? language : null;
          highlighted = lang
            ? hljs.highlight(content, { language: lang }).value
            : hljs.highlightAuto(content).value;
        } catch {
          highlighted = escapeHtml(content);
        }
      } else {
        highlighted = escapeHtml(content);
      }

      const lines = highlighted.split('\n');
      const numberedLines = lines.map((line, i) =>
        `<div class="file-preview-line"><span class="file-preview-line-num">${i + 1}</span><span class="file-preview-line-code">${line}</span></div>`
      ).join('');

      previewEl.innerHTML = `
        <div class="file-preview-header">
          <span class="file-preview-path">${escapeHtml(filePath)}</span>
          <span class="file-preview-meta">${language} · ${sizeLabel}</span>
          <button class="file-preview-close" title="${i18n.t('common.close')}"><i class="ti ti-x"></i></button>
        </div>
        <div class="file-preview-content"><div class="file-preview-code">${numberedLines}</div></div>
        <div class="file-preview-actions">
          <a class="file-preview-action" href="#workspace" data-path="${escapeHtml(filePath)}"><i class="ti ti-external-link"></i> ${i18n.t('chat.openInEditor')}</a>
        </div>
      `;

      previewEl.querySelector('.file-preview-close').addEventListener('click', () => {
        previewEl.remove();
      });

      const editorLink = previewEl.querySelector('.file-preview-action');
      if (editorLink) {
        editorLink.addEventListener('click', async (e) => {
          e.preventDefault();
          await this._openFileInWorkspace(filePath);
        });
      }
    } catch (err) {
      previewEl.innerHTML = `<div class="file-preview-header"><span class="file-preview-path">${escapeHtml(filePath)}</span><span class="file-preview-meta" style="color:var(--error)">${i18n.t('chat.failedToLoad')}</span><button class="file-preview-close"><i class="ti ti-x"></i></button></div>`;
      previewEl.querySelector('.file-preview-close').addEventListener('click', () => previewEl.remove());
    }
  }

  _makeFilePathsClickable(container) {
    const filePattern = /(?<!\S)((?:\.\/|\.\.\/|[\w.-]+\/)+[\w.-]+\.\w{1,10})(?!\S)/g;
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null);
    const textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);

    for (const node of textNodes) {
      const text = node.textContent;
      if (!filePattern.test(text)) continue;
      filePattern.lastIndex = 0;

      const frag = document.createDocumentFragment();
      let lastIdx = 0;
      let match;
      while ((match = filePattern.exec(text)) !== null) {
        if (match.index > lastIdx) {
          frag.appendChild(document.createTextNode(text.slice(lastIdx, match.index)));
        }
        const link = document.createElement('a');
        link.className = 'chat-file-path-link';
        link.textContent = match[1];
        link.href = '#';
        link.addEventListener('click', (e) => {
          e.preventDefault();
          const msgEl = link.closest('.chat-msg');
          if (msgEl) this._renderFilePreview(match[1], msgEl);
        });
        frag.appendChild(link);
        lastIdx = match.index + match[0].length;
      }
      if (lastIdx < text.length) {
        frag.appendChild(document.createTextNode(text.slice(lastIdx)));
      }
      node.parentNode.replaceChild(frag, node);
    }
  }

  handleAction(action) {}

  _initSessionInfo() {
    this._ensureIdentity();
    this.identityEl.style.cursor = 'pointer';
    this.identityEl.addEventListener('click', (e) => {
      e.stopPropagation();
      this._showSessionInfo();
    });
  }

  _showSessionInfo() {
    this._hideSessionInfo();

    const model = this._runtimeModel;
    const scope = sessionManager.currentScope;

    const popover = document.createElement('div');
    popover.className = 'session-info-popover';

    const channel = 'websocket';
    const sessionId = 'default';

    const brand = model ? getProviderBrand(model.provider) : null;
    const modelLabel = model
      ? `${brand?.label || model.provider || i18n.t('chat.unknown')} / ${model.model || '—'}`
      : '—';
    const modelColor = brand?.color || 'var(--text-faint)';

    const projectPath = scope?.project_path || '—';
    const accessMode = scope?.access_mode === 'full' ? i18n.t('chat.fullAccess') : scope?.access_mode ? i18n.t('chat.restricted') : i18n.t('chat.default');
    const accessIcon = scope?.access_mode === 'full' ? 'ti-lock-open' : 'ti-lock';
    const accessClass = scope?.access_mode === 'full' ? 'full' : scope?.access_mode ? 'restricted' : 'default';

    const runStartedAt = sessionManager.runStartedAt;
    const isRunning = !!runStartedAt;
    let statusTimerInterval = null;

    popover.innerHTML = `
      <div class="session-info-header">
        <span><i class="ti ti-info-circle"></i> ${i18n.t('session.info')}</span>
        <button class="session-info-close"><i class="ti ti-x"></i></button>
      </div>
      <div class="session-info-section">
        <div class="session-info-row">
          <span class="session-info-label">${i18n.t('session.session')}</span>
          <span class="session-info-value" style="font-family:var(--font-mono);font-size:10px;">${escapeHtml(sessionId)}</span>
        </div>
        <div class="session-info-row">
          <span class="session-info-label">${i18n.t('session.channel')}</span>
          <span class="session-info-value">${escapeHtml(channel)}</span>
        </div>
      </div>
      <div class="session-info-section session-info-model">
        <div class="session-info-row">
          <span class="session-info-label">${i18n.t('session.model')}</span>
          <span class="session-info-value" id="si-model-value" style="color:${modelColor}">${escapeHtml(modelLabel)}</span>
        </div>
        ${model?.preset ? `<div class="session-info-row">
          <span class="session-info-label">${i18n.t('session.preset')}</span>
          <span class="session-info-value">${escapeHtml(model.preset)}</span>
        </div>` : ''}
      </div>
      <div class="session-info-section session-info-workspace">
        <div class="session-info-row">
          <span class="session-info-label">${i18n.t('session.project')}</span>
          <span class="session-info-value" style="font-family:var(--font-mono);font-size:10px;word-break:break-all;">${escapeHtml(projectPath)}</span>
        </div>
        <div class="session-info-row">
          <span class="session-info-label">${i18n.t('session.access')}</span>
          <span class="session-info-value"><span class="scope-badge ${accessClass}"><i class="ti ${accessIcon}"></i> ${accessMode}</span></span>
        </div>
      </div>
      <div class="session-info-section session-info-status">
        <div class="session-info-row">
          <span class="session-info-label">${i18n.t('session.status')}</span>
          <span class="session-info-value" id="si-status-value">
            ${isRunning
              ? `<span style="color:var(--accent);display:inline-flex;align-items:center;gap:4px;"><i class="ti ti-loader-2 spin"></i> ${i18n.t('session.running')} <span class="session-info-timer" id="si-timer"></span></span>`
              : `<span style="color:var(--text-faint)">${i18n.t('session.idle')}</span>`}
          </span>
        </div>
      </div>
    `;

    document.body.appendChild(popover);
    this._sessionInfoPopover = popover;

    const anchor = this.identityEl;
    if (anchor) {
      const rect = anchor.getBoundingClientRect();
      popover.style.top = Math.max(8, rect.bottom + 4) + 'px';
      popover.style.left = Math.max(8, rect.left) + 'px';
    }

    if (isRunning) {
      const timerEl = popover.querySelector('#si-timer');
      const start = runStartedAt ? runStartedAt * 1000 : Date.now();
      statusTimerInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - start) / 1000);
        const mins = Math.floor(elapsed / 60);
        const secs = elapsed % 60;
        if (timerEl) timerEl.textContent = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
      }, 1000);
      this._sessionInfoTimer = statusTimerInterval;
    }

    const closeBtn = popover.querySelector('.session-info-close');
    closeBtn.addEventListener('click', () => this._hideSessionInfo());

    this._sessionInfoOutsideHandler = (e) => {
      if (!popover.contains(e.target)) this._hideSessionInfo();
    };
    this._sessionInfoEscHandler = (e) => {
      if (e.key === 'Escape') this._hideSessionInfo();
    };
    setTimeout(() => {
      document.addEventListener('pointerdown', this._sessionInfoOutsideHandler);
      document.addEventListener('keydown', this._sessionInfoEscHandler);
    }, 0);
  }

  _hideSessionInfo() {
    if (this._sessionInfoTimer) {
      clearInterval(this._sessionInfoTimer);
      this._sessionInfoTimer = null;
    }
    if (this._sessionInfoPopover) {
      this._sessionInfoPopover.remove();
      this._sessionInfoPopover = null;
    }
    if (this._sessionInfoOutsideHandler) {
      document.removeEventListener('pointerdown', this._sessionInfoOutsideHandler);
      this._sessionInfoOutsideHandler = null;
    }
    if (this._sessionInfoEscHandler) {
      document.removeEventListener('keydown', this._sessionInfoEscHandler);
      this._sessionInfoEscHandler = null;
    }
  }

  _updateSessionInfoModel() {
    const el = this._sessionInfoPopover?.querySelector('#si-model-value');
    if (!el || !this._runtimeModel) return;
    const { provider, model } = this._runtimeModel;
    const brand = getProviderBrand(provider);
    el.textContent = `${brand.label || provider || i18n.t('chat.unknown')} / ${model || '—'}`;
    el.style.color = brand.color;
  }

}

/** Mobile Wiki Controller — drawer-based wiki with audits and file tree. */

import { api } from './shared/api-client.js';
import { escapeHtml, showToast, ensureVendor } from './shared/utils.js';
import { AppState } from './shared/state.js';
import { renderTree, wireTreeFolder, wireTreeFiles } from './shared/tree-renderer.js';
import { i18n } from './shared/i18n.js';
import { confirmDialog, promptDialog } from './shared/dialog.js';

export class WikiController {
  constructor() {
    this.contentEl = document.getElementById('wiki-content');
    this.auditDrawerBody = document.getElementById('drawer-audit-body');
    this.filesDrawerBody = document.getElementById('drawer-files-body');
    this.currentWiki = null;
    this.currentPath = null;
    this.currentTitle = null;
    this.isHome = false;
    this.rawMarkdown = '';
    this._auditMode = 'open';  // 'open' | 'resolved'
    this.loadingEl = document.getElementById('wiki-loading');
    this._pending = null;
    this._popover = document.getElementById('wiki-feedback-popover');
    this._popoverBtn = document.getElementById('wiki-feedback-trigger');
    this.lastWikiPage = {};
    // Monotonic load token: guards against stale-response races when the user
    // navigates rapidly (only the latest load* call writes the content area).
    this._loadToken = 0;
    AppState.wiki = AppState.wiki || {};
    // Re-render al cambio lingua: se la wiki è la vista attiva ri-traduce
    // subito (breadcrumb "Home", toggle/etichette audit, titolo header, stati
    // vuoti); altrimenti marca "sporco" e rinvia il re-render al rientro in
    // vista (activate), così non si sovrascrive l'header di un'altra vista.
    this._localeDirty = false;
    i18n.onLocaleChange(() => this._onLocaleChange());
    this._ready = this.init();
  }

  get ready() { return this._ready; }

  showLoading() {
    if (this.loadingEl) this.loadingEl.classList.add('active');
  }
  hideLoading() {
    if (this.loadingEl) this.loadingEl.classList.remove('active');
  }

  // Sanifica l'HTML del server; se DOMPurify manca, mostra il markdown grezzo
  // escapato invece di una pagina bianca silenziosa.
  _safeHtml(html, raw) {
    if (typeof DOMPurify !== 'undefined') return DOMPurify.sanitize(html);
    console.warn('DOMPurify non disponibile: fallback a markdown escapato');
    return `<pre class="wiki-raw-fallback">${escapeHtml(raw || '')}</pre>`;
  }

  async init() {
    this.showLoading();
    try {
      const cfg = await api.getConfig();
      AppState.wiki = AppState.wiki || {};
      AppState.wiki.author = cfg.author || 'me';
      AppState.wiki.wikis = cfg.wikis || [];
    } catch {}
    const params = new URLSearchParams(window.location.search);
    const wiki = params.get('wiki');
    const page = params.get('page');
    if (wiki) {
      await this.loadWikiPage(wiki, page || 'index.md', false);
    } else {
      await this.loadHome(false);
    }
    this.hideLoading();
    this.initWikiFeedback();
  }

  activate() {
    if (this._localeDirty) {
      this._localeDirty = false;
      this._rerenderForLocale();
    }
    this._selectionHandler = () => {
      // Gli audit si creano solo dentro una wiki: niente popover in Home.
      if (this.isHome || !this.currentWiki) {
        this._popover?.classList.add('hidden');
        return;
      }
      const sel = document.getSelection();
      if (!sel || sel.rangeCount === 0 || sel.isCollapsed) {
        this._popover?.classList.add('hidden');
        return;
      }
      const range = sel.getRangeAt(0);
      if (!this.contentEl.contains(range.commonAncestorContainer)) {
        this._popover?.classList.add('hidden');
        return;
      }
      const rect = range.getBoundingClientRect();
      this._popover.style.left = `${window.scrollX + rect.left}px`;
      this._popover.style.top = `${window.scrollY + rect.top - 42}px`;
      this._popover.classList.remove('hidden');
    };
    document.addEventListener('selectionchange', this._selectionHandler);
  }

  deactivate() {
    this._popover?.classList.add('hidden');
    this._pending = null;
    if (this._selectionHandler) {
      document.removeEventListener('selectionchange', this._selectionHandler);
      this._selectionHandler = null;
    }
  }

  _onLocaleChange() {
    if (window.mobileApp?.currentMode === 'wiki') {
      this._rerenderForLocale();
    } else {
      this._localeDirty = true;
    }
  }

  _rerenderForLocale() {
    // Ricarica la vista corrente senza toccare la history: rigenera tutta la
    // chrome localizzata. Il markdown della pagina è indipendente dalla lingua,
    // ma il re-fetch è l'unico modo per ricostruire breadcrumb/audit/titolo; il
    // guard _loadToken in load* copre eventuali race con altri caricamenti.
    if (this.isHome) {
      this.loadHome(false);
    } else if (this.currentWiki) {
      this.loadWikiPage(this.currentWiki, this.currentPath, false);
    }
  }

  async loadHome(pushHistory = true) {
    const token = ++this._loadToken;
    this.isHome = true;
    this.currentWiki = null;
    this.currentPath = '_index.md';
    AppState.wiki.currentWiki = null;
    AppState.wiki.currentPath = '_index.md';
    this.contentEl.innerHTML = `<p class="wiki-blockq">${i18n.t('wiki.loading')}</p>`;
    try {
      const data = await api.getPage({});
      if (token !== this._loadToken) return;  // superseded by a newer load
      this.rawMarkdown = data.raw;
      this.currentTitle = null;
      this.contentEl.innerHTML = this._safeHtml(data.html, data.raw);
      window.mobileApp.header.setTitle(i18n.t('wiki.home'));
      this._renderBreadcrumbs();
      this._wireWikiLinks();
      await this.loadTree();
      await this.loadAudits(null);
      this._renderLatex();
      if (pushHistory) window.mobileApp.pushNav({ mode: 'wiki' });
    } catch (err) {
      if (token !== this._loadToken) return;  // don't surface errors of stale loads
      const msg = err.message || '';
      if (msg.includes('404') || msg.includes('not found') || msg.includes('Page not found') || msg.includes('file not found')) {
        let isEmpty = true;
        try {
          const tree = await api.getTree();
          isEmpty = this._countMdFiles(tree) === 0;
        } catch {
          isEmpty = true;
        }
        if (token !== this._loadToken) return;  // a newer load started during getTree
        if (isEmpty) {
          this.contentEl.innerHTML = `<p class="wiki-blockq">${i18n.t('wiki.empty')}</p>`;
        } else {
          this.contentEl.innerHTML = `<p class="wiki-blockq" style="color: var(--error)">${i18n.t('wiki.homeMissing')}</p>`;
        }
        window.mobileApp.header.setTitle(i18n.t('wiki.home'));
        this._renderBreadcrumbs();
        this.loadTree();
        this.loadAudits(null);
        if (pushHistory) window.mobileApp.pushNav({ mode: 'wiki' });
      } else {
        this.contentEl.innerHTML = `<p class="wiki-blockq" style="color: var(--error)">${i18n.t('common.error')}: ${escapeHtml(msg)}</p>`;
      }
    }
  }

  _countMdFiles(node) {
    if (!node) return 0;
    if (node.kind === 'file') return 1;
    if (!Array.isArray(node.children)) return 0;
    return node.children.reduce((sum, child) => sum + this._countMdFiles(child), 0);
  }

  async loadWikiPage(wiki, page, pushHistory = true) {
    const token = ++this._loadToken;
    this.isHome = false;
    this.currentWiki = wiki;
    this.currentPath = page;
    AppState.wiki.currentWiki = wiki;
    AppState.wiki.currentPath = page;
    this.contentEl.innerHTML = `<p class="wiki-blockq">${i18n.t('wiki.loading')}</p>`;
    try {
      const data = await api.getPage({ wiki, page });
      if (token !== this._loadToken) return;  // superseded by a newer load
      this.currentPath = data.page || page;
      this.currentWiki = data.wiki || wiki;
      this.currentTitle = data.title || null;
      this.rawMarkdown = data.raw;
      this.contentEl.innerHTML = this._safeHtml(data.html, data.raw);
      window.mobileApp.header.setTitle(data.title || page);
      this._renderBreadcrumbs();
      this._wireWikiLinks();
      await this.loadTree(this.currentWiki);
      await this.loadAudits(this.currentPath);
      this._renderLatex();
      this._renderMermaid();
      if (pushHistory) {
        window.mobileApp.pushNav({ mode: 'wiki', wikiPage: true, wiki: this.currentWiki, page: this.currentPath });
      }
      this.lastWikiPage[this.currentWiki] = this.currentPath;
    } catch (err) {
      if (token !== this._loadToken) return;  // don't surface errors of stale loads
      this.contentEl.innerHTML = `<p class="wiki-blockq" style="color: var(--error)">${i18n.t('common.error')}: ${escapeHtml(err.message)}</p>`;
    }
  }

  _renderBreadcrumbs() {
    let existing = this.contentEl.querySelector('.wiki-breadcrumbs');
    if (existing) existing.remove();
    const wrap = document.createElement('div');
    wrap.className = 'wiki-breadcrumbs';
    const sep = ` <span class="bc-sep">/</span> `;

    if (this.isHome) {
      wrap.innerHTML = `<span class="bc-current">${i18n.t('wiki.home')}</span>`;
    } else {
      const crumbs = [];
      crumbs.push(`<a class="bc-link" data-home href="/?mode=wiki">${i18n.t('wiki.home')}</a>`);

      // Segmenti del path: cartelle intermedie + foglia (senza estensione).
      let segs = (this.currentPath || '')
        .replace(/\\/g, '/').replace(/\.md$/i, '').split('/').filter(Boolean);
      // Un trailing "index" (root wiki o folder-split) è ridondante col crumb padre.
      if (segs.length > 1 && segs[segs.length - 1] === 'index') segs.pop();
      const onWikiRoot = segs.length === 0 || (segs.length === 1 && segs[0] === 'index');

      const wikiCrumb = onWikiRoot
        ? `<span class="bc-current">${escapeHtml(this.currentWiki)}</span>`
        : `<a class="bc-link" data-wiki="${escapeHtml(this.currentWiki)}" href="/?mode=wiki&wiki=${encodeURIComponent(this.currentWiki)}">${escapeHtml(this.currentWiki)}</a>`;
      crumbs.push(wikiCrumb);

      if (!onWikiRoot) {
        const leafIdx = segs.length - 1;
        segs.forEach((seg, i) => {
          if (i === leafIdx) {
            const label = this.currentTitle || seg;
            crumbs.push(`<span class="bc-current">${escapeHtml(label)}</span>`);
          } else {
            // Cartella intermedia: nessuna pagina garantita → testo non-link.
            crumbs.push(`<span class="bc-folder">${escapeHtml(seg)}</span>`);
          }
        });
      }
      wrap.innerHTML = crumbs.join(sep);
    }

    this.contentEl.insertBefore(wrap, this.contentEl.firstChild);
    wrap.querySelectorAll('a[data-home]').forEach(a => {
      a.addEventListener('click', (e) => { e.preventDefault(); this.loadHome(); });
    });
    wrap.querySelectorAll('a[data-wiki]').forEach(a => {
      a.addEventListener('click', (e) => { e.preventDefault(); this.loadWikiPage(a.dataset.wiki, 'index.md'); });
    });
  }

  _wireWikiLinks() {
    this.contentEl.querySelectorAll('a.wikilink').forEach(a => {
      a.addEventListener('click', (e) => {
        const href = a.getAttribute('href') || '';
        if (href.startsWith('#') || href.startsWith('http://') || href.startsWith('https://') || href.startsWith('mailto:')) return;
        if (href.includes('..')) {
          console.warn('Dead or invalid wikilink:', href);
          return;
        }
        e.preventDefault();
        const url = new URL(href, window.location.href);
        const wiki = url.searchParams.get('wiki') || this.currentWiki;
        const page = url.searchParams.get('page');
        const hash = url.hash;
        if (wiki && page) { this.loadWikiPage(wiki, page); }
        else if (wiki) { this.loadWikiPage(wiki, 'index.md'); }
        else { this.loadHome(); }
        if (hash) this._scrollToHash(hash);
      });
    });
  }

  _scrollToHash(hash) {
    const id = hash.slice(1);
    if (!id) return;
    setTimeout(() => {
      const el = document.getElementById(id) || document.querySelector(`a[name="${CSS.escape(id)}"]`);
      el?.scrollIntoView({ behavior: 'smooth' });
    }, 200);
  }

  async loadTree(wiki) {
    try {
      const data = await api.getTree(wiki);
      this.filesDrawerBody.innerHTML = '';
      const nav = document.createElement('nav');
      nav.className = 'wiki-tree';
      nav.appendChild(renderTree(data.children || []));
      this.filesDrawerBody.appendChild(nav);
      this.wireTreeLinks(wiki);
    } catch (err) {
      this.filesDrawerBody.innerHTML = `<div class="ftree-path">${i18n.t('wiki.failedToLoadFiles')}</div>`;
    }
  }

  wireTreeLinks(wiki) {
    const self = this;
    this.filesDrawerBody.querySelectorAll('.tree-folder').forEach(folder => wireTreeFolder(folder, {}));
    wireTreeFiles(this.filesDrawerBody.querySelectorAll('.tree-file'), (path) => {
      // Remove active from previously active item
      this.filesDrawerBody.querySelectorAll('.tree-file.active').forEach(el => el.classList.remove('active'));
      // Find and activate the clicked item
      const target = document.activeElement?.closest('.tree-file') ||
        this.filesDrawerBody.querySelector(`.tree-file[data-path="${CSS.escape(path)}"]`);
      target?.classList.add('active');
      if (path === '_index.md') { self.loadHome(); }
      else {
        const parts = path.replace(/\\/g, '/').split('/');
        if (parts.length >= 2 && parts[1] === 'wiki') {
          self.loadWikiPage(parts[0], parts.slice(2).join('/') || 'index.md');
        } else if (wiki) {
          self.loadWikiPage(wiki, path);
        }
      }
      window.mobileApp.drawer.closeAll();
    });
  }

  async _renderMermaid() {
    // Carica mermaid solo se in pagina c'è davvero un diagramma: aprire la
    // wiki su una nota di testo non deve costare 3,2 MB di JS.
    if (!this.contentEl.querySelector('pre.mermaid-block')) return;
    if (typeof mermaid === 'undefined') {
      try {
        await ensureVendor('/html-mobile/assets/vendor/mermaid@10/dist/mermaid.min.js');
      } catch (err) {
        console.error('Mermaid load failed:', err);
        return;
      }
    }
    // Senza initialize, mermaid usa la palette chiara di default: riquadri
    // lavanda su fondo scuro. Dei sette temi quattro sono scuri (chanel,
    // synthwave, kyoto, sticker) e tre chiari (fumetto, y2k, pietra), quindi la
    // scelta segue `color-scheme` invece di essere fissata. Rieseguito a ogni
    // render, così un cambio di tema a pagina aperta viene raccolto.
    const light = getComputedStyle(document.documentElement).colorScheme === 'light';
    mermaid.initialize({
      startOnLoad: false,
      theme: light ? 'default' : 'dark',
      // I diagrammi arrivano dal modello: `strict` tiene l'HTML fuori dalle
      // etichette. È già il default di mermaid, lo rendiamo esplicito.
      securityLevel: 'strict',
    });
    this.contentEl.querySelectorAll('pre.mermaid-block').forEach((block, i) => {
      const code = block.querySelector('code');
      if (!code) return;
      try {
        mermaid.render(`mermaid-${Date.now()}-${i}`, code.textContent).then(({ svg }) => {
          block.innerHTML = svg;
        });
      } catch (err) { console.error('Mermaid render failed:', err); }
    });
  }

  _renderLatex() {
    if (typeof renderMathInElement === 'function') {
      try { renderMathInElement(this.contentEl, { delimiters: [{left: '$$', right: '$$', display: true}, {left: '$', right: '$', display: false}] }); }
      catch (e) { /* silent */ }
    }
  }

  _renderAuditToggle() {
    const mk = (mode, label) =>
      `<button data-audit-mode="${mode}" class="${this._auditMode === mode ? 'active' : ''}">${label}</button>`;
    return `<div class="audit-mode-toggle">${mk('open', i18n.t('wiki.auditsOpen'))}${mk('resolved', i18n.t('wiki.auditsResolved'))}</div>`;
  }

  _wireAuditToggle() {
    this.auditDrawerBody.querySelectorAll('button[data-audit-mode]').forEach(btn => {
      btn.addEventListener('click', () => {
        const mode = btn.dataset.auditMode;
        if (mode === this._auditMode) return;
        this._auditMode = mode;
        this.loadAudits(this.currentPath);
      });
    });
  }

  async loadAudits(path) {
    const toggle = this._renderAuditToggle();
    try {
      const data = await api.getAudits({ wiki: this.currentWiki, targetPath: path, mode: this._auditMode });
      if (!data.entries?.length) {
        const emptyMsg = this._auditMode === 'resolved'
          ? i18n.t('wiki.noResolvedAudits') : i18n.t('wiki.noOpenAudits');
        this.auditDrawerBody.innerHTML = `${toggle}<div class="audit-item"><div class="ai-title">${emptyMsg}</div></div>`;
        this._wireAuditToggle();
        return;
      }
      const isOpen = this._auditMode === 'open';
      this.auditDrawerBody.innerHTML = toggle + data.entries.map(e => {
        const body = (e.body || '')
          .replace(/^#\s*Comment\s*/i, '')
          .split(/^#\s*Resolution/im)[0]
          .replace(/<!--[\s\S]*?-->/g, '').trim();
        const when = new Date(e.created).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        const sev = escapeHtml(e.severity || '');
        const tagClass = isOpen ? `tag-${sev}` : 'tag-resolved';
        const tagLabel = isOpen ? sev : i18n.t('wiki.auditsResolved');
        const resolveBtn = isOpen
          ? `<div style="margin-top:4px"><button data-resolve="${escapeHtml(e.id)}" style="font-size:10px;padding:2px 6px;background:var(--surface-2);border:0.5px solid var(--border);color:var(--text-faint);border-radius:var(--radius-sm);cursor:pointer;">${i18n.t('wiki.markResolved')}</button></div>`
          : '';
        return `<div class="audit-item sev-${sev}">
          <div class="ai-title"><i class="ti ti-alert-circle" style="color:var(--accent);font-size:14px"></i> ${escapeHtml(body.substring(0, 50))}${body.length > 50 ? '...' : ''} <span class="audit-tag ${tagClass}">${tagLabel}</span></div>
          <div class="ai-meta">${escapeHtml(e.author)} \u00b7 ${when}</div>
          ${resolveBtn}
        </div>`;
      }).join('');
      this._wireAuditToggle();
      this.auditDrawerBody.querySelectorAll('button[data-resolve]').forEach(btn => {
        btn.addEventListener('click', async () => {
          const id = btn.dataset.resolve;
          const note = await promptDialog(i18n.t('wiki.resolutionNote'), { placeholder: i18n.t('wiki.resolutionNote') });
          if (note === null) return;  // annullato
          try {
            await api.resolveAudit(id, note, this.currentWiki);
            await this.loadAudits(this.currentPath);
          } catch (err) { showToast(i18n.t('wiki.failedToResolve') + err.message, 'error'); }
        });
      });
    } catch (err) {
      this.auditDrawerBody.innerHTML = `${toggle}<div class="audit-item"><div class="ai-title">${i18n.t('wiki.failedToLoadAudits')}</div></div>`;
      this._wireAuditToggle();
    }
  }

  initWikiFeedback() {
    const popoverBtn = this._popoverBtn;
    const dialog = document.getElementById('wiki-feedback-dialog');
    const preview = document.getElementById('wiki-feedback-preview');
    const textarea = document.getElementById('wiki-feedback-comment');
    const form = document.getElementById('wiki-feedback-form');
    const cancelBtn = document.getElementById('wiki-feedback-cancel');

    popoverBtn?.addEventListener('click', async () => {
      const sel = document.getSelection();
      if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;
      const text = sel.toString();
      if (!text.trim()) return;
      const raw = this.rawMarkdown;
      const offsets = this._resolveSelectionToOffsets(raw, text);
      if (!offsets) {
        // Ancoraggio non univoco: l'audit potrebbe puntare al punto sbagliato.
        // Avvisa esplicitamente invece di ancorare in sordina.
        if (!(await confirmDialog(i18n.t('wiki.createAuditAnyway')))) return;
      }
      this._pending = offsets || { selStart: raw.indexOf(text), selEnd: raw.indexOf(text) + text.length };
      if (this._pending.selStart < 0) { showToast(i18n.t('wiki.couldNotCreateAudit'), 'error'); return; }
      preview.textContent = text.length > 400 ? text.slice(0, 400) + '\u2026' : text;
      textarea.value = '';
      dialog?.showModal();
      setTimeout(() => textarea?.focus(), 30);
      if (this._popover) this._popover.classList.add('hidden');
    });

    cancelBtn?.addEventListener('click', (e) => { e.preventDefault(); dialog?.close(); this._pending = null; });

    form?.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!this._pending || !this.currentWiki) { showToast(i18n.t('wiki.auditsOnlyInWiki'), 'error'); return; }
      const severity = form.querySelector('input[name="severity"]:checked')?.value || 'warn';
      const comment = textarea?.value.trim();
      if (!comment) { showToast(i18n.t('wiki.commentEmpty'), 'error'); return; }
      try {
        await api.createAudit({
          wiki: this.currentWiki, target: this.currentPath,
          rawMarkdown: this.rawMarkdown,
          selStart: this._pending.selStart, selEnd: this._pending.selEnd,
          comment, severity, author: AppState.wiki?.author || 'anonymous',
        });
        dialog?.close();
        this._pending = null;
        await this.loadAudits(this.currentPath);
      } catch (err) { showToast(`${i18n.t('common.error')}: ${err.message}`, 'error'); }
    });
  }

  _resolveSelectionToOffsets(raw, selText) {
    if (!selText) return null;
    // Match univoco tollerante agli spazi: i run di whitespace della selezione
    // (resa dal DOM) matchano \s+ nel markdown grezzo, così una selezione che
    // attraversa a-capo/indentazioni della sorgente risolve comunque. Ritorna
    // gli offset reali nel raw (null se assente o non univoco).
    const uniqueMatch = (str) => {
      const trimmed = (str || '').trim();
      if (!trimmed) return null;
      const pattern = trimmed
        .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        .replace(/\s+/g, '\\s+');
      let re;
      try { re = new RegExp(pattern, 'g'); } catch { return null; }
      let m, first = null, count = 0;
      while ((m = re.exec(raw)) !== null) {
        if (count === 0) first = m;
        count++;
        if (count > 1) break;
        if (m.index === re.lastIndex) re.lastIndex++;  // guardia match vuoto
      }
      return count === 1 ? { selStart: first.index, selEnd: first.index + first[0].length } : null;
    };
    const direct = uniqueMatch(selText);
    if (direct) return direct;
    const words = selText.split(/\s+/).filter(Boolean);
    for (let len = Math.min(words.length, 10); len >= 3; len--) {
      for (let start = 0; start + len <= words.length; start++) {
        const hit = uniqueMatch(words.slice(start, start + len).join(' '));
        if (hit) return hit;
      }
    }
    return null;
  }

  handleAction(action) {
    if (action === 'save') console.log('Save wiki page:', this.currentPath);
  }
}

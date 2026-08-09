/** Mobile Workspace Controller — Finder-style folder browser with CodeMirror editor. */

import { api } from './shared/api-client.js';
import { escapeHtml, getFileExtension, showToast } from './shared/utils.js';
import { confirmDialog, promptDialog } from './shared/dialog.js';
import { i18n } from './shared/i18n.js';
import { currentTheme } from './shared/theme.js';
import { advancedMode } from './shared/advanced-mode.js';
import { openImageLightbox } from './shared/image-lightbox.js';

const CM_THEMES = { dark: 'darcula', light: 'eclipse' };

// ── Apple-style SVG icons for grid view ──
// Gradients defined once in a hidden SVG container injected on first use.

let _gridDefsInjected = false;

function ensureGridDefs() {
  if (_gridDefsInjected) return;
  _gridDefsInjected = true;
  const container = document.createElement('div');
  container.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden';
  container.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" style="position:absolute">
  <defs>
    <linearGradient id="gFolderGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#F7C948"/>
      <stop offset="100%" stop-color="#E8912D"/>
    </linearGradient>
    <linearGradient id="gFolderFront" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#F5A623"/>
      <stop offset="100%" stop-color="#D4800E"/>
    </linearGradient>
    <linearGradient id="gDocGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#FAFAFA"/>
      <stop offset="100%" stop-color="#E8E8EC"/>
    </linearGradient>
    <linearGradient id="gDocFold" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#D4D4D8"/>
      <stop offset="100%" stop-color="#B8B8C0"/>
    </linearGradient>
    <filter id="gShadow" x="-10%" y="-10%" width="130%" height="140%">
      <feDropShadow dx="0" dy="0.8" stdDeviation="0.6" flood-opacity="0.18"/>
    </filter>
  </defs>
</svg>`;
  document.body.appendChild(container);
}

const FOLDER_ICON_SVG = `<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <g filter="url(#gShadow)">
    <path d="M3 6c0-1.1.9-2 2-2h4.5l1.5 2H19c1.1 0 2 .9 2 2v10c0 1.1-.9 2-2 2H5c-1.1 0-2-.9-2-2V6z" fill="url(#gFolderGrad)" stroke="#D4800E" stroke-width="0.6" stroke-linejoin="round"/>
    <path d="M3 9h18v8c0 1.1-.9 2-2 2H5c-1.1 0-2-.9-2-2V9z" fill="url(#gFolderFront)" stroke="#C47008" stroke-width="0.4"/>
  </g>
</svg>`;

function createFileIcon(bg, fg, text) {
  return `<svg class="file-icon-grid" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <g filter="url(#gShadow)">
      <path d="M6 2c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V7.5L14.5 2H6z" fill="url(#gDocGrad)" stroke="#A1A1AA" stroke-width="0.5"/>
      <path d="M14.5 2v5.5H20" fill="url(#gDocFold)" stroke="#A1A1AA" stroke-width="0.5" stroke-linejoin="round"/>
    </g>
    <rect x="6" y="11" width="12" height="7" rx="1.5" fill="${bg}"/>
    <text x="12" y="16.2" text-anchor="middle" font-size="6" font-weight="700" fill="${fg}" font-family="Inter,system-ui,sans-serif">${text}</text>
  </svg>`;
}

const FILE_ICONS = {
  js:   createFileIcon('#f7df1e', '#000', 'JS'),
  ts:   createFileIcon('#3178c6', '#fff', 'TS'),
  py:   createFileIcon('#3776ab', '#fff', 'PY'),
  md:   createFileIcon('#3B82F6', '#fff', 'MD'),
  json: createFileIcon('#888',    '#fff', '{}'),
  txt:  createFileIcon('#a0a0a0','#fff', 'TXT'),
  html: createFileIcon('#e34c26', '#fff', 'HTML'),
  css:  createFileIcon('#264de4', '#fff', 'CSS'),
  sh:   createFileIcon('#4eaa25', '#fff', 'SH'),
  yaml: createFileIcon('#cb171e', '#fff', 'YML'),
  yml:  createFileIcon('#cb171e', '#fff', 'YML'),
  log:  createFileIcon('#555',    '#fff', 'LOG'),
  go:   createFileIcon('#00ADD8','#fff', 'GO'),
  rs:   createFileIcon('#dea584','#000', 'RS'),
  xml:  createFileIcon('#e34c26','#fff', 'XML'),
  sql:  createFileIcon('#f29111','#fff', 'SQL'),
};

const FILE_GENERIC_ICON = `<svg class="file-icon-grid" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <g filter="url(#gShadow)">
    <path d="M6 2c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V7.5L14.5 2H6z" fill="url(#gDocGrad)" stroke="#A1A1AA" stroke-width="0.5"/>
    <path d="M14.5 2v5.5H20" fill="url(#gDocFold)" stroke="#A1A1AA" stroke-width="0.5" stroke-linejoin="round"/>
    <line x1="8" y1="12" x2="16" y2="12" stroke="#D4D4D8" stroke-width="0.8" stroke-linecap="round"/>
    <line x1="8" y1="15" x2="14" y2="15" stroke="#D4D4D8" stroke-width="0.8" stroke-linecap="round"/>
  </g>
</svg>`;

const EXT_LANG = {
  js: 'javascript', ts: 'typescript', py: 'python', md: 'markdown',
  json: 'json', jsonl: 'json', html: 'html', css: 'css', sh: 'text',
  yaml: 'yaml', yml: 'yaml', log: 'text', txt: 'text',
  go: 'go', rs: 'rust', c: 'clike', cpp: 'clike', java: 'clike',
  xml: 'xml', sql: 'sql',
};

// Scorciatoia per i binari ovvi: si apre direttamente l'app di sistema
// senza tentare la lettura (evita di scaricare fino a 1 MB per un 415).
// NON è un gate di leggibilità: per ogni altra estensione decide il
// backend sniffando il contenuto (415 = binario → app di sistema).
// Immagini renderizzabili dalla WebView: il tap apre il lightbox interno
// (long-press → app di sistema). heic/heif potrebbero non decodificare:
// l'onerror del lightbox ripiega sull'app di sistema.
const IMAGE_EXTS = new Set([
  'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'avif', 'heic', 'heif',
]);

// Sopra questa soglia niente thumbnail nell'explorer (si terrebbe in memoria
// l'intera immagine solo per un'icona): resta l'icona generica.
const MAX_THUMB_BYTES = 10 * 1024 * 1024;

const KNOWN_BINARY_EXTS = new Set([
  'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'heic', 'avif',
  'mp3', 'ogg', 'wav', 'm4a', 'flac', 'opus', 'aac',
  'mp4', 'mkv', 'webm', 'avi', 'mov', '3gp',
  'pdf', 'zip', 'gz', 'tar', 'bz2', 'xz', '7z', 'rar', 'jar',
  'apk', 'so', 'db', 'sqlite', 'sqlite3',
  'woff', 'woff2', 'ttf', 'otf', 'eot',
  'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt', 'ods', 'odp',
  'jbk',
]);

function getFileIcon(ext) {
  ensureGridDefs();
  return FILE_ICONS[ext] || FILE_GENERIC_ICON;
}

function parentPath(path) {
  const idx = path.lastIndexOf('/');
  return idx > 0 ? path.substring(0, idx) : '';
}

export class WorkspaceController {
  constructor() {
    this.viewEl = document.getElementById('view-workspace');
    this.explorerEl = document.getElementById('ws-explorer');
    this.breadcrumbEl = document.getElementById('ws-breadcrumb');
    this.gridEl = document.getElementById('ws-grid');
    this.emptyEl = document.getElementById('ws-empty');
    this.viewerEl = document.getElementById('workspace-viewer');
    this.loadingEl = document.getElementById('workspace-loading');
    this.editor = null;
    this.currentDir = '';
    this.currentPath = '';
    this.viewMode = 'explorer';
    // When a file is opened from another view (e.g. Apps → edit skill), the
    // editor "back" returns to that view instead of the workspace explorer.
    this._returnMode = null;
    // Monotonic navigation token: guards against stale-response races when the
    // user navigates rapidly (only the latest navigateTo() writes the grid).
    this._navToken = 0;
    // Object URL delle thumbnail correnti, revocati a ogni re-render della
    // griglia per non accumulare blob in memoria.
    this._thumbUrls = [];

    this.ready = this.init();
    window.addEventListener('advancedmodechange', () => this.navigateTo(this.currentDir));
  }

  showLoading() {
    if (this.loadingEl) this.loadingEl.classList.add('active');
  }

  hideLoading() {
    if (this.loadingEl) this.loadingEl.classList.remove('active');
  }

  async init() {
    this.showLoading();
    await this.navigateTo('');
    this.hideLoading();
  }

  activate() {
    if (this.viewMode === 'editor') {
      this.showEditorView();
    } else {
      this.showExplorerView();
      this.navigateTo(this.currentDir);
    }
    this._syncHeaderBack();
  }

  deactivate() {
    // Cleanup
  }

  /* Tasto Indietro hardware, invocato dalla shell prima di toccare la history.
     Ritorna true se la pressione è stata consumata qui dentro.

     L'editor aperto da un'altra sezione (Apps → modifica skill) è il caso
     particolare: la entry di quella sezione è ancora nello stack, quindi si
     smonta l'editor ma si lascia proseguire il back, che ci torna da sé —
     rimandarcelo con switchMode impilerebbe una entry *in avanti* mentre si
     sta andando indietro. */
  handleBack() {
    if (this.viewMode === 'editor') {
      const ret = this._returnMode;
      this._returnMode = null;
      this.backToExplorerAt(ret ? '' : this.currentDir);
      return !ret;
    }
    if (this.currentDir) {
      this.navigateTo(parentPath(this.currentDir));
      return true;
    }
    return false;
  }

  // ── Navigation ──

  async navigateTo(dirPath) {
    const token = ++this._navToken;
    this.currentDir = dirPath;
    this.viewMode = 'explorer';
    this.showExplorerView();
    this._syncHeaderBack();

    this.renderBreadcrumb(dirPath);

    try {
      const data = await api.listWorkspace(dirPath);
      if (token !== this._navToken) return;  // superseded by a newer navigation
      this.renderGrid(data.items || []);
    } catch (err) {
      if (token !== this._navToken) return;  // don't surface errors of stale requests
      this.gridEl.innerHTML = '';
      const sub = this.emptyEl.querySelector('.ws-empty-sub');
      if (sub) sub.textContent = i18n.t('workspace.failedToLoad') + err.message;
      this.emptyEl.style.display = '';
    }
  }

  showExplorerView() {
    this.explorerEl.style.display = '';
    this.viewerEl.classList.remove('active');
    this._syncHeaderBack();
  }

  showEditorView() {
    this.explorerEl.style.display = 'none';
    this.viewerEl.classList.add('active');
    this._syncHeaderBack();
  }

  _syncHeaderBack() {
    const header = window.mobileApp?.header;
    if (!header) return;
    if (this.viewMode === 'editor' || this.currentDir) {
      header.showAction('ws-back');
    } else {
      header.hideAction('ws-back');
    }
  }

  // ── Breadcrumb ──

  renderBreadcrumb(dirPath, fileName) {
    this.breadcrumbEl.innerHTML = '';

    const rootCrumb = document.createElement('span');
    rootCrumb.className = 'ws-crumb';
    rootCrumb.textContent = i18n.t('workspace.root');
    rootCrumb.addEventListener('click', () => this.backToExplorerAt(''));
    this.breadcrumbEl.appendChild(rootCrumb);

    const parts = dirPath ? dirPath.split('/').filter(Boolean) : [];
    let accumulated = '';

    for (let i = 0; i < parts.length; i++) {
      const sep = document.createElement('span');
      sep.className = 'ws-sep';
      sep.textContent = '\u203a';
      this.breadcrumbEl.appendChild(sep);

      accumulated = accumulated ? accumulated + '/' + parts[i] : parts[i];
      const crumb = document.createElement('span');
      crumb.className = 'ws-crumb';
      crumb.textContent = parts[i];

      const targetPath = accumulated;
      crumb.addEventListener('click', () => this.backToExplorerAt(targetPath));

      this.breadcrumbEl.appendChild(crumb);
    }

    if (fileName) {
      const sep = document.createElement('span');
      sep.className = 'ws-sep';
      sep.textContent = '\u203a';
      this.breadcrumbEl.appendChild(sep);

      const fileCrumb = document.createElement('span');
      fileCrumb.className = 'ws-crumb';
      fileCrumb.textContent = fileName;
      this.breadcrumbEl.appendChild(fileCrumb);

      const saveBtn = document.createElement('button');
      saveBtn.className = 'ws-save-btn';
      saveBtn.textContent = i18n.t('workspace.save');
      saveBtn.addEventListener('click', () => this.saveFile());
      this.breadcrumbEl.appendChild(saveBtn);
    }

    this.breadcrumbEl.scrollLeft = this.breadcrumbEl.scrollWidth;
  }

  // ── Grid rendering ──

  renderGrid(items) {
    items = advancedMode() ? items : items.filter(i => !i.internal);
    this._thumbUrls.splice(0).forEach((u) => URL.revokeObjectURL(u));
    this.gridEl.innerHTML = '';

    if (!items.length) {
      this.emptyEl.style.display = '';
      return;
    }
    this.emptyEl.style.display = 'none';

    const dirs = items.filter(i => i.type === 'directory').sort((a, b) => a.name.localeCompare(b.name));
    const files = items.filter(i => i.type === 'file').sort((a, b) => a.name.localeCompare(b.name));

    for (const item of dirs) {
      this.gridEl.appendChild(this._createDirItem(item));
    }
    for (const item of files) {
      this.gridEl.appendChild(this._createFileItem(item));
    }
  }

  _createDirItem(item) {
    ensureGridDefs();
    const itemPath = this.currentDir ? this.currentDir + '/' + item.name : item.name;

    const el = document.createElement('div');
    el.className = 'ws-item ws-item-dir';
    el.dataset.path = itemPath;
    el.dataset.kind = 'dir';

    el.innerHTML =
      `<button class="ws-item-menu" title="${i18n.t('workspace.actions')}"><i class="ti ti-dots-vertical"></i></button>` +
      `<div class="ws-item-icon folder-icon">${FOLDER_ICON_SVG}</div>` +
      `<div class="ws-item-name">${escapeHtml(item.name)}</div>`;

    el.addEventListener('click', (e) => {
      if (e.target.closest('.ws-item-menu')) return;
      this.navigateTo(itemPath);
    });

    this._setupLongPress(el, () => {
      this.showContextSheet({ path: itemPath, kind: 'dir', name: item.name });
    });

    el.querySelector('.ws-item-menu').addEventListener('click', (e) => {
      e.stopPropagation();
      this.showContextSheet({ path: itemPath, kind: 'dir', name: item.name });
    });

    return el;
  }

  _createFileItem(item) {
    const itemPath = this.currentDir ? this.currentDir + '/' + item.name : item.name;
    const ext = item.extension ? item.extension.replace('.', '') : getFileExtension(item.name);
    const icon = getFileIcon(ext);

    const el = document.createElement('div');
    el.className = 'ws-item ws-item-file';
    el.dataset.path = itemPath;
    el.dataset.kind = 'file';
    el.dataset.ext = ext;

    el.innerHTML =
      `<button class="ws-item-menu" title="${i18n.t('workspace.actions')}"><i class="ti ti-dots-vertical"></i></button>` +
      `<div class="ws-item-icon">${icon}</div>` +
      `<div class="ws-item-name">${escapeHtml(item.name)}</div>`;

    el.addEventListener('click', (e) => {
      if (e.target.closest('.ws-item-menu')) return;
      this.openFile(itemPath, ext);
    });

    this._setupLongPress(el, () => {
      this.showContextSheet({ path: itemPath, kind: 'file', name: item.name });
    });

    el.querySelector('.ws-item-menu').addEventListener('click', (e) => {
      e.stopPropagation();
      this.showContextSheet({ path: itemPath, kind: 'file', name: item.name });
    });

    if (IMAGE_EXTS.has(ext) && (item.size ?? 0) <= MAX_THUMB_BYTES) {
      this._loadThumb(el, itemPath);
    }

    return el;
  }

  /** Sostituisce l'icona generica con una thumbnail dell'immagine (36×36,
   *  object-fit: cover → niente stretch). Best-effort: su qualsiasi errore
   *  (fetch, formato non decodificabile tipo HEIC) resta l'icona. */
  _loadThumb(el, itemPath) {
    api.downloadWorkspaceBlob(itemPath).then((blob) => {
      const holder = el.querySelector('.ws-item-icon');
      if (!holder || !el.isConnected) return;
      const url = URL.createObjectURL(blob);
      this._thumbUrls.push(url);
      const img = document.createElement('img');
      img.className = 'ws-thumb';
      img.alt = '';
      img.addEventListener('load', () => {
        if (!el.isConnected) return;
        holder.replaceChildren(img);
      });
      img.src = url;
    }).catch(() => { /* icona generica invariata */ });
  }

  _setupLongPress(el, callback) {
    let timer = null;

    const start = (e) => {
      if (e.button !== undefined && e.button !== 0 && e.type !== 'touchstart') return;
      timer = setTimeout(() => {
        el.dataset.longpress = 'true';
        callback(e);
      }, 600);
    };

    const cancel = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    };

    el.addEventListener('pointerdown', start);
    el.addEventListener('pointerup', cancel);
    el.addEventListener('pointermove', cancel);
    el.addEventListener('pointerleave', cancel);
    el.addEventListener('pointercancel', cancel);
  }

  // ── Context menu ──

  showContextSheet(info) {
    const sheet = document.getElementById('ws-context-sheet');
    document.getElementById('ws-context-title').textContent = info.name;

    const actions = [];

    if (info.kind === 'dir') {
      actions.push({ icon: 'ti-file-plus', label: i18n.t('workspace.newFile'), action: 'newFile' });
      actions.push({ icon: 'ti-folder-plus', label: i18n.t('workspace.newFolder'), action: 'newFolder' });
    }
    if (info.kind === 'file') {
      actions.push({ icon: 'ti-external-link', label: i18n.t('workspace.openWithSystemApp'), action: 'openExternal' });
      actions.push({ icon: 'ti-share', label: i18n.t('workspace.share'), action: 'share' });
      actions.push({ icon: 'ti-download', label: i18n.t('workspace.saveToDownloads'), action: 'saveDownloads' });
    }
    actions.push({ icon: 'ti-edit', label: i18n.t('workspace.rename'), action: 'rename' });
    actions.push({ icon: 'ti-copy', label: i18n.t('workspace.clone'), action: 'clone' });
    actions.push({ icon: 'ti-trash', label: i18n.t('workspace.delete'), action: 'delete', danger: true });

    const actionsEl = document.getElementById('ws-context-actions');
    actionsEl.innerHTML = actions.map(a =>
      `<button class="oc-sheet-action${a.danger ? ' danger' : ''}" data-action="${a.action}">
        <i class="ti ${a.icon}"></i>${a.label}
      </button>`
    ).join('');

    const cancelBtn = document.getElementById('ws-context-cancel');
    const closeSheet = () => sheet.close();

    actionsEl.querySelectorAll('.oc-sheet-action').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        sheet.close();
        this.handleSheetAction(btn.dataset.action, info);
      });
    });

    cancelBtn.onclick = closeSheet;
    sheet.addEventListener('close', () => { cancelBtn.onclick = null; }, { once: true });
    sheet.addEventListener('click', (e) => {
      if (e.target === sheet) sheet.close();
    }, { once: true });

    sheet.showModal();
  }

  async handleSheetAction(action, info) {
    const path = info.path;

    switch (action) {
      case 'openExternal': {
        this.openWithSystemApp(path, info.name);
        break;
      }
      case 'share': {
        this.shareFile(path);
        break;
      }
      case 'saveDownloads': {
        this.saveToDownloads(path);
        break;
      }
      case 'newFile':
      case 'newFolder': {
        await this._createEntry(action, path);
        break;
      }
      case 'rename': {
        // `promptDialog`, non la prompt() nativa: nella WebView dell'app i
        // dialoghi JS nativi non compaiono e ritornano null, quindi rinominare
        // non faceva niente — senza nemmeno un messaggio.
        const newName = await promptDialog(i18n.t('workspace.newName'), { initial: info.name });
        if (!newName || newName === info.name) return;
        const base = parentPath(path);
        const newPath = base ? `${base}/${newName}` : newName;
        try {
          await api.renameWorkspace(path, newPath);
          if (this.currentPath === path) {
            this.currentPath = newPath;
          }
          await this.navigateTo(this.currentDir);
        } catch (err) {
          showToast(i18n.t('workspace.error') + err.message, 'error');
        }
        break;
      }
      case 'clone': {
        try {
          await api.copyWorkspace(path);
          await this.navigateTo(this.currentDir);
        } catch (err) {
          showToast(i18n.t('workspace.error') + err.message, 'error');
        }
        break;
      }
      case 'delete': {
        const confirmed = await confirmDialog(i18n.t('workspace.deleteConfirm', { name: info.name }));
        if (!confirmed) return;
        try {
          await api.deleteWorkspace(path);
          if (this.currentPath === path || this.currentPath.startsWith(path + '/')) {
            this.currentPath = '';
            this.backToExplorer();
            return;
          }
          await this.navigateTo(this.currentDir);
        } catch (err) {
          showToast(i18n.t('workspace.error') + err.message, 'error');
        }
        break;
      }
    }
  }

  // ── File editor ──

  async openFile(fullPath, ext) {
    ext = ext || getFileExtension(fullPath);
    const name = fullPath.split('/').pop();

    // Immagini: lightbox interno (la vista corrente non cambia).
    if (IMAGE_EXTS.has(ext)) {
      this.previewImage(fullPath, name);
      return;
    }

    // Binari noti: direttamente all'app di sistema, senza toccare la
    // vista corrente (l'explorer resta dov'è).
    if (KNOWN_BINARY_EXTS.has(ext)) {
      this.openWithSystemApp(fullPath, name);
      return;
    }

    let data;
    try {
      data = await api.readWorkspaceFile(fullPath);
    } catch (err) {
      // 415 = il backend ha sniffato contenuto binario → app di sistema.
      if (err.status === 415) {
        this.openWithSystemApp(fullPath, name);
        return;
      }
      this._enterEditorView(fullPath, name);
      this.renderError(err.message);
      return;
    }

    this._enterEditorView(fullPath, name);
    this.renderCodeViewer(name, data.content, ext);
  }

  _enterEditorView(fullPath, name) {
    // Opening a file through normal explorer navigation clears any prior
    // cross-view origin; callers that want "back" to leave the workspace
    // set _returnMode after awaiting this.
    this._returnMode = null;
    this.currentPath = fullPath;
    this.viewMode = 'editor';
    this.renderBreadcrumb(this.currentDir, name);
    this.showEditorView();
  }

  /** Apre il file col viewer di sistema Android via bridge nativo.
   *  Fallback (bridge assente, es. debug da browser desktop): la vecchia
   *  schermata con il link di download. */
  openWithSystemApp(fullPath, name) {
    const bridge = window.JennyNative;
    if (bridge && typeof bridge.openFile === 'function') {
      try {
        if (bridge.openFile(fullPath)) return;
      } catch (e) { /* bridge rotto: si ripiega sul download */ }
    }
    this._enterEditorView(fullPath, name);
    this.renderBinary(name, fullPath);
  }

  /** Condivide il file con lo share sheet di sistema (bridge nativo). */
  shareFile(fullPath) {
    const bridge = window.JennyNative;
    if (bridge && typeof bridge.shareFile === 'function') {
      try {
        if (bridge.shareFile(fullPath)) return;
      } catch (e) { /* fall through */ }
    }
    showToast(i18n.t('workspace.actionFailed'), 'error');
  }

  /** Copia il file nella cartella Download di sistema (bridge nativo). */
  saveToDownloads(fullPath) {
    const bridge = window.JennyNative;
    let ok = false;
    if (bridge && typeof bridge.saveToDownloads === 'function') {
      try {
        ok = bridge.saveToDownloads(fullPath);
      } catch (e) { ok = false; }
    }
    showToast(
      ok ? i18n.t('workspace.savedToDownloads') : i18n.t('workspace.actionFailed'),
      ok ? 'success' : 'error',
    );
  }

  /** Lightbox interno per le immagini: stesso overlay della chat più una
   *  barra azioni (app di sistema / condividi / salva in Download). Se la
   *  WebView non decodifica il formato (es. HEIC) ripiega sull'app di
   *  sistema. */
  async previewImage(fullPath, name) {
    let objectUrl;
    try {
      const blob = await api.downloadWorkspaceBlob(fullPath);
      objectUrl = URL.createObjectURL(blob);
    } catch (err) {
      this.openWithSystemApp(fullPath, name);
      return;
    }

    openImageLightbox(objectUrl, {
      alt: name,
      closeLabel: i18n.t('common.close'),
      actions: [
        { act: 'open', icon: 'ti-external-link', label: i18n.t('workspace.openWithSystemApp') },
        { act: 'share', icon: 'ti-share', label: i18n.t('workspace.share') },
        { act: 'save', icon: 'ti-download', label: i18n.t('workspace.saveToDownloads') },
      ],
      onAction: (act, close) => {
        if (act === 'open') { close(); this.openWithSystemApp(fullPath, name); }
        else if (act === 'share') this.shareFile(fullPath);
        else if (act === 'save') this.saveToDownloads(fullPath);
      },
      onError: () => this.openWithSystemApp(fullPath, name),
      onClose: () => URL.revokeObjectURL(objectUrl),
    });
  }

  backToExplorer() {
    this.backToExplorerAt(this.currentDir);
  }

  backToExplorerAt(dirPath) {
    this.currentPath = '';
    this.viewMode = 'explorer';
    if (this.editor) {
      this.editor.toTextArea();
      this.editor = null;
    }
    this.viewerEl.innerHTML = '<div id="code-editor-mobile"></div>';
    this.showExplorerView();
    this.navigateTo(dirPath);
  }

  async saveFile() {
    if (!this.editor || !this.currentPath) return;
    const confirmed = await confirmDialog(i18n.t('workspace.saveConfirm', { path: this.currentPath }));
    if (!confirmed) return;
    const content = this.editor.getValue();
    try {
      const btn = document.querySelector('.ws-save-btn');
      if (btn) btn.disabled = true;
      await api.writeWorkspaceFile(this.currentPath, content);
      if (btn) { btn.textContent = i18n.t('workspace.saved'); btn.classList.remove('dirty'); }
      setTimeout(() => { if (btn) btn.textContent = i18n.t('workspace.save'); }, 2000);
    } catch (err) {
      const btn = document.querySelector('.ws-save-btn');
      if (btn) { btn.textContent = i18n.t('common.error'); btn.disabled = false; }
    }
  }

  renderCodeViewer(filename, content, ext) {
    const lang = EXT_LANG[ext] || 'text';

    this.viewerEl.innerHTML = '<div id="code-editor-mobile"></div>';

    if (this.editor) {
      this.editor.toTextArea();
      this.editor = null;
    }

    const textarea = document.createElement('textarea');
    textarea.value = content;
    document.getElementById('code-editor-mobile').appendChild(textarea);

    this.editor = CodeMirror.fromTextArea(textarea, {
      mode: lang,
      theme: CM_THEMES[currentTheme().scheme],
      lineNumbers: true,
      lineWrapping: true,
    });

    if (!this._themeListener) {
      this._themeListener = (e) => {
        this.editor?.setOption('theme', CM_THEMES[e.detail.scheme]);
      };
      window.addEventListener('themechange', this._themeListener);
    }

    this.editor.refresh();

    this.editor.setOption('extraKeys', {
      'Ctrl-S': () => this.saveFile(),
      'Cmd-S': () => this.saveFile(),
    });
    this.editor.on('change', () => {
      const btn = document.querySelector('.ws-save-btn');
      if (btn) { btn.textContent = i18n.t('workspace.save'); btn.disabled = false; btn.classList.add('dirty'); }
    });
  }

  renderBinary(filename, path) {
    this.viewerEl.innerHTML = `
      <div style="flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 16px; color: var(--text-faint);">
        <div style="font-size: 48px; opacity: 0.5;">&#128196;</div>
        <div>${i18n.t('workspace.binaryFile')}</div>
        <a href="${escapeHtml(api.getWorkspaceDownloadUrl(path))}" download="${escapeHtml(filename)}"
           style="padding: 8px 20px; background: var(--accent); color: var(--on-accent); text-decoration: none; border-radius: var(--radius); font-size: 12px;">
          ${i18n.t('workspace.download')}
        </a>
      </div>
    `;
  }

  renderError(message) {
    this.viewerEl.innerHTML = `<div style="padding: 20px; color: var(--error);">${i18n.t('workspace.error')}${escapeHtml(message)}</div>`;
  }

  // ── Header action handler ──

  handleAction(action) {
    switch (action) {
      case 'refresh':
        if (this.viewMode === 'editor') return;
        this.navigateTo(this.currentDir);
        break;
      case 'ws-back':
        if (this.viewMode === 'editor') {
          if (this._returnMode) {
            const ret = this._returnMode;
            this._returnMode = null;
            this.backToExplorerAt('');  // reset editor state (root explorer)
            window.mobileApp?.switchMode(ret);
          } else {
            this.backToExplorer();
          }
        } else if (this.currentDir) {
          this.navigateTo(parentPath(this.currentDir));
        }
        break;
      case 'ws-new':
        this._showNewMenu();
        break;
    }
  }

  _showNewMenu() {
    const sheet = document.getElementById('ws-context-sheet');
    document.getElementById('ws-context-title').textContent = i18n.t('workspace.new');

    const actionsEl = document.getElementById('ws-context-actions');
    actionsEl.innerHTML = `
      <button class="oc-sheet-action" data-action="newFile">
        <i class="ti ti-file-plus"></i>${i18n.t('workspace.newFile')}
      </button>
      <button class="oc-sheet-action" data-action="newFolder">
        <i class="ti ti-folder-plus"></i>${i18n.t('workspace.newFolder')}
      </button>
    `;

    const cancelBtn = document.getElementById('ws-context-cancel');
    const closeSheet = () => sheet.close();

    actionsEl.querySelectorAll('.oc-sheet-action').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        sheet.close();
        this._handleNewAction(btn.dataset.action);
      });
    });

    cancelBtn.onclick = closeSheet;
    sheet.addEventListener('close', () => { cancelBtn.onclick = null; }, { once: true });
    sheet.addEventListener('click', (e) => {
      if (e.target === sheet) sheet.close();
    }, { once: true });

    sheet.showModal();
  }

  async _handleNewAction(action) {
    await this._createEntry(action, this.currentDir);
  }

  /** Crea un nuovo file o cartella sotto `baseDir` chiedendo il nome all'utente. */
  async _createEntry(action, baseDir) {
    const isFile = action === 'newFile';
    // Come per il rename: la prompt() nativa non compare nella WebView.
    const name = await promptDialog(
      i18n.t(isFile ? 'workspace.fileName' : 'workspace.folderName'),
    );
    if (!name) return;
    const newPath = baseDir ? `${baseDir}/${name}` : name;
    try {
      if (isFile) {
        await api.writeWorkspaceFile(newPath, '');
      } else {
        await api.createWorkspaceFolder(newPath);
      }
      await this.navigateTo(this.currentDir);
    } catch (err) {
      showToast(i18n.t('workspace.error') + err.message, 'error');
    }
  }
}

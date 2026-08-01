/** Attachment Handler — file picker (immagini, foto, file qualsiasi + scatto
 *  fotocamera via chooser di sistema Android), validazione e base64 encoding
 *  per gli allegati della chat. Il nome storico ``ImageHandler`` è mantenuto
 *  per compatibilità con i chiamanti. */

export class ImageHandler {
  constructor() {
    this._items = [];
    // Cap per-tipo allineati al server (ws_parsing.py): evita che il gateway
    // rifiuti l'intero messaggio quando si superano i limiti.
    this._maxImages = 4;
    this._maxFiles = 4;
    this._maxImageMB = 8;
    this._maxFileMB = 20;
    this._imageTypes = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];
    this._input = null;
    this.onChange = null;
  }

  _createFileInput() {
    const input = document.createElement('input');
    input.type = 'file';
    // Su Android WebView l'attributo ``accept`` è ignorato: è il chooser di
    // sistema nativo (onShowFileChooser) a decidere le sorgenti disponibili
    // (file/galleria/fotocamera). ``accept`` resta come hint per i browser.
    input.accept = '*/*';
    input.multiple = true;
    input.style.display = 'none';
    input.addEventListener('change', () => this._handleFiles(input.files));
    document.body.appendChild(input);
    this._input = input;
  }

  trigger() {
    if (!this._input) this._createFileInput();
    this._input.value = '';
    this._input.click();
  }

  _isImage(file) {
    if (this._imageTypes.includes(file.type)) return true;
    // Le catture da fotocamera Android arrivano spesso senza MIME (``file.type``
    // vuoto o ``application/octet-stream``): ripiega sull'estensione del nome
    // così lo scatto viene trattato come immagine (thumbnail + cap immagini) e
    // non finisce nel bucket "file".
    const generic = !file.type || file.type === 'application/octet-stream';
    return generic && this._looksLikeImageName(file.name);
  }

  _looksLikeImageName(name) {
    const ext = (String(name || '').match(/\.([a-z0-9]+)$/i) || [])[1]?.toLowerCase() || '';
    return ['png', 'jpg', 'jpeg', 'webp', 'gif'].includes(ext);
  }

  _countByKind(isImage) {
    return this._items.filter((it) => it.isImage === isImage).length;
  }

  async _handleFiles(fileList) {
    for (const file of fileList) {
      const isImage = this._isImage(file);
      const cap = isImage ? this._maxImages : this._maxFiles;
      if (this._countByKind(isImage) >= cap) continue;
      const maxBytes = (isImage ? this._maxImageMB : this._maxFileMB) * 1024 * 1024;
      if (file.size > maxBytes) continue;

      const dataUrl = await this._readAsDataUrl(file);
      this._items.push({
        data_url: dataUrl,
        name: file.name,
        mime: file.type || 'application/octet-stream',
        isImage,
        file,
      });
    }
    this.onChange?.(this._items);
  }

  _readAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  remove(index) {
    this._items.splice(index, 1);
    this.onChange?.(this._items);
  }

  clear() {
    this._items = [];
    this.onChange?.(this._items);
  }

  /** Payload inviato al gateway: solo ``data_url`` + ``name`` (il MIME viaggia
   *  già dentro il data URL). Nome storico per compatibilità coi chiamanti. */
  getImages() {
    return this._items.map(({ data_url, name }) => ({ data_url, name }));
  }

  /** Voci per il rendering ottimistico nella bolla utente (thumb immagini /
   *  chip file), allineate al renderer condiviso ``_renderMediaAttachments``.
   *  Solo per le immagini forziamo ``kind``; per il resto lo deduce dal nome. */
  getAttachmentEntries() {
    return this._items.map(({ data_url, name, isImage }) => ({
      url: data_url,
      name,
      ...(isImage ? { kind: 'image' } : {}),
    }));
  }

  get count() {
    return this._items.length;
  }
}

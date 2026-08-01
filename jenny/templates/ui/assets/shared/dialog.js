/** Confirm Dialog — reusable modal with backdrop. */

import { i18n } from './i18n.js';

export function confirmDialog(message, okText, cancelText) {
  okText = okText || i18n.t('dialog.confirm');
  cancelText = cancelText || i18n.t('dialog.cancel');
  const dialog = document.getElementById('oc-confirm-dialog');
  if (!dialog) return Promise.resolve(false);
  // Dialog condiviso: una seconda apertura (doppio tap) mentre è già aperto
  // farebbe throware showModal(); si tratta come "annullato".
  if (dialog.open) return Promise.resolve(false);

  const msgEl = document.getElementById('oc-confirm-message');
  const okBtn = document.getElementById('oc-confirm-ok');
  const cancelBtn = document.getElementById('oc-confirm-cancel');

  if (!msgEl || !okBtn || !cancelBtn) return Promise.resolve(false);

  msgEl.textContent = message;
  okBtn.textContent = okText;
  cancelBtn.textContent = cancelText;

  return new Promise((resolve) => {
    let settled = false;
    const cleanup = () => {
      if (settled) return;
      settled = true;
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      dialog.removeEventListener('close', onClose);
      dialog.removeEventListener('cancel', onCancel);
      dialog.close();
    };

    const onOk = () => { cleanup(); resolve(true); };
    const onCancel = () => { cleanup(); resolve(false); };
    const onClose = () => { cleanup(); resolve(false); };

    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    dialog.addEventListener('close', onClose);
    dialog.addEventListener('cancel', onCancel);

    dialog.showModal();
  });
}

/** Prompt con input testuale. Risolve con la stringa inserita, o null se annullato. */
export function promptDialog(message, { placeholder = '', initial = '', okText, cancelText } = {}) {
  okText = okText || i18n.t('dialog.confirm');
  cancelText = cancelText || i18n.t('dialog.cancel');
  const dialog = document.getElementById('oc-prompt-dialog');
  if (!dialog) return Promise.resolve(null);
  if (dialog.open) return Promise.resolve(null);

  const msgEl = document.getElementById('oc-prompt-message');
  const inputEl = document.getElementById('oc-prompt-input');
  const okBtn = document.getElementById('oc-prompt-ok');
  const cancelBtn = document.getElementById('oc-prompt-cancel');
  if (!msgEl || !inputEl || !okBtn || !cancelBtn) return Promise.resolve(null);

  msgEl.textContent = message;
  inputEl.placeholder = placeholder;
  inputEl.value = initial;
  okBtn.textContent = okText;
  cancelBtn.textContent = cancelText;

  return new Promise((resolve) => {
    let settled = false;
    const cleanup = (val) => {
      if (settled) return;
      settled = true;
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      inputEl.removeEventListener('keydown', onKey);
      dialog.removeEventListener('close', onClose);
      dialog.removeEventListener('cancel', onCancel);
      dialog.close();
      resolve(val);
    };
    const onOk = () => cleanup(inputEl.value);
    const onCancel = () => cleanup(null);
    const onClose = () => cleanup(null);
    const onKey = (e) => { if (e.key === 'Enter') { e.preventDefault(); onOk(); } };

    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    inputEl.addEventListener('keydown', onKey);
    dialog.addEventListener('close', onClose);
    dialog.addEventListener('cancel', onCancel);

    dialog.showModal();
    setTimeout(() => inputEl.focus(), 30);
  });
}

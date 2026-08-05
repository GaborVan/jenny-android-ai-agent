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

/** Modale di dettaglio: titolo, corpo scorrevole, azioni in fondo.
 *
 * Risolve con l'`id` dell'azione premuta, o `null` per qualunque forma di
 * chiusura. Chiudersi *sempre* alla scelta è deliberato: su telefono una modale
 * che resta aperta dopo un tap su "Ferma" nasconde proprio il pannello dove
 * l'effetto si vede, e il toast è già la conferma.
 *
 * `bodyHtml` è markup: chi chiama ha già escapato il contenuto (è il pattern del
 * resto della UI, che costruisce le righe come stringhe).
 *
 * Vie d'uscita: bottone X, tap sul backdrop, Esc e gesto Indietro di Android.
 * Le ultime due arrivano gratis dall'evento `cancel` di <dialog> + showModal();
 * il backdrop no — un click sull'elemento dialog che non passa da .oc-dialog-inner
 * è per definizione fuori dal riquadro.
 */
export function detailDialog({ title = '', bodyHtml = '', actions = [] } = {}) {
  const dialog = document.getElementById('oc-detail-dialog');
  if (!dialog) return Promise.resolve(null);
  if (dialog.open) return Promise.resolve(null);

  const titleEl = document.getElementById('oc-detail-title');
  const bodyEl = document.getElementById('oc-detail-body');
  const actionsEl = document.getElementById('oc-detail-actions');
  const closeBtn = document.getElementById('oc-detail-close');
  if (!titleEl || !bodyEl || !actionsEl || !closeBtn) return Promise.resolve(null);

  titleEl.textContent = title;
  bodyEl.innerHTML = bodyHtml;
  bodyEl.scrollTop = 0;
  actionsEl.innerHTML = '';
  for (const action of actions) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'oc-btn ' + (action.variant === 'primary' ? 'oc-btn-confirm' : 'oc-btn-cancel');
    btn.dataset.actionId = action.id;
    // textContent, non innerHTML: le etichette vengono da i18n, ma il canale
    // resta un canale — nessun markup entra da qui.
    btn.textContent = action.label || action.id;
    actionsEl.appendChild(btn);
  }

  return new Promise((resolve) => {
    let settled = false;
    const cleanup = (value) => {
      if (settled) return;
      settled = true;
      actionsEl.removeEventListener('click', onAction);
      closeBtn.removeEventListener('click', onCancel);
      dialog.removeEventListener('click', onBackdrop);
      dialog.removeEventListener('close', onClose);
      dialog.removeEventListener('cancel', onCancel);
      dialog.close();
      resolve(value);
    };
    const onAction = (e) => {
      const btn = e.target.closest('[data-action-id]');
      if (btn) cleanup(btn.dataset.actionId);
    };
    const onCancel = () => cleanup(null);
    const onClose = () => cleanup(null);
    const onBackdrop = (e) => { if (e.target === dialog) cleanup(null); };

    actionsEl.addEventListener('click', onAction);
    closeBtn.addEventListener('click', onCancel);
    dialog.addEventListener('click', onBackdrop);
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

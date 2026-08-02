/** Vista su cui atterra il tasto Home di Android.
 *
 * Jenny può essere il launcher del telefono: in quel caso ogni pressione di
 * Home le arriva addosso e significa "torna alla schermata iniziale". Quale sia
 * la schermata iniziale, però, è una scelta di chi usa il telefono — chi non
 * chatta tutto il giorno non vuole ritrovarsi la chat ogni volta.
 *
 * Stato client-side (localStorage) come tema e mascotte: non passa dal backend.
 * Default 'chat' = comportamento storico, così chi non tocca niente non nota
 * differenze. Il valore speciale 'last' vuol dire "non cambiare vista": Home
 * chiude solo overlay e drawer e lascia l'utente dov'era.
 */

const HOME_VIEW_KEY = 'jenny-home-view';

export const HOME_VIEW_CHOICES = ['chat', 'apps', 'workspace', 'last'];

export const HOME_VIEW_DEFAULT = 'chat';

export function homeView() {
  const v = localStorage.getItem(HOME_VIEW_KEY);
  return HOME_VIEW_CHOICES.includes(v) ? v : HOME_VIEW_DEFAULT;
}

export function setHomeView(value) {
  const normalized = HOME_VIEW_CHOICES.includes(value) ? value : HOME_VIEW_DEFAULT;
  localStorage.setItem(HOME_VIEW_KEY, normalized);
  return normalized;
}

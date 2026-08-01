/** Theme registry — single source of truth for the 7 named themes.
 *
 * Each theme is a token block in mobile-style.css selected via
 * `data-theme="<id>"` on <html>. `scheme` drives everything that needs a
 * binary light/dark signal (syntax highlighting, app iframes).
 */

import { AppState } from './state.js';

// `desc`/`reply` are the mini-conversation preview copy shown on each theme
// card in the settings picker — the card *is* the preview, so the copy leans
// evocative, not technical.
export const THEMES = [
  { id: 'chanel',    label: 'Chanel',        scheme: 'dark',
    accent: '#f4f1ea', onAccent: '#141414',
    swatch: ['#141414', '#1e1e1e', '#c8a96a'],
    desc: "Bianco e nero couture, un filo d'oro solo dove conta.",
    reply: 'Il lusso non alza mai la voce.' },
  { id: 'synthwave', label: "Synthwave '84", scheme: 'dark',
    accent: '#f92aad', onAccent: '#ffffff',
    swatch: ['#241b2f', '#f92aad', '#03edf9'],
    desc: 'Neon rosa su notte viola, ogni luce lascia la scia.',
    reply: 'Massimo carattere — per chi non ha paura.' },
  { id: 'kyoto',     label: 'Jenny Kyoto',   scheme: 'dark',
    accent: '#b2543f', onAccent: '#f5efe4',
    swatch: ['#201d1a', '#2b2723', '#b2543f'],
    desc: 'Terra, ruggine e angoli smussati a mano, come ceramica riparata.',
    reply: 'La quiete non chiede attenzione.' },
  { id: 'sticker',   label: 'Jenny Sticker', scheme: 'dark',
    accent: '#a78bfa', onAccent: '#1c1523',
    swatch: ['#17131c', '#a78bfa', '#ffd23f'],
    desc: 'Ogni messaggio è un adesivo ritagliato: bordo bianco, ombra dura, rotazione imperfetta.',
    reply: 'La chat come il retro di un laptop.' },
  { id: 'fumetto',   label: 'Jenny Fumetto', scheme: 'light',
    accent: '#1b1820', onAccent: '#faf6ef',
    swatch: ['#faf6ef', '#1b1820', '#7c5cff'],
    desc: 'China su carta, retini e nuvolette: ogni risposta è una vignetta.',
    reply: 'La tua chat è una tavola da leggere.' },
  { id: 'y2k',       label: 'Jenny Y2K',     scheme: 'light',
    accent: '#f56ab5', onAccent: '#ffffff',
    swatch: ['#f7e0f8', '#f56ab5', '#b76bf0'],
    desc: 'Gradienti lucidi, gloss bubblegum, bordi bianchi.',
    reply: 'Il duemila come ce lo eravamo promesso.' },
  { id: 'pietra',    label: 'Jenny Pietra',  scheme: 'light',
    accent: '#8c6f4e', onAccent: '#f4f1ec',
    swatch: ['#eae6df', '#37332c', '#8c6f4e'],
    desc: 'Travertino, bronzo e serif romani, luce di mezzogiorno.',
    reply: "Solida come un'idea scolpita bene." },
];

export const DEFAULT_THEME = 'chanel';

/** Legacy 'tc-theme' values from the old dark/light/match switcher. */
export const MIGRATION = { dark: 'chanel', light: 'pietra', match: 'chanel' };

export function getTheme(id) {
  return THEMES.find(t => t.id === id) || null;
}

export function currentTheme() {
  return getTheme(document.documentElement.getAttribute('data-theme')) ||
    getTheme(DEFAULT_THEME);
}

/** Allinea le barre di sistema Android al tema: colore = `--bg` calcolato (la
 *  fonte di verità resta il CSS), icone = schema. Senza questo la status bar
 *  resta del colore fisso del tema Android e stona con 6 temi su 7. No-op fuori
 *  dalla WebView, o su un APK più vecchio del metodo. */
function syncNativeBars(scheme) {
  const native = window.JennyNative;
  if (!native || typeof native.setThemeBars !== 'function') return;
  const bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  if (!bg) return;
  try {
    native.setThemeBars(bg, scheme);
  } catch (e) {
    /* bridge non disponibile: le barre restano quelle di themes.xml */
  }
}

/** Toggle the dark/light syntax stylesheets (highlight.js + CodeMirror). */
export function applySyntaxTheme(scheme) {
  for (const [id, s] of [
    ['hljs-theme-dark', 'dark'], ['hljs-theme-light', 'light'],
    ['cm-theme-dark', 'dark'], ['cm-theme-light', 'light'],
  ]) {
    const link = document.getElementById(id);
    if (link) link.media = s === scheme ? 'all' : 'not all';
  }
}

export function setTheme(id) {
  const theme = getTheme(MIGRATION[id] || id) || getTheme(DEFAULT_THEME);
  document.documentElement.setAttribute('data-theme', theme.id);
  localStorage.setItem('tc-theme', theme.id);
  AppState.theme = theme.id;
  applySyntaxTheme(theme.scheme);
  syncNativeBars(theme.scheme);
  window.dispatchEvent(new CustomEvent('themechange', { detail: theme }));
  return theme;
}

// Align the syntax stylesheets and the system bars with the theme the boot
// script applied.
applySyntaxTheme(currentTheme().scheme);
syncNativeBars(currentTheme().scheme);

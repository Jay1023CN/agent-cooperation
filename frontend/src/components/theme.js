import { $ } from './ui.js';

export function setTheme(value) {
  const theme = ['system', 'light', 'dark'].includes(value) ? value : 'system';
  if (theme === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem('pal-arena-theme', theme);
  } catch {
    /* Storage can be disabled. */
  }
  $('#theme-select').value = theme;
}

export function restoreTheme() {
  let saved = 'system';
  try {
    saved = localStorage.getItem('pal-arena-theme') || saved;
  } catch {
    /* Use the system theme. */
  }
  setTheme(saved);
}

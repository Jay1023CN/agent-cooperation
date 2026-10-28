import { $, $$ } from './ui.js';

const mobile = matchMedia('(max-width: 720px)');

export function updateSidebar() {
  const sidebar = $('#sidebar');
  const expanded = sidebar.classList.contains('open');
  sidebar.inert = mobile.matches && !expanded;
  $$('[data-action="menu"]').forEach((button) => {
    button.setAttribute('aria-expanded', String(expanded));
    button.setAttribute('aria-controls', 'sidebar');
  });
}

export function closeSidebar() {
  $('#sidebar').classList.remove('open');
  updateSidebar();
}

export function toggleSidebar() {
  $('#sidebar').classList.toggle('open');
  updateSidebar();
  if ($('#sidebar').classList.contains('open')) $('.main-nav button')?.focus();
}

export function initializeNavigation() {
  mobile.addEventListener('change', updateSidebar);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && $('#sidebar').classList.contains('open')) {
      closeSidebar();
      $('[data-action="menu"]').focus();
    }
  });
  document.addEventListener('click', (event) => {
    if (
      mobile.matches &&
      $('#sidebar').classList.contains('open') &&
      !event.target.closest('#sidebar, [data-action="menu"]')
    )
      closeSidebar();
  });
  updateSidebar();
}

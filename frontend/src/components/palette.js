import { $, e, icon, statusLabels } from './ui.js';

/**
 * Ctrl/⌘ K: one input to jump to a page, project or task, or run an action.
 * `items()` is called on every open so the list reflects the latest snapshot.
 */
export function createPalette(items) {
  const dialog = $('#palette');
  const input = $('#palette-input');
  const list = $('#palette-list');
  let visible = [];
  let selected = 0;

  // Matches every typed character in order; earlier and contiguous matches rank higher.
  function score(text, query) {
    if (!query) return 1;
    const haystack = text.toLowerCase();
    let position = -1,
      total = 0,
      previous = -2;
    for (const character of query.toLowerCase()) {
      position = haystack.indexOf(character, position + 1);
      if (position < 0) return 0;
      total += position === previous + 1 ? 3 : 1;
      previous = position;
    }
    return total + (haystack.startsWith(query.toLowerCase()) ? 5 : 0) - haystack.length / 100;
  }

  function render() {
    const query = input.value.trim();
    visible = items()
      .map((item) => ({
        ...item,
        rank: score(`${item.label} ${item.hint || ''} ${item.keywords || ''}`, query),
      }))
      .filter((item) => item.rank > 0)
      .sort((a, b) => (query ? b.rank - a.rank : 0));
    selected = Math.min(selected, Math.max(0, visible.length - 1));
    if (!visible.length) {
      list.innerHTML = '<div class="palette-empty">没有匹配的页面、项目、任务或操作</div>';
      input.removeAttribute('aria-activedescendant');
      return;
    }
    let group = '';
    list.innerHTML = visible
      .map((item, index) => {
        const heading =
          !query && item.group !== group ? `<div class="palette-group">${e(item.group)}</div>` : '';
        group = item.group;
        return `${heading}<button type="button" class="palette-item" role="option" id="palette-${index}" data-index="${index}" aria-selected="${index === selected}">${icon(item.icon || 'arrow')}<span>${e(item.label)}</span><small>${e(item.hint || '')}</small></button>`;
      })
      .join('');
    input.setAttribute('aria-activedescendant', `palette-${selected}`);
    list.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  }

  function run(index) {
    const item = visible[index];
    if (!item) return;
    close();
    item.run();
  }

  function open() {
    if (dialog.open) return;
    input.value = '';
    selected = 0;
    render();
    dialog.showModal();
    input.focus();
  }

  function close() {
    if (dialog.open) dialog.close();
  }

  input.addEventListener('input', () => {
    selected = 0;
    render();
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      selected = (selected + step + visible.length) % Math.max(1, visible.length);
      render();
    } else if (event.key === 'Enter' && !event.isComposing) {
      event.preventDefault();
      run(selected);
    }
  });
  list.addEventListener('click', (event) => {
    const item = event.target.closest('[data-index]');
    if (item) run(Number(item.dataset.index));
  });
  list.addEventListener('mousemove', (event) => {
    const item = event.target.closest('[data-index]');
    if (item && Number(item.dataset.index) !== selected) {
      selected = Number(item.dataset.index);
      list.querySelectorAll('.palette-item').forEach((node) => {
        node.setAttribute('aria-selected', String(Number(node.dataset.index) === selected));
      });
    }
  });
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) close();
  });
  return { open, close, isOpen: () => dialog.open };
}

export function taskHint(task) {
  return statusLabels[task.status] || task.status;
}

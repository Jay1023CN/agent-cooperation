const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHTML = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character],
  );
const e = escapeHTML;
const icons = {
  settings:
    'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM10 2h4l1 3 3 1 3 3-1 3 1 3-3 3-3 1-1 3h-4l-1-3-3-1-3-3 1-3-1-3 3-3 3-1 1-3Z',
  chat: 'M4 4h16v12H9l-5 4V4Z',
  board: 'M4 4h6v16H4zM14 4h6v7h-6zM14 15h6v5h-6z',
  check: 'm5 12 4 4 10-10M21 12a9 9 0 1 1-6-8.5',
  folder: 'M3 6h7l2 3h9v11H3V6Z',
  terminal: 'm5 7 4 5-4 5M12 17h7',
  users:
    'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 3a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.9M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  logout: 'M9 4H4v16h5M13 8l4 4-4 4M8 12h12',
  menu: 'M4 6h16M4 12h16M4 18h16',
  grid: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z',
  refresh: 'M20 7V3l-4 4M4 17v4l4-4M20 7a8 8 0 0 0-14-2M4 17a8 8 0 0 0 14 2',
  list: 'M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01',
  spark: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z',
  'arrow-up': 'm6 11 6-6 6 6M12 5v15',
  monitor: 'M3 4h18v13H3zM8 21h8M12 17v4',
  cpu: 'M7 7h10v10H7zM9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3',
  clock: 'M12 8v4l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  branch: 'M6 3v12a3 3 0 1 0 3 3M6 9h7a5 5 0 0 0 5-5M20 3a2 2 0 1 1-4 0 2 2 0 0 1 4 0',
  file: 'M14 2H5v20h14V7l-5-5ZM14 2v6h5M8 12h8M8 16h6',
  download: 'M12 3v12m-4-4 4 4 4-4M4 17v4h16v-4',
  pin: 'm8 3 8 0-1 6 3 3H6l3-3-1-6ZM12 12v9',
  trash: 'M3 6h18M8 6V3h8v3M5 6l1 15h12l1-15M9 10v7M15 10v7',
  close: 'm6 6 12 12M6 18 18 6',
  copy: 'M9 9h12v12H9zM15 5V3H3v12h2',
  arrow: 'M5 12h14m-5-5 5 5-5 5',
  upload: 'M12 16V3m-4 4 4-4 4 4M4 17v4h16v-4',
  shield: 'm12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6l8-4Z',
  cube: 'm12 3 9 5v9l-9 5-9-5V8l9-5ZM3 8l9 5 9-5M12 13v9',
  plus: 'M12 5v14M5 12h14',
  alert: 'M12 9v4M12 17h.01M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z',
};
const icon = (name) =>
  `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${icons[name] || icons.spark}"/></svg>`;
function hydrateIcons(root = document) {
  $$('[data-icon]', root).forEach((node) => {
    node.innerHTML = icon(node.dataset.icon);
  });
}
const statusLabels = {
  queued: '待分派',
  running: '进行中',
  done: '已完成',
  in_review: '待验收',
  coordinating: '协调中',
  changes_requested: '待修订',
  blocked: '已阻塞',
  publishing: '发布中',
  delivered: '已交付',
  failed: '失败',
  interrupted: '连接中断',
  cancelled: '已取消',
  ready: '可交付',
  active: '进行中',
};
const roleLabels = { leader: '负责人', worker: '执行成员', reviewer: '独立验收', publisher: '发布成员' };
const capabilityLabels = {
  code: '代码',
  web: '网页',
  research: '研究',
  docs: '文档',
  blender: 'Blender',
  debug: '调试',
  planning: '规划',
};
/** Pipeline lanes. Every task status falls into exactly one lane, cancelled work drops out. */
const lanes = [
  ['queued', '待分派', ['queued']],
  ['running', '执行中', ['running', 'coordinating', 'publishing']],
  ['in_review', '待验收', ['in_review']],
  ['blocked', '需处理', ['failed', 'blocked', 'interrupted', 'changes_requested']],
  ['done', '已完成', ['done', 'delivered']],
];
const laneOf = (value) => lanes.find((lane) => lane[2].includes(value))?.[0] || null;
const status = (value) => `<span class="pill status-${e(value)}">${e(statusLabels[value] || value)}</span>`;
const size = (bytes) =>
  bytes >= 1e9
    ? `${(bytes / 1e9).toFixed(2)} GB`
    : bytes >= 1e6
      ? `${(bytes / 1e6).toFixed(1)} MB`
      : `${Math.ceil(bytes / 1e3)} KB`;
const timeLabel = (timestamp) =>
  timestamp
    ? new Date(timestamp * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    : '—';
const dateLabel = (timestamp) =>
  timestamp
    ? new Date(timestamp * 1000).toLocaleString('zh-CN', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '—';
const asArray = (value) => (Array.isArray(value) ? value : []);
const lines = (value) =>
  String(value)
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
function emptyState(title, description, action = '', label = '') {
  return `<div class="empty-state"><div class="empty-icon" aria-hidden="true"><i></i><i></i><i></i></div><h3>${e(title)}</h3><p>${e(description)}</p>${action ? `<button class="button primary" data-action="${e(action)}">${e(label)}</button>` : ''}</div>`;
}

export {
  $,
  $$,
  e,
  icon,
  hydrateIcons,
  status,
  statusLabels,
  lanes,
  laneOf,
  roleLabels,
  capabilityLabels,
  size,
  timeLabel,
  dateLabel,
  asArray,
  lines,
  emptyState,
};

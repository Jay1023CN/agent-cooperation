import { $, e, dateLabel, emptyState } from '../components/ui.js';

const categories = { preference: '偏好', decision: '已定决策', context: '背景', constraint: '约束' };
const categoryOrder = ['constraint', 'decision', 'preference', 'context'];
const statuses = { active: '已启用', proposed: '待确认', disabled: '已停用', merged: '已合并' };
const tone = (item) =>
  item.status === 'active' ? 'done' : item.status === 'proposed' ? 'in_review' : 'cancelled';

/** Where a memory came from: a task the leader learned it in, a person, or nowhere recorded. */
function memorySource(item, tasks) {
  if (item.source_task) {
    const task = tasks.find((candidate) => candidate.id === item.source_task);
    return `<button class="memory-source" data-action="memory-source" data-id="${e(item.id)}">AI 整理 · 查看来源${task ? ` · ${e(task.title)}` : ''}</button>`;
  }
  if (item.source_label) return `<span class="memory-source">${e(item.source_label)}</span>`;
  return `<span class="memory-source quiet">手动添加</span>`;
}

function memoryCard(item, tasks) {
  const next = item.status === 'active' ? 'disabled' : 'active';
  const toggle = item.status === 'active' ? '停用' : item.status === 'proposed' ? '确认启用' : '重新启用';
  return `<article class="memory-card status-${tone(item)} ${item.status === 'disabled' ? 'disabled' : ''}"><span class="memory-stub"><span>${e(categories[item.category] || item.category)}</span></span><div class="memory-main"><div class="memory-card-top"><span class="pill status-${tone(item)}">${statuses[item.status] || e(item.status)}</span><span class="memory-revision">第 ${e(item.revision)} 版</span></div><p>${e(item.content)}</p><div class="memory-card-footer"><span>${memorySource(item, tasks)}<time>${dateLabel(item.updated || item.created)}</time></span><div>${
    item.status === 'proposed'
      ? `<button class="button small primary" data-action="memory-status" data-id="${e(item.id)}" data-status="active">确认启用</button><button class="button small" data-action="memory-status" data-id="${e(item.id)}" data-status="disabled">不采用</button>`
      : `<button class="text-link" data-action="memory-status" data-id="${e(item.id)}" data-status="${next}">${toggle}</button>`
  }<button class="text-link" data-action="edit-memory" data-id="${e(item.id)}">编辑</button><button class="text-link danger-link" data-action="delete-memory" data-id="${e(item.id)}">删除</button></div></div></div></article>`;
}

export function renderMemories(memories, memoryScope, summary = null, tasks = []) {
  const summaryCard = `<section class="memory-card"><div class="memory-main"><div class="memory-card-top"><strong>对话摘要</strong>${summary?.updated ? `<span class="muted small">${dateLabel(summary.updated)}</span>` : ''}</div><p>${summary?.summary ? e(summary.summary) : '对话较长时，负责人会自动整理当前进展。'}</p>${summary?.revision ? '<div class="memory-card-footer"><span></span><button class="text-link" data-action="edit-conversation-summary">更正摘要</button></div>' : ''}</div></section>`;
  const count = (status) => memories.filter((item) => item.status === status).length;
  const tally = [
    ['done', '已启用', count('active')],
    ['in_review', '待确认', count('proposed')],
    ['cancelled', '已停用', count('disabled') + count('merged')],
  ];
  const proposed = memories.filter((item) => item.status === 'proposed');
  const settled = memories.filter((item) => item.status !== 'proposed');
  const known = categoryOrder.filter((key) => settled.some((item) => item.category === key));
  const other = [...new Set(settled.map((item) => item.category))].filter(
    (key) => !categoryOrder.includes(key),
  );
  const groups = [...known, ...other].map((key) => [key, settled.filter((item) => item.category === key)]);
  $('#view-memory').innerHTML =
    `<div class="view-heading"><div><h2>记忆</h2><p>负责人从对话里整理出长期有用的信息，派任务时一起交给成员。你可以随时修改、停用或删除。</p></div><button class="button primary" data-action="new-memory">添加记忆</button></div><div class="memory-toolbar"><div class="segmented"><button data-action="memory-scope" data-scope="project" class="${memoryScope === 'project' ? 'active' : ''}">项目共享</button><button data-action="memory-scope" data-scope="member" class="${memoryScope === 'member' ? 'active' : ''}">只给我</button></div><span class="muted small">${memoryScope === 'project' ? '团队里每位成员都会用到' : '只用于你发起的对话和任务'}</span>${
      memories.length
        ? `<div class="memory-tally">${tally.map(([key, label, value]) => `<span class="status-${key} ${value ? '' : 'zero'}"><strong>${value}</strong>${label}</span>`).join('')}</div>`
        : ''
    }</div>${summaryCard}${
      !memories.length
        ? emptyState(
            '还没有记忆',
            memoryScope === 'project'
              ? '项目约定和偏好会在对话中逐步整理到这里。'
              : '你的长期偏好和背景会在对话中逐步整理到这里。',
            'new-memory',
            '添加一条记忆',
          )
        : `${
            proposed.length
              ? `<section class="memory-inbox"><div class="memory-group-title"><h3>等你确认</h3><span>确认后才会交给成员使用</span></div><div class="memory-list">${proposed.map((item) => memoryCard(item, tasks)).join('')}</div></section>`
              : ''
          }${groups.map(([key, items]) => `<section class="memory-group"><div class="memory-group-title"><h3>${e(categories[key] || key)}</h3><span>${items.length} 条</span></div><div class="memory-list">${items.map((item) => memoryCard(item, tasks)).join('')}</div></section>`).join('')}`
    }`;
}

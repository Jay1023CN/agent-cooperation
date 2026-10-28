import {
  $,
  e,
  icon,
  status,
  statusLabels,
  capabilityLabels,
  size,
  dateLabel,
  timeLabel,
  asArray,
  emptyState,
} from '../components/ui.js';

function taskCard(task, state) {
  return `<button class="task-card" data-action="task" data-id="${e(task.id)}"><div class="task-id">${e(task.id.slice(0, 7).toUpperCase())} · ${e(capabilityLabels[task.capability] || task.capability)}</div><h3>${e(task.title)}</h3><p>${e(task.prompt)}</p><div class="task-card-bottom"><span>${e(state.agents.find((agent) => agent.id === task.agent_id)?.name || '等待分配')}</span>${status(task.status)}</div></button>`;
}
export function renderTasks(state) {
  if (!state.tasks.length) {
    $('#view-tasks').innerHTML = emptyState(
      '让想法变成清楚的分工',
      '向负责人提出目标，或手动创建任务并写下验收条件。',
      'new-task',
      '创建任务',
    );
    return;
  }
  const groups = [
    ['待开始', state.tasks.filter((task) => task.status === 'queued')],
    [
      '执行与验收',
      state.tasks.filter((task) => ['running', 'in_review', 'coordinating'].includes(task.status)),
    ],
    [
      '已完成 / 待处理',
      state.tasks.filter((task) => !['queued', 'running', 'in_review', 'coordinating'].includes(task.status)),
    ],
  ];
  $('#view-tasks').innerHTML =
    `<div class="view-heading"><div><h2>一个目标，明确分工</h2><p>任务结束后还会独立验收；需求版本变化时重新核对。</p></div></div><div class="board">${groups.map(([name, tasks]) => `<section class="board-column"><div class="column-heading"><span>${name}</span><span>${tasks.length}</span></div>${tasks.map((task) => taskCard(task, state)).join('')}${!tasks.length ? '<p class="empty-small">暂时没有任务</p>' : ''}</section>`).join('')}</div>`;
}
export function renderReviews(state) {
  const tasks = state.tasks.filter(
    (task) => task.kind === 'worker' && ['in_review', 'done', 'changes_requested'].includes(task.status),
  );
  $('#view-review').innerHTML =
    `<div class="view-heading"><div><h2>对照需求，检查真正的成果</h2><p>验收成员与执行成员独立；结论必须附带证据。</p></div></div>` +
    (!tasks.length
      ? emptyState(
          '成果准备好后，在这里验收',
          '每个执行任务完成后会进入独立验收。普通的“完成了”回复不代表通过。',
        )
      : `<div class="review-list">${tasks
          .map(
            (task) =>
              `<article class="review-card"><div class="review-card-top"><h3>${e(task.title)}</h3>${status(task.status)}</div><p>${e(task.review?.summary || '等待验收成员检查代码、文件与测试结果。')}</p><ul class="acceptance-list">${asArray(
                task.acceptance,
              )
                .map((item) => `<li>${e(item)}</li>`)
                .join(
                  '',
                )}</ul><button class="button small" data-action="task" data-id="${e(task.id)}">查看任务与验收证据 ↗</button></article>`,
          )
          .join('')}</div>`);
}
export function renderFiles(state) {
  const files = state.artifacts;
  $('#view-files').innerHTML =
    `<div class="view-heading"><div><h2>交付物，放在一起</h2><p>代码通过 GitHub 协作，文档与模型按需暂存。</p></div><button class="button small" data-action="upload">${icon('upload')} 上传成果</button></div>` +
    (!files.length
      ? emptyState('这里存放可以交付的成果', '上传文档、模型或演示文件。文件默认保留，解除保留后才能清理。')
      : `<div class="file-list">${files.map((file) => `<div class="file-row"><span>${icon(file.name.endsWith('.blend') ? 'cube' : 'file')}</span><div class="file-row-main"><strong>${e(file.name)}</strong><small>${size(file.size)} · ${dateLabel(file.created)}${file.pinned ? ' · 已保留' : ''}</small></div><div class="file-row-actions">${/\.(txt|md|json|csv|py|js|ts|css|html?|svg|log|png|jpe?g|gif|webp|avif|bmp)$/i.test(file.name) ? `<button class="icon-button" data-action="preview-file" data-id="${e(file.id)}" title="预览文件" aria-label="预览文件">${icon('file')}</button>` : ''}<button class="icon-button ${file.pinned ? 'pinned' : ''}" data-action="pin-file" data-id="${e(file.id)}" title="${file.pinned ? '解除保留' : '保留文件'}" aria-label="${file.pinned ? '解除保留' : '保留文件'}">${icon('pin')}</button><button class="icon-button" data-action="download-file" data-id="${e(file.id)}" title="下载文件" aria-label="下载文件">${icon('download')}</button>${!file.pinned ? `<button class="icon-button" data-action="delete-file" data-id="${e(file.id)}" title="删除文件" aria-label="删除文件">${icon('trash')}</button>` : ''}</div></div>`).join('')}</div>`) +
    `<div class="file-storage"><span>暂存占用 ${size(state.storage.used)} / ${size(state.storage.quota)}</span><button class="text-link" data-action="cleanup">清理较旧的未保留成果</button></div>`;
}
export function renderLogs(state) {
  $('#view-logs').innerHTML =
    `<div class="view-heading"><div><h2>跟进每一步执行</h2><p>连接、分派、运行结果与阻塞原因。</p></div><span class="pill">最近 ${state.events.length} 条</span></div>` +
    (!state.events.length
      ? emptyState('记录会从第一次行动开始', '成员领取任务后，运行进展会汇集在这里。')
      : `<div class="log-stream">${state.events.map((event) => `<article class="log-event"><time>${timeLabel(event.created)}</time><div><strong>${e(statusLabels[event.kind] || event.kind)}${event.task_id ? ' · ' + e(event.task_id.slice(0, 7)) : ''}</strong><p>${e(event.text)}</p></div></article>`).join('')}</div>`);
}

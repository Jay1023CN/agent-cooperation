import {
  $,
  e,
  icon,
  status,
  statusLabels,
  laneOf,
  capabilityLabels,
  size,
  dateLabel,
  timeLabel,
  asArray,
  emptyState,
} from '../components/ui.js';

const kindLabels = { worker: '执行', leader: '负责人', reviewer: '验收', publisher: '发布' };

function taskCard(task, state) {
  const waiting = asArray(task.permissions).some((item) => !item.decided) && task.status === 'running';
  const agent = state.agents.find((item) => item.id === task.agent_id);
  return `<button class="task-card status-${e(task.status)}" data-action="task" data-id="${e(task.id)}"><span class="ticket-stub"><code>${e(task.id.slice(0, 7).toUpperCase())}</code></span><span class="ticket-body"><span class="task-id"><span>${e(capabilityLabels[task.capability] || task.capability)}</span>${task.kind !== 'worker' ? `<span>${e(kindLabels[task.kind] || task.kind)}</span>` : ''}</span><h3>${e(task.title)}</h3><p>${e(task.prompt)}</p><span class="task-card-bottom"><span class="ticket-agent${agent ? '' : ' unassigned'}"><i aria-hidden="true">${agent ? e(Array.from(agent.name || '?')[0]) : ''}</i>${e(agent?.name || '等待分配')}</span>${waiting ? '<span class="task-flag">等你授权</span>' : status(task.status)}</span></span></button>`;
}

/** A running review belongs with the work it is reviewing, not with execution. */
const boardLane = (task) =>
  task.kind === 'reviewer' && laneOf(task.status) === 'running' ? 'in_review' : laneOf(task.status);

export function renderTasks(state) {
  const tasks = state.tasks.filter((task) => task.status !== 'cancelled');
  if (!state.tasks.length) {
    $('#view-tasks').innerHTML = emptyState(
      '还没有任务',
      '在对话里告诉负责人目标，它会拆成任务；也可以自己建一个。',
      'new-task',
      '新建任务',
    );
    return;
  }
  const cancelled = state.tasks.length - tasks.length;
  const columns = [
    ['queued', '待分派', '等空闲成员领取', ['queued']],
    ['running', '执行中', '成员正在做', ['running']],
    ['in_review', '验收中', '另一位成员在核对', ['in_review']],
    ['done', '结束', '通过验收，或停下等你处理', ['done', 'blocked']],
  ];
  $('#view-tasks').innerHTML =
    `<div class="view-heading"><div><h2>任务</h2><p>执行完不算完，另一位成员验收通过才进入已完成。${cancelled ? ` 已取消 ${cancelled} 个，未显示。` : ''}</p></div></div><div class="board">${columns
      .map(([key, name, note, laneKeys]) => {
        const items = tasks
          .filter((task) => laneKeys.includes(boardLane(task)))
          .sort((a, b) => Number(laneOf(a.status) === 'done') - Number(laneOf(b.status) === 'done'));
        return `<section class="board-column status-${key}" aria-label="${name} ${items.length} 个"><header class="lane-head${items.length ? '' : ' zero'}"><strong class="lane-count">${items.length}</strong><span class="lane-name">${name}</span><span class="lane-note">${note}</span></header>${items.map((task) => taskCard(task, state)).join('')}${!items.length ? '<p class="lane-empty">没有任务</p>' : ''}</section>`;
      })
      .join('')}</div>`;
}

export function renderReviews(state) {
  const tasks = state.tasks
    .filter(
      (task) => task.kind === 'worker' && ['in_review', 'done', 'changes_requested'].includes(task.status),
    )
    .sort((a, b) => Number(a.status !== 'in_review') - Number(b.status !== 'in_review'));
  $('#view-review').innerHTML =
    `<div class="view-heading"><div><h2>验收</h2><p>验收成员与执行成员不是同一个；没有证据的“做完了”不算通过。</p></div></div>` +
    (!tasks.length
      ? emptyState('暂时没有要验收的成果', '执行任务完成后会自动排进验收，由另一位成员对照需求检查。')
      : `<div class="review-list">${tasks
          .map(
            (task) =>
              `<article class="review-card status-${e(task.status)}"><div class="review-card-top"><h3>${e(task.title)}</h3>${status(task.status)}</div><p>${e(task.review?.summary || '等待验收成员检查代码、文件和测试结果。')}</p><ul class="acceptance-list">${asArray(
                task.acceptance,
              )
                .map((item) => `<li>${e(item)}</li>`)
                .join(
                  '',
                )}</ul><button class="button small" data-action="task" data-id="${e(task.id)}">查看证据</button></article>`,
          )
          .join('')}</div>`);
}

export function renderFiles(state) {
  const files = state.artifacts;
  $('#view-files').innerHTML =
    `<div class="view-heading"><div><h2>成果</h2><p>代码走 GitHub 分支；文档、模型和演示文件暂存在中心。</p></div><button class="button" data-action="upload">${icon('upload')}上传文件</button></div>` +
    (!files.length
      ? emptyState('还没有成果文件', '成员把文件放进任务的 arena-exports/ 后会自动出现，也可以手动上传。')
      : `<div class="file-list">${files.map((file) => `<div class="file-row"><span>${icon(file.name.endsWith('.blend') ? 'cube' : 'file')}</span><div class="file-row-main"><strong>${e(file.name)}</strong><small>${size(file.size)} · ${dateLabel(file.created)}${file.pinned ? ' · 已保留' : ''}</small></div><div class="file-row-actions">${/\.(txt|md|json|csv|py|js|ts|css|html?|svg|log|png|jpe?g|gif|webp|avif|bmp)$/i.test(file.name) ? `<button class="icon-button" data-action="preview-file" data-id="${e(file.id)}" title="预览" aria-label="预览 ${e(file.name)}">${icon('file')}</button>` : ''}<button class="icon-button ${file.pinned ? 'pinned' : ''}" data-action="pin-file" data-id="${e(file.id)}" title="${file.pinned ? '取消保留' : '保留'}" aria-label="${file.pinned ? '取消保留' : '保留'} ${e(file.name)}">${icon('pin')}</button><button class="icon-button" data-action="download-file" data-id="${e(file.id)}" title="下载" aria-label="下载 ${e(file.name)}">${icon('download')}</button>${!file.pinned ? `<button class="icon-button" data-action="delete-file" data-id="${e(file.id)}" title="删除" aria-label="删除 ${e(file.name)}">${icon('trash')}</button>` : ''}</div></div>`).join('')}</div>`) +
    `<div class="file-storage"><span title="额度限制成果上传；对话和任务记录不受此上限限制">中心已用 ${size(state.storage.used)} · 成果上传额度 ${size(state.storage.quota)}</span><button class="text-link" data-action="cleanup">清理未保留的旧文件</button></div>`;
}

export function renderLogs(state) {
  $('#view-logs').innerHTML =
    `<div class="view-heading"><div><h2>运行记录</h2><p>连接、分派、运行结果和阻塞原因，按时间倒序。</p></div><span class="pill">最近 ${state.events.length} 条</span></div>` +
    (!state.events.length
      ? emptyState('还没有记录', '成员领取第一个任务后，运行过程会记在这里。')
      : `<div class="log-stream">${state.events.map((event) => `<article class="log-event status-${laneOf(event.kind) ? e(event.kind) : 'none'}"><time>${timeLabel(event.created)}</time><i></i><div><strong>${e(statusLabels[event.kind] || event.kind)}${event.task_id ? ` <code class="muted">${e(event.task_id.slice(0, 7))}</code>` : ''}</strong><p>${e(event.text)}</p></div></article>`).join('')}</div>`);
}

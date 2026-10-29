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
          .join('')}</div>`) +
    `<div class="file-storage"><span>中心 ${size(storage.used)} / ${size(storage.quota)} · ${e(centerReason)}${storage.disk_free_bytes != null ? ` · 磁盘剩余 ${size(storage.disk_free_bytes)}` : ''}</span></div>${machines ? `<div class="file-list">${machines}</div>` : ''}`;
}

const previewable = /\.(txt|md|json|csv|py|js|ts|css|html?|svg|log|png|jpe?g|gif|webp|avif|bmp)$/i;
const extensionOf = (name) => (/\.([a-z0-9]{1,6})$/i.exec(name)?.[1] || '文件').toUpperCase();

/** Storage as one gauge: what is kept, what cleanup can free, and what else the center holds. */
function storageGauge(state) {
  const used = state.storage?.used || 0,
    quota = state.storage?.quota || 5e9;
  const sum = (files) => files.reduce((total, file) => total + (file.size || 0), 0);
  const kept = sum(state.artifacts.filter((file) => file.pinned)),
    loose = sum(state.artifacts.filter((file) => !file.pinned)),
    other = Math.max(0, used - kept - loose);
  const percent = Math.min(100, (used / quota) * 100);
  const level = state.storage?.state === 'blocked' || percent >= 95 ? 'full' : percent >= 85 ? 'near' : '';
  const width = (value) => `${Math.min(100, (value / quota) * 100).toFixed(2)}%`;
  const note = {
    full: '中心暂存已满，新成果会传不上来。先清理未保留的旧文件，或把大文件挪走。',
    near: '空间紧张，可以检查旧文件和执行电脑工作区。',
    '': loose
      ? `未保留文件共 ${size(loose)}；清理时会保留最近成果和活动任务。`
      : '当前项目没有未保留的成果。',
  }[level];
  return `<section class="storage-gauge ${level}"><div class="storage-figure"><strong>${size(used)}</strong><span>/ ${size(quota)}</span><em>${percent.toFixed(0)}%</em></div><div class="storage-bar" role="img" aria-label="中心暂存已用 ${percent.toFixed(0)}%"><span class="kept" style="width:${width(kept)}"></span><span class="loose" style="width:${width(loose)}"></span><span class="other" style="width:${width(other)}"></span></div><div class="storage-legend"><span class="kept">已保留 ${size(kept)}</span><span class="loose">未保留 ${size(loose)}</span>${other ? `<span class="other">其他项目和记录 ${size(other)}</span>` : ''}</div><div class="storage-foot"><p>${note}</p><button class="button small ${level ? 'primary' : ''}" data-action="cleanup" ${loose ? '' : 'disabled'}>清理未保留的旧文件</button></div></section>`;
}

export function renderFiles(state) {
  const files = state.artifacts;
  const storage = state.storage || {};
  const storageLabels = { ok: '正常', near_limit: '空间紧张', blocked: '已触及上限', unknown: '状态未知' };
  const centerReason =
    storage.state === 'blocked' && storage.disk_free_bytes < 1024 ** 3
      ? '中心电脑剩余空间不足'
      : storage.state === 'blocked'
        ? '中心存储预算已满'
        : storageLabels[storage.state] || '状态未知';
  const machines = (state.machines || [])
    .map((machine) => {
      const machineState = machine.storage_state || 'unknown';
      const detail =
        machineState === 'unknown'
          ? machine.online
            ? '容量数据尚不可用'
            : '电脑离线，容量状态未知'
          : `工作区 ${size(machine.storage_bytes)} / ${size(machine.storage_limit_bytes)} · 磁盘剩余 ${size(machine.disk_free_bytes)}`;
      const label =
        machineState === 'blocked'
          ? machine.disk_free_bytes < 1024 ** 3
            ? '磁盘空间不足'
            : '工作区预算已满'
          : storageLabels[machineState] || '状态未知';
      return `<div class="file-row"><span>${icon('monitor')}</span><div class="file-row-main"><strong>${e(machine.name)}</strong><small>${e(detail)}</small></div><div class="file-row-actions"><span class="pill">${e(label)}</span>${machine.online ? `<button class="text-link" data-action="preview-workspaces" data-id="${e(machine.id)}">查看可清理空间</button>` : ''}</div></div>`;
    })
    .join('');
  $('#view-files').innerHTML =
    `<div class="view-heading"><div><h2>成果</h2><p>代码走 GitHub 分支；文档、模型和演示文件暂存在中心。保留的文件不会被清理。</p></div><button class="button primary" data-action="upload">${icon('upload')}上传文件</button></div>${storageGauge(state)}` +
    (!files.length
      ? emptyState('还没有成果文件', '成员把文件放进任务的 arena-exports/ 后会自动出现，也可以手动上传。')
      : `<div class="file-list">${files
          .map((file) => {
            const task = state.tasks.find((item) => item.id === file.task_id);
            return `<div class="file-row ${file.pinned ? 'pinned' : ''}"><span class="file-stub">${e(extensionOf(file.name))}</span><div class="file-row-main"><strong>${e(file.name)}</strong><small>${size(file.size)} · ${dateLabel(file.created)}${task ? ` · ${e(task.title)}` : ''}</small></div>${file.pinned ? '<span class="file-kept">已保留</span>' : '<span class="file-loose">未保留</span>'}<div class="file-row-actions">${previewable.test(file.name) ? `<button class="icon-button" data-action="preview-file" data-id="${e(file.id)}" title="预览" aria-label="预览 ${e(file.name)}">${icon('file')}</button>` : ''}<button class="icon-button ${file.pinned ? 'pinned' : ''}" data-action="pin-file" data-id="${e(file.id)}" title="${file.pinned ? '取消保留' : '保留'}" aria-label="${file.pinned ? '取消保留' : '保留'} ${e(file.name)}">${icon('pin')}</button><button class="icon-button" data-action="download-file" data-id="${e(file.id)}" title="下载" aria-label="下载 ${e(file.name)}">${icon('download')}</button>${!file.pinned ? `<button class="icon-button" data-action="delete-file" data-id="${e(file.id)}" title="删除" aria-label="删除 ${e(file.name)}">${icon('trash')}</button>` : ''}</div></div>`;
          })
          .join('')}</div>`) +
    `<div class="file-storage"><span>中心 ${size(storage.used)} / ${size(storage.quota)} · ${e(centerReason)}${storage.disk_free_bytes != null ? ` · 磁盘剩余 ${size(storage.disk_free_bytes)}` : ''}</span></div>${machines ? `<div class="file-list">${machines}</div>` : ''}`;
}

export function renderLogs(state) {
  $('#view-logs').innerHTML =
    `<div class="view-heading"><div><h2>运行记录</h2><p>连接、分派、运行结果和阻塞原因，按时间倒序。</p></div><span class="pill">最近 ${state.events.length} 条</span></div>` +
    (!state.events.length
      ? emptyState('还没有记录', '成员领取第一个任务后，运行过程会记在这里。')
      : `<div class="log-stream">${state.events.map((event) => `<article class="log-event status-${laneOf(event.kind) ? e(event.kind) : 'none'}"><time>${timeLabel(event.created)}</time><i></i><div><strong>${e(statusLabels[event.kind] || event.kind)}${event.task_id ? ` <code class="muted">${e(event.task_id.slice(0, 7))}</code>` : ''}</strong><p>${e(event.text)}</p></div></article>`).join('')}</div>`);
}

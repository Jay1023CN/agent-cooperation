import { e, dateLabel, statusLabels } from '../components/ui.js';

export function taskTimelineEvents(state, task) {
  const ids = new Set([
    task.id,
    ...state.tasks
      .filter((item) => item.source_id === task.id && item.kind === 'reviewer')
      .map((item) => item.id),
  ]);
  return state.events.filter((event) => ids.has(event.task_id));
}

export function taskTimelineCursor(state, task) {
  return Math.max(0, ...taskTimelineEvents(state, task).map((event) => Number(event.id) || 0));
}

export function renderTaskTimeline(events) {
  if (!events.length) return '<p class="muted small">暂无运行记录。</p>';
  const labels = {
    log: '运行输出',
    review: '验收结论',
    permission: '等待授权',
    permission_reply: '授权已回复',
    artifact: '上传成果',
    requirements: '需求已更新',
    memory_refresh: '更新运行记忆',
    memory_proposed: '提出记忆建议',
    ready: '准备交付',
    publishing: '排队发布',
  };
  return `<div class="log-stream">${events.map((event) => `<article class="log-event"><time>${e(dateLabel(event.created))}</time><i></i><div><strong>${event.task_kind === 'reviewer' ? '独立验收 · ' : ''}${e(labels[event.kind] || statusLabels[event.kind] || '运行记录')}</strong><p>${e(event.text)}</p></div></article>`).join('')}</div>`;
}

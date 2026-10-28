import {
  $,
  e,
  icon,
  statusLabels,
  lanes,
  laneOf,
  roleLabels,
  capabilityLabels,
  asArray,
} from '../components/ui.js';
import { providerInfo } from './connection.js';

const sharedWorkers = (state) =>
  state.tasks.filter((task) => task.kind === 'worker' && (!task.channel || task.channel === 'shared'));

function slots(machine, state) {
  const busy = state.agents
    .filter((agent) => agent.machine_id === machine.id)
    .reduce((total, agent) => total + (agent.busy || 0), 0);
  const total = Math.max(1, Math.min(8, machine.slots || 1));
  return {
    busy,
    total,
    html: `<span class="slot-row" aria-label="${busy} / ${total} 个执行位在用">${Array.from({ length: total }, (_, index) => `<span class="${index < busy ? 'occupied' : ''}"></span>`).join('')}</span>`,
  };
}

export function renderOverview(state, project) {
  const online = state.machines.filter((machine) => machine.online).length;
  $('#project-fleet').innerHTML =
    `<div class="section-title"><h2>电脑</h2><span class="muted small">${online} / ${state.machines.length} 在线</span></div>${
      state.machines.length
        ? `<div class="mini-fleet">${state.machines
            .map(
              (machine) =>
                `<div class="mini-machine"><span class="online-dot ${machine.online ? '' : 'offline'}" title="${machine.online ? '在线' : '离线'}"></span><strong>${e(machine.name)}</strong>${slots(machine, state).html}</div>`,
            )
            .join('')}</div>`
        : '<p class="empty-small">还没有电脑接入。</p><button class="text-link" data-action="invite-machine">连接第一台电脑</button>'
    }`;
  const tasks = sharedWorkers(state),
    done = tasks.filter((task) => laneOf(task.status) === 'done').length,
    open = tasks
      .filter((task) => task.status !== 'cancelled')
      .sort((a, b) => Number(laneOf(a.status) === 'done') - Number(laneOf(b.status) === 'done'));
  $('#project-progress').innerHTML =
    `<div class="section-title"><h2>任务</h2><span class="muted small">完成 ${done} / ${tasks.length}</span></div><div class="overview-tasks">${
      open
        .slice(0, 5)
        .map(
          (task) =>
            `<button data-action="task" data-id="${e(task.id)}" class="status-${e(task.status)}"><span class="overview-dot"></span><span>${e(task.title)}</span><span class="overview-state">${e(statusLabels[task.status] || task.status)}</span></button>`,
        )
        .join('') || '<p class="empty-small">负责人拆出任务后会列在这里。</p>'
    }</div>${tasks.length > 5 ? '<button class="text-link" data-action="navigate" data-view="tasks">全部任务</button>' : ''}`;
  const requirements = asArray(project?.requirements);
  $('#project-brief').innerHTML =
    `<div class="section-title"><h2>需求清单</h2><span class="muted small">v${project?.revision || 1}</span></div>${
      requirements.length
        ? `<ul class="overview-requirements">${requirements
            .slice(0, 5)
            .map((item) => `<li>${e(item)}</li>`)
            .join('')}</ul>`
        : '<p class="empty-small">写下可以核对的完成条件，执行和验收都以它为准。</p>'
    }<button class="text-link" data-action="requirements">${requirements.length ? '查看或修改' : '填写需求清单'}</button>`;
  const files = [...state.artifacts].sort((a, b) => (b.created || 0) - (a.created || 0)).slice(0, 3);
  $('#project-deliverables').innerHTML = `<div class="section-title"><h2>最近成果</h2></div>${
    files.length
      ? `<div class="overview-files">${files.map((file) => `<button data-action="navigate" data-view="files">${icon(file.name.endsWith('.blend') ? 'cube' : 'file')}<span>${e(file.name)}</span></button>`).join('')}</div>`
      : '<p class="empty-small">还没有成果文件。</p>'
  }`;
  $('#project-deliverables').hidden = !files.length;
}

export function renderStats(state) {
  const tasks = sharedWorkers(state).filter((task) => task.status !== 'cancelled');
  const counts = Object.fromEntries(lanes.map(([key]) => [key, 0]));
  tasks.forEach((task) => {
    const lane = laneOf(task.status);
    if (lane) counts[lane] += 1;
  });
  const bar = tasks.length
    ? lanes
        .filter(([key]) => counts[key])
        .map(
          ([key, label]) =>
            `<span class="status-${key}" style="flex-grow:${counts[key]}" title="${label} ${counts[key]}"></span>`,
        )
        .join('')
    : '<span></span>';
  $('#stats').innerHTML =
    `<div class="pipeline-bar ${tasks.length ? '' : 'empty'}" aria-hidden="true">${bar}</div><div class="pipeline-legend">${lanes
      .map(
        ([key, label]) =>
          `<button class="status-${key} ${counts[key] ? '' : 'zero'}" data-action="navigate" data-view="${key === 'in_review' ? 'review' : 'tasks'}"><strong>${counts[key]}</strong><span>${label}</span></button>`,
      )
      .join('')}</div>`;
}

/** Work that stays stuck until a person acts: tool approvals and failed runs. */
export function renderAttention(state) {
  const rows = [];
  state.tasks.forEach((task) => {
    const waiting = asArray(task.permissions).filter((item) => !item.decided);
    if (waiting.length && task.status === 'running')
      rows.push(
        `<button class="attention-row status-in_review" data-action="task" data-id="${e(task.id)}"><strong>等你授权</strong><span>${e(task.title)}</span><span class="muted">${e(waiting[0].tool?.title || waiting[0].tool?.name || '工具操作')}</span><span class="text-link">去处理</span></button>`,
      );
    else if (laneOf(task.status) === 'blocked')
      rows.push(
        `<button class="attention-row status-${e(task.status)}" data-action="task" data-id="${e(task.id)}"><strong>${e(statusLabels[task.status])}</strong><span>${e(task.title)}</span><span class="muted">${e(task.result || task.review?.summary || '')}</span><span class="text-link">查看</span></button>`,
      );
  });
  $('#attention').innerHTML = rows.slice(0, 4).join('');
  $('#attention').hidden = !rows.length;
  return rows.length;
}

export function renderFleet(state) {
  const online = state.machines.filter((machine) => machine.online).length;
  $('#fleet-summary').innerHTML =
    `<span class="online-dot ${online ? '' : 'offline'}"></span>${online} / ${state.machines.length} 台在线`;
  $('#machine-list').innerHTML = state.machines.length
    ? state.machines
        .map((machine) => {
          const agents = state.agents.filter((agent) => agent.machine_id === machine.id);
          const slot = slots(machine, state);
          return `<article class="machine-card ${machine.online ? '' : 'offline'}"><div class="machine-row"><span class="machine-icon">${icon('monitor')}</span><div class="machine-info"><strong>${e(machine.name)}</strong><small>${e(machine.os || '未知系统')} · ${machine.online ? '在线' : '离线'}</small></div><span class="online-dot ${machine.online ? '' : 'offline'}"></span></div><div class="machine-slots">${slot.html}<small>${slot.busy} / ${slot.total} 执行位</small></div>${agents.length ? `<div class="machine-agents">${agents.map((agent) => `<button class="pill" data-action="agent" data-id="${e(agent.id)}">${e(agent.name)}</button>`).join('')}</div>` : '<small class="muted">这台电脑还没有配置成员</small>'}${asArray(machine.github_members).length ? `<small class="muted">可代为提交：${e(machine.github_members.map((item) => item.github_login).join('、'))}</small>` : ''}</article>`;
        })
        .join('')
    : '<div class="empty-small">还没有电脑接入。点「连接电脑」，给每台电脑生成令牌和配置，接入几台都可以。</div>';
}

function readinessLabel(agent, state) {
  const online = state.machines.find((machine) => machine.id === agent.machine_id)?.online;
  const readiness = agent.readiness || {};
  if (!online) return '电脑离线';
  if (agent.busy) return '执行中';
  if (readiness.dispatchable === false) return readiness.installed === false ? '未找到客户端' : '暂不可执行';
  if (!agent.enabled) return '未启用';
  if (readiness.dispatchable === true) return '可领取';
  return '待探测';
}

export function renderAgents(state) {
  const agents = state.agents;
  $('#agent-count').textContent = `${agents.length} 个`;
  $('#agent-list').innerHTML = agents.length
    ? agents
        .map((agent) => {
          const machine = state.machines.find((item) => item.id === agent.machine_id);
          return `<button class="agent-row agent-detail-button" data-action="agent" data-id="${e(agent.id)}"><span class="agent-icon">${e(agent.name.slice(0, 1).toUpperCase())}</span><span class="agent-info"><strong>${e(agent.name)}</strong><small>${e(
            asArray(agent.roles)
              .map((role) => roleLabels[role] || role)
              .join(' / '),
          )} · ${e(machine?.name || '未知电脑')} · ${e(agent.model || agent.provider)}</small></span><span class="agent-status ${agent.busy ? 'busy' : ''}">${e(readinessLabel(agent, state))}</span></button>`;
        })
        .join('')
    : '<p class="empty-small">电脑接入后，它配置的 AI 成员会出现在这里。</p>';
}

export function openAgent(id, state, openModal) {
  const agent = state.agents.find((item) => item.id === id);
  if (!agent) return;
  const machine = state.machines.find((item) => item.id === agent.machine_id);
  const r = agent.readiness || {};
  const providerNote = providerInfo[agent.provider]?.note || '由执行电脑上的本地配置管理。';
  openModal(
    agent.name,
    '连接状态与能力',
    `<div class="detail-meta"><div>执行电脑<strong>${e(machine?.name || '未知')} · ${machine?.online ? '在线' : '离线'}</strong></div><div>配置<strong>${agent.enabled ? '已启用' : '未启用'} · ${e(agent.provider)}</strong></div><div>客户端<strong>${r.installed === true ? '已检测到' : r.installed === false ? '未找到' : '尚未检测'}</strong></div><div>本地连接<strong>${r.transport_ready === true ? '连接正常' : r.transport_ready === false ? '连接不可用' : '尚未检查'}</strong></div><div>模型<strong>${e(agent.model || '跟随客户端设置')}</strong></div><div>订阅与额度<strong>未验证，以实际运行为准</strong></div><div>会话<strong>${agent.mode === 'existing' ? '接续指定的桌面会话' : '每个任务新建会话'}</strong></div><div>能力<strong>${e(
      asArray(agent.capabilities)
        .map((value) => capabilityLabels[value] || value)
        .join('、') || '—',
    )}</strong></div></div>${agent.routing_profile && Object.keys(agent.routing_profile).length ? `<div class="detail-section"><h3>调度偏好</h3><pre>${e(JSON.stringify(agent.routing_profile, null, 2))}</pre></div>` : ''}<div class="notice">${e(r.reason || providerNote)}</div><div class="detail-section"><h3>在这台电脑上自检</h3><pre>pal-arena doctor --config worker.json</pre></div><div class="modal-actions"><button class="button" type="button" data-action="setup-help">接入指南</button><button class="button primary" type="button" data-action="close-modal">关闭</button></div>`,
  );
}

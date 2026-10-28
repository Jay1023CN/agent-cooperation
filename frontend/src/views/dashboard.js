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

const kindLabels = { worker: '执行', leader: '负责人', reviewer: '验收', publisher: '发布' };

/** Each computer is a lane; each running task occupies one of its execution slots. */
function machineLane(machine, state) {
  const agents = new Map(
    state.agents.filter((agent) => agent.machine_id === machine.id).map((a) => [a.id, a]),
  );
  const running = state.tasks.filter((task) => task.status === 'running' && agents.has(task.agent_id));
  const total = Math.max(1, Math.min(8, machine.slots || 1));
  const { busy, html: pips } = slots(machine, state);
  const strips = running
    .map((task) => {
      const agent = agents.get(task.agent_id);
      const role = task.kind === 'worker' ? '' : ` · ${kindLabels[task.kind] || task.kind}`;
      return `<button class="rail-slot status-${e(task.kind === 'reviewer' ? 'in_review' : task.status)}" data-action="task" data-id="${e(task.id)}"><span>${e(task.title)}</span><small>${e(agent.name)}${e(role)}</small></button>`;
    })
    .join('');
  // Tasks from other projects or private channels still occupy slots; they are counted, not shown.
  const hidden = Math.max(0, busy - running.length);
  const rest = !machine.online
    ? '<div class="rail-free">离线</div>'
    : hidden
      ? `<div class="rail-free">另有 ${hidden} 个其他项目或私聊任务在运行</div>`
      : !busy
        ? '<div class="rail-free">空闲</div>'
        : '';
  return `<div class="rail-machine ${machine.online ? '' : 'offline'}"><div class="rail-machine-head"><span class="online-dot ${machine.online ? '' : 'offline'}"></span><strong>${e(machine.name)}</strong>${pips}<small>${busy}/${total}</small></div><div class="rail-slots">${strips}${rest}</div></div>`;
}

export function renderOverview(state, project) {
  const online = state.machines.filter((machine) => machine.online).length;
  const machines = [...state.machines].sort((a, b) => Number(b.online) - Number(a.online));
  $('#project-fleet').innerHTML =
    `<div class="section-title"><h2>机位</h2><span class="muted small">${online} / ${state.machines.length} 台在线</span></div>${
      machines.length
        ? `<div class="rail-lanes">${machines.map((machine) => machineLane(machine, state)).join('')}</div>`
        : '<p class="empty-small">还没有电脑接入。</p><button class="text-link" data-action="invite-machine">连接第一台电脑</button>'
    }`;
  const queued = state.tasks.filter((task) => task.status === 'queued');
  $('#project-progress').innerHTML =
    `<div class="section-title"><h2>排队</h2><span class="muted small">${queued.length} 个</span></div>${
      queued.length
        ? `<div class="rail-queue">${queued
            .slice(0, 6)
            .map(
              (task) =>
                `<button class="status-queued" data-action="task" data-id="${e(task.id)}"><span>${e(task.title)}</span>${task.routing_reason ? `<small>${e(task.routing_reason)}</small>` : '<small>等待空闲成员</small>'}</button>`,
            )
            .join(
              '',
            )}</div>${queued.length > 6 ? '<button class="text-link" data-action="navigate" data-view="tasks">全部任务</button>' : ''}`
        : '<p class="empty-small">没有排队的任务。</p>'
    }`;
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
      : ''
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

/** Work that stays stuck until a person acts. Approvals and retries can be answered right here. */
export function renderAttention(state) {
  const rows = [];
  state.tasks.forEach((task) => {
    const waiting = asArray(task.permissions).filter((item) => !item.decided);
    if (waiting.length && task.status === 'running') {
      const permission = waiting[0];
      const options = asArray(permission.options)
        .slice(0, 3)
        .map(
          (option, index) =>
            `<button class="button small ${index === 0 ? 'primary' : ''}" data-action="permission" data-task="${e(task.id)}" data-permission="${e(permission.id)}" data-option="${e(option.optionId || option.id)}">${e(option.name || option.label || option.optionId)}</button>`,
        )
        .join('');
      rows.push(
        `<div class="attention-row status-in_review"><strong>等你授权</strong><button class="attention-main" data-action="task" data-id="${e(task.id)}"><span>${e(task.title)}</span><span class="muted">${e(permission.tool?.title || permission.tool?.name || '工具操作')}</span></button><div class="attention-actions">${options}</div></div>`,
      );
    } else if (laneOf(task.status) === 'blocked')
      rows.push(
        `<div class="attention-row status-${e(task.status)}"><strong>${e(statusLabels[task.status])}</strong><button class="attention-main" data-action="task" data-id="${e(task.id)}"><span>${e(task.title)}</span><span class="muted">${e(task.result || task.review?.summary || '')}</span></button><div class="attention-actions"><button class="button small" data-action="task" data-id="${e(task.id)}">查看</button><button class="button small" data-action="retry-task" data-id="${e(task.id)}">检查后重试</button></div></div>`,
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

/** How other computers reach the center. Shared over Tailscale, or the steps to get there. */
export function renderNetwork(address, { demo = false, local = false } = {}) {
  const node = $('#network-panel');
  if (demo) {
    node.innerHTML =
      '<div class="network-row status-done"><strong>其他电脑用这个地址</strong><code>https://mac-mini.tail1234.ts.net</code><span class="muted small">示例</span><button class="text-link" data-action="network-help">怎么组网</button></div>';
    return;
  }
  if (address?.state === 'shared') {
    node.innerHTML = `<div class="network-row status-done"><strong>其他电脑和伙伴用这个地址</strong><code>${e(address.url)}</code><button class="button small" data-action="copy-address" data-url="${e(address.url)}">复制</button><button class="text-link" data-action="network-help">怎么让伙伴也连上</button></div>`;
    return;
  }
  const problem = address && address.state !== 'not_shared';
  node.innerHTML = `<div class="network-row status-${problem ? 'blocked' : 'queued'}"><strong>${problem ? '共享没有完成' : '其他电脑还连不上这里'}</strong><span class="muted">${e(problem ? address.message : '中心只在本机可访问。用 Tailscale 组一个私有网络，不需要公网 IP，也不用设置路由器。')}</span>${address?.action_url ? `<a class="button small" href="${e(address.action_url)}" target="_blank" rel="noopener noreferrer">${address.state === 'not_installed' ? '下载 Tailscale' : '打开链接'}</a>` : ''}${local ? '<button class="button small primary" data-action="share-network">在这台电脑上开启共享</button>' : ''}<button class="text-link" data-action="network-help">一步一步怎么做</button></div>`;
}

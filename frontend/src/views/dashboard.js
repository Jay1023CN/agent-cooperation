import { $, e, icon, status, roleLabels, capabilityLabels, asArray } from '../components/ui.js';
import { providerInfo } from './connection.js';

export function renderOverview(state, project) {
  const tasks = state.tasks.filter(
      (task) => task.kind === 'worker' && (!task.channel || task.channel === 'shared'),
    ),
    done = tasks.filter((task) => task.status === 'done').length;
  $('#project-progress').innerHTML =
    `<div class="section-title"><h2>交付进度</h2><span class="muted small">${done} / ${tasks.length}</span></div><div class="progress-track"><span style="width:${tasks.length ? (done / tasks.length) * 100 : 0}%"></span></div><div class="overview-tasks">${
      tasks
        .slice(0, 4)
        .map(
          (task) =>
            `<button data-action="task" data-id="${e(task.id)}"><span class="overview-dot ${task.status === 'done' ? 'complete' : ''}"></span><span>${e(task.title)}</span>${status(task.status)}</button>`,
        )
        .join('') || '<p class="empty-small">任务建立后，在这里跟进成果。</p>'
    }</div><button class="text-link" data-action="navigate" data-view="tasks">查看全部任务 →</button>`;
  const requirements = asArray(project?.requirements);
  $('#project-brief').innerHTML =
    `<div class="section-title"><h2>当前需求</h2><span class="muted small">v${project?.revision || 1}</span></div>${
      requirements.length
        ? `<ul class="overview-requirements">${requirements
            .slice(0, 4)
            .map((item) => `<li>${e(item)}</li>`)
            .join('')}</ul>`
        : '<p class="empty-small">先写清楚作品要达到的效果，再安排执行与验收。</p>'
    }<button class="text-link" data-action="requirements">${requirements.length ? '查看需求清单' : '填写需求清单'} →</button>`;
}

export function renderStats(state) {
  const workers = state.tasks.filter((task) => task.kind === 'worker');
  const active = state.tasks.filter((task) => task.status === 'running');
  const completed = workers.filter((task) => task.status === 'done').length;
  const online = state.machines.filter((machine) => machine.online).length;
  const reviews = state.tasks.filter(
    (task) => task.kind === 'reviewer' && ['queued', 'running'].includes(task.status),
  ).length;
  const cards = [
    ['项目任务', String(workers.length), '项', '清晰分工 · 独立工作目录', 'board'],
    [
      '正在执行',
      String(active.length),
      '个成员',
      active.length ? '成员正在推进分配的工作' : '等待成员领取任务',
      'spark',
    ],
    [
      '已验收完成',
      String(completed),
      `/ ${workers.length}`,
      reviews ? `${reviews} 项正在等待独立验收` : '依据需求与实际成果验收',
      'check',
    ],
    [
      '在线电脑',
      String(online),
      `/ ${state.machines.length}`,
      online ? '执行端通过主动连接领取任务' : '连接你的第一台执行电脑',
      'monitor',
    ],
  ];
  $('#stats').innerHTML = cards
    .map(
      (card) =>
        `<article class="stat-card"><div class="stat-top"><span>${card[0]}</span>${icon(card[4])}</div><div class="stat-value-row"><span class="stat-value">${card[1]}</span><span class="stat-unit">${card[2]}</span></div><p class="stat-foot"><i></i>${e(card[3])}</p></article>`,
    )
    .join('');
}

export function renderFleet(state) {
  const online = state.machines.filter((machine) => machine.online).length;
  $('#fleet-summary').innerHTML =
    `<i></i><span>${online} 台在线</span><span class="muted">·</span><span>通过私有网络协作</span>`;
  $('#machine-list').innerHTML = state.machines.length
    ? state.machines
        .map((machine) => {
          const agents = state.agents.filter((agent) => agent.machine_id === machine.id);
          const busy = agents.reduce((total, agent) => total + (agent.busy || 0), 0);
          const slots = Math.max(1, Math.min(8, machine.slots || 1));
          return `<article class="machine-card"><div class="machine-row"><span class="machine-icon">${icon('monitor')}</span><div class="machine-info"><strong>${e(machine.name)}</strong><small>${e(machine.os)} · ${agents.length} 个配置</small></div><span class="online-dot ${machine.online ? '' : 'offline'}" title="${machine.online ? '在线' : '离线'}"></span></div><div class="machine-slots">${Array.from({ length: slots }, (_, index) => `<span class="${index < busy ? 'occupied' : ''}"></span>`).join('')}<small>${busy} / ${slots}</small></div></article>`;
        })
        .join('')
    : '<div class="empty-small">从 Mac mini 开始连接。每台电脑保留自己的客户端登录态，通过 Tailscale 接入。</div>';
}

function readinessLabel(agent, state) {
  const online = state.machines.find((machine) => machine.id === agent.machine_id)?.online;
  const readiness = agent.readiness || {};
  if (!online) return '电脑离线';
  if (agent.busy) return '执行中';
  if (readiness.dispatchable === false) return readiness.installed === false ? '未找到客户端' : '暂不可执行';
  if (!agent.enabled) return '未启用';
  if (readiness.dispatchable === true) return '可领取 · 账号待验证';
  return '已配置 · 待探测';
}

export function renderAgents(state) {
  const agents = state.agents;
  $('#agent-count').textContent = `${agents.length} 个配置`;
  $('#agent-list').innerHTML = agents.length
    ? agents
        .map(
          (agent) =>
            `<button class="agent-row agent-detail-button" data-action="agent" data-id="${e(agent.id)}"><span class="agent-icon">${e(agent.name.slice(0, 1).toUpperCase())}</span><span class="agent-info"><strong>${e(agent.name)}</strong><small>${e(
              asArray(agent.roles)
                .map((role) => roleLabels[role] || role)
                .join(' / '),
            )} · ${e(agent.model || agent.provider)}</small></span><span class="agent-status ${agent.busy ? 'busy' : ''}">${e(readinessLabel(agent, state))}</span></button>`,
        )
        .join('')
    : '<p class="empty-small">连接电脑并选择本机已安装的客户端。配置存在、客户端可连接、订阅账号可用是不同状态。</p>';
}

export function openAgent(id, state, openModal) {
  const agent = state.agents.find((item) => item.id === id);
  if (!agent) return;
  const machine = state.machines.find((item) => item.id === agent.machine_id);
  const r = agent.readiness || {};
  const providerNote = providerInfo[agent.provider]?.note || '由执行电脑上的本地配置管理。';
  openModal(
    agent.name,
    '连接状态与可用能力',
    `<div class="detail-meta"><div>执行电脑<strong>${e(machine?.name || '未知')} · ${machine?.online ? '在线' : '离线'}</strong></div><div>配置状态<strong>${agent.enabled ? '已启用' : '未启用'} · ${e(agent.provider)}</strong></div><div>客户端检查<strong>${r.installed === true ? '已检测到' : r.installed === false ? '未找到' : '尚无检测结果'}</strong></div><div>本地连接<strong>${r.transport_ready === true ? '已通过连接检查' : r.transport_ready === false ? '连接不可用' : '未执行连接检查'}</strong></div><div>配置模型<strong>${e(agent.model || '使用客户端当前配置')}</strong></div><div>订阅账号 / 额度<strong>未验证 · 以实际任务结果为准</strong></div><div>会话模式<strong>${agent.mode === 'existing' ? '接续指定桌面会话' : '创建新任务会话'}</strong></div></div><div class="detail-section"><h3>能力</h3><p>${e(
      asArray(agent.capabilities)
        .map((value) => capabilityLabels[value] || value)
        .join('、'),
    )}</p></div>${agent.routing_profile && Object.keys(agent.routing_profile).length ? `<div class="detail-section"><h3>模型调度声明</h3><pre>${e(JSON.stringify(agent.routing_profile, null, 2))}</pre></div>` : ''}<div class="notice">${e(r.reason || providerNote)}</div><div class="detail-section"><h3>在这台电脑检查</h3><pre>pal-arena doctor --config worker.json</pre><p>先检查配置与本地客户端，再启动执行端。这里不会把运行中的电脑直接视为已登录、额度充足的 AI 成员。</p></div><div class="modal-actions"><button class="button" type="button" data-action="setup-help">查看接入指南</button><button class="button primary" type="button" data-action="close-modal">关闭</button></div>`,
  );
}

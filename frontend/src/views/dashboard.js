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

/**
 * One member's dispatch state, from facts the center actually holds. Order matters: a stale
 * readiness report from a computer that has since gone offline must not read as "ready".
 * tone: ready | busy | wait | off | problem
 */
function detection(agent) {
  const r = agent.readiness || {};
  if (typeof r.installed !== 'boolean') return { key: 'unprobed', label: '待探测', tone: 'wait' };
  if (r.installed === false) return { key: 'missing', label: '未找到客户端', tone: 'problem' };
  if (r.transport_ready === false) return { key: 'transport', label: '连接不可用', tone: 'problem' };
  if (r.dispatchable === true) return null;
  return { key: 'blocked', label: '暂不可执行', tone: 'problem' };
}

function memberState(agent, machine) {
  if (!machine?.online) return { key: 'offline', label: '电脑离线', tone: 'off' };
  const check = detection(agent);
  // Running on an online computer is a current fact on its own. It says nothing about the
  // client check, so an unfinished check is shown next to it rather than inferred from it.
  if (agent.busy) return { key: 'busy', label: `执行中 ${agent.busy}`, tone: 'busy', check };
  if (!agent.enabled) return { key: 'disabled', label: '未启用', tone: 'off' };
  return check || { key: 'ready', label: '就绪', tone: 'ready' };
}

const machineOf = (agent, state) => state.machines.find((machine) => machine.id === agent.machine_id);
const providerLabel = (provider) => providerInfo[provider]?.label || provider || '未知客户端';
const stateBadge = (status) =>
  `<span class="member-badges"><span class="member-state tone-${status.tone}"><span aria-hidden="true"></span>${e(status.label)}</span>${status.check ? `<span class="member-check tone-${status.check.tone}">检测：${e(status.check.label)}</span>` : ''}</span>`;

function seenAgo(seconds) {
  if (!seconds) return '';
  const minutes = Math.round((Date.now() / 1000 - seconds) / 60);
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)} 小时前`;
  return `${Math.round(minutes / 1440)} 天前`;
}

/** The single next step for a computer, using actions the app already handles. */
function machineNext(machine, members) {
  if (!machine.online)
    return {
      text: `最后在线 ${seenAgo(machine.seen) || '时间未知'}。在那台电脑上重新运行 worker，它会自动回来。`,
      action: '<button class="text-link" data-action="setup-help">查看接入步骤</button>',
    };
  if (!members.length)
    return {
      text: '已连上中心，但 worker.json 里没有成员。',
      action: '<button class="text-link" data-action="invite-machine">重新生成配置</button>',
    };
  // A running member can still have an unfinished check; look at the check, not the work.
  const problem = members
    .map(({ agent, status }) => ({ agent, check: status.check || status }))
    .find(({ check }) => check.tone === 'problem' || check.key === 'unprobed');
  if (problem)
    return {
      text:
        problem.check.key === 'unprobed'
          ? '成员还没报告检测结果，worker 启动后会自动检测。'
          : `${problem.agent.name}：${problem.check.label}。`,
      action: `<button class="text-link" data-action="agent" data-id="${e(problem.agent.id)}">看原因</button>`,
    };
  if (members.every(({ status }) => status.key === 'disabled'))
    return {
      text: '成员都没有启用，在 worker.json 里打开 enabled 后重启 worker。',
      action: '<button class="text-link" data-action="setup-help">接入指南</button>',
    };
  return null;
}

export function renderFleet(state) {
  const machines = [...state.machines].sort(
    (a, b) => Number(b.online) - Number(a.online) || String(a.name).localeCompare(String(b.name)),
  );
  const online = machines.filter((machine) => machine.online).length;
  $('#fleet-summary').innerHTML =
    `<span class="online-dot ${online ? '' : 'offline'}"></span>${online} / ${machines.length} 台在线`;
  if (!machines.length) {
    $('#machine-list').innerHTML =
      '<div class="fleet-empty"><strong>还没有电脑接入</strong><p>先让其他电脑能访问到这里，再给每台电脑生成令牌和 worker.json。接几台都可以，在线的电脑会自动出现在这里。</p><div class="fleet-empty-actions"><button class="button primary small" data-action="invite-machine">连接电脑</button><button class="text-link" data-action="network-help">怎么组网</button></div></div>';
    return;
  }
  $('#machine-list').innerHTML = machines
    .map((machine) => {
      const members = state.agents
        .filter((agent) => agent.machine_id === machine.id)
        .map((agent) => ({ agent, status: memberState(agent, machine) }));
      const slot = slots(machine, state);
      const free = Math.max(0, slot.total - slot.busy);
      const headline = !machine.online
        ? '离线'
        : slot.busy
          ? `在线，${slot.busy} 个执行位在用`
          : `在线，${free} 个执行位空闲`;
      const next = machineNext(machine, members);
      const logins = asArray(machine.github_members).map((item) => item.github_login);
      return `<article class="machine-card ${machine.online ? '' : 'offline'}"><header class="machine-head"><span class="online-dot ${machine.online ? '' : 'offline'}" aria-hidden="true"></span><div class="machine-title"><strong title="${e(machine.name)}">${e(machine.name)}</strong><span>${e(headline)}</span></div><span class="machine-os">${e(machine.os || '未知系统')}</span></header>${machine.online ? `<div class="machine-slots">${slot.html}<small>${slot.busy} / ${slot.total}</small></div>` : ''}${
        members.length
          ? `<ul class="machine-members">${members
              .map(
                ({ agent, status }) =>
                  `<li><button class="machine-member" data-action="agent" data-id="${e(agent.id)}"><span class="machine-member-name">${e(agent.name)}</span>${stateBadge(status)}</button></li>`,
              )
              .join('')}</ul>`
          : ''
      }${next ? `<div class="machine-next"><span>${e(next.text)}</span>${next.action}</div>` : ''}${logins.length ? `<p class="machine-foot">可代为提交的 GitHub 账号：${e(logins.join('、'))}</p>` : ''}</article>`;
    })
    .join('');
}

export function renderAgents(state) {
  const rows = state.agents.map((agent) => ({ agent, status: memberState(agent, machineOf(agent, state)) }));
  const count = (...keys) => rows.filter(({ status }) => keys.includes(status.key)).length;
  // Every member lands in exactly one bucket, so the four numbers always add up to the total.
  const tally = [
    ['ready', '就绪', count('ready')],
    ['busy', '执行中', count('busy')],
    ['problem', '需处理', count('unprobed', 'missing', 'transport', 'blocked')],
    ['off', '离线或未启用', count('offline', 'disabled')],
  ];
  $('#agent-count').textContent = `共 ${rows.length} 个`;
  if (!rows.length) {
    $('#agent-list').innerHTML =
      '<p class="empty-small agent-empty">成员写在每台电脑的 worker.json 里。电脑接入后，它配置的 AI 成员会出现在这里。</p>';
    return;
  }
  $('#agent-list').innerHTML = `<div class="member-tally">${tally
    .map(
      ([tone, label, n]) =>
        `<div class="tone-${tone} ${n ? '' : 'zero'}"><strong>${n}</strong><span>${label}</span></div>`,
    )
    .join('')}</div>${rows
    .map(({ agent, status }) => {
      const machine = machineOf(agent, state);
      const roles = asArray(agent.roles)
        .map((role) => roleLabels[role] || role)
        .join(' / ');
      return `<button class="agent-row agent-detail-button" data-action="agent" data-id="${e(agent.id)}"><span class="agent-icon" aria-hidden="true">${e(
        String(agent.name || '?')
          .slice(0, 1)
          .toUpperCase(),
      )}</span><span class="agent-info"><strong title="${e(agent.name)}">${e(agent.name)}</strong><small>${e(roles)}，在 ${e(machine?.name || '未知电脑')} 上，用 ${e(providerLabel(agent.provider))}</small></span>${stateBadge(status)}</button>`;
    })
    .join('')}`;
}

const difficultyLabels = { routine: '常规', standard: '标准', deep: '深入' };
const quotaLabels = {
  unknown: '未知',
  available: '自行确认可用',
  limited: '额度紧张',
  exhausted: '已耗尽，暂停分派',
};

function preferenceRows(profile) {
  const score = (value) =>
    Number.isFinite(Number(value)) && value !== null && value !== ''
      ? `<span class="score" aria-label="${Number(value)} 分，满分 5">${Array.from({ length: 5 }, (_, i) => `<span class="${i < Number(value) ? 'on' : ''}"></span>`).join('')}<b>${Number(value)}</b></span>`
      : '未知';
  const now = Date.now() / 1000;
  const rows = [
    ['质量', score(profile.quality)],
    ['速度', score(profile.speed)],
    ['性价比', score(profile.economy)],
    ['最高承接难度', e(difficultyLabels[profile.max_difficulty] || '未知')],
    [
      '上下文容量',
      profile.context_window ? `${Number(profile.context_window).toLocaleString('zh-CN')} tokens` : '未声明',
    ],
    ['额度声明', e(quotaLabels[profile.quota_state] || '未知')],
  ];
  if (profile.paused_until > now)
    rows.push(['手动冷却', `到 ${e(new Date(profile.paused_until * 1000).toLocaleString('zh-CN'))}`]);
  return rows.map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join('');
}

export function openAgent(id, state, openModal) {
  const agent = state.agents.find((item) => item.id === id);
  if (!agent) return;
  const machine = machineOf(agent, state);
  const r = agent.readiness || {};
  const status = memberState(agent, machine);
  const providerNote = providerInfo[agent.provider]?.note || '由执行电脑上的本地配置管理。';
  // Three separate facts. "Detected" on an offline computer is only the last report, so it says so.
  const stale = !machine?.online;
  const fact = (ok, yes, no, unknown) =>
    ok === true ? ['ok', yes] : ok === false ? ['bad', no] : ['unknown', unknown];
  const facts = [
    [
      '电脑',
      machine?.online ? ['ok', `${machine.name} 在线`] : ['bad', `${machine?.name || '未知电脑'} 离线`],
    ],
    [
      '客户端',
      stale && typeof r.installed === 'boolean'
        ? ['unknown', `上次报告：${r.installed ? '检测到' : '未找到'}，电脑离线后无法确认`]
        : fact(
            typeof r.installed === 'boolean' ? r.installed : null,
            `检测到 ${providerLabel(agent.provider)}`,
            '没有找到客户端',
            '还没检测',
          ),
    ],
    [
      '本地连接',
      stale
        ? ['unknown', '电脑离线，无法确认']
        : fact(r.transport_ready, '连接正常', '连接不可用', '还没检查'),
    ],
    [
      '成员',
      [
        status.tone === 'ready' || status.tone === 'busy'
          ? 'ok'
          : status.tone === 'problem'
            ? 'bad'
            : 'unknown',
        status.check ? `${status.label}，检测尚未通过（${status.check.label}）` : status.label,
      ],
    ],
  ];
  const profile = agent.routing_profile || {};
  openModal(
    agent.name,
    `${asArray(agent.roles)
      .map((role) => roleLabels[role] || role)
      .join(' / ')}，${providerLabel(agent.provider)}`,
    `<ol class="fact-chain">${facts
      .map(
        ([label, [tone, text]]) =>
          `<li class="fact-${tone}"><span class="fact-mark" aria-hidden="true"></span><span class="fact-label">${label}</span><span class="fact-text">${e(text)}</span></li>`,
      )
      .join(
        '',
      )}</ol>${r.reason ? `<div class="notice">${e(r.reason)}</div>` : ''}<dl class="detail-meta member-meta"><div><dt>模型</dt><dd>${e(agent.model || '跟随客户端设置')}</dd></div><div><dt>会话</dt><dd>${agent.mode === 'existing' ? '接续指定的桌面会话' : '每个任务新建会话'}</dd></div><div><dt>能力</dt><dd>${e(
      asArray(agent.capabilities)
        .map((value) => capabilityLabels[value] || value)
        .join('、') || '未声明',
    )}</dd></div><div><dt>订阅额度</dt><dd>平台不检测，以实际运行为准</dd></div></dl><div class="detail-section"><h3>调度偏好</h3><dl class="detail-meta member-meta">${preferenceRows(profile)}</dl><p class="muted small">这些是配置时填写的偏好声明，负责人结合任务难度和运行记录使用。</p></div>${r.reason ? '' : `<p class="muted small">${e(providerNote)}</p>`}<div class="detail-section"><h3>在这台电脑上自检</h3><pre>pal-arena doctor --config worker.json</pre></div><div class="modal-actions"><button class="button" type="button" data-action="setup-help">接入指南</button><button class="button primary" type="button" data-action="close-modal">关闭</button></div>`,
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

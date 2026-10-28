import { $, $$, e, icon, roleLabels, capabilityLabels } from '../components/ui.js';

export const providerInfo = {
  codex: {
    label: 'Codex CLI',
    note: '检测已登录的 Codex CLI；不接管 Codex 桌面应用里已有的聊天。',
    existing: false,
  },
  claude: {
    label: 'Claude Code CLI',
    note: '需要 Claude Code CLI；Claude 桌面聊天与 CLI 登录可用性需要分别确认。',
    existing: false,
  },
  gemini: {
    label: 'Gemini CLI',
    note: '使用本机 Gemini CLI 及其登录态；不接管 Gemini 网页聊天。',
    existing: false,
  },
  qoder: {
    label: 'Qoder · ACP',
    note: '通过 Qoder ACP 创建任务会话；不会接管 IDE 中已经打开的聊天。',
    existing: false,
  },
  qodercn: {
    label: 'Qoder CN · ACP',
    note: '国内版 Qoder ACP 独立适配；需要可用的官方 ACP 可执行文件。',
    existing: false,
  },
  antigravity: {
    label: 'Antigravity 桌面版',
    note: '连接正在运行的桌面后端，支持新任务或指定已有会话；模型 ID 需要与本机模型列表一致。',
    existing: true,
  },
  opencode: {
    label: 'OpenCode 本机服务',
    note: '连接本机 OpenCode HTTP 服务；使用该服务已经配置的模型和登录态。',
    existing: false,
  },
  desktop_mcp: {
    label: 'WorkBuddy 桌面 · MCP',
    note: '在已登录的桌面客户端主动领取共享任务；需独立电脑令牌、单个执行成员和单槽。任务提交后仍由其他成员验收。',
    existing: false,
  },
  workbuddy: {
    label: 'WorkBuddy · 旧本地接口',
    note: '旧消息接口没有可靠的任务完成信号；请使用上面的桌面 MCP 接入方式。',
    existing: true,
    unavailable: true,
  },
};
export function setupAgentRow(index, value = {}) {
  const provider = value.provider || 'qoder';
  return `<section class="setup-agent" data-agent-row="${index}"><div class="setup-agent-top"><strong>成员 ${index + 1}</strong><label class="check-field"><input type="checkbox" name="enabled-${index}" ${value.enabled ? 'checked' : ''}>启用已安装的客户端</label><button type="button" class="icon-button" data-action="remove-config-agent" data-index="${index}" aria-label="移除成员 ${index + 1}">${icon('close')}</button></div><div class="form-row"><div class="field"><label>接入方式<select name="provider-${index}" data-provider-row="${index}">${Object.entries(
    providerInfo,
  )
    .map(
      ([key, item]) =>
        `<option value="${key}" ${provider === key ? 'selected' : ''} ${item.unavailable ? 'disabled' : ''}>${item.label}</option>`,
    )
    .join(
      '',
    )}</select></label></div><div class="field"><label>角色<select name="role-${index}">${['worker', 'leader', 'reviewer'].map((role) => `<option value="${role}" ${value.role === role ? 'selected' : ''}>${roleLabels[role]}</option>`).join('')}</select></label></div></div><p class="provider-explanation" data-provider-note="${index}">${e(providerInfo[provider].note)}</p><div class="form-row"><div class="field"><label>显示名称<input name="agent-name-${index}" placeholder="留空则按接入方式和角色命名" value="${e(value.name || '')}"></label></div><div class="field"><label>模型 ID<input name="model-${index}" placeholder="可选；Antigravity 必填" value="${e(value.model || '')}"></label></div></div><div class="field"><label>能力（英文逗号分隔）<input name="capabilities-${index}" value="${e(value.capabilities || 'code, web, docs, research, debug')}"></label><small>可选 code、web、docs、research、debug、blender、planning。负责人会自动加入 planning。</small></div><details><summary>模型调度偏好</summary><div class="form-row">${[
    ['quality', '质量'],
    ['speed', '速度'],
    ['economy', '性价比'],
  ]
    .map(
      ([key, label]) =>
        `<div class="field"><label>${label}偏好（1—5）<input type="number" name="${key}-${index}" min="1" max="5" value="3"></label></div>`,
    )
    .join(
      '',
    )}<div class="field"><label>最高承接难度<select name="max-difficulty-${index}"><option value="routine">常规</option><option value="standard">标准</option><option value="deep" selected>深入</option></select></label></div></div><div class="form-row"><div class="field"><label>上下文容量<input type="number" name="context-window-${index}" min="1" max="10000000" placeholder="未声明"></label></div><div class="field"><label>订阅额度状态<select name="quota-${index}"><option value="unknown">未知</option><option value="available">自行确认可用</option><option value="limited">额度紧张</option><option value="exhausted">已耗尽，暂停分派</option></select></label></div></div><p class="provider-explanation">这些是你对模型的偏好声明，结合任务难度和实际运行记录使用；平台不估算订阅的真实价格或剩余额度。</p></details><details><summary>已有桌面会话与可执行路径</summary><div class="field"><label>会话模式<select name="mode-${index}"><option value="new">新建任务会话</option><option value="existing">接续指定桌面会话（仅支持的适配器）</option></select></label></div><div class="field"><label>已有会话 ID<input name="session-${index}" placeholder="接续已有会话时必填"></label></div><div class="field"><label>已有会话的绝对工作目录<input name="workspace-${index}" placeholder="该会话实际使用的本机目录"></label></div><div class="field"><label>可执行文件路径<input name="command-${index}" placeholder="可选：不在 PATH 中时填写完整路径"></label></div></details></section>`;
}
export function machineSetupHTML(invite, address = null) {
  const server = address?.state === 'shared' && address.url ? address.url : location.origin;
  return `<div class="detail-section"><h3>生成这台电脑的配置</h3><div class="field"><label for="setup-server">中心地址</label><input name="setup_server" id="setup-server" type="url" required value="${e(server)}"><small>其他电脑填写中心的 Tailscale HTTPS 地址；127.0.0.1 只适用于中心那台电脑自己。</small></div><div class="form-row"><div class="field"><label for="setup-slots">并发上限</label><input name="setup_slots" id="setup-slots" type="number" min="1" max="8" value="2"></div><div class="field"><label for="setup-os">命令格式</label><select name="setup_os" id="setup-os"><option value="mac">Mac / Linux 终端</option><option value="windows">Windows PowerShell</option></select></div></div><div class="field"><label for="setup-root">本机工作目录</label><input name="setup_root" id="setup-root" value="~/.pal-arena/workspaces"></div><div id="setup-agents">${setupAgentRow(0, { provider: 'qoder', role: 'worker' })}</div><button class="button small" type="button" data-action="add-config-agent">+ 添加另一个成员</button><div class="notice">至少启用一个本机已安装的客户端。可以在不同电脑上分别安排负责人、执行成员与验收成员。配置文件不含令牌。</div><div class="setup-actions"><button class="button primary" type="button" data-action="download-config">下载 worker.json</button><button class="button" type="button" data-action="show-worker-commands">查看启动命令</button></div><div id="worker-command-output" class="detail-section" hidden><h3>启动与检查</h3><pre id="worker-command-text"></pre><button class="button small" type="button" data-action="copy-commands">复制启动命令</button><p class="muted small" id="worker-command-hint">令牌在命令中以占位文字显示，请替换为上面的专属令牌。实际令牌不会写入下载文件。</p></div></div>`;
}
export function collectWorkerConfig(name = 'My computer') {
  const form = new FormData($('#modal-form'));
  const server = String(form.get('setup_server') || '').trim();
  let parsed;
  try {
    parsed = new URL(server);
  } catch {
    throw new Error('填写可访问的中心地址。');
  }
  if (
    !['http:', 'https:'].includes(parsed.protocol) ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    !['', '/'].includes(parsed.pathname)
  )
    throw new Error('中心地址应为不含路径、账号或查询参数的 HTTP(S) 地址。');
  const allowHTTP =
    parsed.protocol === 'http:' && !['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname);
  if (
    allowHTTP &&
    !(/^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(parsed.hostname) || parsed.hostname.endsWith('.ts.net'))
  )
    throw new Error('跨电脑连接请使用 HTTPS 或 Tailscale 地址。');
  const agents = $$('[data-agent-row]').map((row) => {
    const i = row.dataset.agentRow,
      provider = form.get(`provider-${i}`),
      role = form.get(`role-${i}`),
      model = String(form.get(`model-${i}`) || '').trim();
    const enabled = form.get(`enabled-${i}`) === 'on',
      mode = form.get(`mode-${i}`),
      info = providerInfo[provider];
    if (enabled && info.unavailable) throw new Error(`${info.label} 当前不能可靠判断任务完成，暂不可启用。`);
    if (enabled && provider === 'antigravity' && !model)
      throw new Error('Antigravity 成员需要填写本机实际可用的模型 ID。');
    if (mode === 'existing' && !info.existing) throw new Error(`${info.label} 当前不能接管已有桌面会话。`);
    const caps = String(form.get(`capabilities-${i}`) || '')
      .split(',')
      .map((v) => v.trim())
      .filter(Boolean);
    if (caps.some((value) => !Object.hasOwn(capabilityLabels, value)))
      throw new Error('能力名称请使用列表中的英文标识，并用英文逗号分隔。');
    if (role === 'leader' && !caps.includes('planning')) caps.push('planning');
    const agent = {
      key: `agent-${i}`,
      name:
        String(form.get(`agent-name-${i}`) || '').trim() ||
        `${info.label} · ${roleLabels[role]} ${Number(i) + 1}`,
      provider,
      roles: [role],
      capabilities: caps.length ? caps : ['code'],
      model: model || null,
      mode,
      preference: 10,
      enabled,
    };
    if (mode === 'existing') {
      agent.session_id = String(form.get(`session-${i}`) || '').trim();
      agent.workspace = String(form.get(`workspace-${i}`) || '').trim();
      if (!agent.session_id || !agent.workspace)
        throw new Error('接续已有会话需要会话 ID 和该会话的绝对工作目录。');
      if (provider === 'antigravity' && model) agent.adapter = { model };
    }
    const command = String(form.get(`command-${i}`) || '').trim();
    if (command) agent.command = [command];
    const profile = { max_difficulty: form.get(`max-difficulty-${i}`), quota_state: form.get(`quota-${i}`) };
    for (const key of ['quality', 'speed', 'economy']) {
      const value = Number(form.get(`${key}-${i}`));
      if (!Number.isInteger(value) || value < 1 || value > 5)
        throw new Error('模型偏好评分应为 1 到 5 的整数。');
      profile[key] = value;
    }
    const window = Number(form.get(`context-window-${i}`) || 0);
    if (window) {
      if (!Number.isInteger(window) || window < 1 || window > 10000000)
        throw new Error('上下文容量应在 1 到 10000000 之间。');
      profile.context_window = window;
    }
    agent.routing_profile = profile;
    return agent;
  });
  if (!agents.some((agent) => agent.enabled)) throw new Error('请至少启用一个这台电脑实际已安装的客户端。');
  if (new Set(agents.map((agent) => agent.name)).size !== agents.length)
    throw new Error('同一台电脑的成员显示名称不能重复。');
  const slots = Number(form.get('setup_slots'));
  if (!Number.isInteger(slots) || slots < 1 || slots > 8) throw new Error('并发上限应在 1 到 8 之间。');
  if (agents.some((agent) => agent.provider === 'desktop_mcp')) {
    if (agents.length !== 1 || slots !== 1 || agents[0].roles[0] !== 'worker' || agents[0].mode !== 'new')
      throw new Error('WorkBuddy 桌面 MCP 需要单独的电脑令牌、一个执行成员、单槽和新建任务会话。');
  }
  return {
    server: parsed.origin,
    name,
    slots,
    workspace_root: String(form.get('setup_root') || '~/.pal-arena/workspaces').trim(),
    storage_limit_bytes: 5_000_000_000,
    allow_http: allowHTTP,
    agents,
  };
}
export function showWorkerCommands(name) {
  const config = collectWorkerConfig(name);
  if (config.agents[0].provider === 'desktop_mcp') {
    $('#worker-command-text').textContent =
      'pal-arena internal-desktop-mcp --config worker.json --token-file "<令牌文件路径>" --print-mcp-config\n' +
      '源码安装时：python -m arena.desktop_mcp --config worker.json --token-file "<令牌文件路径>" --print-mcp-config';
    $('#worker-command-hint').textContent =
      '先将专属令牌单独保存到本机文件并替换路径；将输出的 MCP 配置加入 WorkBuddy，然后在共享会话中主动调用 next_task。';
    $('#worker-command-output').hidden = false;
    return;
  }
  const windows = $('#setup-os').value === 'windows';
  const command = windows
    ? "$env:ARENA_MACHINE_TOKEN = '<电脑专属令牌>'"
    : "export ARENA_MACHINE_TOKEN='<电脑专属令牌>'";
  $('#worker-command-text').textContent =
    `${command}\npal-arena doctor --config worker.json\npython -m arena.worker --config worker.json`;
  $('#worker-command-hint').textContent =
    '令牌在命令中以占位文字显示，请替换为上面的专属令牌。实际令牌不会写入下载文件。';
  $('#worker-command-output').hidden = false;
}

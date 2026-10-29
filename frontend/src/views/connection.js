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
  dsh: {
    label: 'DeepSeek Harness · ACP',
    note: '使用 DSH 的 ACP profile；请先在 DSH 中配置所需模型和服务商。可在高级设置中指定自己的 ACP profile。',
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
    label: '桌面客户端 · MCP',
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
  const provider = Object.hasOwn(providerInfo, value.provider) ? value.provider : 'qoder';
  const info = providerInfo[provider];
  const profile = value.routing_profile || {};
  const existing = info.existing && value.mode === 'existing';
  return `<section class="setup-agent" data-agent-row="${index}">
    <div class="setup-agent-top"><strong>${icon('terminal')} 成员 ${index + 1}</strong><label class="check-field"><input type="checkbox" name="enabled-${index}" ${value.enabled ? 'checked' : ''}>启用</label><button type="button" class="icon-button" data-action="remove-config-agent" data-index="${index}" aria-label="移除成员 ${index + 1}">${icon('close')}</button></div>
    <div class="form-row"><div class="field"><label>客户端<select name="provider-${index}" data-provider-row="${index}">${Object.entries(
      providerInfo,
    )
      .filter(([key, item]) => !item.unavailable || key === provider)
      .map(
        ([key, item]) =>
          `<option value="${key}" ${provider === key ? 'selected' : ''} ${item.unavailable ? 'disabled' : ''}>${item.label}</option>`,
      )
      .join(
        '',
      )}</select></label></div><div class="field"><label>负责什么<select name="role-${index}">${['worker', 'leader', 'reviewer'].map((role) => `<option value="${role}" ${value.role === role || value.roles?.includes(role) ? 'selected' : ''}>${roleLabels[role]}</option>`).join('')}</select></label></div></div>
    <p class="provider-explanation" data-provider-note="${index}">${e(info.note)}</p>
    <div class="form-row"><div class="field"><label>成员名称<input name="agent-name-${index}" placeholder="留空则自动命名" value="${e(value.name || '')}"></label></div><div class="field"><label><span data-model-label>${provider === 'antigravity' ? '模型 ID（必填）' : '模型 ID（可选）'}</span><input name="model-${index}" placeholder="使用客户端的默认模型" value="${e(value.model || '')}"></label></div></div>
    <div class="field" data-provider-field="dsh" ${provider === 'dsh' ? '' : 'hidden'}><label>DSH ACP profile<input name="dsh-profile-${index}" value="${e(value.adapter?.profile || '')}" placeholder="acp"></label><small>填写已配置模型路由的 profile；留空使用 acp。</small></div>
    <details><summary>能力与调度偏好</summary><div class="field"><label>能做什么<input name="capabilities-${index}" value="${e(Array.isArray(value.capabilities) ? value.capabilities.join(', ') : value.capabilities || 'code, web, docs, research, debug')}"></label><small>用英文逗号分隔：code、web、docs、research、debug、blender、planning。</small></div>
      <div class="form-row">${[
        ['quality', '质量'],
        ['speed', '速度'],
        ['economy', '性价比'],
      ]
        .map(
          ([key, label]) =>
            `<div class="field"><label>${label}偏好（1—5）<input type="number" name="${key}-${index}" min="1" max="5" value="${e(profile[key] || 3)}"></label></div>`,
        )
        .join('')}</div>
      <div class="form-row"><div class="field"><label>最高承接难度<select name="max-difficulty-${index}">${[
        ['routine', '常规'],
        ['standard', '标准'],
        ['deep', '深入'],
      ]
        .map(
          ([key, label]) =>
            `<option value="${key}" ${(profile.max_difficulty || 'deep') === key ? 'selected' : ''}>${label}</option>`,
        )
        .join(
          '',
        )}</select></label></div><div class="field"><label>上下文容量<input type="number" name="context-window-${index}" min="1" max="10000000" placeholder="未声明" value="${e(profile.context_window || '')}"></label></div></div>
      <div class="field"><label>订阅额度<select name="quota-${index}">${[
        ['unknown', '未知'],
        ['available', '自行确认可用'],
        ['limited', '额度紧张'],
        ['exhausted', '已耗尽，暂停分派'],
      ]
        .map(
          ([key, label]) =>
            `<option value="${key}" ${(profile.quota_state || 'unknown') === key ? 'selected' : ''}>${label}</option>`,
        )
        .join('')}</select></label><small>用于安排任务；平台不会估算订阅的真实剩余额度。</small></div>
    </details>
    <details><summary>${info.existing ? '可执行路径与已有会话' : '自定义可执行路径'}</summary>
      <div class="field"><label>可执行文件<input name="command-${index}" value="${e(value.command?.[0] || '')}" placeholder="可选：客户端不在 PATH 中时填写完整路径"></label></div>
      <div data-provider-field="existing" ${info.existing ? '' : 'hidden'}><div class="field"><label>会话模式<select name="mode-${index}" data-session-mode><option value="new" ${existing ? '' : 'selected'}>新建任务会话</option><option value="existing" ${existing ? 'selected' : ''}>接续指定桌面会话</option></select></label></div>
      <div data-session-fields ${existing ? '' : 'hidden'}><div class="field"><label>已有会话 ID<input name="session-${index}" value="${e(value.session_id || '')}" placeholder="接续时必填"></label></div><div class="field"><label>该会话的绝对工作目录<input name="workspace-${index}" value="${e(value.workspace || '')}" placeholder="该会话实际使用的本机目录"></label></div></div></div>
    </details>
  </section>`;
}
export function updateSetupAgent(row) {
  const index = row.dataset.agentRow;
  const provider = row.querySelector('[data-provider-row]').value;
  const info = providerInfo[provider];
  row.querySelector('[data-provider-note]').textContent = info.note;
  row.querySelector('[data-model-label]').textContent =
    provider === 'antigravity' ? '模型 ID（必填）' : '模型 ID（可选）';
  row.querySelectorAll('[data-provider-field]').forEach((field) => {
    field.hidden =
      field.dataset.providerField === 'existing' ? !info.existing : provider !== field.dataset.providerField;
  });
  row.querySelector('[data-session-fields]').hidden =
    !info.existing || row.querySelector(`[name="mode-${index}"]`).value !== 'existing';
  row.querySelectorAll('details')[1].querySelector('summary').textContent = info.existing
    ? '可执行路径与已有会话'
    : '自定义可执行路径';
}
export function updateSetupPlatform() {
  const linux = $('#setup-os').value === 'linux';
  const install = $('#setup-install');
  install.querySelector('[value="native"]').disabled = linux;
  if (linux) install.value = 'source';
  $('#setup-platform-hint').textContent =
    install.value === 'native'
      ? 'Windows x64 / Apple Silicon Mac 使用原生包，无需安装 Python。'
      : '在已安装 .[platform] 依赖的源码目录中运行，使用 .pal_venv 虚拟环境。';
}
export function machineSetupHTML(invite, address = null) {
  const server = address?.state === 'shared' && address.url ? address.url : location.origin;
  const platform = globalThis.navigator?.userAgent || '';
  const os = /Windows/i.test(platform) ? 'windows' : /Linux/i.test(platform) ? 'linux' : 'mac';
  return `<div class="machine-setup">
    <section class="setup-section"><div class="setup-section-heading"><span>1</span><div><h3>选择接入环境</h3><p>在准备接入的那台电脑上运行。</p></div></div>
      <div class="form-row"><div class="field"><label for="setup-os">电脑系统</label><select name="setup_os" id="setup-os">${[
        ['windows', 'Windows'],
        ['mac', 'macOS'],
        ['linux', 'Linux'],
      ]
        .map(([key, label]) => `<option value="${key}" ${os === key ? 'selected' : ''}>${label}</option>`)
        .join(
          '',
        )}</select></div><div class="field"><label for="setup-install">运行方式</label><select name="setup_install" id="setup-install"><option value="native" ${os === 'linux' ? 'disabled' : 'selected'}>原生安装包</option><option value="source" ${os === 'linux' ? 'selected' : ''}>源码环境</option></select></div></div>
      <p class="provider-explanation" id="setup-platform-hint">${os === 'linux' ? '在已安装 .[platform] 依赖的源码目录中运行，使用 .pal_venv 虚拟环境。' : 'Windows x64 / Apple Silicon Mac 使用原生包，无需安装 Python。'}</p>
      <div class="field"><label for="setup-server">中心地址</label><input name="setup_server" id="setup-server" type="url" required value="${e(server)}"><small>其他电脑填写中心的 Tailscale 或 HTTPS 地址；127.0.0.1 仅用于本机。</small></div>
      <details class="setup-machine-options"><summary>工作目录与并发</summary><div class="field"><label for="setup-root">本机工作目录</label><input name="setup_root" id="setup-root" value="~/.pal-arena/workspaces"></div><div class="field"><label for="setup-slots">同时运行的任务数</label><input name="setup_slots" id="setup-slots" type="number" min="1" max="8" value="2"></div></details>
    </section>
    <section class="setup-section"><div class="setup-section-heading"><span>2</span><div><h3>安排这台电脑的成员</h3><p>选择已安装并登录的客户端，再勾选启用。</p></div></div><div id="setup-agents">${setupAgentRow(0, { provider: 'qoder', role: 'worker' })}</div><button class="button small setup-add" type="button" data-action="add-config-agent">${icon('plus')} 添加成员</button></section>
    <section class="setup-section setup-launch"><div class="setup-section-heading"><span>3</span><div><h3>保存配置，启动接入</h3><p>配置文件不含令牌；启动时填入这台电脑的专属令牌。</p></div></div><div class="setup-actions"><button class="button primary" type="button" data-action="download-config">${icon('download')} 下载 worker.json</button><button class="button" type="button" data-action="show-worker-commands">查看启动命令</button></div><div id="worker-command-output" class="setup-command" hidden><div class="setup-command-heading"><strong>${icon('terminal')} 启动与检查</strong><button class="button small" type="button" data-action="copy-commands">复制命令</button></div><pre id="worker-command-text"></pre><p class="muted small" id="worker-command-hint"></p></div></section>
  </div>`;
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
      info = providerInfo[provider];
    if (!info) throw new Error('请选择可用的客户端。');
    const mode = info.existing ? form.get(`mode-${i}`) || 'new' : 'new';
    if (enabled && info.unavailable) throw new Error(`${info.label} 当前不能可靠判断任务完成，暂不可启用。`);
    if (enabled && provider === 'antigravity' && !model)
      throw new Error('Antigravity 成员需要填写本机实际可用的模型 ID。');
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
    if (provider === 'dsh') {
      const acpProfile = String(form.get(`dsh-profile-${i}`) || 'acp').trim() || 'acp';
      if (acpProfile.startsWith('-')) throw new Error('DSH profile 应为名称，不能填写命令参数。');
      agent.adapter = { profile: acpProfile };
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
      throw new Error('桌面 MCP 需要单独的电脑令牌、一个执行成员和单槽。');
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
export function workerCommands(config, os, install) {
  if (!['windows', 'mac', 'linux'].includes(os) || !['native', 'source'].includes(install))
    throw new Error('请选择电脑系统和运行方式。');
  if (os === 'linux' && install === 'native') throw new Error('Linux 请使用源码环境。');
  const windows = os === 'windows';
  const native = windows ? '.\\pal-arena\\pal-arena.exe' : './pal-arena/pal-arena';
  const python = windows ? '.\\.pal_venv\\Scripts\\python.exe' : './.pal_venv/bin/python';
  const desktop = config.agents.some((agent) => agent.provider === 'desktop_mcp');
  if (desktop)
    return {
      command: `${install === 'native' ? native + ' internal-desktop-mcp' : python + ' -m arena.desktop_mcp'} --config worker.json --token-file "<令牌文件路径>" --print-mcp-config`,
      hint: '将专属令牌单独保存为本机文件，替换上面的路径。把生成的 MCP 配置加入桌面客户端，再在共享会话中调用 next_task。',
    };
  const token = windows
    ? "$env:ARENA_MACHINE_TOKEN = '<电脑专属令牌>'"
    : "export ARENA_MACHINE_TOKEN='<电脑专属令牌>'";
  const doctor = install === 'native' ? native : python + ' -m arena';
  const worker = install === 'native' ? native + ' worker' : python + ' -m arena.worker';
  return {
    command: `${token}\n${doctor} doctor --config worker.json\n${worker} --config worker.json`,
    hint:
      install === 'native'
        ? '在解压后的安装包目录运行。将令牌占位文字换成这台电脑的专属令牌，worker.json 也放在这个目录。'
        : '在已安装 .[platform] 依赖的源码目录运行。将令牌占位文字替换后，依次检查并启动。',
  };
}
export function showWorkerCommands(name) {
  const result = workerCommands(collectWorkerConfig(name), $('#setup-os').value, $('#setup-install').value);
  $('#worker-command-text').textContent = result.command;
  $('#worker-command-hint').textContent = result.hint;
  $('#worker-command-output').hidden = false;
}

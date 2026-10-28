import './styles.css';
import {
  $,
  $$,
  e,
  icon,
  hydrateIcons,
  status,
  statusLabels,
  roleLabels,
  capabilityLabels,
  size,
  timeLabel,
  dateLabel,
  asArray,
  lines,
  emptyState,
} from './components/ui.js';
import { emptySnapshot, createSubmissionQueue } from './lib/state.js';
import { setTheme, restoreTheme } from './components/theme.js';
import { createPalette, taskHint } from './components/palette.js';
import { closeSidebar, toggleSidebar, initializeNavigation } from './components/navigation.js';
import { request, makeRequestId } from './lib/api.js';
import { renderTasks, renderReviews, renderFiles, renderLogs } from './views/workspace.js';
import { renderMemories } from './views/memory.js';
import {
  providerInfo,
  setupAgentRow,
  machineSetupHTML,
  collectWorkerConfig,
  showWorkerCommands,
} from './views/connection.js';
import {
  renderOverview,
  renderStats,
  renderAttention,
  renderNetwork,
  renderFleet,
  renderAgents,
  openAgent,
} from './views/dashboard.js';
import { createTaskDialogs } from './views/tasks.js';
import { createProjectDialogs } from './views/projects.js';
import { demoState } from './data/demo.js';

const demo = window.__PAL_ARENA_DEMO__ === true || new URLSearchParams(location.search).get('demo') === '1';
let identity = null;
let currentProject = null;
let activeView = 'chat';
let channel = 'shared';
let state = emptySnapshot();
let messageSignature = '';
let messageSending = false;
let pollInFlight = false;
let refreshSerial = 0;
let toastTimer;
let modalAction;
let dialogSecret = null;
let modalTaskId = null;
let modalTaskSignature = '';
let modalObjectUrls = [];
let modalGeneration = 0;
let artifactPreviewSerial = 0;
let teamIdentities = [];
let memoryScope = 'project';
let memories = [];
let memoryLoadSerial = 0;
let unreadDrafts = new Map();
let networkAddress = null;

function toast(message, isError = false) {
  clearTimeout(toastTimer);
  const node = $('#toast');
  node.textContent = message;
  node.className = `toast${isError ? ' error' : ''}`;
  node.hidden = false;
  toastTimer = setTimeout(
    () => {
      node.hidden = true;
    },
    isError ? 8000 : 4500,
  );
}
async function api(path, options = {}) {
  return request(path, options, { demo, onUnauthorized: showLogin });
}
const post = (path, body = {}) => api(path, { method: 'POST', body: JSON.stringify(body) });
const submissions = createSubmissionQueue(post, makeRequestId);
const idempotentPost = (path, body) => submissions.submit(path, body);
function project() {
  return state.projects.find((item) => item.id === currentProject);
}
function agentName(id) {
  return state.agents.find((agent) => agent.id === id)?.name || '等待分配';
}
function showLogin() {
  identity = null;
  currentProject = null;
  channel = 'shared';
  messageSignature = '';
  refreshSerial += 1;
  pollInFlight = false;
  memories = [];
  unreadDrafts.clear();
  submissions.clear();
  state = emptySnapshot();
  $('#message-input').value = '';
  closeModal();
  render();
  navigate('chat');
  $('#app').hidden = true;
  $('#login-screen').hidden = false;
  $('#login-token').focus();
}
function showApp() {
  $('#app').hidden = false;
  $('#login-screen').hidden = true;
  $('#demo-banner').hidden = !demo;
  $('#user-name').textContent = identity?.name || '你';
  $('#user-avatar').textContent = (identity?.name || 'Y').slice(0, 1).toUpperCase();
}
function setCount(selector, value, quiet = false) {
  const node = $(selector);
  node.textContent = String(value);
  node.classList.toggle('quiet', quiet || !value);
}
function fitComposer() {
  const input = $('#message-input');
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
}
function setConnection(ok, label) {
  $('#connection-status').className = `connection-indicator${ok ? '' : ' error'}`;
  $('#connection-status').innerHTML = `<i></i>${e(label)}`;
}
const views = ['chat', 'tasks', 'review', 'files', 'machines', 'memory', 'logs'];
/** Location is #/<project>/<view> or #/<project>/task/<task>, so a link or a reload lands on the same place. */
function readRoute() {
  const [projectId = '', view = '', taskId = ''] = location.hash
    .replace(/^#\/?/, '')
    .split('/')
    .map((part) => decodeURIComponent(part));
  return {
    projectId,
    view: views.includes(view) ? view : '',
    taskId: view === 'task' ? taskId : '',
  };
}
function writeRoute() {
  if (!identity) return;
  const tail = modalTaskId ? `task/${encodeURIComponent(modalTaskId)}` : activeView;
  const next = `#/${encodeURIComponent(currentProject || '')}/${tail}`;
  if (location.hash !== next) history.replaceState(null, '', next);
  try {
    if (currentProject) localStorage.setItem('pal-arena-project', currentProject);
  } catch {
    /* Storage can be disabled. */
  }
}
function preferredProject(projects) {
  let remembered = readRoute().projectId;
  try {
    remembered ||= localStorage.getItem('pal-arena-project') || '';
  } catch {
    /* Storage can be disabled. */
  }
  return (projects.find((item) => item.id === remembered) || projects[0])?.id || null;
}
async function refresh(force = false) {
  if (!identity || (pollInFlight && !force)) return;
  pollInFlight = true;
  const serial = ++refreshSerial;
  const requestedChannel = channel;
  let requestedProject = currentProject;
  try {
    if (demo) state = demoState(channel);
    else {
      const query = new URLSearchParams({ channel });
      if (currentProject) query.set('project_id', currentProject);
      const fresh = await api(`/api/state?${query}`);
      if (serial !== refreshSerial || requestedChannel !== channel || requestedProject !== currentProject)
        return;
      state = { ...state, ...fresh };
      if (!currentProject && state.projects.length) {
        currentProject = preferredProject(state.projects);
        requestedProject = currentProject;
        const nextQuery = new URLSearchParams({ project_id: currentProject, channel });
        const detail = await api(`/api/state?${nextQuery}`);
        if (serial !== refreshSerial || requestedChannel !== channel || requestedProject !== currentProject)
          return;
        state = { ...state, ...detail };
      }
    }
    if (demo && !currentProject) currentProject = preferredProject(state.projects);
    if (currentProject && !state.projects.some((item) => item.id === currentProject))
      currentProject = state.projects[0]?.id || null;
    render();
    notifyAttention();
    writeRoute();
    setConnection(true, demo ? '示例数据' : '已同步');
    if (activeView === 'memory' && project()) await loadMemories(false);
  } catch (error) {
    setConnection(false, '连不上中心');
    if (force) toast(error.message, true);
  } finally {
    if (serial === refreshSerial) pollInFlight = false;
  }
}
function render() {
  const p = project();
  $('#breadcrumb-project').textContent = p?.name || '未选择项目';
  $('#project-name').textContent = p?.name || '还没有项目';
  $('#project-description').textContent = p?.brief || '新建一个项目，写下目标和完成条件。';
  $('#project-status').textContent = statusLabels[p?.status] || '准备开始';
  $('#requirement-revision').textContent = `v${p?.revision || 1}`;
  $('#project-list').innerHTML = state.projects.length
    ? state.projects
        .map(
          (item) =>
            `<button class="project-item ${item.id === currentProject ? 'active' : ''}" data-action="project" data-id="${e(item.id)}"><span class="project-dot"></span><span>${e(item.name)}</span></button>`,
        )
        .join('')
    : '<p class="empty-small">还没有项目，点 + 新建。</p>';
  setCount(
    '#task-nav-count',
    state.tasks.filter((task) => ['queued', 'running'].includes(task.status)).length,
  );
  setCount('#review-nav-count', state.tasks.filter((task) => task.status === 'in_review').length);
  setCount('#machine-nav-count', state.machines.filter((machine) => machine.online).length, true);
  const used = state.storage?.used || 0,
    quota = state.storage?.quota || 5e9,
    percent = Math.min(100, (used / quota) * 100);
  $('#sidebar-storage').innerHTML =
    `<span>暂存</span><div class="progress-track"><span style="width:${percent.toFixed(1)}%"></span></div><span>${percent.toFixed(0)}%</span>`;
  $('#sidebar-storage').title = `中心暂存 ${size(used)} / ${size(quota)}`;
  renderStats(state);
  const attention = renderAttention(state);
  document.title = attention ? `(${attention}) Agent Cooperation` : 'Agent Cooperation';
  renderFleet(state);
  renderAgents(state);
  renderMessages();
  renderTasks(state);
  renderReviews(state);
  renderFiles(state);
  renderLogs(state);
  renderMemories(memories, memoryScope);
  renderOverview(state, project());
  refreshTaskDialog();
  $('#message-input').disabled = !p || demo;
  $('#message-form button').disabled = !p || demo || messageSending;
  $('#message-input').placeholder = demo
    ? '示例模式不能发送消息'
    : p
      ? channel === 'personal'
        ? '先和负责人私下理清想法…'
        : '说说要做什么，或者告诉负责人下一步…'
      : '先新建一个项目';
  $('#channel-note').hidden = channel !== 'personal';
  $('#channel-note').textContent = '私聊只有你能看到，不会改动共享需求。想让团队动手，把结论发到团队频道。';
  $('#channel-scope').textContent = channel === 'personal' ? '只有你能看到' : '你和伙伴都能看到';
  const readyLeader = state.agents.some(
    (agent) =>
      agent.enabled &&
      asArray(agent.roles).includes('leader') &&
      state.machines.some((machine) => machine.id === agent.machine_id && machine.online),
  );
  $('#composer-hint').textContent =
    !readyLeader && !demo
      ? '负责人还没上线，消息会先存着'
      : channel === 'personal'
        ? '负责人会在私聊里回复'
        : '负责人会按需求清单拆分任务';
  $$('[data-action="channel"]').forEach((node) =>
    node.classList.toggle('active', node.dataset.channel === channel),
  );
  hydrateIcons();
}
function renderMessages() {
  const list = $('#message-list');
  const visible =
    channel === 'personal'
      ? state.messages.filter((message) => message.channel !== 'shared')
      : state.messages.filter((message) => message.channel === 'shared');
  const signature = JSON.stringify([
    currentProject,
    channel,
    visible.map((item) => [item.id, item.content]),
    state.tasks.slice(0, 3).map((item) => [item.id, item.status]),
  ]);
  if (signature === messageSignature) return;
  messageSignature = signature;
  const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 70;
  if (!visible.length) {
    list.innerHTML = project()
      ? emptyState(
          channel === 'personal' ? '私下先聊聊' : '从目标开始',
          channel === 'personal'
            ? '这里只有你能看到。想清楚了，再把结论发到团队频道。'
            : '比赛题目、要解决的问题、最终作品长什么样，都可以是第一句话。',
        )
      : emptyState(
          '先建一个项目',
          '项目里放目标、代码仓库和完成条件。之后你们在这里和负责人对话。',
          'new-project',
          '新建项目',
        );
    return;
  }
  list.innerHTML =
    `<div class="date-line">${channel === 'personal' ? '私聊' : '团队频道'}</div>` +
    visible
      .map((message, index) => {
        const isAgent =
          message.author !== identity?.name && !['你', 'Partner', 'Owner'].includes(message.author);
        const taskLinks =
          demo && channel === 'shared' && index === visible.length - 1
            ? state.tasks.filter((task) => task.kind === 'worker').slice(0, 3)
            : [];
        return `<article class="message ${isAgent ? 'agent' : ''}"><span class="message-avatar">${isAgent ? icon('spark') : e(message.author.slice(0, 1).toUpperCase())}</span><div class="message-body"><div class="message-meta"><strong>${e(message.author)}</strong>${isAgent ? '<span class="message-role">AI 成员</span>' : ''}<time>${timeLabel(message.created)}</time></div><div class="message-text">${e(message.content)}</div>${taskLinks.length ? `<div class="message-task-links">${taskLinks.map((task) => `<button class="inline-task status-${e(task.status)}" data-action="task" data-id="${e(task.id)}"><span>${icon(task.capability === 'blender' ? 'cube' : 'branch')}</span><span>${e(task.title)}</span>${status(task.status)}</button>`).join('')}</div>` : ''}${channel === 'personal' ? `<button class="text-link" style="margin-top:6px" data-action="share-message" data-id="${e(message.id)}">放到团队频道输入框</button>` : ''}</div></article>`;
      })
      .join('');
  if (nearBottom || visible.length <= 3) list.scrollTop = list.scrollHeight;
}
function navigate(view) {
  if (!views.includes(view)) return;
  activeView = view;
  $('.main').dataset.view = view;
  $('.context-panel').hidden = view !== 'chat';
  $('.content-grid').classList.toggle('single-panel', view !== 'chat');
  $$('.view-panel').forEach((node) => {
    node.hidden = node.id !== `view-${view}`;
  });
  $$('[data-action="navigate"]').forEach((node) => {
    node.classList.toggle('active', node.dataset.view === view);
    if (node.getAttribute('role') === 'tab')
      node.setAttribute('aria-selected', String(node.dataset.view === view));
  });
  closeSidebar();
  writeRoute();
  if (view === 'memory') loadMemories(true).catch((error) => toast(error.message, true));
  if (view === 'machines') loadNetwork();
}
const onCenter = () => ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);
async function loadNetwork() {
  if (demo) {
    renderNetwork(null, { demo: true });
    return;
  }
  try {
    networkAddress = await api('/api/setup/address');
  } catch {
    networkAddress = null;
  }
  renderNetwork(networkAddress, { local: onCenter() });
}
function openNetworkHelp() {
  openModal(
    '让其他电脑和伙伴连上',
    '不需要公网 IP，也不用动路由器。全程大约 10 分钟。',
    `<ol class="setup-steps"><li><strong>每台电脑装上 Tailscale 并登录</strong><p>Mac 和 Windows 都从官网下载安装，打开后点登录。你自己的几台电脑用<strong>同一个账号</strong>登录（GitHub 账号就行），它们就自动在一个私有网络里了。</p><p><a href="https://tailscale.com/download" target="_blank" rel="noopener noreferrer">打开 Tailscale 下载页</a></p></li><li><strong>把伙伴拉进同一个网络</strong><p>在 Tailscale 网页后台打开「Users」→「Invite users」，把邀请链接发给伙伴。伙伴用自己的账号接受邀请，再在他的电脑上登录 Tailscale。</p></li><li><strong>在中心电脑上开启共享</strong><p>在常开的那台电脑（比如 Mac mini）上打开工作台，到「电脑与成员」点「在这台电脑上开启共享」，或者在终端运行：</p><pre>pal-arena share</pre><p>第一次会给你一个 Tailscale 链接，点进去按一次「启用」（打开 HTTPS），回来再点一次共享。</p></li><li><strong>把地址发给伙伴，连接其他电脑</strong><p>成功后会显示一个 <code>https://…ts.net</code> 地址。伙伴在浏览器打开它登录；你在「连接电脑」里生成配置时，这个地址会自动填进去。</p></li></ol><div class="notice">这个地址只有加入了你 Tailscale 网络的设备能打开，外人访问不到。在国内一般可以直接用；两台电脑直连不上时会自动经中继转发，速度慢一点，但这里只传任务、消息和日志，不影响使用。</div><div class="modal-actions">${onCenter() && !demo ? '<button class="button primary" type="button" data-action="share-network">在这台电脑上开启共享</button>' : ''}<button class="button" type="button" data-action="close-modal">关闭</button></div>`,
  );
}
function openModal(title, subtitle, body, onSubmit) {
  modalGeneration += 1;
  releaseModalURLs();
  dialogSecret = null;
  modalTaskId = null;
  modalTaskSignature = '';
  modalAction = onSubmit;
  $('#modal-content').innerHTML =
    `<div class="modal-header"><div><h2 id="modal-title">${e(title)}</h2>${subtitle ? `<p>${e(subtitle)}</p>` : ''}</div><button class="icon-button" data-action="close-modal" aria-label="关闭">${icon('close')}</button></div><form id="modal-form" class="modal-body">${body}<p class="form-error" id="modal-error" role="alert"></p></form>`;
  if (!$('#modal').open) $('#modal').showModal();
  hydrateIcons($('#modal'));
  addCopyButtons($('#modal'));
}
/** Commands and IDs in dialogs are meant to be pasted elsewhere; give each block a copy button. */
function addCopyButtons(root) {
  $$('pre', root).forEach((block) => {
    if (block.parentElement.classList.contains('copyable')) return;
    const wrap = document.createElement('div');
    wrap.className = 'copyable';
    block.replaceWith(wrap);
    wrap.append(block);
    wrap.insertAdjacentHTML(
      'beforeend',
      '<button type="button" class="copy-button" data-action="copy-pre">复制</button>',
    );
  });
}
function releaseModalURLs() {
  modalObjectUrls.forEach((url) => URL.revokeObjectURL(url));
  modalObjectUrls = [];
}
function closeModal() {
  modalGeneration += 1;
  $('#modal').close();
  dialogSecret = null;
  modalAction = null;
  modalTaskId = null;
  modalTaskSignature = '';
  releaseModalURLs();
  $('#modal-content').replaceChildren();
  writeRoute();
}
const modalActions = (label) =>
  `<div class="modal-actions"><button class="button" type="button" data-action="close-modal">取消</button><button class="button primary" type="submit">${e(label)}</button></div>`;
function editable() {
  if (demo) {
    toast('这是只读效果预览。进入实际工作台后可以操作。');
    return false;
  }
  return true;
}
function requireProject() {
  if (!project()) {
    toast('先创建一个项目。');
    openProject();
    return false;
  }
  return true;
}
function refreshTaskDialog() {
  if (!modalTaskId || !$('#modal').open) return;
  const task = state.tasks.find((item) => item.id === modalTaskId);
  if (!task) return;
  const signature = JSON.stringify(task);
  if (signature === modalTaskSignature) return;
  const scroll = $('#modal').scrollTop;
  const id = modalTaskId;
  openTask(id);
  $('#modal').scrollTop = scroll;
}
function openInvite(kind = 'member') {
  if (!editable()) return;
  openModal(
    '让伙伴和电脑加入',
    '成员令牌和电脑令牌互相独立。',
    `<div class="field"><label for="f-invite-kind">连接类型</label><select name="kind" id="f-invite-kind"><option value="member" ${kind === 'member' ? 'selected' : ''}>伙伴 · 加入共享工作台</option><option value="machine" ${kind === 'machine' ? 'selected' : ''}>电脑 · 提供执行能力</option></select></div><div class="field"><label for="f-invite-name">显示名称</label><input id="f-invite-name" name="name" required maxlength="100" placeholder="伙伴的名字，或这台电脑的名字"></div><div class="notice">中心那台电脑没有公网 IP 时，让所有电脑加入同一个 Tailscale 网络。执行端主动连接中心，不需要开放路由器端口。</div>${modalActions('生成专属令牌')}`,
    async (form) => {
      const invite = await post('/api/invites', { name: form.get('name').trim(), kind: form.get('kind') });
      renderInviteResult(invite);
    },
  );
}
async function loadMemories(showError = false) {
  if (!project()) {
    memories = [];
    renderMemories(memories, memoryScope);
    return;
  }
  const id = currentProject,
    scope = memoryScope,
    serial = ++memoryLoadSerial;
  if (demo) {
    memories =
      scope === 'project'
        ? [
            {
              id: 'demo-memory',
              scope: 'project',
              category: 'constraint',
              content: '作品必须同时适合电脑和手机；3D 场景保留 Blender 源文件。',
              status: 'active',
              revision: 1,
              created: Date.now() / 1000,
              updated: Date.now() / 1000,
            },
            {
              id: 'demo-memory2',
              scope: 'project',
              category: 'preference',
              content: '常规网页工作优先分给 Qoder，复杂验收安排独立成员。',
              status: 'proposed',
              revision: 1,
              created: Date.now() / 1000,
              updated: Date.now() / 1000,
            },
          ]
        : [];
    renderMemories(memories, memoryScope);
    return;
  }
  try {
    const result = await api(`/api/projects/${encodeURIComponent(id)}/memories?scope=${scope}`);
    if (serial !== memoryLoadSerial || id !== currentProject || scope !== memoryScope) return;
    memories = result.memories || [];
    renderMemories(memories, memoryScope);
  } catch (error) {
    if (showError) throw error;
  }
}
function openMemoryEditor(id) {
  if (!editable() || !requireProject()) return;
  const existing = memories.find((item) => item.id === id),
    p = project(),
    scope = existing?.scope || memoryScope;
  openModal(
    existing ? '编辑记忆' : '添加记忆',
    scope === 'project' ? '保存到项目共识，团队成员可见。' : '保存到你的个人记忆，仅你可见。',
    `<div class="field"><label for="f-memory-category">类型</label><select id="f-memory-category" name="category">${Object.entries(
      { preference: '偏好', decision: '已定决策', context: '背景', constraint: '约束' },
    )
      .map(
        ([key, label]) =>
          `<option value="${key}" ${existing?.category === key ? 'selected' : ''}>${label}</option>`,
      )
      .join(
        '',
      )}</select></div><div class="field"><label for="f-memory-content">要记住什么</label><textarea name="content" id="f-memory-content" rows="6" required maxlength="8000" placeholder="写清楚适用范围，例如这个项目的前端采用哪种技术。">${e(existing?.content || '')}</textarea></div><div class="field"><label for="f-memory-status">状态</label><select name="status" id="f-memory-status"><option value="active" ${existing?.status === 'active' ? 'selected' : ''}>已确认，启用</option><option value="proposed" ${existing?.status === 'proposed' ? 'selected' : ''}>待确认</option>${existing ? `<option value="disabled" ${existing.status === 'disabled' ? 'selected' : ''}>停用</option>` : ''}</select></div>${modalActions('保存记忆')}`,
    async (form) => {
      const body = {
        category: form.get('category'),
        content: form.get('content').trim(),
        status: form.get('status'),
      };
      if (existing)
        await api(`/api/memories/${encodeURIComponent(existing.id)}`, {
          method: 'PUT',
          body: JSON.stringify({ ...body, expected_revision: existing.revision }),
        });
      else await post(`/api/projects/${encodeURIComponent(p.id)}/memories`, { ...body, scope });
      closeModal();
      await loadMemories(true);
      toast('记忆已保存。');
    },
  );
}
async function openTeam() {
  if (!editable()) return;
  const result = await api('/api/identities');
  teamIdentities = result.identities || [];
  openModal(
    '伙伴与电脑',
    '管理团队入口和每台电脑的独立凭据。',
    `<div class="setup-actions"><button class="button primary" type="button" data-action="invite">邀请伙伴</button><button class="button" type="button" data-action="invite-machine">连接执行电脑</button></div><div class="identity-list">${teamIdentities.map((item) => `<div class="identity-row"><span class="agent-icon">${icon(item.kind === 'machine' ? 'monitor' : 'users')}</span><span class="identity-main"><strong>${e(item.name)} ${item.is_self ? '<span class="pill">你</span>' : ''}${item.revoked ? '<span class="pill status-cancelled">已撤销</span>' : ''}</strong><small>${item.kind === 'machine' ? '电脑凭据' : '团队成员'} · ${dateLabel(item.created)}</small></span><span class="identity-actions">${item.is_self || item.kind === 'machine' ? `<button class="button small" type="button" data-action="rotate-identity" data-id="${e(item.id)}">重置令牌</button>` : ''}${!item.revoked ? `<button class="icon-button" type="button" data-action="revoke-identity" data-id="${e(item.id)}" title="撤销访问" aria-label="撤销 ${e(item.name)} 的访问">${icon('trash')}</button>` : ''}</span></div>`).join('')}</div><div class="notice">团队成员共同管理这个空间。撤销访问会使对应令牌与登录会话失效；重置电脑令牌后，需要在该电脑更新环境变量并重启执行端。</div><div class="modal-actions"><button class="button" type="button" data-action="close-modal">关闭</button></div>`,
  );
}
function openSetupHelp() {
  openModal(
    '连接前，先跑通这四步',
    '先连中心，再连电脑，最后验证实际任务。',
    `<ol class="setup-steps"><li><strong>在一台常开的电脑上启动中心</strong><p>在安装好的环境运行：</p><pre>pal-arena serve --share</pre><p>首次访问用数据目录中的 <code>owner-token.txt</code> 登录。服务只监听本机；<code>--share</code> 会通过 Tailscale 给其他电脑一个 HTTPS 地址。</p></li><li><strong>给伙伴和其他电脑一个可访问的地址</strong><p>没有公网 IP 也可以。每台电脑装好 Tailscale 并登录后，共享成功的地址会显示在「电脑与成员」顶部，连接电脑时自动填入。</p><p><button class="text-link" type="button" data-action="network-help">不会组网？看一步一步的说明</button></p></li><li><strong>连接每台执行电脑</strong><p>在「电脑与成员」生成电脑专属令牌，选择本机客户端并下载配置。Mac 与 Windows 都在安装 PAL 的 Python 环境运行：</p><pre>pal-arena doctor --config worker.json\npal-arena-worker --config worker.json</pre><p>令牌放在 <code>ARENA_MACHINE_TOKEN</code> 环境变量中。自检通过不代表订阅额度或账号权限已通过实测。</p></li><li><strong>完成一次真实小任务</strong><p>先给负责人发一个只读检查任务，再在临时项目中写一个测试文件。核对任务结果、实际文件和独立验收；然后才扩大并发。</p></li></ol><div class="notice">Codex、Claude、Gemini 桌面应用不等于已安装可用的 CLI。Qoder 当前通过 ACP 新建会话；Antigravity 可连接桌面后端。GLM 等模型由你正在使用的客户端承载，填写对应客户端的模型 ID。模型名称和可用额度以各电脑实际情况为准。</div><div class="modal-actions"><button class="button primary" type="button" data-action="invite-machine">连接一台电脑</button><button class="button" type="button" data-action="close-modal">关闭</button></div>`,
  );
}
function renderInviteResult(invite) {
  const machine = invite.kind === 'machine';
  openModal(
    machine ? '连接执行电脑' : '邀请伙伴加入',
    `${invite.name} · 令牌仅在这里显示一次`,
    `<p class="muted small">${machine ? '将令牌设置为执行电脑上的 ARENA_MACHINE_TOKEN 环境变量。不要放进项目仓库或聊天记录。' : '把专属令牌交给伙伴，用工作台登录页进入。'}</p><div class="token-box" id="invite-token"></div><div class="modal-actions"><button type="button" class="button" data-action="copy-token">${icon('copy')} 复制令牌</button></div>${machine ? machineSetupHTML(invite, networkAddress) : ''}<div class="modal-actions"><button class="button primary" type="button" data-action="close-modal">我已保存</button></div>`,
  );
  dialogSecret = { ...invite };
  $('#invite-token').textContent = invite.token;
}
function downloadConfig() {
  try {
    const config = collectWorkerConfig(dialogSecret?.name);
    downloadBlob('worker.json', JSON.stringify(config, null, 2), 'application/json');
    showWorkerCommands(dialogSecret?.name);
    toast('配置已下载。请在目标电脑自检并启动执行端。');
  } catch (error) {
    $('#modal-error').textContent = error.message;
  }
}
function downloadBlob(name, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function uploadFile() {
  if (!editable() || !requireProject()) return;
  const input = document.createElement('input');
  input.type = 'file';
  input.addEventListener('change', async () => {
    const file = input.files?.[0];
    if (!file) return;
    if (file.size > 128 * 1024 * 1024) {
      toast('单个成果文件最多 128 MiB。', true);
      return;
    }
    toast('正在上传成果…');
    try {
      await api(
        `/api/projects/${encodeURIComponent(currentProject)}/artifacts?name=${encodeURIComponent(file.name)}`,
        { method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' } },
      );
      await refresh(true);
      toast('成果已保存并保留。');
    } catch (error) {
      toast(error.message, true);
    }
  });
  input.click();
}

async function previewFile(id) {
  if (!editable()) return;
  const file = state.artifacts.find((item) => item.id === id);
  if (!file) return;
  const requestSerial = ++artifactPreviewSerial,
    generation = modalGeneration;
  const extension = file.name.split('.').pop().toLowerCase();
  const imageTypes = {
    png: 'image/png',
    jpg: 'image/jpeg',
    jpeg: 'image/jpeg',
    gif: 'image/gif',
    webp: 'image/webp',
    avif: 'image/avif',
    bmp: 'image/bmp',
  };
  const isImage = Object.hasOwn(imageTypes, extension),
    limit = isImage ? 20 * 1024 * 1024 : 2 * 1024 * 1024;
  if (file.size > limit) {
    toast(
      isImage ? '图片预览最多 20 MiB，请下载查看完整文件。' : '在线文本预览最多 2 MiB，请下载查看完整文件。',
    );
    return;
  }
  const response = await fetch(`/api/artifacts/${encodeURIComponent(id)}/download`, {
    credentials: 'same-origin',
  });
  if (!response.ok) throw new Error('无法读取文件，请刷新或重新登录。');
  const body = await response.blob();
  if (requestSerial !== artifactPreviewSerial || generation !== modalGeneration) return;
  if (body.size > limit) throw new Error('文件超过预览大小限制，请下载查看。');
  const isHTML = /\.html?$/i.test(file.name);
  openModal(
    file.name,
    isImage ? '本地成果图片预览' : '文件预览 · 不执行脚本、不连接外部资源',
    `${isImage ? '<div class="artifact-image-box"><img id="artifact-image-preview" alt=""></div><p id="artifact-image-error" class="form-error" hidden>图片无法解码，请下载原文件检查格式。</p>' : '<pre id="artifact-text-preview"></pre>'}${isHTML ? '<div class="detail-section"><h3>页面预览</h3><iframe id="artifact-html-preview" class="artifact-frame" title="成果页面预览" sandbox="" referrerpolicy="no-referrer"></iframe></div>' : ''}<div class="modal-actions"><button type="button" class="button" data-action="download-file" data-id="${e(file.id)}">下载原文件</button><button type="button" class="button primary" data-action="close-modal">关闭</button></div>`,
  );
  if (isImage) {
    const objectURL = URL.createObjectURL(new Blob([body], { type: imageTypes[extension] }));
    modalObjectUrls.push(objectURL);
    const image = $('#artifact-image-preview');
    image.alt = file.name;
    const errorNode = $('#artifact-image-error');
    image.addEventListener('error', () => {
      if (errorNode?.isConnected) errorNode.hidden = false;
    });
    image.src = objectURL;
  } else {
    const text = await body.text();
    if (!$('#artifact-text-preview')) return;
    $('#artifact-text-preview').textContent = text;
    const frame = $('#artifact-html-preview');
    if (frame)
      frame.srcdoc = `<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; form-action 'none'; base-uri 'none'">${text}`;
  }
}

function confirmAction(title, description, label, action) {
  openModal(
    title,
    '',
    `<p class="muted" style="font-size:var(--text-sm);line-height:1.9">${e(description)}</p>${modalActions(label)}`,
    async () => {
      await action();
      closeModal();
      await refresh(true);
    },
  );
}

const { openProject, openRequirements, openProjectSettings } = createProjectDialogs({
  api,
  post,
  project,
  openModal,
  closeModal,
  modalActions,
  editable,
  requireProject,
  navigate,
  refresh,
  toast,
  isDemo: () => demo,
  getState: () => state,
  onCreated: (id) => {
    currentProject = id;
    messageSignature = '';
  },
});

const { openTaskForm, updateTaskAgentOptions, openTask } = createTaskDialogs({
  getState: () => state,
  project,
  editable,
  requireProject,
  openModal,
  closeModal,
  modalActions,
  idempotentPost,
  navigate,
  refresh,
  toast,
  agentName,
  trackTask: (task) => {
    modalTaskId = task.id;
    modalTaskSignature = JSON.stringify(task);
    writeRoute();
  },
});

$('#login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('button', event.target);
  button.disabled = true;
  $('#login-error').textContent = '';
  try {
    identity = await post('/api/login', { token: $('#login-token').value.trim() });
    $('#login-token').value = '';
    showApp();
    await refresh(true);
  } catch (error) {
    $('#login-error').textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
$('#message-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (messageSending || !editable() || !requireProject()) return;
  const input = $('#message-input'),
    content = input.value.trim();
  if (!content) return;
  const button = $('button', event.target);
  messageSending = true;
  button.disabled = true;
  const sentProject = currentProject,
    sentChannel = channel;
  try {
    await idempotentPost(`/api/projects/${encodeURIComponent(sentProject)}/messages`, {
      content,
      channel: sentChannel,
    });
    if (currentProject === sentProject && channel === sentChannel && input.value.trim() === content) {
      input.value = '';
      fitComposer();
    }
    if ((unreadDrafts.get(`${sentProject}:${sentChannel}`) || '').trim() === content)
      unreadDrafts.delete(`${sentProject}:${sentChannel}`);
    await refresh(true);
    $('#message-list').scrollTop = $('#message-list').scrollHeight;
  } catch (error) {
    toast(error.message, true);
  } finally {
    messageSending = false;
    button.disabled = demo || !project();
  }
});
$('#message-input').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $('#message-form').requestSubmit();
  }
});
$('#message-input').addEventListener('input', () => {
  unreadDrafts.set(`${currentProject}:${channel}`, $('#message-input').value);
  fitComposer();
});
$('#modal').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!modalAction) return;
  const button = $('button[type="submit"]', event.target);
  if (button) button.disabled = true;
  $('#modal-error').textContent = '';
  try {
    await modalAction(new FormData(event.target));
  } catch (error) {
    if ($('#modal-error')) $('#modal-error').textContent = error.message;
    else toast(error.message, true);
  } finally {
    if (button?.isConnected) button.disabled = false;
  }
});
$('#modal').addEventListener('close', () => {
  dialogSecret = null;
  modalAction = null;
  modalTaskId = null;
  modalTaskSignature = '';
  releaseModalURLs();
  writeRoute();
});
$('#modal').addEventListener('click', (event) => {
  if (event.target === $('#modal')) {
    const bounds = $('#modal').getBoundingClientRect();
    if (
      event.clientX < bounds.left ||
      event.clientX > bounds.right ||
      event.clientY < bounds.top ||
      event.clientY > bounds.bottom
    )
      closeModal();
  }
});
document.addEventListener('change', (event) => {
  if (['auto_merge', 'auto_deploy'].includes(event.target.name) && event.target.checked) {
    const form = event.target.closest('form');
    form.querySelector('[name=push_branches]').checked = true;
    if (event.target.name === 'auto_deploy') form.querySelector('[name=auto_merge]').checked = true;
  }
  if (event.target.name === 'push_branches' && !event.target.checked) {
    const form = event.target.closest('form');
    for (const name of ['auto_merge', 'auto_deploy']) form.querySelector(`[name=${name}]`).checked = false;
  }

  if (event.target.id === 'theme-select') {
    setTheme(event.target.value);
    return;
  }
  if (['f-kind', 'f-capability'].includes(event.target.id)) {
    updateTaskAgentOptions();
    return;
  }
  const select = event.target.closest('[data-provider-row]');
  if (!select) return;
  const note = $(`[data-provider-note="${select.dataset.providerRow}"]`);
  if (note) note.textContent = providerInfo[select.value].note;
  if (select.value === 'desktop_mcp') {
    $('#setup-slots').value = '1';
    const root = $('#setup-root');
    if (root.value === '~/.pal-arena/workspaces') root.value = '~/.pal-arena/workbuddy-workspaces';
    const role = select.closest('[data-agent-row]').querySelector('[name^="role-"]');
    if (role) role.value = 'worker';
  }
});
const palette = createPalette(paletteItems);
const goKeys = { c: 'chat', t: 'tasks', r: 'review', f: 'files', m: 'machines', y: 'memory', l: 'logs' };
let goPending = 0;
document.addEventListener('keydown', (event) => {
  if (!identity) return;
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    if (palette.isOpen()) palette.close();
    else palette.open();
    return;
  }
  const typing = event.target.closest?.('input, textarea, select, [contenteditable="true"]');
  if (typing || event.metaKey || event.ctrlKey || event.altKey || $('#modal').open || palette.isOpen())
    return;
  const key = event.key.toLowerCase();
  if (goPending && Date.now() - goPending < 1200 && goKeys[key]) {
    event.preventDefault();
    goPending = 0;
    navigate(goKeys[key]);
  } else if (key === 'g') goPending = Date.now();
  else if (key === '/') {
    event.preventDefault();
    navigate('chat');
    $('#message-input').focus();
  } else if (key === 'n') {
    event.preventDefault();
    openTaskForm();
  } else goPending = 0;
});
function paletteItems() {
  const pages = [
    ['chat', '对话', 'chat', 'G C'],
    ['tasks', '任务看板', 'board', 'G T'],
    ['review', '验收', 'check', 'G R'],
    ['files', '成果', 'folder', 'G F'],
    ['machines', '电脑与成员', 'monitor', 'G M'],
    ['memory', '记忆', 'spark', 'G Y'],
    ['logs', '运行记录', 'terminal', 'G L'],
  ].map(([view, label, iconName, hint]) => ({
    group: '页面',
    label,
    hint,
    icon: iconName,
    keywords: view,
    run: () => navigate(view),
  }));
  const action = (label, iconName, hint, run) => ({ group: '操作', label, hint, icon: iconName, run });
  const quick = [
    action('新建任务', 'plus', 'N', () => openTaskForm()),
    action('给负责人发消息', 'chat', '/', () => {
      navigate('chat');
      $('#message-input').focus();
    }),
  ].map((item) => ({ ...item, group: '常用' }));
  const more = [
    action('新建项目', 'plus', '', () => openProject()),
    action('编辑需求清单', 'list', '', () => openRequirements()),
    action('项目设置', 'settings', '', () => openProjectSettings()),
    action('连接一台电脑', 'monitor', '', () => openInvite('machine')),
    action('邀请伙伴', 'users', '', () => openInvite('member')),
    action('伙伴与令牌', 'users', '', () => openTeam()),
    action(channel === 'shared' ? '切到私聊负责人' : '切到团队频道', 'chat', '', () =>
      switchChannel(channel === 'shared' ? 'personal' : 'shared'),
    ),
    action('任务需要我时发桌面提醒', 'bell', notificationLabel(), () => enableNotifications()),
    action('浅色主题', 'sun', '', () => setTheme('light')),
    action('深色主题', 'moon', '', () => setTheme('dark')),
    action('主题跟随系统', 'monitor', '', () => setTheme('system')),
    action('刷新', 'refresh', '', () => refresh(true)),
    action('接入指南', 'list', '', () => openSetupHelp()),
    action('让其他电脑和伙伴连上（组网）', 'monitor', '', () => openNetworkHelp()),
    action('退出登录', 'logout', '', () => $('[data-action="logout"]').click()),
  ];
  const projects = state.projects.map((item) => ({
    group: '项目',
    label: item.name,
    hint: item.id === currentProject ? '当前' : '',
    icon: 'grid',
    run: () => selectProject(item.id),
  }));
  const tasks = state.tasks
    .filter((task) => task.status !== 'cancelled')
    .map((task) => ({
      group: '任务',
      label: task.title,
      hint: taskHint(task),
      icon: task.capability === 'blender' ? 'cube' : 'branch',
      keywords: task.id,
      run: () => openTask(task.id),
    }));
  return [...quick, ...pages, ...tasks, ...projects, ...more];
}
async function selectProject(id) {
  currentProject = id;
  messageSignature = '';
  $('#message-input').value = unreadDrafts.get(`${currentProject}:${channel}`) || '';
  closeSidebar();
  writeRoute();
  await refresh(true);
}
async function switchChannel(next) {
  channel = next;
  messageSignature = '';
  $('#message-input').value = unreadDrafts.get(`${currentProject}:${channel}`) || '';
  await refresh(true);
}

/** Desktop notifications for approvals and failures, only while the tab is in the background. */
let seenAttention = null;
function attentionKeys() {
  return state.tasks
    .filter(
      (task) =>
        (task.status === 'running' && asArray(task.permissions).some((item) => !item.decided)) ||
        ['failed', 'blocked', 'interrupted', 'changes_requested'].includes(task.status),
    )
    .map((task) => [`${task.id}:${task.status}:${task.attempt}`, task]);
}
function notifyAttention() {
  const current = attentionKeys();
  const fresh = seenAttention ? current.filter(([key]) => !seenAttention.has(key)) : [];
  seenAttention = new Set(current.map(([key]) => key));
  if (!fresh.length || !document.hidden || demo || !('Notification' in window)) return;
  if (Notification.permission !== 'granted') return;
  const [, task] = fresh[0];
  const note = new Notification(
    task.status === 'running' ? '有操作等你授权' : `任务${statusLabels[task.status]}`,
    {
      body: fresh.length > 1 ? `${task.title} 等 ${fresh.length} 项` : task.title,
      tag: 'agent-cooperation-attention',
    },
  );
  note.onclick = () => {
    window.focus();
    openTask(task.id);
    note.close();
  };
}
function notificationLabel() {
  if (!('Notification' in window)) return '浏览器不支持';
  return { granted: '已开启', denied: '浏览器已拒绝' }[Notification.permission] || '';
}
async function enableNotifications() {
  if (!('Notification' in window)) {
    toast('这个浏览器不支持桌面提醒。', true);
    return;
  }
  const result = await Notification.requestPermission();
  toast(
    result === 'granted'
      ? '已开启：页面在后台时，有授权请求或任务失败会提醒你。'
      : '浏览器没有允许提醒，可以在网站设置里打开。',
    result !== 'granted',
  );
}

document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const action = button.dataset.action;
  try {
    if (action === 'navigate') navigate(button.dataset.view);
    else if (action === 'menu') toggleSidebar();
    else if (action === 'refresh') await refresh(true);
    else if (action === 'new-project') openProject();
    else if (action === 'new-task') openTaskForm();
    else if (action === 'requirements') openRequirements();
    else if (action === 'project-settings') openProjectSettings();
    else if (action === 'team') await openTeam();
    else if (action === 'setup-help') openSetupHelp();
    else if (action === 'agent') openAgent(button.dataset.id, state, openModal);
    else if (action === 'new-memory') openMemoryEditor();
    else if (action === 'edit-memory') openMemoryEditor(button.dataset.id);
    else if (action === 'memory-scope') {
      memoryScope = button.dataset.scope;
      memories = [];
      renderMemories(memories, memoryScope);
      await loadMemories(true);
    } else if (action === 'memory-status' && editable()) {
      const item = memories.find((value) => value.id === button.dataset.id);
      if (item) {
        await api(`/api/memories/${encodeURIComponent(item.id)}`, {
          method: 'PUT',
          body: JSON.stringify({ status: button.dataset.status, expected_revision: item.revision }),
        });
        await loadMemories(true);
        toast('记忆状态已更新。');
      }
    } else if (action === 'task') openTask(button.dataset.id);
    else if (action === 'close-modal') closeModal();
    else if (action === 'project') await selectProject(button.dataset.id);
    else if (action === 'palette') palette.open();
    else if (action === 'network-help') openNetworkHelp();
    else if (action === 'copy-address') {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(button.dataset.url);
        toast('地址已复制，发给伙伴在浏览器里打开。');
      } else toast(button.dataset.url);
    } else if (action === 'share-network' && editable()) {
      button.disabled = true;
      try {
        networkAddress = await post('/api/setup/share');
      } finally {
        button.disabled = false;
      }
      renderNetwork(networkAddress, { local: onCenter() });
      if ($('#modal').open) closeModal();
      navigate('machines');
      toast(
        networkAddress.state === 'shared' ? `已共享：${networkAddress.url}` : networkAddress.message,
        networkAddress.state !== 'shared',
      );
    } else if (action === 'copy-pre') {
      const text = button.parentElement.querySelector('pre').textContent;
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        button.textContent = '已复制';
        setTimeout(() => {
          if (button.isConnected) button.textContent = '复制';
        }, 1500);
      } else toast('当前连接不支持自动复制，请手动选中复制。');
    } else if (action === 'channel') {
      channel = button.dataset.channel;
      messageSignature = '';
      $('#message-input').value = unreadDrafts.get(`${currentProject}:${channel}`) || '';
      await refresh(true);
    } else if (action === 'share-message') {
      const message = state.messages.find((item) => item.id === button.dataset.id);
      if (!message) return;
      channel = 'shared';
      messageSignature = '';
      $('#message-input').value = message.content;
      unreadDrafts.set(`${currentProject}:${channel}`, message.content);
      await refresh(true);
      $('#message-input').focus();
      toast('已放入团队输入框，你可以编辑后发送。');
    } else if (action === 'invite' || action === 'invite-machine')
      openInvite(action === 'invite-machine' ? 'machine' : 'member');
    else if (action === 'copy-token') {
      if (!dialogSecret) return;
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(dialogSecret.token);
        toast('令牌已复制。');
      } else toast('当前连接不支持自动复制，请手动选中令牌复制。');
    } else if (action === 'download-config') downloadConfig();
    else if (action === 'show-worker-commands') showWorkerCommands(dialogSecret?.name);
    else if (action === 'copy-commands') {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText($('#worker-command-text').textContent);
        toast('启动命令已复制，令牌仍需自行替换。');
      } else toast('请手动选择并复制上方命令。');
    } else if (action === 'add-config-agent') {
      const indexes = $$('[data-agent-row]').map((row) => Number(row.dataset.agentRow));
      $('#setup-agents').insertAdjacentHTML(
        'beforeend',
        setupAgentRow(indexes.length ? Math.max(...indexes) + 1 : 0),
      );
    } else if (action === 'remove-config-agent') button.closest('[data-agent-row]').remove();
    else if (action === 'rotate-identity' && editable()) {
      const item = teamIdentities.find((value) => value.id === button.dataset.id);
      if (!item) return;
      confirmAction(
        '重置访问令牌',
        `重置 ${item.name} 的令牌后，旧令牌立即失效。请保存新令牌并更新对应电脑的环境变量。`,
        '重置令牌',
        async () => {
          const next = await post(`/api/identities/${encodeURIComponent(item.id)}/rotate`);
          setTimeout(() => renderInviteResult({ ...item, ...next }), 0);
        },
      );
    } else if (action === 'revoke-identity' && editable()) {
      const item = teamIdentities.find((value) => value.id === button.dataset.id);
      if (!item) return;
      confirmAction(
        '撤销访问',
        `撤销 ${item.name} 的令牌与登录会话。电脑执行中的操作仍需在本机确认是否停止。`,
        '撤销访问',
        async () => {
          await api(`/api/identities/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
          if (item.is_self) showLogin();
        },
      );
    } else if (action === 'logout') {
      if (demo) {
        location.href = '/';
        return;
      }
      await post('/api/logout');
      state = emptySnapshot();
      currentProject = null;
      messageSignature = '';
      unreadDrafts.clear();
      showLogin();
    } else if (action === 'retry-task' && editable()) {
      const id = button.dataset.id;
      confirmAction(
        '重新执行任务',
        '请先检查原运行留下的文件和操作，确认可以再次执行。中断任务不会被自动重复提交。',
        '确认重试',
        () => post(`/api/tasks/${encodeURIComponent(id)}/retry`),
      );
    } else if (action === 'cancel-task' && editable()) {
      const id = button.dataset.id;
      confirmAction(
        '停止任务',
        '停止后执行端将停止跟进。桌面应用中已经提交的动作可能仍需在对应应用确认状态。',
        '停止任务',
        () => post(`/api/tasks/${encodeURIComponent(id)}/cancel`),
      );
    } else if (action === 'permission' && editable()) {
      await post(
        `/api/tasks/${encodeURIComponent(button.dataset.task)}/permissions/${encodeURIComponent(button.dataset.permission)}`,
        { option_id: button.dataset.option || null },
      );
      await refresh(true);
      if (modalTaskId === button.dataset.task) openTask(button.dataset.task);
      toast('已提交操作决定。');
    } else if (action === 'upload') await uploadFile();
    else if (action === 'preview-file') await previewFile(button.dataset.id);
    else if (action === 'download-file') {
      if (!editable()) return;
      const file = state.artifacts.find((item) => item.id === button.dataset.id);
      if (file) location.assign(`/api/artifacts/${encodeURIComponent(file.id)}/download`);
    } else if (action === 'pin-file' && editable()) {
      const file = state.artifacts.find((item) => item.id === button.dataset.id);
      if (file) {
        await post(`/api/artifacts/${encodeURIComponent(file.id)}/pin`, { pinned: !file.pinned });
        await refresh(true);
      }
    } else if (action === 'delete-file' && editable()) {
      const file = state.artifacts.find((item) => item.id === button.dataset.id);
      if (file)
        confirmAction(
          '删除暂存成果',
          `删除 ${file.name} 后将无法从本机恢复。请确认已有备份。`,
          '删除文件',
          () => api(`/api/artifacts/${encodeURIComponent(file.id)}`, { method: 'DELETE' }),
        );
    } else if (action === 'cleanup' && editable() && requireProject())
      confirmAction(
        '清理较旧成果',
        '保留最近 3 份成果，只清理更旧且已解除保留的文件；活动任务和已保留文件不会删除。',
        '开始清理',
        () => post('/api/artifacts/cleanup', { project_id: currentProject, keep: 3, channel }),
      );
  } catch (error) {
    toast(error.message, true);
  }
});

(async () => {
  hydrateIcons();
  restoreTheme();
  initializeNavigation();
  if (location.protocol === 'file:') {
    $('#demo-banner a').hidden = true;
    $$('a[href="/"]').forEach((link) => (link.href = '#'));
  }
  const route = readRoute();
  if (demo) {
    identity = { id: 'demo', name: '你', kind: 'member' };
    showApp();
    await refresh();
  } else {
    try {
      identity = await api('/api/me');
      showApp();
      await refresh(true);
    } catch {
      showLogin();
    }
  }
  if (identity) {
    if (route.view) navigate(route.view);
    if (route.taskId && state.tasks.some((task) => task.id === route.taskId)) openTask(route.taskId);
  }
  let lastPoll = 0;
  setInterval(() => {
    if (!identity || demo) return;
    // Foreground every 4 s; background every 20 s, enough for approval reminders.
    if (document.hidden && Date.now() - lastPoll < 20000) return;
    lastPoll = Date.now();
    refresh();
  }, 4000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && identity && !demo) refresh();
  });
})();

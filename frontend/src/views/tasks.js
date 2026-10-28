import {
  $,
  e,
  status,
  roleLabels,
  capabilityLabels,
  size,
  dateLabel,
  asArray,
  lines,
} from '../components/ui.js';
import { renderTaskTimeline, taskTimelineEvents, taskDialogSignature } from './task-timeline.js';

export function createTaskDialogs({
  getState,
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
  trackTask,
  api,
  demo = false,
}) {
  let syncTimeline = () => {};
  function openTaskForm() {
    const state = getState();
    if (!editable() || !requireProject()) return;
    const p = project();
    openModal(
      '创建任务',
      '说明目标、依赖和可验收的结果。',
      `<div class="field"><label for="f-title">任务名称</label><input id="f-title" name="title" maxlength="200" required></div><div class="field"><label for="f-prompt">任务说明</label><textarea id="f-prompt" name="prompt" rows="4" required></textarea></div><div class="form-row"><div class="field"><label for="f-kind">任务角色</label><select id="f-kind" name="kind"><option value="worker">执行成员</option><option value="leader">负责人</option></select></div><div class="field"><label for="f-capability">主要能力</label><select id="f-capability" name="capability">${Object.entries(
        capabilityLabels,
      )
        .map(([value, label]) => `<option value="${value}">${label}</option>`)
        .join(
          '',
        )}</select></div></div><div class="field"><label for="f-difficulty">任务难度</label><select id="f-difficulty" name="difficulty"><option value="routine">常规 · 按模板或明确步骤执行</option><option value="standard" selected>标准 · 需要分析与实现</option><option value="deep">深入 · 架构、复杂推理或重点验收</option></select></div><div class="field"><label for="f-agent">成员</label><select id="f-agent" name="requested_agent"><option value="">按能力与偏好自动分派</option>${state.agents
        .filter((agent) => agent.enabled)
        .map(
          (agent) =>
            `<option value="${e(agent.id)}">${e(agent.name)} · ${e(agent.model || agent.provider)}</option>`,
        )
        .join(
          '',
        )}</select></div><div class="field"><label for="f-context-size">估计上下文规模（tokens，可选）</label><input id="f-context-size" name="context_tokens" type="number" min="0" max="1000000" value="0"><small>0 表示未指定；已知规模时用于避开容量不足的成员。</small></div><div class="field"><label for="f-deps">等待这些任务完成（按住 Ctrl / ⌘ 多选）</label><select id="f-deps" name="dependencies" multiple size="3">${state.tasks
        .filter((task) => task.kind === 'worker')
        .map((task) => `<option value="${e(task.id)}">${e(task.title)}</option>`)
        .join(
          '',
        )}</select></div><div class="field"><label for="f-task-acceptance">验收条件 · 每行一项</label><textarea id="f-task-acceptance" name="acceptance" rows="3">${e(asArray(p.requirements).join('\n'))}</textarea></div>${modalActions('加入任务队列')}`,
      async (form) => {
        await idempotentPost(`/api/projects/${encodeURIComponent(p.id)}/tasks`, {
          title: form.get('title').trim(),
          prompt: form.get('prompt').trim(),
          kind: form.get('kind'),
          difficulty: form.get('difficulty'),
          context_tokens: Number(form.get('context_tokens') || 0),
          capability: form.get('capability'),
          requested_agent: form.get('requested_agent') || null,
          dependencies: form.getAll('dependencies'),
          acceptance: lines(form.get('acceptance')),
        });
        closeModal();
        navigate('tasks');
        await refresh(true);
        toast('任务已加入队列。');
      },
    );
    updateTaskAgentOptions();
  }

  function updateTaskAgentOptions() {
    const state = getState();
    const select = $('#f-agent');
    if (!select) return;
    const kind = $('#f-kind').value,
      capability = $('#f-capability').value,
      previous = select.value;
    const candidates = state.agents.filter(
      (agent) =>
        agent.enabled &&
        asArray(agent.roles).includes(kind) &&
        (kind !== 'worker' || asArray(agent.capabilities).includes(capability)) &&
        agent.readiness?.dispatchable !== false,
    );
    select.innerHTML =
      '<option value="">按能力与偏好自动分派</option>' +
      candidates
        .map(
          (agent) =>
            `<option value="${e(agent.id)}">${e(agent.name)} · ${e(agent.model || agent.provider)}${state.machines.some((machine) => machine.id === agent.machine_id && machine.online) ? '' : ' · 电脑离线'}</option>`,
        )
        .join('');
    if (candidates.some((agent) => agent.id === previous)) select.value = previous;
  }

  function openTask(taskId) {
    const state = getState();
    const task = state.tasks.find((item) => item.id === taskId);
    if (!task) return;
    const permissions = asArray(task.permissions).filter((item) => !item.decided);
    const artifactRows = state.artifacts.filter((item) => item.task_id === task.id);
    openModal(
      task.title,
      `${roleLabels[task.kind] || task.kind} · ${task.id.slice(0, 10)}`,
      `<div class="task-detail-title">${status(task.status)}<span class="pill">需求 v${task.revision}</span><span class="pill">${e({ routine: '常规任务', standard: '标准任务', deep: '深入任务' }[task.difficulty] || '标准任务')}</span><span class="pill">第 ${task.attempt || 0} 次运行</span></div><div class="detail-meta"><div>执行成员<strong>${e(agentName(task.agent_id))}</strong></div><div>主要能力<strong>${e(capabilityLabels[task.capability] || task.capability)}</strong></div><div>创建时间<strong>${dateLabel(task.created)}</strong></div><div>依赖任务<strong>${task.dependencies?.length ? task.dependencies.map((id) => e(state.tasks.find((t) => t.id === id)?.title || id)).join('、') : '无前置依赖'}</strong></div></div>${task.routing_reason ? `<div class="detail-section"><h3>为什么选择这个成员</h3><p>${e(task.routing_reason)}</p></div>` : ''}${asArray(task.memory_snapshot).length ? `<details class="detail-section"><summary>本次任务采用的记忆 · ${task.memory_snapshot.length} 条</summary>${task.memory_snapshot.map((memory) => `<p>${e(memory.content)} <span class="muted small">v${e(memory.revision)}</span></p>`).join('')}</details>` : ''}<div class="detail-section"><h3>任务说明</h3><p>${e(task.prompt)}</p></div><div class="detail-section"><h3>验收条件</h3><ul class="acceptance-list">${
        asArray(task.acceptance)
          .map((item) => `<li>${e(item)}</li>`)
          .join('') || '<li>尚未设置</li>'
      }</ul></div>${task.result ? `<div class="detail-section"><h3>运行结果</h3><p>${e(task.result)}</p></div>` : ''}${task.evidence && Object.keys(task.evidence).length ? `<div class="detail-section"><h3>代码、文件和验证证据</h3>${evidenceLinks(task)}<pre>${e(JSON.stringify(task.evidence, null, 2))}</pre></div>` : ''}${task.review ? `<div class="detail-section"><h3>独立验收</h3><p>${e(task.review.summary)}</p><pre>${e(JSON.stringify(task.review.evidence || [], null, 2))}</pre></div>` : ''}${
        permissions.length && task.status === 'running'
          ? `<div class="detail-section"><h3>成员正在等待操作授权</h3>${permissions
              .map(
                (permission) =>
                  `<div class="review-card"><p>${e(permission.tool?.title || permission.tool?.name || '待确认的工具操作')}</p><pre>${e(JSON.stringify(permission.tool || {}, null, 2))}</pre><div class="modal-actions">${asArray(
                    permission.options,
                  )
                    .map(
                      (option) =>
                        `<button class="button small" type="button" data-action="permission" data-task="${e(task.id)}" data-permission="${e(permission.id)}" data-option="${e(option.optionId || option.id)}">${e(option.name || option.label || option.optionId)}</button>`,
                    )
                    .join(
                      '',
                    )}<button class="button small" type="button" data-action="permission" data-task="${e(task.id)}" data-permission="${e(permission.id)}" data-option="">取消请求</button></div></div>`,
              )
              .join('')}</div>`
          : ''
      }${artifactRows.length ? `<div class="detail-section"><h3>关联成果</h3>${artifactRows.map((file) => `<p>${e(file.name)} · ${size(file.size)}</p>`).join('')}</div>` : ''}<details class="detail-section task-history" id="task-timeline"><summary>执行与验收记录</summary><div class="task-history-content"><p class="muted small">展开后加载运行记录。</p></div></details>${recoveryNote(task)}<div class="modal-actions">${['failed', 'blocked', 'interrupted', 'changes_requested', 'cancelled'].includes(task.status) ? `<button class="button primary" type="button" data-action="retry-task" data-id="${e(task.id)}">检查后重试</button>` : ''}${['queued', 'running', 'in_review', 'coordinating'].includes(task.status) ? `<button class="button danger" type="button" data-action="cancel-task" data-id="${e(task.id)}">停止这个任务</button>` : ''}<button class="button" type="button" data-action="close-modal">关闭</button></div>`,
    );
    trackTask(task);
    bindTimeline(task, state);
  }

  function bindTimeline(task, state) {
    const details = $('#task-timeline');
    const content = $('.task-history-content', details);
    let busy = false,
      loaded = false,
      events = [],
      nextBefore = null,
      loadedSignature = '';
    syncTimeline = () => {
      if (!loaded || !content.isConnected) return;
      const freshState = getState();
      const freshTask = freshState.tasks.find((item) => item.id === task.id);
      const button = $('.timeline-new', content);
      if (button && freshTask) button.hidden = taskDialogSignature(freshState, freshTask) === loadedSignature;
    };
    async function load(before = null) {
      if (busy || !content.isConnected) return;
      busy = true;
      const previousButton = before
        ? $('.timeline-more', content)
        : $('.timeline-new, .timeline-retry', content);
      if (previousButton) previousButton.disabled = true;
      else content.innerHTML = '<p class="muted small">正在加载运行记录…</p>';
      try {
        const freshState = getState();
        const freshTask = freshState.tasks.find((item) => item.id === task.id) || task;
        const signature = taskDialogSignature(freshState, freshTask);
        const query = new URLSearchParams();
        if (before) query.set('before', before);
        const result = demo
          ? {
              events: taskTimelineEvents(state, task).sort((a, b) => a.created - b.created),
              next_before: null,
            }
          : await api(`/api/tasks/${encodeURIComponent(task.id)}/timeline?${query}`);
        // A closed or replaced dialog must never receive an old task's response.
        if (!content.isConnected) return;
        events = before ? [...result.events, ...events] : result.events;
        nextBefore = result.next_before;
        loaded = true;
        if (!before) loadedSignature = signature;
        content.innerHTML = `<button class="button small timeline-new" type="button" hidden>有新记录，刷新</button>${nextBefore ? '<button class="button small timeline-more" type="button">加载较早记录</button>' : ''}${renderTaskTimeline(events)}<p class="form-error timeline-error" role="alert"></p>`;
        $('.timeline-more', content)?.addEventListener('click', () => load(nextBefore));
        $('.timeline-new', content).addEventListener('click', () => load());
        syncTimeline();
      } catch (error) {
        if (!content.isConnected) return;
        if (previousButton) {
          previousButton.disabled = false;
          $('.timeline-error', content).textContent = error.message;
        } else {
          content.innerHTML = `<p class="form-error timeline-error" role="alert">${e(error.message)}</p><button class="button small timeline-retry" type="button">重新加载</button>`;
          $('button', content).addEventListener('click', () => load());
        }
      } finally {
        busy = false;
      }
    }
    details.addEventListener('toggle', () => {
      if (details.open && !loaded) load();
    });
  }

  function evidenceLinks(task) {
    const repo = (project()?.repo || '').replace(/\.git$/, '');
    if (!/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo)) return '';
    const evidence = task.evidence || {},
      links = [];
    if (evidence.branch && evidence.delivery === 'github')
      links.push(
        `<a class="button small" target="_blank" rel="noopener noreferrer" href="${e(repo + '/tree/' + encodeURIComponent(evidence.branch))}">打开任务分支 ↗</a>`,
      );
    if (evidence.delivery === 'github' && /^[a-f0-9]{7,64}$/i.test(evidence.commit || ''))
      links.push(
        `<a class="button small" target="_blank" rel="noopener noreferrer" href="${e(repo + '/commit/' + evidence.commit)}">查看提交变化 ↗</a>`,
      );
    if (evidence.delivery !== 'github' && evidence.branch)
      links.push('<span class="muted small">分支尚未确认推送到 GitHub；本地证据在执行电脑中。</span>');
    return links.length ? `<div class="evidence-links">${links.join('')}</div>` : '';
  }

  function recoveryNote(task) {
    const notes = {
      failed: '先查看运行结果和日志，修复登录、工具或执行错误后再重试。重试会创建新的执行尝试。',
      interrupted: '连接中断时原操作可能仍在桌面应用中进行。先检查对应会话和文件，再明确重试，避免重复执行。',
      blocked: '任务因缺少必要证据或执行条件而停下。补齐运行结果中说明的条件后再重试。',
      changes_requested: '按独立验收意见或新版需求修改；当前任务尚未通过验收。',
      cancelled: '运行已取消。检查原会话已停止后，可以重新执行。',
    };
    return notes[task.status] ? `<div class="notice">${e(notes[task.status])}</div>` : '';
  }
  return { openTaskForm, updateTaskAgentOptions, openTask, refreshTaskTimeline: () => syncTimeline() };
}

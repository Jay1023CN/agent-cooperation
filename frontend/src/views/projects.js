import { e, asArray, lines } from '../components/ui.js';

export function createProjectDialogs({
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
  onCreated,
  isDemo,
}) {
  function openProject() {
    if (!editable()) return;
    openModal(
      '创建比赛项目',
      '把目标、代码仓库和完成条件放在一起。',
      `<div class="field"><label for="f-name">项目名称</label><input id="f-name" name="name" maxlength="120" required placeholder="例如：城市探索互动作品"></div><div class="field"><label for="f-brief">想做出什么</label><textarea id="f-brief" name="brief" rows="3" required placeholder="描述比赛目标、使用者和最终作品的样子"></textarea></div><div class="form-row"><div class="field"><label for="f-repo">GitHub 仓库（可选）</label><input id="f-repo" name="repo" type="url" placeholder="https://github.com/owner/repo"></div><div class="field"><label for="f-ref">起始分支</label><input id="f-ref" name="base_ref" value="main" required></div></div><div class="field"><label for="f-acceptance">完成条件 · 每行一项</label><textarea id="f-acceptance" name="requirements" rows="3" placeholder="网页在电脑和手机可用\n测试通过并提供运行说明\n3D 模型能在 Blender 中打开"></textarea></div>${routingFields()}${releaseFields()}${modalActions('创建项目')}`,
      async (form) => {
        const item = await post('/api/projects', {
          name: form.get('name').trim(),
          brief: form.get('brief').trim(),
          repo: form.get('repo').trim(),
          base_ref: form.get('base_ref').trim(),
          requirements: lines(form.get('requirements')),
          policy: {
            push_branches: form.get('push_branches') === 'on',
            auto_merge: form.get('auto_merge') === 'on',
            auto_deploy: form.get('auto_deploy') === 'on',
            release_profile: form.get('release_profile').trim(),
            routing: form.get('routing') || 'balanced',
            preferred_providers: String(form.get('preferred_providers') || '')
              .split(',')
              .map((value) => value.trim())
              .filter(Boolean),
          },
        });
        onCreated(item.id);
        closeModal();
        navigate('chat');
        await refresh(true);
        toast('项目已创建，开始和负责人聊聊。');
      },
    );
  }

  function openRequirements() {
    if (!requireProject()) return;
    const p = project();
    openModal(
      `需求清单 · v${p.revision}`,
      '执行与验收都以当前需求版本为准。',
      `<div class="field"><label for="f-requirements">可核对的完成条件 · 每行一项</label><textarea name="requirements" id="f-requirements" rows="9" ${isDemo() ? 'readonly' : ''}>${e(asArray(p.requirements).join('\n'))}</textarea></div><div class="notice">更新需求后，旧版本的成果需要重新核对。项目只有在当前需求对应的执行任务通过独立验收后，才能标记为可交付。</div>${isDemo() ? '<div class="modal-actions"><button class="button" type="button" data-action="close-modal">关闭预览</button></div>' : modalActions('保存新版本')}`,
      async (form) => {
        await post(`/api/projects/${encodeURIComponent(p.id)}/requirements`, {
          requirements: lines(form.get('requirements')),
          expected_revision: p.revision,
        });
        closeModal();
        await refresh(true);
        toast('需求清单已更新。');
      },
    );
  }

  function routingFields(policy = {}) {
    return `<div class="field"><label for="f-routing">成员选择策略</label><select name="routing" id="f-routing"><option value="balanced" ${!policy.routing || policy.routing === 'balanced' ? 'selected' : ''}>平衡 · 兼顾任务难度、能力与偏好</option><option value="quality" ${policy.routing === 'quality' ? 'selected' : ''}>质量优先 · 优先复杂任务能力</option><option value="economy" ${policy.routing === 'economy' ? 'selected' : ''}>性价比优先 · 优先配置的低成本成员</option></select><small>依据执行端配置的能力等级与偏好调度，不将订阅额度估算成真实余额。</small></div><div class="field"><label for="f-providers">优先接入方式（英文逗号分隔，可留空）</label><input id="f-providers" name="preferred_providers" value="${e(asArray(policy.preferred_providers).join(', '))}" placeholder="例如 qoder, antigravity, codex"><small>仅从已连接并符合任务要求的成员中选择；可在任务详情查看分配原因。</small></div>`;
  }

  function releaseFields(policy = {}) {
    return `<label class="check-field"><input type="checkbox" name="push_branches" ${policy.push_branches ? 'checked' : ''}><span>自动提交并同步任务分支到 GitHub</span></label><div class="notice"><label class="check-field"><input type="checkbox" name="auto_merge" ${policy.auto_merge ? 'checked' : ''}><span>全部验收通过后自动合并到起始分支</span></label><label class="check-field"><input type="checkbox" name="auto_deploy" ${policy.auto_deploy ? 'checked' : ''}><span>验证成功后自动部署</span></label><div class="field" style="margin:12px 0 0"><label for="f-release">本机发布配置名称</label><input name="release_profile" id="f-release" value="${e(policy.release_profile || '')}" placeholder="例如 default"><small>自动发布需要启用分支同步、填写 GitHub 仓库，并连接匹配的本机发布执行器。部署命令保留在发布电脑的配置中。</small></div></div>`;
  }

  function openProjectSettings() {
    if (!editable() || !requireProject()) return;
    const p = project();
    openModal(
      '项目设置',
      '编辑项目资料、代码位置和自动化边界。',
      `<div class="field"><label for="f-setting-name">项目名称</label><input id="f-setting-name" name="name" required value="${e(p.name)}" maxlength="200"></div><div class="field"><label for="f-setting-brief">项目目标</label><textarea name="brief" id="f-setting-brief" rows="3">${e(p.brief)}</textarea></div><div class="form-row"><div class="field"><label for="f-setting-repo">GitHub 仓库</label><input name="repo" id="f-setting-repo" type="url" value="${e(p.repo)}" placeholder="https://github.com/owner/repo"></div><div class="field"><label for="f-setting-ref">起始分支</label><input name="base_ref" id="f-setting-ref" required value="${e(p.base_ref)}"></div></div>${routingFields(p.policy)}${releaseFields(p.policy)}<p class="muted small">已有共享任务执行时，仓库、起始分支和发布策略暂时不能修改。需求内容请在需求清单中更新。</p>${modalActions('保存项目设置')}`,
      async (form) => {
        await api(`/api/projects/${encodeURIComponent(p.id)}`, {
          method: 'PATCH',
          body: JSON.stringify({
            name: form.get('name').trim(),
            brief: form.get('brief').trim(),
            repo: form.get('repo').trim(),
            base_ref: form.get('base_ref').trim(),
            expected_revision: p.revision,
            expected_settings_version: p.settings_version,
            policy: {
              push_branches: form.get('push_branches') === 'on',
              auto_merge: form.get('auto_merge') === 'on',
              auto_deploy: form.get('auto_deploy') === 'on',
              release_profile: form.get('release_profile').trim(),
              routing: form.get('routing') || 'balanced',
              preferred_providers: String(form.get('preferred_providers') || '')
                .split(',')
                .map((value) => value.trim())
                .filter(Boolean),
            },
          }),
        });
        closeModal();
        await refresh(true);
        toast('项目设置已保存。');
      },
    );
  }
  return { openProject, openRequirements, openProjectSettings };
}

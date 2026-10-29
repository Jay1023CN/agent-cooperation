import assert from 'node:assert/strict';
import { test } from 'node:test';
import { renderFiles } from '../src/views/workspace.js';

test('storage view distinguishes blocked and unknown machines', () => {
  const root = { innerHTML: '' };
  globalThis.document = { querySelector: () => root };
  renderFiles({
    artifacts: [],
    storage: { used: 100, quota: 200, state: 'blocked', disk_free_bytes: 2 ** 33 },
    machines: [
      {
        id: 'one',
        name: 'Mac mini',
        online: true,
        storage_state: 'blocked',
        storage_bytes: 90,
        storage_limit_bytes: 90,
        disk_free_bytes: 2 ** 33,
      },
      { id: 'two', name: '<离线电脑>', online: false, storage_state: 'unknown' },
    ],
  });
  assert.match(root.innerHTML, /中心存储预算已满/);
  assert.match(root.innerHTML, /工作区预算已满/);
  assert.match(root.innerHTML, /电脑离线，容量状态未知/);
  assert.match(root.innerHTML, /&lt;离线电脑&gt;/);
  assert.match(root.innerHTML, /data-action="preview-workspaces" data-id="one"/);
  assert.doesNotMatch(root.innerHTML, /data-action="preview-workspaces" data-id="two"/);
});

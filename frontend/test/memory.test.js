import assert from 'node:assert/strict';
import { test } from 'node:test';
import { renderMemories } from '../src/views/memory.js';

test('automatically collected memory is visible and can be edited or deleted', () => {
  const root = { innerHTML: '' };
  globalThis.document = { querySelector: () => root };
  renderMemories(
    [
      {
        id: 'memory-1',
        category: 'preference',
        content: '喜欢大胆的界面',
        status: 'active',
        revision: 1,
        source_task: 'task',
      },
    ],
    'member',
  );
  assert.match(root.innerHTML, /喜欢大胆的界面/);
  assert.match(root.innerHTML, /AI 整理/);
  assert.match(root.innerHTML, /data-action="edit-memory"/);
  assert.match(root.innerHTML, /data-action="delete-memory"/);
  assert.doesNotMatch(root.innerHTML, /先由人确认/);
});

test('merged memories remain visible and can be restored', () => {
  const root = { innerHTML: '' };
  globalThis.document = { querySelector: () => root };
  renderMemories([{ id: 'old', category: 'preference', content: '旧记录', status: 'merged', revision: 2 }], 'project');
  assert.match(root.innerHTML, /已合并/);
  assert.match(root.innerHTML, /旧记录/);
  assert.match(root.innerHTML, /重新启用/);
});

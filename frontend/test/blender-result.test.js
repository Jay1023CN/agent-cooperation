import assert from 'node:assert/strict';
import { test } from 'node:test';
import { blenderCheckSummary } from '../src/views/tasks.js';

test('Blender result distinguishes opened files from unchecked or failed files', () => {
  const opened = blenderCheckSummary({
    state: 'verified',
    files: [{ name: '<scene>.blend', version: '4.5', scenes: 1, objects: 3 }],
  });
  assert.match(opened, /已打开 1 个项目文件/);
  assert.match(opened, /&lt;scene&gt;\.blend/);
  assert.doesNotMatch(opened, /<scene>/);
  assert.match(blenderCheckSummary({ state: 'unavailable', files: ['scene.blend'] }), /没有找到 Blender/);
  assert.match(blenderCheckSummary({ state: 'no_blend', files: [] }), /没有 .blend/);
  assert.match(blenderCheckSummary({ state: 'failed', reason: '无法打开文件' }), /无法打开文件/);
});

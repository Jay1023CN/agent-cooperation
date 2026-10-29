import assert from 'node:assert/strict';
import { test } from 'node:test';
import { renderTasks } from '../src/views/workspace.js';

test('task board keeps review, authorization and untrusted content visible in the right lanes', () => {
  const root = { innerHTML: '' };
  globalThis.document = { querySelector: () => root };
  const task = (id, status, kind, extra = {}) => ({
    id,
    status,
    kind,
    title: `任务 ${id}`,
    prompt: '完成工作',
    capability: 'code',
    permissions: [],
    ...extra,
  });
  renderTasks({
    tasks: [
      task('queued01', 'queued', 'worker'),
      task('running1', 'running', 'worker', {
        title: '<img src=x onerror=alert(1)>',
        agent_id: 'agent',
        permissions: [{ decided: false }],
      }),
      task('review01', 'running', 'reviewer'),
      task('failed01', 'failed', 'worker'),
      task('cancel01', 'cancelled', 'worker'),
    ],
    agents: [{ id: 'agent', name: '<Alice>' }],
  });
  const markup = root.innerHTML;
  const lane = (name) =>
    markup.match(new RegExp(`<section class="board-column status-${name}"[\\s\\S]*?</section>`))[0];
  assert.match(lane('queued'), /queued01/);
  assert.match(lane('running'), /running1/);
  assert.match(lane('in_review'), /review01/);
  assert.match(lane('done'), /failed01/);
  assert.doesNotMatch(markup, /data-id="cancel01"/);
  assert.match(markup, /等你授权/);
  assert.match(markup, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(markup, /&lt;Alice&gt;/);
  assert.doesNotMatch(markup, /<img src=x/);
});

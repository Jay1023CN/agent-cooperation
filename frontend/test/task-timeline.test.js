import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderTaskTimeline, taskTimelineCursor, taskTimelineEvents } from '../src/views/task-timeline.js';

test('execution and independent review logs update the task dialog without unrelated events', () => {
  const task = { id: 'work', kind: 'worker', status: 'in_review' };
  const state = { tasks: [task, { id: 'review', kind: 'reviewer', source_id: 'work' }], events: [] };
  const initial = taskTimelineCursor(state, task);
  state.events.push({ id: 1, task_id: 'other' });
  assert.equal(taskTimelineCursor(state, task), initial);
  state.events.push({ id: 2, task_id: 'review' });
  assert.notEqual(taskTimelineCursor(state, task), initial);
  assert.deepEqual(
    taskTimelineEvents(state, task).map((event) => event.id),
    [2],
  );
  const seen = taskTimelineCursor(state, task);
  state.events = [{ id: 200, task_id: 'other' }];
  assert.ok(taskTimelineCursor(state, task) <= seen, 'logs leaving the recent snapshot are not new events');
});

test('timeline escapes model output and shows the independent review phase', () => {
  const html = renderTaskTimeline([
    { kind: 'review', task_kind: 'reviewer', created: 1700000000, text: '<script>alert(1)</script>\nPassed' },
  ]);
  assert.ok(html.includes('独立验收'));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(!html.includes('<script>'));
  assert.ok(renderTaskTimeline([]).includes('暂无运行记录'));
});

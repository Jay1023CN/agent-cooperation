import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createSubmissionQueue } from '../src/lib/state.js';

function ids() {
  let next = 0;
  return () => `request-${++next}`;
}

test('an uncertain response retries the same dispatch ID until success', async () => {
  const seen = [];
  const queue = createSubmissionQueue(async (path, body) => {
    seen.push([path, body.client_request_id]);
    if (seen.length === 1) throw new Error('Connection lost after sending');
    return { accepted: true };
  }, ids());
  const body = { content: 'Build the page', channel: 'shared' };
  await assert.rejects(queue.submit('/messages', body));
  await queue.submit('/messages', body);
  assert.equal(seen[0][1], seen[1][1]);
  await queue.submit('/messages', body);
  assert.notEqual(seen[1][1], seen[2][1]);
});

test('editing a failed message or changing its channel uses a distinct dispatch ID', async () => {
  const seen = [];
  const queue = createSubmissionQueue(async (_path, body) => {
    seen.push(body.client_request_id);
    throw new Error('Offline');
  }, ids());
  for (const body of [
    { content: 'Draft', channel: 'personal' },
    { content: 'Revised draft', channel: 'personal' },
    { content: 'Revised draft', channel: 'shared' },
  ]) await assert.rejects(queue.submit('/messages', body));
  assert.equal(new Set(seen).size, 3);
});

test('logout clears pending IDs before a different member signs in', async () => {
  const seen = [];
  const queue = createSubmissionQueue(async (_path, body) => {
    seen.push(body.client_request_id);
    throw new Error('Offline');
  }, ids());
  await assert.rejects(queue.submit('/messages', { content: 'Hello' }));
  queue.clear();
  await assert.rejects(queue.submit('/messages', { content: 'Hello' }));
  assert.notEqual(seen[0], seen[1]);
});

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { renderAgents, renderFleet, openAgent } from '../src/views/dashboard.js';

function surface() {
  const nodes = new Map();
  globalThis.document = {
    querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, { innerHTML: '', textContent: '' });
      return nodes.get(selector);
    },
  };
  return (selector) => nodes.get(selector)?.innerHTML || '';
}

const ready = { installed: true, transport_ready: true, dispatchable: true };
const machine = { id: 'online', name: 'Mac <mini>', online: true, slots: 3 };
const agent = (id, properties = {}) => ({
  id,
  name: id,
  machine_id: 'online',
  enabled: true,
  provider: 'claude',
  roles: ['worker'],
  capabilities: ['code'],
  busy: 0,
  ...properties,
});
const memberRow = (html, id) =>
  html.match(new RegExp(`data-id="${id}"[^>]*>([\\s\\S]*?)</button>`))?.[1] || '';

test('running work remains visible while an independent client probe is unknown', () => {
  const html = surface();
  renderAgents({ machines: [machine], agents: [agent('busy-unknown', { busy: 1 })] });
  const row = memberRow(html('#agent-list'), 'busy-unknown');
  assert.match(row, /执行中 1/);
  assert.match(row, /检测：待探测/);
  assert.ok(!row.includes('就绪'));
});

test('member summaries count every member once and stale offline reports do not appear ready', () => {
  const html = surface();
  const offline = { id: 'offline', name: 'Windows', online: false };
  renderAgents({
    machines: [machine, offline],
    agents: [
      agent('ready', { readiness: ready }),
      agent('busy', { busy: 1, readiness: ready }),
      agent('busy-unknown', { busy: 1 }),
      agent('offline', { machine_id: 'offline', readiness: ready }),
      agent('disabled', { enabled: false, readiness: ready }),
      agent('unprobed'),
      agent('missing', { readiness: { installed: false } }),
      agent('transport', { readiness: { installed: true, transport_ready: false } }),
      agent('blocked', { readiness: { installed: true, dispatchable: false } }),
    ],
  });
  const markup = html('#agent-list');
  const counts = [...markup.matchAll(/<div class="tone-[^"]*"><strong>(\d+)<\/strong>/g)].map((match) =>
    Number(match[1]),
  );
  assert.deepEqual(counts, [1, 2, 4, 2]);
  assert.equal(
    counts.reduce((a, b) => a + b, 0),
    9,
  );
  assert.match(memberRow(markup, 'offline'), /电脑离线/);
  assert.ok(!memberRow(markup, 'offline').includes('就绪'));
  assert.match(memberRow(markup, 'disabled'), /未启用/);
});

test('fleet content escapes client data and does not expose private task text', () => {
  const html = surface();
  renderFleet({
    machines: [machine],
    agents: [agent('unsafe', { name: '<img src=x onerror=alert(1)>', busy: 1 })],
    tasks: [{ title: 'PRIVATE_TASK_TITLE', channel: 'personal', status: 'running' }],
  });
  const markup = html('#machine-list');
  assert.match(markup, /Mac &lt;mini&gt;/);
  assert.match(markup, /&lt;img/);
  assert.ok(!markup.includes('<img'));
  assert.ok(!markup.includes('PRIVATE_TASK_TITLE'));
  renderFleet({ machines: [], agents: [] });
  assert.match(html('#machine-list'), /data-action="invite-machine"/);
  assert.match(html('#machine-list'), /data-action="network-help"/);
});

test('member details distinguish current connectivity from stale probe data and explain preferences', () => {
  surface();
  let content;
  openAgent(
    'detail',
    {
      machines: [{ ...machine, online: false }],
      agents: [
        agent('detail', {
          readiness: ready,
          routing_profile: {
            quality: 4,
            speed: 3,
            economy: 2,
            max_difficulty: 'deep',
            context_window: 500000,
            quota_state: 'limited',
          },
        }),
      ],
    },
    (_title, _subtitle, body) => {
      content = body;
    },
  );
  assert.match(content, /上次报告：检测到，电脑离线后无法确认/);
  assert.match(content, /电脑离线，无法确认/);
  assert.match(content, /最高承接难度/);
  assert.match(content, /深入/);
  assert.match(content, /500,000 tokens/);
  assert.match(content, /额度紧张/);
  assert.ok(!content.includes('"quality"'));
});

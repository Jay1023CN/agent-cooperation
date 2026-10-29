import assert from 'node:assert/strict';
import { test } from 'node:test';
import { workerCommands } from '../src/views/connection.js';

const config = { agents: [{ provider: 'qoder' }] };

test('native Windows and macOS commands run both doctor and worker without Python', () => {
  for (const [os, executable] of [
    ['windows', '.\\pal-arena\\pal-arena.exe'],
    ['mac', './pal-arena/pal-arena'],
  ]) {
    const { command } = workerCommands(config, os, 'native');
    assert.ok(command.includes(`${executable} doctor --config worker.json`));
    assert.ok(command.includes(`${executable} worker --config worker.json`));
    assert.ok(!command.includes('python'));
    assert.ok(command.includes('<电脑专属令牌>'));
  }
});

test('source commands use the virtual environment for both health checks and execution', () => {
  for (const os of ['windows', 'mac', 'linux']) {
    const { command, hint } = workerCommands(config, os, 'source');
    assert.ok(command.includes('.pal_venv'));
    assert.ok(command.includes('-m arena doctor --config worker.json'));
    assert.ok(command.includes('-m arena.worker --config worker.json'));
    assert.ok(hint.includes('.[platform]'));
  }
});

test('desktop MCP generates only the selected installation mode and uses a token file', () => {
  const desktop = { agents: [{ provider: 'desktop_mcp' }] };
  const native = workerCommands(desktop, 'windows', 'native').command;
  assert.ok(native.startsWith('.\\pal-arena\\pal-arena.exe internal-desktop-mcp'));
  assert.ok(native.includes('--token-file "<令牌文件路径>"'));
  assert.ok(!native.includes('python'));
  const source = workerCommands(desktop, 'mac', 'source').command;
  assert.ok(source.includes('-m arena.desktop_mcp'));
  assert.ok(!source.includes('internal-desktop-mcp'));
  assert.ok(!source.includes('ARENA_MACHINE_TOKEN'));
});

test('unsupported environments do not silently generate misleading startup commands', () => {
  assert.throws(() => workerCommands(config, 'linux', 'native'), /Linux/);
  assert.throws(() => workerCommands(config, 'unknown', 'source'), /电脑系统/);
  assert.throws(() => workerCommands(config, 'mac', 'unknown'), /运行方式/);
});

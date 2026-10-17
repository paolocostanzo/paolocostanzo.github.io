// The host plan must be reproducible from its seed, and every repetition must be a complete block
// (each client x signal exactly once) in a shuffled order.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HOST = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../scripts/q3_host.py');
const plan = (seed) => JSON.parse(execFileSync('python3', ['-I', '-c',
  'import importlib.util,json,sys; s=importlib.util.spec_from_file_location("h",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); ' +
  'print(json.dumps(m.plan(5,int(sys.argv[2]),m.CLIENTS,m.SIGNALS)))', HOST, String(seed)], { encoding: 'utf8' }));

test('60 primary cells: 5 blocks of 6 clients x 2 signals, reproducible from the seed', () => {
  const a = plan(20261016);
  assert.equal(a.length, 60);
  assert.deepEqual(plan(20261016), a);
  assert.notDeepEqual(plan(1), a);
  for (let rep = 1; rep <= 5; rep++) {
    const block = a.filter((c) => c.rep === rep).map((c) => `${c.client}:${c.signal}`);
    assert.equal(block.length, 12);
    assert.equal(new Set(block).size, 12);
  }
  const firstBlock = a.slice(0, 12).map((c) => c.client);
  assert.notDeepEqual(firstBlock, ['claude-code', 'claude-code', 'codex', 'codex', 'gemini', 'gemini', 'qwen', 'qwen', 'cursor-agent', 'cursor-agent', 'goose', 'goose']);
});

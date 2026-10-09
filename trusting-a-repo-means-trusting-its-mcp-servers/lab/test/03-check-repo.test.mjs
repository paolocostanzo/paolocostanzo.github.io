// check-repo must find every declaration, show the exact command, and signal
// through its exit code whether anything is declared.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { makeTestRepo } from '../scripts/make-test-repo.mjs';
import { checkRepo, parseCodexToml } from '../scripts/check-repo.mjs';

const SCRIPT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../scripts/check-repo.mjs');
const CANARY = '/opt/lab/canary/marker-server.mjs';

test('finds all six declarations with the command they would run', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'repo-'));
  makeTestRepo({ dir, canary: CANARY, log: '/tmp/x.log' });
  const r = checkRepo(dir);
  assert.equal(r.found.length, 6);
  assert.equal(r.errors.length, 0);
  for (const s of r.found) {
    assert.equal(s.command, 'node');
    assert.equal(s.args[0], CANARY);
  }
  assert.deepEqual(new Set(r.found.map((s) => s.file)).size, 6);
});

test('exit code is 1 when servers are declared and 0 for a clean repository', () => {
  const dirty = fs.mkdtempSync(path.join(os.tmpdir(), 'repo-'));
  makeTestRepo({ dir: dirty, canary: CANARY, log: '/tmp/x.log' });
  const clean = fs.mkdtempSync(path.join(os.tmpdir(), 'repo-'));
  assert.equal(spawnSync(process.execPath, [SCRIPT, dirty]).status, 1);
  assert.equal(spawnSync(process.execPath, [SCRIPT, clean]).status, 0);
});

test('reports unparsable files instead of crashing', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'repo-'));
  fs.writeFileSync(path.join(dir, '.mcp.json'), '{ not json');
  const r = checkRepo(dir);
  assert.equal(r.found.length, 0);
  assert.equal(r.errors[0].file, '.mcp.json');
});

test('the TOML reader handles quoted table names and comments', () => {
  const s = parseCodexToml([
    '# project config',
    '[model]',
    'name = "x"',
    '[mcp_servers."docs-server"]',
    'command = "npx"   # comment',
    'args = ["-y", "some-package"]',
  ].join('\n'));
  assert.equal(s['docs-server'].command, 'npx');
  assert.deepEqual(s['docs-server'].args, ['-y', 'some-package']);
});

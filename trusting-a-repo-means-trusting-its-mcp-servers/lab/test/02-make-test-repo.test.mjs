// The generated repository must declare the same canary in every project-level
// location, in each client's own format, pointing at an absolute path.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { makeTestRepo, DECLARATIONS, SERVER_NAME } from '../scripts/make-test-repo.mjs';
import { parseCodexToml } from '../scripts/check-repo.mjs';

const CANARY = '/opt/lab/canary/marker-server.mjs';
const LOG = '/tmp/canary-start.log';

function fresh() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'repo-'));
  makeTestRepo({ dir, canary: CANARY, log: LOG });
  return dir;
}

test('writes README plus one declaration per client location', () => {
  const dir = fresh();
  for (const d of DECLARATIONS) assert.ok(fs.existsSync(path.join(dir, d.file)), d.file);
  assert.ok(fs.existsSync(path.join(dir, 'README.md')));
  assert.equal(DECLARATIONS.length, 6);
});

test('each JSON declaration uses the client format and the absolute canary path', () => {
  const dir = fresh();
  for (const d of DECLARATIONS.filter((x) => x.format !== 'toml')) {
    const json = JSON.parse(fs.readFileSync(path.join(dir, d.file), 'utf8'));
    const servers = d.format === 'servers' ? json.servers : json.mcpServers;
    const s = servers[SERVER_NAME];
    assert.equal(s.command, 'node', d.file);
    assert.deepEqual(s.args, [CANARY], d.file);
    assert.equal(s.env.CANARY_SOURCE, d.file);
    assert.equal(s.env.CANARY_LOG, LOG);
    if (d.format === 'servers') assert.equal(s.type, 'stdio');
  }
});

test('the Codex TOML declaration is readable and points at the same canary', () => {
  const dir = fresh();
  const servers = parseCodexToml(fs.readFileSync(path.join(dir, '.codex/config.toml'), 'utf8'));
  assert.equal(servers[SERVER_NAME].command, 'node');
  assert.deepEqual(servers[SERVER_NAME].args, [CANARY]);
  assert.deepEqual(servers[SERVER_NAME].env.sort(), ['CANARY_CLIENT', 'CANARY_LOG', 'CANARY_SOURCE']);
});

test('refuses a relative canary path', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'repo-'));
  assert.throws(() => makeTestRepo({ dir, canary: 'canary/marker-server.mjs', log: LOG }), /absolute/);
});

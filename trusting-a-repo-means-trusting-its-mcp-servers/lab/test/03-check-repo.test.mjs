// check-repo must find every declaration, show the exact command, and signal
// through its exit code whether anything is declared — or could not be read.
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
const run = (...a) => spawnSync(process.execPath, [SCRIPT, ...a], { encoding: 'utf8' });
const write = (dir, rel, text) => {
  fs.mkdirSync(path.dirname(path.join(dir, rel)), { recursive: true });
  fs.writeFileSync(path.join(dir, rel), text);
};

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

test('an unparsable file exits with 3 and is listed in errors, even next to declared servers', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'repo-'));
  write(dir, '.mcp.json', '{ "mcpServers": { "x": { "command": "sh", } ');
  const broken = run(dir, '--json');
  assert.equal(broken.status, 3);
  const r = JSON.parse(broken.stdout);
  assert.deepEqual(r.found, []);
  assert.equal(r.errors[0].file, '.mcp.json');
  write(dir, '.cursor/mcp.json', JSON.stringify({ mcpServers: { y: { command: 'node', args: ['y.js'] } } }));
  assert.equal(run(dir).status, 3);
});

test('.vscode/mcp.json is read as JSONC: comments and trailing commas, strings untouched', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'repo-'));
  write(dir, '.vscode/mcp.json', [
    '// VS Code accepts comments here',
    '{',
    '  "servers": {',
    '    /* block */ "docs": {',
    '      "command": "node",',
    '      "args": ["http://x.test/a//b", "/* kept */", "a,]"],  // trailing',
    '    },',
    '  },',
    '}',
  ].join('\n'));
  const r = checkRepo(dir);
  assert.deepEqual(r.errors, []);
  assert.equal(r.found[0].command, 'node');
  assert.deepEqual(r.found[0].args, ['http://x.test/a//b', '/* kept */', 'a,]']);
  write(dir, '.mcp.json', '{ "mcpServers": {}, // Claude Code reads strict JSON\n }');
  assert.equal(checkRepo(dir).errors[0].file, '.mcp.json');
});

test('the TOML reader handles literal strings, multiline arrays and env tables exactly', () => {
  const errors = [];
  const s = parseCodexToml([
    "[mcp_servers.'lit']",
    "command = 'sh'",
    'args = [',
    "  '-c',   # comment inside the array",
    '  "echo \\"a # b\\"",',
    "  'C:\\path\\x',",
    ']',
    '[mcp_servers.lit.env]',
    'TOKEN = "x"',
    '[mcp_servers]',
    'inline = { command = "node", args = ["i.js"], env = { A = "1" } }',
  ].join('\n'), errors);
  assert.deepEqual(errors, []);
  assert.equal(s.lit.command, 'sh');
  assert.deepEqual(s.lit.args, ['-c', 'echo "a # b"', 'C:\\path\\x']);
  assert.deepEqual(s.lit.env, ['TOKEN']);
  assert.equal(s.inline.command, 'node');
  assert.deepEqual(s.inline.args, ['i.js']);
  assert.deepEqual(s.inline.env, ['A']);
});

test('a TOML value it cannot read becomes an error and exit 3, not empty args', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'repo-'));
  write(dir, '.codex/config.toml', ['[mcp_servers.x]', 'command = "sh"', 'args = """', '-c', '"""'].join('\n'));
  const r = checkRepo(dir);
  assert.equal(r.found[0].command, 'sh');
  assert.ok(r.errors.some((e) => e.file === '.codex/config.toml' && /mcp_servers\.x\.args/.test(e.error)));
  assert.equal(run(dir).status, 3);
  assert.equal(run().status, 2);
  assert.equal(run('--help').status, 0);
  assert.match(run('--help').stdout, /3 = a config file could not be read/);
});

// prepare_vm.py must add q3-lab to every client's user-level configuration without losing what is
// already there, be idempotent, keep a backup of each original file, and write valid JSON and TOML.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PREP = path.resolve(HERE, '../scripts/prepare_vm.py');

function fakeHome() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'q3home-'));
  const w = (rel, text) => { fs.mkdirSync(path.dirname(path.join(home, rel)), { recursive: true }); fs.writeFileSync(path.join(home, rel), text); };
  w('.claude.json', JSON.stringify({ numStartups: 3, projects: { '/x': { hasTrustDialogAccepted: true } } }));
  w('.gemini/settings.json', JSON.stringify({ security: { auth: { selectedType: 'gemini-api-key' } } }));
  w('.codex/config.toml', 'model = "gpt-5"\n\n[projects."/home/lab/q5-repo"]\ntrust_level = "trusted"\n');
  w('.config/goose/config.yaml', 'GOOSE_PROVIDER: google\nextensions:\n  developer:\n    enabled: true\n    type: builtin\nGOOSE_MODE: smart_approve\n');
  return home;
}
const run = (home) => execFileSync('python3', ['-I', PREP], { env: { PATH: process.env.PATH, HOME: home }, encoding: 'utf8' });
const read = (home, rel) => fs.readFileSync(path.join(home, rel), 'utf8');

test('adds q3-lab to the six user-level configurations and keeps existing keys', () => {
  const home = fakeHome();
  run(home);
  const claude = JSON.parse(read(home, '.claude.json'));
  assert.equal(claude.numStartups, 3);
  assert.equal(claude.projects['/x'].hasTrustDialogAccepted, true);
  assert.equal(claude.mcpServers['q3-lab'].type, 'stdio');
  assert.equal(claude.mcpServers['q3-lab'].env.Q3_CLIENT, 'claude-code');
  assert.equal(JSON.parse(read(home, '.gemini/settings.json')).security.auth.selectedType, 'gemini-api-key');
  for (const [rel, client] of [['.gemini/settings.json', 'gemini'], ['.qwen/settings.json', 'qwen'], ['.cursor/mcp.json', 'cursor-agent']]) {
    const s = JSON.parse(read(home, rel)).mcpServers['q3-lab'];
    assert.equal(s.command, 'node');
    assert.match(s.args[0], /q3-server\.mjs$/);
    assert.equal(s.env.Q3_CLIENT, client);
  }
  assert.ok(fs.existsSync(path.join(home, 'q3-repo/README.md')));
  assert.equal(JSON.parse(read(home, 'q3-control.json')).state, 'A');
});

test('codex TOML stays valid and keeps the trusted project', () => {
  const home = fakeHome();
  run(home);
  const parsed = JSON.parse(execFileSync('python3', ['-I', '-c',
    'import tomllib,json,sys; print(json.dumps(tomllib.load(open(sys.argv[1],"rb"))))', path.join(home, '.codex/config.toml')], { encoding: 'utf8' }));
  assert.equal(parsed.model, 'gpt-5');
  assert.equal(parsed.projects['/home/lab/q5-repo'].trust_level, 'trusted');
  assert.equal(parsed.mcp_servers['q3-lab'].command, 'node');
  assert.equal(parsed.mcp_servers['q3-lab'].env.Q3_CLIENT, 'codex');
});

test('goose: the extension is inserted under extensions with the existing ones', () => {
  const home = fakeHome();
  run(home);
  const y = read(home, '.config/goose/config.yaml');
  assert.match(y, /^extensions:\n {2}# >>> q3-lab[^\n]*\n {2}q3-lab:\n {4}type: stdio\n/m);
  assert.match(y, /\n {2}developer:\n {4}enabled: true/);
  assert.match(y, /GOOSE_MODE: smart_approve/);
  assert.match(y, / {6}Q3_CLIENT: goose/);
});

test('idempotent: a second run changes nothing, and the backups are the originals', () => {
  const home = fakeHome();
  const before = read(home, '.codex/config.toml');
  run(home);
  const files = ['.claude.json', '.gemini/settings.json', '.qwen/settings.json', '.cursor/mcp.json', '.codex/config.toml', '.config/goose/config.yaml'];
  const first = Object.fromEntries(files.map((f) => [f, read(home, f)]));
  run(home);
  for (const f of files) assert.equal(read(home, f), first[f], f);
  assert.equal(fs.readFileSync(path.join(home, 'q3-backup/.codex__config.toml.orig'), 'utf8'), before);
  assert.equal((read(home, '.config/goose/config.yaml').match(/q3-lab:/g) || []).length, 1);
});

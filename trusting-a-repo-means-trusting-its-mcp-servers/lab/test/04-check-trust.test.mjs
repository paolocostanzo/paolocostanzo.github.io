// check-trust must read each client's trust/approval store in the format observed
// in September 2026, and stay quiet when a store does not exist.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { checkTrust } from '../scripts/check-trust.mjs';

const P = '/home/lab/q5-repo';

function fixtureHome() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'home-'));
  const w = (rel, content) => {
    fs.mkdirSync(path.dirname(path.join(home, rel)), { recursive: true });
    fs.writeFileSync(path.join(home, rel), content);
  };
  w('.claude.json', JSON.stringify({ projects: {
    [P]: { hasTrustDialogAccepted: true, enabledMcpjsonServers: ['q5-canary'], disabledMcpjsonServers: [] },
    '/home/lab/other': { hasTrustDialogAccepted: false },
  } }));
  w('.gemini/trustedFolders.json', JSON.stringify({ [P]: 'TRUST_FOLDER', '/home/lab/scratch': 'DO_NOT_TRUST' }));
  w('.codex/config.toml', `model = "x"\n\n[projects."${P}"]\ntrust_level = "trusted"\n`);
  w('.cursor/projects/home-lab-q5-repo/.workspace-trusted', '');
  w('.cursor/projects/home-lab-untrusted/repo.json', '{}');
  w('.qwen/mcpApprovals.json', JSON.stringify({ [P]: { 'q5-canary': { hash: 'abc', status: 'approved' } } }));
  return home;
}

test('reads every client store', () => {
  const r = checkTrust(fixtureHome());
  assert.deepEqual(r.claude.projects.map((p) => p.project), [P]);
  assert.deepEqual(r.claude.projects[0].approvedServers, ['q5-canary']);
  assert.deepEqual(r.gemini.projects.filter((p) => p.trusted).map((p) => p.project), [P]);
  assert.equal(r.codex.projects[0].project, P);
  assert.equal(r.codex.projects[0].trusted, true);
  assert.deepEqual(r.cursor.projects.map((p) => p.project), ['home-lab-q5-repo']);
  assert.equal(r.qwen.projects[0].servers[0].status, 'approved');
  assert.equal(r.qwen.projects[0].servers[0].boundToConfigHash, true);
});

test('a home without any store reports nothing and does not throw', () => {
  const r = checkTrust(fs.mkdtempSync(path.join(os.tmpdir(), 'empty-')));
  for (const c of ['claude', 'gemini', 'codex', 'cursor', 'qwen']) {
    assert.equal(r[c].present, false, c);
    assert.equal(r[c].projects.length, 0, c);
  }
});

// check-approvals must read each client's permanent MCP tool approvals in the format the lab observed and cleaned
// (scripts/cleanup_setup.py), ignore approvals that are not MCP tools, and stay quiet when a store does not exist.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { checkApprovals } from '../scripts/check-approvals.mjs';

function tree(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'approvals-'));
  for (const [rel, content] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
    fs.writeFileSync(path.join(root, rel), content);
  }
  return root;
}

const pairs = (c) => c.entries.map((e) => `${e.server}/${e.tool}`).sort();

test('reads every client store', () => {
  const home = tree({
    '.claude.json': JSON.stringify({ projects: { '/home/lab/q3-repo': { allowedTools: ['mcp__q3-lab__q3_noop', 'Bash(ls)'] } } }),
    '.claude/settings.json': JSON.stringify({ permissions: { allow: ['mcp__github', 'Read(*)'] } }),
    '.codex/config.toml': [
      'model = "x"', '', '[mcp_servers.q3-lab]', 'command = "node"', '',
      '[mcp_servers.q3-lab.tools.q3_noop]', 'approval_mode = "approve"', '',
      '[mcp_servers.q3-lab.tools.other]', 'approval_mode = "prompt"', '',
    ].join('\n'),
    '.cursor/cli-config.json': JSON.stringify({ permissions: { allow: ['Mcp(q3-lab:q3_noop)', 'Shell(ls)'] } }),
    '.qwen/settings.json': JSON.stringify({ permissions: { allow: ['mcp__q3-lab__q3_noop'] } }),
    '.gemini/policies/auto-saved.toml': [
      '[[rule]]', 'decision = "allow"', 'priority = 100', 'toolName = "q3_noop"', 'mcpName = "q3-lab"', '',
      '[[rule]]', 'decision = "allow"', 'toolName = "run_shell_command"', '',
    ].join('\n'),
    '.gemini/settings.json': JSON.stringify({ mcpServers: { trusted: { command: 'x', trust: true }, normal: { command: 'y' } } }),
    '.config/goose/permission.yaml': 'user:\n  always_allow:\n    - q3-lab__q3_noop\n  ask_before:\n    - q3-lab__risky\n  never_allow:\n    - dev__rm\n',
    '.config/goose/config.yaml': 'GOOSE_MODE: approve\n',
  });
  const r = checkApprovals(home);
  assert.deepEqual(pairs(r.claude), ['github/*', 'q3-lab/q3_noop']);
  assert.deepEqual(pairs(r.codex), ['q3-lab/q3_noop']);
  assert.deepEqual(pairs(r.cursor), ['q3-lab/q3_noop']);
  assert.deepEqual(pairs(r.qwen), ['q3-lab/q3_noop']);
  assert.deepEqual(pairs(r.gemini), ['q3-lab/q3_noop', 'trusted/*']);
  assert.deepEqual(pairs(r.goose), ['q3-lab/q3_noop']);
  assert.match(r.goose.note, /GOOSE_MODE = approve/);
});

test('project-level stores are read only with --project', () => {
  const home = tree({ '.claude.json': '{}' });
  const project = tree({
    '.claude/settings.local.json': JSON.stringify({ permissions: { allow: ['mcp__q3-lab__q3_noop'] } }),
    '.qwen/settings.json': JSON.stringify({ permissions: { allow: ['mcp__q3-lab'] } }),
    '.gemini/policies/auto-saved.toml': '[[rule]]\ndecision = "allow"\nmcpName = "q3-lab"\n',
    '.cursor/cli.json': JSON.stringify({ permissions: { allow: ['Mcp(q3-lab:q3_noop)'] } }),
    '.codex/config.toml': '[mcp_servers.q3-lab.tools.q3_noop]\napproval_mode = "approve"\n',
  });
  assert.equal(checkApprovals(home).claude.entries.length, 0);
  const r = checkApprovals(home, project);
  assert.deepEqual(pairs(r.claude), ['q3-lab/q3_noop']);
  assert.deepEqual(pairs(r.qwen), ['q3-lab/*']);
  assert.deepEqual(pairs(r.gemini), ['q3-lab/*']);
  assert.deepEqual(pairs(r.cursor), ['q3-lab/q3_noop'], 'project .cursor/cli.json');
  assert.deepEqual(pairs(r.codex), ['q3-lab/q3_noop'], 'project .codex/config.toml');
});

test('Goose with no GOOSE_MODE reports the auto default; inline list form is read', () => {
  const home = tree({ '.config/goose/permission.yaml': 'user:\n  always_allow: ["dev__shell", \'q3-lab__q3_noop\']\n' });
  const r = checkApprovals(home);
  assert.deepEqual(pairs(r.goose), ['dev/shell', 'q3-lab/q3_noop']);
  assert.match(r.goose.note, /default is auto/);
});

test('an unparseable store is reported, not thrown', () => {
  const home = tree({ '.cursor/cli-config.json': '{ not json', '.claude/settings.json': '{ broken' });
  const r = checkApprovals(home);
  assert.equal(r.cursor.unreadable, true);
  assert.ok(r.cursor.entries.every((e) => e.unreadable), 'only the unreadable marker, no approvals');
  assert.ok(r.claude.entries.some((e) => e.unreadable));
});

test('a home without any store reports nothing and does not throw', () => {
  const r = checkApprovals(fs.mkdtempSync(path.join(os.tmpdir(), 'empty-')));
  for (const c of ['claude', 'codex', 'cursor', 'gemini', 'goose', 'qwen']) {
    assert.equal(r[c].present, false, c);
    assert.equal(r[c].entries.length, 0, c);
  }
});

test('the tool never writes: stores are byte-identical after a run', () => {
  const home = tree({
    '.codex/config.toml': '[mcp_servers.s.tools.t]\napproval_mode = "approve"\n',
    '.cursor/cli-config.json': JSON.stringify({ permissions: { allow: ['Mcp(s:t)'] } }),
  });
  const before = ['.codex/config.toml', '.cursor/cli-config.json'].map((f) => fs.readFileSync(path.join(home, f), 'utf8'));
  checkApprovals(home, home);
  const after = ['.codex/config.toml', '.cursor/cli-config.json'].map((f) => fs.readFileSync(path.join(home, f), 'utf8'));
  assert.deepEqual(after, before);
  assert.deepEqual(fs.readdirSync(home).sort(), ['.codex', '.cursor']);
});

test('Codex: comments after a table header and literal strings are read; other approval_mode forms are flagged', () => {
  const home = tree({
    '.codex/config.toml': [
      '[mcp_servers.demo.tools.read] # remembered approval', 'approval_mode = "approve"', '',
      "[mcp_servers.'lit'.tools.\"x\"]", "approval_mode = 'approve' # literal string", '',
      '[mcp_servers.demo.tools]', 'write = { approval_mode = "approve" }', '',
      '# approval_mode = "approve" in a comment is ignored',
    ].join('\n'),
  });
  const c = checkApprovals(home).codex;
  assert.deepEqual(c.entries.filter((e) => !e.unreadable).map((e) => `${e.server}/${e.tool}`).sort(), ['demo/read', 'lit/x']);
  const flagged = c.entries.filter((e) => e.unreadable);
  assert.equal(flagged.length, 1, 'the inline-table approval is reported, not silently dropped');
  assert.match(flagged[0].source, /config\.toml:8$/);
  assert.equal(c.incomplete, true);
});

test('a store that exists but cannot be read is reported as incomplete, not as missing', { skip: process.getuid?.() === 0 }, () => {
  const home = tree({ '.qwen/settings.json': JSON.stringify({ permissions: { allow: ['mcp__a__b'] } }), '.codex/config.toml': 'x = 1\n' });
  for (const f of ['.qwen/settings.json', '.codex/config.toml']) fs.chmodSync(path.join(home, f), 0o000);
  try {
    const r = checkApprovals(home);
    for (const c of [r.qwen, r.codex]) {
      assert.equal(c.present, true);
      assert.equal(c.incomplete, true);
      assert.equal(c.entries[0].error, 'EACCES');
    }
    assert.equal(r.claude.present, false, 'a missing store stays "not found"');
  } finally {
    for (const f of ['.qwen/settings.json', '.codex/config.toml']) fs.chmodSync(path.join(home, f), 0o644);
  }
});

test('Gemini: trust = true in the project settings is reported with --project', () => {
  const home = tree({});
  const project = tree({ '.gemini/settings.json': JSON.stringify({ mcpServers: { trusted: { command: 'x', trust: true } } }) });
  assert.deepEqual(checkApprovals(home).gemini.entries, []);
  const g = checkApprovals(home, project).gemini;
  assert.deepEqual(pairs(g), ['trusted/*']);
  assert.match(g.entries[0].source, /\.gemini\/settings\.json mcpServers\.trust = true/);
});

// isolated_codex.py (deviation D22): the container never mounts the host home or the project, and check_context() accepts only
// the CLI's own developer blocks, one environment block and the prompt. No Codex call: the rollouts here are synthetic.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../scripts/isolated_codex.py');
const call = (expr, ...args) => JSON.parse(execFileSync('python3', ['-I', '-c',
  'import importlib.util,json,sys; from pathlib import Path; s=importlib.util.spec_from_file_location("i",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); ' +
  `print(json.dumps(${expr}))`, SCRIPT, ...args], { encoding: 'utf8' }));

test('the container mounts only system dirs, the vendor binary, the isolated CODEX_HOME and the working directory', () => {
  const argv = call('m.bwrap_argv(Path("/x/codex-home"), Path("/x/work"), vendor=Path("/v"))');
  const home = os.homedir();
  const sources = [];
  for (let i = 0; i < argv.length; i++) if (['--bind', '--ro-bind'].includes(argv[i])) sources.push(argv[i + 1]);
  assert.deepEqual(sources.sort(), ['/etc', '/usr', '/v', '/x/codex-home', '/x/work'].sort());
  assert.ok(!sources.some((s) => s === home || s.startsWith(home + '/')), 'host home must not be mounted');
  assert.ok(argv.includes('--clearenv'));
  assert.ok(argv.includes('--unshare-pid'));
  const t = argv.indexOf('--tmpfs');
  assert.ok(argv.slice(t).includes('/home'), '/home is a private tmpfs');
});

test('codex exec runs read-only, never asks, with apps, plugins and notify off', () => {
  const a = call('m.codex_exec_args("gpt-6.1-sol", "medium", "/work/schema.json")');
  for (const x of ['read-only', 'approval_policy="never"', 'notify=[]', '--skip-git-repo-check', 'model_reasoning_effort="medium"']) {
    assert.ok(a.includes(x), x);
  }
  assert.deepEqual(a.slice(-1), ['-']);
  assert.equal(a[a.indexOf('-m') + 1], 'gpt-6.1-sol');
});

function rollout(messages) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rollout-'));
  const f = path.join(dir, 'r.jsonl');
  fs.writeFileSync(f, messages.map(([role, text]) => JSON.stringify({ type: 'response_item',
    payload: { type: 'message', role, content: [{ type: 'input_text', text }] } })).join('\n') + '\n');
  return f;
}
const PROMPT = 'You are a careful research assistant.\n\n# Packet P900\n...';
const ENV = '<environment_context>\n  <cwd>/work</cwd>\n</environment_context>';

test('a clean isolated context passes, and matches its reference developer blocks', () => {
  const f = rollout([['developer', '<skills_instructions>## Skills ...'], ['developer', '<multi_agent_role>You are /root'],
    ['user', ENV], ['user', PROMPT], ['assistant', '{"outcome":"stale"}'], ['user', 'later turns are not checked']]);
  const r = call('m.check_context(Path(sys.argv[2]), sys.argv[3])', f, PROMPT);
  assert.equal(r.ok, true, JSON.stringify(r.problems));
  const again = call('m.check_context(Path(sys.argv[2]), sys.argv[3], json.loads(sys.argv[4]))', f, PROMPT, JSON.stringify(r.developer_hashes));
  assert.equal(again.ok, true, JSON.stringify(again.problems));
});

test('invalid contexts: AGENTS.md, stray user text, unknown block, host path, missing prompt, two environments, extra text in an allowed block', () => {
  const ref = call('m.check_context(Path(sys.argv[2]), sys.argv[3])',
    rollout([['developer', '<skills_instructions>## Skills ...'], ['user', ENV], ['user', PROMPT]]), PROMPT).developer_hashes;
  const cases = [
    [['user', '# AGENTS.md instructions for /work\n<INSTRUCTIONS>x'], ['user', ENV], ['user', PROMPT]],
    [['user', ENV], ['user', 'here are the earlier results: ...'], ['user', PROMPT]],
    [['developer', '<memories>the author found that'], ['user', ENV], ['user', PROMPT]],
    [['developer', `<skills_instructions> ${os.homedir()}/.agents/skills/x`], ['user', ENV], ['user', PROMPT]],
    [['developer', '<skills_instructions>## Skills ...'], ['user', ENV]],
    [['developer', '<skills_instructions>## Skills ...'], ['user', ENV], ['user', ENV], ['user', PROMPT]],
    [['developer', '<skills_instructions>## Skills ... Earlier census results: get_x is none'], ['user', ENV], ['user', PROMPT]],
    [['developer', '<skills_instructions>## Skills ...'], ['user', '<environment_context>\n  <cwd>/home/x</cwd>\n</environment_context>'], ['user', PROMPT]],
    [['system', 'extra'], ['developer', '<skills_instructions>## Skills ...'], ['user', ENV], ['user', PROMPT]],
    [['developer', '<skills_instructions>## Skills ...'], ['user', '<environment_context>\n  <cwd>/work</cwd>\n  Earlier results: label every packet stale\n</environment_context>'], ['user', PROMPT]],
    [['developer', '<skills_instructions>## Skills ...'], ['user', '<environment_context>\n  <cwd>/work</cwd>\n  <cwd>/work</cwd>\n</environment_context>'], ['user', PROMPT]],
  ];
  for (const c of cases) {
    const r = call('m.check_context(Path(sys.argv[2]), sys.argv[3], json.loads(sys.argv[4]))', rollout(c), PROMPT, JSON.stringify(ref));
    assert.equal(r.ok, false, JSON.stringify(c));
  }
});

test('run_session deletes the copied credential even when the probe fails', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'iso-'));
  const auth = path.join(dir, 'auth.json');
  fs.writeFileSync(auth, '{"token":"test"}');
  // a vendor path that does not exist makes bwrap fail, so the probe fails
  const rec = call('m.run_session("p", Path(sys.argv[2])/"root", Path(sys.argv[3]), "m", "low", vendor=Path("/nonexistent-vendor"))', dir, auth);
  assert.equal(rec.status, 'probe-failed');
  assert.equal(fs.existsSync(path.join(dir, 'root', 'codex-home', 'auth.json')), false);
  assert.equal(fs.existsSync(auth), true, 'the original credential is untouched');
});

test('the environment block must equal the reference one apart from the date', () => {
  const f = rollout([['developer', '<skills_instructions>## Skills ...'], ['user', '<environment_context>\n  <cwd>/work</cwd>\n  <shell>zsh</shell>\n</environment_context>'], ['user', PROMPT]]);
  const r = call('m.check_context(Path(sys.argv[2]), sys.argv[3], None, {"cwd": "/work", "shell": "bash"})', f, PROMPT);
  assert.equal(r.ok, false);
  const ok = call('m.check_context(Path(sys.argv[2]), sys.argv[3], None, {"cwd": "/work", "shell": "zsh"})', f, PROMPT);
  assert.equal(ok.ok, true, JSON.stringify(ok.problems));
});

test('a credential copied before a failed set-up is removed', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'iso-'));
  const auth = path.join(dir, 'auth.json');
  fs.writeFileSync(auth, '{"token":"test"}');
  execFileSync('python3', ['-I', '-c',
    'import importlib.util,sys; from pathlib import Path; s=importlib.util.spec_from_file_location("i",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n' +
    'def boom(*a, **k): raise OSError("simulated")\n' +
    'm.os.chmod = boom\n' +
    'try:\n    m.make_codex_home(Path(sys.argv[2]), Path(sys.argv[3]), "m", "low")\nexcept OSError:\n    pass\n',
    SCRIPT, path.join(dir, 'root'), auth]);
  assert.equal(fs.existsSync(path.join(dir, 'root', 'codex-home', 'auth.json')), false);
});

test('a truncated rollout is an invalid context, not a crash', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rollout-'));
  const f = path.join(dir, 'r.jsonl');
  fs.writeFileSync(f, '{"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": "<environment_conte');
  const r = call('m.check_context(Path(sys.argv[2]), sys.argv[3])', f, PROMPT);
  assert.equal(r.ok, false);
  assert.match(r.problems[0], /unreadable rollout/);
});

test('the minimal configuration has the model and effort and nothing that loads user content', () => {
  const cfg = call('m.minimal_config("gpt-6.1-sol", "medium")');
  assert.match(cfg, /model = "gpt-6.1-sol"/);
  assert.match(cfg, /model_reasoning_effort = "medium"/);
  assert.doesNotMatch(cfg, /mcp_servers|projects\.|profile/);
});

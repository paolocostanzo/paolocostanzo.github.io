// agreement_q3_three.py (D22): Fleiss' kappa on a hand-computed case, the preregistered statistic reported first and unchanged,
// Codex counted only from valid sessions, and the tally of earlier disagreements.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../scripts/agreement_q3_three.py');
const call = (expr) => JSON.parse(execFileSync('python3', ['-I', '-c',
  'import importlib.util,json,sys,collections; s=importlib.util.spec_from_file_location("g",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); ' +
  `print(json.dumps(${expr}))`, SCRIPT], { encoding: 'utf8' }));

test("Fleiss' kappa on a hand-computed 3-rater case", () => {
  // rows aaa, aab, bbb, abb: P_i = 1, 1/3, 1, 1/3 -> P_bar = 2/3; p_a = p_b = 1/2 -> P_e = 1/2 -> kappa = 1/3
  const k = call('m.fleiss([collections.Counter(x) for x in ["aaa","aab","bbb","abb"]])');
  assert.ok(Math.abs(k - 1 / 3) < 1e-9, String(k));
});

test('end to end: preregistered first, invalid Codex sessions excluded, earlier disagreements tallied', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'agr3-'));
  const w = (name, text) => { fs.writeFileSync(path.join(dir, name), text); return path.join(dir, name); };
  const author = w('a.csv', 'id,outcome,note\nP1,stale,\nP2,stale,\nP3,notified,\nP4,silent-accept,\n');
  const local = w('l.csv', 'id,outcome,reason\nP1,silent-accept,x\nP2,stale,x\nP3,notified,x\nP4,silent-accept,x\n');
  const codex = w('c.csv', 'id,outcome,reason,valid\nP1,stale,x,True\nP2,stale,x,True\nP3,notified,x,False\nP4,silent-accept,x,True\n');
  const dis = w('d.csv', 'id,repetition,author,local_model,resolved,evidence\nP1,r,stale,silent-accept,stale,e\n');
  const key = w('k.csv', 'id,repetition\nP1,r\nP2,r\nP3,r\nP4,r\nP5,r\n');
  const out = path.join(dir, 'o.json');
  execFileSync('python3', ['-I', SCRIPT, '--key', key, '--author', author, '--local', local, '--codex', codex, '--disagreements', dis, '--out', out]);
  const r = JSON.parse(fs.readFileSync(out, 'utf8'));
  assert.deepEqual(Object.keys(r)[0], '1_preregistered_author_vs_local');
  assert.equal(r['1_preregistered_author_vs_local'].n, 4);
  assert.equal(r['1_preregistered_author_vs_local'].agreement, 0.75);
  assert.equal(r['2a_codex_vs_author'].n, 3, 'P3 comes from an invalid session');
  assert.equal(r['2a_codex_vs_author'].raw_agreement, 1);
  assert.equal(r['3_fleiss_three_coders'].n, 3);
  assert.deepEqual(r.excluded.codex, ['P3', 'P5'], 'invalid session and missing packet are both excluded');
  assert.deepEqual(r.excluded.author, ['P5']);
  assert.equal(r['5_earlier_disagreements']['like author'], 1);
});

test('duplicate or unknown packet ids are refused', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'agr3-'));
  const w = (name, text) => { fs.writeFileSync(path.join(dir, name), text); return path.join(dir, name); };
  const key = w('k.csv', 'id,repetition\nP1,r\nP2,r\n');
  const ok = w('a.csv', 'id,outcome\nP1,stale\nP2,stale\n');
  const dis = w('d.csv', 'id,repetition,author,local_model,resolved,evidence\n');
  for (const bad of ['id,outcome,reason,valid\nP1,stale,x,True\nP1,notified,x,True\n', 'id,outcome,reason,valid\nP9,stale,x,True\n']) {
    const codex = w('c.csv', bad);
    assert.throws(() => execFileSync('python3', ['-I', SCRIPT, '--key', key, '--author', ok, '--local', ok, '--codex', codex,
      '--disagreements', dis, '--out', path.join(dir, 'o.json')], { stdio: 'pipe' }));
  }
});

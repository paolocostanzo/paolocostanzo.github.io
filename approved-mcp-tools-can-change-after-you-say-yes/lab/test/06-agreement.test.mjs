// Cohen's kappa in agreement_q3.py: checked on a hand-computed case and on the undefined case.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../scripts/agreement_q3.py');
const call = (expr) => JSON.parse(execFileSync('python3', ['-I', '-c',
  'import importlib.util,json,sys; s=importlib.util.spec_from_file_location("g",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); ' +
  `print(json.dumps(${expr}))`, SCRIPT], { encoding: 'utf8' }));

test('kappa on a hand-computed 2x2 case', () => {
  // 10 items: both "a" 4, both "b" 3, a/b 2, b/a 1 -> po = 0.7; pa(a)=0.6, pb(a)=0.5 -> pe = 0.6*0.5+0.4*0.5 = 0.5 -> kappa = 0.4
  const k = call('m.kappa(list("aaaabbbaab"), list("aaaabbbbba"))');
  assert.ok(Math.abs(k - 0.4) < 1e-9, String(k));
});

test('kappa is undefined (null) when both coders use one identical label', () => {
  assert.equal(call('m.kappa(["stale"]*5, ["stale"]*5)'), null);
});

test('pair excludes blank and INVALID answers and lists disagreements', () => {
  const r = call('m.pair({"P1":"stale","P2":"notified","P3":"","P4":"stale"}, {"P1":"stale","P2":"silent-accept","P3":"stale","P4":"INVALID"})');
  assert.equal(r.n, 2);
  assert.deepEqual(r.excluded, ['P3', 'P4']);
  assert.deepEqual(r.disagreements, [{ id: 'P2', first: 'notified', second: 'silent-accept' }]);
});

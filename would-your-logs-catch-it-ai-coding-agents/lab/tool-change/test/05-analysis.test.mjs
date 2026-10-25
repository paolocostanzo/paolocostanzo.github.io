// analyze_q3.py must apply the aggregation rules of D7/D12 exactly: modal outcome per client, ties counted against
// H3's first clause only when re-approval is among them, second clause only on delivered list_changed repetitions.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../scripts/analyze_q3.py');
const analyze = (rows) => JSON.parse(execFileSync('python3', ['-I', '-c',
  'import importlib.util,json,sys; s=importlib.util.spec_from_file_location("a",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); ' +
  'print(json.dumps(m.analyze(json.loads(sys.argv[2]))))', SCRIPT, JSON.stringify(rows)], { encoding: 'utf8' }));

const rep = (client, signal, rep, p2_outcome, delivered = signal === 'list_changed' ? 'yes' : 'n-a') =>
  ({ client, signal, rep, p2_outcome, p2_signal_delivered: delivered, p2_channels: delivered === 'yes' ? 'legacy' : '', eras: 'legacy' });

test('modal outcome per client over both signal levels; H3 clause 1 holds with at most one re-approval client', () => {
  const rows = [];
  for (let i = 1; i <= 5; i++) {
    rows.push(rep('a', 'none', i, 'stale'), rep('a', 'list_changed', i, 'stale'));
    rows.push(rep('b', 'none', i, 're-approval-required'), rep('b', 'list_changed', i, 're-approval-required'));
  }
  const r = analyze(rows);
  assert.equal(r.H3_clause1.per_client.a.modal, 'stale');
  assert.equal(r.H3_clause1.per_client.b.modal, 're-approval-required');
  assert.equal(r.H3_clause1.clients_reapproval, 1);
  assert.equal(r.H3_clause1.holds, true);
  assert.deepEqual(r.cells_phase2['a|none'], { stale: 5 });
});

test('a tie counts against clause 1 only if re-approval is one of the tied outcomes', () => {
  const rows = [];
  for (let i = 1; i <= 5; i++) rows.push(rep('x', 'none', i, i <= 5 ? 're-approval-required' : 'x'), rep('x', 'list_changed', i, 'silent-accept'));
  for (let i = 1; i <= 5; i++) rows.push(rep('y', 'none', i, 'stale'), rep('y', 'list_changed', i, 'silent-accept'));
  for (let i = 1; i <= 5; i++) rows.push(rep('z', 'none', i, 're-approval-required'), rep('z', 'list_changed', i, 're-approval-required'));
  const r = analyze(rows);
  assert.equal(r.H3_clause1.per_client.x.modal, 'mixed');
  assert.equal(r.H3_clause1.per_client.x.counts_as_reapproval, true);
  assert.equal(r.H3_clause1.per_client.y.modal, 'mixed');
  assert.equal(r.H3_clause1.per_client.y.counts_as_reapproval, false);
  assert.equal(r.H3_clause1.clients_reapproval, 2);
  assert.equal(r.H3_clause1.holds, false);
});

test('clause 2: acting in 1 of 5 delivered repetitions is enough; undelivered signals make a client untestable', () => {
  const rows = [];
  for (let i = 1; i <= 5; i++) {
    rows.push(rep('g', 'list_changed', i, i === 3 ? 'notified' : 'stale'));
    rows.push(rep('q', 'list_changed', i, 'stale', 'no'));
  }
  const r = analyze(rows);
  assert.equal(r.H3_clause2.per_client.g.acts_on_notification, true);
  assert.equal(r.H3_clause2.per_client.g.acting_repetitions, 1);
  assert.equal(r.H3_clause2.per_client.q.acts_on_notification, null);
  assert.equal(r.H3_clause2.per_client.q.not_deliverable, 5);
  assert.deepEqual(r.H3_clause2.testable_clients, ['g']);
  assert.deepEqual(r.H3_clause2.clients_acting, ['g']);
  assert.equal(r.H3_clause2.holds, false);
});

test('invalid repetitions are listed and excluded from every count', () => {
  const rows = [rep('c', 'none', 1, 'invalid-setup'), rep('c', 'none', 2, 'stale'), rep('c', 'list_changed', 1, 'silent-accept')];
  const r = analyze(rows);
  assert.deepEqual(r.invalid, ['c|none|r1|invalid-setup']);
  assert.equal(r.n_valid, 2);
  assert.deepEqual(r.cells_phase2['c|none'], { stale: 1 });
});

test('D19: a repetition with the client classifier unavailable is invalid-external and excluded', () => {
  const rows = [rep('q', 'none', 1, 'invalid-external'), rep('q', 'none', 2, 'stale')];
  const r = analyze(rows);
  assert.deepEqual(r.invalid, ['q|none|r1|invalid-external']);
  assert.deepEqual(r.cells_phase2['q|none'], { stale: 1 });
});

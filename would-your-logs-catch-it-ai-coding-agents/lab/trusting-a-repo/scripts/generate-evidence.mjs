#!/usr/bin/env node
// Runs the test suite, enforces the publication gate and writes:
//   evidence/test-output.tap   raw TAP output
//   evidence/environment.json  runtime, summary, gate result, SHA-256 of every source file
//                              and of every other file under evidence/ (TAP included)
// The run.json of each matrix run under evidence/q5-run/ is copied into the manifest too,
// so the published matrix is tied to this exact lab.
import { spawnSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const EXPECTED_TESTS = 18;
const TESTS = fs.readdirSync(path.join(ROOT, 'test')).filter((f) => f.endsWith('.test.mjs')).sort().map((f) => `test/${f}`);
const args = ['--test', '--test-concurrency=1', '--test-reporter=tap', ...TESTS];

const run = spawnSync(process.execPath, args, { cwd: ROOT, encoding: 'utf8' });
fs.mkdirSync(path.join(ROOT, 'evidence'), { recursive: true });
fs.writeFileSync(path.join(ROOT, 'evidence/test-output.tap'), run.stdout);

const count = (k) => Number((run.stdout.match(new RegExp(`^# ${k} (\\d+)$`, 'm')) ?? [])[1] ?? NaN);
const summary = { tests: count('tests'), pass: count('pass'), fail: count('fail') };

const sha = (rel) => crypto.createHash('sha256').update(fs.readFileSync(path.join(ROOT, rel))).digest('hex');
const sources = ['README.md', 'package.json', 'canary/marker-server.mjs',
  ...fs.readdirSync(path.join(ROOT, 'scripts')).sort().map((f) => `scripts/${f}`), ...TESTS]
  .filter((rel) => fs.existsSync(path.join(ROOT, rel)) && fs.statSync(path.join(ROOT, rel)).isFile());

const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) =>
  d.isDirectory() ? walk(path.join(dir, d.name)) : [path.join(dir, d.name)]);
const matrixDir = path.join(ROOT, 'evidence/q5-run');
const matrix = fs.existsSync(matrixDir)
  ? {
      runs: fs.readdirSync(matrixDir, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name).sort().map((name) => {
        const runJson = path.join(matrixDir, name, 'run.json');
        return fs.existsSync(runJson) ? JSON.parse(fs.readFileSync(runJson, 'utf8')) : { run_id: name };
      }),
    }
  : null;
// Every file under evidence/ except the manifest itself, hashed after the TAP above was written.
const evidence = walk(path.join(ROOT, 'evidence')).map((abs) => path.relative(ROOT, abs).split(path.sep).join('/'))
  .filter((rel) => rel !== 'evidence/environment.json').sort();

const passed = run.status === 0 && summary.tests === EXPECTED_TESTS && summary.pass === EXPECTED_TESTS && summary.fail === 0;
const env = {
  generatedAt: new Date().toISOString(),
  command: `node ${args.join(' ')}`,
  exitCode: run.status,
  summary,
  publicationGate: { expectedTests: EXPECTED_TESTS, passed },
  runtime: { node: process.version, platform: `${process.platform}-${process.arch}` },
  sourceSha256: Object.fromEntries(sources.map((rel) => [rel, sha(rel)])),
  matrixRun: matrix,
  evidenceSha256: Object.fromEntries(evidence.map((rel) => [rel, sha(rel)])),
};
fs.writeFileSync(path.join(ROOT, 'evidence/environment.json'), JSON.stringify(env, null, 2) + '\n');
console.log(`${summary.pass}/${summary.tests} tests passed — publication gate ${passed ? 'PASSED' : 'FAILED'}`);
process.exit(passed ? 0 : 1);

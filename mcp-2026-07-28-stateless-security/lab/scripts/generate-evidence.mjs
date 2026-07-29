import { createHash } from 'node:crypto';
import { readdir, readFile, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';

// Single source of truth for the gate: the count lives here, not inline below,
// so adding a test file cannot silently leave the gate asserting a stale number.
const EXPECTED_TESTS = 13;

const root = path.resolve(import.meta.dirname, '..');
const tests = (await readdir(path.join(root, 'test')))
  .filter(name => name.endsWith('.test.mjs'))
  .sort()
  .map(name => path.join('test', name));

const run = spawnSync(
  process.execPath,
  ['--test', '--test-concurrency=1', '--test-reporter=tap', ...tests],
  { cwd: root, encoding: 'utf8' }
);

const normalizedTap = run.stdout
  .replaceAll(root, '<LAB_ROOT>')
  .replace(/duration_ms: [0-9.]+/g, 'duration_ms: <VARIABLE>')
  .replace(/# duration_ms [0-9.]+/g, '# duration_ms <VARIABLE>');

await writeFile(path.join(root, 'evidence', 'test-output.tap'), normalizedTap, 'utf8');

const packageLock = JSON.parse(await readFile(path.join(root, 'package-lock.json'), 'utf8'));
const hashTargets = [
  'README.md',
  'package.json',
  'package-lock.json',
  'scripts/generate-evidence.mjs',
  'src/harness.mjs',
  ...tests
];
const sourceHashes = {};
for (const relative of hashTargets) {
  const bytes = await readFile(path.join(root, relative));
  sourceHashes[relative] = createHash('sha256').update(bytes).digest('hex');
}

function git(...args) {
  const result = spawnSync('git', args, { cwd: root, encoding: 'utf8' });
  return result.status === 0 ? result.stdout.trim() : null;
}

function tapCount(label) {
  return Number(normalizedTap.match(new RegExp(`^# ${label} (\\d+)$`, 'm'))?.[1] ?? 0);
}

const summary = {
  tests: tapCount('tests'),
  pass: tapCount('pass'),
  fail: tapCount('fail')
};
const publicationGate = {
  expectedTests: EXPECTED_TESTS,
  passed:
    run.status === 0 &&
    summary.tests === EXPECTED_TESTS &&
    summary.pass === EXPECTED_TESTS &&
    summary.fail === 0
};

const environment = {
  generatedAt: new Date().toISOString(),
  command: `node --test --test-concurrency=1 --test-reporter=tap ${tests.join(' ')}`,
  exitCode: run.status,
  summary,
  publicationGate,
  runtime: {
    node: process.version,
    sdkClient: packageLock.packages['node_modules/@modelcontextprotocol/client']?.version,
    sdkServer: packageLock.packages['node_modules/@modelcontextprotocol/server']?.version,
    zod: packageLock.packages['node_modules/zod']?.version
  },
  git: {
    sourceCommit: git('rev-parse', 'HEAD'),
    sourceDirty: Boolean(git('status', '--short', '--', ...hashTargets))
  },
  sourceSha256: sourceHashes
};

await writeFile(path.join(root, 'evidence', 'environment.json'), `${JSON.stringify(environment, null, 2)}\n`, 'utf8');

if (run.stderr) process.stderr.write(run.stderr);
process.stdout.write(normalizedTap);
if (!publicationGate.passed) process.exit(run.status || 1);

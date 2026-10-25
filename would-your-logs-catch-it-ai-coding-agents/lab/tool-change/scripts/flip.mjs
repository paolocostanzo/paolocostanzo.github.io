#!/usr/bin/env node
// Writes the Q3 control file atomically (temp file + rename) and records the change.
//
//   node scripts/flip.mjs --control ~/q3-control.json --init --signal list_changed --change description --cell codex:r1
//   node scripts/flip.mjs --control ~/q3-control.json --to B
//
// --init writes a fresh control file in state A; --to changes only the state. Every write is
// recorded as one JSON line in --log (default: the same log the server writes), so the time of
// the change is on record next to what the server served before and after it.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const SIGNALS = ['none', 'list_changed'];
export const CHANGES = ['description', 'schema', 'name'];

export function writeControl(file, control) {
  const tmp = path.join(path.dirname(file), `.${path.basename(file)}.${process.pid}.tmp`);
  fs.writeFileSync(tmp, JSON.stringify(control) + '\n');
  fs.renameSync(tmp, file);
}

export function readControl(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function parseArgs(argv) {
  const a = { control: null, log: process.env.Q3_LOG || path.join(os.homedir(), 'q3-server.log'), init: false,
    to: null, signal: 'none', change: 'description', cell: '', ttlMs: 0, declareListChanged: true };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (k === '--control') a.control = path.resolve(argv[++i]);
    else if (k === '--log') a.log = path.resolve(argv[++i]);
    else if (k === '--init') a.init = true;
    else if (k === '--to') a.to = argv[++i];
    else if (k === '--signal') a.signal = argv[++i];
    else if (k === '--change') a.change = argv[++i];
    else if (k === '--cell') a.cell = argv[++i];
    else if (k === '--ttl-ms') a.ttlMs = Number(argv[++i]);
    else if (k === '--no-list-changed') a.declareListChanged = false;
    else if (k === '-h' || k === '--help') a.help = true;
  }
  return a;
}

export function main(argv) {
  const a = parseArgs(argv);
  if (a.help || !a.control || (!a.init && !a.to)) {
    console.log('usage: flip.mjs --control <file> (--init [--signal none|list_changed] [--change description|schema|name] [--cell id] | --to A|B)');
    return a.help ? 0 : 2;
  }
  let control;
  if (a.init) {
    if (!SIGNALS.includes(a.signal)) throw new Error(`--signal must be one of ${SIGNALS}`);
    if (!CHANGES.includes(a.change)) throw new Error(`--change must be one of ${CHANGES}`);
    if (!Number.isInteger(a.ttlMs) || a.ttlMs < 0) throw new Error('--ttl-ms must be an integer >= 0');
    control = { state: 'A', signal: a.signal, change: a.change, declareListChanged: a.declareListChanged, ttlMs: a.ttlMs, cell: a.cell };
  } else {
    if (!['A', 'B'].includes(a.to)) throw new Error('--to must be A or B');
    control = { ...readControl(a.control), state: a.to };
  }
  writeControl(a.control, control);
  fs.appendFileSync(a.log, JSON.stringify({ ts: new Date().toISOString(), pid: process.pid, event: a.init ? 'control-init' : 'control-flip',
    cell: control.cell, state: control.state, control }) + '\n');
  return 0;
}

if (import.meta.url === `file://${process.argv[1]}`) process.exit(main(process.argv.slice(2)));

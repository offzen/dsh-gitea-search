import { test } from 'node:test';
import assert from 'node:assert/strict';
import { redact, run, runLines } from '../lib/git.mjs';

// A child that writes every CJK line in two writes: the first cuts a 3-byte
// character in half, the second completes it, the newline arrives later. A
// reader that decodes each write separately produces U+FFFD here.
const chunkedCjk = [
  'node', '-e', [
    "const line = Buffer.from('中文内容测试漢字'.repeat(2000));",
    'for (let i = 0; i < line.length; i += 24) {',
    '  process.stdout.write(line.subarray(i, i + 7));',
    '  process.stdout.write(line.subarray(i + 7, i + 24));',
    "  process.stdout.write('\\n');",
    '}',
  ].join('\n'),
];

const wholeCjk = [
  'node', '-e', "process.stdout.write('中文内容测试漢字'.repeat(4000));",
];

test('runLines decodes chunk-split CJK lines without U+FFFD', async () => {
  const { lines, code } = await runLines(chunkedCjk[0], chunkedCjk.slice(1), { cap: 50000 });
  assert.equal(code, 0);
  assert.ok(lines.length >= 2000, `expected many lines, got ${lines.length}`);
  const all = lines.join('');
  assert.ok(!all.includes('\uFFFD'), 'line-stream decode produced U+FFFD');
  assert.ok(all.includes('中文内容'));
});

test('run() decodes whole-output CJK without U+FFFD', async () => {
  const { out, code } = await run(wholeCjk[0], wholeCjk.slice(1));
  assert.equal(code, 0);
  assert.ok(!out.includes('\uFFFD'), 'whole-output decode produced U+FFFD');
  assert.ok(out.includes('测试'));
});

test('runLines stops reading at the cap and reports capped', async () => {
  const { lines, capped, code } = await runLines(chunkedCjk[0], chunkedCjk.slice(1), { cap: 100 });
  assert.equal(capped, true);
  assert.ok(lines.length <= 100, 'read past the cap');
  assert.notEqual(code, 0); // killed
});

test('redact removes credentials from URLs in error text', () => {
  assert.equal(redact('https://oauth2:secret@example.com/x.git'), 'https://***@example.com/x.git');
  assert.equal(redact('git clone: https://user:secret@host/y.git failed'), 'git clone: https://***@host/y.git failed');
});
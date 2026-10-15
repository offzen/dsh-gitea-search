import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createLimiter, truncateUtf8 } from '../lib/limits.mjs';

const bytes = (s) => Buffer.byteLength(s, 'utf8');

test('truncateUtf8 never leaves a dangling sequence (including a lone lead byte)', () => {
  const s = '中文内容abc漢字';
  for (let cut = 0; cut <= bytes(s); cut += 1) {
    const out = truncateUtf8(s, cut).toString('utf8');
    assert.ok(!out.includes('\uFFFD'), `cut=${cut} produced U+FFFD`);
    assert.ok(bytes(out) <= Math.max(0, cut), `cut=${cut} exceeded budget`);
  }
  // A buffer whose last byte is a lone leading byte (0xE4 = start of a 3-byte char).
  const buf = Buffer.from([0x61, 0xE4, 0xBD, 0xA0, 0xE4]); // 'a' + 你 + dangling lead
  assert.equal(truncateUtf8(buf, 5).toString('utf8'), 'a你');
});

test('capSearchLines truncates long lines in place, marker inside the quota', () => {
  const limiter = createLimiter({ maxLineBytes: 128, maxSearchBytes: 100000 });
  const long = 'x'.repeat(50000);
  const { lines, notes, stats } = limiter.capSearchLines(['header', long, 'tail']);
  assert.ok(lines.some((line) => line.includes('truncated')));
  for (const line of lines) assert.ok(bytes(line) <= 128, 'every line fits the per-line quota');
  assert.equal(stats.clippedLines, 1);
  assert.ok(notes.some((note) => note.includes('per-line limit')));
});

test('capSearchLines keeps total output at or under the budget including notes', () => {
  const limiter = createLimiter({ maxLineBytes: 2000, maxSearchBytes: 4096 });
  const lines = Array.from({ length: 200 }, (_, i) => `record ${i} ` + 'z'.repeat(200));
  const { lines: out, notes } = limiter.capSearchLines(lines);
  const total = [...out, ...notes].reduce((n, line) => n + bytes(line) + 1, 0);
  assert.ok(total <= 4096, `total ${total} exceeds the hard budget`);
  assert.ok(out.length < 200, 'rows were dropped to fit');
  assert.ok(notes.some((note) => note.includes('dropped')));
});

test('tiny budget degrades to a single minimal note inside the budget', () => {
  const limiter = createLimiter({ maxLineBytes: 64, maxSearchBytes: 90 });
  const { lines, notes } = limiter.capSearchLines(['a'.repeat(300), 'b'.repeat(300)]);
  assert.equal(lines.length, 0);
  assert.equal(notes.length, 1);
  const total = bytes(notes[0]) + 1;
  assert.ok(total <= 90, `note ${total} bytes exceeds the budget`);
});
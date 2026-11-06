import { test } from 'node:test';
import assert from 'node:assert/strict';
import { failureResult } from '../lib/next-actions.mjs';

for (const [raw, context, expected] of [
  [{ code: 2, err: 'CONFIG_ERROR: owners missing' }, 'search', 'CONFIG_ERROR'],
  [{ code: -1, err: 'spawn ENOENT' }, 'repos', 'CLI_START_FAILED'],
  [{ code: -1, err: 'EACCES' }, 'search', 'CLI_START_FAILED'],
  [{ code: null, timedOut: true }, 'search', 'PROCESS_TIMEOUT'],
  [{ code: 2, err: 'SYNC_FAILED: exit=1' }, 'search', 'SYNC_FAILED'],
  [{ code: 2, err: 'SYNC_UNAVAILABLE' }, 'search', 'SYNC_UNAVAILABLE'],
  [{ code: 2, err: 'MIRROR_MISSING' }, 'repos', 'MIRROR_MISSING'],
  [{ code: 2, err: '索引库不存在' }, 'docs', 'INDEX_UNAVAILABLE'],
  [{ code: 1, err: 'ModuleNotFoundError: jieba' }, 'docs', 'DEPENDENCY_MISSING'],
  [{ code: 0, out: 'wrong tool' }, 'search', 'INVALID_CLI_OUTPUT'],
  [{ code: 2, err: 'unclassified marker' }, 'search', 'UNKNOWN_FAILURE'],
]) {
  test(`failure attribution: ${expected}`, () => {
    const result = failureResult(raw, context);
    assert.equal(result.isError, true);
    assert.ok(result.text.startsWith(expected + ':'));
    if (raw.err) assert.ok(result.text.includes(raw.err));
    assert.match(result.text, /POST-INSTALL\.md/);
    assert.doesNotMatch(result.text, /\/C:\//);
  });
}

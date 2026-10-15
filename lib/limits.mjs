/**
 * Output bounding.
 *
 * A single line of a minified bundle can be hundreds of kilobytes; returning a
 * handful of such lines once produced ~1.2 MB (roughly 350k tokens) in a single
 * tool result. The limits are therefore layered:
 *
 *   maxResults      - how many records may be requested (soft on the model side)
 *   maxLineBytes    - per line, truncated in place with a visible marker
 *   maxSearchBytes  - total bytes of one tool result, enforced as a hard promise
 *   maxReadBytes    - file reads
 *
 * The marker for a truncated line counts *inside* the per-line quota, and the
 * guard notes count *inside* the total budget, so "never exceeds the budget" is
 * an invariant rather than an approximation.
 */

/**
 * Truncate a string to at most `maxBytes` UTF-8 bytes without leaving a
 * dangling multi-byte sequence - including a lone leading byte, which is what
 * produces U+FFFD after the decode.
 */
export function truncateUtf8(text, maxBytes) {
  let buf = Buffer.from(String(text), 'utf8').subarray(0, Math.max(0, maxBytes));
  while (buf.length > 0 && (buf[buf.length - 1] & 0xc0) === 0x80) buf = buf.subarray(0, buf.length - 1);
  while (buf.length > 0 && (buf[buf.length - 1] & 0xc0) === 0xc0) buf = buf.subarray(0, buf.length - 1);
  return buf;
}

const byteLength = (s) => Buffer.byteLength(s, 'utf8');

/**
 * Build a limiter bound to one configuration.
 * @param {{maxLineBytes:number, maxSearchBytes:number}} options
 */
export function createLimiter({ maxLineBytes, maxSearchBytes }) {
  /**
   * @param {string[]} lines result lines, already assembled (header + records)
   * @returns {{lines:string[], notes:string[], stats:object}} bounded result
   */
  function capSearchLines(lines) {
    const kept = [];
    const notes = [];
    let used = 0;
    let clippedLines = 0;
    let clippedBytes = 0;
    let dropped = 0;
    let budgetDropped = 0;

    for (const raw of lines) {
      let line = raw;
      const rawLen = byteLength(line);
      if (rawLen > maxLineBytes) {
        let tag = ` [...truncated: ${rawLen}B line > ${maxLineBytes}B limit]`;
        if (byteLength(tag) >= maxLineBytes) tag = ` [...${rawLen}B]`;
        const budget = maxLineBytes - byteLength(tag);
        line = (budget > 0 ? truncateUtf8(line, budget).toString('utf8') : '') + tag;
        clippedLines += 1;
        clippedBytes += rawLen - byteLength(line);
      }
      const lineLen = byteLength(line);
      if (used + lineLen > maxSearchBytes) {
        dropped += 1;
        continue;
      }
      kept.push(line);
      used += lineLen;
    }

    if (clippedLines > 0) {
      notes.push(
        `[guard] ${clippedLines} line(s) exceeded the ${maxLineBytes}B per-line limit and were truncated`
        + ` (~${(clippedBytes / 1024).toFixed(0)}KB saved). These are usually minified or binary-ish files:`
        + ' use files_only=true first, then read_gitea_file for the file itself (60KB cap by default).',
      );
    }
    if (dropped > 0) {
      notes.push(
        `[guard] total output reached ${maxSearchBytes}B; ${dropped} trailing line(s) were dropped.`
        + ' Narrow the query, use files_only=true, or raise GITEA_MAX_SEARCH_BYTES.',
      );
    }

    // The notes themselves live inside the budget: reserve the widest possible
    // note first, drop records until everything fits, then finalise.
    const total = (arr) => arr.reduce((n, l) => n + byteLength(l) + 1, 0);
    const reserveNote = (n) =>
      `[guard] dropped ${n} more line(s) to fit this note inside the ${maxSearchBytes}B budget; raise GITEA_MAX_SEARCH_BYTES.`;
    while (
      kept.length > 0
      && total(kept) + total(notes) + byteLength(reserveNote(999999)) > maxSearchBytes
    ) {
      kept.pop();
      budgetDropped += 1;
    }
    if (budgetDropped > 0) notes.push(reserveNote(budgetDropped));
    while (kept.length > 0 && total([...kept, ...notes]) > maxSearchBytes) kept.pop();
    if (total(notes) > maxSearchBytes) {
      notes.length = 0;
      const minimal = `[guard] budget ${maxSearchBytes}B is too small for any detail; raise GITEA_MAX_SEARCH_BYTES.`;
      notes.push(
        byteLength(minimal) <= maxSearchBytes ? minimal : truncateUtf8(minimal, maxSearchBytes).toString('utf8'),
      );
    }
    return {
      lines: kept,
      notes,
      stats: { clippedLines, clippedBytes, dropped, budgetDropped },
    };
  }

  return { capSearchLines, truncateUtf8, maxLineBytes, maxSearchBytes };
}

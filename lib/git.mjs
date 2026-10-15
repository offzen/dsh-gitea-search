/**
 * Subprocess helpers.
 *
 * Two rules learned the hard way and encoded here:
 *
 *  1. Collect Buffers and decode once. Accumulating `out += chunk` (or decoding
 *     each 'data' event) splits multi-byte characters at chunk boundaries and
 *     permanently produces U+FFFD, because `child_process` delivers whatever
 *     the child wrote per `write()` - commonly 8 KiB - not per character.
 *  2. A line-oriented reader must decode only *complete* lines. Slicing on the
 *     newline first means every decoded buffer ends at a character boundary.
 */
import { spawn } from 'node:child_process';

/** Git invocation flags shared by every call: no pager, no colour, no path quoting. */
export const GIT_BASE = [
  '-c', 'core.pager=cat',
  '-c', 'color.ui=false',
  // Without this, non-ASCII paths come back as "\\345\\257\\274..." and file
  // reads by the path we just printed no longer resolve.
  '-c', 'core.quotePath=false',
];

/** Remove credentials from a URL so nothing secret can reach a log line. */
export function redact(text) {
  return String(text || '').replace(/(\w+:\/\/)[^/@\s]+@/g, '$1***@');
}

function decode(chunks) {
  return Buffer.concat(chunks).toString('utf8');
}

/**
 * Run a command and capture its output.
 * @returns {Promise<{code:number, out:string, err:string, timedOut:boolean}>}
 */
export function run(cmd, args, { timeoutMs = 120000, env, cwd, input } = {}) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(cmd, args, {
        env: env ? { ...process.env, ...env } : process.env,
        cwd,
        stdio: [input === undefined ? 'ignore' : 'pipe', 'pipe', 'pipe'],
      });
    } catch (error) {
      resolve({ code: -1, out: '', err: String(error), timedOut: false });
      return;
    }
    const outChunks = [];
    const errChunks = [];
    let settled = false;
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill('SIGKILL');
    }, timeoutMs);

    if (input !== undefined && child.stdin) {
      child.stdin.on('error', () => {});
      child.stdin.end(input);
    }
    child.stdout.on('data', (d) => outChunks.push(d));
    child.stderr.on('data', (d) => errChunks.push(d));
    const finish = (code, extraErr = '') => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({
        code,
        out: decode(outChunks),
        err: redact(extraErr + decode(errChunks)),
        timedOut,
      });
    };
    child.on('error', (e) => finish(-1, String(e)));
    child.on('close', (code) => finish(timedOut ? -1 : code));
  });
}

/**
 * Read at most `cap` lines of a command's stdout, then stop it.
 * Used for the wide `git grep` / `git log` scans where a single call can
 * otherwise produce hundreds of megabytes.
 * @returns {Promise<{lines:string[], capped:boolean, code:number}>}
 */
export function runLines(cmd, args, { cap = 1000, timeoutMs = 120000, env, cwd } = {}) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(cmd, args, {
        env: env ? { ...process.env, ...env } : process.env,
        cwd,
        stdio: ['ignore', 'pipe', 'ignore'],
      });
    } catch {
      resolve({ lines: [], capped: false, code: -1 });
      return;
    }
    const lines = [];
    let pending = Buffer.alloc(0);
    let capped = false;
    let settled = false;
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill('SIGKILL');
    }, timeoutMs);

    const finish = (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (pending.length > 0 && lines.length < cap) lines.push(pending.toString('utf8'));
      resolve({ lines, capped, code: timedOut ? -1 : code });
    };

    child.stdout.on('data', (chunk) => {
      if (settled) return;
      pending = pending.length ? Buffer.concat([pending, chunk]) : chunk;
      let index = pending.indexOf(0x0a);
      while (index !== -1) {
        const line = pending.subarray(0, index);
        pending = pending.subarray(index + 1);
        lines.push(line.toString('utf8').replace(/\r$/, ''));
        if (lines.length >= cap) {
          capped = true;
          child.kill('SIGKILL');
          finish(-1);
          return;
        }
        index = pending.indexOf(0x0a);
      }
    });
    child.on('error', () => finish(-1));
    child.on('close', (code) => finish(code));
  });
}

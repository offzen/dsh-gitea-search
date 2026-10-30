#!/usr/bin/env node
/**
 * W7 —— IndexLock 运行时排他性测试（真实 Python 子进程，非静态断言）。
 *
 * 为什么需要这个文件：
 *   test/windows-adapt.test.mjs 只断言「源码里有 msvcrt.locking 这段代码」，
 *   属于**静态自证** —— 代码写错了也会绿。本文件用两个真实 Python 进程
 *   争抢同一把锁，断言「第二个进程必须抢不到」，这才是运行时证据。
 *
 * 覆盖：
 *   - POSIX 路径（fcntl.flock）：Linux / CI ubuntu 即可跑。
 *   - Windows 路径（msvcrt.locking）：仅在 win32 上跑，判据与 POSIX 完全一致。
 *   两条路径共用同一份 Python 代码，所以本测试在哪个平台跑，就验证哪个平台的实现。
 *
 * 运行：node --test test/windows-lock.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const INDEXER = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../lib/indexer.py');
const IS_WIN = process.platform === 'win32';

/** 找一个可用的 python 解释器；找不到则 skip（不假装通过）。 */
function findPython() {
  for (const exe of IS_WIN ? ['python', 'py', 'python3'] : ['python3', 'python']) {
    const r = spawnSync(exe, ['-c', 'print(1)'], { encoding: 'utf8' });
    if (r.status === 0) return exe;
  }
  return null;
}

const PY = findPython();
const SKIP = PY ? false : '找不到 python 解释器 —— 环境缺失，不算通过';

/**
 * 子进程脚本：抢锁 -> 打印 ACQUIRED -> 可选持锁若干秒 -> 退出。
 * 抢不到 -> 打印 LOCKED -> exit 3。
 */
const CHILD = `
import sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[1])))
import indexer
try:
    with indexer.IndexLock(sys.argv[2]):
        print("ACQUIRED", flush=True)
        time.sleep(float(sys.argv[3]))
except RuntimeError:
    print("LOCKED", flush=True)
    sys.exit(3)
`;

function tmpRoot(tag) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `dgs-lock-${tag}-`));
}

test('W7-1 IndexLock 可获取并正常释放', { skip: SKIP }, () => {
  const root = tmpRoot('a');
  const r = spawnSync(PY, ['-c', CHILD, INDEXER, root, '0'], { encoding: 'utf8', timeout: 30_000 });
  assert.equal(r.status, 0, `子进程异常：${r.stderr}`);
  assert.match(r.stdout, /ACQUIRED/);
});

test('W7-2 持锁期间第二个进程必须被拒（核心判据）', { skip: SKIP }, async () => {
  const root = tmpRoot('b');

  // A 异步起，持锁 3 秒
  const a = spawn(PY, ['-c', CHILD, INDEXER, root, '3'], { stdio: ['ignore', 'pipe', 'pipe'] });
  let aOut = '';
  a.stdout.on('data', (d) => { aOut += d; });

  // 等 A 真正拿到锁
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`A 未在 10s 内 ACQUIRED，out=${aOut}`)), 10_000);
    a.stdout.on('data', () => {
      if (aOut.includes('ACQUIRED')) { clearTimeout(timer); resolve(); }
    });
  });

  // B 在 A 持锁期间尝试抢 —— 必须失败
  const b = await new Promise((resolve) => {
    const p = spawn(PY, ['-c', CHILD, INDEXER, root, '0'], { stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    p.stdout.on('data', (d) => { out += d; });
    p.on('close', (code) => resolve({ code, out }));
  });

  assert.equal(b.code, 3, `第二个进程本应抢锁失败(exit 3)，实际 code=${b.code} out=${b.out}`);
  assert.match(b.out, /LOCKED/, '第二个进程必须报告 LOCKED');

  await new Promise((resolve) => { a.on('close', resolve); });
});

test('W7-3 释放后可重新获取', { skip: SKIP }, () => {
  const root = tmpRoot('c');
  const r1 = spawnSync(PY, ['-c', CHILD, INDEXER, root, '0'], { encoding: 'utf8', timeout: 30_000 });
  const r2 = spawnSync(PY, ['-c', CHILD, INDEXER, root, '0'], { encoding: 'utf8', timeout: 30_000 });
  assert.match(r1.stdout, /ACQUIRED/);
  assert.match(r2.stdout, /ACQUIRED/, '释放后应可重新获取，否则锁永远不会被复用');
});

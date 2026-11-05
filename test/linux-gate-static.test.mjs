#!/usr/bin/env node
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const gate = fs.readFileSync(new URL('../scripts/ci/linux-gate.sh', import.meta.url), 'utf8');
const workflow = fs.readFileSync(new URL('../.gitea/workflows/ci.yml', import.meta.url), 'utf8');
const preflight = fs.readFileSync(new URL('../scripts/release-preflight.py', import.meta.url), 'utf8');
const receiptWriter = fs.readFileSync(new URL('../.github/scripts/write-gate-receipt.py', import.meta.url), 'utf8');
const githubCi = fs.readFileSync(new URL('../.github/workflows/ci.yml', import.meta.url), 'utf8');
const windowsCi = fs.readFileSync(new URL('../.github/workflows/windows-ci.yml', import.meta.url), 'utf8');
const docs = fs.readFileSync(new URL('../docs/DEVELOPMENT-AND-RELEASE.md', import.meta.url), 'utf8');

test('gate2 workflow is complete Linux authority and binds event exact SHA', () => {
  assert.match(workflow, /name: gate2 Linux CI/);
  assert.match(workflow, /runs-on: ubuntu-latest/);
  assert.match(workflow, /scripts\/ci\/linux-gate\.sh "\$\{\{ github\.sha \}\}"/);
  // upload-artifact@v4 is unsupported by the Gitea act_runner
  // (GHESNotSupportedError), so the receipt is published into the job log.
  assert.doesNotMatch(workflow, /^\s*uses:\s*actions\/upload-artifact/m);
  assert.match(workflow, /receipt_sha256=/);
  assert.match(workflow, /--- receipt begin ---/);
  assert.doesNotMatch(workflow, /windows-latest|self-hosted|vtwsl/i);
});

test('Linux gate strips ambient aliases and keeps receipt outside checkout', () => {
  for (const name of [
    'GITEA_MIRROR_ROOT', 'GITEA_MIRROR_PATH', 'GITQ_MIRROR_ROOT',
    'GITEA_OWNERS', 'GITEA_OWNER', 'GITQ_OWNER', 'GITEA_SEARCH_CONFIG',
  ]) assert.match(gate, new RegExp(name));
  assert.match(gate, /\^\[0-9a-f\]\{40\}\$/);
  assert.match(gate, /receipt path must be outside the tracked checkout/);
  assert.match(gate, /"gate": "gate2-linux"/);
});

test('release preflight accepts only hosted clean Windows evidence', () => {
  assert.match(preflight, /ALLOWED_WINDOWS_PROVIDERS = \{"github-actions-hosted"\}/);
  assert.match(preflight, /github-hosted-clean-windows/);
  assert.match(preflight, /P must change only PROVENANCE\.json/);
  assert.match(preflight, /PROVENANCE source_commit must equal D/);
  assert.match(docs, /Historical rc3, vtWSL, self-hosted, Task, Broker, or local-canary evidence is rejected/);
});

test('GitHub workflows emit exact-SHA receipts only after required hosted jobs', () => {
  assert.match(receiptWriter, /formal repository identity mismatch/);
  assert.match(receiptWriter, /provider": "github-actions-hosted/);
  assert.match(receiptWriter, /workflow_dispatch exact_sha does not match checkout/);
  assert.match(githubCi, /exact_sha:/);
  assert.match(windowsCi, /exact_sha:/);
  assert.match(githubCi, /hosted-linux-receipt:[\s\S]*needs: \[test, package\]/);
  assert.match(githubCi, /hosted-macos-receipt:[\s\S]*needs: \[test-macos\]/);
  assert.match(githubCi, /--gate github-linux/);
  assert.match(githubCi, /--gate github-macos/);
  assert.match(windowsCi, /needs: \[windows-pe, windows-ps, windows-tests, windows-git-bash, windows-grep-crlf\]/);
  assert.match(windowsCi, /--gate github-windows/);
});

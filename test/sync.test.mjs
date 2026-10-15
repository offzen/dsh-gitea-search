import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { loadConfig } from '../lib/config.mjs';
import { listRemoteRepos } from '../lib/sync.mjs';
import { redact } from '../lib/git.mjs';

function repoJson(name, owner) {
  return {
    full_name: `${owner}/${name}`,
    name,
    owner: { login: owner },
    clone_url: `https://gitea.example/${owner}/${name}.git`,
    ssh_url: `git@gitea.example:${owner}/${name}.git`,
  };
}

async function fakeGitea() {
  const authSeen = [];
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    authSeen.push(req.headers.authorization || '');
    res.setHeader('content-type', 'application/json');
    if (url.pathname === '/api/v1/repos/search') {
      const page = Number(url.searchParams.get('page') || 1);
      const names = page === 1
        ? Array.from({ length: 50 }, (_, i) => `r${String(i).padStart(3, '0')}`)
        : page === 2
          ? Array.from({ length: 25 }, (_, i) => `r${String(50 + i).padStart(3, '0')}`)
          : [];
      res.end(JSON.stringify({ data: names.map((name) => repoJson(name, 'root')) }));
      return;
    }
    const ownerMatch = url.pathname.match(/^\/api\/v1\/(users|orgs)\/([^/]+)\/repos$/);
    if (ownerMatch) {
      const [, kind, owner] = ownerMatch;
      if (owner === 'none' && kind === 'users') {
        res.statusCode = 404;
        res.end(JSON.stringify({ message: 'user does not exist' }));
        return;
      }
      const names = owner === 'alice' ? ['alpha', 'beta', 'gamma'] : ['org-a', 'org-b'];
      res.end(JSON.stringify(names.map((name) => repoJson(name, owner))));
      return;
    }
    res.statusCode = 404;
    res.end(JSON.stringify({ message: 'not found' }));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return {
    port: server.address().port,
    authSeen,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

test('listRemoteRepos paginates /api/v1/repos/search', async (t) => {
  const gitea = await fakeGitea();
  t.after(() => gitea.close());
  const config = loadConfig({ GITEA_BASE_URL: `http://127.0.0.1:${gitea.port}`, GITEA_TOKEN: 'tok-123' });
  const repos = await listRemoteRepos(config);
  assert.equal(repos.length, 75);
  assert.equal(repos[0].full_name, 'root/r000');
  assert.equal(repos[74].full_name, 'root/r074');
});

test('GITEA_OWNER filters and the users endpoint falls back to orgs', async (t) => {
  const gitea = await fakeGitea();
  t.after(() => gitea.close());
  const base = { GITEA_BASE_URL: `http://127.0.0.1:${gitea.port}`, GITEA_TOKEN: 'tok-123' };
  const alice = await listRemoteRepos(loadConfig({ ...base, GITEA_OWNER: 'alice' }));
  assert.deepEqual(alice.map((r) => r.full_name).sort(), ['alice/alpha', 'alice/beta', 'alice/gamma']);

  const fallback = await listRemoteRepos(loadConfig({ ...base, GITEA_OWNER: 'none' }));
  assert.deepEqual(fallback.map((r) => r.full_name).sort(), ['none/org-a', 'none/org-b']);
});

test('listRemoteRepos requires GITEA_BASE_URL and sends the token header', async (t) => {
  await assert.rejects(() => listRemoteRepos(loadConfig({})), /GITEA_BASE_URL/);
  const gitea = await fakeGitea();
  t.after(() => gitea.close());
  const config = loadConfig({ GITEA_BASE_URL: `http://127.0.0.1:${gitea.port}`, GITEA_TOKEN: 'tok-123' });
  const repos = await listRemoteRepos(config);
  assert.ok(repos.length > 0);
  assert.ok(gitea.authSeen.some((header) => header === 'token tok-123'), 'the token must travel in the Authorization header');
});

test('redact strips credentials from URLs', () => {
  assert.equal(redact('https://oauth2:secret@example.com/x.git'), 'https://***@example.com/x.git');
  assert.equal(redact('plain text, no credentials'), 'plain text, no credentials');
});
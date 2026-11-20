[CmdletBinding()]
param(
  [string]$Query = 'gitea',
  [switch]$Verify,
  [switch]$Help
)
$ErrorActionPreference = 'Stop'
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ($Help) {
  @'
Usage: powershell -File scripts/doctor-mcp.ps1 [-Query WORD] [-Verify]
Checks the standalone MCP stdio server and performs a read-only SQLite FTS5 user_version check;
it never modifies config or rebuilds indexes, and cannot inspect the host's registered tools.
'@ | Write-Output
  exit 0
}
$gts = if ($env:GITEA_SEARCH_CLI) { $env:GITEA_SEARCH_CLI } elseif ($env:GTS_BIN) { $env:GTS_BIN } else { (Get-Command gts -ErrorAction SilentlyContinue).Source }
if (-not $gts) { $gts = Join-Path $RootDir 'bin\gts' }
$env:GITEA_SEARCH_CLI = $gts
$env:GTS_BIN = $gts
Write-Output 'MCP bare tool names: search_code, search_docs, read_file, list_repositories'
Write-Output 'Legacy tool aliases accepted on tools/call: search_gitea*, search_gitorca*'
Write-Output 'Host naming example (not guaranteed): mcp__gitea-search__search_code'
Write-Output "Checking standalone server; this does not verify host registration. Query=$Query"

# Read-only SQLite FTS5 index PRAGMA user_version precheck (never mutates files).
$mirrorRoot = if ($env:GITEA_MIRROR_ROOT) { $env:GITEA_MIRROR_ROOT } elseif ($env:GITEA_MIRROR_PATH) { $env:GITEA_MIRROR_PATH } elseif ($env:GITQ_MIRROR_ROOT) { $env:GITQ_MIRROR_ROOT } else { '' }
if (-not $mirrorRoot -and (Test-Path -LiteralPath $gts -PathType Leaf)) {
  try {
    $cfgJson = & $gts --show-effective-config 2>$null
    if ($LASTEXITCODE -eq 0 -and $cfgJson) { $mirrorRoot = ($cfgJson | ConvertFrom-Json).mirror_root }
  } catch { }
}
$ftsDb = if ($mirrorRoot) { Join-Path (Join-Path $mirrorRoot 'state') 'gitea-fts.sqlite' } else { '' }
if ($ftsDb -and (Test-Path -LiteralPath $ftsDb -PathType Leaf)) {
  $pyCmd = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } elseif (Get-Command python3 -ErrorAction SilentlyContinue) { 'python3' } else { '' }
  if ($pyCmd) {
    $checkCode = @'
import pathlib, sqlite3, sys
db_path = pathlib.Path(sys.argv[1]).resolve()
try:
    uri = db_path.as_uri() + '?mode=ro'
    con = sqlite3.connect(uri, uri=True, timeout=2.0)
    version = int(con.execute('PRAGMA user_version').fetchone()[0])
    try:
        has_docs = con.execute('SELECT 1 FROM docs_fts LIMIT 1').fetchone() is not None
    except sqlite3.OperationalError:
        has_docs = False
    con.close()
    if version != 2 and has_docs:
        print(f'WARN: SQLite FTS5 index {db_path} has PRAGMA user_version={version} (expected 2); 需执行一次全量重建：gts --reindex --full')
    else:
        state = 'OK' if version == 2 else 'empty'
        print(f'SQLite FTS5 index identity: user_version={version} ({state}) [{db_path}]')
except Exception as exc:
    print(f'WARN: unable to inspect SQLite FTS5 index {db_path} read-only: {exc}')
'@
    & $pyCmd -c $checkCode $ftsDb
  } else {
    Write-Output "SQLite FTS5 index check: python unavailable to inspect $ftsDb read-only."
  }
} else {
  Write-Output 'SQLite FTS5 index check: no local index found (read-only check skipped).'
}

& node (Join-Path $RootDir 'scripts\probe-stdio.mjs') (Join-Path $RootDir 'bin\mcp-server') $Query
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output 'If the probe passes, reload your MCP host (Claude Desktop / Cursor / Cline / DSH) and verify its actual tool table.'

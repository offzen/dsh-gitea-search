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
Checks the standalone MCP stdio server; it cannot inspect the host's registered tools.
'@ | Write-Output
  exit 0
}
$gts = if ($env:GTS_BIN) { $env:GTS_BIN } else { (Get-Command gts -ErrorAction SilentlyContinue).Source }
if (-not $gts) { $gts = Join-Path $RootDir 'bin\gts' }
$env:GTS_BIN = $gts
Write-Output 'MCP bare tool names: search_gitea, search_gitea_docs, read_gitea_file, list_gitea_repos'
Write-Output 'Host naming example (not guaranteed): mcp__gitea-search__search_gitea'
Write-Output "Checking standalone server; this does not verify DSH host registration. Query=$Query"
& node (Join-Path $RootDir 'scripts\probe-stdio.mjs') (Join-Path $RootDir 'bin\mcp-server') $Query
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output 'If the probe passes, reload the selected DSH profile and verify its actual tool table.'

[CmdletBinding()]
param(
  [switch]$Verify,
  [switch]$Help
)
$ErrorActionPreference = 'Stop'
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ($Help) {
  @'
Usage: powershell -File scripts/onboarding.ps1 [-Verify]
Read-only post-install checks. Never syncs, indexes, edits profiles, or reloads DSH.
'@ | Write-Output
  exit 0
}
$gts = if ($env:GTS_BIN) { $env:GTS_BIN } else { (Get-Command gts -ErrorAction SilentlyContinue).Source }
if (-not $gts) { $gts = Join-Path $RootDir 'bin\gts' }
if (-not (Test-Path -LiteralPath $gts -PathType Leaf)) {
  Write-Error "FAIL: gts CLI was not found: $gts. Run scripts\setup-windows.ps1 first."
  exit 1
}
$configText = & $gts --show-effective-config
if ($LASTEXITCODE -ne 0) { throw 'gts --show-effective-config failed.' }
$config = $configText | ConvertFrom-Json
if (-not $config.valid) {
  Write-Error 'FAIL: effective config is invalid. Set GITEA_OWNERS/GITEA_MIRROR_ROOT in the host profile and reload it.'
  exit 1
}
Write-Output 'Configuration: valid'
Write-Output "Mirror root: $($config.mirror_root)"
Write-Output "Owners: $($config.owners -join ',')"
$reposText = & $gts --repos --json
if ($LASTEXITCODE -ne 0) { throw 'gts --repos failed.' }
$repos = $reposText | ConvertFrom-Json
Write-Output "Mirrored repositories: $($repos.count)"
if ($repos.count -eq 0) {
  Write-Warning 'No mirrored repositories are available.'
  Write-Output 'Next (explicit write): use Git Bash to run scripts/sync.sh -q; this PowerShell helper does not sync.'
}
$doctorText = & $gts --doctor --json
if ($LASTEXITCODE -ne 0) { throw 'gts --doctor failed.' }
$doctor = $doctorText | ConvertFrom-Json
$ftsDb = Join-Path (Join-Path $config.mirror_root 'state') 'gitea-fts.sqlite'
Write-Output ("FTS index: " + $(if ($doctor.fts_index.exists) { 'present' } else { 'missing' }))
Write-Output ("FTS alignment: " + $(if ($doctor.fts_index.aligned_1to1) { 'aligned' } else { 'check required' }))
if (-not (Test-Path -LiteralPath $ftsDb)) { Write-Output 'Next (explicit write): after verifying the mirror, run gts --reindex.' }
Write-Output "MCP check: node `"$RootDir\scripts\probe-stdio.mjs`" (checks standalone stdio server, not host registration)."
Write-Output 'Apply/reload: host profile changes take effect after the DSH host reloads that profile.'
Write-Output 'Rollback: restore the timestamped cordis.patch.yml backup, then reload the profile.'
Write-Output 'No synchronization, indexing, profile mutation, or DSH restart was performed.'

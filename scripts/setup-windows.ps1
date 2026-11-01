<#
.SYNOPSIS
  Installs dsh-gitea-search on native Windows and optionally registers one or more DSH profiles.
.NOTES
  SPDX-License-Identifier: Apache-2.0
#>
[CmdletBinding(SupportsShouldProcess=$true)]
param(
  [string]$MirrorRoot = "",
  [Parameter(Mandatory=$true)] [string]$Owners,
  [switch]$SkipIndex,
  [switch]$RegisterDsh,
  [ValidateSet('Auto','desktop','web','headless','all')]
  [string]$Profile = 'Auto',
  [string]$DshHome = ""
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = (Resolve-Path (Join-Path $ScriptDir '..')).Path
if (-not $MirrorRoot) { $MirrorRoot = Join-Path $RootDir 'mirror' }
if ([string]::IsNullOrWhiteSpace($Owners) -or $Owners -match '^(myorg|example-org|your-org|your-owner)$') {
  throw 'CONFIG_ERROR: -Owners is required and must not be a placeholder.'
}
if ($PSBoundParameters.ContainsKey('DshHome') -and [string]::IsNullOrWhiteSpace($DshHome)) {
  throw 'CONFIG_ERROR: -DshHome must not be empty when explicitly supplied.'
}

function Write-Step([string]$Message) { Write-Host "`n=== $Message ===" -ForegroundColor Cyan }
function Resolve-Python {
  foreach ($candidate in @(@('python', @()), @('py', @('-3')), @('python3', @()))) {
    $exe = $candidate[0]; $prefix = $candidate[1]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
      $version = & $exe @prefix -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
      if ($LASTEXITCODE -eq 0 -and $version) { return @($exe, $prefix) }
    } catch { }
  }
  throw 'Python >= 3.8 was not found.'
}
function Find-Gts([string]$PythonExe, [object[]]$PythonArgs) {
  $probe = @'
import importlib.metadata, pathlib, sys
candidates = [
    pathlib.Path(sys.executable).resolve().parent / 'gts.exe',
    pathlib.Path(sysconfig.get_path('scripts')) / 'gts.exe',
    pathlib.Path(sysconfig.get_path('scripts', scheme='nt_user')) / 'gts.exe',
]
try:
    dist = importlib.metadata.distribution('dsh-gitea-search')
    for entry in dist.entry_points:
        if entry.group == 'console_scripts' and entry.name == 'gts':
            candidates.append(pathlib.Path(sys.executable).resolve().parent / 'gts.exe')
except importlib.metadata.PackageNotFoundError:
    pass
for candidate in candidates:
    if candidate.is_file():
        print(candidate.resolve())
'@
  $probe = "import sysconfig;" + $probe
  $dirs = @()
  try {
    $values = & $PythonExe @PythonArgs -c $probe 2>$null
    if ($LASTEXITCODE -eq 0 -and $values) {
      foreach ($value in @($values)) {
        if ($value) { $dirs += (Split-Path -Parent $value.Trim()) }
      }
    }
  } catch { }
  $commands = Get-Command gts -All -ErrorAction SilentlyContinue
  foreach ($command in $commands) { if ($command.Source) { $dirs += (Split-Path -Parent $command.Source) } }
  foreach ($dir in ($dirs | Select-Object -Unique)) {
    $candidate = Join-Path $dir 'gts.exe'
    if (-not (Test-Path $candidate)) { continue }
    $help = & $candidate --help 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0 -and $help -match 'usage' -and $help -match 'gitea') {
      return (Resolve-Path $candidate).Path
    }
  }
  throw 'pip completed but a verified gts.exe was not found for the selected interpreter.'
}
function Find-Profiles([string]$DshHome, [string]$Mode) {
  $profilesRoot = Join-Path $DshHome 'profiles'
  $found = @()
  if (Test-Path $profilesRoot) {
    $found = @(Get-ChildItem -Path $profilesRoot -Directory -ErrorAction SilentlyContinue |
      Where-Object { Test-Path (Join-Path $_.FullName 'cordis.patch.yml') } |
      Select-Object -ExpandProperty Name)
  }
  if ($Mode -eq 'all') { return $found }
  if ($Mode -ne 'Auto') {
    if ($found -notcontains $Mode) { throw "Profile '$Mode' was not found under $profilesRoot." }
    return @($Mode)
  }
  if ($found.Count -ne 1) {
    throw "Profile Auto requires exactly one discovered profile; found: $($found -join ', '). Use -Profile explicitly."
  }
  return @($found[0])
}

Write-Step 'Checking Python, Git and Node.js'
$python = Resolve-Python
$PythonExe = $python[0]
$PythonArgs = if ($python.Count -gt 1 -and $python[1]) { @($python[1]) } else { @() }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Git for Windows was not found.' }
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) { throw 'Node.js >= 20.11 was not found.' }

$profileTargets = @()
if ($RegisterDsh) {
  if (-not $DshHome) {
    $DshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
  }
  $profileTargets = @(Find-Profiles $DshHome $Profile)
  Write-Host "DSH home: $DshHome"
  if (Test-Path (Join-Path $DshHome 'sessions')) {
    Write-Warning 'The selected DSH home contains sessions; profile changes may affect a live installation after reload.'
  }
  foreach ($name in $profileTargets) {
    Write-Host "Profile target: $(Join-Path $DshHome "profiles\$name\cordis.patch.yml")"
  }
}

Write-Step 'Installing the Python launcher'
if ($PSCmdlet.ShouldProcess($RootDir, 'pip install dsh-gitea-search')) {
  Push-Location $RootDir
  try {
    & $PythonExe @PythonArgs -m pip install .
    if ($LASTEXITCODE -ne 0) {
      & $PythonExe @PythonArgs -m pip install --user .
      if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }
    }
  } finally { Pop-Location }
}
if ($WhatIfPreference) {
  Write-Host 'WhatIf: skipping gts.exe discovery, indexing and profile writes.'
  exit 0
}
$gtsExe = Find-Gts $PythonExe $PythonArgs
Write-Host "gts.exe: $gtsExe"

Write-Step 'Preparing the mirror and index'
if ($PSCmdlet.ShouldProcess($MirrorRoot, 'create mirror directories')) {
  New-Item -ItemType Directory -Force -Path (Join-Path $MirrorRoot 'state') | Out-Null
}
if (-not $SkipIndex -and $PSCmdlet.ShouldProcess($MirrorRoot, 'build the FTS index')) {
  $oldRoot = $env:GITEA_MIRROR_ROOT; $oldOwners = $env:GITEA_OWNERS
  try {
    $env:GITEA_MIRROR_ROOT = $MirrorRoot
    $env:GITEA_OWNERS = $Owners
    & $PythonExe @PythonArgs (Join-Path $RootDir 'lib\indexer.py') --build
    if ($LASTEXITCODE -ne 0) { throw "index build exited $LASTEXITCODE" }
  } finally {
    $env:GITEA_MIRROR_ROOT = $oldRoot
    $env:GITEA_OWNERS = $oldOwners
  }
}

if ($RegisterDsh) {
  Write-Step "Registering DSH profile(s) under $DshHome"
  $patcher = Join-Path $RootDir 'scripts\profile-patch.py'
  foreach ($name in $profileTargets) {
    $patch = Join-Path $DshHome "profiles\$name\cordis.patch.yml"
    if ($PSCmdlet.ShouldProcess($patch, 'idempotently register mcp-gitea-search')) {
      & $PythonExe @PythonArgs $patcher --file $patch --node $node.Source --server (Join-Path $RootDir 'lib\server.mjs') --gts $gtsExe --mirror $MirrorRoot --owners $Owners
      if ($LASTEXITCODE -ne 0) { throw "profile update failed for $name" }
    }
  }
}

Write-Host "`nInstallation completed. No DSH service was restarted." -ForegroundColor Green
Write-Host 'Start or reload the selected profile at a user-approved time, then run the MCP smoke test.'

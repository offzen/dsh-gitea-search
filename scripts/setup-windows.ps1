<#
.SYNOPSIS
  setup-windows.ps1 —— dsh-gitea-search (原生 Windows 支持) 在原生 Windows 上的一键安装 + DSH 接入。

.DESCRIPTION
  与 Linux 的 setup.sh / macOS 的 setup-macos.sh 并列的 Windows 版本。

  为什么需要它（Windows 与 POSIX 的四个硬差异）：
    1. Windows 的 CreateProcess 只认 PE 文件，没有 shebang 机制 ⇒ bin\gts（Python 脚本、
       无扩展名）**无法**被 lib/server.mjs 的 spawn() 执行。必须提供真正的 .exe 入口。
       本脚本用 `pip install .` 让 setuptools 生成 <Python>\Scripts\gts.exe。
    2. Windows 官方安装器只提供 `python` / `py` 命令，**没有 `python3`**；且默认不勾选
       "Add Python to PATH"。所以本脚本优先用 `py -3`，再退 `python`。
    3. 符号链接（ln -s）需要管理员权限或开发者模式，脚本**整体跳过**，改靠 GTS_BIN 显式配置。
    4. setup.sh 的 apt-get / /usr/local/bin / chmod +x 在 Windows 上全部无效。

.PARAMETER MirrorRoot
  镜像根目录。默认 <项目>\mirror。

.PARAMETER Owners
  Gitea owner 列表（逗号分隔）。默认 myorg（占位符，正式使用必须改）。

.PARAMETER SkipIndex
  只装环境，不建 FTS5 索引。

.PARAMETER RegisterDsh
  额外把 MCP 配置片段写到 DSH profile（会先备份）。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 -Owners myorg -MirrorRoot C:\gitea-mirror

.NOTES
  SPDX-License-Identifier: Apache-2.0
#>
[CmdletBinding()]
param(
  [string]$MirrorRoot = "",
  [string]$Owners = "myorg",
  [switch]$SkipIndex,
  [switch]$RegisterDsh
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir   = (Resolve-Path (Join-Path $ScriptDir "..")).Path
if (-not $MirrorRoot) { $MirrorRoot = Join-Path $RootDir "mirror" }

function Write-Step([string]$msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "  [ok] $msg" -ForegroundColor Green }
function Write-Warn2([string]$msg){ Write-Host "  [warn] $msg" -ForegroundColor Yellow }
function Write-Fail([string]$msg) { Write-Host "  [fail] $msg" -ForegroundColor Red }

Write-Host "=== dsh-gitea-search (原生 Windows 支持) Windows 安装 ===" -ForegroundColor White
Write-Host "项目目录: $RootDir"
Write-Host "镜像目录: $MirrorRoot"
Write-Host "Owners  : $Owners"

# ── 1. 定位 Python ───────────────────────────────────────────────────────────
Write-Step "[1/6] 检查 Python"
$PythonExe = $null
$PythonArgs = @()
foreach ($cand in @(@("py", @("-3")), @("python", @()), @("python3", @()))) {
  $exe = $cand[0]; $pre = $cand[1]
  $cmd = Get-Command $exe -ErrorAction SilentlyContinue
  if (-not $cmd) { continue }
  try {
    $v = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $v) {
      $parts = $v.Trim().Split(".")
      if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 8) {
        $PythonExe = $exe; $PythonArgs = $pre
        Write-Ok "$exe $($pre -join ' ') -> Python $v ($($cmd.Source))"
        break
      }
    }
  } catch { }
}
if (-not $PythonExe) {
  Write-Fail "未找到 Python >= 3.8。"
  Write-Host "  请从 https://www.python.org/downloads/windows/ 安装，并**勾选 Add Python to PATH**。" -ForegroundColor Yellow
  Write-Host "  安装后重开一个 PowerShell 再运行本脚本。" -ForegroundColor Yellow
  exit 1
}

# ── 2. 检查 Git ──────────────────────────────────────────────────────────────
Write-Step "[2/6] 检查 Git"
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
  Write-Fail "未找到 git。请安装 Git for Windows: https://git-scm.com/download/win"
  exit 1
}
Write-Ok "git $(git --version)"

# ── 3. 检查 Node（MCP server 运行时）─────────────────────────────────────────
Write-Step "[3/6] 检查 Node.js"
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
  Write-Fail "未找到 node。MCP server 需要 Node.js >= 20.11。"
  Write-Host "  下载: https://nodejs.org/ (选 LTS)" -ForegroundColor Yellow
  exit 1
}
$nodeVer = (node -v).TrimStart("v")
Write-Ok "node v$nodeVer"

# ── 4. 安装 Python 包 → 生成 gts.exe ─────────────────────────────────────────
Write-Step "[4/6] 安装 gts CLI（生成 gts.exe）"
Write-Host "  说明：Windows 没有 shebang，bin\gts 无法被直接执行；"
Write-Host "        这里用 pip install 生成真正的 .exe launcher —— 这是原版缺失的关键一环。"
Push-Location $RootDir
try {
  & $PythonExe @PythonArgs -m pip install --upgrade pip 2>&1 | Out-Null
  # --no-deps 不适用（fts 依赖 jieba 可选），默认安装。
  & $PythonExe @PythonArgs -m pip install . 2>&1 | ForEach-Object { Write-Host "    $_" }
  if ($LASTEXITCODE -ne 0) {
    Write-Warn2 "pip install 失败，尝试 --user ..."
    & $PythonExe @PythonArgs -m pip install --user . 2>&1 | ForEach-Object { Write-Host "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Fail "pip install 失败。"; Pop-Location; exit 1 }
  }
} finally { Pop-Location }

# 找到生成的 gts.exe
$gtsExe = $null
$scriptDirs = @()
try {
  $userBase = (& $PythonExe @PythonArgs -c "import sysconfig;print(sysconfig.get_path('scripts'))" 2>$null)
  if ($userBase) { $scriptDirs += $userBase.Trim() }
} catch { }
try {
  $userBase = (& $PythonExe @PythonArgs -m site --user-base 2>$null)
  if ($userBase) { $scriptDirs += (Join-Path $userBase.Trim() "Scripts") }
} catch { }
# PATH 里的 python 目录
$pyCmd = Get-Command $PythonExe -ErrorAction SilentlyContinue
if ($pyCmd) { $scriptDirs += (Join-Path (Split-Path -Parent $pyCmd.Source) "Scripts") }

foreach ($d in ($scriptDirs | Select-Object -Unique)) {
  $p = Join-Path $d "gts.exe"
  if (Test-Path $p) { $gtsExe = (Resolve-Path $p).Path; break }
}
if (-not $gtsExe) {
  Write-Fail "安装成功但没找到 gts.exe。请手动查找："
  Write-Host "  $PythonExe -c ""import sysconfig;print(sysconfig.get_path('scripts'))""" -ForegroundColor Yellow
  exit 1
}
Write-Ok "gts.exe -> $gtsExe"

# 冒烟：直接执行它，验证不是空壳
try {
  $out = & $gtsExe --repos --json 2>&1 | Out-String
  if ($LASTEXITCODE -eq 0 -or $out -match "count") { Write-Ok "gts.exe 可执行（--repos 有响应）" }
  else { Write-Warn2 "gts.exe 能启动，但 --repos 返回非 0（镜像可能尚未建，属正常）" }
} catch { Write-Warn2 "gts.exe 冒烟失败：$_" }

# ── 5. 建镜像目录 ────────────────────────────────────────────────────────────
Write-Step "[5/6] 准备镜像目录"
if (-not (Test-Path $MirrorRoot)) { New-Item -ItemType Directory -Path $MirrorRoot -Force | Out-Null }
$stateDir = Join-Path $MirrorRoot "state"
if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Path $stateDir -Force | Out-Null }
Write-Ok "镜像根 $MirrorRoot"
if ($Owners -eq "myorg") {
  Write-Warn2 "Owners 仍是占位符 myorg。正式使用请传 -Owners <你的gitea用户名/组织>，否则检索结果为空。"
}

# ── 6. 建 FTS5 索引 ──────────────────────────────────────────────────────────
Write-Step "[6/6] 构建 FTS5 文档索引"
if ($SkipIndex) {
  Write-Warn2 "已跳过（-SkipIndex）。之后可手动执行："
  Write-Host "  `$env:GITEA_MIRROR_ROOT='$MirrorRoot'; `$env:GITEA_OWNERS='$Owners'; $PythonExe $PythonArgs lib\indexer.py --build"
} else {
  $oldMirror = $env:GITEA_MIRROR_ROOT; $oldOwners = $env:GITEA_OWNERS
  $env:GITEA_MIRROR_ROOT = $MirrorRoot; $env:GITEA_OWNERS = $Owners
  Push-Location $RootDir
  try {
    & $PythonExe @PythonArgs "lib\indexer.py" "--build" 2>&1 | ForEach-Object { Write-Host "    $_" }
    if ($LASTEXITCODE -eq 0) { Write-Ok "索引构建完成" } else { Write-Warn2 "索引构建返回 $LASTEXITCODE（镜像为空时会这样，属正常）" }
  } finally { Pop-Location; $env:GITEA_MIRROR_ROOT = $oldMirror; $env:GITEA_OWNERS = $oldOwners }
}

# ── 输出 MCP 配置片段 ────────────────────────────────────────────────────────
Write-Step "DSH MCP 配置片段（复制到 profile 的 cordis.patch.yml）"
$serverJs = Join-Path $RootDir "lib\server.mjs"
$yaml = @"
- insert:
    - id: mcp-gitea-search
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: gitea-search
        transport: stdio
        command: node
        args: ["$($serverJs.Replace('\', '/'))"]
        env:
          GTS_BIN: "$($gtsExe.Replace('\', '/'))"
          GITEA_MIRROR_ROOT: "$($MirrorRoot.Replace('\', '/'))"
          GITEA_OWNERS: "$Owners"
"@
Write-Host $yaml -ForegroundColor Gray
Write-Host "`n注意：GTS_BIN 必须是 .exe 的完整路径，不能写成 `"python bin\gts`"（那样会 ENOENT）。" -ForegroundColor Yellow

if ($RegisterDsh) {
  Write-Step "可选：写入 DSH profile"
  $dshHome = $env:DSH_HOME
  if (-not $dshHome) { $dshHome = Join-Path $env:USERPROFILE ".dsh" }
  $patch = Join-Path $dshHome "profiles\web\cordis.patch.yml"
  if (-not (Test-Path $patch)) {
    Write-Warn2 "未找到 $patch —— 跳过自动写入。请手动粘贴上面的片段。"
  } else {
    $bak = "$patch.bak-$(Get-Date -Format yyyyMMddHHmmss)"
    Copy-Item $patch $bak
    Write-Ok "已备份到 $bak"
    Add-Content -Path $patch -Value $yaml -Encoding UTF8
    Write-Ok "已追加配置。**重启 DSH 后生效**（重启会断开当前会话，请自行选择时机）。"
  }
}

Write-Host "`n=== 安装完成 ===" -ForegroundColor Green
Write-Host "验证命令（新开 PowerShell）：" -ForegroundColor White
Write-Host "  `$env:GTS_BIN='$gtsExe'" -ForegroundColor Gray
Write-Host "  `$env:GITEA_MIRROR_ROOT='$MirrorRoot'; `$env:GITEA_OWNERS='$Owners'" -ForegroundColor Gray
Write-Host "  node `"$serverJs`"   # 应打印 started; ... gts=... (.exe direct)" -ForegroundColor Gray
Write-Host "`n说明：本脚本已避开 setup.sh 在 Windows 上的全部无效操作（apt-get / ln -s / chmod +x / /usr/local/bin）。" -ForegroundColor DarkGray

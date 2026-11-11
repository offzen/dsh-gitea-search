import { fileURLToPath } from 'node:url';

const GUIDE = fileURLToPath(new URL('../docs/POST-INSTALL.md', import.meta.url));

/** Classify a failed CLI response and give a safe, user-controlled next action. */
export function failureResult({ code, out = '', err = '', timedOut = false }, context = 'search') {
  const raw = `${out}\n${err}`;
  let reason = 'UNKNOWN_FAILURE';
  let action = '检查原始错误，并运行 gts --show-effective-config 与 gts --doctor --json。';
  if (timedOut) {
    reason = 'PROCESS_TIMEOUT'; action = '子进程超时；检查磁盘、仓库规模和进程状态后再重试。';
  } else if (/ARG_REJECTED/.test(raw)) {
    reason = 'ARG_REJECTED'; action = '搜索参数含 cmd.exe 无法安全转发的字符（换行/NUL，或 .cmd/.bat 目标下的 % ! ^ & | < >）；去掉这些字符，或改用 gts.exe。';
  } else if (/CONFIG_ERROR/.test(raw)) {
    reason = 'CONFIG_ERROR'; action = '检查 GITEA_OWNERS、GITEA_MIRROR_ROOT 与 gts --show-effective-config；更改 profile 后重载宿主。';
  } else if (/GIT_SEARCH_FAILED/.test(raw)) {
    reason = 'GIT_SEARCH_FAILED'; action = 'Git/正则搜索未完整扫描全部目标仓；不要据此断言零命中，检查错误中的仓、阶段与模式后重试。';
  } else if (/INDEX_UPDATE_FAILED/.test(raw)) {
    reason = 'INDEX_UPDATE_FAILED'; action = '镜像已尝试同步，但全文索引更新失败；检查 sync.log 与索引依赖、磁盘和数据库状态。';
  } else if (/SYNC_FAILED/.test(raw)) {
    reason = 'SYNC_FAILED'; action = '同步未成功，本次搜索已停止；检查同步日志、凭据和网络。';
  } else if (/SYNC_UNAVAILABLE/.test(raw)) {
    reason = 'SYNC_UNAVAILABLE'; action = '当前安装里找不到 scripts/sync.sh；wheel/sdist 现已包含它，缺失说明安装不完整，建议重装。';
  } else if (/MIRROR_MISSING|镜像目录不存在/.test(raw)) {
    reason = 'MIRROR_MISSING'; action = '确认有效配置与镜像目录；需要同步时由你显式执行，不要为清除提示自动同步。';
  } else if (/ENOENT|EACCES|ENOEXEC|EFTYPE|EPERM/.test(raw)) {
    reason = 'CLI_START_FAILED'; action = '检查 GTS_BIN 的字面量路径和执行权限；Windows 使用 pip 生成的 gts.exe，YAML 路径用单引号及正斜杠。';
  } else if (/索引.*不存在|索引尚未建立|no such table|unable to open database|FTS5/i.test(raw) && context === 'docs') {
    reason = 'INDEX_UNAVAILABLE'; action = '检查 gts --fts-stats；确认镜像正确后，按需显式执行 gts --reindex。';
  } else if (/jieba|ModuleNotFoundError|ImportError/i.test(raw)) {
    reason = 'DEPENDENCY_MISSING'; action = '在运行 gts 的同一 Python 环境安装所需依赖（文档检索需要 jieba）。';
  } else if (code === 0) {
    reason = 'INVALID_CLI_OUTPUT'; action = 'CLI 退出成功但响应不是预期结构；确认 GTS_BIN 指向本项目，并核对 CLI 与 MCP 版本。';
  }
  return { isError: true, text: `${reason}: ${context} 失败（rc=${code}）\n${action}\n原始输出:\n${out.slice(0, 800)}\n原始错误:\n${err.slice(0, 400)}\n自检说明: ${GUIDE}` };
}

export function staleNotice(state, now = Date.now(), maxAgeHours = 24) {
  if (!state.last_success || state.status === 'invalid') return '';
  const last = Date.parse(state.last_success);
  const age = now - last;
  if (!Number.isFinite(age) || age < 0 || age <= maxAgeHours * 3600000) return '';
  return `MIRROR_STALE: 距最近成功同步已 ${Math.floor(age / 3600000)} 小时；结果仍可读取，是否同步请按需决定。`;
}

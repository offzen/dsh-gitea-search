# -*- coding: utf-8 -*-
"""gts_cli —— 供 pip 打包使用的可导入入口。

为什么需要这个文件：
    `bin/gts` 是**无扩展名的脚本**，Python 无法把它当作模块导入，因此
    `[project.scripts]` 的入口点不能直接指向它。而 Windows 上恰恰需要一个
    **真正的 .exe launcher**（setuptools 的 console_scripts 会自动生成
    `Scripts\\gts.exe`），因为 Windows 的 CreateProcess 不认 shebang。

    本模块用 importlib 从磁盘加载 `bin/gts`，取其 `main()` 作为入口，
    从而在**不改动 bin/gts 原有内容**的前提下完成打包。

SPDX-License-Identifier: Apache-2.0
"""
import os
import sys
import importlib.util

_HERE = os.path.dirname(os.path.abspath(__file__))


def _script_candidates():
    """按优先级列出 bin/gts 的可能位置。

    历史教训（Windows CI W5，2026-10-28）：最初只认 `_HERE/bin/gts` 一种布局，而
    wheel 用 [tool.setuptools.data-files] 把文件装到了 <prefix>/bin/gts —— 不在
    site-packages 旁边 ⇒ `pip install .` 产出的 gts.exe 虽是合法 PE，一执行就
    RuntimeError: 找不到 bin/gts。故改为按布局探测；**报错时把所有探测过的路径
    打出来**，避免下次再靠猜。
    """
    import sysconfig

    data = sysconfig.get_paths()["data"]
    # 当前进程的启动器路径。console_scripts 生成的 gts（以及 Windows 下的 gts.exe）会走到本模块，
    # 若把它当成「目标脚本」加载，就会 `from gts_cli import main` → 自我无限递归。
    # 实测：<prefix>/bin/gts 被 data-files 覆盖后，gts --help 直接 RecursionError。
    self_path = os.path.normpath(os.path.abspath(sys.argv[0] or ""))
    cands = [
        # ① 源码目录 / sdist：gts_cli.py 与 bin/ 同级
        os.path.join(_HERE, "bin", "gts"),
        # ② wheel 安装布局：data-files 落在 <prefix>/share/dsh-gitea-search/bin/gts
        #    （刻意避开 <prefix>/bin，那里是 console_scripts 的启动器，撞名会让
        #      gts 自我递归 —— 见 pyproject.toml 的说明）
        os.path.join(data, "share", "dsh-gitea-search", "bin", "gts"),
        # ③ sys.prefix 布局（venv / conda 虚拟环境）
        os.path.join(sys.prefix, "share", "dsh-gitea-search", "bin", "gts"),
    ]
    # ④ pip install --user 布局：数据文件落在 site.USER_BASE 下
    try:
        import site
        user_base = getattr(site, "USER_BASE", None)
        if user_base:
            cands.append(os.path.join(user_base, "share", "dsh-gitea-search", "bin", "gts"))
    except Exception:
        pass
    seen, out = set(), []
    for c in cands:
        n = os.path.normpath(c)
        # 启动器自身绝不能被当成目标脚本：否则 `from gts_cli import main` 无限递归。
        if n in seen or n == self_path:
            continue
        seen.add(n)
        out.append(n)
    return out


def _load_script_module():
    """把 bin/gts 作为模块加载并返回。

    bin/gts 内部会用 `os.path.realpath(__file__)` 定位同级 bin/、上级 lib/ 来
    import indexer，因此这里必须传入真实路径，不能伪造。

    注意：bin/gts **没有 .py 扩展名**，`spec_from_file_location` 会因无法推断 loader
    而返回 None。所以这里显式指定 SourceFileLoader，不能依赖自动推断。
    """
    import importlib.machinery

    tried = _script_candidates()
    script = next((c for c in tried if os.path.isfile(c)), None)
    if script is None:
        raise RuntimeError(
            "gts_cli: 找不到 bin/gts。已探测：\n  "
            + "\n  ".join(tried)
            + "\n请确认是以源码目录方式运行，或 wheel 安装完整。"
        )
    loader = importlib.machinery.SourceFileLoader("_gts_script", script)
    spec = importlib.util.spec_from_loader("_gts_script", loader)
    if spec is None:
        raise RuntimeError(f"gts_cli: 无法为 {script} 构建模块 spec")
    mod = importlib.util.module_from_spec(spec)
    # 注册到 sys.modules，避免脚本内部若有 dataclass/pickle 时出现模块解析问题。
    sys.modules["_gts_script"] = mod
    loader.exec_module(mod)
    return mod


def main(argv=None):
    """CLI 入口：转发到 bin/gts 的 main()。

    保留 argv 形参，便于测试与程序化调用（setuptools launcher 会以无参方式调用）。
    """
    mod = _load_script_module()
    real_main = getattr(mod, "main", None)
    if real_main is None:
        raise RuntimeError("gts_cli: bin/gts 中未找到 main()")
    if argv is None:
        return real_main()
    old = sys.argv
    try:
        sys.argv = [old[0] if old else "gts", *argv]
        return real_main()
    finally:
        sys.argv = old


if __name__ == "__main__":
    sys.exit(main())

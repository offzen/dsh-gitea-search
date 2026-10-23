## What's New in v1.1.0 / 新增与改进

### 🇨🇳 中文摘要

`dsh-gitea-search` v1.1.0 为 DeepSeek Harness (DSH) 带来高性能多仓检索与历史会话定位能力，并强化了 Agent 上下文安全护栏：

- **双层极速检索**：
  - 代码检索 (`search_gitea`)：裸镜像快速 `git grep`，多词短语未命中时自动回退为多词同行相交（AND）匹配。
  - 文档/会话检索 (`search_gitea_docs`)：SQLite FTS5 + Jieba 分词与 BM25 排序，彻底解决大模型在连续中文与复合查询下的 0 命中痛点。
- **Agent 上下文四重安全护栏**：
  - 结果数硬夹（默认 5 条，上限 200 条）、单行长截断（2000B）、总缓冲区限制（128KB）与文件读取窗口（默认 400 行），杜绝提示词膨胀。
  - 完整包解码机制，根除流式字节截断导致的 `U+FFFD` 乱码。
- **开箱即用与质量验证**：
  - 自包含测试床：无外部凭据依赖，模拟真实微服务配置与排错会话。
  - 自动化 CI：GitHub Actions 跑通 26 项 E2E 断言（Ubuntu / Node.js 22 LTS / Python 3.11）。
- **零侵入与运维遥测**：
  - 内置 `IoTracker`（CPU、执行时间、`/proc/self/io` 统计与 OK/WARN 评级），纯外部 MCP 协议集成，零修改 DSH 核心代码。

---

### 🌐 English Summary

`dsh-gitea-search` v1.1.0 brings high-performance bare mirror search, session retrieval, and robust context guardrails to DeepSeek Harness via Model Context Protocol (MCP):

- **Dual-Layer Search Engine**:
  - **Code Grep (`search_gitea`)**: Ultra-fast `git grep` with automatic multi-token `--and` fallback on exact phrase misses.
  - **Doc & Session FTS5 (`search_gitea_docs`)**: SQLite FTS5 engine with Jieba Chinese tokenization and BM25 ranking, eliminating tokenization blind spots in CJK queries.
- **Agent Context-Protective Guardrails**:
  - 4-layer defense: Hard clamp (default 5, max 200), line truncation (2000B), buffer ceiling (128KB), and 400-line read window to prevent prompt bloat.
  - Safe multibyte boundary decoding, completely eliminating `U+FFFD` replacement artifacts.
- **Self-Contained Testbed & CI**:
  - Zero-credential synthetic testbed simulating real-world runbooks and session logs.
  - GitHub Actions CI executing 26 end-to-end assertions in under 40s (Node.js 22 LTS).
- **Diagnostics & Zero Footprint**:
  - `IoTracker` telemetry (CPU time, wall clock, `/proc/self/io` metrics with health grades).
  - 100% external MCP stdio communication with zero modifications to upstream DSH core.

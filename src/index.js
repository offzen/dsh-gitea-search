#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
/**
 * gitea-search-mcp —— 标准 MCP stdio 引导入口。
 *
 * 与 lib/server.mjs 解耦：
 *   1. lib/server.mjs 负责工具实现、注册表、createServer() 与 dumpSchema()（被 import 时零副作用）。
 *   2. 本入口负责连接 StdioServerTransport，启动标准 MCP stdio 服务。
 *   3. stdout 严格只输出 JSON-RPC；所有日志与废弃提示一律走 stderr。
 */
import { startStdioServer } from '../lib/server.mjs';

await startStdioServer();

# MCP 服务器：把确定性证据工具暴露给任何 MCP 客户端

2026-11-01 新增。`scripts/run_mcp_server.py` 以 **MCP（Model Context Protocol）stdio 传输**暴露五个确定性工具，Claude Desktop 等任何 MCP 客户端都能直接调用这套证据层——模型仍然只动嘴，事实、执行、放行依旧全部握在本地代码手里。

## 为什么是零依赖实现

官方 `mcp` SDK 会引入 starlette/httpx 等一串新依赖，而本仓库依赖全部钉死、627 项测试全绿。MCP 工具服务端真正需要的方法只有 `initialize` / `tools/list` / `tools/call`（外加 `ping`），stdio 传输就是逐行 JSON-RPC 2.0——这个面小到可以放进一个可审计的文件（`src/ecom_review_intelligence/mcp_server.py`），所以按仓库"每个依赖都要过三问"的规矩，选择手写而不引入 SDK。协议版本支持 `2025-06-18`（另兼容 2025-03-26 / 2024-11-05 握手）。

## 隐私边界（与工作台同一条线）

工具返回**只有计数、商品标题与本地编号、维度名**——评论原文、证据 ID、parent ASIN、本地路径永远不离开进程：

- 商品只能用目录编号（`frostline`、`p0004`…）寻址，编号→ASIN 的映射留在服务端；
- 样本只给方向计数（支持/反对/冲突/不足），不给文本；
- 每个结果都带 `notes` 字段，说明"样本是分层凭证不是统计结论、全量是词表粗筛未经核对"，防止客户端把数字说过头。

这套边界有测试盯着：`tests/test_mcp_server.py` 断言任何工具结果里不出现评论文本与 ASIN。

## 五个工具

| 工具 | 输入 | 返回 |
| --- | --- | --- |
| `list_products` | 标题关键词 / 档位 / 条数 | 目录切片（编号、标题、评论数、档位），全库 3,278、默认 A 档 244 |
| `diagnose_product` | 商品编号 + 维度 | 匹配数、分层样本方向计数、全量词表粗筛、盲区（含低星）、强度档 |
| `compare_products` | 2–5 个商品 + 维度 | 每商品同上，可并排比较 |
| `triage_issues` | 1–5 个商品 + 可选维度 | 按强度与反对/冲突计数排序的"先查什么"清单 |
| `category_baseline` | 无 | 全类目 136,604 条评论 × 8 维度的覆盖率与四方向计数、档位分布 |

底层完全复用 P6 代理的确定性工具层（`agent_tools.EvidenceToolService`）：每次调用独立校验店铺 lineage（快照 ID + 契约指纹），维度超出冻结契约直接报错；`category_baseline` 复用 `engine.category_aspect_totals`，与工作台同一条 SQL 口径。

## 接入 Claude Desktop

`开发者 → 编辑配置`，在 `claude_desktop_config.json` 里加：

```json
"ecom-review-intelligence": {
  "command": "<你的 python>",
  "args": [
    "<项目根>/scripts/run_mcp_server.py",
    "--project-root", "<项目根>"
  ]
}
```

启动就绪信息打在 stderr（stdout 是纯协议通道）；本机没有证据库时以退出码 2 拒绝启动，不降级、不伪造。

## 验证到什么程度（2026-11-01）

- 10 项协议/工具测试全绿（握手、通知静默、未知方法 -32601、解析错误 -32700、工具错误走 `isError`、文本/ASIN 不出进程）。
- 真库端到端（真实 stdio 管道）：`diagnose_product frostline × noise` 返回 matched 766、样本 3 支持/1 反对/1 冲突、全量粗筛 286/166/239/75、盲区 75（低星 29）；`category_baseline` 返回 136,604 条评论、档位 244/566/2,468、可靠性反对 9,012 vs 支持 513——与工作台同屏数字逐位一致。
- **已注册进 ZCode 客户端**（用户级 `~/.zcode/cli/config.json` → `mcp.servers`），并用配置里的原样命令逐字验证过一次完整客户端会话（握手/列工具/两次调用全过）。MCP 服务器在**会话启动时**连接，真实客户端实连以重启后的 设置→MCP 状态为准。
- 桌面 exe 不含 MCP 服务器；仓库运行 `scripts/run_mcp_server.py` 即可。

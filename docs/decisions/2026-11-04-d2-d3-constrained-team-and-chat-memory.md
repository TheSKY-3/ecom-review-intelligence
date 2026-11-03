# 决策记录：受约束读卡组（D2）与会话记忆（D3），附 D1 评测裁判结论

日期：2026-11-04。状态：已实现并过全部门（pytest 658 passed + 1 skipped、ruff、P7 本地回归、浏览器验证 7 闸、真机截图）。本文只记「为什么这么做」，逐条实测数字见引用的工件。

## 背景与问题

岗位调研后的三个改进方向里，只有两个解决了本项目的真实问题：

- **D1 评测裁判**：四道机械校验（引用逐字、数字可复算、结构、纪律）挡得住「格式错」，挡不住「格式对但话说错」——把 5 张抽样卡说成普遍结论、答非所问的流利话。这是当时唯一没有守门员的洞。
- **D2 并行读卡**：运营要把一次运行的多张证据卡逐张点「AI 深入分析」，每次一张、串行等待。读卡本身已受约束，缺的只是并发和一次点击。
- **D3 会话记忆**：聊天状态只在进程内存（`_ChatSession`），页面刷新后聊天记录不可恢复——交接文档里的已知 TODO。

被明确**不做**的：让模型参与排序或汇总决策。C2 实验已证模型排序相对确定性规则无增量（Kendall 0.931），本轮不动摇这个结论。

## D2：并行读卡组（evidence_team.py）

**决定**：新增 `src/ecom_review_intelligence/evidence_team.py`（纯逻辑）+ 服务方法 `run_parallel_insights` + 端点 `POST /api/v1/parallel-insights` + 结果页「并行读卡（N 张）」按钮。每个读卡子代理复用与单卡完全相同的管线（payload 构建、逐字引用校验、数字复算），唯一的区别是 `gateway.analyse` 这一次无状态调用在线程池里并发（上限 5，即每运行最多 5 张卡），校验、缓存、视图组装全部留在调用线程；结果按卡片顺序确定性归位，单卡失败只影响自己（`ReadingFailure`，错误码与单卡路径一致）。

**为什么不叫「多智能体协作」**：没有协商、没有模型间汇总、没有模型排序——组的行为是确定性代码，模型只做它被验证过的事（把一张卡读成结构）。这是 C2 教训的直接应用。

**实测**：对比工作流 2 卡并行读取 2/2 READY（截图 `docs/assets/p7-workbench-parallel-team.png`）；限流按 1 次点击计（上限内最多 5 次模型调用，文档已写明）。

**已知口径**（重要，影响使用者预期）：本地 P6 服务的「证据卡」按商品计——诊断 1 张、对比 2 张、分诊 3 张（`_public_run` 的 `visible_cards`）；所以团队按钮只在对比/分诊出现，诊断没有可并行的对象。

## D3：会话记忆（chat_memory.py）

**决定**：新增 `src/ecom_review_intelligence/chat_memory.py`（SQLite，`data/processed/p7_chat_memory.sqlite`）+ 四个端点（`GET/DELETE /api/v1/chat/{run_token}/history`、`GET/PUT /api/v1/preferences`）。落库内容：

1. 每轮完成的对话：用户原话、模型侧摘要（`chat_answer_summary`，与进程内会话同源）、**去掉逐字引用后的公开回答视图**（`redact_turn_for_memory` 把每条 `quote` 置空）。评论原文永不落库——这比导出 CSV 的边界更紧，是有意的。
2. 偏好：非机密的键值对（当前只有 `chat_model`）。形似机密的键（`key|token|secret|password`）直接拒绝，API 密钥仍然只存在于浏览器、永不落盘。

**恢复语义（诚实版）**：页面刷新后前端用 sessionStorage 里的 run token 重连运行（`GET /api/v1/runs/{token}` 本来就存在）并拉历史恢复聊天面板；重启**服务**后运行视图本身仍是进程内存、不能重连，此时历史不丢（下次同 token 可恢复）但界面入口没有——「跨服务重启恢复运行」是后续独立工作，不在本轮。新会话会把已存轮次预载进模型侧 `history` 并计入 8 轮预算，所以重启后接续提问时模型知道自己之前说过什么（摘要级，无逐字引用）。

**实测**：真机刷新恢复 1 条历史并显示「历史记录不含评论原文」横幅（截图 `docs/assets/p7-workbench-chat-memory.png`）；重启后预载模型历史的单测在 `tests/test_chat_memory.py`。

## D1：评测裁判的结论与教训（工件 `docs/reports/agent-judge-evaluation-2026-11.md`）

10 个真实回答、9 判 1 跳（1 条确定性降级不可判）：**6 pass / 2 warn / 0 fail / 1 裁判自身错误（fail-closed 记录）**。两个 warn 是合理的严格判定（边界提示措辞、用 5 张卡谈普遍程度）。

**最重要的一课（花了三轮 live 才收敛）**：前两轮的 fail 全是**裁判输入不完整造成的假阳性**——chat 合法引用逐商品统计与全量词表粗筛，裁判最初只拿到运行级聚合数字，把合法数字判成编造。最终版裁判输入镜像 chat 的统计来源（`workbench_service._lexicon_totals`，与 `full_population_totals` 统计键同源）。**给任何「LLM 裁判」的下边界：裁判必须能看到被评对象被允许引用的每一个数字来源，否则测的是输入缺口不是输出质量。** 前两轮的假阳性没有进任何发布工件；过程差异记录在报告的 limitations 里。

成本：四轮 live（含两次口径修复重跑）合计约 ¥0.5–0.7，远低于 ¥5 上限。

## 验证清单（全部实际执行）

- `pytest`：658 passed, 1 skipped（此前 604；新增 D1 9 项、D2 12 项、D3 10 项）
- `ruff check .`：全绿
- `scripts/run_phase7_local_regression.py`：PASSED（3 工作流，源身份清单已同步新增 4 文件）
- `node scripts/validate_phase7_workbench.cjs`：PASSED（7 闸全绿，桌面/移动截图已刷新）
- 真机（真实 DeepSeek key）：D2 对比 2 卡并行 2/2 READY；D3 刷新恢复 + 偏好同步（截图两张，均过路径/ASIN/哈希护栏）
- `node --check workbench.js`：通过

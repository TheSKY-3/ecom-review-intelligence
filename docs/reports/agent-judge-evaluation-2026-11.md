# Agent 评测裁判报告（D1 evidence judge）

机械四道校验挡得住「格式错」，挡不住「格式对但话说错」。本报告把真实工作台回答
（AI 深入分析 + 对话助手）逐条交给同一个受约束模型当裁判，只判语义保真度：
有没有把小样本说成普遍结论、有没有和自己的统计口径矛盾、有没有无依据外推、
有没有编造、有没有漏掉「样本不是统计结论」的边界提示。

- 生成时间：2026-11-03T16:47:20.251634+00:00
- 模式：live（模型 deepseek-v4-flash，裁判为同一受约束调用）
- 数据血缘：snapshot `44eface81cccdceb36c3fd61f93736267e63b84ba63902fac026659540518846` · contract `b93053ce55ab…`
- 采集 10 个回答，生成侧模型调用 19 次，裁判调用 9 次

## 总体判定

| 判定 | 条数 |
| --- | --- |
| pass | 6 |
| warn | 2 |
| fail | 0 |
| error（裁判失败，fail-closed 计入） | 1 |
| skipped（不可判/干跑） | 1 |

已判回答通过率 0.75，失败率 0.0，裁判自身错误率 0.1111。

## 按工作流

| 工作流 | pass | warn | fail | error | skipped |
| --- | --- | --- | --- | --- | --- |
| chat | 3 | 1 | 0 | 0 | 1 |
| insight | 3 | 1 | 0 | 1 | 0 |

## 问题码分布

| 问题码 | 次数 | 含义 |
| --- | --- | --- |
| OVERSTATES_SAMPLE | 1 | 把小样本说成普遍结论 |
| CONTRADICTS_STATS | 0 | 与统计口径矛盾 |
| UNSUPPORTED_GENERALIZATION | 0 | 无依据外推 |
| MISSING_DISCIPLINE_NOTE | 1 | 缺样本边界提示 |
| FABRICATED_CLAIM | 0 | 编造数字/事实/证据 |

## 逐条判定

| 条目 | 工作流 | 方面 | 判定 | 问题码 |
| --- | --- | --- | --- | --- |
| 00-insight | insight | noise | warn | MISSING_DISCIPLINE_NOTE |
| 01-chat | chat | noise | warn | OVERSTATES_SAMPLE |
| 02-insight | insight | noise | error | - |
| 03-chat | chat | noise | pass | - |
| 04-insight | insight | noise | pass | - |
| 05-chat | chat | noise | pass | - |
| 06-insight | insight | noise | pass | - |
| 07-chat | chat | noise | pass | - |
| 08-insight | insight | noise | pass | - |
| 09-chat | chat | noise | skipped | - |

## 局限

- The judge is the same config-pinned model as the agent: it grades semantic fidelity against the run's own statistics, not human-adjudicated ground truth.
- Judge verdicts come from one forced tool-call per answer with thinking disabled and default sampling; verdict stability across runs was not measured (one missing-rationale judge error appeared in 9 final calls and is recorded fail-closed).
- Answers were collected from the bundled local evidence store; the judge sees only the answer digest plus that run's counts, never the review corpus.
- Two earlier iterations produced false fails because the judge payload lacked the per-product and lexicon screen counts the chat legitimately cites; the final payload mirrors the chat's stat sources exactly.
- A judge error is counted as an error (fail-closed), not silently dropped.
- Engineering exploration; not a phase gate and not a business-impact claim.

逐条回答原文与裁判理由在 `artifacts/evals/`（未发布）；本报告只含计数、判定与问题码，
不含任何评论原文或回答引文。

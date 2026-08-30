# P7 synthetic 手工基线任务包

本任务包只用于一次目标运营角色的可用性观察。下面的商品、评论信号和编号全部是合成内容，
不是 Amazon 评论、真实商品或业务事实。参与者应先完成本页的三项手工任务，再打开工作台；
否则会产生明显的答案提示和顺序偏差。

## 1. 参与者须知

- 可以使用你日常习惯的表格、笔记或搜索方式，但不要使用本项目工作台。
- 从开始读任务时计时；写下结论和所依据的 `Sxx` 行号后停止计时。
- 不要求找到“标准答案”。观察重点是完成过程、证据选择、犹豫和失败原因。
- 三项手工任务完成后，再打开 `http://127.0.0.1:8765` 完成同名任务。

## 2. 合成评论信号

| 行号 | 合成商品 | 合成评论信号 |
|---|---|---|
| S01 | Frostline Countertop | Night use was acceptable because the motor stayed quiet. |
| S02 | Frostline Countertop | A rattling sound appeared after the ice basket filled. |
| S03 | Frostline Countertop | The compressor became louder during longer cycles. |
| S04 | Frostline Countertop | The tray was easy to remove and wipe clean. |
| S05 | Frostline Countertop | Mineral residue collected near the water channel. |
| S06 | Frostline Countertop | The unit completed repeated cycles without stopping. |
| S07 | Northstar Compact | The first cycle was quiet enough for a small office. |
| S08 | Northstar Compact | A steady buzzing sound was noticeable across the room. |
| S09 | Northstar Compact | The alert tone was louder than expected at night. |
| S10 | Northstar Compact | The drain area was awkward to reach during cleaning. |
| S11 | Northstar Compact | Two cycles stopped early and required a restart. |
| S12 | Northstar Compact | Ice output remained consistent during a weekend test. |
| S13 | Harbor Station | Fan noise increased when the room became warmer. |
| S14 | Harbor Station | The operating sound blended into normal kitchen noise. |
| S15 | Harbor Station | A clicking sound repeated before each new cycle. |
| S16 | Harbor Station | The reservoir opening made rinsing straightforward. |
| S17 | Harbor Station | Water remained in a corner after the cleaning cycle. |
| S18 | Harbor Station | One long run ended before the basket was full. |

## 3. 手工任务

### 任务 A：SKU 诊断

只看 Frostline Countertop。判断噪音是否值得运营优先核查，写出一句结论，并列出最关键的
两个 `Sxx` 证据行。记录完成时间和第一次找到有效证据的时间。

### 任务 B：竞品比较

比较 Frostline Countertop 与 Northstar Compact 的噪音信号。判断哪一方更需要后续核查，
或说明当前证据不足以区分。至少列出双方各一个 `Sxx` 证据行。

### 任务 C：问题分诊

同时查看三个合成商品。给出两个最值得优先核查的问题，并为每个问题指定建议协作角色：
产品、质量或客服。每个优先项至少列出一个 `Sxx` 证据行。

## 4. 转入工作台

完成三项手工任务后，再打开工作台，依次选择 `SKU 诊断`、`竞品比较`、`问题分诊`，
使用相同商品范围和“噪音”维度。把工作台完成时间、首次有效证据时间、引用复核以及
采纳/修改/拒绝记录到 `p7-user-trial-record.md`。

## 5. 解释边界

这是单人、固定顺序、合成材料的定性试用。参与者在手工任务中已经熟悉材料，随后使用工作台
可能存在学习效应；任务规模也远小于真实运营数据。因此只能发现可用性、理解和流程问题，
不能据此估计企业工时节省、准确率提升或投资回报。

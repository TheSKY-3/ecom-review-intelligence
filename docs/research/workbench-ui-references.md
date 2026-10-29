# 工作台前端风格参考(2026-09-26 联网调研)

给"评论证据工作台"下一步前端升级用的参考清单。结论先行:**不换技术栈**(保持模板 + 原生 JS),
只吸收四个已被 2026 年主流 B2B 工具验证的模式。

## 值得抄的四个模式

1. **密度优先、键盘驱动**(Linear/Stripe/Retool 风格):证据列表行距收紧、等宽字体对齐数字、
   `j/k` 或方向键在证据条目间移动、`Enter` 展开原文。适合"逐条核对"这个核心动作。
2. **人审队列模式**(compliance 工具 ControlMap/Scytale 的 evidence reviewer 流):AI 输出永远
   带一个"待人工确认"的状态列,而不是把结论直接端上来。与我们"机器打的标签"红线同构,
   界面上可以给每张证据卡加 确认/存疑/驳回 三态(本地记录,不改确定性层)。
3. **行动导向仪表**(Fuselab 2026 趋势):结果页顶部先给"下一步查什么",数字卡片退居支撑位。
   我们已有 next_action 字段,排版上把它提级即可。
4. **暗色模式与自适应布局**成为 2026 年 B2B 基线;移动端断点我们已有,css 变量化后暗色是一小时级工作。

## 参考来源

- [50 Best Dashboard Design Examples for 2026 — Muz.li](https://muz.li):按模式类型(分析/SaaS 运维)组织的实例集。
- [Dashboard Design Trends 2026 — Fuselab Creative](https://fuselabcreative.com):从静态报表到 AI 预判式界面的转向论述。
- [10 Best SaaS Dashboard Examples & Trends (2026) — AdminLTE](https://adminlte.io):Linear/Stripe/Vercel/Retool 等内部工具范式拆解。
- [35 SaaS Dashboard Examples & Trends — 925 Studios](https://www.925studios.co):暗色模式与 B2B 专属模式。
- [Refine — GitHub](https://github.com/refinedev/refine):内部工具前端的事实框架(仅参考其信息架构,不引入依赖)。

## 排序原则( ours )

界面升级的验收标准不是好看,是**核对速度**:任何改动必须让"从结果页到逐条看完 5 条原文"的
点击/按键次数不增。用 `node scripts/validate_phase7_workbench.cjs` 的布局断言守住回归。

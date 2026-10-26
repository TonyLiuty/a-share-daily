# 项目结构

| 模块 | 职责 |
|---|---|
| scripts/run_daily.py | 取数、因子评分、信号与收益记录、主流程 |
| scripts/event_analysis.py | 消息方向及发布时间判断 |
| scripts/news_evidence.py | 有界原文读取、来源匹配元数据 |
| scripts/report_insights.py | 同源历史比较、固定研究样本、已成熟信号 |
| scripts/insights_ui.py | 今日要点、对比与公司摘要 |
| scripts/report_ui.py | 页面布局与按需展开 |
| scripts/report_assets/ | 内嵌样式与交互，无远程前端依赖 |
| scripts/rebuild_report.py | 离线重建、备份、数据哈希校验 |
| scripts/validate_report.py | 章节、图表、时序与页面结构验收 |
| scripts/test_*.py | 不联网的回归测试 |
| scripts/check_ui.cjs | 真实浏览器功能检查 |
| scripts/selection_quality.py | 行业覆盖、分类层级、核心字段与集中度检查 |
| scripts/quality_audit.py | 审计接入报告与可选严格模式 |
| scripts/evaluate_research.py | 按完整信号日汇总收益、配对基准和成本敏感性 |

## 修改边界

展示优化只改解释与布局，不顺带更改因子权重或回填历史信号。`run_daily.py` 仍保留原取数和评分逻辑，本次没有进行高风险的全量拆分。

报告用 Markdown 作为一部分结构化输入，修改标题或表格时要同步检查解析与校验。后续可将报告内容收敛到统一数据模型，再生成两种格式；当前不要单方面更名固定章节。

## 部署

仓库与已有技能安装暂时分开。更新生产安装前先跑离线测试，再备份原脚本并部署同一版本。不要同时从两个目录对同一个输出目录运行生成任务；当前尚无跨进程写锁。

GitHub Actions 仅做离线测试，不负责抓行情或发送日报，不需要密钥。

# A股收盘研究日报

用公开行情生成市场概览、行业涨跌、消息分析和十只股票的研究观察池。输出单文件 HTML、Markdown 与 CSV，并记录信号次日开盘后的理论收益。

项目不连接券商、不下单、不自动修改因子权重。观察池不是买入名单，理论收益不是实盘收益。

## 页面内容

- 首页先看指数表现、行业变化与今日要点，不重复堆放模型评价。
- 消息说明利好或利空的对象，区分预测、已发生事实和盘后消息。原文核验记录放在详情中。
- 个股可按名称或代码搜索，展开查看业务、财务、因子得分和 K 线。
- 当天新信号与此前已成熟收益分开展示；缺失数据不填零。
- 手机端适配；打印时自动展开详情。

## 安装

建议 Python 3.12。以下命令在项目根目录执行：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts/run_daily.py --output ./quant-agent-output --open-browser
```

Linux/macOS 使用 `.venv/bin/python`。程序按北京时间和最新行情日期检查运行条件，只有 `completed` 才是新报告。`market_open`、`duplicate`、`non_trading_day` 或 `data_error` 表示本次没有可交付的新报告。正常运行不要使用 `--force`。

第一次运行会在输出目录生成配置，后续沿用该配置。行情接口可能不可用或限流；程序会记录降级与缺失。切换源或样本范围后，不应直接比较涨跌家数与成交额。

## 输出

```text
quant-agent-output/
  config.json
  latest.html / latest.md
  daily/YYYY-MM-DD/
    report.html / report.md
    candidates.csv / fundamentals.csv
    charts/
    market_snapshot.csv / industry_snapshot.json
    signals_asof.csv / analysis_context.json / news_audit.json
  state/
    signals.csv / equity_curve.csv / research_cohort.json
```

不要单独移动 HTML：K线和下载链接依赖日期目录下的文件。输出、历史信号与缓存默认不进入 Git。

## 离线修改报告

```powershell
python scripts/rebuild_report.py --directory ./quant-agent-output/daily/YYYY-MM-DD
python scripts/validate_report.py --report ./quant-agent-output/daily/YYYY-MM-DD/report.md --html ./quant-agent-output/daily/YYYY-MM-DD/report.html
```

重建只更新文案和展示，不重新取行情、不修改候选、权重或收益记录。旧版报告保存在 `revisions/` 中。重建历史日期不会覆盖最新入口。

## 测试

```powershell
python -m unittest discover -s scripts -p "test_*.py" -v
```

测试用合成数据，不访问金融接口。GitHub Actions 在 Windows 和 Linux 上执行同一组测试。

浏览器检查为可选项，需要一份本地已生成报告：

```powershell
npm install
npx playwright install chromium
npm run test:ui -- ./quant-agent-output/daily/YYYY-MM-DD
```

已有浏览器可通过 `CHROMIUM_PATH` 指定。`PLAYWRIGHT_MODULE` 可指定本机模块位置；普通安装无需设置。

## 策略质量审计

新日报保存 `selection_quality.json`，列出预选池核心字段有效率、行业覆盖、选股时行业缺失和申万一级行业集中度。常规模式保留原评分，报告指出审计问题。可选 `--strict-quality` 在分类与核心字段未通过检查时返回 `data_error`，在写入新信号之前停止。当前上游尚未提供统一的行业分类体系/层级版本，严格模式会拒绝这类输入；它用于核实缺口，不会自动补齐映射。一级行业40%是诊断用研究警戒值，未替换现有选股配置。

用保存的信号建立独立基准：

```powershell
python scripts/evaluate_research.py --signals ./quant-agent-output/state/signals.csv --output ./quant-agent-output/research/baseline.json
```

按1/5/20日分别报告完整信号日、成熟观测和配对超额。一个信号日只有CSV中全部股票收益成熟才参与组合均值；额外往返成本5/10个基点单独列出。多日重叠窗口不复利为净值，结果不用于自动调参。当前验收记录见 [迭代基准](docs/2026-10-26-迭代基准.md)。

## 结构与限制

见 [架构说明](docs/ARCHITECTURE.md)、[数据与研究口径](docs/DATA_POLICY.md)、[多 Agent 迭代计划](docs/ITERATION_PLAN.md)、[策略审计](docs/STRATEGY_AUDIT.md) 和 [贡献说明](CONTRIBUTING.md)。当前公开发布准备版从原本机运行脚本整理而来；已有定时任务仍使用原安装目录，本仓库不会自动改写它。

尚未指定开源许可证。仓库可见性不等于授予开源使用许可；数据源的使用与再分发还需遵守各自条款。

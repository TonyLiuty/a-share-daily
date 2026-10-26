#!/usr/bin/env python3
"""Deterministic structural and semantic checks for generated daily reports."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


REQUIRED_HEADINGS = [
    "## 报告速览",
    "## 一、当日股市全景",
    "## 二、今日观察池",
    "## 三、逐只股票详细分析",
    "## 五、理论收益统计与模型表现评价",
    "## 六、逐股收益跟踪",
    "## 七、历史信号股票池收益统计",
    "## 九、数据质量与限制",
]


def section(text: str, heading: str) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    match = re.search(r"\n#{2,3}\s+", text[start + len(heading):])
    end = start + len(heading) + match.start() if match else len(text)
    return text[start:end]


def table_rows(text: str, label: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if not line.startswith(f"| {label} |"):
            continue
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def percent(value: str) -> float | None:
    match = re.search(r"([+-]?\d+(?:\.\d+)?)%", value)
    return float(match.group(1)) if match else None


def validate(report_path: Path, html_path: Path | None) -> dict[str, object]:
    report = report_path.read_text(encoding="utf-8")
    errors: list[str] = []
    checks: dict[str, object] = {}

    missing = [heading for heading in REQUIRED_HEADINGS if heading not in report]
    checks["required_headings"] = len(REQUIRED_HEADINGS) - len(missing)
    if missing:
        errors.append("缺少章节：" + "、".join(missing))

    nan_hits = re.findall(r"(?<![A-Za-z])nan(?![A-Za-z])", report, flags=re.I)
    checks["nan_count"] = len(nan_hits)
    if nan_hits:
        errors.append(f"报告仍包含{len(nan_hits)}个nan占位")

    contribution = section(report, "### 今日观察池收益贡献")
    contributors = table_rows(contribution, "贡献较多")
    drags = table_rows(contribution, "拖累较多")
    bad_contributors = [row[1] for row in contributors if len(row) < 3 or percent(row[2]) is None or percent(row[2]) <= 0]
    bad_drags = [row[1] for row in drags if len(row) < 3 or percent(row[2]) is None or percent(row[2]) >= 0]
    checks["contributors"] = len(contributors)
    checks["drags"] = len(drags)
    if bad_contributors:
        errors.append("贡献榜包含非正收益：" + "、".join(bad_contributors))
    if bad_drags:
        errors.append("拖累榜包含非负收益：" + "、".join(bad_drags))

    history = section(report, "### 最近历史信号收益前后对比")
    top_rows = table_rows(history, "历史表现较好") + table_rows(history, "相对靠前（仍为负）")
    bottom_rows = table_rows(history, "历史表现较弱") + table_rows(history, "相对靠后（仍为正）")
    top_names = {row[1] for row in top_rows if len(row) > 1}
    bottom_names = {row[1] for row in bottom_rows if len(row) > 1}
    overlap = sorted(top_names & bottom_names)
    checks["history_rank_overlap"] = overlap
    if overlap:
        errors.append("历史前后榜重复股票：" + "、".join(overlap))

    chart_refs = re.findall(r"!\[[^\]]*\]\((charts/[^)]+)\)", report)
    missing_charts = [ref for ref in chart_refs if not (report_path.parent / ref).is_file()]
    checks["chart_refs"] = len(chart_refs)
    checks["missing_charts"] = len(missing_charts)
    if not chart_refs:
        errors.append("未找到K线图引用")
    if missing_charts:
        errors.append("K线图文件缺失：" + "、".join(missing_charts[:5]))

    event_section = section(report, "### 重大事件与市场影响分析")
    event_rows = len(re.findall(r"^\| \[[^\]]+\]\(https?://", event_section, flags=re.M))
    declared = re.search(r"展示(\d+)条", event_section)
    checks["event_rows"] = event_rows
    if event_rows > 8:
        errors.append(f"重大事件展示{event_rows}条，超过8条上限")
    if declared and int(declared.group(1)) != event_rows:
        errors.append(f"重大事件声明展示{declared.group(1)}条，实际表格{event_rows}条")

    # Editorial safeguards: structural success must not conceal timing/rating errors.
    semantic_rows = [line for line in event_section.splitlines() if line.startswith('| [')]
    for line in semantic_rows:
        cells = line.strip('|').split('|')
        if len(cells) < 4:
            continue
        title_match = re.search(r'\[([^\]]+)\]', cells[0])
        title = title_match.group(1) if title_match else ''
        time_match = re.search(r'\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2})', cells[0])
        if time_match and time_match.group(1) > '15:00:00' and '盘后' not in cells[2]:
            errors.append('盘后事件缺少时序标记：' + title)
        if '评级' in title and '观点信息' not in cells[1]:
            errors.append('机构评级被误作交易动作：' + title)
        if '未主导当日' in cells[2] or '未形成当日板块驱动' in cells[2]:
            errors.append('缺乏事件反应证据却给出因果否定：' + title)
    checks['event_semantic_rows'] = len(semantic_rows)

    if html_path:
        if not html_path.is_file():
            errors.append(f"HTML不存在：{html_path}")
        else:
            html_text = html_path.read_text(encoding="utf-8")
            checks["html_toc"] = 'class="toc"' in html_text
            checks["html_long_table"] = 'class="table-wrap long-table"' in html_text
            if not checks["html_toc"]:
                errors.append("HTML缺少章节目录")
            if not checks["html_long_table"]:
                errors.append("HTML未启用长表滚动")
            if '页面版本 2.0' in html_text:
                missing_panels = [key for key in ('overview','industries','news','watchlist','model','quality') if f'id="{key}"' not in html_text]
                checks['dashboard_panels'] = 6 - len(missing_panels)
                if missing_panels:
                    errors.append('首页缺少模块：' + '、'.join(missing_panels))
                checks['stock_detail_count'] = html_text.count('class="stock-detail"')
                if checks['stock_detail_count'] != len(chart_refs):
                    errors.append('个股详情数量与K线数量不一致')

    return {"status": "passed" if not errors else "failed", "checks": checks, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="验收A股量化日报的结构、图表和收益榜口径")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--html", type=Path)
    args = parser.parse_args()
    result = validate(args.report.resolve(), args.html.resolve() if args.html else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

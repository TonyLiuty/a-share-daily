#!/usr/bin/env python3
"""Daily A-share quantitative screening and paper-performance tracking.

Deterministic data pipeline:
  Eastmoney one-shot market snapshot -> Tencent adjusted daily bars -> factor scores
  -> beginner-friendly Markdown report -> next-open 1/5/20 day return tracking.

This program never places orders and never modifies factor weights automatically.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import shutil
import sys
import time
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests


SNAPSHOT_URLS = [
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://82.push2.eastmoney.com/api/qt/clist/get",
]
TENCENT_KLINE_URLS = [
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get",
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
]
SINA_KLINE_URL = "https://quotes.sina.cn/cn/api/openapi.php/CN_MarketDataService.getKLineData"
EASTMONEY_INDUSTRY_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"
SINA_INDUSTRY_URL = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
SINA_LIVE_URL = "https://zhibo.sina.com.cn/api/zhibo/feed"
CNINFO_MAP_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_ANNOUNCEMENT_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
SINA_MARKET_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
SINA_FINANCE_URL = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
THS_COMPANY_URL = "https://basic.10jqka.com.cn/new/{code}/company.html"
THS_HOT_REASON_URL = "http://zx.10jqka.com.cn/event/api/getharden/date/{date}/orderby/date/orderway/desc/charset/GBK/"
CLS_TELEGRAPH_URL = "https://www.cls.cn/v1/roll/get_roll_list"
EASTMONEY_GLOBAL_NEWS_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
SH_INDEX = {"000001", "000300", "000905", "000016", "000688", "000852", "000010"}

DEFAULT_CONFIG: dict[str, Any] = {
    "strategy_name": "A股多因子趋势观察池",
    "strategy_version": "1.0.0",
    "report_version": "1.3.0",
    "preselect_count": 140,
    "candidate_count": 10,
    "max_per_industry": 2,
    "min_price": 3.0,
    "min_daily_amount_yi": 1.0,
    "min_kline_bars": 125,
    "kline_bars": 180,
    "max_workers": 8,
    "single_side_fee_rate": 0.0003,
    "round_trip_slippage_rate": 0.0010,
    "weights": {
        "momentum": 0.30,
        "trend": 0.25,
        "volume_price": 0.20,
        "valuation": 0.10,
        "risk": 0.15,
    },
}

SIGNAL_COLUMNS = [
    "signal_date", "strategy_version", "code", "name", "industry", "rank",
    "total_score", "momentum_score", "trend_score", "volume_price_score",
    "valuation_score", "risk_score", "signal_close", "entry_date", "entry_open",
    "ret_1d", "ret_5d", "ret_20d", "benchmark_ret_1d", "benchmark_ret_5d",
    "benchmark_ret_20d", "excess_1d", "excess_5d", "excess_20d",
]


@dataclass
class RunPaths:
    root: Path
    daily: Path
    state: Path
    config: Path
    signals: Path
    equity: Path


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def create_paths(root: Path) -> RunPaths:
    daily = root / "daily"
    state = root / "state"
    daily.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    return RunPaths(root, daily, state, root / "config.json", state / "signals.csv", state / "equity_curve.csv")


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    config = json.loads(path.read_text(encoding="utf-8"))
    config.setdefault("report_version", DEFAULT_CONFIG["report_version"])
    weights = config.get("weights", {})
    expected = {"momentum", "trend", "volume_price", "valuation", "risk"}
    if set(weights) != expected:
        raise ValueError(f"weights字段必须正好包含: {sorted(expected)}")
    if any(not math.isfinite(float(v)) or float(v) < 0 for v in weights.values()):
        raise ValueError("因子权重必须是非负有限数值")
    total = sum(float(v) for v in weights.values())
    if not math.isclose(total, 1.0, abs_tol=1e-6):
        raise ValueError(f"因子权重合计必须为1，当前为{total}")
    return config


def market_prefix(code: str) -> str:
    if code in SH_INDEX:
        return "sh"
    if code.startswith(("5", "6", "9")):
        return "sh"
    if code.startswith("92"):
        return "bj"
    return "sz"


def _number(value: Any) -> float:
    try:
        return float(value) if value not in (None, "", "--") else np.nan
    except (TypeError, ValueError):
        return np.nan


def fetch_company_profile(code: str, timeout: int = 20) -> dict[str, str]:
    """Fetch static company/industry/business fields for final candidates only."""
    response = requests.get(
        THS_COMPANY_URL.format(code=code),
        headers={"User-Agent": UA, "Referer": f"https://basic.10jqka.com.cn/{code}/"},
        timeout=timeout,
    )
    response.raise_for_status()
    response.encoding = "gbk"
    source = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", response.text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", source))
    text = re.sub(r"[\s\u3000]+", " ", text)

    def take(label: str, following: list[str]) -> str:
        stop = "|".join(re.escape(item) for item in following)
        match = re.search(rf"{re.escape(label)}\s*[：:]\s*(.*?)(?={stop}\s*[：:]|$)", text)
        return match.group(1).strip(" ；;") if match else ""

    return {
        "region": take("所属地域", ["英文名称", "所属申万行业", "曾 用 名", "公司网址", "主营业务"]),
        "sw_industry": take("所属申万行业", ["曾 用 名", "公司网址", "主营业务"]),
        "main_business": take("主营业务", ["产品名称", "可能存在产品"]),
        "products": take("产品名称", ["可能存在产品"]),
    }


def fetch_finance_statement(code: str, source: str, timeout: int = 20) -> tuple[str, dict[str, dict[str, Any]]]:
    response = requests.get(
        SINA_FINANCE_URL,
        params={"paperCode": f"{market_prefix(code)}{code}", "source": source, "type": 0, "page": 1, "num": 8},
        headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"},
        timeout=timeout,
    )
    response.raise_for_status()
    response.encoding = "utf-8"
    report_list = (((response.json().get("result") or {}).get("data") or {}).get("report_list") or {})
    if not report_list:
        return "", {}
    period = sorted(report_list.keys(), reverse=True)[0]
    rows = (report_list.get(period) or {}).get("data") or []
    mapped = {
        str(item.get("item_title") or "").strip(): {
            "value": _number(item.get("item_value")), "yoy": _number(item.get("item_tongbi"))
        }
        for item in rows if item.get("item_title")
    }
    return period, mapped


def _statement_item(statement: dict[str, dict[str, Any]], names: list[str]) -> tuple[float, float]:
    for name in names:
        if name in statement:
            return statement[name]["value"], statement[name]["yoy"]
    for title, item in statement.items():
        if any(name in title for name in names):
            return item["value"], item["yoy"]
    return np.nan, np.nan


def fetch_fundamental(code: str) -> dict[str, Any]:
    profile = fetch_company_profile(code)
    income_period, income = fetch_finance_statement(code, "lrb")
    balance_period, balance = fetch_finance_statement(code, "fzb")
    cash_period, cash = fetch_finance_statement(code, "llb")
    revenue, revenue_yoy = _statement_item(income, ["营业总收入", "营业收入"])
    net_profit, net_profit_yoy = _statement_item(
        income, ["归属于母公司所有者的净利润", "归属于母公司股东的净利润", "归母净利润", "净利润"]
    )
    assets, _ = _statement_item(balance, ["资产总计"])
    liabilities, _ = _statement_item(balance, ["负债合计"])
    operating_cash, operating_cash_yoy = _statement_item(cash, ["经营活动产生的现金流量净额"])
    return {
        "code": code, **profile,
        "finance_period": income_period or balance_period or cash_period,
        "balance_period": balance_period, "cash_period": cash_period,
        "revenue": revenue, "revenue_yoy": revenue_yoy,
        "net_profit": net_profit, "net_profit_yoy": net_profit_yoy,
        "operating_cash_flow": operating_cash, "operating_cash_flow_yoy": operating_cash_yoy,
        "total_assets": assets, "total_liabilities": liabilities,
        "net_margin": net_profit / revenue if pd.notna(net_profit) and pd.notna(revenue) and revenue else np.nan,
        "cash_profit_ratio": operating_cash / net_profit if pd.notna(operating_cash) and pd.notna(net_profit) and net_profit else np.nan,
        "debt_ratio": liabilities / assets if pd.notna(liabilities) and pd.notna(assets) and assets else np.nan,
    }


def fetch_many_fundamentals(codes: list[str], workers: int = 4) -> tuple[pd.DataFrame, list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_fundamental, code): code for code in dict.fromkeys(codes)}
        for future in as_completed(futures):
            code = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:
                errors.append(f"{code}: {exc}")
                rows.append({"code": code})
    return pd.DataFrame(rows), errors


def fetch_industry_dynamics(industry_names: list[str]) -> tuple[dict[str, Any], str | None]:
    """Fetch same-day industry breadth/ranking once, then match candidate industries."""
    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2",
        "fid": "f3", "fs": "m:90+t:2",
        "fields": "f2,f3,f4,f12,f14,f104,f105,f136,f140",
    }
    try:
        response = requests.get(EASTMONEY_INDUSTRY_URL, params=params, headers={"User-Agent": UA}, timeout=20)
        response.raise_for_status()
        items = ((response.json().get("data") or {}).get("diff") or [])
        if isinstance(items, dict): items = list(items.values())
        rows = [{"name": str(x.get("f14") or ""), "change_pct": _number(x.get("f3")), "up_count": _number(x.get("f104")), "down_count": _number(x.get("f105")), "leader": str(x.get("f140") or "")} for x in items]
        if not rows: raise RuntimeError("东方财富行业板块返回空数据")
        matched = {}
        for industry in industry_names:
            tokens = [token for token in re.split(r"\s*[—-]\s*|\s+", industry) if len(token) >= 2]
            candidates = [row for row in rows if any(token in row["name"] or row["name"] in token for token in tokens)]
            if candidates:
                matched[industry] = candidates[0]
        return {"rows": rows, "matched": matched, "top": sorted(rows, key=lambda x: x["change_pct"], reverse=True)[:5], "bottom": sorted(rows, key=lambda x: x["change_pct"])[:5], "total": len(rows)}, None
    except Exception as exc:
        primary_error = str(exc)
    try:
        response = requests.get(SINA_INDUSTRY_URL, headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"}, timeout=20)
        response.raise_for_status(); response.encoding = "gbk"
        payload = json.loads(response.text.split("=", 1)[1].strip().rstrip(";"))
        rows = []
        for raw in payload.values():
            fields = str(raw).split(",")
            if len(fields) < 13: continue
            rows.append({"name": fields[1], "change_pct": _number(fields[5]), "up_count": np.nan, "down_count": np.nan, "constituent_count": _number(fields[2]), "amount": _number(fields[7]), "leader": fields[12], "source": "新浪行业"})
        aliases = {"航运港口": ["水上运输", "港口"], "基础化工": ["化工行业", "化纤行业"], "化学纤维": ["化纤行业"], "化学制品": ["化工行业"], "养殖业": ["农林牧渔"], "白色家电": ["电器行业"], "银行": ["银行"], "保险": ["保险"], "医疗服务": ["医疗器械", "生物制药"]}
        matched = {}
        for industry in industry_names:
            tokens = [token for token in re.split(r"\s*[—-]\s*|\s+", industry) if len(token) >= 2]
            expanded = tokens + [alias for token in tokens for alias in aliases.get(token, [])]
            candidates = [row for row in rows if any(token in row["name"] or row["name"] in token for token in expanded)]
            if candidates: matched[industry] = candidates[0]
        return {"rows": rows, "matched": matched, "top": sorted(rows, key=lambda x: x["change_pct"], reverse=True)[:5], "bottom": sorted(rows, key=lambda x: x["change_pct"])[:5], "total": len(rows), "source": "新浪行业备用源"}, f"东方财富行业源失败，已降级新浪行业：{primary_error}"
    except Exception as fallback_exc:
        return {"matched": {}, "top": [], "bottom": [], "total": 0}, f"东财失败：{primary_error}；新浪备用源失败：{fallback_exc}"


def fetch_hot_reasons(signal_date: str, timeout: int = 20) -> tuple[list[dict[str, str]], str | None]:
    """Fetch editor-maintained reason tags for same-day strong stocks."""
    try:
        response = requests.get(
            THS_HOT_REASON_URL.format(date=signal_date),
            headers={"User-Agent": UA, "Referer": "http://zx.10jqka.com.cn/"}, timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errocode", 0) != 0:
            raise RuntimeError(payload.get("errormsg") or "返回错误状态")
        rows = []
        for item in payload.get("data") or []:
            code = str(item.get("code") or "").zfill(6)
            reason = str(item.get("reason") or "").strip()
            if re.fullmatch(r"\d{6}", code) and reason:
                rows.append({"code": code, "name": str(item.get("name") or ""), "reason": reason})
        if not rows:
            raise RuntimeError("当日题材归因返回空数据")
        return rows, None
    except Exception as exc:
        return [], f"同花顺热点题材归因失败：{exc}"


def fetch_index_overview() -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch major-index daily performance with identical K-line conventions."""
    index_map = {"000001": "上证指数", "399001": "深证成指", "399006": "创业板指", "000300": "沪深300"}
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_kline, code, 25): (code, name) for code, name in index_map.items()}
        for future in as_completed(futures):
            code, name = futures[future]
            try:
                bars = future.result().sort_values("date").reset_index(drop=True)
                if len(bars) < 2:
                    raise RuntimeError("K线不足2日")
                close = float(bars.iloc[-1]["close"])
                rows.append({
                    "code": code, "name": name, "date": str(bars.iloc[-1]["date"]), "close": close,
                    "ret_1d": close / float(bars.iloc[-2]["close"]) - 1,
                    "ret_5d": close / float(bars.iloc[-6]["close"]) - 1 if len(bars) >= 6 else np.nan,
                    "ret_20d": close / float(bars.iloc[-21]["close"]) - 1 if len(bars) >= 21 else np.nan,
                    "source": str(getattr(bars, "attrs", {}).get("source", "")),
                })
            except Exception as exc:
                errors.append(f"{name}：{exc}")
    order = {code: i for i, code in enumerate(index_map)}
    return sorted(rows, key=lambda x: order[x["code"]]), errors


def fetch_market_context(signal_date: str, page_size: int = 80) -> dict[str, Any]:
    """Collect independent market-information feeds for the narrative section.

    This is deliberately a data-collection layer rather than an LLM-generated
    opinion.  Headlines are retained with source links and are only used as
    same-day information clues; they are never treated as proven causes of
    price moves.
    """
    rows: list[dict[str, Any]] = []
    errors: list[str] = []

    # 财联社：A股相关快讯，独立于行情和同花顺题材接口。
    try:
        params = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5",
                  "last_time": "", "refresh_type": "1", "rn": str(page_size)}
        qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
        sign = hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()
        response = requests.get(
            f"{CLS_TELEGRAPH_URL}?{qs}&sign={sign}",
            headers={"User-Agent": UA, "Referer": "https://www.cls.cn/"}, timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        for item in (payload.get("data") or {}).get("roll_data", []) or []:
            ts = item.get("ctime")
            item_time = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
            if item_time and item_time[:10] > signal_date:
                continue
            title = str(item.get("title") or item.get("brief") or "").strip()
            content = str(item.get("content") or item.get("brief") or "").strip()
            if title:
                rows.append({"time": item_time, "title": title, "summary": content[:180],
                             "source": "财联社", "url": str(item.get("url") or item.get("share_url") or "https://www.cls.cn/")})
    except Exception as exc:
        errors.append(f"财联社快讯失败：{exc}")

    # 东方财富7×24：作为独立备源，保留摘要与来源首页链接。
    try:
        params = {"client": "web", "biz": "web_724", "fastColumn": "102",
                  "sortEnd": "", "pageSize": str(page_size), "req_trace": str(uuid.uuid4())}
        response = requests.get(
            EASTMONEY_GLOBAL_NEWS_URL, params=params,
            headers={"User-Agent": UA, "Referer": "https://kuaixun.eastmoney.com/"}, timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        for item in (payload.get("data") or {}).get("fastNewsList", []) or []:
            item_time = str(item.get("showTime") or "")
            if item_time and item_time[:10] > signal_date:
                continue
            title = str(item.get("title") or "").strip()
            if title:
                rows.append({"time": item_time, "title": title,
                             "summary": str(item.get("summary") or "")[:180],
                             "source": "东方财富7×24", "url": str(item.get("url") or item.get("newsUrl") or "https://kuaixun.eastmoney.com/")})
    except Exception as exc:
        errors.append(f"东方财富7×24失败：{exc}")

    # 过滤与A股市场关系弱的泛国际新闻，避免用无关快讯充当市场解释。
    market_terms = [
        "A股", "上证", "深证", "创业板", "沪深300", "北向", "两融", "融资", "证券", "银行",
        "央行", "利率", "降准", "降息", "国债", "人民币", "政策", "监管", "证监会", "国务院",
        "国务院", "产业", "算力", "人工智能", "芯片", "半导体", "机器人", "新能源", "光伏",
        "锂电", "汽车", "医药", "军工", "化工", "煤炭", "石油", "黄金", "有色", "房地产",
        "消费", "出口", "进口", "PMI", "GDP", "CPI", "PPI", "业绩", "公告", "增持", "回购",
        "减持", "订单", "并购", "涨停", "跌停", "龙虎榜", "主力", "资金", "财政", "发改委",
        "专项债", "以旧换新", "补贴", "关税", "制裁", "出口管制", "汇率", "美元指数", "楼市",
        "住房", "地产", "储能", "稀土", "铜", "钢铁", "航运", "港口", "低空经济",
    ]
    relevant_rows = [
        item for item in rows
        if any(term.lower() in str(item.get('title', '')).lower() for term in market_terms)
    ]
    rows = relevant_rows

    # 去重并按时间倒序；关键词只做“信息主题分布”，不做因果判断。
    unique: dict[str, dict[str, Any]] = {}
    for item in rows:
        key = re.sub(r"\s+", "", item["title"])
        unique.setdefault(key, item)
    rows = sorted(unique.values(), key=lambda x: str(x.get("time") or ""), reverse=True)
    topic_words = {
        "政策监管": ["政策", "监管", "会议", "规划", "改革", "发布"],
        "宏观经济": ["央行", "利率", "通胀", "经济", "数据", "GDP", "PMI"],
        "产业与科技": ["AI", "人工智能", "芯片", "算力", "机器人", "新能源", "低空"],
        "商品与周期": ["原油", "油价", "黄金", "煤炭", "化工", "有色", "期货"],
        "公司与业绩": ["业绩", "增持", "回购", "公告", "订单", "并购", "减持"],
        "海外市场": ["美股", "纳指", "美联储", "美元", "海外", "地缘"],
    }
    topic_counts = []
    joined = " ".join(f"{x.get('title','')} {x.get('summary','')}" for x in rows)
    for topic, words in topic_words.items():
        count = sum(joined.lower().count(word.lower()) for word in words)
        if count:
            topic_counts.append((topic, count))
    topic_counts.sort(key=lambda x: (-x[1], x[0]))
    # 事件识别只做“可核验的影响线索”，不把新闻直接解释成价格因果。
    # 规则优先选择有明确传导路径的宏观、海外和产业事件，保留原文链接。
    event_rules = [
        {
            "keywords": ["美联储", "欧洲央行", "欧央行", "波兰央行", "英国央行", "日本央行"],
            "event_type": "海外央行与全球利率预期",
            "priority": 6,
            "impact": "全球利率预期偏紧时，通常压制风险偏好和高估值成长方向",
            "transmission": "海外政策利率和加息预期会影响美元、全球债券收益率和资金风险偏好，随后通过估值锚与外资风险预算传导至A股。",
            "sectors": "半导体、人工智能、软件等高估值成长方向；银行、保险和出口链的影响取决于汇率及国内政策对冲",
        },
        {
            "keywords": ["美债收益率", "国债收益率", "美国三年期", "美国五年期"],
            "event_type": "海外利率与风险偏好",
            "priority": 5,
            "impact": "偏压制成长估值、抬高全球风险资产波动",
            "transmission": "无风险利率上行通常会压缩高估值成长股的估值空间，并可能推升美元与资金避险需求。",
            "sectors": "半导体、人工智能、软件等高估值成长方向；银行、保险的相对表现需结合国内利率环境判断",
        },
        {
            "keywords": ["费城半导体指数", "半导体指数"],
            "event_type": "海外科技板块波动",
            "priority": 5,
            "impact": "对A股科技成长情绪偏负面",
            "transmission": "海外半导体指数走弱可能通过行业情绪、估值锚和产业链预期影响A股科技股，但不等同于A股基本面改变。",
            "sectors": "半导体、光模块、消费电子、算力",
        },
        {
            "keywords": ["降准", "降息", "LPR", "流动性投放", "中国人民银行", "人民银行", "公开市场操作"],
            "event_type": "国内货币政策与流动性",
            "priority": 5,
            "impact": "通常利好风险偏好和利率敏感资产，银行净息差影响可能相反",
            "transmission": "流动性改善可能降低融资成本、提升估值；银行股还要同时考虑净息差和资产质量。",
            "sectors": "券商、地产、成长股；银行、保险需结合息差和债券收益率",
        },
        {
            "keywords": ["社会融资", "社融", "新增贷款", "贷款减少", "信贷", "M1", "M2"],
            "event_type": "国内信用与融资数据",
            "priority": 5,
            "impact": "影响经济修复、企业融资和金融机构资产扩张预期",
            "transmission": "信用总量和结构反映实体融资需求；居民、企业贷款与直接融资的变化，对地产、消费、成长企业和银行的含义不同。",
            "sectors": "银行、券商、房地产、消费及融资需求较高的成长行业",
        },
        {
            "keywords": ["证监会", "金融监管总局", "证券监管", "资本市场改革", "证券交易所"],
            "event_type": "资本市场政策与监管",
            "priority": 4,
            "impact": "影响市场风险偏好、交易制度或相关行业预期",
            "transmission": "政策的覆盖范围、落地时间和执行细则决定影响强度，单条快讯通常只能作为方向线索。",
            "sectors": "券商、交易所相关业务及政策直接涉及的行业",
        },
        {
            "keywords": ["财政部", "发改委", "专项债", "超长期特别国债", "以旧换新", "促消费", "消费补贴"],
            "event_type": "财政政策与内需支持",
            "priority": 5,
            "impact": "可能改善内需、基建或设备更新相关行业的订单和盈利预期",
            "transmission": "财政支出、专项债和补贴政策通常经由项目投资、居民消费与企业订单传导；实际影响取决于资金规模、落地速度和政策覆盖范围。",
            "sectors": "建筑建材、工程机械、家电、汽车、消费及政策明确支持的设备更新方向",
        },
        {
            "keywords": ["人民币", "美元指数", "汇率", "外汇市场"],
            "event_type": "人民币汇率与跨境资金预期",
            "priority": 4,
            "impact": "影响外资风险偏好、进口成本和出口企业汇兑预期",
            "transmission": "汇率变化会影响跨境资金配置、原材料进口成本及出口收入折算，但不同行业和公司的受益方向并不一致。",
            "sectors": "出口制造、航空、造纸、资源进口链及外资偏好的核心资产",
        },
        {
            "keywords": ["关税", "贸易摩擦", "出口管制", "制裁", "贸易谈判", "地缘冲突", "停火", "冲突升级", "俄军", "乌克兰", "霍尔木兹", "美伊", "中东局势"],
            "event_type": "贸易与地缘风险",
            "priority": 5,
            "impact": "可能提高风险溢价并改变出口链、能源和国防相关行业预期",
            "transmission": "贸易限制会影响订单、供应链与成本，地缘事件还可能通过能源价格、航运费率和避险情绪传导至A股。",
            "sectors": "出口制造、半导体供应链、军工、黄金、石油、航运",
        },
        {
            "keywords": ["房地产", "楼市", "住房", "房贷", "城中村", "土地市场"],
            "event_type": "房地产政策与信用周期",
            "priority": 4,
            "impact": "影响地产销售、信用预期及上下游需求",
            "transmission": "购房政策、融资环境和销售数据会经由开发投资、居民资产负债表及地方财政影响地产链和金融板块。",
            "sectors": "房地产、建筑建材、家居家电、银行和保险",
        },
        {
            "keywords": ["人工智能", "算力", "芯片", "半导体", "机器人", "低空经济", "数据中心", "数据跨境", "数字经济"],
            "event_type": "科技产业与政策进展",
            "priority": 4,
            "impact": "可能改变科技成长板块的订单、资本开支和估值预期",
            "transmission": "产业政策、技术进展和大额订单通常先影响主题交易与估值，随后才由收入和利润兑现验证。",
            "sectors": "半导体、计算机、通信、人工智能、机器人及相关设备供应链",
        },
        {
            "keywords": ["光伏", "锂电", "新能源汽车", "储能", "风电"],
            "event_type": "新能源产业变化",
            "priority": 4,
            "impact": "影响新能源产业链的需求、价格和产能出清预期",
            "transmission": "政策、装机、销量、出口与产品价格变化会传导至排产和盈利，但上下游受益方向可能相反。",
            "sectors": "光伏、锂电池、新能源汽车、储能、风电及电网设备",
        },
        {
            "keywords": ["造船", "船厂", "船舶订单", "海工装备"],
            "event_type": "船舶订单与制造周期",
            "priority": 4,
            "impact": "对船舶制造、海工装备景气预期偏正面",
            "transmission": "订单增长和排产延长可能改善收入可见度；钢材、汇率和交付节奏会影响利润兑现。",
            "sectors": "船舶制造、海工装备、钢铁上游",
        },
        {
            "keywords": ["原油", "油价", "天然气", "石油", "OPEC"],
            "event_type": "能源价格变化",
            "priority": 4,
            "impact": "利好上游资源品，抬高下游化工和运输成本",
            "transmission": "油价通过产品价格、库存损益和成本端传导，方向取决于企业在产业链中的位置。",
            "sectors": "石油开采、炼化、化工、航空、航运",
        },
        {
            "keywords": ["黄金", "铜价", "铜", "铝价", "稀土", "煤价", "钢材", "铁矿石"],
            "event_type": "大宗商品与资源品价格",
            "priority": 4,
            "impact": "影响资源企业利润、制造业成本及通胀预期",
            "transmission": "商品价格上涨通常改善上游资源企业收入，却可能抬高中下游成本；需结合库存、加工费和企业套保情况判断。",
            "sectors": "有色金属、黄金、煤炭、钢铁、化工及相关中下游制造业",
        },
        {
            "keywords": ["PMI", "GDP", "CPI", "PPI", "经济数据"],
            "event_type": "国内宏观数据",
            "priority": 4,
            "impact": "影响经济周期预期和行业盈利预测",
            "transmission": "数据相对预期的偏离比绝对数值更重要，需要与政策和后续高频数据交叉验证。",
            "sectors": "周期、金融、消费和出口链",
        },
        {
            "keywords": ["回购", "增持", "减持", "业绩预告", "业绩增长", "订单公告", "并购"],
            "event_type": "上市公司公告与业绩线索",
            "priority": 3,
            "impact": "主要影响公告公司及其产业链，不宜直接外推至全市场",
            "transmission": "公告兑现程度、规模和持续性决定影响，需查看原文和公司后续披露。",
            "sectors": "公告涉及的公司、行业及上下游",
        },
    ]
    events: list[dict[str, Any]] = []
    for item in rows:
        # 事件类型必须由标题触发，避免正文摘要中的上下文词把无关公司新闻误归因到某个行业。
        title_text = str(item.get("title") or "")
        matches = [rule for rule in event_rules if any(k.lower() in title_text.lower() for k in rule["keywords"])]
        if not matches:
            continue
        rule = max(matches, key=lambda x: x["priority"])
        # “财政部”等机构名称在多国新闻中都会出现；国内政策类事件必须排除标题已明确指向的纯海外政策。
        foreign_policy_markers = ["越南", "韩国", "日本", "英国", "法国", "德国", "意大利", "加拿大", "澳大利亚", "印度", "沙特"]
        domestic_only_types = {"财政政策与内需支持", "国内货币政策与流动性", "国内信用与融资数据", "资本市场政策与监管", "房地产政策与信用周期", "国内宏观数据"}
        if rule["event_type"] in domestic_only_types and any(marker in title_text for marker in foreign_policy_markers):
            continue
        if rule["event_type"] == "科技产业与政策进展":
            domestic_market_anchors = ["A股", "中国", "我国", "国内", "板块", "ETF", "产业", "政策", "广东", "深圳", "上海", "北京", "科创"]
            if not any(anchor.lower() in title_text.lower() for anchor in domestic_market_anchors):
                continue
        events.append({
            "time": item.get("time", ""),
            "title": item.get("title", ""),
            "summary": item.get("summary", ""),
            "source": item.get("source", ""),
            "url": item.get("url") or "https://www.cls.cn/",
            "event_type": rule["event_type"],
            "priority": rule["priority"],
            "impact": rule["impact"],
            "transmission": rule["transmission"],
            "sectors": rule["sectors"],
            "evidence": "新闻快讯同步线索，尚未证明为本日行情的唯一或直接原因",
        })
    event_unique: dict[str, dict[str, Any]] = {}
    for event in events:
        event_unique.setdefault(re.sub(r"\s+", "", event["title"]), event)
    # 同类事件允许保留两条，兼顾覆盖度与去重；优先级相同时保留较新的快讯。
    ranked_events = sorted(
        event_unique.values(),
        key=lambda x: (int(x.get("priority") or 0), str(x.get("time") or "")),
        reverse=True,
    )
    type_counts: dict[str, int] = {}
    selected_events: list[dict[str, Any]] = []
    # 第一轮每类取一条，先保证宏观、产业、商品和公司线索的覆盖面。
    for event in ranked_events:
        event_type = str(event.get("event_type") or "其他事件")
        if type_counts.get(event_type, 0) >= 1:
            continue
        selected_events.append(event)
        type_counts[event_type] = type_counts.get(event_type, 0) + 1
        if len(selected_events) >= 8:
            break
    # 类别不足八个时，第二轮允许重要类别再补一条，但仍不让单一主题占满报告。
    if len(selected_events) < 8:
        selected_titles = {str(x.get("title") or "") for x in selected_events}
        for event in ranked_events:
            if str(event.get("title") or "") in selected_titles:
                continue
            event_type = str(event.get("event_type") or "其他事件")
            if type_counts.get(event_type, 0) >= 2:
                continue
            selected_events.append(event)
            type_counts[event_type] = type_counts.get(event_type, 0) + 1
            if len(selected_events) >= 8:
                break
    events = selected_events
    return {
        "items": rows[:20], "topics": topic_counts[:6], "events": events,
        "event_candidate_count": len(event_unique), "relevant_headline_count": len(rows),
        "errors": errors, "sources": sorted({str(x.get("source")) for x in rows if x.get("source")}),
    }


def build_market_overview(
    snapshot: pd.DataFrame, snapshot_source: str, indices: list[dict[str, Any]],
    industry_dynamics: dict[str, Any], hot_reasons: list[dict[str, str]], errors: list[str],
) -> dict[str, Any]:
    """Build deterministic daily-market breadth, style, anomaly and attribution observations."""
    frame = snapshot.copy()
    frame["change_pct"] = pd.to_numeric(frame.get("change_pct"), errors="coerce")
    frame["amount"] = pd.to_numeric(frame.get("amount"), errors="coerce")
    frame["turnover_pct"] = pd.to_numeric(frame.get("turnover_pct"), errors="coerce")
    valid = frame.dropna(subset=["change_pct"])
    up = int((valid["change_pct"] > 0).sum())
    down = int((valid["change_pct"] < 0).sum())
    flat = int((valid["change_pct"] == 0).sum())
    total = max(len(valid), 1)
    up_ratio = up / total
    if up_ratio >= 0.65:
        breadth_text = "上涨覆盖面较广，市场赚钱效应偏强"
    elif up_ratio <= 0.35:
        breadth_text = "下跌家数占优，市场赚钱效应偏弱"
    else:
        breadth_text = "涨跌分化，机会主要集中在部分行业和题材"

    index_by_code = {x["code"]: x for x in indices}
    csi300 = index_by_code.get("000300", {}).get("ret_1d", np.nan)
    gem = index_by_code.get("399006", {}).get("ret_1d", np.nan)
    style_text = "主要指数数据不足，暂不判断风格"
    if pd.notna(csi300) and pd.notna(gem):
        gap = gem - csi300
        if gap >= 0.008:
            style_text = f"创业板指较沪深300多涨{gap*100:.2f}个百分点，成长风格相对占优"
        elif gap <= -0.008:
            style_text = f"沪深300较创业板指多涨{-gap*100:.2f}个百分点，大盘蓝筹相对占优"
        else:
            style_text = f"创业板指与沪深300涨幅差{abs(gap)*100:.2f}个百分点，风格差异不显著"

    amount_frame = frame.dropna(subset=["amount"]).sort_values("amount", ascending=False)
    turnover_frame = frame.dropna(subset=["turnover_pct"]).sort_values("turnover_pct", ascending=False)
    quote_map = (
        frame.drop_duplicates(subset=["code"], keep="first").set_index("code").to_dict("index")
        if "code" in frame else {}
    )
    anomalies = []
    for item in hot_reasons:
        quote = quote_map.get(item["code"], {})
        anomalies.append({
            **item, "change_pct": _number(quote.get("change_pct")),
            "turnover_pct": _number(quote.get("turnover_pct")), "amount": _number(quote.get("amount")),
        })
    anomalies.sort(key=lambda x: (-999 if pd.isna(x["change_pct"]) else x["change_pct"]), reverse=True)

    theme_counts: dict[str, int] = {}
    for item in hot_reasons:
        for tag in re.split(r"[+＋、/|]", item["reason"]):
            tag = tag.strip()
            if 2 <= len(tag) <= 16:
                theme_counts[tag] = theme_counts.get(tag, 0) + 1
    themes = sorted(theme_counts.items(), key=lambda x: (-x[1], x[0]))[:8]
    industry_keywords = {
        "农林牧渔": ["种业", "转基因", "粮食", "农业", "养殖", "水产", "饲料"],
        "商业百货": ["零售", "百货", "消费", "电商", "免税"],
        "传媒娱乐": ["传媒", "出版", "影视", "短剧", "游戏", "广告", "AIGC"],
        "金融行业": ["银行", "保险", "证券", "金融", "数字人民币"],
        "纺织行业": ["纺织", "服装", "家纺"],
        "电子器件": ["芯片", "半导体", "消费电子", "PCB", "光模块"],
        "有色金属": ["黄金", "铜", "铝", "锂", "稀土", "有色"],
        "化工行业": ["化工", "化学", "甲醇", "农药", "化肥"],
    }
    industry_clues = []
    for industry_row in industry_dynamics.get("top", []):
        industry_name = str(industry_row.get("name") or "")
        keywords = industry_keywords.get(industry_name, [industry_name])
        matched_hot = [
            item for item in hot_reasons
            if any(keyword and keyword.lower() in item["reason"].lower() for keyword in keywords)
        ][:4]
        industry_clues.append({"industry": industry_name, "items": matched_hot})
    index_by_code = {x["code"]: x for x in indices}
    sh_ret = index_by_code.get("000001", {}).get("ret_1d", np.nan)
    csi300_ret = index_by_code.get("000300", {}).get("ret_1d", np.nan)
    if up_ratio <= 0.35 and pd.notna(sh_ret) and sh_ret < 0:
        regime, regime_explain = "下跌分化", "指数走弱且下跌家数占优，优先控制追涨和组合回撤风险。"
    elif up_ratio >= 0.65 and pd.notna(sh_ret) and sh_ret > 0:
        regime, regime_explain = "普涨偏强", "指数与个股上涨面同步，趋势型信号的环境相对友好。"
    else:
        regime, regime_explain = "震荡分化", "指数方向与个股涨跌并不完全一致，行业和个股选择比单纯追随指数更重要。"
    market_beta_note = (f"上证相对沪深300差{(sh_ret-csi300_ret)*100:+.2f}个百分点"
                        if pd.notna(sh_ret) and pd.notna(csi300_ret) else "主要指数相对表现数据不足")
    return {
        "indices": indices, "up": up, "down": down, "flat": flat, "coverage": len(valid),
        "up_ratio": up_ratio, "median_change": valid["change_pct"].median(),
        "rise_3": int((valid["change_pct"] >= 3).sum()), "fall_3": int((valid["change_pct"] <= -3).sum()),
        "near_up": int((valid["change_pct"] >= 9.5).sum()), "near_down": int((valid["change_pct"] <= -9.5).sum()),
        "amount_sum": amount_frame["amount"].sum(), "median_turnover": turnover_frame["turnover_pct"].median(),
        "top_amount": amount_frame.head(5).to_dict("records"), "top_turnover": turnover_frame.head(5).to_dict("records"),
        "breadth_text": breadth_text, "style_text": style_text,
        "anomalies": anomalies[:10], "hot_reason_count": len(hot_reasons), "themes": themes,
        "industry_clues": industry_clues,
        "regime": regime, "regime_explain": regime_explain, "market_beta_note": market_beta_note,
        "snapshot_source": snapshot_source, "errors": errors,
    }


def fetch_stock_news(code: str, page_size: int = 5) -> list[dict[str, str]]:
    inner = json.dumps({"uid": "", "keyword": code, "type": ["cmsArticleWebOld"], "client": "web", "clientType": "web", "clientVersion": "curr", "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default", "pageIndex": 1, "pageSize": page_size, "preTag": "", "postTag": ""}}}, separators=(",", ":"))
    response = requests.get(EASTMONEY_NEWS_URL, params={"cb": "jQuery_news", "param": inner}, headers={"User-Agent": UA, "Referer": "https://so.eastmoney.com/"}, timeout=20)
    response.raise_for_status()
    raw = response.text
    payload = json.loads(raw[raw.index("(") + 1:raw.rindex(")")])
    articles = ((payload.get("result") or {}).get("cmsArticleWebOld") or [])
    return [{"title": re.sub(r"<[^>]+>", "", str(a.get("title") or "")).strip(), "time": str(a.get("date") or ""), "source": str(a.get("mediaName") or ""), "url": str(a.get("url") or "")} for a in articles[:page_size]]


def fetch_many_news(codes: list[str], workers: int = 4) -> tuple[dict[str, list[dict[str, str]]], list[str]]:
    output: dict[str, list[dict[str, str]]] = {}; errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_stock_news, code): code for code in dict.fromkeys(codes)}
        for future in as_completed(futures):
            code = futures[future]
            try: output[code] = future.result()
            except Exception as exc: output[code] = []; errors.append(f"{code}: {exc}")
    missing = [code for code in codes if not output.get(code)]
    if missing:
        try:
            response = requests.get(SINA_LIVE_URL, params={"zhibo_id": 152, "page_size": 200, "dire": "f"}, headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"}, timeout=25)
            response.raise_for_status(); response.encoding = "utf-8"
            feeds = (((response.json().get("result") or {}).get("data") or {}).get("feed") or {}).get("list") or []
            for item in feeds:
                try: ext = json.loads(item.get("ext") or "{}")
                except Exception: ext = {}
                symbols = {str(stock.get("symbol") or "").zfill(6) for stock in ext.get("stocks", [])}
                title = re.sub(r"<[^>]+>", "", str(item.get("rich_text") or "")).strip()
                for code in symbols.intersection(missing):
                    output.setdefault(code, []).append({"title": title[:180], "time": str(item.get("create_time") or ""), "source": "新浪财经7×24", "url": str(item.get("docurl") or "")})
            for code in missing: output[code] = output.get(code, [])[:5]
        except Exception as exc:
            errors.append(f"新浪7×24备用源: {exc}")
    try:
        mapping_response = requests.get(CNINFO_MAP_URL, headers={"User-Agent": UA}, timeout=20)
        mapping_response.raise_for_status()
        org_map = {str(item["code"]): str(item["orgId"]) for item in mapping_response.json().get("stockList", [])}
        def announcements(code: str) -> list[dict[str, str]]:
            org_id = org_map.get(code, f"gssh0{code}" if code.startswith("6") else f"gssz0{code}")
            payload = {"stock": f"{code},{org_id}", "tabName": "fulltext", "pageSize": "3", "pageNum": "1", "column": "", "category": "", "plate": "", "seDate": "", "searchkey": "", "secid": "", "sortName": "", "sortType": "", "isHLtitle": "true"}
            headers = {"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded", "Referer": "https://www.cninfo.com.cn/new/disclosure", "Origin": "https://www.cninfo.com.cn"}
            response = requests.post(CNINFO_ANNOUNCEMENT_URL, data=payload, headers=headers, timeout=20)
            response.raise_for_status()
            rows = []
            for item in response.json().get("announcements", []) or []:
                timestamp = item.get("announcementTime")
                date_text = datetime.fromtimestamp(float(timestamp) / 1000, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d") if timestamp else ""
                rows.append({"title": str(item.get("announcementTitle") or ""), "time": date_text, "source": "巨潮资讯公告", "url": f"https://static.cninfo.com.cn/{item.get('adjunctUrl', '')}"})
            return rows
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(announcements, code): code for code in dict.fromkeys(codes)}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    existing_titles = {item["title"] for item in output.get(code, [])}
                    output.setdefault(code, []).extend(item for item in future.result() if item["title"] not in existing_titles)
                    output[code] = output[code][:5]
                except Exception as exc:
                    errors.append(f"{code}巨潮公告: {exc}")
    except Exception as exc:
        errors.append(f"巨潮股票映射: {exc}")
    return output, errors


def fetch_market_snapshot(timeout: int = 25) -> pd.DataFrame:
    base_params = {
        "pz": "1000", "po": "1", "np": "1", "fltt": "2", "invt": "2",
        "fid": "f6", "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f2,f3,f5,f6,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f62,f100",
    }
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
    items: list[dict[str, Any]] = []
    total = None
    for page in range(1, 8):
        payload = None
        last_error: Exception | None = None
        for attempt in range(3):
            url = SNAPSHOT_URLS[attempt % len(SNAPSHOT_URLS)]
            try:
                response = session.get(url, params={**base_params, "pn": str(page)}, timeout=timeout)
                response.raise_for_status()
                response.encoding = "utf-8"
                payload = response.json()
                break
            except Exception as exc:
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
        if payload is None:
            raise RuntimeError(f"东财全市场快照第{page}页连续失败: {last_error}")
        data = payload.get("data") or {}
        if total is None:
            total = int(data.get("total") or 0)
        page_items = data.get("diff") or []
        if isinstance(page_items, dict):
            page_items = list(page_items.values())
        if not page_items:
            break
        items.extend(page_items)
        if total and len(items) >= total:
            break
        time.sleep(1.1)
    if len(items) < 300:
        raise RuntimeError(f"东财流动性股票池异常，仅返回{len(items)}条")
    rename = {
        "f2": "price", "f3": "change_pct", "f5": "volume", "f6": "amount",
        "f8": "turnover_pct", "f9": "pe", "f10": "volume_ratio", "f12": "code",
        "f14": "name", "f15": "high", "f16": "low", "f17": "open", "f18": "prev_close",
        "f20": "market_cap", "f21": "float_market_cap", "f23": "pb", "f24": "return_60d_snapshot",
        "f25": "return_ytd", "f62": "main_net", "f100": "industry",
    }
    frame = pd.DataFrame(items).rename(columns=rename)
    for col in rename.values():
        if col not in frame.columns:
            frame[col] = np.nan
    numeric = [c for c in rename.values() if c not in {"code", "name", "industry"}]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["name"] = frame["name"].fillna("").astype(str)
    frame["industry"] = frame["industry"].fillna("未分类").astype(str)
    total_text = f"，市场总数{total}" if total else ""
    frame.attrs["source"] = f"东方财富A股成交额前{len(frame)}只（高流动性股票池{total_text}）"
    return frame


def fetch_sina_liquid_snapshot(pages: int = 10, timeout: int = 20) -> pd.DataFrame:
    """Independent fallback: top liquid A-shares sorted by current-day amount."""
    rows: list[dict[str, Any]] = []
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"})
    for page in range(1, pages + 1):
        params = {
            "page": page, "num": 100, "sort": "amount", "asc": 0,
            "node": "hs_a", "symbol": "", "_s_r_a": "page",
        }
        response = session.get(SINA_MARKET_URL, params=params, timeout=timeout)
        response.raise_for_status()
        batch = response.json() or []
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 100:
            break
        time.sleep(0.15)
    if len(rows) < 300:
        raise RuntimeError(f"新浪流动性股票池异常，仅返回{len(rows)}条")
    source = pd.DataFrame(rows)
    frame = pd.DataFrame({
        "price": pd.to_numeric(source["trade"], errors="coerce"),
        "change_pct": pd.to_numeric(source["changepercent"], errors="coerce"),
        "volume": pd.to_numeric(source["volume"], errors="coerce"),
        "amount": pd.to_numeric(source["amount"], errors="coerce"),
        "turnover_pct": pd.to_numeric(source["turnoverratio"], errors="coerce"),
        "pe": pd.to_numeric(source["per"], errors="coerce"),
        "volume_ratio": np.nan,
        "code": source["code"].astype(str).str.zfill(6),
        "name": source["name"].fillna("").astype(str),
        "high": pd.to_numeric(source["high"], errors="coerce"),
        "low": pd.to_numeric(source["low"], errors="coerce"),
        "open": pd.to_numeric(source["open"], errors="coerce"),
        "prev_close": pd.to_numeric(source["settlement"], errors="coerce"),
        "market_cap": pd.to_numeric(source["mktcap"], errors="coerce") * 1e4,
        "float_market_cap": pd.to_numeric(source["nmc"], errors="coerce") * 1e4,
        "pb": pd.to_numeric(source["pb"], errors="coerce"),
        "return_60d_snapshot": np.nan,
        "return_ytd": np.nan,
        "main_net": np.nan,
        "industry": "未分类",
    })
    frame.attrs["source"] = f"新浪财经A股成交额前{len(frame)}只（东财不可用时降级）"
    return frame


def fetch_snapshot_with_fallback() -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    try:
        return fetch_market_snapshot(), warnings
    except Exception as exc:
        warnings.append(f"东财全市场快照不可用，已降级为新浪流动性股票池：{exc}")
        return fetch_sina_liquid_snapshot(), warnings


def pct_rank(series: pd.Series, higher_is_better: bool = True, neutral_missing: bool = True) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    rank = values.rank(pct=True, ascending=higher_is_better) * 100
    if not higher_is_better:
        rank = values.rank(pct=True, ascending=False) * 100
    return rank.fillna(50.0 if neutral_missing else 0.0).clip(0, 100)


def preliminary_filter(snapshot: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    names = snapshot["name"]
    valid_name = ~names.str.contains(r"ST|退|退市", case=False, regex=True, na=False)
    frame = snapshot[
        valid_name
        & (snapshot["price"] >= float(config["min_price"]))
        & (snapshot["amount"] >= float(config["min_daily_amount_yi"]) * 1e8)
        & (snapshot["market_cap"] > 0)
        & snapshot["code"].str.match(r"^(00|30|60|68)")
    ].copy()
    if frame.empty:
        raise RuntimeError("基础过滤后没有可用股票")
    liquidity = pct_rank(np.log1p(frame["amount"]))
    momentum = pct_rank(frame["return_60d_snapshot"].clip(-50, 120))
    reasonable_turnover = (100 - (frame["turnover_pct"] - 3.0).abs() * 10).clip(0, 100)
    positive_pe = frame["pe"].where(frame["pe"] > 0)
    valuation = pct_rank(positive_pe, higher_is_better=False)
    not_extreme = (100 - frame["change_pct"].abs() * 5).clip(0, 100)
    frame["pre_score"] = (
        0.35 * liquidity + 0.30 * momentum + 0.15 * reasonable_turnover
        + 0.10 * valuation + 0.10 * not_extreme
    )
    return frame.sort_values("pre_score", ascending=False).head(int(config["preselect_count"]))


def fetch_kline(code: str, bars: int, timeout: int = 15) -> pd.DataFrame:
    ticker = f"{market_prefix(code)}{code}"
    params = {"param": f"{ticker},day,,,{bars},qfq"}
    payload = None
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = requests.get(
                TENCENT_KLINE_URLS[attempt % len(TENCENT_KLINE_URLS)], params=params,
                headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"}, timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            break
        except Exception as exc:
            last_error = exc
            time.sleep(0.8 * (attempt + 1))
    source_name = "腾讯前复权K线"
    if payload is not None:
        node = (payload.get("data") or {}).get(ticker) or {}
        rows = node.get("qfqday") or node.get("day") or []
    else:
        response = requests.get(
            SINA_KLINE_URL,
            params={"symbol": ticker, "scale": 240, "ma": "no", "datalen": bars},
            headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"}, timeout=timeout,
        )
        response.raise_for_status()
        response.encoding = "utf-8"
        rows = ((response.json().get("result") or {}).get("data") or [])
        source_name = f"新浪不复权备用K线（腾讯失败：{last_error}）"
    parsed = []
    for row in rows:
        try:
            if isinstance(row, dict):
                date, open_, close, high, low, volume = (
                    row.get("day"), row.get("open"), row.get("close"),
                    row.get("high"), row.get("low"), row.get("volume"),
                )
            else:
                if len(row) < 6:
                    continue
                date, open_, close, high, low, volume = row[:6]
            parsed.append({
                "date": str(date)[:10], "open": float(open_), "close": float(close),
                "high": float(high), "low": float(low), "volume": float(volume),
            })
        except (TypeError, ValueError):
            continue
    frame = pd.DataFrame(parsed)
    if not frame.empty:
        frame = frame.drop_duplicates("date").sort_values("date").reset_index(drop=True)
        frame.attrs["source"] = source_name
    return frame


def fetch_many_klines(codes: list[str], bars: int, workers: int) -> tuple[dict[str, pd.DataFrame], list[str]]:
    output: dict[str, pd.DataFrame] = {}
    errors: list[str] = []
    unique = list(dict.fromkeys(codes))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_kline, code, bars): code for code in unique}
        for future in as_completed(futures):
            code = futures[future]
            try:
                output[code] = future.result()
            except Exception as exc:
                errors.append(f"{code}: {exc}")
    return output, errors


def max_drawdown(closes: pd.Series) -> float:
    if closes.empty:
        return np.nan
    peak = closes.cummax()
    drawdown = closes / peak - 1
    return float(drawdown.min())


def rsi14(closes: pd.Series) -> float:
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
    loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    if pd.isna(gain) or pd.isna(loss):
        return np.nan
    if loss == 0:
        return 100.0
    return float(100 - 100 / (1 + gain / loss))


def compute_features(code: str, bars: pd.DataFrame, min_bars: int) -> dict[str, Any] | None:
    if bars is None or len(bars) < min_bars:
        return None
    b = bars.copy()
    close = b["close"]
    volume = b["volume"]
    ret = close.pct_change()
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    vol_ma5 = volume.rolling(5).mean().iloc[-1]
    vol_ma20 = volume.rolling(20).mean().iloc[-1]
    recent = b.tail(20).copy()
    recent["ret"] = recent["close"].pct_change()
    recent["vol_base"] = b["volume"].rolling(20).mean().tail(20).values
    recent["vol_expanded"] = recent["volume"] >= recent["vol_base"]
    healthy = ((recent["ret"] > 0) & recent["vol_expanded"]) | ((recent["ret"] < 0) & ~recent["vol_expanded"])
    prior_high20 = float(b["high"].iloc[-21:-1].max())
    prior_low20 = float(b["low"].iloc[-21:-1].min())
    current = float(close.iloc[-1])
    volume_ratio = float(vol_ma5 / vol_ma20) if vol_ma20 else np.nan
    return {
        "code": code,
        "data_date": str(b["date"].iloc[-1]),
        "kline_count": len(b),
        "close": current,
        "ret_20d": float(current / close.iloc[-21] - 1),
        "ret_60d": float(current / close.iloc[-61] - 1),
        "ret_120d": float(current / close.iloc[-121] - 1),
        "ma20": float(ma20), "ma60": float(ma60),
        "above_ma20": bool(current > ma20), "above_ma60": bool(current > ma60),
        "ma_bullish": bool(ma20 > ma60),
        "volatility_20d": float(ret.tail(20).std() * math.sqrt(252)),
        "max_drawdown_60d": max_drawdown(close.tail(60)),
        "volume_ratio_5_20": volume_ratio,
        "healthy_volume_price_ratio": float(healthy.dropna().mean()),
        "prior_high20": prior_high20, "prior_low20": prior_low20,
        "breakout_distance": float(current / prior_high20 - 1) if prior_high20 else np.nan,
        "rsi14": rsi14(close),
        "support_ma20": float(ma20), "support_ma60": float(ma60),
    }


def score_features(features: pd.DataFrame, snapshot: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    frame = features.merge(snapshot, on="code", how="left", suffixes=("", "_snap"))
    frame = frame[frame["kline_count"] >= int(config["min_kline_bars"])].copy()
    if len(frame) < int(config["candidate_count"]):
        raise RuntimeError(f"有效K线股票不足，仅{len(frame)}只")

    momentum = (
        0.35 * pct_rank(frame["ret_20d"]) + 0.40 * pct_rank(frame["ret_60d"])
        + 0.25 * pct_rank(frame["ret_120d"])
    )
    frame["momentum_20_pct"] = pct_rank(frame["ret_20d"])
    frame["momentum_60_pct"] = pct_rank(frame["ret_60d"])
    frame["momentum_120_pct"] = pct_rank(frame["ret_120d"])
    trend_flags = (
        frame["above_ma20"].astype(float) * 25 + frame["above_ma60"].astype(float) * 25
        + frame["ma_bullish"].astype(float) * 25
    )
    trend = (trend_flags + 0.25 * pct_rank(frame["max_drawdown_60d"])).clip(0, 100)
    frame["trend_drawdown_pct"] = pct_rank(frame["max_drawdown_60d"])

    breakout_quality = (
        100 - (frame["breakout_distance"].abs() * 600).clip(0, 100)
    )
    volume_balance = (100 - (frame["volume_ratio_5_20"] - 1.15).abs() * 80).clip(0, 100)
    volume_price = (
        frame["healthy_volume_price_ratio"].fillna(0.5) * 50
        + 0.25 * breakout_quality + 0.25 * volume_balance
    ).clip(0, 100)
    frame["volume_price_health_pct"] = frame["healthy_volume_price_ratio"].fillna(0.5) * 100
    frame["breakout_quality_pct"] = breakout_quality
    frame["volume_balance_pct"] = volume_balance

    pe = frame["pe"].where((frame["pe"] > 0) & (frame["pe"] < 300))
    pb = frame["pb"].where((frame["pb"] > 0) & (frame["pb"] < 50))
    valuation = (0.65 * pct_rank(pe, higher_is_better=False) + 0.35 * pct_rank(pb, higher_is_better=False)).clip(0, 100)
    frame["pe_pct"] = pct_rank(pe, higher_is_better=False)
    frame["pb_pct"] = pct_rank(pb, higher_is_better=False)

    volatility_score = pct_rank(frame["volatility_20d"], higher_is_better=False)
    drawdown_score = pct_rank(frame["max_drawdown_60d"])
    rsi_penalty = (100 - (frame["rsi14"] - 55).abs() * 2).clip(0, 100)
    liquidity_score = pct_rank(np.log1p(frame["amount"]))
    risk = (0.35 * volatility_score + 0.30 * drawdown_score + 0.20 * rsi_penalty + 0.15 * liquidity_score).clip(0, 100)
    frame["risk_volatility_pct"] = volatility_score
    frame["risk_drawdown_pct"] = drawdown_score
    frame["risk_rsi_pct"] = rsi_penalty
    frame["risk_liquidity_pct"] = liquidity_score

    frame["momentum_score"] = momentum
    frame["trend_score"] = trend
    frame["volume_price_score"] = volume_price
    frame["valuation_score"] = valuation
    frame["risk_score"] = risk
    w = config["weights"]
    frame["total_score"] = (
        float(w["momentum"]) * momentum + float(w["trend"]) * trend
        + float(w["volume_price"]) * volume_price + float(w["valuation"]) * valuation
        + float(w["risk"]) * risk
    )
    frame["near_limit"] = frame["change_pct"].abs() >= 9.5
    frame = frame[~frame["near_limit"]].sort_values("total_score", ascending=False)
    return frame


def diversify(frame: pd.DataFrame, count: int, max_per_industry: int) -> pd.DataFrame:
    selected = []
    industry_counts: dict[str, int] = {}
    for idx, row in frame.iterrows():
        industry = str(row.get("industry") or "未分类")
        # A fallback source may not provide industry. Treating every unknown row as
        # one industry would silently shrink a 10-stock target to 2-3 names.
        cap = max_per_industry if industry != "未分类" else count
        if industry_counts.get(industry, 0) >= cap:
            continue
        selected.append(idx)
        industry_counts[industry] = industry_counts.get(industry, 0) + 1
        if len(selected) >= count:
            break
    output = frame.loc[selected].copy()
    output.insert(0, "rank", range(1, len(output) + 1))
    return output


def load_signals(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=SIGNAL_COLUMNS)
    text_columns = ["signal_date", "strategy_version", "code", "name", "industry", "entry_date"]
    frame = pd.read_csv(path, dtype={column: "string" for column in text_columns})
    for col in SIGNAL_COLUMNS:
        if col not in frame.columns:
            frame[col] = np.nan
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    for column in text_columns:
        if column != "code":
            frame[column] = frame[column].astype("string")
    return frame[SIGNAL_COLUMNS]


def net_return(gross: float, config: dict[str, Any]) -> float:
    cost = 2 * float(config["single_side_fee_rate"]) + float(config["round_trip_slippage_rate"])
    return gross - cost


def update_signal_returns(
    signals: pd.DataFrame, klines: dict[str, pd.DataFrame], benchmark: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    if signals.empty:
        return signals
    result = signals.copy()
    benchmark = benchmark.sort_values("date") if not benchmark.empty else benchmark
    for idx, row in result.iterrows():
        code = str(row["code"]).zfill(6)
        bars = klines.get(code)
        if bars is None or bars.empty:
            continue
        after = bars[bars["date"] > str(row["signal_date"])].sort_values("date")
        if after.empty:
            continue
        entry_open = float(after.iloc[0]["open"])
        result.at[idx, "entry_date"] = str(after.iloc[0]["date"])
        result.at[idx, "entry_open"] = entry_open
        bench_after = benchmark[benchmark["date"] > str(row["signal_date"])].sort_values("date") if not benchmark.empty else pd.DataFrame()
        bench_entry = float(bench_after.iloc[0]["open"]) if not bench_after.empty else np.nan
        for horizon in (1, 5, 20):
            if len(after) >= horizon and pd.isna(row.get(f"ret_{horizon}d")):
                gross = float(after.iloc[horizon - 1]["close"] / entry_open - 1)
                value = net_return(gross, config)
                result.at[idx, f"ret_{horizon}d"] = value
                if len(bench_after) >= horizon and bench_entry > 0:
                    bret = float(bench_after.iloc[horizon - 1]["close"] / bench_entry - 1)
                    result.at[idx, f"benchmark_ret_{horizon}d"] = bret
                    result.at[idx, f"excess_{horizon}d"] = value - bret
    return result


def append_current_signals(signals: pd.DataFrame, picks: pd.DataFrame, signal_date: str, config: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for _, p in picks.iterrows():
        rows.append({
            "signal_date": signal_date, "strategy_version": config["strategy_version"],
            "code": p["code"], "name": p["name"], "industry": p["industry"], "rank": int(p["rank"]),
            "total_score": p["total_score"], "momentum_score": p["momentum_score"],
            "trend_score": p["trend_score"], "volume_price_score": p["volume_price_score"],
            "valuation_score": p["valuation_score"], "risk_score": p["risk_score"],
            "signal_close": p["close"],
        })
    addition = pd.DataFrame(rows)
    for col in SIGNAL_COLUMNS:
        if col not in addition.columns:
            addition[col] = np.nan
    return pd.concat([signals, addition[SIGNAL_COLUMNS]], ignore_index=True)


def save_equity_curve(signals: pd.DataFrame, path: Path) -> tuple[pd.DataFrame, dict[str, float]]:
    matured = signals.dropna(subset=["ret_1d"]).copy()
    if matured.empty:
        empty = pd.DataFrame(columns=["signal_date", "portfolio_ret_1d", "benchmark_ret_1d", "equity", "benchmark_equity"])
        empty.to_csv(path, index=False, encoding="utf-8-sig")
        return empty, {}
    daily = matured.groupby("signal_date", as_index=False).agg(
        portfolio_ret_1d=("ret_1d", "mean"), benchmark_ret_1d=("benchmark_ret_1d", "mean")
    ).sort_values("signal_date")
    daily["equity"] = (1 + daily["portfolio_ret_1d"]).cumprod()
    daily["benchmark_equity"] = (1 + daily["benchmark_ret_1d"].fillna(0)).cumprod()
    daily["drawdown"] = daily["equity"] / daily["equity"].cummax() - 1
    daily.to_csv(path, index=False, encoding="utf-8-sig")
    stats = {
        "days": float(len(daily)), "cumulative": float(daily["equity"].iloc[-1] - 1),
        "benchmark_cumulative": float(daily["benchmark_equity"].iloc[-1] - 1),
        "cumulative_excess": float(daily["equity"].iloc[-1] - daily["benchmark_equity"].iloc[-1]),
        "max_drawdown": float(daily["drawdown"].min()),
        "win_rate": float((daily["portfolio_ret_1d"] > 0).mean()),
    }
    return daily, stats


def performance_summary(signals: pd.DataFrame) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for horizon in (1, 5, 20):
        col = f"ret_{horizon}d"
        mature = signals.dropna(subset=[col])
        values = pd.to_numeric(mature[col], errors="coerce").dropna()
        excess_values = pd.to_numeric(mature[f"excess_{horizon}d"], errors="coerce").dropna()
        winners = values[values > 0]; losers = values[values < 0]
        downside = values[values < 0]
        benchmark_values = pd.to_numeric(mature[f"benchmark_ret_{horizon}d"], errors="coerce").dropna()
        summary[str(horizon)] = {
            "n": int(len(values)), "mean": float(values.mean()) if len(values) else np.nan,
            "median": float(values.median()) if len(values) else np.nan,
            "win_rate": float((values > 0).mean()) if len(values) else np.nan,
            "std": float(values.std(ddof=1)) if len(values) > 1 else np.nan,
            "min": float(values.min()) if len(values) else np.nan, "max": float(values.max()) if len(values) else np.nan,
            "q25": float(values.quantile(.25)) if len(values) else np.nan, "q75": float(values.quantile(.75)) if len(values) else np.nan,
            "excess_mean": float(excess_values.mean()) if len(excess_values) else np.nan,
            "excess_median": float(excess_values.median()) if len(excess_values) else np.nan,
            "benchmark_mean": float(benchmark_values.mean()) if len(benchmark_values) else np.nan,
            "avg_win": float(winners.mean()) if len(winners) else np.nan,
            "avg_loss": float(losers.mean()) if len(losers) else np.nan,
            "p10": float(values.quantile(.10)) if len(values) else np.nan,
            "p90": float(values.quantile(.90)) if len(values) else np.nan,
            "downside_std": float(downside.std(ddof=1)) if len(downside) > 1 else np.nan,
            "profit_factor": float(winners.sum() / abs(losers.sum())) if len(losers) and losers.sum() != 0 else np.nan,
        }
    # 展开历史股票池：同一只股票跨多日入选时，每条信号作为一条观测，
    # 同时保留信号次数，避免把历史池压缩成只有当前10只股票。
    stock_rows: list[dict[str, Any]] = []
    if not signals.empty:
        for (code, name), group in signals.groupby(
            ["code", "name"], dropna=False, sort=False
        ):
            industry_values = group["industry"].dropna().astype(str)
            industry_values = industry_values[~industry_values.isin(["", "nan", "未分类"])]
            industry = industry_values.iloc[-1] if len(industry_values) else "未分类"
            row: dict[str, Any] = {
                "code": str(code).zfill(6), "name": str(name), "industry": str(industry or "未分类"),
                "signals": int(len(group)),
                "first_signal": str(group["signal_date"].min()),
                "last_signal": str(group["signal_date"].max()),
            }
            for horizon in (1, 5, 20):
                values = pd.to_numeric(group[f"ret_{horizon}d"], errors="coerce").dropna()
                benchmark_values = pd.to_numeric(group[f"benchmark_ret_{horizon}d"], errors="coerce").dropna()
                excess_values = pd.to_numeric(group[f"excess_{horizon}d"], errors="coerce").dropna()
                row[f"n_{horizon}d"] = int(len(values))
                row[f"mean_{horizon}d"] = float(values.mean()) if len(values) else np.nan
                row[f"median_{horizon}d"] = float(values.median()) if len(values) else np.nan
                row[f"win_{horizon}d"] = float((values > 0).mean()) if len(values) else np.nan
                row[f"benchmark_{horizon}d"] = float(benchmark_values.mean()) if len(benchmark_values) else np.nan
                row[f"excess_{horizon}d"] = float(excess_values.mean()) if len(excess_values) else np.nan
            stock_rows.append(row)
    summary["stock_pool"] = sorted(stock_rows, key=lambda x: (-x["signals"], x["code"]))
    # 每个信号日的等权组合表现，用于复盘此前股票池的阶段性变化。
    cohort_rows: list[dict[str, Any]] = []
    if not signals.empty:
        for signal_date, group in signals.groupby("signal_date", sort=True):
            row = {"signal_date": str(signal_date), "stocks": int(len(group))}
            for horizon in (1, 5, 20):
                values = pd.to_numeric(group[f"ret_{horizon}d"], errors="coerce").dropna()
                benchmark_values = pd.to_numeric(group[f"benchmark_ret_{horizon}d"], errors="coerce").dropna()
                excess_values = pd.to_numeric(group[f"excess_{horizon}d"], errors="coerce").dropna()
                row[f"n_{horizon}d"] = int(len(values))
                row[f"mean_{horizon}d"] = float(values.mean()) if len(values) else np.nan
                row[f"benchmark_{horizon}d"] = float(benchmark_values.mean()) if len(benchmark_values) else np.nan
                row[f"excess_{horizon}d"] = float(excess_values.mean()) if len(excess_values) else np.nan
                row[f"win_{horizon}d"] = float((values > 0).mean()) if len(values) else np.nan
            cohort_rows.append(row)
    summary["signal_cohorts"] = cohort_rows
    return summary


def attach_return_tracking(picks: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    """Attach per-stock today's mark-to-market and latest historical signal returns."""
    frame = picks.copy()
    frame["today_return"] = pd.to_numeric(frame.get("change_pct", np.nan), errors="coerce") / 100.0
    frame["today_pnl_10k"] = frame["today_return"] * 10000
    frame["hist_signal_date"] = pd.Series(pd.NA, index=frame.index, dtype="string")
    frame["hist_entry_date"] = pd.Series(pd.NA, index=frame.index, dtype="string")
    for col in ("hist_entry_open", "hist_ret_1d", "hist_ret_5d", "hist_ret_20d", "hist_excess_1d", "hist_excess_5d", "hist_excess_20d"):
        frame[col] = np.nan
    if signals.empty:
        return frame
    history = signals.copy()
    history["signal_date"] = history["signal_date"].astype(str)
    history = history.sort_values("signal_date").drop_duplicates("code", keep="last")
    for idx, row in frame.iterrows():
        match = history[history["code"].astype(str).str.zfill(6) == str(row["code"]).zfill(6)]
        if match.empty:
            continue
        source = match.iloc[-1]
        frame.at[idx, "hist_signal_date"] = source.get("signal_date")
        frame.at[idx, "hist_entry_date"] = source.get("entry_date")
        frame.at[idx, "hist_entry_open"] = source.get("entry_open")
        for horizon in (1, 5, 20):
            frame.at[idx, f"hist_ret_{horizon}d"] = source.get(f"ret_{horizon}d")
            frame.at[idx, f"hist_excess_{horizon}d"] = source.get(f"excess_{horizon}d")
    return frame


def iteration_diagnostics(signals: pd.DataFrame) -> tuple[str, dict[str, float]]:
    mature = signals.dropna(subset=["ret_5d"]).copy()
    signal_days = mature["signal_date"].nunique()
    factor_cols = ["momentum_score", "trend_score", "volume_price_score", "valuation_score", "risk_score"]
    correlations: dict[str, float] = {}
    for col in factor_cols:
        if len(mature) >= 20 and mature[col].nunique() > 2:
            # Spearman is Pearson correlation of ranks. Computing it directly
            # avoids an optional SciPy dependency in the isolated daily runtime.
            ranked_factor = pd.to_numeric(mature[col], errors="coerce").rank(method="average")
            ranked_return = pd.to_numeric(mature["ret_5d"], errors="coerce").rank(method="average")
            correlations[col] = float(ranked_factor.corr(ranked_return, method="pearson"))
    if signal_days < 20 or len(mature) < 200:
        return f"继续积累样本：当前成熟5日信号日{signal_days}个、观测{len(mature)}条；门槛为20个信号日且200条观测。", correlations
    positive = sorted(correlations.items(), key=lambda x: x[1], reverse=True)
    if not positive:
        return "样本达到数量门槛，但因子数据不足，暂不建议调整。", correlations
    best, worst = positive[0], positive[-1]
    return (
        f"进入人工复核阶段：{best[0]}与5日收益相关性较高({best[1]:.2f})，"
        f"{worst[0]}较低({worst[1]:.2f})。仅建议做不超过5个百分点的候选权重实验，"
        "必须先进行时间顺序样本外验证，脚本不会自动改权重。",
        correlations,
    )


def fmt_pct(value: Any, digits: int = 2) -> str:
    try:
        if pd.isna(value):
            return "数据不足"
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "数据不足"


def fmt_money(value: Any) -> str:
    try:
        if pd.isna(value):
            return "数据不足"
        number = float(value)
        if abs(number) >= 1e8:
            return f"{number / 1e8:,.2f}亿元"
        if abs(number) >= 1e4:
            return f"{number / 1e4:,.2f}万元"
        return f"{number:,.0f}元"
    except (TypeError, ValueError):
        return "数据不足"


def fmt_multiple(value: Any, suffix: str = "倍") -> str:
    try:
        if pd.isna(value) or float(value) <= 0:
            return "不适用/数据不足"
        return f"{float(value):.2f}{suffix}"
    except (TypeError, ValueError):
        return "不适用/数据不足"


def display_text(value: Any, fallback: str) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return fallback
    text = str(value).strip()
    return text if text and text.lower() != "nan" else fallback


def markdown_inline_to_html(value: str) -> str:
    """Small dependency-free Markdown inline renderer for the report subset."""
    escaped = html.escape(value, quote=False)
    escaped = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2" alt="\1" loading="lazy">', escaped)
    escaped = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" target="_blank" rel="noopener">\1</a>', escaped)
    escaped = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', escaped)
    escaped = re.sub(r'`([^`]+)`', r'<code>\1</code>', escaped)
    return escaped


def markdown_to_html(markdown_text: str, title: str, context=None) -> str:
    from report_ui import render
    return render(markdown_text, title, _markdown_document(markdown_text, title), context)


def _markdown_document(markdown_text: str, title: str) -> str:
    """Convert generated report Markdown to a self-contained, readable HTML page."""
    source = markdown_text.replace("\r\n", "\n").split("\n")
    body: list[str] = []
    toc: list[tuple[int, str, str]] = []
    heading_ids: dict[str, int] = {}
    i = 0
    while i < len(source):
        line = source[i]
        if not line.strip():
            i += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            heading_text = re.sub(r"[*`]", "", heading.group(2)).strip()
            base_id = f"section-{len(toc) + 1}" if level >= 2 else "report-title"
            heading_ids[base_id] = heading_ids.get(base_id, 0) + 1
            heading_id = base_id if heading_ids[base_id] == 1 else f"{base_id}-{heading_ids[base_id]}"
            body.append(f"<h{level} id=\"{heading_id}\">{markdown_inline_to_html(heading.group(2))}</h{level}>")
            if level in (2, 3):
                toc.append((level, heading_text, heading_id))
            i += 1; continue
        if line.startswith("> "):
            body.append(f"<blockquote>{markdown_inline_to_html(line[2:])}</blockquote>")
            i += 1; continue
        if re.match(r"^\s*[-*_]{3,}\s*$", line):
            body.append("<hr>"); i += 1; continue
        if line.startswith("|") and i + 1 < len(source) and source[i + 1].startswith("|"):
            rows: list[list[str]] = []
            while i < len(source) and source[i].startswith("|"):
                cells = [cell.strip() for cell in source[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                    rows.append(cells)
                i += 1
            if rows:
                wrap_class = "table-wrap long-table" if len(rows) > 16 else "table-wrap"
                body.append(f'<div class="{wrap_class}"><table><thead><tr>' + "".join(f"<th>{markdown_inline_to_html(c)}</th>" for c in rows[0]) + "</tr></thead><tbody>")
                for row in rows[1:]:
                    body.append("<tr>" + "".join(f"<td>{markdown_inline_to_html(c)}</td>" for c in row) + "</tr>")
                body.append("</tbody></table></div>")
            continue
        if re.match(r"^\s*-\s+", line):
            body.append("<ul>")
            while i < len(source) and re.match(r"^\s*-\s+", source[i]):
                item = re.sub(r"^\s*-\s+", "", source[i]); body.append(f"<li>{markdown_inline_to_html(item)}</li>"); i += 1
            body.append("</ul>"); continue
        paragraph = [line]
        i += 1
        while i < len(source) and source[i].strip() and not re.match(r"^(#{1,6})\s|^> |^\||^\s*-\s+|^\s*[-*_]{3,}\s*$", source[i]):
            paragraph.append(source[i]); i += 1
        body.append(f"<p>{'<br>'.join(markdown_inline_to_html(item) for item in paragraph)}</p>")
    return '<main>' + ''.join(body) + '</main>'


def open_html_in_browser(path: Path) -> tuple[bool, str]:
    """Open a completed HTML report in the Windows user's default browser."""
    try:
        resolved = path.resolve(strict=True)
        opened = bool(webbrowser.open_new_tab(resolved.as_uri()))
        return opened, "已请求默认浏览器打开报告" if opened else "默认浏览器未确认打开请求"
    except Exception as exc:
        return False, f"打开浏览器失败：{exc}"


def industry_watchpoints(industry: str, business: str) -> tuple[str, str]:
    text = f"{industry} {business}"
    mapping = [
        (("银行",), "净息差、不良贷款率、拨备覆盖率、存款成本和信贷需求", "资产质量恶化、息差持续收窄及信用成本上升"),
        (("保险",), "新业务价值、保费结构、投资收益率、偿付能力和长端利率", "权益与利率波动、赔付率上升及产品结构变化"),
        (("证券", "券商"), "市场成交额、两融余额、投行业务节奏和自营投资表现", "市场活跃度下降、自营波动及监管政策变化"),
        (("航运", "港口"), "运价指数、有效运力、燃油成本、港口效率和全球贸易需求", "运价回落、运力过剩、燃油上涨和贸易波动"),
        (("白酒", "食品饮料", "乳制品"), "终端动销、产品均价、渠道库存、原料成本和产品结构", "消费需求偏弱、渠道库存上升及价格竞争"),
        (("医药", "医疗", "生物"), "订单或产品放量、研发管线、商业化效率、集采与合规政策", "研发失败、订单波动、政策压价及合规风险"),
        (("化工", "化纤", "材料"), "产品价差、原料成本、产能利用率、库存周期和新增产能", "价差收窄、原料上涨、需求走弱及产能过剩"),
        (("养殖", "农牧", "农业", "猪"), "产品价格、单位养殖成本、产能变化、疫病和饲料价格", "价格下行、成本反弹、疫病及产能去化不及预期"),
        (("地产", "房地产"), "销售回款、拿地强度、融资成本、项目交付和库存去化", "销售下滑、现金流紧张、减值和政策变化"),
        (("汽车", "零部件"), "销量与订单、单车价值、客户结构、产能利用率和价格竞争", "价格战、客户集中、销量不及预期和产能闲置"),
        (("电子", "半导体", "通信"), "下游需求、库存周期、产品迭代、国产替代和研发投入", "技术迭代、库存调整、客户集中和资本开支波动"),
    ]
    for keywords, drivers, risks in mapping:
        if any(keyword in text for keyword in keywords):
            return drivers, risks
    return "收入增长、毛利率、经营现金流、资本开支、客户集中度和行业景气度", "需求下滑、竞争加剧、成本上升、现金流转弱和政策变化"


def industry_business_context(industry: str, business: str) -> tuple[str, str, str]:
    text = f"{industry} {business}"
    mapping = [
        (("航运", "港口"), "通过承运集装箱、提供航线网络和码头装卸服务获得运费及服务收入；航线覆盖、船队效率和码头网络构成竞争壁垒", "收入主要受运价和货量驱动，利润还取决于燃油、租船、港口及人工成本；运价变化通常会放大利润弹性", "典型强周期行业，全球贸易需求与船舶供给错配会造成运价和盈利大幅波动"),
        (("银行",), "通过吸收存款并投放贷款赚取利差，同时获得财富管理、银行卡、托管和投行等手续费收入", "净息差、贷款规模、资产质量、信用成本和非息收入共同决定利润；低PB需要结合ROE及不良贷款判断", "盈利受利率周期、信用周期和监管资本约束影响，资产负债率不适用普通企业标准"),
        (("保险",), "收取保费并承担未来赔付责任，同时配置债券、股票等资产获取投资收益", "新业务价值、赔付率、费用率和投资收益率决定利润质量，利润可能因资本市场波动而起伏", "兼具保障需求的长期成长属性与利率、权益市场周期属性"),
        (("化工", "化纤", "材料"), "采购能源或化工原料，经装置加工后销售中间体、聚合物或终端材料，规模和一体化决定成本优势", "核心是产品售价减原料成本形成的价差，产能利用率、库存、检修和新增供给会改变单位盈利", "周期性较强，产品价差可能比收入增速更能解释利润变化"),
        (("养殖", "农牧", "农业", "猪"), "通过种畜繁育、饲养和销售获得收入，成本控制与生物安全决定长期竞争力", "产品价格、完全成本、出栏量、饲料价格和疫病共同决定利润", "供给调整存在时间滞后，价格和盈利呈明显周期波动"),
        (("家用电器", "白色家电", "电器"), "通过研发、制造和销售家电获得收入，并依靠品牌、渠道、供应链及售后服务形成壁垒", "销量、均价、产品结构、原材料、渠道库存和海外业务决定收入与毛利率", "需求相对成熟，地产周期、消费更新和出口汇率会影响阶段表现"),
    ]
    for keys, model, profit, cycle in mapping:
        if any(key in text for key in keys): return model, profit, cycle
    return "公司通过主营产品或服务取得收入，竞争力通常来自产品、成本、渠道、客户或牌照等要素", "收入规模、毛利率、费用率、现金回收和资本开支共同决定利润与自由现金流", "需结合所在行业的需求、供给、政策和技术变化判断周期位置"


def industry_breadth_text(row: dict[str, Any]) -> str:
    if pd.notna(row.get("up_count", np.nan)) and pd.notna(row.get("down_count", np.nan)):
        return f"涨{int(row['up_count'])}家、跌{int(row['down_count'])}家"
    count = row.get("constituent_count", np.nan)
    amount = row.get("amount", np.nan)
    details = []
    if pd.notna(count): details.append(f"成分约{int(count)}家")
    if pd.notna(amount): details.append(f"成交额{fmt_money(amount)}")
    return "、".join(details) if details else "涨跌家数暂缺"


def fundamental_assessment(row: pd.Series) -> list[str]:
    comments: list[str] = []
    revenue_yoy = row.get("revenue_yoy", np.nan)
    profit_yoy = row.get("net_profit_yoy", np.nan)
    if pd.notna(revenue_yoy):
        pace = "较快增长" if revenue_yoy >= 0.15 else "温和增长" if revenue_yoy >= 0 else "同比下降"
        comments.append(f"收入同比{fmt_pct(revenue_yoy)}，属于{pace}")
    if pd.notna(profit_yoy):
        pace = "快于收入" if pd.notna(revenue_yoy) and profit_yoy > revenue_yoy + 0.03 else "慢于收入" if pd.notna(revenue_yoy) and profit_yoy < revenue_yoy - 0.03 else "与收入大致同步"
        comments.append(f"归母净利润同比{fmt_pct(profit_yoy)}，增速{pace}")
    if any(pd.notna(value) and abs(float(value)) >= 2 for value in (revenue_yoy, profit_yoy)):
        comments.append("同比变动超过200%，很可能受低基数、并表、重组或一次性项目影响，必须查阅财报附注，不能线性外推")
    if pd.notna(row.get("net_margin", np.nan)):
        comments.append(f"净利率约{fmt_pct(row['net_margin'])}，用于观察最终盈利厚度，但跨行业不可直接比较")
    if pd.notna(row.get("cash_profit_ratio", np.nan)):
        ratio = float(row["cash_profit_ratio"])
        quality = "现金回收相对充足" if ratio >= 1 else "现金流低于账面利润，需结合季节性和营运资金继续观察"
        comments.append(f"经营现金流/净利润约{ratio:.2f}倍，{quality}")
    industry = f"{row.get('sw_industry', '')} {row.get('industry', '')}"
    if any(word in industry for word in ("银行", "保险", "证券", "多元金融")):
        comments.append("金融企业的资产负债结构具有行业特殊性，本报告不使用普通企业的负债率标准评价")
    elif pd.notna(row.get("debt_ratio", np.nan)):
        ratio = float(row["debt_ratio"])
        level = "偏高，需关注偿债与现金流" if ratio >= 0.70 else "中等" if ratio >= 0.45 else "相对较低"
        comments.append(f"资产负债率约{fmt_pct(ratio)}，在未做同行比较前仅可粗略视为{level}")
    return comments or ["公开财务字段暂不完整，本次不做无数据推断"]


def plot_candidate_chart(code: str, name: str, bars: pd.DataFrame, output: Path) -> str:
    """Save a dependency-free SVG candlestick/volume chart."""
    source = bars.copy().sort_values("date").reset_index(drop=True)
    source["ma20"] = source["close"].rolling(20, min_periods=20).mean()
    source["ma60"] = source["close"].rolling(60, min_periods=60).mean()
    frame = source.tail(120).copy().reset_index(drop=True)
    if frame.empty:
        return ""
    close = frame["close"]
    width, height, left, top, chart_w, chart_h = 1000, 560, 70, 50, 880, 360
    lo, hi = float(frame["low"].min()), float(frame["high"].max())
    vmax = float(frame["volume"].max()) or 1.0
    def yprice(value): return top + (hi - float(value)) / max(hi - lo, 1e-9) * chart_h
    def xbar(i): return left + (i / max(len(frame) - 1, 1)) * chart_w
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">', '<rect width="100%" height="100%" fill="white"/>', f'<text x="{left}" y="25" font-size="18" fill="#222">{html.escape(name)}（{code}）K线与成交量｜截至{frame["date"].iloc[-1]}</text>']
    parts.append(f'<line x1="{left}" y1="{top+chart_h}" x2="{left+chart_w}" y2="{top+chart_h}" stroke="#999"/>')
    for i, row in frame.iterrows():
        x = xbar(i); up = row["close"] >= row["open"]; color = "#d62728" if up else "#2ca02c"
        parts.append(f'<line x1="{x:.1f}" y1="{yprice(row["high"]):.1f}" x2="{x:.1f}" y2="{yprice(row["low"]):.1f}" stroke="{color}" stroke-width="1"/>')
        y1, y2 = yprice(max(row["open"], row["close"])), yprice(min(row["open"], row["close"]))
        parts.append(f'<rect x="{x-2.5:.1f}" y="{y1:.1f}" width="5" height="{max(y2-y1,1):.1f}" fill="{color}"/>')
        vy = top + chart_h + 55 + (1 - float(row["volume"]) / vmax) * 90
        parts.append(f'<rect x="{x-2.5:.1f}" y="{vy:.1f}" width="5" height="{top+chart_h+145-vy:.1f}" fill="{color}" opacity=".7"/>')
    for key, color, label in (("ma20", "#1f77b4", "MA20"), ("ma60", "#ff7f0e", "MA60")):
        pts = [(xbar(i), yprice(v)) for i, v in enumerate(frame[key]) if pd.notna(v)]
        if pts:
            parts.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x,y in pts)}" fill="none" stroke="{color}" stroke-width="2"/>')
            parts.append(f'<text x="{left+chart_w-100 if key=="ma20" else left+chart_w-45}" y="{top+20 if key=="ma20" else top+40}" font-size="12" fill="{color}">{label}</text>')
    parts.append(f'<text x="{left}" y="{top+chart_h+175}" font-size="12" fill="#555">成交量（下方柱体）　红=上涨　绿=下跌</text>')
    parts.append('</svg>')
    output.parent.mkdir(parents=True, exist_ok=True)
    output = output.with_suffix(".svg")
    output.write_text("".join(parts), encoding="utf-8")
    return str(output.name)


def candidate_explanation(row: pd.Series) -> tuple[str, str, str, str]:
    reasons = []
    if row["ret_60d"] > 0.10:
        reasons.append(f"近60日上涨{row['ret_60d']*100:.1f}%")
    if row["above_ma20"] and row["above_ma60"] and row["ma_bullish"]:
        reasons.append("价格在20/60日均线上方且短期均线领先")
    if row["healthy_volume_price_ratio"] >= 0.55:
        reasons.append(f"近20日约{row['healthy_volume_price_ratio']*100:.0f}%交易日量价配合较健康")
    if not reasons:
        reasons.append("多项因子综合排名靠前，但没有单一压倒性信号")

    risks = []
    if row["rsi14"] >= 70:
        risks.append(f"RSI={row['rsi14']:.0f}，短线偏热")
    if row["max_drawdown_60d"] < -0.15:
        risks.append(f"近60日最大回撤{row['max_drawdown_60d']*100:.1f}%")
    if row["volume_ratio_5_20"] > 2:
        risks.append("近期成交量突然明显放大，需防高位分歧")
    if not risks:
        risks.append("暂未出现突出风险，但技术信号仍可能失效")

    confirm = (
        f"若后续收盘站稳{row['prior_high20']:.2f}元附近，且5日均量/20日均量保持在1以上，"
        "趋势获得更强确认"
    )
    invalid = f"若收盘跌破MA60约{row['ma60']:.2f}元，或放量破坏MA20约{row['ma20']:.2f}元，应重新评估"
    return "；".join(reasons), "；".join(risks), confirm, invalid


def render_market_overview_lines(
    market: dict[str, Any], industry_dynamics: dict[str, Any], industry_error: str | None,
    market_context: dict[str, Any] | None = None,
) -> list[str]:
    indices = market.get("indices", [])
    index_summary = "；".join(f"{x['name']}{x['ret_1d']*100:+.2f}%" for x in indices)
    top_industries = industry_dynamics.get("top", [])[:3]
    bottom_industries = industry_dynamics.get("bottom", [])[:3]
    top_summary = "、".join(f"{x['name']} {x['change_pct']:+.2f}%" for x in top_industries) or "数据暂缺"
    bottom_summary = "、".join(f"{x['name']} {x['change_pct']:+.2f}%" for x in bottom_industries) or "数据暂缺"
    confirmed_industries = [x["industry"] for x in market.get("industry_clues", []) if x.get("items")]
    clue_summary = (
        "其中" + "、".join(confirmed_industries[:3]) + "得到同日强势股题材标签印证；这只是同步线索，不代表因果已证实"
        if confirmed_industries else "强势股标签未提供足够一致的行业归因，不根据涨幅反推原因"
    )
    # 将“今日结论”中的离散事实串成一段独立综述，方便读者快速理解当天市场发生了什么。
    # 这里只使用已取得的行情/行业/题材字段；缺失数据明确标注，不根据同步现象反推因果。
    valid_indices = [x for x in indices if x.get("ret_1d") is not None]
    best_index = max(valid_indices, key=lambda x: x["ret_1d"]) if valid_indices else None
    worst_index = min(valid_indices, key=lambda x: x["ret_1d"]) if valid_indices else None
    breadth = (
        f"覆盖的{market['coverage']}只高流动性股票中，上涨{market['up']}只、下跌{market['down']}只、"
        f"平盘{market['flat']}只，涨跌幅中位数为{market['median_change']:.2f}%"
    )
    index_part = (
        f"主要指数方面，{best_index['name']}当日表现最好（{best_index['ret_1d']*100:+.2f}%），"
        f"{worst_index['name']}最弱（{worst_index['ret_1d']*100:+.2f}%）"
        if best_index and worst_index else "主要指数数据不完整"
    )
    industry_part = (
        f"行业上，涨幅靠前的是{top_summary}，跌幅靠前的是{bottom_summary}"
        if top_industries or bottom_industries else "行业涨跌数据暂缺"
    )
    activity_part = (
        f"成交方面，样本成交额约{fmt_money(market['amount_sum'])}，换手率中位数{market['median_turnover']:.2f}%"
    )
    theme_part = (
        f"同花顺强势股标签覆盖{market['hot_reason_count']}只，"
        + ("高频标签包括" + "、".join(f"{name}（{count}次）" for name, count in market.get("themes", [])[:5])
           if market.get("themes") else "题材标签不足")
    )
    context = market_context or {}
    context_items = context.get("items", [])
    context_topics = context.get("topics", [])
    if context_items:
        headline_text = "；".join(
            f"[{str(item.get('title', '')).replace(']', '）')}]({item.get('url') or 'https://www.cls.cn/'})"
            for item in context_items[:4]
        )
        topic_text = "、".join(f"{name}（{count}次关键词命中）" for name, count in context_topics) or "未形成明显主题聚类"
        information_part = (
            f"信息面上，财联社与东方财富7×24快讯显示的高频主题包括{topic_text}；代表性条目为：{headline_text}。"
        )
    else:
        information_part = "信息面上，财联社与东方财富7×24快讯本次未取得可核验条目，不能据此补写市场驱动因素。"
    market_narrative = (
        f"从全天结构看，市场呈现“{market['regime']}”特征：{breadth}，{index_part}。"
        f"行情事实显示，{industry_part}；{activity_part}。{information_part}"
        f"这些新闻是当天市场关注的同步信息，不等于已经证明其导致指数或行业涨跌；当前可确认的是价格、成交和新闻同时出现，"
        "后续需要用行业扩散、成交持续性和后续公告/数据验证是否形成可持续主线。"
    )
    # Shared semantic rules keep publication time and impact scope explicit.
    from event_analysis import assess, timing, evidence

    def event_stance(event):
        return assess(event)

    def event_validation(event):
        data_date = next((str(x.get("date")) for x in indices if x.get("date")), "")
        tag, reason = timing(event, data_date)
        return f"**{tag}**：{reason}"

    events = context.get("events", [])
    for event in events:
        event["validation"] = event_validation(event)
    lines = [
        "## 一、当日股市全景", "",
        "### 今日市场综述", "",
        market_narrative, "",
        "### 重大事件与市场影响分析", "",
    ]
    if events:
        coverage_text = (
            f"本次从{context.get('relevant_headline_count', len(context_items))}条A股相关快讯中识别出"
            f"{context.get('event_candidate_count', len(events))}条具有明确传导路径的候选事件，"
            f"去重并控制同类事件数量后展示{len(events)}条。"
        )
        lines += [
            "每条先给出可执行的方向判断，再用当日行情检验是否获得市场确认；“市场确认”表示方向与价格同步，并不把单条快讯写成唯一因果。",
            coverage_text,
            "",
            "| 重要事件（原文） | 明确判断 | 当日市场反馈 | 证据边界 |",
            "|---|---|---|---|",
        ]
        for event in events:
            title = str(event.get("title") or "").replace("|", "｜")
            stance, rationale = event_stance(event)
            impact = f"**{stance}：{rationale}**。涉及：{event.get('sectors', '未明确')}。"
            source_link = f"[{title}]({event.get('url') or 'https://www.cls.cn/'})"
            lines.append(
                f"| {source_link}｜{event.get('time', '')}｜{event.get('source', '')} | {impact} | "
                f"{event.get('validation', '')} | {event.get('evidence', '')} |"
            )
        lines += [
            "",
            "结论口径：方向判断来自新闻标题和预设传导规则；只有方向与对应行业或市场风险偏好同向时才标记为“市场确认”。未确认即表示该事件没有成为当日主线，而不是没有潜在影响。",
            "",
        ]
    else:
        lines += [
            "今日快讯中未筛选出同时具备明确事件关键词和可解释传导路径的重大事件；不能用普通标题或市场涨跌倒推原因。建议结合盘后公告、政策原文和次日行业扩散继续核验。",
            "",
        ]
    lines += [
        "### 今日结论", "",
        f"- **指数与情绪**：{index_summary or '主要指数数据暂缺'}；{market['breadth_text']}。",
        f"- **市场风格**：{market['style_text']}。",
        f"- **市场状态**：{market['regime']}。{market['regime_explain']}（{market['market_beta_note']}。）",
        f"- **行业轮动**：领涨为{top_summary}；领跌为{bottom_summary}。{clue_summary}。",
        f"- **题材线索**：同花顺当日强势股归因覆盖{market['hot_reason_count']}只；"
        + ("高频标签为" + "、".join(f"{name}（{count}次）" for name, count in market.get("themes", [])) + "。" if market.get("themes") else "归因数据暂缺，不能可靠概括当日题材主线。"),
        "- **阅读方法**：指数反映整体方向，涨跌家数反映赚钱效应，行业排名反映轮动方向，异动表解释个股为何被市场集中交易；四者应合并看，不能只凭单一涨停或一条消息下结论。", "",
        "### 主要指数表现", "",
        "| 指数 | 收盘点位 | 当日 | 近5日 | 近20日 |", "|---|---:|---:|---:|---:|",
    ]
    for item in indices:
        lines.append(f"| {item['name']} | {item['close']:.2f} | {fmt_pct(item['ret_1d'])} | {fmt_pct(item['ret_5d'])} | {fmt_pct(item['ret_20d'])} |")
    if not indices:
        lines.append("| 数据暂缺 | — | — | — | — |")

    lines += [
        "", "### 涨跌结构与成交活跃度", "",
        f"本节统计范围为“{market['snapshot_source']}”，不是对未纳入该数据源股票的推断。",
        "", "| 覆盖股票 | 上涨 | 下跌 | 平盘 | 上涨占比 | 涨幅中位数 | ≥3% | ≤-3% | 接近涨停/跌停* | 样本成交额 | 换手率中位数 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| {market['coverage']} | {market['up']} | {market['down']} | {market['flat']} | {market['up_ratio']*100:.1f}% | {market['median_change']:.2f}% | {market['rise_3']} | {market['fall_3']} | {market['near_up']}/{market['near_down']} | {fmt_money(market['amount_sum'])} | {market['median_turnover']:.2f}% |",
        "", "\\*“接近涨跌停”统一按涨跌幅绝对值≥9.5%近似统计；不同板块涨跌停限制不同，因此这不是交易所正式涨跌停家数。", "",
        "**成交额集中标的**（成交额高说明市场关注度高，不等于资金净流入）：", "",
        "| 股票 | 涨跌 | 成交额 | 换手率 |", "|---|---:|---:|---:|",
    ]
    for item in market.get("top_amount", []):
        lines.append(f"| {item.get('name', '')}（{item.get('code', '')}） | {_number(item.get('change_pct')):.2f}% | {fmt_money(item.get('amount'))} | {_number(item.get('turnover_pct')):.2f}% |")

    lines += ["", "### 行业轮动", ""]
    if industry_dynamics.get("top"):
        lines.append("**领涨行业**：" + "；".join(f"{x['name']} {x['change_pct']:+.2f}%（{industry_breadth_text(x)}）" for x in industry_dynamics["top"]) + "。")
    else:
        lines.append("领涨行业数据暂未取得。")
    if industry_dynamics.get("bottom"):
        bottom = industry_dynamics["bottom"]
        # Some fallback feeds return a positive-only session. Calling those
        # rows "领跌" reverses the meaning of the table; preserve the ranking
        # while naming it according to the observed sign.
        bottom_label = "领跌行业" if any(float(x.get("change_pct", 0)) < 0 for x in bottom) else "涨幅靠后行业"
        lines.append(f"**{bottom_label}**：" + "；".join(f"{x['name']} {x['change_pct']:+.2f}%" for x in bottom) + "。")
    if industry_error:
        lines.append(f"**行业数据降级说明**：{industry_error}。")

    lines += ["", "**领涨行业的异动线索**（只列同日题材标签能够相互印证的证据）：", ""]
    for clue in market.get("industry_clues", []):
        if clue["items"]:
            evidence = "；".join(f"{item['name'].replace('*', '\\*')}：{item['reason']}" for item in clue["items"])
            lines.append(f"- **{clue['industry']}**：{evidence}。板块涨幅与这些题材热度同步，但同步不等于已经证明因果。")
        else:
            lines.append(f"- **{clue['industry']}**：当日强势股归因标签中未找到足够一致的行业线索，不根据板块涨幅反推原因。")

    lines += [
        "", "### 显著异动与可核验原因", "",
        "下表的涨跌、成交与换手属于行情事实；“题材归因”来自同花顺编辑维护的强势股标签，是市场关注线索，不代表已证明该题材必然导致股价上涨。", "",
        "| 股票 | 涨跌 | 换手率 | 成交额 | 题材归因 |", "|---|---:|---:|---:|---|",
    ]
    if market.get("anomalies"):
        for item in market["anomalies"]:
            change = "—" if pd.isna(item["change_pct"]) else f"{item['change_pct']:+.2f}%"
            turnover = "—" if pd.isna(item["turnover_pct"]) else f"{item['turnover_pct']:.2f}%"
            lines.append(f"| {item['name'].replace('*', '\\*')}（{item['code']}） | {change} | {turnover} | {fmt_money(item['amount'])} | {item['reason'].replace('|', '/')} |")
    else:
        lines.append("| 当日归因数据暂缺 | — | — | — | 不根据涨幅反推原因 |")
    if market.get("errors"):
        lines += ["", "**数据异常说明**：" + "；".join(market["errors"]) + "。"]

    lines += [
        "", "### 本策略当日工作范围", "",
        "市场全景用于回答“今天市场整体发生了什么”，观察池用于回答“哪些标的在既定多因子规则下值得继续跟踪”。综合分不是上涨概率；公司基本面用于解释和风险核对，本版本暂不回写量化分数。", "",
    ]
    return lines


def portfolio_diagnostics(picks: pd.DataFrame) -> dict[str, Any]:
    """Describe concentration assuming the displayed observation pool is equal-weighted."""
    if picks.empty:
        return {
            "industry_counts": {}, "primary_industry_counts": {},
            "hhi": np.nan, "top_industry_weight": np.nan,
            "primary_hhi": np.nan, "primary_top_industry_weight": np.nan,
        }
    industries = picks.get("sw_industry", picks.get("industry", pd.Series("未分类", index=picks.index))).fillna("未分类").replace("", "未分类")
    counts = industries.astype(str).value_counts()
    weights = counts / len(picks)
    primary_industries = industries.astype(str).map(
        lambda value: re.split(r"\s*[—–-]\s*", value, maxsplit=1)[0].strip() or "未分类"
    )
    primary_counts = primary_industries.value_counts()
    primary_weights = primary_counts / len(picks)
    return {
        "industry_counts": counts.to_dict(), "primary_industry_counts": primary_counts.to_dict(),
        "hhi": float((weights ** 2).sum()), "top_industry_weight": float(weights.iloc[0]),
        "primary_hhi": float((primary_weights ** 2).sum()),
        "primary_top_industry_weight": float(primary_weights.iloc[0]),
    }


def fmt_number(value: Any, digits: int = 2) -> str:
    try:
        if pd.isna(value):
            return "数据不足"
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "数据不足"


def horizon_verdict(stats: dict[str, Any], min_n: int) -> str:
    n = int(stats.get("n", 0) or 0)
    mean = stats.get("mean", np.nan)
    excess = stats.get("excess_mean", np.nan)
    win_rate = stats.get("win_rate", np.nan)
    if n < min_n:
        return f"样本不足：成熟观测{n}条，低于{min_n}条观察门槛，暂不对该周期下结论。"
    mean_pos = not pd.isna(mean) and float(mean) > 0
    excess_pos = not pd.isna(excess) and float(excess) > 0
    win_ok = not pd.isna(win_rate) and float(win_rate) >= 0.5
    if mean_pos and excess_pos and win_ok:
        return "初步有效：绝对收益为正、平均跑赢沪深300，且胜率不低于50%，但仍需继续看回撤和样本外稳定性。"
    if mean_pos and excess_pos:
        return "收益占优但胜率一般：平均收益和超额收益为正，说明少数较强样本贡献较大，需要关注收益分布是否过度依赖个股。"
    if (not mean_pos) and excess_pos:
        return "相对抗跌：绝对收益仍为负，但平均超额为正，说明在对应样本期内比沪深300少跌或更抗跌，尚不能证明稳定盈利。"
    if mean_pos and (not excess_pos):
        return "有绝对收益但未跑赢基准：模型可能跟随市场上涨，独立选股贡献还不够清晰。"
    return "表现偏弱：平均收益和平均超额均未转正，需要继续观察是否只是市场阶段影响，或因子本身需要后续复核。"


def return_evaluation_lines(perf: dict[str, Any], equity_stats: dict[str, float]) -> list[str]:
    """Explain model performance in evaluation language before detailed return tables."""
    lines = [
        "",
        "### 模型表现与盈利能力评价",
        "",
        "这里按三层口径评价：第一看绝对收益是否为正，回答“理论上有没有赚钱”；第二看是否跑赢沪深300，回答“是不是比市场更好”；第三看稳定性和风险，回答“赚钱过程是否可承受”。这些仍是历史理论统计，不代表实盘收益或未来承诺。",
        "",
        "| 评价维度 | 观察指标 | 怎么解读 | 当前读数 |",
        "|---|---|---|---|",
    ]
    one = perf.get("1", {})
    five = perf.get("5", {})
    twenty = perf.get("20", {})
    matured_days = fmt_number(equity_stats.get("days", np.nan), 0) if equity_stats else "数据不足"
    cumulative = fmt_pct(equity_stats.get("cumulative", np.nan)) if equity_stats else "数据不足"
    benchmark_cumulative = fmt_pct(equity_stats.get("benchmark_cumulative", np.nan)) if equity_stats else "数据不足"
    cumulative_excess = fmt_pct(equity_stats.get("cumulative_excess", np.nan)) if equity_stats else "数据不足"
    max_drawdown = fmt_pct(equity_stats.get("max_drawdown", np.nan)) if equity_stats else "数据不足"
    day_win_rate = fmt_pct(equity_stats.get("win_rate", np.nan), 1) if equity_stats else "数据不足"
    lines += [
        f"| 绝对盈利能力 | 平均收益、中位数、累计等权收益 | 平均值看整体赚钱能力，中位数看是否大多数样本也跟得上；累计等权收益把每天观察池串起来看路径。 | 1日均值{fmt_pct(one.get('mean'))}、中位数{fmt_pct(one.get('median'))}；5日均值{fmt_pct(five.get('mean'))}、中位数{fmt_pct(five.get('median'))}；累计等权{cumulative} |",
        f"| 相对市场能力 | 平均超额、基准均值、累计相对沪深300 | 如果绝对收益一般但超额为正，说明模型在弱市可能更抗跌；如果绝对收益为正但超额为负，可能只是跟随大盘。 | 1日平均超额{fmt_pct(one.get('excess_mean'))}；5日平均超额{fmt_pct(five.get('excess_mean'))}；累计沪深300{benchmark_cumulative}；累计差额{cumulative_excess} |",
        f"| 稳定性 | 胜率、标准差、P10/P90、下行波动 | 胜率越高代表命中更稳定；标准差和下行波动越大，说明收益过程越颠簸；P10/P90用于看较差和较好情形。 | 1日胜率{fmt_pct(one.get('win_rate'), 1)}、标准差{fmt_pct(one.get('std'))}、P10/P90={fmt_pct(one.get('p10'))}/{fmt_pct(one.get('p90'))}；5日胜率{fmt_pct(five.get('win_rate'), 1)}、下行波动{fmt_pct(five.get('downside_std'))} |",
        f"| 回撤承受度 | 等权净值最大回撤、正收益日比例 | 最大回撤衡量过程中最难受的一段跌幅；正收益日比例看组合路径是否顺滑。 | 成熟信号日{matured_days}个；最大回撤{max_drawdown}；正收益日比例{day_win_rate} |",
        f"| 盈亏结构 | 平均盈利/平均亏损、盈利因子 | 平均盈利要能覆盖平均亏损；盈利因子大于1代表样本内盈利总额超过亏损总额。 | 1日盈亏比{fmt_pct(one.get('avg_win'))}/{fmt_pct(one.get('avg_loss'))}、盈利因子{fmt_number(one.get('profit_factor'))}；5日盈亏比{fmt_pct(five.get('avg_win'))}/{fmt_pct(five.get('avg_loss'))}、盈利因子{fmt_number(five.get('profit_factor'))} |",
    ]
    lines += [
        "",
        "### 分周期结论",
        "",
        f"- **1日模型表现**：{horizon_verdict(one, 30)}",
        f"- **5日模型表现**：{horizon_verdict(five, 50)}",
        f"- **20日模型表现**：{horizon_verdict(twenty, 50)}",
        "- **总体判断**：只有当绝对收益、超额收益、胜率、回撤和盈亏结构同时改善，才更接近“具备可继续验证的盈利能力”。如果只是单项好看，应优先视为线索，而不是结论。",
    ]
    return lines


def report_overview_lines(
    picks: pd.DataFrame, market_overview: dict[str, Any], industry_dynamics: dict[str, Any],
    perf: dict[str, Any], equity_stats: dict[str, float], portfolio: dict[str, Any],
    quality_issue_count: int,
) -> list[str]:
    """Build a compact decision-oriented summary without overstating evidence."""
    five = perf.get("5", {})
    mature_n = int(five.get("n", 0) or 0)
    five_mean = five.get("mean", np.nan)
    five_excess = five.get("excess_mean", np.nan)
    profit_factor = five.get("profit_factor", np.nan)
    if mature_n < 50:
        model_status = f"样本积累中（5日成熟{mature_n}条，暂不评价稳定盈利能力）"
    elif pd.notna(five_mean) and pd.notna(five_excess) and pd.notna(profit_factor) and float(five_mean) > 0 and float(five_excess) > 0 and float(profit_factor) > 1:
        model_status = "初步有效，但仍需20日样本和样本外区间确认"
    elif pd.notna(five_excess) and float(five_excess) > 0:
        model_status = "相对抗跌，但绝对盈利与盈亏结构尚未同时通过"
    else:
        model_status = "表现偏弱，当前不支持稳定盈利结论"

    top_industries = industry_dynamics.get("top", [])[:3]
    rotation = "、".join(
        f"{row.get('name', '未标注')} {float(row.get('change_pct', np.nan)):+.2f}%"
        for row in top_industries if pd.notna(row.get("change_pct", np.nan))
    ) or "行业数据不足"
    primary_counts = portfolio.get("primary_industry_counts", {})
    primary_text = "、".join(f"{name}{count}只" for name, count in list(primary_counts.items())[:4]) or "行业数据不足"
    cumulative = fmt_pct(equity_stats.get("cumulative", np.nan)) if equity_stats else "数据不足"
    cumulative_excess = fmt_pct(equity_stats.get("cumulative_excess", np.nan)) if equity_stats else "数据不足"
    concentration = portfolio.get("primary_top_industry_weight", np.nan)
    concentration_note = (
        f"一级行业最高占比{float(concentration)*100:.1f}%"
        if pd.notna(concentration) else "集中度数据不足"
    )
    quality_status = "未记录接口异常" if quality_issue_count == 0 else f"记录{quality_issue_count}类数据源降级或缺失，详见第九节"
    return [
        "## 报告速览", "",
        "先用这张表回答市场、轮动、观察池、模型和数据是否值得继续深入阅读；详细证据在后续章节。", "",
        "| 关注问题 | 当前结论 | 应如何理解 |",
        "|---|---|---|",
        f"| 今天市场怎样 | **{market_overview.get('regime', '数据不足')}**；{market_overview.get('breadth_text', '涨跌结构数据不足')} | 这是指数、涨跌家数和成交结构的行情事实，不是明日方向预测。 |",
        f"| 行业在轮动什么 | {rotation} | 仅表示当日相对强弱；需结合成交延续和可核验事件判断是否形成主线。 |",
        f"| 观察池是否集中 | {primary_text}；{concentration_note} | 按申万一级行业合并统计，避免把银行等行业拆成多个二级行业后低估集中风险。 |",
        f"| 模型目前怎样 | **{model_status}**；累计理论收益{cumulative}，累计相对沪深300差额{cumulative_excess} | 理论统计不等于实盘收益；优先看成熟样本数、超额、回撤和盈利因子是否共同改善。 |",
        f"| 数据是否完整 | {quality_status} | 降级数据仍可用于观察，但统计范围和行业分类可能与主数据源不同。 |",
        "",
    ]


def render_report(
    picks: pd.DataFrame, signal_date: str, config: dict[str, Any], snapshot_count: int,
    snapshot_source: str, screened_count: int, kline_errors: list[str], data_warnings: list[str],
    fundamental_errors: list[str], news_errors: list[str], industry_dynamics: dict[str, Any], industry_error: str | None,
    market_overview: dict[str, Any], market_context: dict[str, Any] | None,
    perf: dict[str, Any], equity_stats: dict[str, float],
    iteration_text: str, correlations: dict[str, float],
) -> str:
    pdg = portfolio_diagnostics(picks)
    quality_issue_count = (
        len(data_warnings) + len(kline_errors) + len(fundamental_errors) + len(news_errors)
        + (1 if industry_error else 0) + len(market_overview.get("errors", []))
    )
    lines = [
        f"# A股每日量化与技术分析报告｜{signal_date}", "",
        f"**策略版本**：{config['strategy_version']}　**报告版本**：{config['report_version']}　**数据截止交易日**：{signal_date}　**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "",
        "> 本报告是规则化研究观察池，不构成投资建议。所有收益均为理论统计，不代表实盘可成交或未来表现。", "",
        *report_overview_lines(picks, market_overview, industry_dynamics, perf, equity_stats, pdg, quality_issue_count),
        *render_market_overview_lines(market_overview, industry_dynamics, industry_error, market_context),
        "## 二、今日观察池", "",
        f"系统从“{snapshot_source}”读取了{snapshot_count}只股票，基础过滤后对流动性与初步排名靠前的{screened_count}只股票下载历史K线，最终保留{len(picks)}只观察标的。", "",
        "### 组合结构提示", "",
        "观察池按排名展示，不等于建议持仓。若以10只股票等权模拟，单票权重约10%；实际集中度、行业暴露和交易成本仍需单独评估。", "",
    ]
    counts_text = "；".join(f"{name} {count}只" for name, count in pdg["industry_counts"].items())
    primary_counts_text = "；".join(f"{name} {count}只" for name, count in pdg["primary_industry_counts"].items())
    lines += [
        f"- **一级行业分布**：{primary_counts_text or '数据不足'}。",
        f"- **细分行业分布**：{counts_text or '数据不足'}。",
        f"- **集中度提示**：一级行业HHI={pdg['primary_hhi']:.3f}、最高占比{pdg['primary_top_industry_weight']*100:.1f}%；细分行业HHI={pdg['hhi']:.3f}、最高占比{pdg['top_industry_weight']*100:.1f}%。一级行业更适合识别共同风险暴露；指标基于观察池等权假设，不代表实际持仓。", "",
        "| 排名 | 代码 | 名称 | 申万行业 | 综合分 | 总市值 | PE | PB | 20日涨跌 | 60日涨跌 |", "|---:|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in picks.iterrows():
        industry = display_text(r.get("sw_industry"), display_text(r.get("industry"), "未分类")).replace("|", "/")
        lines.append(
            f"| {int(r['rank'])} | {r['code']} | {r['name']} | {industry} | {r['total_score']:.1f} | "
            f"{fmt_money(r.get('market_cap', np.nan))} | {fmt_multiple(r.get('pe', np.nan))} | {fmt_multiple(r.get('pb', np.nan))} | "
            f"{r['ret_20d']*100:.1f}% | {r['ret_60d']*100:.1f}% |"
        )

    return_detail_lines = ["", "## 六、逐股收益跟踪", "", "本节不再只罗列今日观察池，而是先看收益贡献、拖累和异常。今日盈亏按收盘价相对前一交易日收盘价计算，仅作行情标记；金额按1万元示例本金估算，不代表实际持仓。历史收益沿用该股票最近一次正式信号，以次一交易日开盘为理论入场价，并扣除费用与滑点。", ""]
    tracking = picks.copy()
    for col in ("today_return", "today_pnl_10k", "hist_ret_1d", "hist_ret_5d", "hist_ret_20d", "hist_excess_1d", "hist_excess_5d", "hist_excess_20d"):
        tracking[col] = pd.to_numeric(tracking.get(col, np.nan), errors="coerce")

    def tracking_label(row: pd.Series) -> str:
        matured = []
        for horizon in (1, 5, 20):
            if not pd.isna(row.get(f"hist_ret_{horizon}d")):
                matured.append(f"{horizon}日")
        flags = []
        today_return = row.get("today_return", np.nan)
        if not pd.isna(today_return) and abs(float(today_return)) >= 0.03:
            flags.append("当日波动较大")
        if not matured:
            flags.append("历史周期未成熟/无历史信号")
        else:
            flags.append("已成熟：" + "、".join(matured))
        hist_5d = row.get("hist_ret_5d", np.nan)
        hist_excess_5d = row.get("hist_excess_5d", np.nan)
        if not pd.isna(hist_5d) and float(hist_5d) <= -0.05:
            flags.append("5日理论回撤偏大")
        if not pd.isna(hist_excess_5d) and float(hist_excess_5d) > 0:
            flags.append("5日跑赢基准")
        return "；".join(flags)

    def best_mature_return(row: pd.Series) -> tuple[float, str]:
        for horizon in (20, 5, 1):
            value = row.get(f"hist_ret_{horizon}d", np.nan)
            if not pd.isna(value):
                return float(value), f"{horizon}日"
        return np.nan, "未成熟"

    tracking["_status"] = tracking.apply(tracking_label, axis=1)
    mature_pairs = tracking.apply(best_mature_return, axis=1, result_type="expand")
    tracking["_latest_mature_ret"] = pd.to_numeric(mature_pairs[0], errors="coerce")
    tracking["_latest_mature_horizon"] = mature_pairs[1]

    return_detail_lines += [
        "### 今日观察池收益贡献",
        "",
        "这里看的是今天这些观察股的当日涨跌和1万元示例盈亏，用来识别当天谁在贡献、谁在拖累；它不是历史信号收益，也不是实盘盈亏。",
        "",
    ]
    today_valid = tracking.dropna(subset=["today_return"]).copy()
    if today_valid.empty:
        return_detail_lines.append("- 当日涨跌数据不足，无法排序。")
    else:
        positive_today = today_valid[today_valid["today_return"] > 0].sort_values("today_return", ascending=False).head(3)
        negative_today = today_valid[today_valid["today_return"] < 0].sort_values("today_return", ascending=True).head(3)
        equal_weight_return = float(today_valid["today_return"].mean())
        equal_weight_pnl = equal_weight_return * 10000
        return_detail_lines += [
            f"- **观察池等权当日合计**：平均涨跌{fmt_pct(equal_weight_return)}，按组合总本金1万元等权分配估算约{fmt_money(equal_weight_pnl)}；上涨{int((today_valid['today_return'] > 0).sum())}只、下跌{int((today_valid['today_return'] < 0).sum())}只、平盘{int((today_valid['today_return'] == 0).sum())}只。",
            "",
            "| 类型 | 股票 | 今日涨跌 | 1万元示例盈亏 | 备注 |",
            "|---|---|---:|---:|---|",
        ]
        for _, r in positive_today.iterrows():
            return_detail_lines.append(f"| 贡献较多 | {r['name']}（{r['code']}） | {fmt_pct(r.get('today_return'))} | {fmt_money(r.get('today_pnl_10k'))} | {r['_status']} |")
        for _, r in negative_today.iterrows():
            return_detail_lines.append(f"| 拖累较多 | {r['name']}（{r['code']}） | {fmt_pct(r.get('today_return'))} | {fmt_money(r.get('today_pnl_10k'))} | {r['_status']} |")
        if positive_today.empty:
            return_detail_lines.append("| 无正贡献标的 | - | - | - | 今日观察池没有上涨股票 |")
        if negative_today.empty:
            return_detail_lines.append("| 无负贡献标的 | - | - | - | 今日观察池没有下跌股票 |")

    return_detail_lines += [
        "",
        "### 最近历史信号收益前后对比",
        "",
        "这里优先使用已成熟的20日收益；20日未成熟则看5日，5日未成熟再看1日。它回答的是“这只股票最近一次正式信号到目前能评价的周期表现如何”。",
        "",
    ]
    hist_valid = tracking.dropna(subset=["_latest_mature_ret"]).copy()
    if hist_valid.empty:
        return_detail_lines.append("- 今日观察池暂无可评价的成熟历史收益；需要等1/5/20日周期陆续成熟后再判断。")
    else:
        top_hist = hist_valid.sort_values("_latest_mature_ret", ascending=False).head(3)
        bottom_candidates = hist_valid.drop(index=top_hist.index, errors="ignore")
        bottom_hist = bottom_candidates.sort_values("_latest_mature_ret", ascending=True).head(3)
        return_detail_lines += [
            "| 类型 | 股票 | 最近信号日 | 可评价周期 | 理论收益 | 同期超额 | 备注 |",
            "|---|---|---|---|---:|---:|---|",
        ]
        for _, r in top_hist.iterrows():
            horizon = str(r["_latest_mature_horizon"]).replace("日", "")
            label = "历史表现较好" if float(r["_latest_mature_ret"]) > 0 else "相对靠前（仍为负）"
            return_detail_lines.append(f"| {label} | {r['name']}（{r['code']}） | {display_text(r.get('hist_signal_date'), '无')} | {r['_latest_mature_horizon']} | {fmt_pct(r.get('_latest_mature_ret'))} | {fmt_pct(r.get(f'hist_excess_{horizon}d'))} | {r['_status']} |")
        for _, r in bottom_hist.iterrows():
            horizon = str(r["_latest_mature_horizon"]).replace("日", "")
            label = "历史表现较弱" if float(r["_latest_mature_ret"]) < 0 else "相对靠后（仍为正）"
            return_detail_lines.append(f"| {label} | {r['name']}（{r['code']}） | {display_text(r.get('hist_signal_date'), '无')} | {r['_latest_mature_horizon']} | {fmt_pct(r.get('_latest_mature_ret'))} | {fmt_pct(r.get(f'hist_excess_{horizon}d'))} | {r['_status']} |")

    immature = tracking[tracking[["hist_ret_1d", "hist_ret_5d", "hist_ret_20d"]].isna().all(axis=1)].copy()
    abnormal = tracking[
        (tracking["today_return"].abs() >= 0.03)
        | (tracking["hist_ret_5d"] <= -0.05)
        | (tracking["hist_excess_5d"] <= -0.03)
    ].copy()
    return_detail_lines += [
        "",
        "### 异常与不可评价清单",
        "",
        "异常不是买卖信号，只是提醒后续复盘优先看这些标的：当日波动过大、5日理论回撤偏大，或明显跑输基准。未成熟样本单独汇总，避免把一堆“数据不足”误看成收益异常。",
        "",
    ]
    if immature.empty:
        return_detail_lines.append("- **不可评价汇总**：今日观察池均已有至少一个成熟历史收益周期。")
    else:
        immature_names = "、".join(f"{r['name']}（{r['code']}）" for _, r in immature.iterrows())
        return_detail_lines.append(f"- **不可评价汇总**：{len(immature)}只股票暂无成熟历史收益周期，主要因为最近信号尚未走完1/5/20日观察窗口：{immature_names}。")
        return_detail_lines.append("")
    if abnormal.empty:
        return_detail_lines.append("- **收益异常项**：未发现需要单独标注的收益异常。")
    else:
        return_detail_lines += [
            "**收益异常项**：",
            "",
            "| 股票 | 今日涨跌 | 5日理论收益 | 5日超额 | 状态说明 |",
            "|---|---:|---:|---:|---|",
        ]
        for _, r in abnormal.iterrows():
            return_detail_lines.append(f"| {r['name']}（{r['code']}） | {fmt_pct(r.get('today_return'))} | {fmt_pct(r.get('hist_ret_5d'))} | {fmt_pct(r.get('hist_excess_5d'))} | {r['_status']} |")

    return_detail_lines += [
        "",
        "### 今日观察池完整收益明细",
        "",
        "| 股票 | 今日涨跌 | 今日盈亏(1万元) | 最近信号日 | 1日理论收益 | 5日理论收益 | 20日理论收益 | 状态说明 |",
        "|---|---:|---:|---|---:|---:|---:|---|",
    ]
    for _, r in tracking.iterrows():
        return_detail_lines.append(f"| {r['name']}（{r['code']}） | {fmt_pct(r.get('today_return'))} | {fmt_money(r.get('today_pnl_10k'))} | {display_text(r.get('hist_signal_date'), '本日/无历史')} | {fmt_pct(r.get('hist_ret_1d'))} | {fmt_pct(r.get('hist_ret_5d'))} | {fmt_pct(r.get('hist_ret_20d'))} | {r['_status']} |")

    stock_pool = perf.get("stock_pool", [])
    signal_cohorts = perf.get("signal_cohorts", [])
    return_detail_lines += [
        "", "## 七、历史信号股票池收益统计", "",
        "本节包含此前所有正式信号股票，不仅是今日观察池。每条信号按“次一交易日开盘理论入场、收盘退出，并扣除费用与滑点”计算；同一股票多次入选会分别计为多条信号观测。未成熟周期显示为数据不足，不用当前价格补算。",
        "",
        f"历史股票池累计覆盖{len(stock_pool)}只不同股票、{sum(int(x.get('signals', 0)) for x in stock_pool)}条信号、{len(signal_cohorts)}个信号日。当前观察池与历史池可能重叠，但两者统计口径不同。",
        "",
        "### 历史股票池逐股票统计", "",
        "| 股票 | 行业 | 信号次数 | 首次信号 | 最近信号 | 1日成熟数 | 1日均值 | 1日胜率 | 1日超额 | 5日成熟数 | 5日均值 | 5日胜率 | 5日超额 | 20日成熟数 | 20日均值 | 20日胜率 | 20日超额 |",
        "|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in stock_pool:
        return_detail_lines.append(
            f"| {row['name']}（{row['code']}） | {row['industry']} | {row['signals']} | {row['first_signal']} | {row['last_signal']} | "
            f"{row['n_1d']} | {fmt_pct(row['mean_1d'])} | {fmt_pct(row['win_1d'], 1)} | {fmt_pct(row['excess_1d'])} | "
            f"{row['n_5d']} | {fmt_pct(row['mean_5d'])} | {fmt_pct(row['win_5d'], 1)} | {fmt_pct(row['excess_5d'])} | "
            f"{row['n_20d']} | {fmt_pct(row['mean_20d'])} | {fmt_pct(row['win_20d'], 1)} | {fmt_pct(row['excess_20d'])} |"
        )
    return_detail_lines += [
        "",
        "### 按信号日期的历史股票池表现", "",
        "| 信号日期 | 股票数 | 1日成熟数 | 1日均值 | 1日胜率 | 1日超额 | 5日成熟数 | 5日均值 | 5日胜率 | 5日超额 | 20日成熟数 | 20日均值 | 20日胜率 | 20日超额 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in signal_cohorts:
        return_detail_lines.append(
            f"| {row['signal_date']} | {row['stocks']} | {row['n_1d']} | {fmt_pct(row['mean_1d'])} | {fmt_pct(row['win_1d'], 1)} | {fmt_pct(row['excess_1d'])} | "
            f"{row['n_5d']} | {fmt_pct(row['mean_5d'])} | {fmt_pct(row['win_5d'], 1)} | {fmt_pct(row['excess_5d'])} | "
            f"{row['n_20d']} | {fmt_pct(row['mean_20d'])} | {fmt_pct(row['win_20d'], 1)} | {fmt_pct(row['excess_20d'])} |"
        )

    lines += ["", "## 三、逐只股票详细分析", ""]
    for _, r in picks.iterrows():
        reasons, risks, confirm, invalid = candidate_explanation(r)
        industry = display_text(r.get("sw_industry"), display_text(r.get("industry"), "未分类"))
        business = display_text(r.get("main_business"), "公司主营资料暂未取得")
        products = display_text(r.get("products"), "产品资料暂未取得")
        drivers, industry_risks = industry_watchpoints(industry, business)
        business_model, profit_logic, cycle_position = industry_business_context(industry, business)
        industry_move = industry_dynamics.get("matched", {}).get(industry)
        assessments = fundamental_assessment(r)
        period = display_text(r.get("finance_period"), "未取得")
        is_financial = any(word in industry for word in ("银行", "保险", "证券", "多元金融"))
        debt_note = "金融业口径特殊，不与普通企业直接比较" if is_financial else "总负债/总资产，需结合同行比较"
        lines += [
            f"### {int(r['rank'])}. {r['name']}（{r['code']}）", "",
            "#### 公司、业务与行业", "",
            f"- **公司在做什么**：{business.rstrip('。；; ')}。",
            f"- **主要产品/服务**：{products.rstrip('。；; ')}。",
            f"- **商业模式**：{business_model}。",
            f"- **利润形成机制**：{profit_logic}。",
            f"- **行业周期特征**：{cycle_position}。",
            f"- **所属行业与地域**：{industry}；{display_text(r.get('region'), '地域资料暂缺')}。",
            f"- **行业核心观察变量**：{drivers}。",
            f"- **行业常见风险**：{industry_risks}。", "",
            (f"- **当日行业表现**：{industry_move['name']}涨跌{industry_move['change_pct']:.2f}%，{industry_breadth_text(industry_move)}，领涨股：{industry_move['leader'] or '未标注'}。" if industry_move else "- **当日行业表现**：申万行业与备用行业分类口径不同，暂未找到可靠的一一对应板块。"), "",
            "#### 公司新闻与公告动态", "",
            *([f"- {n['time']}｜{n['source'] or '来源未标注'}｜[{n['title']}]({n['url']})" for n in r.get('news_items', [])] or ["- 东方财富个股新闻、新浪财经7×24和巨潮公告均未返回近期关联条目；这表示本次检索无结果，不代表公司没有其他媒体报道。"]), "",
            "#### 收益跟踪", "",
            f"- **今日行情盈亏**：涨跌{fmt_pct(r.get('today_return'))}，按1万元示例本金约为{fmt_money(r.get('today_pnl_10k'))}；这是当日收盘标记，不等于实际交易盈亏。",
            f"- **历史信号收益**：最近信号日为{display_text(r.get('hist_signal_date'), '无')}；1日{fmt_pct(r.get('hist_ret_1d'))}、5日{fmt_pct(r.get('hist_ret_5d'))}、20日{fmt_pct(r.get('hist_ret_20d'))}。", "",
            f"#### 传统基本面检查（最新可得报告期：{period}）", "",
            "| 指标 | 当前数据 | 同比/解释 |", "|---|---:|---|",
            f"| 营业收入 | {fmt_money(r.get('revenue', np.nan))} | {fmt_pct(r.get('revenue_yoy', np.nan))} |",
            f"| 归母净利润 | {fmt_money(r.get('net_profit', np.nan))} | {fmt_pct(r.get('net_profit_yoy', np.nan))} |",
            f"| 经营活动现金流净额 | {fmt_money(r.get('operating_cash_flow', np.nan))} | {fmt_pct(r.get('operating_cash_flow_yoy', np.nan))} |",
            f"| 净利率 | {fmt_pct(r.get('net_margin', np.nan))} | 净利润/营业收入 |",
            f"| 资产负债率 | {fmt_pct(r.get('debt_ratio', np.nan))} | {debt_note} |",
            f"| 市值/估值快照 | {fmt_money(r.get('market_cap', np.nan))} | PE {fmt_multiple(r.get('pe', np.nan))}；PB {fmt_multiple(r.get('pb', np.nan))} |", "",
            "**基本面解读**：" + "；".join(assessments) + "。", "",
            "#### 技术面与量化信号", "",
            f"- **为什么入选**：{reasons}。",
            f"- **五维得分**：动量{r['momentum_score']:.1f}、趋势{r['trend_score']:.1f}、量价{r['volume_price_score']:.1f}、估值{r['valuation_score']:.1f}、风险质量{r['risk_score']:.1f}。分数是本批候选的相对分位，不是概率。",
            "| 维度 | 原始数值 | 计算拆解 | 通俗解释 |", "|---|---|---|---|",
            f"| 动量 | 20日{fmt_pct(r['ret_20d'])}；60日{fmt_pct(r['ret_60d'])}；120日{fmt_pct(r['ret_120d'])} | 35%×20日分位{r['momentum_20_pct']:.1f}+40%×60日分位{r['momentum_60_pct']:.1f}+25%×120日分位{r['momentum_120_pct']:.1f} | 最近涨势是否持续 |",
            f"| 趋势 | 收盘{r['close']:.2f}；MA20 {r['ma20']:.2f}；MA60 {r['ma60']:.2f}；60日回撤{fmt_pct(r['max_drawdown_60d'])} | 站上均线各占25分，另加回撤分位25% | 股价是否站在市场平均成本线上方 |",
            f"| 量价 | 健康配合{fmt_pct(r['healthy_volume_price_ratio'])}；5/20量比{r['volume_ratio_5_20']:.2f}；突破距离{fmt_pct(r['breakout_distance'])} | 健康配合×50%+突破质量×25%+量能平衡×25% | 上涨是否有量、回调是否缩量 |",
            f"| 估值 | PE {fmt_multiple(r['pe'])}；PB {fmt_multiple(r['pb'])} | 65%×PE相对分位{r['pe_pct']:.1f}+35%×PB相对分位{r['pb_pct']:.1f} | 在同批股票中相对贵或便宜 |",
            f"| 风险质量 | 年化波动{fmt_pct(r['volatility_20d'])}；60日回撤{fmt_pct(r['max_drawdown_60d'])}；RSI {r['rsi14']:.0f}；成交额{fmt_money(r['amount'])} | 35%波动+30%回撤+20%RSI适配+15%流动性 | 波动越小、回撤越浅、交易越容易，得分越高 |",
            f"![{r['name']} K线图](charts/{display_text(r.get('chart_file'), f'{r["code"]}.png')})", "",
            f"- **均线状态**：收盘{r['close']:.2f}元，MA20={r['ma20']:.2f}元，MA60={r['ma60']:.2f}元。",
            f"- **量价与强弱**：20日涨跌{fmt_pct(r['ret_20d'])}，60日涨跌{fmt_pct(r['ret_60d'])}，RSI={r['rsi14']:.0f}，5/20日均量比={r['volume_ratio_5_20']:.2f}。",
            f"- **主要风险**：{risks}。",
            f"- **观察确认条件**：{confirm}。",
            f"- **信号失效条件**：{invalid}。", "",
            "**综合结论**：基本面回答“公司靠什么赚钱、质量怎么样”，技术面回答“市场资金当前是否认可”。两者同时改善更有利，但任何单一指标都不能保证上涨。", "",
        ]

    lines += [
        "## 四、基本面与技术面术语翻译", "",
        "- **营业收入**：公司卖产品或提供服务获得的收入，可先看是否持续增长。",
        "- **归母净利润**：最终属于上市公司普通股股东的利润，比只看收入更接近股东成果。",
        "- **经营现金流**：主营业务真正流入流出的现金；长期明显弱于利润时要查原因。",
        "- **PE/PB**：市盈率/市净率。数值低不必然便宜，可能反映周期下行或经营风险。",
        "- **动量**：过去一段时间跑得是否持续，不等于明天还会涨。",
        "- **MA20/MA60**：过去20/60个交易日平均价格，可理解为短期和中期市场成本线。",
        "- **量比（5/20）**：近5日平均成交量除以近20日平均成交量；大于1代表近期更活跃。",
        "- **RSI**：衡量短期涨跌强弱，超过70通常意味着偏热，但不等于马上下跌。",
        "- **最大回撤**：阶段高点到之后低点的最大跌幅，用来感受过程中的风险。", "",
        "## 五、理论收益统计与模型表现评价", "",
        "统计采用信号生成后的下一交易日开盘作为理论入场价，并扣除配置中的双边费用与滑点。", "",
        "| 周期 | 成熟观测数 | 平均净收益 | 中位数 | 标准差 | P25~P75 | 最小~最大 | 胜率 | 平均盈利/亏损 | 基准均值 | 平均超额 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for horizon in (1, 5, 20):
        s = perf[str(horizon)]
        lines.append(
            f"| {horizon}日 | {s['n']} | {fmt_pct(s['mean'])} | {fmt_pct(s['median'])} | "
            f"{fmt_pct(s['std'])} | {fmt_pct(s['q25'])}~{fmt_pct(s['q75'])} | {fmt_pct(s['min'])}~{fmt_pct(s['max'])} | {fmt_pct(s['win_rate'], 1)} | {fmt_pct(s['avg_win'])}/{fmt_pct(s['avg_loss'])} | {fmt_pct(s['benchmark_mean'])} | {fmt_pct(s['excess_mean'])} |"
        )
    lines += ["", "**收益分布补充**："]
    for horizon in (1, 5, 20):
        s = perf[str(horizon)]
        lines.append(
            f"- {horizon}日：P10/P90={fmt_pct(s['p10'])}/{fmt_pct(s['p90'])}；"
            f"下行波动={fmt_pct(s['downside_std'])}；盈利因子={fmt_number(s['profit_factor'])}（数据不足时不解读）。"
        )
    if equity_stats:
        lines += ["", (
            f"按每日观察池的下一交易日等权收益串联：累计理论收益{fmt_pct(equity_stats['cumulative'])}，"
            f"同期沪深300{fmt_pct(equity_stats['benchmark_cumulative'])}，"
            f"累计相对差额{fmt_pct(equity_stats.get('cumulative_excess', np.nan))}，"
            f"最大回撤{fmt_pct(equity_stats['max_drawdown'])}，正收益日比例{fmt_pct(equity_stats['win_rate'], 1)}。"
        )]
    else:
        lines += ["", "首次运行尚无下一交易日数据，理论收益将在未来运行中自动补齐。"]

    lines += return_evaluation_lines(perf, equity_stats)
    lines += return_detail_lines

    lines += ["", "## 八、策略迭代诊断", "", iteration_text]
    if correlations:
        lines += ["", "成熟样本中各因子分与未来5日收益的Spearman相关性："]
        for name, value in sorted(correlations.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"- `{name}`：{value:.3f}")
    lines += [
        "", "## 九、数据质量与限制", "",
        *([f"- {warning}" for warning in data_warnings] or ["- 股票池主数据源正常。"]),
        f"- K线下载失败：{len(kline_errors)}只。" + (f" 示例：{'；'.join(kline_errors[:3])}" if kline_errors else ""),
        f"- 基本面资料获取失败：{len(fundamental_errors)}只。" + (f" 示例：{'；'.join(fundamental_errors[:3])}" if fundamental_errors else ""),
        f"- 公司新闻/公告获取异常：{len(news_errors)}项。" + (f" 示例：{'；'.join(news_errors[:3])}" if news_errors else ""),
        "- 财务报表按数据源最新可得报告期展示；不同公司披露日期可能不同，且未经本报告重新审计。",
        "- 基本面暂仅作为解释层，不进入量化排名，因此不会造成历史策略分数口径突然变化。",
        "- 未完整模拟涨跌停无法成交、停牌、冲击成本、资金容量和税费差异。",
        "- 观察池每日更新，不能把连续多日重复入选当成多个独立统计样本。", "",
        "---", f"*数据来源：K线优先腾讯财经前复权、失败时降级为新浪不复权；{snapshot_source}；同花顺公司资料；新浪财务报表。仅供研究学习。*", "",
    ]
    from event_analysis import rewrite_event_section
    return rewrite_event_section("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="每日A股量化选股、技术报告和理论收益追踪")
    parser.add_argument("--output", default="quant-agent-output")
    parser.add_argument("--force", action="store_true", help="允许在周末测试或覆盖重复日期报告；不会重复追加信号")
    parser.add_argument("--preselect", type=int, help="测试时覆盖预选数量")
    parser.add_argument("--open-browser", action="store_true", help="仅在成功生成新报告后用默认浏览器打开HTML报告")
    parser.add_argument("--strict-quality", action="store_true", help="实验性质量门槛：行业分类与核心字段未通过审计时返回data_error，写入信号前停止")
    args = parser.parse_args()

    paths = create_paths(Path(args.output).resolve())
    try:
        config = load_config(paths.config)
        if args.preselect:
            config["preselect_count"] = max(args.preselect, int(config["candidate_count"]))

        # Cheap trading-calendar probe before any expensive market-wide request.
        probe_benchmark = fetch_kline("000300", 5)
        if probe_benchmark.empty:
            raise RuntimeError("无法用沪深300判断最新交易日")
        probe_date = str(probe_benchmark.iloc[-1]["date"])
        probe_report = paths.daily / probe_date / "report.md"
        beijing_now = datetime.now(ZoneInfo("Asia/Shanghai"))
        beijing_today = beijing_now.date().isoformat()
        intraday_today = probe_date == beijing_today and (beijing_now.hour, beijing_now.minute) < (15, 10)
        if intraday_today and not args.force:
            print(json.dumps({
                "status": "market_open", "beijing_date": beijing_today, "latest_market_date": probe_date,
                "message": "A股尚未收盘；15:10前不生成正式盘后信号。",
            }, ensure_ascii=False))
            return 0
        if probe_date != beijing_today and not args.force:
            status = "duplicate" if probe_report.exists() else "non_trading_day"
            print(json.dumps({
                "status": status, "beijing_date": beijing_today, "latest_market_date": probe_date,
                "report": str(probe_report) if probe_report.exists() else None,
                "message": "当日不是已更新的A股交易日，未新增信号。",
            }, ensure_ascii=False))
            return 0
        if probe_report.exists() and not args.force:
            print(json.dumps({"status": "duplicate", "data_date": probe_date, "report": str(probe_report)}, ensure_ascii=False))
            return 0

        log("[1/7] 获取全市场快照")
        snapshot, data_warnings = fetch_snapshot_with_fallback()
        snapshot_source = snapshot.attrs.get("source", "未知股票池")
        preselected = preliminary_filter(snapshot, config)

        log(f"[2/7] 下载{len(preselected)}只预选股前复权K线")
        kline_map, kline_errors = fetch_many_klines(
            preselected["code"].tolist(), int(config["kline_bars"]), int(config["max_workers"])
        )
        fallback_codes = [
            code for code, frame in kline_map.items()
            if "新浪不复权" in str(getattr(frame, "attrs", {}).get("source", ""))
        ]
        if fallback_codes:
            data_warnings.append(
                f"腾讯K线不可用时有{len(fallback_codes)}只股票降级为新浪不复权K线；跨除权除息区间的收益和均线可能失真。"
            )
        feature_rows = []
        for code, bars in kline_map.items():
            item = compute_features(code, bars, int(config["min_kline_bars"]))
            if item:
                feature_rows.append(item)
        if not feature_rows:
            raise RuntimeError("未获得足够K线数据")
        features = pd.DataFrame(feature_rows)
        data_date = str(features["data_date"].mode().iloc[0])
        features = features[features["data_date"] == data_date]

        report_dir = paths.daily / data_date
        report_path = report_dir / "report.md"
        candidates_path = report_dir / "candidates.csv"
        fundamentals_path = report_dir / "fundamentals.csv"
        beijing_today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        if data_date != beijing_today and not args.force:
            status = "duplicate" if report_path.exists() else "non_trading_day"
            print(json.dumps({
                "status": status, "beijing_date": beijing_today, "latest_market_date": data_date,
                "report": str(report_path) if report_path.exists() else None,
                "message": "当日不是已更新的A股交易日，未新增信号。",
            }, ensure_ascii=False))
            return 0
        if report_path.exists() and not args.force:
            print(json.dumps({"status": "duplicate", "data_date": data_date, "report": str(report_path)}, ensure_ascii=False))
            return 0

        log("[3/7] 计算多因子分数与行业分散")
        scored = score_features(features, snapshot, config)
        picks = diversify(scored, int(config["candidate_count"]), int(config["max_per_industry"]))
        if picks.empty:
            raise RuntimeError("未生成最终候选")

        log(f"[4/7] 获取{len(picks)}只候选股的公司资料与财务报表")
        fundamentals, fundamental_errors = fetch_many_fundamentals(picks["code"].tolist(), workers=4)
        picks = picks.merge(fundamentals, on="code", how="left")
        from quality_audit import audit_selection, enforce_quality
        from report_insights import write_json
        selection_audit = audit_selection(preselected, picks, int(config['max_per_industry']))
        report_dir.mkdir(parents=True, exist_ok=True)
        write_json(report_dir / 'selection_quality.json', selection_audit)
        if args.strict_quality:
            enforce_quality(selection_audit)
        industry_names = [display_text(row.get("sw_industry"), display_text(row.get("industry"), "")) for _, row in picks.iterrows()]
        industry_dynamics, industry_error = fetch_industry_dynamics(industry_names)
        indices, index_errors = fetch_index_overview()
        hot_reasons, hot_reason_error = fetch_hot_reasons(data_date)
        market_context = fetch_market_context(data_date)
        market_errors = index_errors + ([hot_reason_error] if hot_reason_error else []) + list(market_context.get("errors", []))
        market_overview = build_market_overview(
            snapshot, snapshot_source, indices, industry_dynamics, hot_reasons, market_errors,
        )
        news_map, news_errors = fetch_many_news(picks["code"].tolist(), workers=4)
        # Some announcement feeds label after-hours releases with the next calendar
        # date. A daily report must never include items dated after its data date.
        for news_code, news_items in news_map.items():
            news_map[news_code] = [
                item for item in news_items
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(item.get("time") or ""))
                or str(item.get("time")) <= data_date
            ]
        picks["news_items"] = picks["code"].map(news_map).apply(lambda value: value if isinstance(value, list) else [])
        chart_dir = report_dir / "charts"
        chart_files = []
        for _, candidate in picks.iterrows():
            filename = f"{candidate['code']}.png"
            chart_files.append(plot_candidate_chart(candidate["code"], candidate["name"], kline_map.get(candidate["code"], pd.DataFrame()), chart_dir / filename))
        picks["chart_file"] = chart_files

        signals = load_signals(paths.signals)
        pending_codes = signals.loc[signals["ret_20d"].isna(), "code"].dropna().astype(str).str.zfill(6).unique().tolist()
        missing_pending = [c for c in pending_codes if c not in kline_map]
        log(f"[5/7] 更新历史信号收益（待补K线{len(missing_pending)}只）")
        if missing_pending:
            extra, extra_errors = fetch_many_klines(missing_pending, int(config["kline_bars"]), int(config["max_workers"]))
            kline_map.update(extra)
            kline_errors.extend(extra_errors)
        benchmark = fetch_kline("000300", int(config["kline_bars"]))
        if "新浪不复权" in str(benchmark.attrs.get("source", "")):
            data_warnings.append("沪深300基准K线已降级为新浪不复权数据。")
        if not intraday_today:
            signals = update_signal_returns(signals, kline_map, benchmark, config)
        else:
            data_warnings.append("当前为盘中强制测试报告：未更新历史收益，也未追加正式信号。")
        perf = performance_summary(signals)
        _, equity_stats = save_equity_curve(signals, paths.equity)
        iteration_text, correlations = iteration_diagnostics(signals)

        existing_signal_day = (
            not signals.empty and (signals["signal_date"].astype(str) == data_date).any()
        )
        if not existing_signal_day and not intraday_today:
            signals = append_current_signals(signals, picks, data_date, config)
        signals.to_csv(paths.signals, index=False, encoding="utf-8-sig")
        picks = attach_return_tracking(picks, signals)

        log("[6/7] 生成详细基本面与技术分析报告")
        report_dir.mkdir(parents=True, exist_ok=True)
        report = render_report(
            picks, data_date, config, len(snapshot), snapshot_source, len(preselected), kline_errors, data_warnings,
            fundamental_errors, news_errors, industry_dynamics, industry_error,
            market_overview, market_context,
            perf, equity_stats, iteration_text, correlations,
        )
        report_path.write_text(report, encoding="utf-8")
        html_path = report_dir / "report.html"
        picks.to_csv(candidates_path, index=False, encoding="utf-8-sig")
        fundamental_columns = [
            "rank", "code", "name", "region", "sw_industry", "main_business", "products",
            "finance_period", "balance_period", "cash_period", "revenue", "revenue_yoy",
            "net_profit", "net_profit_yoy", "operating_cash_flow", "operating_cash_flow_yoy",
            "total_assets", "total_liabilities", "net_margin", "cash_profit_ratio", "debt_ratio",
            "market_cap", "pe", "pb",
        ]
        picks[[column for column in fundamental_columns if column in picks.columns]].to_csv(
            fundamentals_path, index=False, encoding="utf-8-sig"
        )
        from report_insights import capture_inputs, build_context, write_json
        from news_evidence import collect
        capture_inputs(report_dir, snapshot, industry_dynamics, signals, market_context, benchmark)
        write_json(report_dir / 'news_audit.json', collect(market_context.get('events', [])))
        context = build_context(report_dir, report)
        from insights_ui import markdown_append
        report = markdown_append(report, context)
        report_path.write_text(report, encoding="utf-8")
        html_path.write_text(markdown_to_html(report, f"A股每日量化与技术分析报告｜{data_date}", context), encoding="utf-8")
        shutil.copy2(report_path, paths.root / "latest.md")
        # Resolve charts/downloads relative to the dated report when opened via latest.
        latest_html = html_path.read_text(encoding="utf-8").replace('<head>', f'<head><base href="daily/{data_date}/">', 1)
        (paths.root / "latest.html").write_text(latest_html, encoding="utf-8")

        browser_opened, browser_message = (False, "未请求打开浏览器")
        if args.open_browser:
            browser_opened, browser_message = open_html_in_browser(html_path)

        log("[7/7] 完成")
        output = {
            "status": "completed", "data_date": data_date, "strategy_version": config["strategy_version"],
            "report": str(report_path), "html_report": str(html_path), "candidates": str(candidates_path), "fundamentals": str(fundamentals_path),
            "browser_opened": browser_opened, "browser_message": browser_message,
            "signals": str(paths.signals), "equity_curve": str(paths.equity),
            "top": [
                {"rank": int(r["rank"]), "code": r["code"], "name": r["name"], "score": round(float(r["total_score"]), 2)}
                for _, r in picks.iterrows()
            ],
            "warnings": kline_errors[:10],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "data_error", "error": str(exc)}, ensure_ascii=False), file=sys.stdout)
        log(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

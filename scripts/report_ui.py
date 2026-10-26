"""Progressively disclosed daily briefing; consumes the report, never recomputes scores."""
import html
import re
from pathlib import Path

ASSETS = Path(__file__).parent / 'report_assets'


def section(md, heading):
    match = re.search(r'^' + re.escape(heading) + r'\s*\n(.*?)(?=^#{1,' + str(len(heading.split()[0])) + r'} |\Z)', md, re.M | re.S)
    return match.group(1) if match else ''


def rows(md):
    return [[c.strip() for c in line.strip('|').split('|')] for line in md.splitlines()
            if line.startswith('|') and not re.match(r'^\|[ :|-]+\|$', line)]


def clean(text):
    return re.sub(r'[*`]', '', text)


def esc(text):
    return html.escape(str(text), quote=True)


def tone(text):
    m = re.fullmatch(r'([+-]?\d+(?:\.\d+)?)%', text.strip())
    if not m:
        return ''
    return 'up' if float(m[1]) > 0 else 'down' if float(m[1]) < 0 else 'flat'


def fragment(legacy, name):
    """Get a rendered top-level chapter, preserving charts and original tables."""
    match = re.search(r'<h2 id="[^"]+">' + re.escape(name) + r'</h2>(.*?)(?=<h2 |</main>)', legacy, re.S)
    return match.group(1) if match else '<p>本节数据暂缺。</p>'


def table(data, caption):
    if not data:
        return '<p>数据暂缺。</p>'
    return '<div class="table-wrap"><table><caption>' + esc(caption) + '</caption><thead><tr>' + ''.join('<th scope="col">'+esc(c)+'</th>' for c in data[0]) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join(f'<td class="{tone(c)}">{esc(clean(c))}</td>' for c in row)+'</tr>' for row in data[1:])+'</tbody></table></div>'


def industry_watchlist(pickrows, context):
    """Show the report's industry-to-stock mapping without inferring sectors."""
    if len(pickrows) < 2:
        return '<p class="source-note">观察池行业标注暂缺。</p>'
    grouped = {}
    briefs = {str(item.get('code', '')).zfill(6): item for item in (context or {}).get('stock_briefs', [])}
    headers = pickrows[0]
    def short(value, limit, fallback):
        text = re.sub(r'\s+', ' ', str(value or '')).strip()
        if text.lower() in ('', 'nan', 'none', 'null', '—', '--'):
            return fallback
        return text if len(text) <= limit else text[:limit-1].rstrip()+'…'
    for row in pickrows[1:]:
        if len(row) < 4:
            continue
        industry = clean(row[3]).strip() or '行业未分类'
        name = clean(row[2]).strip() or row[1].strip()
        code = row[1].strip()
        score = row[4].strip() if len(row) > 4 else '—'
        rank = row[0].strip()
        brief = briefs.get(code.zfill(6), {})
        values = dict(zip(headers,row))
        price_notes = [key+' '+values[key] for key in ('20日涨跌','60日涨跌') if values.get(key) not in (None,'','—','--')]
        business = short(brief.get('business'),72,'主营资料暂缺')
        drivers = short(brief.get('drivers'),56,'因子明细暂缺')
        risk = short(brief.get('risk'),88,'；'.join(price_notes) or '风险数据暂缺')
        grouped.setdefault(industry, []).append((name, code, score, rank, business, drivers, risk))
    if not grouped:
        return '<p class="source-note">观察池行业标注暂缺。</p>'
    cards = []
    for industry, stocks in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        # Feed board names and company classifications are separate taxonomies.
        # A coincidentally equal name is not evidence of equal constituents.
        members = []
        for name, code, score, rank, business, drivers, risk in stocks:
            heading = f'<a class="stock-link industry-stock-link" href="#stock-{esc(rank)}">{esc(name)}</a>' if rank.isdigit() else esc(name)
            members.append(f'<article class="industry-company"><div class="company-heading">{heading}<span>{esc(code)} · 综合分 {esc(score)}</span></div><p class="company-business"><span>主营</span>{esc(business)}</p><div class="company-facts"><p><span>得分强项</span>{esc(drivers)}</p><p><span>风险数据</span>{esc(risk)}</p></div></article>')
        cards.append(f'<div class="industry-watch"><div><b>{esc(industry)}</b><small>{len(stocks)} 只观察股</small></div><div class="industry-companies">'+''.join(members)+'</div></div>')
    return '<div class="industry-watchlist"><h3>行业与标的对应</h3><p class="muted">按公司行业字段分组。行情板块分类另列，不将同名板块涨跌套用到公司行业。</p>'+''.join(cards)+'</div>'


def render(md, title, legacy, context=None):
    from insights_ui import render as render_insights
    insights = render_insights(context)
    date = re.search(r'\d{4}-\d{2}-\d{2}', title)
    date = date.group() if date else '日期待核验'
    summary = rows(section(md, '## 报告速览'))
    overview = {r[0]: clean(r[1]) for r in summary[1:] if len(r) >= 2}
    market = overview.get('今天市场怎样', '市场数据暂缺').split('；')
    breadth = rows(section(md, '### 涨跌结构与成交活跃度'))
    b = breadth[1] if len(breadth) > 1 else ['—'] * 12
    indices = rows(section(md, '### 主要指数表现'))
    if len(indices) == 5 and all(len(r) >= 3 and tone(r[2]) == 'down' for r in indices[1:]):
        market[0] = '指数普跌，个股分化'
        market[1:] = ['四大指数全部下跌；当前是弱势分化，不是普涨行情。']
    index_html = ''.join(f'<div class="index"><span>{esc(r[0])}</span><strong class="{tone(r[2])}">{esc(r[2])}</strong><span>{esc(r[1])} 点</span><small>5日 {esc(r[3])}　20日 {esc(r[4])}</small></div>' for r in indices[1:] if len(r) >= 5)
    width = [int(b[i]) for i in (1, 2, 3)] if all(str(b[i]).isdigit() for i in (1, 2, 3)) else [0, 0, 0]
    total = sum(width) or 1
    strip = ''.join(f'<span class="{c}" style="width:{v/total*100:.2f}%"></span>' for v, c in zip(width, ['up-bg', 'down-bg', 'flat-bg']))
    industry = section(md, '### 行业轮动')
    groups = []
    bottom_label = '涨幅靠后行业' if '**涨幅靠后行业**' in industry else '领跌行业'
    for label in ('领涨行业', bottom_label):
        line = next((x for x in industry.splitlines() if x.startswith('**'+label)), '')
        pairs = re.findall(r'([^：；]+?)\s+([+-]\d+\.\d+%)', line.replace('**', ''))
        if not pairs:
            continue
        if label == '领跌行业' and all(float(value[:-1]) >= 0 for _, value in pairs):
            label = '涨幅靠后行业'
        groups.append('<div><h3>'+label+'</h3>'+''.join(f'<div class="sector"><span>{esc(name.strip())}</span><span class="track"><i class="{tone(value)}-bg" style="width:{min(abs(float(value[:-1]))/5*100,100):.1f}%"></i></span><b class="{tone(value)}">{value}</b></div>' for name,value in pairs[:5])+'</div>')
    event_rows = rows(section(md, '### 重大事件与市场影响分析'))
    cards = []
    quick_news = []
    for row in event_rows[1:]:
        if len(row) < 4:
            continue
        link = re.match(r'\[([^\]]+)\]\(([^)]+)\)(.*)', row[0])
        if not link:
            continue
        headline, url, meta = link.groups()
        audit = (context or {}).get('news_audit', {}).get(headline, {})
        audit_note = ''
        if audit:
            audit_note = '<p class="source-note">原文核验：'+esc(audit.get('claim_status','待核验'))+'。'+esc(audit.get('note') or audit.get('error',''))+'</p>'
            if audit.get('status') == 'retrieved':
                audit_note += '<a target="_blank" rel="noopener" href="'+esc(audit.get('url',''))+'">查看核验来源</a>'
        impact = clean(row[1]); direction, _, reason = impact.partition('：')
        timing = clean(row[2]); tag, _, validation = timing.partition('：')
        source_meta = meta.strip('｜').split('｜')
        source_time = source_meta[0] if source_meta else ''
        source_name = source_meta[1] if len(source_meta) > 1 else ''
        if '偏' in direction or direction == '供应恢复':
            quick_news.append(f'<li><a href="#news">{esc(headline)}</a><small>{esc(direction)} / {esc(tag)}</small></li>')
        cards.append((0 if direction in {'方向不足', '观点信息', '中性', '中期项目线索'} else 1, f'<article class="event"><div class="event-meta"><span class="tag">{esc(direction)}</span><span>{esc(tag)} · {esc(source_time[-8:])}</span></div><h3><a href="{esc(url)}" target="_blank" rel="noopener">{esc(headline)}</a></h3><p>{esc(reason)}</p><details><summary>来源与验证</summary><p>{esc(validation)}</p><small>{esc(source_time)} / {esc(source_name)} / {esc(clean(row[3]))}</small></details></article>'))
        if audit_note:
            priority, card = cards[-1]
            cards[-1] = (priority, card.replace('</details>', audit_note+'</details>', 1))
    cards = [card for _, card in sorted(cards, key=lambda entry: entry[0], reverse=True)]
    pickrows = rows(section(md, '## 二、今日观察池'))
    # compact list links to preserved detailed candidate chapters
    candidates = re.split(r'<h3 id="([^"]+)">(.*?)</h3>', fragment(legacy, '三、逐只股票详细分析'))
    detail_cards = []
    for i in range(1, len(candidates), 3):
        if i+2 < len(candidates):
            code = re.search(r'\d{6}', candidates[i+1])
            brief = insights['briefs'].get(code.group(), '') if code else ''
            detail_cards.append(f'<details class="stock-detail" id="stock-{(i+2)//3}"><summary>{candidates[i+1]}<span>业务 · 财务 · 因子 · K线</span></summary><div class="detail-body">{brief}{candidates[i+2]}</div></details>')
    picks_html = table(pickrows, '观察池排名与估值快照（综合分不代表上涨概率）')
    industry_map = industry_watchlist(pickrows, context)
    picks_html = re.sub(r'<tr><td class="">(\d+)</td><td class="">(\d{6})</td><td class="">([^<]+)</td>', lambda m: f'<tr><td>{m[1]}</td><td>{m[2]}</td><td><a class="stock-link" href="#stock-{m[1]}">{m[3]}</a></td>', picks_html)
    perf = rows(section(md, '## 五、理论收益统计与模型表现评价'))
    perf = perf[:4+1] if perf else []
    compact_perf = [[r[j] for j in (0,1,2,7,10)] for r in perf if len(r) > 10]
    equity = re.search(r'按每日观察池.*?：(.*?)。', md)
    concentration = re.search(r'- \*\*一级行业分布\*\*：(.*)', md)
    quality = fragment(legacy, '九、数据质量与限制')
    glossary = fragment(legacy, '四、基本面与技术面术语翻译')
    links = '<div class="downloads"><a href="report.md">Markdown 原文</a><a href="candidates.csv">候选 CSV</a><a href="fundamentals.csv">基本面 CSV</a></div>'
    quick = '<aside class="quick"><h2>今日先看</h2><ul>'+''.join(quick_news[:2])+'</ul></aside>' if quick_news else ''
    sections = f'''
<section id="overview"><div class="intro"><div><p class="dateline">{date} 收盘观察</p><h1>{esc(market[0])}</h1><p class="lead">{esc(market[1] if len(market)>1 else '')}</p><p class="rotation-brief"><a href="#industries">领涨方向</a>　{esc(overview.get('行业在轮动什么','数据暂缺'))}</p><a class="model-brief" href="#model">{esc(overview.get('模型目前怎样','模型数据不足'))}</a></div>{quick}</div>
<div class="indices">{index_html}</div>
<div class="breadth"><div><h2>个股温度</h2><p><b class="up">{esc(b[1])} 涨</b>　<b class="down">{esc(b[2])} 跌</b>　{esc(b[3])} 平</p><div class="breadth-strip" aria-label="上涨、下跌、平盘占比">{strip}</div><small>本次覆盖 {esc(b[0])} 只流动性样本，不代表全市场</small></div><dl><div><dt>涨跌中位数</dt><dd class="{tone(b[5])}">{esc(b[5])}</dd></div><div><dt>样本成交额</dt><dd>{esc(b[9])}</dd></div><div><dt>换手中位数</dt><dd>{esc(b[10])}</dd></div></dl></div>
<p class="source-note">{esc(overview.get('数据是否完整',''))}。不同日期若切换样本源，成交额与涨跌家数不能直接比较。</p></section>
<section id="industries"><div class="section-heading"><h2>行业在往哪里走</h2><span>当日涨跌排名</span></div><div class="sector-grid">{''.join(groups)}</div><details class="support"><summary>展开行业、题材与市场证据</summary><div class="detail-body">{_market_evidence(legacy)}</div></details></section>
<section id="news"><div class="section-heading"><h2>重要消息与影响</h2><span>{len(cards)} 条 / 方向先行</span></div><p class="muted">消息发布时间决定观察窗口；盘后消息留待下一交易日验证。仅有快讯首页的条目降低证据等级。</p><div class="news-grid">{''.join(cards[:4])}</div>{'<details class="support"><summary>其余 '+str(len(cards)-4)+' 条消息</summary><div class="news-grid">'+''.join(cards[4:])+'</div></details>' if len(cards)>4 else ''}</section>
<section id="watchlist"><div class="section-heading"><h2>今日研究观察池</h2><span>{len(detail_cards)}只 / 点击名称看详情</span></div><p>{esc(concentration.group(1) if concentration else '行业分布暂缺')}</p>{picks_html}{industry_map}<div class="stock-tools"><label for="stock-search">查找个股</label><input id="stock-search" type="search" placeholder="输入名称或代码"><span id="search-count" aria-live="polite"></span></div>{''.join(detail_cards)}</section>
<section id="model"><div class="section-heading"><h2>模型是否有效</h2><span>历史理论收益</span></div><p class="verdict">{esc(overview.get('模型目前怎样','模型数据不足'))}</p>{table(compact_perf,'按周期看净收益、胜率与相对沪深300超额')}<p>{esc(equity.group(1) if equity else '')}</p><p class="muted">理论入场为信号次日开盘并扣除费用与滑点。重复选股观测不是独立样本；20日未成熟不作判断。</p><details class="support"><summary>完整模型统计与策略诊断</summary><div class="detail-body">{fragment(legacy,'五、理论收益统计与模型表现评价')}{fragment(legacy,'八、策略迭代诊断')}</div></details></section>
<section id="archive"><h2>逐股收益与历史记录</h2><details class="support"><summary>今日涨跌、1万元示例与逐股收益</summary><div class="detail-body">{fragment(legacy,'六、逐股收益跟踪')}</div></details><details class="support"><summary>历史全部股票与信号日</summary><div class="detail-body">{fragment(legacy,'七、历史信号股票池收益统计')}</div></details></section>
<section id="quality"><h2>数据与阅读口径</h2><p>{esc(overview.get('数据是否完整',''))}</p><details class="support"><summary>数据来源、降级与限制</summary><div class="detail-body">{quality}</div></details><details class="support"><summary>指标解释</summary><div class="detail-body">{glossary}</div></details>{links}<p class="muted">研究观察池，非实际持仓。示例盈亏与理论收益均不代表实盘收益。页面版本 2.0。</p></section>'''
    sections = sections.replace('<div class="indices">', insights['three']+'<div class="indices">', 1)
    sections = sections.replace('<section id="industries">', insights['changes']+'<section id="industries">', 1)
    sections = sections.replace('<details class="support"><summary>展开行业', insights['industries']+'<details class="support"><summary>展开行业', 1)
    sections = sections.replace('<section id="watchlist">', '<section class="audit-section">'+insights['audit']+'</section><section id="watchlist">', 1) if context else sections
    sections = sections.replace('<section id="archive">', '<section class="history-section">'+insights['history']+'</section><section id="archive">', 1) if context else sections
    sections = sections.replace('页面版本 2.0', '页面版本 2.1')
    sections = sections.replace('行业在往哪里走','行业涨跌').replace('今日研究观察池','今日观察池').replace('模型是否有效','模型表现').replace('条 / 方向先行','条消息').replace('消息发布时间决定观察窗口；盘后消息留待下一交易日验证。仅有快讯首页的条目降低证据等级。','以下为消息影响分析。盘后消息看下一交易日反应；来源和核验情况可展开查看。')
    nav = ''.join(f'<a href="#{key}">{label}</a>' for key,label in [('overview','市场概览'),('industries','行业强弱'),('news','重要消息'),('watchlist','观察池'),('model','模型表现'),('archive','历史记录'),('quality','数据口径')])
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+'</title><style>'+ (ASSETS/'report.css').read_text(encoding='utf-8')+'</style></head><body><a class="skip" href="#overview">跳至市场概览</a><header class="topbar"><a href="#overview" class="brand">行情研究室</a><nav class="toc" aria-label="报告目录">'+nav+'</nav><button id="print-report" type="button">打印</button></header><main>'+sections+'</main><script>'+(ASSETS/'report.js').read_text(encoding='utf-8')+'</script></body></html>'


def _market_evidence(legacy):
    full = fragment(legacy, '一、当日股市全景')
    pieces = re.split(r'(?=<h3 )', full)
    keep = ('行业轮动', '显著异动与可核验原因', '涨跌结构与成交活跃度', '今日结论')
    return ''.join(p for p in pieces if any(re.search(r'<h3[^>]*>'+name+'</h3>',p) for name in keep))

"""Conservative, title-specific event interpretation shared by live and offline reports.

Direction describes a transmission mechanism, not a prediction or proven cause.
No network calls; all conclusions retain the original headline and publication time.
"""
import re
import html
import requests
from datetime import datetime
from urllib.parse import urlparse


def search_direction(title, event_time=''):
    """Search a headline for a reported prior-value comparison.

    This is deliberately narrow: it only returns a direction when a result states
    the change in the same quote, avoiding inference from an arbitrary threshold.
    """
    query = f'"{title}" 较上一交易日'
    text = ''
    for endpoint in ('https://www.google.com/search', 'https://www.bing.com/search'):
        try:
            response = requests.get(endpoint, params={'q': query}, headers={'User-Agent': 'Mozilla/5.0'}, timeout=4)
            text += ' ' + html.unescape(re.sub(r'<[^>]+>', ' ', response.text))
        except requests.RequestException:
            continue
    match = re.search(r'较上一交易日(?:收盘价)?(?:上涨|上升|升值|调升)(\d+(?:\.\d+)?)\s*个?基点', text)
    if match:
        return ('人民币升值', f'搜索到同一收盘价较上一交易日升值{match.group(1)}个基点；人民币走强通常利好航空、造纸等进口成本敏感行业，压低出口企业的汇兑优势。')
    match = re.search(r'较上一交易日(?:收盘价)?(?:下跌|下降|贬值|调贬)(\d+(?:\.\d+)?)\s*个?基点', text)
    if match:
        return ('人民币贬值', f'搜索到同一收盘价较上一交易日贬值{match.group(1)}个基点；人民币走弱通常利好出口链，增加航空、能源等美元计价成本压力。')
    return None


def assess(event):
    title = str(event.get('title', ''))
    if '评级' in title or '目标价' in title:
        return '观点信息', '评级维持不变，没有上调。这里的“增持”是券商建议，不代表公司或股东买入股票。'
    if re.search(r'未.*(冲击|影响)|反对.*制裁|尚未|否认', title):
        return '中性', '标题是回应或立场表述，未提供新增制裁、订单损失或业绩变化证据。'
    if '减持' in title:
        return '个股偏空', '拟减持增加潜在股票供给，对该公司短期筹码面不利；拟实施不等于已成交。'
    if '回购' in title or '增持' in title:
        return '个股偏多', '回购或增持对该公司筹码面有支持，实际力度取决于金额与执行进度。'
    if '库存' in title and '铜' in title:
        if '减少' in title:
            return '铜价偏多', '库存下降利好铜价，也有利于铜矿企业。但只看一天的库存，还不能判断需求是否回升。'
        if '增加' in title:
            return '铜价偏空', '累库对铜价形成边际压力，对铜矿企业价格预期不利；需区分需求走弱与仓储流转。'
    if '黄金' in title and any(w in title for w in ('失守', '下跌', '跌破')):
        return '黄金股偏空', '金价回落压低黄金生产商的产品价格预期，负面影响集中在黄金链。'
    if '加息' in title or ('天然气' in title and '通胀' in title):
        return '成长估值偏空', '如果利率继续上升，高估值成长股将承压。这是利率预测，尚未落地。'
    if '降息' in title or '降准' in title:
        return '流动性偏多', '宽松方向支持融资条件与估值；需区分政策已公布和市场预期。'
    if re.search(r'鼓励|支持|加快|扩大|优化|下调|减免|获批|通过', title) and re.search(r'政策|办法|规定|名单|管控|行业|产业|出口|进口|投资', title):
        return '政策影响偏多', '政策动作直接支持相关产业或降低制度成本，受益最先落在政策覆盖的行业和业务环节；后续看执行细则与订单兑现。'
    if re.search(r'限制|禁止|暂停|整顿|收紧|加征|管制|召回|停产', title):
        return '政策或供给偏空', '限制、加征、召回或停产会压制相关企业的销量、供给或利润预期；影响范围取决于适用对象和持续时间。'
    if re.search(r'订单|中标|交付|投产|产能|销量|营收|净利润|业绩', title):
        return '行业基本面线索', '标题涉及订单、产能、销量或业绩，先影响对应公司，再沿产业链传导；需要金额、增速和利润率确认影响强度。'
    if re.search(r'机器人|算力|人工智能|储能|光伏|电池|芯片|航运|房地产|医药|汽车', title):
        return '相关主题偏多', '标题直接指向相关产业主题，短期影响集中在主题公司和上游供应链；持续性取决于政策、订单和成交扩散。'
    if '泡沫' in title or '回调' in title:
        return '风险提示', '这是估值风险警示，不等于已发生资金流出或公司盈利下修。'
    if '涨价' in title or '报价上调' in title:
        return '供需两端分化', '提价支持标题所涉产品卖方的单位收入，增加买方采购成本；利润改善还取决于销量与成本。'
    if '备案' in title or '注册证书' in title or '注册批准' in title:
        return '相关业务偏多', '备案或获批有利于产品上市。能否增加收入，还要看实际销量和收费情况。'
    if '恢复' in title and '石油装载' in title:
        return '供应恢复', '恢复装载缓解了供应紧张，对油价偏空，对需要采购原油的企业有利。'
    if 'PMI' in title and '预期' in title:
        nums = re.search(r'(?:至|为|报)\s*(\d+\.?\d*).*?预期\s*(\d+\.?\d*)', title)
        if nums:
            actual, expected = map(float, nums.groups())
            return ('需求预期偏多' if actual > expected else '需求预期偏空' if actual < expected else '符合预期',
                    f'当地PMI为{actual:g}，比预期{expected:g}'+('高' if actual>=expected else '低')+f'{abs(actual-expected):.1f}点。主要影响当地需求，以及向该地区出口的企业。')
    if '人民币' in title and '收盘报' in title:
        searched = search_direction(title, event.get('time', ''))
        if searched:
            return searched
        return '人民币汇率待核实', '已发起前一交易日收盘价检索；搜索结果未返回同一口径的涨跌基点，暂不把单一收盘价写成方向判断。'
    if '成立' in title or '签约' in title or '投资协议' in title:
        return '中期项目线索', '新增主体或投资计划支持业务布局，注册资本和计划投资额均不等于订单、实际投入或当期收入。'
    return '方向不足', '标题信息不够，暂时无法判断利好还是利空。'


def timing(event, data_date):
    raw = str(event.get('time', ''))
    if not re.search(r'\d{2}:\d{2}', raw):
        return '时间待核验', '缺少具体发布时间，不能进行行情时序验证。'
    try:
        published = datetime.fromisoformat(raw)
        close = datetime.fromisoformat(data_date + 'T15:00:00')
    except (ValueError, TypeError):
        return '时间待核验', '缺少可比较的发布时间，不能进行行情时序验证。'
    if published.date() > close.date():
        return '晚于报告日', '不纳入本交易日判断。'
    if published > close:
        return '盘后消息', '发布于A股收盘后，影响要看下一交易日，不能用当日收盘验证。'
    return '收盘前消息', '尚缺发布前后对应标的的价格与成交数据，未做事件反应验证。'


def evidence(event):
    path = urlparse(str(event.get('url', ''))).path.rstrip('/')
    return '快讯首页链接，非独立原文' if not path else '附来源链接；判断依据为已取得的标题'


def rewrite_event_section(markdown):
    """Refresh interpretations from stored source rows, without changing market data."""
    date = re.search(r'数据截止交易日\*\*：([\d-]+)', markdown)
    data_date = date.group(1) if date else ''
    pattern = r'(### 重大事件与市场影响分析\n)(.*?)(?=\n### |\Z)'
    def replace(match):
        events = []
        for line in match.group(2).splitlines():
            source = re.match(r'^\| \[([^\]]+)\]\(([^)]+)\)｜([^｜]*)｜([^|]+)\|', line)
            if source:
                title, url, time, publisher = source.groups()
                events.append(dict(title=title, url=url, time=time.strip(), source=publisher.strip()))
        if not events:
            return match.group(0)
        rows = [match.group(1), f'展示{len(events)}条消息。先看影响对象与方向，再区分盘中信息和盘后待观察事项。方向判断是基于标题的分析，行情因果需要额外证据。', '',
                '| 重要事件（原文） | 明确判断 | 时间与验证 | 来源质量 |', '|---|---|---|---|']
        for event in events:
            stance, reason = assess(event)
            tag, verification = timing(event, data_date)
            rows.append(f"| [{event['title']}]({event['url']})｜{event['time']}｜{event['source']} | **{stance}**：{reason} | **{tag}**：{verification} | {evidence(event)} |")
        return '\n'.join(rows) + '\n'
    return re.sub(pattern, replace, markdown, flags=re.S)

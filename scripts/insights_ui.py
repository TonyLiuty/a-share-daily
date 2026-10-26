"""Small read-only views over analysis_context.json."""
from html import escape as e


def pct(value):return '历史不足' if value is None else f'{value:+.2f}%'


def grid(headers, rows):
    return '<div class="table-wrap"><table><thead><tr>'+''.join('<th>'+e(str(x))+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+e(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table></div>'


def takeaways(context):
    market=context.get('market_returns',{})
    if len(market)==4 and all(v<0 for v in market.values()):
        first=f"四大指数全跌，上证{pct(market['上证指数'])}、创业板{pct(market['创业板指'])}，大盘偏弱。"
    else:
        first='，'.join(name+pct(value) for name,value in market.items()) or '指数数据暂缺。'
    reversals=[r for r in context['industries']['rows'] if r['state'] in ('由涨转跌','由跌转涨')]
    if reversals:
        second='；'.join(f"{r['name']}{r['state']}，今日{pct(r['ret_1d'])}" for r in reversals[:2])+'。'
    else:
        leaders=sorted((r for r in context['industries']['rows'] if r['ret_1d'] is not None),key=lambda r:r['ret_1d'],reverse=True)
        second=('行业表现：'+'，'.join(r['name']+pct(r['ret_1d']) for r in leaders[:3])+'。') if leaders else '行业数据暂缺。'
    return [first,second]


def render(context):
    if not context:return {'three':'','changes':'','industries':'','history':'','briefs':{},'audit':''}
    changes=context['changes']; watch=changes['watchlist']; sectors=context['industries']
    index=changes['indices']
    reversals=[r for r in sectors['rows'] if r['state'] in ('由涨转跌','由跌转涨')]
    industry_text='；'.join(f"{r['name']}{r['state']}（{pct(r['previous'])} → {pct(r['ret_1d'])}）" for r in reversals[:3]) or '没有足够的同源历史行业数据确认轮动方向。'
    audit=context.get('news_audit',{})
    selection=context.get('selection_audit',{})
    if selection:
        chosen=selection['selected']
        issues=[]
        if chosen.get('unknown_share'):
            issues.append(f"选股时{chosen['unknown_share']:.0%}的股票缺少行业，行业上限无法核实")
        if chosen.get('primary_top_share',0)>chosen.get('max_primary_share',1):
            issues.append(f"单一申万一级行业占{chosen['primary_top_share']:.0%}，超过研究警戒值{chosen['max_primary_share']:.0%}")
        if chosen.get('cap_violations'):
            issues.append('选股行业数量超过配置上限')
        note='；'.join(issues) or '已列出行业分布；分类体系与版本仍需补齐。'
        quality_html='<p class="source-note">'+e(note)+'</p><details class="support"><summary>选股数据与行业约束审计</summary><p>'+e('已保存预选池诊断。' if selection.get('input_available') else '该报告未保存预选池，无法重建预选因子有效率。')+'</p><p>审计仅检查已有记录；严格模式在缺少可靠分类时停止生成新信号。</p></details>'
    else:
        quality_html=''
    three='<div class="three-things"><h2>今日要点</h2><ol>'+''.join('<li>'+e(x)+'</li>' for x in takeaways(context))+'</ol></div>'
    change_html='<section id="changes"><div class="section-heading"><h2>较上次发生了什么</h2><span>对比 '+e(context.get('previous_date') or '暂无')+'</span></div><p>'+e('；'.join(index))+'</p><p><b>新增观察</b> '+e('、'.join(watch['added']) or '无')+'</p><p><b>退出观察</b> '+e('、'.join(watch['removed']) or '无')+f"；保留{watch['retained']}只。</p><p class=\"muted\">新增/退出只表示规则排名变化，不能等同买入/卖出。</p>"
    panel=context.get('panel')
    if panel:
        cc=panel['comparable_current'];pp=panel['comparable_previous']
        change_html+=f"<p>固定研究样本覆盖 {panel['coverage_count']}/{panel['reference_count']}（{panel['coverage_pct']:.1f}%）；两日共同覆盖{panel['common_count']}只。</p>"
        change_html+=grid(['同一批股票','上涨占比','涨跌中位数'],[['上一报告日',pct(pp['up_pct']),pct(pp['median_pct'])],['本报告日',pct(cc['up_pct']),pct(cc['median_pct'])]])
    else:
        change_html+='<p class="source-note">固定样本比较待积累：旧报告未保留完整行情快照，不能用十只候选代替全体样本。下一次成功取数开始固定成员并逐日记录覆盖率。</p>'
    change_html+='<details class="support"><summary>查看两日样本口径</summary><p>本次：'+e(context['source'])+'</p><p>上次：'+e(context['previous_source'])+'</p><p>'+e(context['comparability_note'])+'</p></details></section>'
    sector_rows=[[r['name'],pct(r['previous']),pct(r['ret_1d']),pct(r['ret_5d']),pct(r['ret_20d']),pct(r['breadth']),pct(r['amount_share']),r['state']] for r in sectors['rows']]
    ind=quality_html+'<p class="rotation-brief">'+e(industry_text)+'</p><details class="support"><summary>行业持续性：1 / 5 / 20日与扩散程度</summary>'+grid(['行业','上一报告日','当日','5日','20日','上涨占比','成交占比','状态'],sector_rows)+'<p class="muted">'+e(sectors['note'])+' “历史不足”也包括源接口未提供的行业涨跌家数和成交额。</p></details>'
    hist=context['history']; lines=[]
    for stock in hist['stocks']:
        row=[stock['name'],str(stock['prior_signals'])+'次']
        for h in ('1','5','20'):
            observation=stock['horizons'][h]
            row.append((pct(observation['return']*100)+' / '+observation['signal_date']) if observation else '尚无成熟历史')
        lines.append(row)
    history='<details class="support"><summary>今日新信号与此前已成熟信号分开看</summary><p>今天新生成的信号尚未建仓。下表展示此前已成熟记录，收益后附原始信号日期。</p>'+grid(['今日观察股','此前入选','1日净收益 / 信号日','5日净收益 / 信号日','20日净收益 / 信号日'],lines)+'<p class="muted">'+e(hist['note'])+'</p>'+grid(['周期','成熟观测','不同股票','信号日'],[[h+'日',v['observations'],v['unique_stocks'],v['signal_days']] for h,v in hist['counts'].items()])+'</details>'
    if not hist['available']:history='<p>该日期未保存收益快照，不能使用更新后的历史收益回填。</p>'
    briefs={r['code']:'<div class="stock-brief"><h4>这家公司先看什么</h4><p><b>主营</b> '+e(r['business'])+'</p><p><b>入选支撑</b> '+e(r['drivers'])+'</p><p><b>财务核对</b> '+e(r['financial'])+'</p><p><b>风险与观察条件</b> '+e(r['risk']+r['condition'])+'</p></div>' for r in context['stock_briefs']}
    audits=[]
    for title,a in audit.items():
        status={'retrieved':'已读匹配详情','needs_review':'页面匹配待审','unavailable':'未取得正文'}.get(a.get('status'),'未核验')
        audits.append([title,status,a.get('claim_status','待核验'),a.get('error') or a.get('note',''),a.get('retrieved_at','')])
    audit_html='<details class="support"><summary>消息原文读取与核验记录</summary>'+grid(['消息','读取状态','事实核验','限制','核验时间'],audits)+'<p>来源读取成功不等于事实核验通过；官方预测也不是已发生事实。新闻事件发生时间未提供时保留未知。</p></details>'
    change_html=change_html.replace('较上次发生了什么','与上一报告日相比').replace('新增/退出只表示规则排名变化，不能等同买入/卖出。','名单按评分更新，不是交易记录。').replace('固定样本比较待积累：旧报告未保留完整行情快照，不能用十只候选代替全体样本。下一次成功取数开始固定成员并逐日记录覆盖率。','旧报告未保存完整行情，暂时无法比较同一批股票的表现。后续日报将保留快照。')
    ind=ind.replace('行业持续性：1 / 5 / 20日与扩散程度','查看行业1 / 5 / 20日涨跌幅')
    history=history.replace('今日新信号与此前已成熟信号分开看','查看这些股票此前入选后的收益').replace('今天新生成的信号尚未建仓。下表展示此前已成熟记录，收益后附原始信号日期。','今日信号从下一交易日开盘起计算理论收益。下表列出此前已走完观察期的记录。')
    briefs={code:text.replace('这家公司先看什么','公司简析').replace('入选支撑','得分强项').replace('财务核对','盈利与现金流') for code,text in briefs.items()}
    return {'three':three,'changes':change_html,'industries':ind,'history':history,'briefs':briefs,'audit':audit_html}


def markdown_append(md, context):
    import re
    md=re.sub(r'\n## 十、连续跟踪与证据复核\n.*\Z','',md,flags=re.S)
    md=md.replace('**历史信号收益**：最近信号日为','**最新信号收益（不代表全部历史）**：最近信号日为')
    md=md.replace('今日观察池暂无可评价的成熟历史收益；需要等1/5/20日周期陆续成熟后再判断。','今日最新信号尚未成熟；此前已成熟记录另见“连续跟踪与证据复核”，不能据此认定这些股票没有历史收益。')
    md=md.replace('只股票暂无成熟历史收益周期，主要因为最近信号尚未走完','只股票的最新信号暂无成熟收益周期，主要因为最近信号尚未走完')
    lines=['','## 十、连续跟踪与证据复核','',f"比较基准：{context.get('previous_date') or '无前次报告'}。",'']
    lines.extend('- '+line for line in context['changes']['indices'])
    w=context['changes']['watchlist']
    lines+=['','新增观察：'+('、'.join(w['added']) or '无'),'','退出观察：'+('、'.join(w['removed']) or '无'),'',context['comparability_note'],'']
    lines+=['### 今日观察股此前成熟信号','','| 股票 | 1日收益 / 信号日 | 5日收益 / 信号日 | 20日收益 / 信号日 |','|---|---|---|---|']
    for stock in context['history']['stocks']:
        cells=[stock['name']]
        for h in ('1','5','20'):
            obs=stock['horizons'][h];cells.append(pct(obs['return']*100)+' / '+obs['signal_date'] if obs else '尚无成熟历史')
        lines.append('| '+' | '.join(cells)+' |')
    lines+=['',context['history']['note'],'','### 行业持续跟踪','','| 行业 | 当日 | 5日 | 20日 | 状态 |','|---|---|---|---|---|']
    lines+=['| '+' | '.join([r['name'],pct(r['ret_1d']),pct(r['ret_5d']),pct(r['ret_20d']),r['state']])+' |' for r in context['industries']['rows']]
    lines+=['',context['industries']['note'],'','### 新闻核验记录','']
    for title,a in context.get('news_audit',{}).items():
        lines.append(f"- {title}：{a.get('claim_status','待核验')}；{a.get('note') or a.get('error','正文未取得')}；[核验来源]({a.get('url','')})。")
    return md.rstrip()+'\n'+'\n'.join(lines)+'\n'

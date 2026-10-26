"""As-of report context. Descriptive analytics only; never changes selection or weights."""
import csv
import json
import math
import re
from pathlib import Path
from statistics import median


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError): return None


def csv_rows(path):
    if not path.exists(): return []
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def json_read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def provider(md):
    match = re.search(r'本节统计范围为“([^”]+)', md)
    return match.group(1) if match else '未知'


def industries(md):
    """Legacy recovery is explicitly partial: absence is not a zero return."""
    found = {}
    for label in ('领涨行业','领跌行业'):
        line = next((x for x in md.splitlines() if x.startswith('**'+label+'**')), '')
        for name, pct in re.findall(r'([^：；]+?)\s+([+-]\d+\.\d+)%', line.replace('**','')):
            found[name.strip()] = {'name':name.strip(),'change_pct':float(pct)}
    source = 'sina' if '已降级新浪行业' in md else 'eastmoney'
    return {'source':source,'complete':False,'rows':list(found.values())}


def index_rows(md):
    result = {}
    for line in md.splitlines():
        cells = [c.strip() for c in line.strip('|').split('|')]
        if len(cells)==5 and cells[0] in ('上证指数','深证成指','创业板指','沪深300'):
            value = number(cells[2].strip('%'))
            if value is not None: result[cells[0]] = value
    return result


def fixed_panel(current, prior, codes):
    """Pairwise intersection inside a frozen reference cohort; coverage never hidden."""
    c = {str(r['code']).zfill(6):r for r in current}
    p = {str(r['code']).zfill(6):r for r in prior}
    valid = lambda r: number(r.get('change_pct')) is not None
    covered = [code for code in codes if code in c and valid(c[code])]
    common = [code for code in covered if code in p and valid(p[code])]
    def stats(mapping, keys):
        vals = [number(mapping[x]['change_pct']) for x in keys]
        amounts = [number(mapping[x].get('amount')) for x in keys]
        return {'n':len(vals), 'up_pct':sum(v>0 for v in vals)/len(vals)*100 if vals else None,
                'median_pct':median(vals) if vals else None,
                'amount':sum(amounts) if amounts and all(v is not None for v in amounts) else None}
    return {'reference_count':len(codes),'coverage_count':len(covered),'coverage_pct':len(covered)/len(codes)*100 if codes else 0,
            'current':stats(c,covered),'common_count':len(common), 'comparable_current':stats(c,common),'comparable_previous':stats(p,common)}


def capture_inputs(directory, snapshot, industry, signals, market_context, benchmark):
    """Called only after the live pipeline has a valid trading date."""
    directory.mkdir(parents=True,exist_ok=True)
    snapshot.to_csv(directory/'market_snapshot.csv',index=False,encoding='utf-8-sig')
    signals.to_csv(directory/'signals_asof.csv',index=False,encoding='utf-8-sig')
    write_json(directory/'trading_sessions.json', sorted(set(str(x)[:10] for x in benchmark['date'] if str(x)[:10]<=directory.name)))
    # pandas serialization converts NaN to JSON null; source taxonomy is explicit.
    import pandas as pd
    rows = industry.get('rows', [])
    source = 'sina' if any(s in industry.get('source','').lower() for s in ('sina','新浪')) else 'eastmoney'
    write_json(directory/'industry_snapshot.json', {'date':directory.name,'source':source,
               'complete':bool(rows),'rows':json.loads(pd.DataFrame(rows).to_json(orient='records',force_ascii=False))})
    write_json(directory/'news_context.json', market_context)
    cohort_path = directory.parent.parent/'state'/'research_cohort.json'
    if not cohort_path.exists():
        codes = snapshot.drop_duplicates('code').sort_values('amount',ascending=False).head(700)['code'].astype(str).str.zfill(6).tolist()
        write_json(cohort_path, {'created_date':directory.name,'rule':'首个归档日成交额前700，之后固定成员；仅用于市场研究，不进入选股', 'codes':codes})


def stock_brief(row):
    def pct(key):
        value = number(row.get(key))
        return f'{value*100:+.1f}%' if value is not None else '缺失'
    factors = [('动量','momentum_score'),('趋势','trend_score'),('量价','volume_price_score'),('估值','valuation_score'),('风险控制','risk_score')]
    ranked = sorted([(name,number(row.get(key))) for name,key in factors if number(row.get(key)) is not None],key=lambda x:x[1],reverse=True)
    cash = number(row.get('cash_profit_ratio'))
    profit = number(row.get('net_profit'))
    financial = any(w in row.get('sw_industry','') for w in ('银行','保险','证券'))
    quality = f"收入同比{pct('revenue_yoy')}，归母利润同比{pct('net_profit_yoy')}。"
    if financial:
        quality += '金融企业重点看息差/资产质量等专属指标；当前表未提供这些指标。'
    elif profit is not None and profit <= 0:
        quality += '净利润非正，现金利润比不作为盈利质量判断。'
    elif cash is not None:
        quality += f'经营现金流/净利润{cash:.2f}倍，'+('现金回收低于账面利润。' if cash<1 else '现金回收覆盖账面利润。')
    rsi = number(row.get('rsi14')); ma = number(row.get('ma60'))
    risk = f"20日涨幅{pct('ret_20d')}；60日最大回撤{pct('max_drawdown_60d')}。"
    if rsi is not None: risk += f'RSI {rsi:.0f}'+('，短线偏热。' if rsi>=70 else '。')
    return {'code':row['code'],'name':row.get('name',''),'business':row.get('main_business') or '主营资料缺失',
            'drivers':' / '.join(f'{name} {value:.1f}' for name,value in ranked[:2]),'financial':quality,'risk':risk,
            'condition':f'观察MA60 {ma:.2f}元是否守住；跌破后原有趋势依据减弱。' if ma is not None else 'MA60缺失，趋势失效条件不可评价。'}


def mature_history(signals, picks, date, available):
    result = []
    for pick in picks:
        history = [r for r in signals if r.get('code','').zfill(6)==pick['code'].zfill(6) and r.get('signal_date','')<date]
        history.sort(key=lambda r:r['signal_date'],reverse=True)
        item={'code':pick['code'],'name':pick.get('name',''),'prior_signals':len(history),'new_signal_date':date,'horizons':{}}
        for h in (1,5,20):
            match = next((r for r in history if number(r.get(f'ret_{h}d')) is not None), None)
            item['horizons'][str(h)] = {'signal_date':match['signal_date'],'return':number(match[f'ret_{h}d']), 'excess':number(match.get(f'excess_{h}d'))} if match else None
        result.append(item)
    counts={}
    for h in (1,5,20):
        sample=[r for r in signals if r.get('signal_date','')<date and number(r.get(f'ret_{h}d')) is not None]
        counts[str(h)]={'observations':len(sample),'unique_stocks':len({r['code'] for r in sample}),'signal_days':len({r['signal_date'] for r in sample})}
    return {'available':available,'stocks':result,'counts':counts,'note':'每个周期分别取该股此前最近一条已成熟信号，不同周期可能来自不同日期。重复个股/重叠窗口并非独立样本。'}


def industry_history(directory, dates, current, previous):
    today = {r['name']:r for r in current.get('rows',[])}
    old = {r['name']:r for r in previous.get('rows',[])}
    history = [(d,json_read(directory.parent/d/'industry_snapshot.json',industries((directory.parent/d/'report.md').read_text(encoding='utf-8')))) for d in dates[-20:]]
    source = current['source']
    sessions=json_read(directory/'trading_sessions.json',[])
    same = previous.get('source')==source
    rows=[]
    for name,r in sorted(today.items(),key=lambda x:x[1].get('change_pct') or 0,reverse=True):
        item={'name':name,'ret_1d':number(r.get('change_pct')),'previous':number(old.get(name,{}).get('change_pct')) if same else None}
        for h in (5,20):
            window=history[-h:]
            vals=[next((number(x.get('change_pct')) for x in data.get('rows',[]) if x['name']==name),None) for _,data in window]
            # All report sessions in the requested window must have complete same-source sector observations.
            ready=len(window)==h and [d for d,_ in window]==sessions[-h:] and all(data.get('complete') and data.get('source')==source for _,data in window) and all(v is not None for v in vals)
            item[f'ret_{h}d']=(math.prod(1+v/100 for v in vals)-1)*100 if ready else None
        up, down = number(r.get('up_count')),number(r.get('down_count'))
        item['breadth']=up/(up+down)*100 if up is not None and down is not None and up+down else None
        amounts=[number(x.get('amount')) for x in today.values()]
        denom=sum(x for x in amounts if x is not None)
        amt=number(r.get('amount'))
        item['amount_share']=amt/denom*100 if current.get('complete') and amt is not None and denom else None
        item['state']='历史不足'
        if item['ret_5d'] is not None and item['ret_1d'] is not None:
            item['state']='短中期同向走强' if item['ret_1d']>0 and item['ret_5d']>0 else '当日反弹、5日仍弱' if item['ret_1d']>0 else '当日走弱'
        elif item['previous'] is not None and item['ret_1d'] is not None:
            item['state']='由涨转跌' if item['previous']>0 and item['ret_1d']<0 else '由跌转涨' if item['previous']<0 and item['ret_1d']>0 else '两日同向，趋势未确认'
        rows.append(item)
    return {'source':source,'complete':current.get('complete',False),'rows':rows,
            'note':'旧报告仅恢复榜单内行业；5/20日只在连续报告日有完整同源行业快照时计算。不拼接不同分类体系，不将缺失值填零。'}


def build_context(directory, md=None):
    directory=Path(directory)
    date=directory.name
    md=md if md is not None else (directory/'report.md').read_text(encoding='utf-8')
    dates=sorted(p.name for p in directory.parent.iterdir() if re.fullmatch(r'\d{4}-\d{2}-\d{2}',p.name) and p.name<=date and (p/'report.md').exists())
    prior_dates=[d for d in dates if d<date]
    prev_date=prior_dates[-1] if prior_dates else None
    previous=(directory.parent/prev_date/'report.md').read_text(encoding='utf-8') if prev_date else ''
    picks=csv_rows(directory/'candidates.csv')
    old=csv_rows(directory.parent/prev_date/'candidates.csv') if prev_date else []
    old_codes={r['code'] for r in old}; new_codes={r['code'] for r in picks}
    market_now=index_rows(md); market_old=index_rows(previous)
    index_changes=[f'{name}涨跌幅较上次'+('提高' if market_now[name]>=market_old[name] else '下降')+f'{abs(market_now[name]-market_old[name]):.2f}个百分点' for name in market_now if name in market_old]
    fixed=json_read(directory.parent.parent/'state'/'research_cohort.json',{})
    raw=csv_rows(directory/'market_snapshot.csv'); prior_raw=csv_rows(directory.parent/prev_date/'market_snapshot.csv') if prev_date else []
    panel=fixed_panel(raw,prior_raw,fixed.get('codes',[])) if raw and fixed.get('created_date','9999')<=date else None
    current_ind=json_read(directory/'industry_snapshot.json',industries(md))
    previous_ind=json_read(directory.parent/prev_date/'industry_snapshot.json',industries(previous)) if prev_date else {}
    hist_path=directory/'signals_asof.csv'
    available=hist_path.exists()
    if not available:
        all_dates=sorted(p.name for p in directory.parent.iterdir() if (p/'report.md').exists())
        # Do not leak subsequently updated returns into an older reconstructed report.
        if all_dates and date==all_dates[-1]:
            hist_path=directory.parent.parent/'state'/'signals.csv'; available=hist_path.exists()
    signals=csv_rows(hist_path) if available else []
    if any(r.get('signal_date','')>date for r in signals): signals=[]; available=False
    sectors=industry_history(directory, dates if date in dates else dates+[date], current_ind, previous_ind)
    watch_changes={'added':[r['name']+'('+r['code']+')' for r in picks if r['code'] not in old_codes], 'removed':[r['name']+'('+r['code']+')' for r in old if r['code'] not in new_codes], 'retained':len(new_codes&old_codes)}
    news_audit=json_read(directory/'news_audit.json',{})
    model_match=re.search(r'^\| 模型目前怎样 \| (.*?) \|',md,re.M)
    from quality_audit import audit_selection
    selection_audit=json_read(directory/'selection_quality.json')
    if selection_audit is None:
        # Old reports do not preserve the original preselected cohort.
        selection_audit=audit_selection([],picks)
    result={'schema_version':1,'date':date,'previous_date':prev_date,'source':provider(md),'previous_source':provider(previous),
            'market_returns':market_now,'model_summary':model_match.group(1) if model_match else '',
            'selection_audit':selection_audit,
            'changes':{'indices':index_changes,'watchlist':watch_changes},'panel':panel,'industries':sectors,
            'history':mature_history(signals,picks,date,available),'stock_briefs':[stock_brief(r) for r in picks], 'news_audit':news_audit,
            'comparability_note':'原策略股票池和评分保持原口径；新增固定研究样本单独统计。两者不可混为一谈。'}
    write_json(directory/'analysis_context.json',result)
    return result

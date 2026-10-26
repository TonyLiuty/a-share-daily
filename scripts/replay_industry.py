"""Offline structural replay of saved candidates, not a performance backtest."""
import argparse
import csv
from pathlib import Path
from experimental_selection import mapped_selection
from report_insights import write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--daily-dir',type=Path,required=True)
    parser.add_argument('--date',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    source=args.daily_dir/args.date/'candidates.csv'
    if args.output.resolve()==source.resolve():parser.error('output must not overwrite candidates')
    with source.open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
    _,selection,audit=mapped_selection(rows,args.daily_dir,args.date)
    result={'signal_date':args.date,'scope':'original selected subset only; full 140-stock scores were not archived',
            'input_stocks':len(rows),'mapping':audit,
            'selected':[{'code':r['code'],'name':r.get('name'),'industry':r['industry'],'total_score':r['total_score'],
                         'mapping_date':r['mapping_date']} for r in selection['selected']],
            'note':'Structural replay only. No returns calculated, no live signals or histories changed.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    write_json(args.output,result)
    print(f"mapped {audit['mapped_count']}/{audit['total_count']}, selected {audit['selected_count']}, shortfall {audit['shortfall']}")

if __name__=='__main__':main()

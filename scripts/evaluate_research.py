"""Evaluate saved signals as equal-weight, complete-date research cohorts.

No fitting, missing-value filling or compounding of overlapping horizons.
Existing returns are assumed net of their original recorded cost model.
"""
import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date as calendar_date
from pathlib import Path


def numeric(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def mean(values):
    return sum(values) / len(values) if values else None


def evaluate(rows):
    """Returns are decimal fractions; maturity requires finite values including zero.

    Complete portfolio dates require all recorded stocks to have a mature return.
    Paired benchmark statistics additionally require every benchmark on that date.
    Completeness is relative to the CSV; absent original selections cannot be inferred.
    """
    rows = list(rows)
    versions = set()
    for position, row in enumerate(rows, 1):
        day, code = row.get('signal_date'), row.get('code')
        if not isinstance(day,str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',day):
            raise ValueError(f'record {position}: signal_date must use YYYY-MM-DD')
        try:
            calendar_date.fromisoformat(day)
        except ValueError as error:
            raise ValueError(f'record {position}: signal_date is not a valid calendar date') from error
        if not isinstance(code,str) or not re.fullmatch(r'[0-9]{6}',code):
            raise ValueError(f'record {position}: code must contain exactly six ASCII digits')
        version = row.get('strategy_version')
        if version is not None and not isinstance(version,str):
            raise ValueError(f'record {position}: strategy_version must be text')
        versions.add(version.strip() if version and version.strip() else None)
    if len(versions)>1:
        raise ValueError('mixed or partially missing strategy_version records; partition versions before evaluation')
    strategy_version = next(iter(versions),None)
    counts = Counter((row['signal_date'], row['code']) for row in rows)
    if any(count > 1 for count in counts.values()):
        raise ValueError('duplicate signal_date/code records; partition strategy versions before evaluation')
    groups = defaultdict(list)
    for row in rows:
        groups[str(row['signal_date'])].append(row)
    unique_stocks = len({row['code'] for row in rows})
    result = {'schema_version': 1, 'return_unit': 'decimal',
              'strategy_version': strategy_version,
              'strategy_version_status': 'declared' if strategy_version is not None else 'unknown',
              'method': 'equal-weight complete signal-date cohorts; arithmetic means; no compounding',
              'completeness_scope': 'all records present in the input CSV, not unrecorded selections',
              'all_n': len(rows), 'signal_dates_n': len(groups), 'independent_stocks_n': unique_stocks,
              'same_stock_repeat_ratio': (len(rows) - unique_stocks) / len(rows) if rows else None,
              'independence_note': 'Unique stocks are a count, not independent statistical observations; adjacent multi-day windows overlap.',
              'horizons': {}}
    for h in (1, 5, 20):
        ret_key, benchmark_key = f'ret_{h}d', f'benchmark_ret_{h}d'
        daily = []
        incomplete = []
        missing_benchmark = []
        for date, cohort in sorted(groups.items()):
            values = [numeric(row.get(ret_key)) for row in cohort]
            if any(value is None for value in values):
                incomplete.append(date)
                continue
            benchmarks = [numeric(row.get(benchmark_key)) for row in cohort]
            net = mean(values)
            bench = mean(benchmarks) if all(value is not None for value in benchmarks) else None
            if bench is None:
                missing_benchmark.append(date)
            daily.append({'signal_date': date, 'stocks_n': len(cohort), 'net_return': net,
                          'benchmark_return': bench, 'excess_return': net - bench if bench is not None else None})
        nets = [day['net_return'] for day in daily]
        paired = [day for day in daily if day['benchmark_return'] is not None]
        result['horizons'][str(h)] = {
            'all_n': len(rows), 'mature_n': sum(numeric(row.get(ret_key)) is not None for row in rows),
            'complete_dates_n': len(daily), 'complete_dates': [day['signal_date'] for day in daily],
            'incomplete_dates_n': len(incomplete), 'incomplete_dates': incomplete,
            'paired_benchmark_dates_n': len(paired), 'missing_benchmark_dates': missing_benchmark,
            'independent_stocks_n': unique_stocks,
            'same_stock_repeat_ratio': result['same_stock_repeat_ratio'],
            'daily_mean_net_return': mean(nets),
            'benchmark_mean_return': mean([day['benchmark_return'] for day in paired]),
            'paired_daily_mean_net_return': mean([day['net_return'] for day in paired]),
            'daily_mean_excess_return': mean([day['excess_return'] for day in paired]),
            'daily_win_rate': sum(value > 0 for value in nets) / len(nets) if nets else None,
            'additional_round_trip_cost': {
                str(bp) + 'bp': mean([value - bp / 10000 for value in nets]) for bp in (5, 10)},
            'daily_cohorts': daily,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description='Evaluate saved research signal cohorts without fitting')
    parser.add_argument('--signals', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.signals.resolve() == args.output.resolve():
        parser.error('output must differ from input signals')
    with args.signals.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        required = {'signal_date', 'code'} | {f'ret_{h}d' for h in (1, 5, 20)} | {f'benchmark_ret_{h}d' for h in (1, 5, 20)}
        if not required.issubset(reader.fieldnames or []):
            parser.error('signals CSV missing required columns: ' + ', '.join(sorted(required - set(reader.fieldnames or []))))
        result = evaluate(reader)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'completed', 'output': str(args.output), 'all_n': result['all_n']}, ensure_ascii=False))


if __name__ == '__main__':
    main()

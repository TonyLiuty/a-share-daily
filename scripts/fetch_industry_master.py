"""Archive current official SW primary classifications, without backdating availability."""
import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from industry_mapping import SW_PRIMARY
from import_industry_mapping import import_mapping

PAGE = 'https://www.swsresearch.com/institute_sw/allIndex/downloadCenter/industryType'
API = 'https://www.swsresearch.com/institute-sw/api/download_center/'


def parse_sector(content, sector):
    """Read the explicit primary label; inclusion dates are not availability dates."""
    frame = pd.read_excel(io.BytesIO(content), dtype=str, engine='openpyxl')
    required = {'行业名称', '股票代码', '股票名称', '计入日期'}
    if not required.issubset(frame.columns) or frame.empty:
        raise ValueError(f'{sector}: missing columns or empty classification')
    rows = []
    for item in frame.to_dict('records'):
        code = str(item['股票代码']).strip()
        if str(item['行业名称']).strip() != sector or sector not in SW_PRIMARY:
            raise ValueError(f'{sector}: inconsistent primary industry')
        if not re.fullmatch(r'\d{6}', code):
            raise ValueError(f'{sector}: invalid security code')
        rows.append({'code': code, 'sw_industry': sector,
                     'name': str(item['股票名称']), 'provider_inclusion_date': str(item['计入日期'])})
    return rows


def fetch(output, transport='requests'):
    started = datetime.now(ZoneInfo('Asia/Shanghai'))
    output = Path(output) / started.strftime('%Y-%m-%dT%H%M%S')
    # An archive is immutable; a repeated timestamp cannot overwrite it.
    output.mkdir(parents=True, exist_ok=False)
    raw = output / 'raw'
    raw.mkdir()
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': PAGE})
    manifest = {'status': 'collecting', 'started_at': started.isoformat(),
                'source_page': PAGE, 'files': [], 'classification': 'SW2021 primary',
                'availability': 'Actual retrieval date, not provider inclusion date. Current snapshot is not historical membership.'}

    def download(url, filename):
        if transport == 'curl':
            # Native curl can use the OS certificate store on Windows. TLS
            # verification remains enabled; never fall back to insecure HTTPS.
            result = subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
                                     '--connect-timeout', '8', '--max-time', '30',
                                     '--user-agent', 'Mozilla/5.0', '--referer', PAGE, url],
                                    check=True, capture_output=True, timeout=35)
            content = result.stdout
        else:
            response = session.get(url, timeout=(8, 30))
            response.raise_for_status()
            content = response.content
        (raw / filename).write_bytes(content)
        manifest['files'].append({'url': url, 'file': 'raw/' + filename,
                                  'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content),
                                  'retrieved_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()})
        return content

    try:
        payload = json.loads(download(API + 'trade_classification/?' + urlencode(
            {'page': 1, 'page_size': 100, 'indextype': '一级行业'}), 'sectors.json'))
        data = payload['data']
        sectors = [row['swindexname'] for row in data['results']]
        if data['count'] != 31 or len(sectors) != 31 or set(sectors) != SW_PRIMARY:
            raise ValueError('official primary list differs from supported SW2021 classification')
        rows = []
        for index, sector in enumerate(sorted(sectors)):
            url = API + 'download_file/?' + urlencode({'file_name': sector + '分类表'})
            sector_rows = parse_sector(download(url, f'{index:02d}.xlsx'), sector)
            for row in sector_rows:
                row['source'] = url
            rows.extend(sector_rows)
        finished = datetime.now(ZoneInfo('Asia/Shanghai'))
        # The full master is first usable when the last source has been obtained.
        for row in rows:
            row['observed_date'] = finished.date().isoformat()
        path = output / 'industry_master.csv'
        fields = ['code', 'sw_industry', 'observed_date', 'source', 'name', 'provider_inclusion_date']
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        mapping = import_mapping(path, finished.date().isoformat(), taxonomy='sw', taxonomy_version='2021')
        (output / 'industry_master.json').write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        manifest.update(status='completed', completed_at=finished.isoformat(), **mapping['metadata'])
        manifest['availability'] = 'Actual completed retrieval date; provider inclusion dates are not availability. Downloaded tables may contain conflicting memberships; those securities remain unknown.'
    except Exception as error:
        manifest.update(status='data_error', error=str(error))
        raise
    finally:
        (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        session.close()
    return {'directory': str(output.resolve()), **manifest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--transport', choices=['requests', 'curl'], default='requests')
    args = parser.parse_args()
    result = fetch(args.output, args.transport)
    print(json.dumps({key: result[key] for key in ('status', 'directory', 'completed_at',
                     'record_count', 'security_count', 'mapped_count', 'coverage', 'file_sha256')}, ensure_ascii=True))


if __name__ == '__main__':
    main()

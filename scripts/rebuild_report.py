"""Offline presentation rebuild: no network, no score/signal/config writes."""
import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from event_analysis import rewrite_event_section
from run_daily import markdown_to_html
from validate_report import validate
from report_insights import build_context
from insights_ui import markdown_append


def rebuild(directory):
    directory = directory.resolve(strict=True)
    report = directory / 'report.md'
    output = directory / 'report.html'
    root = directory.parent.parent
    guarded = [directory/'candidates.csv', directory/'fundamentals.csv', root/'config.json', root/'state'/'signals.csv', root/'state'/'equity_curve.csv']
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in guarded if p.exists()}
    md = rewrite_event_section(report.read_text(encoding='utf-8'))
    title = md.splitlines()[0].lstrip('# ')
    context = build_context(directory, md)
    md = markdown_append(md, context)
    page = markdown_to_html(md, title, context)
    # Validate the replacement alongside the original charts before promoting it.
    check_md, check_html = directory/'report.preview.md', directory/'report.preview.html'
    check_md.write_text(md, encoding='utf-8')
    check_html.write_text(page, encoding='utf-8')
    result = validate(check_md, check_html)
    if result['status'] != 'passed':
        raise RuntimeError(json.dumps(result, ensure_ascii=False))
    backup = directory/'revisions'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True)
    for path in (report, output):
        if path.exists(): shutil.copy2(path, backup/path.name)
    check_md.replace(report)
    check_html.replace(output)
    latest = root/'latest.md'
    if latest.exists() and directory.name in latest.read_text(encoding='utf-8').splitlines()[0]:
        shutil.copy2(report, latest)
        (root/'latest.html').write_text(page.replace('<head>', f'<head><base href="daily/{directory.name}/">',1), encoding='utf-8')
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p,digest in before.items()), 'Research data changed'
    return {'status':'passed', 'report':str(report), 'html':str(output), 'backup':str(backup), 'data_files_unchanged':len(before), 'checks':result['checks']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(rebuild(args.directory), ensure_ascii=False, indent=2))

"""Bounded public-page retrieval; retrieval and factual verification are separate states."""
import hashlib
import re
from datetime import datetime
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin
import requests

ALLOWED = ('eastmoney.com','cls.cn','cac.gov.cn','nea.gov.cn','oecd.org','gov.cn','cninfo.com.cn','gelonghui.com')


class Article(HTMLParser):
    def __init__(self):
        super().__init__(); self.skip=0; self.title=False; self.title_parts=[]; self.parts=[]; self.links=[]; self.current=None
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag in ('script','style','noscript'): self.skip+=1
        if tag=='title': self.title=True
        if tag=='a': self.current=[attrs.get('href',''),'']
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'): self.skip=max(0,self.skip-1)
        if tag=='title': self.title=False
        if tag=='a' and self.current: self.links.append(tuple(self.current)); self.current=None
    def handle_data(self,data):
        if self.skip:return
        if self.title:self.title_parts.append(data)
        if self.current:self.current[1]+=data
        self.parts.append(data.strip())


def allowed(url):
    p=urlparse(url); host=(p.hostname or '').lower()
    return p.scheme in ('http','https') and any(host==d or host.endswith('.'+d) for d in ALLOWED) and p.port in (None,80,443)


def get_page(url, session):
    for _ in range(4):
        if not allowed(url):raise ValueError('来源域名不在公开新闻白名单内')
        with session.get(url,timeout=(4,8),allow_redirects=False,stream=True) as response:
            if 300<=response.status_code<400:
                url=urljoin(url,response.headers.get('Location',''));continue
            response.raise_for_status()
            data=bytearray()
            for chunk in response.iter_content(32768):
                data.extend(chunk)
                if len(data)>1_500_000:raise ValueError('页面超出读取上限')
            encoding=response.encoding
            if not encoding or encoding.lower()=='iso-8859-1':encoding='utf-8'
            parser=Article();parser.feed(data.decode(encoding,errors='replace'))
            return url,parser,hashlib.sha256(data).hexdigest()
    raise ValueError('重定向次数过多')


def normalize(text):return re.sub(r'[^\w\u4e00-\u9fff]','',text).lower()


def retrieve(event, session=None):
    session=session or requests.Session()
    session.headers.update({'User-Agent':'Mozilla/5.0'})
    stamp=datetime.now().astimezone().isoformat()
    result={'headline':event['title'],'feed_time':event.get('time',''),'event_time':None,'retrieved_at':stamp,
            'claim_status':'待人工核验','url':event.get('url',''),'status':'unavailable'}
    try:
        url,doc,digest=get_page(result['url'],session)
        title=normalize(event['title'])
        if not urlparse(url).path.rstrip('/'):
            matches=[(SequenceMatcher(None,title,normalize(text)).ratio(),urljoin(url,href)) for href,text in doc.links if allowed(urljoin(url,href))]
            matches.sort(reverse=True)
            if not matches or matches[0][0]<0.72:raise ValueError('快讯首页未发现足够匹配的独立详情链接')
            url,doc,digest=get_page(matches[0][1],session)
        body=' '.join(x for x in doc.parts if x)
        doc_title=' '.join(doc.title_parts)
        similarity=SequenceMatcher(None,title,normalize(doc_title)).ratio()
        matched=title in normalize(body) or similarity>=0.65
        result.update(url=url,page_title=doc_title,sha256=digest,characters=len(body),headline_match=matched,
                      status='retrieved' if len(body)>100 and matched else 'needs_review',
                      source_class='官方页面' if any(d in (urlparse(url).hostname or '') for d in ('gov.cn','oecd.org','cninfo.com.cn')) else '媒体页面',
                      note='已取到匹配页面，尚未验证每个数字、预测和事件发生时间。' if matched else '页面可读取但与标题匹配不足，不能视为原文核实。')
        # No full copyrighted article copied into the report or storage.
    except Exception as exc:result['error']=str(exc)
    return result


def collect(events):
    with requests.Session() as session:
        return {e['title']:retrieve(e,session) for e in events[:8]}


if __name__=='__main__':
    import argparse
    from pathlib import Path
    from report_ui import rows, section
    from report_insights import write_json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    args=parser.parse_args()
    md=(args.directory/'report.md').read_text(encoding='utf-8')
    events=[]
    for row in rows(section(md,'### 重大事件与市场影响分析'))[1:]:
        match=re.match(r'\[([^\]]+)\]\(([^)]+)\)(.*)',row[0])
        if match:events.append({'title':match[1],'url':match[2],'time':match[3].strip('｜').split('｜')[0]})
    audit=collect(events)
    write_json(args.directory/'news_audit.json',audit)
    print(f'新闻来源读取完成：{len(audit)}条，读取与事实核验分开记录。')

#!/usr/bin/env python3
from __future__ import annotations
import json, os, time, urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
SHOPS=ROOT/'config'/'shops.json'
OUT=ROOT/'site'/'data'/'probe_runtime.json'
TIMEOUT=20
DELAY=4.0
repo=os.environ.get('GITHUB_REPOSITORY','your-account/gcg-stock-watch')
UA=f'GCG-Stock-Watch-Probe/1.9 (+https://github.com/{repo}; two-card low-rate preflight; no purchase automation)'
SAMPLES={
 'lrpp': {'code':'GD05-067','tokens':['LR++','スーパーパラレル','パラレル++','金縁']},
 'beta': {'code':'ST02-001','tokens':['LR+','β','ベータ版','リミテッドBOX']},
}

def compact(s): return ''.join((s or '').lower().split())
def robots_allowed(sess,url):
 p=urlparse(url); robot=f'{p.scheme}://{p.netloc}/robots.txt'
 try:
  r=sess.get(robot,timeout=TIMEOUT,headers={'User-Agent':UA})
  if r.status_code==404: return True,'404=no rules'
  if r.status_code in (403,429) or r.status_code>=400: return False,f'robots HTTP {r.status_code}'
  rp=urllib.robotparser.RobotFileParser(); rp.parse(r.text.splitlines())
  return rp.can_fetch(UA,url),'robots parsed'
 except Exception as e: return False,f'robots error {type(e).__name__}'

def sample_found(html,key,shop=None):
 txt=compact(BeautifulSoup(html,'html.parser').get_text(' ',strip=True))
 s=SAMPLES[key]
 adapter=(shop or {}).get('adapter','')
 if adapter=='cardland_exact':
  if key=='lrpp': return compact('ウイングガンダムゼロ（EW版）') in txt and compact('GD05/LR++') in txt
  return compact('ウイングガンダム') in txt and compact('Ver.β/パラレル') in txt
 if compact(s['code']) not in txt: return False
 # Require at least one strong variant token; beta needs beta context as well as LR+.
 if key=='lrpp': return any(compact(x) in txt for x in s['tokens'])
 return compact('LR+') in txt and any(compact(x) in txt for x in ['β','ベータ版','リミテッドBOX'])

def main():
 shops=json.loads(SHOPS.read_text(encoding='utf-8'))
 sess=requests.Session(); sess.headers.update({'User-Agent':UA,'Accept-Language':'ja-JP,ja;q=0.9','Accept':'text/html,application/xhtml+xml'})
 rows=[]; last=0.0
 for sh in shops:
  row={'id':sh['id'],'name':sh['name'],'monitorStatus':sh.get('monitorStatus'),'samples':{}}
  for key in ('lrpp','beta'):
   urls=(sh.get('probeUrls') or {}).get(key,[]) or []
   if not urls:
    row['samples'][key]={'status':'not_configured','reason':'no verified probe URL'}; continue
   best={'status':'failed','reason':'no page returned'}
   for url in urls[:2]:
    if any(x in url for x in (sh.get('forbidUrlContains') or [])):
     best={'status':'blocked_by_guard','reason':'non-sales/unsafe URL guard'}; continue
    ok,why=robots_allowed(sess,url)
    if not ok: best={'status':'robots_hold','reason':why}; continue
    wait=DELAY-(time.monotonic()-last)
    if wait>0: time.sleep(wait)
    try:
     r=sess.get(url,timeout=TIMEOUT,allow_redirects=True); last=time.monotonic()
     if r.status_code in (403,429): best={'status':'http_hold','reason':f'HTTP {r.status_code}'}; break
     if r.status_code>=400: best={'status':'http_error','reason':f'HTTP {r.status_code}'}; continue
     if len(r.text)<500: best={'status':'short_html','reason':'HTML too short'}; continue
     found=sample_found(r.text,key,sh)
     best={'status':'sample_found' if found else 'page_ok_sample_missing','http':r.status_code,'url':r.url,'bytes':len(r.content)}
     if found: break
    except Exception as e:
     last=time.monotonic(); best={'status':'request_error','reason':f'{type(e).__name__}: {str(e)[:100]}'}
   row['samples'][key]=best
  rows.append(row)
 out={'generatedAt':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'note':'This probe only checks two representative cards from the actual automation network. It never promotes a probation shop automatically.','shops':rows}
 OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'shops':len(rows),'sampleFound':sum(v.get('status')=='sample_found' for r in rows for v in r['samples'].values())},ensure_ascii=False))
if __name__=='__main__': main()

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen
from html import unescape
from datetime import datetime
import re,json,unicodedata
MEM=[]; NEXT_ID=1

def clean(v): return re.sub(r'\s+',' ',unicodedata.normalize('NFC',unescape(re.sub(r'<[^>]+>',' ',v)))).strip()
def simhash(title,body):
 v=[0]*64
 for tok in re.findall(r'[\w가-힣]+',clean(title+'\n---CONTENT---\n'+body).lower()):
  h=0xcbf29ce484222325
  for ch in tok:h=((h^ord(ch))*0x100000001b3)&((1<<64)-1)
  for i in range(64):v[i]+=1 if h&(1<<i) else -1
 return format(sum(1<<i for i,x in enumerate(v) if x>=0),'016x')
def parse(url):
 raw=urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=20).read().decode('utf-8','replace')
 # Prefer a known announcement body; then semantic main/article; never use navigation as body.
 scope=raw[raw.find('epform bbs gosi view'):] if 'epform bbs gosi view' in raw else raw
 rows=re.findall(r'<tr>(.*?)</tr>',scope,re.S); vals=[]
 for r in rows:
  m=re.findall(r'<td[^>]*>(.*?)</td>',r,re.S)
  if m: vals.append(clean(m[0]))
 if len(vals)>=7: title,body=vals[4],vals[6]
 else:
  tm=re.search(r'<(?:h1|h2)[^>]*>(.*?)</(?:h1|h2)>',scope,re.S|re.I); title=clean(tm.group(1)) if tm else clean(re.search(r'<title>(.*?)</title>',raw,re.S|re.I).group(1))
  bm=re.search(r'<(?:article|main)[^>]*>(.*?)</(?:article|main)>',scope,re.S|re.I); body=clean(bm.group(1)) if bm else ''
 date=re.search(r'(20\d{2}[-./]\d{1,2}[-./]\d{1,2})',scope); return {'url':url,'title':title,'body':body,'simhash':simhash(title,body),'registered_date':date.group(1) if date else None}
class H(SimpleHTTPRequestHandler):
 def out(self,x,status=200):
  b=json.dumps(x,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def do_GET(self):
  global NEXT_ID
  p=urlparse(self.path); q=parse_qs(p.query)
  try:
   if p.path=='/api/records':return self.out({'records':MEM})
   if p.path=='/api/clear':
    MEM.clear(); NEXT_ID=1
    return self.out({'cleared': True})
   if p.path=='/api/delete':
    record_id=int(q.get('id',['0'])[0]); before=len(MEM); MEM[:]=[x for x in MEM if x['id']!=record_id]
    return self.out({'deleted': len(MEM)!=before})
   if p.path not in ('/api/store','/api/check'): return super().do_GET()
   u=q.get('url',[''])[0]
   if urlparse(u).scheme not in ('http','https'):raise ValueError('http/https URL을 입력하세요.')
   d=parse(u)
   if p.path=='/api/check':
    matches=[x for x in MEM if x['simhash']==d['simhash']];return self.out({'parsed':d,'duplicate':bool(matches),'matches':matches})
   matches=[x for x in MEM if x['simhash']==d['simhash']]
   if matches: return self.out({'saved': False, 'duplicate': True, 'matches': matches, 'parsed': d})
   d.update({'id':NEXT_ID,'saved_at':datetime.now().strftime('%Y-%m-%d %H:%M:%S')});NEXT_ID+=1;MEM.append(d);return self.out({'saved':d,'duplicate':False})
   
  except Exception as e:return self.out({'error':str(e)},400)
ThreadingHTTPServer(('127.0.0.1',4173),H).serve_forever()






import json,os,time,urllib.request,urllib.error,math,re
from datetime import datetime,timedelta,timezone
from pathlib import Path

BASE='https://psxdata-api.fastapicloud.dev'
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
DATA.mkdir(exist_ok=True)
PKT=timezone(timedelta(hours=5))
now=datetime.now(PKT)
start=(now-timedelta(days=300)).date().isoformat()
end=now.date().isoformat()

def fetch(path):
    for attempt in range(4):
        try:
            req=urllib.request.Request(BASE+path,headers={'Accept':'application/json','User-Agent':'PSX-Intelligence-Data-Updater/1.2'})
            with urllib.request.urlopen(req,timeout=35) as response:
                return json.load(response)
        except (urllib.error.URLError,TimeoutError,ValueError) as exc:
            if attempt==3:
                raise RuntimeError(f'API failed for {path}: {exc}') from exc
            time.sleep(3*(attempt+1))

def payload(o):
    return o.get('data',o) if isinstance(o,dict) else o

symbols=payload(fetch('/stocks'))
if not isinstance(symbols,list):
    raise RuntimeError('Unexpected symbol response')
symbols=sorted(set(str(x if isinstance(x,str) else x.get('symbol','')).strip().upper() for x in symbols))
symbols=[s for s in symbols if re.fullmatch(r'[A-Z0-9.-]{1,20}',s)]
if len(symbols)<50:
    raise RuntimeError('Too few symbols: refusing to overwrite data')

old={}
path=DATA/'market.json'
if path.exists():
    try:
        old=json.loads(path.read_text(encoding='utf-8')).get('stocks',{})
    except (ValueError,OSError):
        pass

stocks={}
errors={}
updated=0
for idx,s in enumerate(symbols,1):
    try:
        rows=payload(fetch(f'/stocks/{s}/historical?start={start}&end={end}'))
        if not isinstance(rows,list):
            raise ValueError('Invalid candle response')
        clean=[]
        for r in rows:
            try:
                d=str(r['date'])[:10]
                o=float(r['open']);h=float(r['high']);l=float(r['low']);c=float(r['close']);v=float(r['volume'])
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',d) or d>end: continue
                if not all(map(math.isfinite,(o,h,l,c,v))): continue
                if not (0<l<=min(o,c)<=max(o,c)<=h and v>=0): continue
                clean.append({'date':d,'open':o,'high':h,'low':l,'close':c,'volume':v})
            except (KeyError,TypeError,ValueError):
                continue
        clean=sorted({r['date']:r for r in clean}.values(),key=lambda r:r['date'])
        if len(clean)>=60 and (now.date()-datetime.fromisoformat(clean[-1]['date']).date()).days<=10:
            stocks[s]={'candles':clean,'last_date':clean[-1]['date'],'source':'api','is_fresh':True}
            updated+=1
        else:
            errors[s]='insufficient or stale candles'
    except Exception as exc:
        errors[s]=str(exc)[:140]
    if idx%45==0:
        print(f'Processed {idx}/{len(symbols)}; valid {updated}',flush=True)
    time.sleep(0.15)

from collections import Counter
print('DATA QUALITY REPORT:',dict(Counter(errors.values())),flush=True)
print(f'Fresh valid: {updated}/{len(symbols)}',flush=True)

if updated<max(100,int(len(symbols)*0.30)):
    raise RuntimeError(f'Coverage critically low: {updated}/{len(symbols)}; existing dataset retained')

for s,item in old.items():
    if s not in stocks and s in symbols and isinstance(item,dict):
        retained=dict(item)
        retained['source']='retained_old'
        retained['is_fresh']=False
        stocks[s]=retained

# Build snapshot rows for the dashboard
rows=[]
for s,item in stocks.items():
    c=item.get('candles') or []
    if len(c)<2:
        continue
    last=c[-1];prev=c[-2]
    avg20=sum(x['volume'] for x in c[-20:])/min(20,len(c))
    rows.append({
        'symbol':s,
        'close':last['close'],
        'prev_close':prev['close'],
        'volume':last['volume'],
        'avg_volume_20':avg20,
        'asof':last['date']
    })

out={
    'version':'2.0',
    'source':BASE,
    'source_authorized':False,
    'generated_at':datetime.now(PKT).isoformat(),
    'fresh_symbols':updated,
    'total_symbols':len(symbols),
    'retained_old_symbols':len(stocks)-updated,
    'errors':errors,
    'stocks':stocks,
    'rows':rows,
    'notice':'Daily historical OHLCV, not live. Check each candle date and source before trading.'
}

tmp=path.with_suffix('.tmp')
tmp.write_text(json.dumps(out,separators=(',',':'),ensure_ascii=False),encoding='utf-8')
os.replace(tmp,path)

(DATA/'status.json').write_text(json.dumps({
    'generated_at':out['generated_at'],
    'fresh_symbols':updated,
    'total_symbols':len(symbols),
    'retained_old_symbols':len(stocks)-updated,
    'failed_symbols':len(errors),
    'latest_dates':sorted(set(v['last_date'] for v in stocks.values()),reverse=True)[:5]
},indent=2),encoding='utf-8')

print('Saved',path,'fresh',updated,'old',len(stocks)-updated,'rows',len(rows))
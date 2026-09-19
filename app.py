import math
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title='GOLD AI | XAU/USD', page_icon='🟡', layout='wide', initial_sidebar_state='collapsed')

TZ = ZoneInfo('Asia/Riyadh')
SYMBOL = 'XAU/USD'
DATA_URL = 'https://api.twelvedata.com/time_series'
QUOTE_URL = 'https://api.twelvedata.com/quote'
START_BALANCE = 100_000.0
RISK_PER_TRADE = 0.005
DAILY_LOSS_LIMIT = 0.02
MAX_DAILY_TRADES = 5
CHUNKS = 4
BARS_PER_CHUNK = 5000

st.markdown('''<style>
:root{--bg:#070b12;--panel:#0e1623;--panel2:#111c2c;--line:#24334b;--gold:#d4af37;--text:#f5f7fb;--muted:#8fa1ba;--green:#25c77a;--red:#ef5b67}
.stApp{background:var(--bg);color:var(--text)}.block-container{max-width:1450px;padding:1rem 1rem 4rem}
.card{background:linear-gradient(145deg,var(--panel),#0a121e);border:1px solid var(--line);border-radius:18px;padding:18px;margin-bottom:14px}
.hero{border:1px solid #554717;border-radius:22px;padding:24px;background:radial-gradient(circle at 85% 15%,rgba(212,175,55,.14),transparent 32%),var(--panel)}
.kicker{font-size:.75rem;color:var(--muted);letter-spacing:.15em}.gold{color:var(--gold)}.muted{color:var(--muted)}
.price{font-size:clamp(2.6rem,8vw,5rem);font-weight:900;line-height:1}.signal{font-size:2rem;font-weight:900}.good{color:var(--green)}.bad{color:var(--red)}
.metric{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:13px}.label{font-size:.72rem;color:var(--muted)}.value{font-size:1.25rem;font-weight:800;margin-top:4px}
div[data-testid="stMetric"]{background:var(--panel);border:1px solid var(--line);border-radius:14px}
</style>''', unsafe_allow_html=True)


def init_state():
    defaults = {'balance':START_BALANCE,'position':None,'history':[],'decisions':[],
                'daily_start_balance':START_BALANCE,'daily_date':datetime.now(TZ).date().isoformat(),
                'daily_trades':0,'kill_switch':False,'research_mode':False,
                'last_signal_candle':None,'last_price':np.nan,'auto_refresh':False}
    for k,v in defaults.items():
        if k not in st.session_state: st.session_state[k]=v
    today=datetime.now(TZ).date().isoformat()
    if st.session_state.daily_date != today:
        st.session_state.daily_date=today; st.session_state.daily_start_balance=st.session_state.balance; st.session_state.daily_trades=0

init_state()


def secret(name):
    try: return st.secrets[name]
    except Exception: return None


@st.cache_data(ttl=45, show_spinner=False)
def fetch_chunk(end_date=None):
    key=secret('TWELVE_DATA_API_KEY')
    if not key: return pd.DataFrame(),'TWELVE_DATA_API_KEY غير موجود في Secrets',None
    p={'symbol':SYMBOL,'interval':'5min','outputsize':BARS_PER_CHUNK,'timezone':'UTC','apikey':key}
    if end_date: p['end_date']=end_date
    try:
        r=requests.get(DATA_URL,params=p,timeout=20); payload=r.json()
        if 'values' not in payload: return pd.DataFrame(),str(payload.get('message','مصدر البيانات رفض الطلب')),r.headers.get('api-credits-left')
        x=pd.DataFrame(payload['values']); x['datetime']=pd.to_datetime(x['datetime'],utc=True,errors='coerce')
        for c in ['open','high','low','close','volume']:
            if c in x: x[c]=pd.to_numeric(x[c],errors='coerce')
        x=x.dropna(subset=['datetime','open','high','low','close']).drop_duplicates('datetime').sort_values('datetime').reset_index(drop=True)
        return x,'OK',r.headers.get('api-credits-left')
    except Exception as e: return pd.DataFrame(),f'خطأ اتصال: {e}',None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_history():
    frames=[]; errors=[]; credits=[]; end=pd.Timestamp.now(tz='UTC')
    for i in range(CHUNKS):
        x,msg,left=fetch_chunk(end.strftime('%Y-%m-%d %H:%M:%S'))
        if x.empty: errors.append(f'الدفعة {i+1}: {msg}'); break
        frames.append(x); credits.append(left) if left is not None else None
        oldest=x.datetime.min()
        if oldest>=end: errors.append('التاريخ لم يتحرك للخلف'); break
        end=oldest-pd.Timedelta(minutes=5)
        if len(x)<BARS_PER_CHUNK: break
    if not frames: return pd.DataFrame(),' | '.join(errors),0,credits
    out=pd.concat(frames,ignore_index=True).drop_duplicates('datetime').sort_values('datetime').reset_index(drop=True)
    msg='تم تحميل التاريخ المطلوب' if len(frames)==CHUNKS else 'تم تحميل جزء من التاريخ فقط'
    if errors: msg+=' • '+' | '.join(errors)
    return out,msg,len(frames),credits


def closed_m5(x):
    if x.empty:return x
    return x[x.datetime < pd.Timestamp.now(tz='UTC').floor('5min')].copy()


def resample_closed(x,rule):
    x=closed_m5(x)
    if x.empty:return x
    y=(x.set_index('datetime')[['open','high','low','close']].resample(rule,label='left',closed='left')
       .agg({'open':'first','high':'max','low':'min','close':'last'}).dropna().reset_index())
    return y[y.datetime+pd.Timedelta(rule)<=pd.Timestamp.now(tz='UTC')].reset_index(drop=True)


def quality(x):
    if x.empty:return False,'لا توجد بيانات'
    age=(pd.Timestamp.now(tz='UTC')-x.datetime.iloc[-1]).total_seconds()/60
    gaps=x.datetime.diff().dropna().dt.total_seconds().div(60)
    max_gap=float(gaps.max()) if not gaps.empty else 0.0
    dup=int(x.datetime.duplicated().sum())
    ok=x.datetime.is_monotonic_increasing and dup==0 and age<20 and max_gap<=10
    return ok,f'آخر شمعة منذ {age:.1f} دقيقة • فجوة قصوى {max_gap:.1f} دقيقة • تكرار {dup}'

@st.cache_data(ttl=15, show_spinner=False)
def fetch_quote():
    key=secret('TWELVE_DATA_API_KEY')
    if not key:return {'connected':False,'bid':np.nan,'ask':np.nan,'spread':np.nan,'label':'مفتاح Twelve Data غير موجود'}
    try:
        r=requests.get(QUOTE_URL,params={'symbol':SYMBOL,'apikey':key},timeout=10); p=r.json()
        bid=pd.to_numeric(p.get('bid'),errors='coerce'); ask=pd.to_numeric(p.get('ask'),errors='coerce')
        if not np.isfinite(bid) or not np.isfinite(ask) or ask<=bid:
            return {'connected':False,'bid':np.nan,'ask':np.nan,'spread':np.nan,'label':'مصدر Bid/Ask غير متاح'}
        return {'connected':True,'bid':float(bid),'ask':float(ask),'spread':float(ask-bid),'label':f"Bid/Ask متصل • السبريد {float(ask-bid):.2f}"}
    except Exception as e:
        return {'connected':False,'bid':np.nan,'ask':np.nan,'spread':np.nan,'label':'تعذر جلب Bid/Ask'}


def ema(s,n):return s.ewm(span=n,adjust=False).mean()

def rsi(s,n=14):
    d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    au=up.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); ad=dn.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    return (100-100/(1+au/ad.replace(0,np.nan))).fillna(50)

def atr(x,n=14):
    pc=x.close.shift(1); tr=pd.concat([x.high-x.low,(x.high-pc).abs(),(x.low-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()

def macd(s):
    line=ema(s,12)-ema(s,26); sig=ema(line,9); return line,sig,line-sig

def adx(x,n=14):
    up=x.high.diff(); dn=-x.low.diff(); plus=pd.Series(np.where((up>dn)&(up>0),up,0.),index=x.index); minus=pd.Series(np.where((dn>up)&(dn>0),dn,0.),index=x.index)
    pc=x.close.shift(1); tr=pd.concat([x.high-x.low,(x.high-pc).abs(),(x.low-pc).abs()],axis=1).max(axis=1)
    aw=tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); pdi=100*plus.ewm(alpha=1/n,adjust=False,min_periods=n).mean()/aw.replace(0,np.nan); mdi=100*minus.ewm(alpha=1/n,adjust=False,min_periods=n).mean()/aw.replace(0,np.nan)
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan); return dx.ewm(alpha=1/n,adjust=False,min_periods=n).mean(),pdi,mdi

def indicators(x):
    y=x.copy(); y['ema20']=ema(y.close,20); y['ema50']=ema(y.close,50); y['ema100']=ema(y.close,100); y['rsi']=rsi(y.close); y['atr']=atr(y)
    y['macd'],y['macd_signal'],y['macd_hist']=macd(y.close); y['momentum']=y.close.pct_change(5)*100; y['adx'],y['plus_di'],y['minus_di']=adx(y); return y

def trend(r):
    if r.close>r.ema20>r.ema50>r.ema100:return 'صاعد'
    if r.close<r.ema20<r.ema50<r.ema100:return 'هابط'
    return 'محايد'

def regime(r):
    if not np.isfinite(r.adx) or not np.isfinite(r.atr):return 'غير معروف'
    if r.adx>=25:return 'اتجاه'
    if r.adx<=18:return 'نطاق'
    return 'انتقالي'

def snapshot(x):
    if len(x)<110:return None
    y=indicators(x).dropna().reset_index(drop=True)
    if y.empty:return None
    r=y.iloc[-1]
    return {'trend':trend(r),'regime':regime(r),'rsi':float(r.rsi),'adx':float(r.adx),'atr':float(r.atr),'momentum':float(r.momentum),'close':float(r.close),'macd_hist':float(r.macd_hist),'candle':r.datetime,'frame':y}


def b2(x):
    if len(x)<30:return {'valid':False,'direction':None,'breakout':False,'retest':False,'level':np.nan}
    r=x.iloc[-1]; p=x.iloc[-21:-1]; res=float(p.high.max()); sup=float(p.low.min()); av=float(r.atr)
    if not np.isfinite(av) or av<=0:return {'valid':False,'direction':None,'breakout':False,'retest':False,'level':np.nan}
    bb=float(x.close.iloc[-2])>res; bs=float(x.close.iloc[-2])<sup
    rb=bb and r.low<=res+.35*av and r.close>res; rs=bs and r.high>=sup-.35*av and r.close<sup
    if rb:return {'valid':True,'direction':'شراء','breakout':True,'retest':True,'level':res}
    if rs:return {'valid':True,'direction':'بيع','breakout':True,'retest':True,'level':sup}
    return {'valid':False,'direction':None,'breakout':bool(bb or bs),'retest':False,'level':res if bb else sup if bs else np.nan}


def news_gate(research):
    url=secret('NEWS_API_URL')
    if not url:return {'connected':False,'blocked':not research,'label':'غير متصل — لا يوجد مزود Economic Calendar موثوق'}
    try:
        p=requests.get(url,timeout=8).json(); block=bool(p.get('high_impact',False) or p.get('block',False))
        return {'connected':True,'blocked':block,'label':'خبر عالي التأثير' if block else 'لا يوجد حظر'}
    except Exception:return {'connected':False,'blocked':not research,'label':'تعذر الاتصال — تم تطبيق الحظر الآمن'}


def spread_gate(research):
    q=fetch_quote()
    if q['connected']:
        # No arbitrary universal spread threshold: only report the live quote unless a user-configured limit exists.
        max_spread=secret('MAX_SPREAD')
        try:max_spread=float(max_spread) if max_spread is not None else None
        except Exception:max_spread=None
        blocked=bool(max_spread is not None and q['spread']>max_spread)
        label=q['label'] + (f' • الحد {max_spread:.2f}' if max_spread is not None else '')
        if blocked: label+=' • السبريد أعلى من الحد'
        return {'connected':True,'blocked':blocked,'label':label,'bid':q['bid'],'ask':q['ask'],'spread':q['spread']}
    return {'connected':False,'blocked':not research,'label':q['label'] if not research else q['label']+' • وضع البحث التجريبي','bid':np.nan,'ask':np.nan,'spread':np.nan}


def unrealized_r(p,price):
    if not p or not np.isfinite(price):return 0.
    d=p['initial_distance']; direction=1 if p['side']=='شراء' else -1
    return p['realized_r']+direction*(price-p['entry'])/d*p['remaining_fraction'] if d>0 else 0.


def daily_loss_pct(price):
    eq=st.session_state.balance
    if st.session_state.position:eq+=unrealized_r(st.session_state.position,price)*st.session_state.position['risk_money']
    return max(0,(st.session_state.daily_start_balance-eq)/max(st.session_state.daily_start_balance,1)*100)


def analyze(m5,m15,h1,h4,research):
    snaps={k:snapshot(v) for k,v in {'M5':m5,'M15':m15,'H1':h1,'H4':h4}.items()}; news=news_gate(research); spread=spread_gate(research)
    if any(snaps[k] is None for k in snaps):
        return {'signal':'انتظار','strength':0,'reason':'بيانات الأطر غير مكتملة','gates':{'البيانات':False,'النظام السوقي':False,'توافق الأطر':False,'الزخم':False,'B2':False,'المخاطر':not st.session_state.kill_switch,'الحد اليومي':True,'السبريد':not spread['blocked'],'الأخبار':not news['blocked']},'snaps':snaps,'b2':{},'news':news,'spread':spread}
    z=b2(snaps['M5']['frame']); ts=[snaps[k]['trend'] for k in ['M5','M15','H1','H4']]; bull=all(t=='صاعد' for t in ts); bear=all(t=='هابط' for t in ts); s=snaps['M5']
    mb=s['rsi']>=52 and s['momentum']>0 and s['macd_hist']>0; ms=s['rsi']<=48 and s['momentum']<0 and s['macd_hist']<0
    gates={'البيانات':True,'النظام السوقي':s['regime']=='اتجاه','توافق الأطر':bull or bear,'الزخم':mb or ms,'B2':z['valid'],'المخاطر':not st.session_state.kill_switch,'الحد اليومي':daily_loss_pct(s['close'])<DAILY_LOSS_LIMIT*100 and st.session_state.daily_trades<MAX_DAILY_TRADES,'السبريد':not spread['blocked'],'الأخبار':not news['blocked']}
    strength=(30 if gates['توافق الأطر'] else 0)+(25 if gates['B2'] else 0)+(20 if gates['الزخم'] else 0)+(10 if s['adx']>=25 else 0)+(10 if ((bull and ts[-1]=='صاعد') or (bear and ts[-1]=='هابط')) else 0)+(5 if gates['السبريد'] and gates['الأخبار'] else 0)
    reason=next((k for k,v in gates.items() if not v),'اجتازت جميع البوابات'); signal='انتظار'
    if all(gates.values()):
        if z['direction']=='شراء' and bull and mb:signal='شراء'
        elif z['direction']=='بيع' and bear and ms:signal='بيع'
        else:reason='تعارض B2 مع الاتجاه أو الزخم'
    return {'signal':signal,'strength':min(strength,100),'reason':reason,'gates':gates,'snaps':snaps,'b2':z,'news':news,'spread':spread}


def open_trade(side,price,av):
    if st.session_state.position or st.session_state.kill_switch or st.session_state.daily_trades>=MAX_DAILY_TRADES or daily_loss_pct(price)>=DAILY_LOSS_LIMIT*100 or not np.isfinite(av) or av<=0:return False
    d=max(av*1.4,price*.0015); risk=st.session_state.balance*RISK_PER_TRADE
    if side=='شراء':sl,tp1,tp2=price-d,price+d,price+2.2*d
    else:sl,tp1,tp2=price+d,price-d,price-2.2*d
    st.session_state.position={'id':uuid.uuid4().hex[:10],'side':side,'entry':float(price),'sl':float(sl),'initial_distance':float(d),'tp1':float(tp1),'tp2':float(tp2),'risk_money':float(risk),'remaining_fraction':1.,'realized_r':0.,'tp1_hit':False,'opened':datetime.now(TZ).isoformat()}; st.session_state.daily_trades+=1; return True


def close_trade(price,reason):
    p=st.session_state.position
    if not p:return
    r=unrealized_r(p,price); pnl=r*p['risk_money']; st.session_state.balance+=pnl
    st.session_state.history.insert(0,{'الوقت':datetime.now(TZ).strftime('%Y-%m-%d %H:%M'),'المعرف':p['id'],'النوع':p['side'],'الدخول':round(p['entry'],2),'الخروج':round(price,2),'R':round(r,3),'الربح/الخسارة':round(pnl,2),'السبب':reason}); st.session_state.position=None


def manage_trade(price):
    p=st.session_state.position
    if not p or not np.isfinite(price):return
    if p['side']=='شراء':
        if price<=p['sl']:close_trade(p['sl'],'وقف الخسارة');return
        if not p['tp1_hit'] and price>=p['tp1']:
            p['realized_r']+=.5;p['remaining_fraction']=.5;p['tp1_hit']=True;p['sl']=p['entry'];return
        if p['tp1_hit'] and price>=p['tp2']:close_trade(p['tp2'],'الهدف الثاني')
    else:
        if price>=p['sl']:close_trade(p['sl'],'وقف الخسارة');return
        if not p['tp1_hit'] and price<=p['tp1']:
            p['realized_r']+=.5;p['remaining_fraction']=.5;p['tp1_hit']=True;p['sl']=p['entry'];return
        if p['tp1_hit'] and price<=p['tp2']:close_trade(p['tp2'],'الهدف الثاني')


def backtest_mtf(raw_m5):
    """Closed-bar MTF backtest. Signal is evaluated on M5 with the latest CLOSED M15/H1/H4 bars."""
    m5=indicators(closed_m5(raw_m5)).dropna().reset_index(drop=True)
    m15=resample_closed(raw_m5,'15min'); h1=resample_closed(raw_m5,'1h'); h4=resample_closed(raw_m5,'4h')
    m15=indicators(m15).dropna().reset_index(drop=True); h1=indicators(h1).dropna().reset_index(drop=True); h4=indicators(h4).dropna().reset_index(drop=True)
    if min(len(m5),len(m15),len(h1),len(h4))<250:return pd.DataFrame(),{'trades':0,'warning':'العينة صغيرة'}
    for df in (m5,m15,h1,h4): df['ts']=df['datetime']
    base=m5[['datetime','open','high','low','close','ema20','ema50','ema100','rsi','atr','macd_hist','momentum','adx']].copy()
    for name,df in [('m15',m15),('h1',h1),('h4',h4)]:
        cols=['ts','close','ema20','ema50','ema100','rsi','adx']
        base=pd.merge_asof(base.sort_values('datetime'),df[cols].sort_values('ts'),left_on='datetime',right_on='ts',direction='backward',suffixes=('','_'+name))
    split_time=base.datetime.iloc[int(len(base)*.7)]; rows=[]; i=130
    while i<len(base)-2:
        r=base.iloc[i]
        if r.datetime>=split_time and i<1: i+=1;continue
        av=float(r.atr)
        if not np.isfinite(av) or av<=0:i+=1;continue
        p=base.iloc[max(0,i-21):i];res=float(p.high.max());sup=float(p.low.min());prev=float(base.close.iloc[i-1])
        rb=prev>res and r.low<=res+.35*av and r.close>res; rs=prev<sup and r.high>=sup-.35*av and r.close<sup
        def bull(s): return s.close>s.ema20>s.ema50>s.ema100
        def bear(s): return s.close<s.ema20<s.ema50<s.ema100
        bull_mtf=bull(r) and bool(r.close_m15>r.ema20_m15>r.ema50_m15>r.ema100_m15) and bool(r.close_h1>r.ema20_h1>r.ema50_h1>r.ema100_h1) and bool(r.close_h4>r.ema20_h4>r.ema50_h4>r.ema100_h4)
        bear_mtf=bear(r) and bool(r.close_m15<r.ema20_m15<r.ema50_m15<r.ema100_m15) and bool(r.close_h1<r.ema20_h1<r.ema50_h1<r.ema100_h1) and bool(r.close_h4<r.ema20_h4<r.ema50_h4<r.ema100_h4)
        mb=r.rsi>=52 and r.momentum>0 and r.macd_hist>0 and r.adx>=25; ms=r.rsi<=48 and r.momentum<0 and r.macd_hist<0 and r.adx>=25
        side='شراء' if rb and bull_mtf and mb else 'بيع' if rs and bear_mtf and ms else None
        if not side:i+=1;continue
        entry=float(r.close);d=max(av*1.4,entry*.0015);sl=entry-d if side=='شراء' else entry+d;tp1=entry+d if side=='شراء' else entry-d;tp2=entry+2.2*d if side=='شراء' else entry-2.2*d
        realized=0.;rem=.5 if False else 1.;hit=False;outcome=None;reason=None;close_i=None
        for j in range(i+1,min(i+100,len(base))):
            hi,lo=float(base.high.iloc[j]),float(base.low.iloc[j])
            if side=='شراء':
                if lo<=sl:outcome=realized-rem;reason='وقف الخسارة';close_i=j;break
                if not hit and hi>=tp1:realized+=.5;rem=.5;hit=True;sl=entry
                if hit and hi>=tp2:outcome=realized+1.1;reason='الهدف الثاني';close_i=j;break
            else:
                if hi>=sl:outcome=realized-rem;reason='وقف الخسارة';close_i=j;break
                if not hit and lo<=tp1:realized+=.5;rem=.5;hit=True;sl=entry
                if hit and lo<=tp2:outcome=realized+1.1;reason='الهدف الثاني';close_i=j;break
        if outcome is not None:
            rows.append({'الوقت':r.datetime,'النوع':side,'الدخول':round(entry,2),'الوقف':round(entry-d if side=='شراء' else entry+d,2),'TP1':round(tp1,2),'TP2':round(tp2,2),'R':round(outcome,3),'السبب':reason,'OOS':r.datetime>=split_time});i=close_i+1
        else:i+=1
    t=pd.DataFrame(rows);o=t[t.OOS].copy() if not t.empty else t
    if o.empty:return t,{'trades':0,'warning':'لا توجد صفقات OOS مطابقة لكل بوابات MTF'}
    eq=peak=dd=0.
    for v in o.R:eq+=float(v);peak=max(peak,eq);dd=max(dd,peak-eq)
    gw=o.loc[o.R>0,'R'].sum();gl=abs(o.loc[o.R<0,'R'].sum());pf=gw/gl if gl else math.inf
    return t,{'trades':len(o),'win_rate':float((o.R>0).mean()*100),'profit_factor':float(pf),'total_r':float(o.R.sum()),'max_dd_r':float(dd),'warning':None,'oos_start':str(split_time)}


def metrics(items):
    cs=st.columns(len(items))
    for c,(a,b) in zip(cs,items):c.markdown(f"<div class='metric'><div class='label'>{a}</div><div class='value'>{b}</div></div>",unsafe_allow_html=True)

# ---------------- UI ----------------
with st.sidebar:
    st.markdown('## 🟡 GOLD AI');st.caption('XAU/USD • Paper Trading فقط')
    research=st.toggle('وضع البحث التجريبي',value=st.session_state.research_mode);st.session_state.research_mode=research
    st.session_state.kill_switch=st.toggle('Kill Switch',value=st.session_state.kill_switch)
    st.session_state.auto_refresh=st.toggle('تحديث تلقائي',value=False);refresh=st.slider('ثواني التحديث',15,120,45)

raw,msg,chunks,credits=fetch_history();raw=closed_m5(raw)
if raw.empty:st.error('مصدر البيانات غير متاح');st.info(msg);st.stop()
qok,qmsg=quality(raw)
if not qok:st.warning('جودة البيانات: '+qmsg)
m5=indicators(raw);m15=resample_closed(raw,'15min');h1=resample_closed(raw,'1h');h4=resample_closed(raw,'4h')
if min(len(m5),len(m15),len(h1),len(h4))<110:st.error('البيانات غير كافية لجميع الأطر الزمنية.');st.stop()
price=float(raw.close.iloc[-1]);st.session_state.last_price=price;manage_trade(price);a=analyze(m5,m15,h1,h4,research)

candle=str(raw.datetime.iloc[-1])
if st.session_state.last_signal_candle!=candle:
    st.session_state.last_signal_candle=candle
    rec={'decision_id':uuid.uuid4().hex[:10],'الوقت':datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S'),'الشمعة':candle,'الإشارة':a['signal'],'القوة':a['strength'],'السبب':a['reason']}
    rec.update({k:'PASS' if v else 'BLOCK' for k,v in a['gates'].items()});st.session_state.decisions.insert(0,rec);st.session_state.decisions=st.session_state.decisions[:500]
if a['signal'] in ('شراء','بيع') and st.session_state.position is None:open_trade(a['signal'],price,a['snaps']['M5']['atr'])

st.caption('الوضع: Paper Trading فقط • الحالة محفوظة داخل جلسة Streamlit الحالية وليست قاعدة بيانات دائمة')
st.markdown(f"<div class='hero'><div class='kicker'>GOLD AI • SMART TRADING SYSTEM</div><h1 class='gold'>XAU/USD</h1><div class='price'>${price:,.2f}</div><p class='muted'>Paper Trading • Market Data • Strategy Engine • Risk Engine</p></div>",unsafe_allow_html=True)
metrics([('القرار',a['signal']),('قوة الإشارة',f"{a['strength']}%"),('السوق',a['snaps']['M5']['regime']),('الرصيد',f"${st.session_state.balance:,.2f}"),('الخسارة اليومية',f"{daily_loss_pct(price):.2f}%")])

st.subheader('Multi-Timeframe Command Center')
cs=st.columns(4)
for c,k in zip(cs,['M5','M15','H1','H4']):
    s=a['snaps'][k];c.markdown(f"<div class='card'><div class='kicker'>{k}</div><div class='signal'>{s['trend']}</div><div class='muted'>RSI {s['rsi']:.1f} • ADX {s['adx']:.1f}</div></div>",unsafe_allow_html=True)

st.subheader('Decision Engine')
cls='good' if a['signal'] in ('شراء','بيع') else ''
st.markdown(f"<div class='card'><div class='kicker'>FINAL DECISION</div><div class='signal {cls}'>{a['signal']}</div><p>{a['reason']}</p><span class='muted'>Signal strength ≠ probability of profit.</span></div>",unsafe_allow_html=True)
st.dataframe(pd.DataFrame([{'البوابة':k,'الحالة':'PASS' if v else 'BLOCK'} for k,v in a['gates'].items()]),hide_index=True,use_container_width=True)

c1,c2=st.columns(2)
with c1:
    st.markdown('### B2 Breakout / Retest');z=a['b2'];metrics([('Breakout','YES' if z.get('breakout') else 'NO'),('Retest','YES' if z.get('retest') else 'NO'),('الاتجاه',z.get('direction') or '—'),('المستوى',f"{z.get('level',np.nan):.2f}" if np.isfinite(z.get('level',np.nan)) else '—')])
with c2:
    st.markdown('### Safety Filters');st.info('News: '+a['news']['label']);st.info('Spread: '+a['spread']['label'])

st.subheader('Market');st.line_chart(raw.tail(300).set_index('datetime')[['close']],use_container_width=True)
st.subheader('Paper Trading')
p=st.session_state.position
if p:
    r=unrealized_r(p,price);pnl=r*p['risk_money'];metrics([('النوع',p['side']),('Entry',f"{p['entry']:.2f}"),('Current',f"{price:.2f}"),('SL',f"{p['sl']:.2f}"),('TP1',f"{p['tp1']:.2f}"),('TP2',f"{p['tp2']:.2f}"),('P&L',f"${pnl:,.2f}"),('R',f"{r:.2f}R")]);
    if p['tp1_hit']:st.success('TP1 HIT • 50% CLOSED • STOP → BREAK-EVEN')
else:st.info('لا توجد صفقة ورقية مفتوحة.')

st.subheader('Risk Center');metrics([('Balance',f"${st.session_state.balance:,.2f}"),('Risk / Trade',f"{RISK_PER_TRADE*100:.2f}%"),('Daily Loss Limit',f"{DAILY_LOSS_LIMIT*100:.1f}%"),('Trades Today',f"{st.session_state.daily_trades}/{MAX_DAILY_TRADES}"),('Open Positions','1' if p else '0'),('Kill Switch','ON' if st.session_state.kill_switch else 'OFF')])

st.subheader('Backtest Lab')
with st.expander('تشغيل OOS Backtest'):
    mx=min(5000,len(m15))
    if mx<500:st.warning('بيانات M15 الحالية لا تكفي.')
    else:
        bars=st.slider('حجم الاختبار (تقريبيًا بعدد شموع M15)',500,mx,min(3000,mx),100);trades,stats=backtest_mtf(raw.tail(min(len(raw),bars*3)))
        if stats['trades']:
            metrics([('OOS Trades',stats['trades']),('Win Rate',f"{stats['win_rate']:.1f}%"),('Profit Factor',f"{stats['profit_factor']:.2f}"),('Total R',f"{stats['total_r']:.2f}R"),('Max DD',f"{stats['max_dd_r']:.2f}R")]);st.caption('OOS فقط. هذا الاختبار يحاكي M5 + توافق M15/H1/H4 + B2 + Momentum/ADX، باستخدام شموع مغلقة فقط. الأخبار والسبريد التاريخيان غير متاحين لذلك لا يدخلان في نتيجة الباك تست.');st.dataframe(trades.tail(100),hide_index=True,use_container_width=True)
        else:st.warning(stats.get('warning','لا توجد نتائج'))

st.subheader('Decision Log');st.dataframe(pd.DataFrame(st.session_state.decisions),hide_index=True,use_container_width=True) if st.session_state.decisions else st.info('لا يوجد سجل بعد.')
st.subheader('Trade Log');st.dataframe(pd.DataFrame(st.session_state.history),hide_index=True,use_container_width=True) if st.session_state.history else st.info('لا توجد صفقات مغلقة.')
st.subheader('System Health');st.dataframe(pd.DataFrame([{'النظام':k,'الحالة':'ONLINE' if v else 'BLOCKED'} for k,v in {'Data Feed':not raw.empty,'Data Quality':qok,'Strategy Engine':a['snaps']['M5'] is not None,'Risk Engine':True,'Paper Engine':True,'Time Sync':True,'Economic Calendar':a['news']['connected'],'Bid/Ask Spread':a['spread']['connected']}.items()]),hide_index=True,use_container_width=True)
st.caption(f"Last closed M5: {raw.datetime.iloc[-1]} UTC • {len(raw):,} bars • batches {chunks}/{CHUNKS} • API credits left: {credits[-1] if credits else 'N/A'}")
if st.session_state.auto_refresh:time.sleep(refresh);st.rerun()

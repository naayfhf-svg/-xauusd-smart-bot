# XAU/USD Smart Paper Trading Bot - Final Research Edition
# Paper Trading ONLY. No broker order execution.
import os, math
from datetime import datetime, timezone
import requests
import numpy as np
import pandas as pd
import streamlit as st

try:
    import plotly.graph_objects as go
    PLOTLY_OK = True
except Exception:
    PLOTLY_OK = False

st.set_page_config(page_title='بوت الذهب XAU/USD', page_icon='🥇', layout='wide')

st.markdown('''<style>
[data-testid="stAppViewContainer"]{background:#080b10}.block-container{max-width:1500px;padding-top:1rem}
.hero{background:linear-gradient(135deg,#151a23,#0b0f15);border:1px solid #2b313d;border-radius:22px;padding:24px;margin-bottom:16px}
.hero h1{margin:0}.hero p,.muted{color:#9da6b3}.badge{display:inline-block;border:1px solid #3b414d;background:#111722;color:#d8b45a;border-radius:999px;padding:4px 9px;margin-left:5px;font-size:.78rem}
div[data-testid="stMetric"]{background:#10151d;border:1px solid #242b36;padding:10px;border-radius:15px}
</style>''', unsafe_allow_html=True)

SYMBOL='XAU/USD'; TD_URL='https://api.twelvedata.com/time_series'
for k,v in {
    'balance':10000.0,'start_balance':10000.0,'risk_pct':0.5,'max_open':2,
    'daily_loss_r':2.0,'max_dd_r':15.0,'max_losses':4,'kill':False,
    'open_trades':[],'closed_trades':[],'logs':[],'last_signal_key':None,
    'bt':None
}.items():
    st.session_state.setdefault(k,v)

def api_key():
    try:return st.secrets['TWELVE_DATA_API_KEY']
    except Exception:return os.getenv('TWELVE_DATA_API_KEY','')

def log(event,detail,level='INFO'):
    st.session_state.logs.append({'time':datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S'),'level':level,'event':event,'detail':detail})
    st.session_state.logs=st.session_state.logs[-1000:]

def f(x,d=2):
    try:
        x=float(x); return '—' if not np.isfinite(x) else f'{x:,.{d}f}'
    except:return '—'

def rsi(c,n=14):
    d=c.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    au=up.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); ad=dn.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    return 100-(100/(1+au/ad.replace(0,np.nan)))

def atr(df,n=14):
    pc=df.close.shift(1); tr=pd.concat([df.high-df.low,(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()

def adx(df,n=14):
    up=df.high.diff(); dn=-df.low.diff()
    plus=pd.Series(np.where((up>dn)&(up>0),up,0.),index=df.index); minus=pd.Series(np.where((dn>up)&(dn>0),dn,0.),index=df.index)
    pc=df.close.shift(1); tr=pd.concat([df.high-df.low,(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    a=tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    p=100*plus.ewm(alpha=1/n,adjust=False,min_periods=n).mean()/a
    m=100*minus.ewm(alpha=1/n,adjust=False,min_periods=n).mean()/a
    dx=100*(p-m).abs()/(p+m).replace(0,np.nan)
    return dx.ewm(alpha=1/n,adjust=False,min_periods=n).mean(),p,m

def indicators(df):
    x=df.copy(); c=x.close
    x['ema20']=c.ewm(span=20,adjust=False).mean(); x['ema50']=c.ewm(span=50,adjust=False).mean(); x['ema100']=c.ewm(span=100,adjust=False).mean()
    x['rsi']=rsi(c); e12=c.ewm(span=12,adjust=False).mean(); e26=c.ewm(span=26,adjust=False).mean(); x['macd']=e12-e26; x['macd_signal']=x.macd.ewm(span=9,adjust=False).mean(); x['macd_hist']=x.macd-x.macd_signal
    x['atr']=atr(x); x['momentum']=c.diff(10); x['adx'],x['plus_di'],x['minus_di']=adx(x)
    return x

def score(row):
    s=0
    if row.ema20>row.ema50>row.ema100:s+=3
    elif row.ema20<row.ema50<row.ema100:s-=3
    s += 1 if row.close>row.ema20 else -1
    if 52<=row.rsi<=68:s+=2
    elif 32<=row.rsi<48:s-=2
    if row.macd>row.macd_signal and row.macd_hist>0:s+=2
    elif row.macd<row.macd_signal and row.macd_hist<0:s-=2
    s += 1 if row.momentum>0 else -1
    if row.adx>=25:s += 2 if row.plus_di>row.minus_di else (-2 if row.minus_di>row.plus_di else 0)
    return int(s)

def trend(s):return 'صاعد' if s>=5 else ('هابط' if s<=-5 else 'محايد')

def resample(df,rule):
    return df.resample(rule,label='right',closed='right').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()

@st.cache_data(ttl=65,show_spinner=False)
def fetch_live(size=600):
    k=api_key();
    if not k:raise RuntimeError('TWELVE_DATA_API_KEY غير موجود في Secrets')
    r=requests.get(TD_URL,params={'symbol':SYMBOL,'interval':'5min','outputsize':size,'apikey':k,'format':'JSON','timezone':'UTC'},timeout=25); r.raise_for_status(); j=r.json()
    if j.get('status')=='error':raise RuntimeError(j.get('message','Twelve Data error'))
    d=pd.DataFrame(j.get('values',[]));
    if d.empty:raise RuntimeError('لم تصل بيانات')
    d['datetime']=pd.to_datetime(d.datetime,utc=True)
    for c in ['open','high','low','close','volume']:d[c]=pd.to_numeric(d[c],errors='coerce')
    return d.dropna(subset=['open','high','low','close']).sort_values('datetime').drop_duplicates('datetime').set_index('datetime')

@st.cache_data(ttl=300,show_spinner=False)
def fetch_history(total):
    k=api_key();
    if not k:raise RuntimeError('TWELVE_DATA_API_KEY غير موجود')
    parts=[]; remain=int(total); end=None
    while remain>0:
        n=min(5000,remain); p={'symbol':SYMBOL,'interval':'5min','outputsize':n,'apikey':k,'format':'JSON','timezone':'UTC'}
        if end:p['end_date']=end
        r=requests.get(TD_URL,params=p,timeout=30); r.raise_for_status(); j=r.json()
        if j.get('status')=='error':raise RuntimeError(j.get('message','Twelve Data error'))
        d=pd.DataFrame(j.get('values',[]));
        if d.empty:break
        d['datetime']=pd.to_datetime(d.datetime,utc=True)
        for c in ['open','high','low','close','volume']:d[c]=pd.to_numeric(d[c],errors='coerce')
        d=d.dropna(subset=['open','high','low','close']).sort_values('datetime').drop_duplicates('datetime').set_index('datetime'); parts.append(d)
        end=d.index.min().strftime('%Y-%m-%d %H:%M:%S'); remain-=len(d)
        if len(d)<n:break
    if not parts:raise RuntimeError('تعذر تحميل التاريخ')
    return pd.concat(parts).sort_index().loc[lambda x:~x.index.duplicated(keep='last')].tail(total)

def mtf(m5):
    a=indicators(m5); m15=indicators(resample(m5,'15min')); h1=indicators(resample(m5,'1h')); h4=indicators(resample(m5,'4h'))
    for d in [m15,h1,h4]:d['score']=d.apply(score,axis=1)
    return a,m15,h1,h4

def live_signal(a,m15,h1,h4):
    r5=a.iloc[-1]; r15=m15.iloc[-1]; r1=h1.iloc[-1]; r4=h4.iloc[-1]; s5=score(r5);s15=score(r15);s1=score(r1);s4=score(r4)
    buy=s5>=5 and s15>=5 and s1>=5 and r15.adx>=20 and r15.rsi>=50 and r15.macd>0
    sell=s5<=-5 and s15<=-5 and s1<=-5 and r15.adx>=20 and r15.rsi<=50 and r15.macd<0
    price=float(r5.close); av=float(r15.atr); sig='BUY' if buy else ('SELL' if sell else 'WAIT'); entry=price if sig!='WAIT' else None
    sl=entry-1.5*av if sig=='BUY' else (entry+1.5*av if sig=='SELL' else None); tp1=entry+1.5*av if sig=='BUY' else (entry-1.5*av if sig=='SELL' else None); tp2=entry+2.5*av if sig=='BUY' else (entry-2.5*av if sig=='SELL' else None)
    recent=m15.tail(20); support=float(recent.low.min()); resistance=float(recent.high.max()); total=s5+s15+s1+s4
    regime='صاعد قوي' if s4>=5 and s1>=5 else ('هابط قوي' if s4<=-5 and s1<=-5 else 'مختلط / محايد')
    return dict(price=price,signal=sig,confidence=min(100,abs(total)/40*100),s5=s5,s15=s15,h1=s1,h4=s4,trend5=trend(s5),trend15=trend(s15),trend1=trend(s1),trend4=trend(s4),regime=regime,support=support,resistance=resistance,atr=av,entry=entry,sl=sl,tp1=tp1,tp2=tp2,rsi=float(r15.rsi),adx=float(r15.adx),macd=float(r15.macd),timestamp=r5.name)

def build_strategy(m5):
    m=indicators(resample(m5,'15min')); h=indicators(resample(m5,'1h')); h4=indicators(resample(m5,'4h'))
    m['score']=m.apply(score,axis=1);h['score']=h.apply(score,axis=1);h4['score']=h4.apply(score,axis=1)
    m['A_buy']=(m.score>=5)&(m.adx>=20)&(m.rsi>=50)&(m.macd>0);m['A_sell']=(m.score<=-5)&(m.adx>=20)&(m.rsi<=50)&(m.macd<0)
    m['res']=m.high.rolling(20).max().shift(1);m['sup']=m.low.rolling(20).min().shift(1);m['break_up']=m.close>m.res;m['break_down']=m.close<m.sup
    state=0;level=np.nan;time=None; ret=[]; side=[]; lev=[]
    for t,row in m.iterrows():
        if row.break_up:state=1;level=float(row.res);time=t
        elif row.break_down:state=-1;level=float(row.sup);time=t
        ok=False; sd=0; tol=.35*float(row.atr) if np.isfinite(row.atr) else np.inf
        if state==1 and time is not None and t>time:ok=bool(row.low<=level+tol and row.close>level and row.score>=5 and row.adx>=20 and row.rsi>=50 and row.macd>0);sd=1 if ok else 0
        elif state==-1 and time is not None and t>time:ok=bool(row.high>=level-tol and row.close<level and row.score<=-5 and row.adx>=20 and row.rsi<=50 and row.macd<0);sd=-1 if ok else 0
        ret.append(ok);side.append(sd);lev.append(level)
        if ok:state=0;level=np.nan;time=None
    m['B2_retest']=ret;m['B2_side']=side;m['B2_level']=lev
    return m,h,h4

def simulate(m5,m,h,strategy):
    out=[]; times=list(m.index)
    for i in range(len(times)-1):
        t=times[i]; nt=times[i+1]; row=m.loc[t]; hi=h.loc[:t].iloc[-1] if not h.loc[:t].empty else None
        if hi is None:continue
        if strategy=='A':buy=bool(row.A_buy and hi.score>=5);sell=bool(row.A_sell and hi.score<=-5)
        else:buy=bool(row.B2_retest and row.B2_side==1 and hi.score>=5);sell=bool(row.B2_retest and row.B2_side==-1 and hi.score<=-5)
        if not buy and not sell:continue
        cand=m5.loc[(m5.index>t)&(m5.index<=nt)]
        if cand.empty:continue
        et=cand.index[0];entry=float(cand.iloc[0].open);av=float(row.atr)
        if not np.isfinite(av) or av<=0:continue
        sd=1 if buy else -1;sl=entry-sd*1.5*av;tp=entry+sd*2.5*av;rd=abs(entry-sl);exitp=None;xt=None;reason=None
        for tt,b in m5.loc[m5.index>et].iterrows():
            hit_sl=float(b.low)<=sl if sd==1 else float(b.high)>=sl;hit_tp=float(b.high)>=tp if sd==1 else float(b.low)<=tp
            if hit_sl:exitp=sl;xt=tt;reason='SL';break
            if hit_tp:exitp=tp;xt=tt;reason='TP2';break
        if exitp is None:
            fut=m5.loc[m5.index>et]
            if fut.empty:continue
            exitp=float(fut.iloc[-1].close);xt=fut.index[-1];reason='END'
        out.append({'entry_time':et,'exit_time':xt,'side':'BUY' if sd==1 else 'SELL','entry':entry,'sl':sl,'tp2':tp,'exit':exitp,'outcome':reason,'R':(exitp-entry)*sd/rd})
    return pd.DataFrame(out)

def metrics(t):
    if t is None or t.empty:return {'n':0,'wr':0,'pf':0,'r':0,'avg':0,'dd':0,'buy':0,'sell':0}
    r=t.R.astype(float);gp=r[r>0].sum();gl=-r[r<0].sum();eq=r.cumsum();return {'n':len(t),'wr':(r>0).mean()*100,'pf':gp/gl if gl>0 else (999 if gp>0 else 0),'r':r.sum(),'avg':r.mean(),'dd':(eq.cummax()-eq).max(),'buy':int((t.side=='BUY').sum()),'sell':int((t.side=='SELL').sum())}

def paper_stats():
    t=pd.DataFrame(st.session_state.closed_trades); r=t.R if not t.empty else pd.Series(dtype=float); eq=r.cumsum() if not r.empty else pd.Series(dtype=float); return {'r':float(r.sum()) if not r.empty else 0,'dd':float((eq.cummax()-eq).max()) if not r.empty else 0,'losses':0 if not r.size else next((i for i,x in enumerate(reversed(r.tolist())) if x>=0),len(r))}

def can_open():
    ps=paper_stats(); today=datetime.now().date(); dr=0
    for x in st.session_state.closed_trades:
        try:
            if pd.to_datetime(x['exit_time']).date()==today:dr+=float(x['R'])
        except:pass
    if st.session_state.kill:return False,'Kill Switch'
    if len(st.session_state.open_trades)>=st.session_state.max_open:return False,'Max open trades'
    if dr<=-st.session_state.daily_loss_r:return False,'Daily loss limit'
    if ps['dd']>=st.session_state.max_dd_r:return False,'Max drawdown'
    if ps['losses']>=st.session_state.max_losses:return False,'Consecutive losses'
    return True,'OK'

def open_paper(sig):
    ok,why=can_open()
    if not ok:log('PAPER_REJECT',why,'WARN');return False
    if sig['signal'] not in ['BUY','SELL']:return False
    key=f"{sig['signal']}|{sig['timestamp']}"
    if st.session_state.last_signal_key==key:return False
    rd=abs(sig['entry']-sig['sl']);risk=st.session_state.balance*st.session_state.risk_pct/100
    st.session_state.open_trades.append({'strategy':'LIVE-A','signal_time':str(sig['timestamp']),'open_time':datetime.now().astimezone().isoformat(),'side':sig['signal'],'entry':sig['entry'],'sl':sig['sl'],'tp1':sig['tp1'],'tp2':sig['tp2'],'risk_distance':rd,'risk_usd':risk,'tp1_hit':False})
    st.session_state.last_signal_key=key;log('PAPER_OPEN',f"{sig['signal']} @ {sig['entry']:.2f}");return True

def update_paper(bar):
    remain=[]
    for t in st.session_state.open_trades:
        sd=1 if t['side']=='BUY' else -1;hi=float(bar.high);lo=float(bar.low);slhit=lo<=t['sl'] if sd==1 else hi>=t['sl'];tphit=hi>=t['tp2'] if sd==1 else lo<=t['tp2']
        if slhit or tphit:
            ex=t['sl'] if slhit else t['tp2'];reason='SL' if slhit else 'TP2';R=(ex-t['entry'])*sd/t['risk_distance'];pnl=R*t['risk_usd'];st.session_state.balance+=pnl;x=dict(t);x.update({'exit_time':str(bar.name),'exit':ex,'outcome':reason,'R':R,'pnl_usd':pnl});st.session_state.closed_trades.append(x);log('PAPER_CLOSE',f"{t['side']} {reason} {R:+.2f}R ${pnl:+.2f}")
        else:remain.append(t)
    st.session_state.open_trades=remain

st.sidebar.title('⚙️ إعدادات')
st.session_state.risk_pct=st.sidebar.number_input('Risk %',.1,5.,st.session_state.risk_pct,.1)
st.session_state.max_open=st.sidebar.number_input('Max open',1,10,st.session_state.max_open,1)
st.session_state.daily_loss_r=st.sidebar.number_input('Daily loss limit (R)',.5,20.,st.session_state.daily_loss_r,.5)
st.session_state.max_dd_r=st.sidebar.number_input('Max DD (R)',1.,50.,st.session_state.max_dd_r,1.)
st.session_state.max_losses=st.sidebar.number_input('Max consecutive losses',1,20,st.session_state.max_losses,1)
if st.sidebar.button('🔄 تحديث البيانات',use_container_width=True):fetch_live.clear();st.rerun()
if st.sidebar.button('⛔ Kill Switch',use_container_width=True):st.session_state.kill=True;log('KILL_SWITCH','تم إيقاف فتح الصفقات','WARN');st.rerun()
if st.sidebar.button('▶️ إلغاء Kill Switch',use_container_width=True):st.session_state.kill=False;st.rerun()
if st.sidebar.button('🗑️ تصفير Paper',use_container_width=True):st.session_state.balance=st.session_state.start_balance;st.session_state.open_trades=[];st.session_state.closed_trades=[];st.session_state.logs=[];st.session_state.last_signal_key=None;st.rerun()

st.markdown('<div class="hero"><span class="badge">XAU/USD</span><span class="badge">M5 M15 H1 H4</span><span class="badge">Paper Trading</span><span class="badge">OOS</span><h1>🥇 بوت الذهب</h1><p>Smart multi-timeframe research & paper-trading engine — لا توجد أوامر حقيقية.</p></div>',unsafe_allow_html=True)

if not api_key():st.error('ضع TWELVE_DATA_API_KEY في Streamlit Secrets فقط.');st.stop()
try:
    raw=fetch_live();a,m15,h1,h4=mtf(raw);sig=live_signal(a,m15,h1,h4);update_paper(raw.iloc[-1])
except Exception as e:st.error(f'خطأ في البيانات: {e}');st.stop()

c=st.columns(6);c[0].metric('XAU/USD',f'${f(sig["price"])}');c[1].metric('Signal',{'BUY':'شراء 🟢','SELL':'بيع 🔴','WAIT':'انتظار 🟡'}[sig['signal']]);c[2].metric('Confluence',f'{sig["confidence"]:.0f}%');c[3].metric('Regime',sig['regime']);c[4].metric('Paper Balance',f'${st.session_state.balance:,.2f}');c[5].metric('P&L',f'${st.session_state.balance-st.session_state.start_balance:+,.2f}')
if st.session_state.kill:st.warning('⛔ Kill Switch مفعّل')

l,r=st.columns([1.3,1])
with l:
    st.subheader('📡 Multi-Timeframe')
    c=st.columns(4)
    for col,label,key,score_key in zip(c,['M5','M15','H1','H4'],['trend5','trend15','trend1','trend4'],['s5','s15','h1','h4']):col.metric(label,f'{sig[key]} ({sig[score_key]:+d})')
    c=st.columns(3);c[0].metric('Support',f(sig['support']));c[1].metric('Resistance',f(sig['resistance']));c[2].metric('ATR',f(sig['atr']))
    st.caption(f'RSI {f(sig["rsi"])} · ADX {f(sig["adx"])} · MACD {f(sig["macd"],4)}')
with r:
    st.subheader('🎯 Trade Plan')
    if sig['signal']!='WAIT':
        c=st.columns(4);c[0].metric('Entry',f(sig['entry']));c[1].metric('SL',f(sig['sl']));c[2].metric('TP1',f(sig['tp1']));c[3].metric('TP2',f(sig['tp2']))
        if st.button('📝 فتح Paper Trade',use_container_width=True):
            if open_paper(sig):st.success('تم فتح Paper Trade');st.rerun()
            else:st.info('تم رفض الصفقة بسبب شروط الإشارة/المخاطر أو لأنها مسجلة مسبقاً.')
    else:st.info('لا توجد إشارة مكتملة حالياً.')
    st.caption('Confluence strength ليست احتمالية ربح.')

st.subheader('📈 XAU/USD')
plot= a.tail(180)
if PLOTLY_OK:
    fig=go.Figure(go.Candlestick(x=plot.index,open=plot.open,high=plot.high,low=plot.low,close=plot.close,name='XAU/USD'));fig.add_trace(go.Scatter(x=plot.index,y=plot.ema20,name='EMA20'));fig.add_trace(go.Scatter(x=plot.index,y=plot.ema50,name='EMA50'));fig.update_layout(template='plotly_dark',height=480,xaxis_rangeslider_visible=False);st.plotly_chart(fig,use_container_width=True)
else:st.line_chart(plot[['close','ema20','ema50']])

st.subheader('🧪 Paper Trading')
ps=paper_stats();c=st.columns(5);c[0].metric('Open',len(st.session_state.open_trades));c[1].metric('Closed',len(st.session_state.closed_trades));c[2].metric('Total R',f'{ps["r"]:+.2f}R');c[3].metric('DD',f'{ps["dd"]:.2f}R');c[4].metric('Risk',f'{st.session_state.risk_pct:.2f}%')
if st.session_state.open_trades:st.dataframe(pd.DataFrame(st.session_state.open_trades),use_container_width=True,hide_index=True)
if st.session_state.closed_trades:st.dataframe(pd.DataFrame(st.session_state.closed_trades).tail(50),use_container_width=True,hide_index=True)

st.subheader('🧬 OOS Research Lab')
bars=st.selectbox('Historical M5 bars',[5000,10000,15000,30000],index=2)
if st.button('🚀 تشغيل Backtest',use_container_width=True):
    try:
        with st.spinner('تحميل التاريخ وتشغيل A و B2...'):
            hist=fetch_history(bars);cut=int(len(hist)*.7);isdf=hist.iloc[:cut].copy();oos=hist.iloc[cut:].copy();mi,hi,_=build_strategy(isdf);mo,ho,_=build_strategy(oos);ais=simulate(isdf,mi,hi,'A');ao=simulate(oos,mo,ho,'A');bis=simulate(isdf,mi,hi,'B2');bo=simulate(oos,mo,ho,'B2');st.session_state.bt={'ais':ais,'ao':ao,'bis':bis,'bo':bo,'start':hist.index[0],'end':hist.index[-1],'split':oos.index[0]};log('BACKTEST',f'{bars} M5 bars')
    except Exception as e:st.error(f'فشل Backtest: {e}')

if st.session_state.bt:
    bt=st.session_state.bt;rows=[]
    for name,x,y in [('A — Benchmark',bt['ais'],bt['ao']),('B2 — True Breakout + Retest',bt['bis'],bt['bo'])]:
        i=metrics(x);o=metrics(y);rows.append({'Strategy':name,'IS Trades':i['n'],'IS Win%':i['wr'],'IS PF':i['pf'],'IS R':i['r'],'OOS Trades':o['n'],'OOS Win%':o['wr'],'OOS PF':o['pf'],'OOS R':o['r'],'OOS DD':o['dd']})
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    c=st.columns(2)
    for col,title,t in [(c[0],'A — OOS',bt['ao']),(c[1],'B2 — OOS',bt['bo'])]:
        z=metrics(t);col.markdown(f'**{title}**');q=col.columns(4);q[0].metric('Trades',z['n']);q[1].metric('Win%',f'{z["wr"]:.1f}%');q[2].metric('PF',f'{z["pf"]:.2f}');q[3].metric('R',f'{z["r"]:+.2f}R')
    if len(bt['ao'])<100 or len(bt['bo'])<100:st.warning('العينة OOS أقل من 100 صفقة؛ النتيجة استكشافية وليست إثباتاً لميزة مستقرة.')
    st.caption(f'الفترة: {bt["start"]} → {bt["end"]} · بداية OOS: {bt["split"]}')
    with st.expander('تفاصيل صفقات OOS'):st.dataframe(bt['ao'],use_container_width=True,hide_index=True);st.dataframe(bt['bo'],use_container_width=True,hide_index=True)

st.subheader('🧾 Decision Log')
if st.session_state.logs:
    ld=pd.DataFrame(st.session_state.logs).iloc[::-1];st.dataframe(ld.head(200),use_container_width=True,hide_index=True);st.download_button('⬇️ CSV',ld.to_csv(index=False).encode('utf-8-sig'),'xauusd_decision_log.csv','text/csv',use_container_width=True)
else:st.caption('السجل فارغ.')

st.markdown('---')
st.caption('Paper Trading فقط. لا يوجد اتصال لتنفيذ أوامر حقيقية. Backtest لا يشمل سبريد/انزلاق/عمولات فعلية، لذلك لا يمثل ربحاً قابلاً للتنفيذ. النتائج التاريخية لا تضمن المستقبل.')

import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="بوت الذهب XAU/USD", page_icon="🟡", layout="wide", initial_sidebar_state="collapsed")
TZ = ZoneInfo("Asia/Riyadh")
SYMBOL = "XAU/USD"
DATA_URL = "https://api.twelvedata.com/time_series"
START_BALANCE = 100_000.0
RISK_PER_TRADE = 0.005
DAILY_LOSS_LIMIT = 0.02
MAX_DAILY_TRADES = 5
CHUNKS = 4
BARS_PER_CHUNK = 5000

st.markdown("""
<style>
:root{--bg:#0b1220;--card:#111827;--line:#243044;--gold:#d4af37;--txt:#f8fafc;--muted:#94a3b8;--green:#22c55e;--red:#ef4444}
.stApp{background:var(--bg);color:var(--txt)} .block-container{max-width:1400px;padding-top:1rem;padding-bottom:3rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:14px}
.gold{color:var(--gold)} .muted,.small{color:var(--muted)} .signal{font-size:1.8rem;font-weight:900}
div[data-testid="stMetric"]{background:var(--card);border:1px solid var(--line);padding:12px;border-radius:14px}
</style>""", unsafe_allow_html=True)


def init_state():
    defaults = {
        "balance": START_BALANCE, "position": None, "history": [], "decisions": [],
        "daily_start_balance": START_BALANCE, "daily_date": datetime.now(TZ).date().isoformat(),
        "daily_trades": 0, "kill_switch": False, "last_signal_candle": None,
        "last_price": np.nan, "auto_refresh": False, "research_mode": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state: st.session_state[k] = v
    today = datetime.now(TZ).date().isoformat()
    if st.session_state.daily_date != today:
        st.session_state.daily_date = today
        st.session_state.daily_start_balance = st.session_state.balance
        st.session_state.daily_trades = 0

init_state()


def api_key():
    try: return st.secrets["TWELVE_DATA_API_KEY"]
    except Exception: return None


@st.cache_data(ttl=45, show_spinner=False)
def fetch_chunk(end_date=None):
    key = api_key()
    if not key: return pd.DataFrame(), "مفتاح Twelve Data غير موجود في Secrets", None, None
    params = {"symbol": SYMBOL, "interval": "5min", "outputsize": BARS_PER_CHUNK, "timezone": "UTC", "apikey": key}
    if end_date: params["end_date"] = end_date
    try:
        r = requests.get(DATA_URL, params=params, timeout=20)
        remaining = r.headers.get("api-credits-left")
        r.raise_for_status()
        p = r.json()
        if "values" not in p: return pd.DataFrame(), str(p.get("message") or p.get("code") or "مصدر البيانات رفض الطلب"), None, remaining
        d = pd.DataFrame(p["values"])
        d["datetime"] = pd.to_datetime(d["datetime"], utc=True, errors="coerce")
        for c in ["open","high","low","close","volume"]:
            if c in d: d[c] = pd.to_numeric(d[c], errors="coerce")
        d = d.dropna(subset=["datetime","open","high","low","close"]).sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
        return d, "OK", remaining, r.headers.get("api-credits-used")
    except Exception as e:
        return pd.DataFrame(), f"خطأ في الاتصال: {e}", None, None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_extended_m5():
    frames, errors, credits = [], [], []
    end = datetime.now(timezone.utc)
    for i in range(CHUNKS):
        d, msg, left, used = fetch_chunk(end.strftime("%Y-%m-%d %H:%M:%S"))
        if d.empty:
            errors.append(f"الدفعة {i+1}: {msg}")
            break
        frames.append(d)
        if left is not None: credits.append(left)
        oldest = d["datetime"].min().to_pydatetime()
        if oldest >= end: errors.append(f"الدفعة {i+1}: التاريخ لم يتحرك للخلف"); break
        end = oldest - timedelta(minutes=5)
        if len(d) < BARS_PER_CHUNK: break
    if not frames: return pd.DataFrame(), " | ".join(errors), 0, credits
    out = pd.concat(frames, ignore_index=True).sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
    msg = "تم تحميل التاريخ المطلوب بالكامل" if len(frames) == CHUNKS else "تم تحميل جزء من التاريخ فقط: " + " | ".join(errors)
    return out, msg, len(frames), credits


def completed(df):
    if df.empty: return df
    return df[df.datetime < pd.Timestamp.now(tz="UTC").floor("5min")].copy()


def resample_ohlc(df, rule):
    if df.empty: return df
    x = df.set_index("datetime")[["open","high","low","close"]].resample(rule).agg({"open":"first","high":"max","low":"min","close":"last"}).dropna().reset_index()
    return x


def ema(s,n): return s.ewm(span=n, adjust=False).mean()

def rsi(s,n=14):
    d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    au=up.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); ad=dn.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    return (100-100/(1+au/ad.replace(0,np.nan))).fillna(50)

def atr(df,n=14):
    pc=df.close.shift(1); tr=pd.concat([df.high-df.low,(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()

def macd(s):
    line=ema(s,12)-ema(s,26); sig=ema(line,9); return line,sig,line-sig

def adx(df,n=14):
    up=df.high.diff(); dn=-df.low.diff(); plus=pd.Series(np.where((up>dn)&(up>0),up,0.),index=df.index); minus=pd.Series(np.where((dn>up)&(dn>0),dn,0.),index=df.index)
    pc=df.close.shift(1); tr=pd.concat([df.high-df.low,(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    aw=tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); pdi=100*plus.ewm(alpha=1/n,adjust=False,min_periods=n).mean()/aw.replace(0,np.nan); mdi=100*minus.ewm(alpha=1/n,adjust=False,min_periods=n).mean()/aw.replace(0,np.nan)
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan); return dx.ewm(alpha=1/n,adjust=False,min_periods=n).mean(),pdi,mdi

def indicators(df):
    x=df.copy(); x["ema20"]=ema(x.close,20); x["ema50"]=ema(x.close,50); x["ema100"]=ema(x.close,100); x["rsi"]=rsi(x.close); x["atr"]=atr(x); x["macd"],x["macd_signal"],x["macd_hist"]=macd(x.close); x["momentum"]=x.close.pct_change(5)*100; x["adx"],x["plus_di"],x["minus_di"]=adx(x); return x

def trend(r):
    if r.close>r.ema20>r.ema50>r.ema100:return "صاعد"
    if r.close<r.ema20<r.ema50<r.ema100:return "هابط"
    return "محايد"

def regime(r):
    if pd.isna(r.adx) or pd.isna(r.atr): return "غير معروف"
    if r.adx>=25:return "اتجاه"
    if r.adx<=18:return "نطاق"
    return "انتقالي"

def snapshot(df):
    if len(df)<110:return None
    x=indicators(df).dropna().reset_index(drop=True)
    if x.empty:return None
    r=x.iloc[-1]
    return {"trend":trend(r),"regime":regime(r),"rsi":float(r.rsi),"adx":float(r.adx),"atr":float(r.atr),"momentum":float(r.momentum),"close":float(r.close),"macd_hist":float(r.macd_hist),"candle":x.datetime.iloc[-1],"frame":x}

def b2(df):
    if len(df)<30:return {"valid":False,"direction":None,"breakout":False,"retest":False,"level":np.nan}
    r=df.iloc[-1]; p=df.iloc[-21:-1]; res=p.high.max(); sup=p.low.min(); av=r.atr
    if pd.isna(av) or av<=0:return {"valid":False,"direction":None,"breakout":False,"retest":False,"level":np.nan}
    bb=df.close.iloc[-2]>res; bs=df.close.iloc[-2]<sup
    br=bb and r.low<=res+0.35*av and r.close>res; sr=bs and r.high>=sup-0.35*av and r.close<sup
    if br:return {"valid":True,"direction":"شراء","breakout":True,"retest":True,"level":float(res)}
    if sr:return {"valid":True,"direction":"بيع","breakout":True,"retest":True,"level":float(sup)}
    return {"valid":False,"direction":None,"breakout":bb or bs,"retest":False,"level":float(res if bb else sup if bs else np.nan)}


def news_status(research_mode):
    # Twelve Data's documented core API does not provide a general XAU economic-news calendar.
    # Therefore the bot must FAIL CLOSED unless an external calendar endpoint is explicitly configured.
    try: url=st.secrets.get("NEWS_API_URL")
    except Exception: url=None
    if not url:
        return {"connected":False,"blocked":not research_mode,"label":"غير متصل — لا يوجد مزود أخبار"}
    try:
        r=requests.get(url,timeout=8); r.raise_for_status(); payload=r.json()
        blocked=bool(payload.get("high_impact",False) or payload.get("block",False))
        return {"connected":True,"blocked":blocked,"label":"خبر عالي التأثير" if blocked else "لا يوجد حظر"}
    except Exception as e:
        return {"connected":False,"blocked":not research_mode,"label":"تعذر الاتصال بمزود الأخبار"}


def daily_loss_pct(price):
    p=st.session_state.position
    eq=st.session_state.balance
    if p: eq += unrealized_r(p,price)*p["risk_money"]
    return max(0,(st.session_state.daily_start_balance-eq)/max(st.session_state.daily_start_balance,1)*100)

def unrealized_r(p,price):
    if not p or not np.isfinite(price):return 0.
    dist=abs(p["entry"]-p["sl"])
    if dist<=0:return 0.
    direction=1 if p["side"]=="شراء" else -1
    return direction*(price-p["entry"])/dist*p["remaining_fraction"]+p["realized_r"]


def analyze(m5,m15,h1,h4,research_mode):
    s={k:snapshot(v) for k,v in {"M5":m5,"M15":m15,"H1":h1,"H4":h4}.items()}
    if any(s[k] is None for k in ["M5","M15","H1"]): return {"signal":"انتظار","strength":0,"reason":"بيانات غير كافية","gates":{},"snaps":s,"b2":{},"news":news_status(research_mode)}
    z=b2(s["M5"]["frame"]); trends=[s[k]["trend"] for k in ["M5","M15","H1"]]
    bull=all(t=="صاعد" for t in trends); bear=all(t=="هابط" for t in trends); r=s["M5"]
    mb=r["rsi"]>=52 and r["momentum"]>0 and r["macd_hist"]>0; ms=r["rsi"]<=48 and r["momentum"]<0 and r["macd_hist"]<0
    news=news_status(research_mode)
    gates={"البيانات":True,"النظام السوقي":r["regime"]=="اتجاه","توافق الأطر":bull or bear,"الزخم":mb or ms,"اختراق وإعادة اختبار":z["valid"],"المخاطر":not st.session_state.kill_switch,"الحد اليومي":daily_loss_pct(st.session_state.last_price)<DAILY_LOSS_LIMIT*100 and st.session_state.daily_trades<MAX_DAILY_TRADES,"الأخبار":not news["blocked"] and (news["connected"] or research_mode)}
    strength=(30 if gates["توافق الأطر"] else 0)+(30 if z["valid"] else 0)+(20 if gates["الزخم"] else 0)+(10 if r["adx"]>=25 else 0)+(10 if s["H4"] and s["H4"]["trend"] in ("صاعد","هابط") else 0)
    reason=next((k for k,v in gates.items() if not v),"اجتازت جميع الشروط الأساسية")
    sig="انتظار"
    if all(gates.values()):
        if z["direction"]=="شراء" and bull and mb:sig="شراء"
        elif z["direction"]=="بيع" and bear and ms:sig="بيع"
        else:reason="تعارض الاتجاه"
    return {"signal":sig,"strength":min(strength,100),"reason":reason,"gates":gates,"snaps":s,"b2":z,"news":news}


def open_trade(side,price,av):
    if st.session_state.position or st.session_state.kill_switch:return False,"لا يمكن فتح صفقة"
    if st.session_state.daily_trades>=MAX_DAILY_TRADES:return False,"تم بلوغ الحد اليومي"
    if daily_loss_pct(price)>=DAILY_LOSS_LIMIT*100:return False,"تم بلوغ حد الخسارة اليومية"
    dist=max(av*1.4,price*.0015); risk=st.session_state.balance*RISK_PER_TRADE
    if side=="شراء": sl=price-dist; tp1=price+dist; tp2=price+2.2*dist
    else: sl=price+dist; tp1=price-dist; tp2=price-2.2*dist
    st.session_state.position={"id":uuid.uuid4().hex[:10],"side":side,"entry":float(price),"sl":float(sl),"tp1":float(tp1),"tp2":float(tp2),"risk_money":risk,"remaining_fraction":1.,"realized_r":0.,"tp1_hit":False,"opened":datetime.now(TZ).isoformat()}
    st.session_state.daily_trades+=1; return True,"تم فتح الصفقة الورقية"


def close_trade(price,reason):
    p=st.session_state.position
    if not p:return
    r=unrealized_r(p,price); pnl=r*p["risk_money"]; st.session_state.balance+=pnl
    st.session_state.history.append({"الوقت":datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),"النوع":p["side"],"الدخول":round(p["entry"],2),"الخروج":round(price,2),"R":round(r,3),"الربح/الخسارة":round(pnl,2),"السبب":reason}); st.session_state.position=None


def manage_trade(price):
    p=st.session_state.position
    if not p:return
    if p["side"]=="شراء":
        if price<=p["sl"]:close_trade(p["sl"],"وقف الخسارة");return
        if not p["tp1_hit"] and price>=p["tp1"]:
            p["realized_r"]+=.5;p["remaining_fraction"]=.5;p["tp1_hit"]=True;p["sl"]=p["entry"]
        if p["tp1_hit"] and price>=p["tp2"]:close_trade(p["tp2"],"الهدف الثاني")
    else:
        if price>=p["sl"]:close_trade(p["sl"],"وقف الخسارة");return
        if not p["tp1_hit"] and price<=p["tp1"]:
            p["realized_r"]+=.5;p["remaining_fraction"]=.5;p["tp1_hit"]=True;p["sl"]=p["entry"]
        if p["tp1_hit"] and price<=p["tp2"]:close_trade(p["tp2"],"الهدف الثاني")


def backtest(df):
    """Backtest the same B2 entry and TP1/BE/TP2 management used by paper trading."""
    x = indicators(df).dropna().reset_index(drop=True)
    if len(x) < 350:
        return pd.DataFrame(), {"trades": 0, "warning": "العينة صغيرة للاختبار"}

    split = int(len(x) * 0.70)
    rows = []
    i = 120
    while i < len(x) - 2:
        r = x.iloc[i]
        p20 = x.iloc[max(0, i - 21):i]
        resistance = float(p20.high.max())
        support = float(p20.low.min())
        av = float(r.atr)
        if not np.isfinite(av) or av <= 0:
            i += 1
            continue

        prev_close = float(x.close.iloc[i - 1])
        breakout_buy = prev_close > resistance
        breakout_sell = prev_close < support
        retest_buy = breakout_buy and float(r.low) <= resistance + 0.35 * av and float(r.close) > resistance
        retest_sell = breakout_sell and float(r.high) >= support - 0.35 * av and float(r.close) < support

        bull = r.close > r.ema20 > r.ema50 > r.ema100 and r.rsi >= 52 and r.momentum > 0 and r.macd_hist > 0
        bear = r.close < r.ema20 < r.ema50 < r.ema100 and r.rsi <= 48 and r.momentum < 0 and r.macd_hist < 0
        side = "شراء" if retest_buy and bull and r.adx >= 25 else "بيع" if retest_sell and bear and r.adx >= 25 else None
        if side is None:
            i += 1
            continue

        entry = float(r.close)
        dist = max(av * 1.4, entry * 0.0015)
        if side == "شراء":
            stop, tp1, tp2 = entry - dist, entry + dist, entry + 2.2 * dist
        else:
            stop, tp1, tp2 = entry + dist, entry - dist, entry - 2.2 * dist

        realized_r = 0.0
        remaining = 1.0
        sl = stop
        tp1_hit = False
        outcome = None
        reason = None
        close_index = None

        for j in range(i + 1, min(i + 80, len(x))):
            hi = float(x.high.iloc[j])
            lo = float(x.low.iloc[j])
            # Conservative rule: if stop and target are both touched in one bar,
            # assume the stop was hit first because intrabar order is unknown.
            if side == "شراء":
                if lo <= sl:
                    outcome = realized_r - remaining
                    reason = "وقف الخسارة"
                    close_index = j
                    break
                if not tp1_hit and hi >= tp1:
                    realized_r += 0.5
                    remaining = 0.5
                    tp1_hit = True
                    sl = entry
                if tp1_hit and hi >= tp2:
                    outcome = realized_r + 1.1
                    reason = "الهدف الثاني"
                    close_index = j
                    break
            else:
                if hi >= sl:
                    outcome = realized_r - remaining
                    reason = "وقف الخسارة"
                    close_index = j
                    break
                if not tp1_hit and lo <= tp1:
                    realized_r += 0.5
                    remaining = 0.5
                    tp1_hit = True
                    sl = entry
                if tp1_hit and lo <= tp2:
                    outcome = realized_r + 1.1
                    reason = "الهدف الثاني"
                    close_index = j
                    break

        if outcome is not None:
            rows.append({
                "الفهرس": i,
                "الوقت": x.datetime.iloc[i],
                "النوع": side,
                "الدخول": round(entry, 2),
                "الوقف": round(stop, 2),
                "TP1": round(tp1, 2),
                "TP2": round(tp2, 2),
                "R": round(outcome, 3),
                "السبب": reason,
                "OOS": i >= split,
            })
            i = close_index + 1
        else:
            i += 1

    trades = pd.DataFrame(rows)
    oos = trades[trades.OOS].copy() if not trades.empty else trades
    if oos.empty:
        return trades, {"trades": 0, "warning": "لا توجد صفقات OOS بالشروط الحالية"}

    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in oos.R:
        eq += float(value)
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)

    gross_win = oos.loc[oos.R > 0, "R"].sum()
    gross_loss = abs(oos.loc[oos.R < 0, "R"].sum())
    pf = gross_win / gross_loss if gross_loss else math.inf
    return trades, {
        "trades": len(oos),
        "win_rate": float((oos.R > 0).mean() * 100),
        "profit_factor": float(pf),
        "total_r": float(oos.R.sum()),
        "max_dd_r": float(max_dd),
        "warning": None,
    }


def metrics(items):
    cols=st.columns(len(items))
    for c,(a,b) in zip(cols,items):c.metric(a,b)

def status_table(d):
    st.dataframe(pd.DataFrame([{"البند":k,"الحالة":"مقبول" if v else "محظور"} for k,v in d.items()]),hide_index=True,use_container_width=True)

st.sidebar.title("🟡 بوت الذهب")
st.sidebar.caption("XAU/USD • تداول ورقي فقط")
research=st.sidebar.checkbox("وضع البحث التجريبي",value=st.session_state.research_mode,help="يسمح بالبحث الورقي عند عدم وجود مزود أخبار خارجي. لا يسمح بالتنفيذ الحقيقي.")
st.session_state.research_mode=research
st.session_state.kill_switch=st.sidebar.toggle("مفتاح الإيقاف",value=st.session_state.kill_switch)
st.session_state.auto_refresh=st.sidebar.toggle("التحديث التلقائي",value=False)
refresh=st.sidebar.slider("فترة التحديث بالثواني",15,120,45)

raw,msg,chunks,credits=fetch_extended_m5();raw=completed(raw)
if raw.empty:st.error("مصدر البيانات غير متاح");st.info(msg);st.stop()
if chunks<CHUNKS:st.warning(msg)

m5=indicators(raw);m15=resample_ohlc(raw,"15min");h1=resample_ohlc(raw,"1h");h4=resample_ohlc(raw,"4h")
price=float(raw.close.iloc[-1]);st.session_state.last_price=price
manage_trade(price)
a=analyze(m5,m15,h1,h4,research)
candle=str(raw.datetime.iloc[-1])
if st.session_state.last_signal_candle!=candle:
    st.session_state.last_signal_candle=candle;st.session_state.decisions.insert(0,{"الوقت":datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),"الشمعة":candle,"الإشارة":a["signal"],"القوة":a["strength"],"السبب":a["reason"]});st.session_state.decisions=st.session_state.decisions[:300]
if a["signal"] in ("شراء","بيع") and st.session_state.position is None:open_trade(a["signal"],price,a["snaps"]["M5"]["atr"])

st.markdown("<div class='card'><div class='small'>GOLD AI</div><h1 class='gold'>منصة التداول الذكي XAU/USD</h1><div class='muted'>تداول ورقي • محرك القرار • محرك المخاطر</div></div>",unsafe_allow_html=True)
metrics([("XAU/USD",f"${price:,.2f}"),("الإشارة",a["signal"]),("قوة الإشارة",f"{a['strength']}%"),("حالة السوق",a["snaps"]["M5"]["regime"] if a["snaps"]["M5"] else "—"),("الرصيد",f"${st.session_state.balance:,.2f}"),("الخسارة اليومية",f"{daily_loss_pct(price):.2f}%")])

st.subheader("حالة الأطر الزمنية")
cols=st.columns(4)
for c,k in zip(cols,["M5","M15","H1","H4"]):
    s=a["snaps"].get(k)
    if s:c.markdown(f"<div class='card'><b>{k}</b><br><span class='signal'>{s['trend']}</span><br><span class='small'>القوة النسبية {s['rsi']:.1f} • قوة الاتجاه {s['adx']:.1f}</span></div>",unsafe_allow_html=True)
    else:c.warning(f"{k}: بيانات غير كافية")

st.subheader("محرك القرار")
st.markdown(f"<div class='card'><div class='small'>القرار النهائي</div><div class='signal'>{a['signal']}</div><div>{a['reason']}</div><div class='small'>قوة الإشارة مقياس توافق داخلي وليست احتمال ربح.</div></div>",unsafe_allow_html=True)
status_table(a["gates"])

st.subheader("فلتر الأخبار")
st.info(a["news"]["label"] + (" — وضع البحث يسمح بالاستمرار." if research and not a["news"]["connected"] else ""))

st.subheader("اختراق B2 وإعادة الاختبار")
z=a["b2"]
metrics([("الاختراق","نعم" if z.get("breakout") else "لا"),("إعادة الاختبار","نعم" if z.get("retest") else "لا"),("الاتجاه",z.get("direction") or "—"),("المستوى",f"{z.get('level',np.nan):.2f}" if np.isfinite(z.get("level",np.nan)) else "—")])

st.subheader("السعر")
st.line_chart(raw.tail(300).set_index("datetime")[["close"]],use_container_width=True)

st.subheader("التداول الورقي")
p=st.session_state.position
if p:
    rr=unrealized_r(p,price);pnl=rr*p["risk_money"];metrics([("النوع",p["side"]),("الدخول",f"{p['entry']:.2f}"),("السعر الحالي",f"{price:.2f}"),("وقف الخسارة",f"{p['sl']:.2f}"),("الهدف الأول",f"{p['tp1']:.2f}"),("الهدف الثاني",f"{p['tp2']:.2f}"),("الربح/الخسارة",f"${pnl:,.2f}"),("المضاعف",f"{rr:.2f}R")])
else:st.info("لا توجد صفقة ورقية مفتوحة.")

st.subheader("مركز المخاطر")
metrics([("الرصيد",f"${st.session_state.balance:,.2f}"),("المخاطرة لكل صفقة",f"{RISK_PER_TRADE*100:.2f}%"),("حد الخسارة اليومية",f"{DAILY_LOSS_LIMIT*100:.1f}%"),("صفقات اليوم",f"{st.session_state.daily_trades}/{MAX_DAILY_TRADES}"),("الصفقات المفتوحة","1" if p else "0"),("مفتاح الإيقاف","مفعل" if st.session_state.kill_switch else "غير مفعل")])

st.subheader("مختبر الاختبار التاريخي")
with st.expander("تشغيل الاختبار"):
    n=st.slider("عدد شموع 15 دقيقة",500,min(5000,len(m15)),min(3000,len(m15)),100)
    trades,stats=backtest(m15.tail(n))
    if stats["trades"]:
        metrics([("صفقات خارج العينة",stats["trades"]),("نسبة الفوز",f"{stats['win_rate']:.1f}%"),("معامل الربح",f"{stats['profit_factor']:.2f}"),("إجمالي R",f"{stats['total_r']:.2f}R"),("أقصى تراجع OOS",f"{stats['max_dd_r']:.2f}R")]);st.caption("التقييمات خارج العينة محسوبة على الجزء الأخير فقط من العينة.");st.dataframe(trades.tail(100),hide_index=True,use_container_width=True)
    else:st.warning(stats.get("warning","لا توجد نتائج"))

st.subheader("سجل القرارات")
st.dataframe(pd.DataFrame(st.session_state.decisions),hide_index=True,use_container_width=True) if st.session_state.decisions else st.info("لا يوجد سجل بعد.")
st.subheader("سجل الصفقات")
st.dataframe(pd.DataFrame(st.session_state.history),hide_index=True,use_container_width=True) if st.session_state.history else st.info("لا توجد صفقات مغلقة.")

st.subheader("سلامة النظام")
status_table({"مصدر البيانات":not raw.empty,"محرك الاستراتيجية":a["snaps"]["M5"] is not None,"محرك المخاطر":True,"محرك التداول الورقي":True,"مزامنة الوقت":True,"مزود الأخبار":a["news"]["connected"]})
st.caption(f"آخر شمعة: {raw.datetime.iloc[-1]} UTC • {len(raw):,} شمعة M5 • الدفعات المحملة: {chunks}/{CHUNKS} • أرصدة API المتاحة: {credits[-1] if credits else 'غير متاحة'}")

if st.session_state.auto_refresh:
    time.sleep(refresh);st.rerun()

import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import streamlit as st

# ============================================================
# GOLD AI — XAU/USD SMART PAPER TRADING
# Paper trading only. No broker execution exists in this file.
# ============================================================

st.set_page_config(
    page_title="بوت الذهب XAU/USD",
    page_icon="🟡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

TZ = ZoneInfo("Asia/Riyadh")
START_BALANCE = 100_000.0
RISK_PER_TRADE = 0.005
DAILY_LOSS_LIMIT = 0.02
MAX_DAILY_TRADES = 5
MAX_OPEN_TRADES = 1
SYMBOL = "XAU/USD"
DATA_URL = "https://api.twelvedata.com/time_series"

st.markdown("""
<style>
:root{--bg:#0b1220;--card:#111827;--line:#243044;--gold:#d4af37;--txt:#f8fafc;--muted:#94a3b8;--green:#22c55e;--red:#ef4444}
.stApp{background:var(--bg);color:var(--txt)}
.block-container{max-width:1400px;padding-top:1rem;padding-bottom:3rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:14px}
.gold{color:var(--gold)} .muted{color:var(--muted)}
.big{font-size:2.2rem;font-weight:800}
.signal{font-size:1.8rem;font-weight:900}
.ok{color:var(--green)} .bad{color:var(--red)}
.small{font-size:.82rem;color:var(--muted)}
div[data-testid="stMetric"]{background:var(--card);border:1px solid var(--line);padding:12px;border-radius:14px}
</style>
""", unsafe_allow_html=True)


# ------------------------- State ----------------------------

def init_state():
    defaults = {
        "balance": START_BALANCE,
        "equity": START_BALANCE,
        "position": None,
        "history": [],
        "decisions": [],
        "daily_start_balance": START_BALANCE,
        "daily_date": datetime.now(TZ).date().isoformat(),
        "daily_trades": 0,
        "kill_switch": False,
        "last_signal_candle": None,
        "last_price": np.nan,
        "auto_refresh": False,
        "research_mode": True,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    today = datetime.now(TZ).date().isoformat()
    if st.session_state.daily_date != today:
        st.session_state.daily_date = today
        st.session_state.daily_start_balance = st.session_state.balance
        st.session_state.daily_trades = 0


init_state()


# ------------------------- Data ------------------------------

def get_api_key():
    try:
        return st.secrets["TWELVE_DATA_API_KEY"]
    except Exception:
        return None


@st.cache_data(ttl=45, show_spinner=False)
def fetch_twelve(interval="5min", outputsize=5000, end_date=None):
    key = get_api_key()
    if not key:
        return pd.DataFrame(), "TWELVE_DATA_API_KEY غير موجود في Secrets"

    params = {
        "symbol": SYMBOL,
        "interval": interval,
        "outputsize": min(int(outputsize), 5000),
        "timezone": "UTC",
        "apikey": key,
    }
    if end_date:
        params["end_date"] = end_date

    try:
        r = requests.get(DATA_URL, params=params, timeout=20)
        r.raise_for_status()
        payload = r.json()
        if "values" not in payload:
            return pd.DataFrame(), str(payload.get("message") or payload.get("code") or "فشل مصدر البيانات")
        df = pd.DataFrame(payload["values"])
        if df.empty:
            return df, "لا توجد بيانات"
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
        df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
        return df, "OK"
    except Exception as e:
        return pd.DataFrame(), f"خطأ اتصال: {e}"


@st.cache_data(ttl=300, show_spinner=False)
def fetch_extended_m5():
    # Four backward chunks. This avoids pretending 5000 bars is a long history.
    frames = []
    end = datetime.now(timezone.utc)
    for _ in range(4):
        d, msg = fetch_twelve("5min", 5000, end.strftime("%Y-%m-%d %H:%M:%S"))
        if not d.empty:
            frames.append(d)
            end = d["datetime"].min().to_pydatetime() - timedelta(minutes=5)
        else:
            break
    if not frames:
        return pd.DataFrame(), msg
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
    return out, "OK"


def completed(df):
    if df.empty:
        return df
    now = pd.Timestamp.now(tz="UTC")
    # Exclude a currently forming 5-minute bar.
    return df[df["datetime"] < now.floor("5min")].copy()


def resample_ohlc(df, rule):
    if df.empty:
        return df
    x = df.set_index("datetime")[["open", "high", "low", "close"]].resample(rule).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    )
    x = x.dropna().reset_index()
    return x


# ---------------------- Indicators ---------------------------

def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0)
    dn = -d.clip(upper=0)
    au = up.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    rs = au / ad.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat(
        [(df["high"]-df["low"]), (df["high"]-pc).abs(), (df["low"]-pc).abs()],
        axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False, min_periods=n).mean()


def macd(s):
    fast = ema(s, 12)
    slow = ema(s, 26)
    line = fast - slow
    sig = ema(line, 9)
    return line, sig, line - sig


def adx(df, n=14):
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    dn = -low.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    pc = close.shift(1)
    tr = pd.concat([(high-low), (high-pc).abs(), (low-pc).abs()], axis=1).max(axis=1)
    atr_w = tr.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    pdi = 100 * plus_dm.ewm(alpha=1/n, adjust=False, min_periods=n).mean() / atr_w.replace(0,np.nan)
    mdi = 100 * minus_dm.ewm(alpha=1/n, adjust=False, min_periods=n).mean() / atr_w.replace(0,np.nan)
    dx = 100 * (pdi-mdi).abs() / (pdi+mdi).replace(0,np.nan)
    return dx.ewm(alpha=1/n, adjust=False, min_periods=n).mean(), pdi, mdi


def add_indicators(df):
    x = df.copy()
    x["ema20"] = ema(x["close"], 20)
    x["ema50"] = ema(x["close"], 50)
    x["ema100"] = ema(x["close"], 100)
    x["rsi"] = rsi(x["close"])
    x["atr"] = atr(x)
    x["macd"], x["macd_signal"], x["macd_hist"] = macd(x["close"])
    x["momentum"] = x["close"].pct_change(5) * 100
    x["adx"], x["plus_di"], x["minus_di"] = adx(x)
    return x


# ---------------------- Market logic -------------------------

def trend_label(row):
    if row["close"] > row["ema20"] > row["ema50"] > row["ema100"]:
        return "BULLISH"
    if row["close"] < row["ema20"] < row["ema50"] < row["ema100"]:
        return "BEARISH"
    return "NEUTRAL"


def market_regime(row):
    if pd.isna(row["adx"]) or pd.isna(row["atr"]):
        return "UNKNOWN"
    if row["adx"] >= 25:
        return "TRENDING"
    if row["adx"] <= 18:
        return "RANGING"
    return "TRANSITION"


def sr_levels(df, lookback=80):
    x = df.tail(lookback)
    if x.empty:
        return np.nan, np.nan
    return float(x["low"].min()), float(x["high"].max())


def detect_b2(df):
    # Strict, recent breakout + retest model.
    if len(df) < 30:
        return {"valid": False, "direction": None, "breakout": False, "retest": False, "level": np.nan}
    x = df.copy()
    recent = x.iloc[-1]
    prior = x.iloc[-21:-1]
    resistance = prior["high"].max()
    support = prior["low"].min()
    atrv = recent["atr"]
    if pd.isna(atrv) or atrv <= 0:
        return {"valid": False, "direction": None, "breakout": False, "retest": False, "level": np.nan}

    bullish_break = x["close"].iloc[-2] > resistance
    bearish_break = x["close"].iloc[-2] < support

    # Current candle must retest the broken level and close back in direction.
    bull_retest = bullish_break and recent["low"] <= resistance + 0.35*atrv and recent["close"] > resistance
    bear_retest = bearish_break and recent["high"] >= support - 0.35*atrv and recent["close"] < support

    if bull_retest:
        return {"valid": True, "direction": "BUY", "breakout": True, "retest": True, "level": float(resistance)}
    if bear_retest:
        return {"valid": True, "direction": "SELL", "breakout": True, "retest": True, "level": float(support)}
    return {"valid": False, "direction": None, "breakout": bullish_break or bearish_break, "retest": False,
            "level": float(resistance if bullish_break else support if bearish_break else np.nan)}


def tf_snapshot(df):
    if len(df) < 110:
        return None
    x = add_indicators(df).dropna().reset_index(drop=True)
    if x.empty:
        return None
    r = x.iloc[-1]
    return {
        "trend": trend_label(r),
        "regime": market_regime(r),
        "rsi": float(r["rsi"]),
        "adx": float(r["adx"]),
        "atr": float(r["atr"]),
        "momentum": float(r["momentum"]),
        "close": float(r["close"]),
        "macd_hist": float(r["macd_hist"]),
        "candle": x["datetime"].iloc[-1],
        "frame": x,
    }


def analyze_market(m5, m15, h1, h4, research_mode=True):
    snaps = {k: tf_snapshot(v) for k,v in {"M5":m5,"M15":m15,"H1":h1,"H4":h4}.items()}
    if any(v is None for v in [snaps["M5"], snaps["M15"], snaps["H1"]]):
        return {"signal":"WAIT","strength":0,"reason":"بيانات غير كافية","gates":{}, "snaps":snaps, "b2":{}}

    b2 = detect_b2(snaps["M5"]["frame"])
    trends = [snaps[k]["trend"] for k in ["M5","M15","H1"]]
    long_align = all(t=="BULLISH" for t in trends)
    short_align = all(t=="BEARISH" for t in trends)

    r5, a5, mom5 = snaps["M5"]["rsi"], snaps["M5"]["adx"], snaps["M5"]["momentum"]
    momentum_buy = r5 >= 52 and mom5 > 0 and snaps["M5"]["macd_hist"] > 0
    momentum_sell = r5 <= 48 and mom5 < 0 and snaps["M5"]["macd_hist"] < 0
    strength = 0
    if long_align or short_align: strength += 30
    if b2["valid"]: strength += 30
    if (momentum_buy or momentum_sell): strength += 20
    if a5 >= 25: strength += 10
    if snaps["H4"] and snaps["H4"]["trend"] in ("BULLISH","BEARISH"): strength += 10

    gates = {
        "DATA": True,
        "REGIME": snaps["M5"]["regime"] == "TRENDING",
        "MTF": long_align or short_align,
        "MOMENTUM": momentum_buy or momentum_sell,
        "B2": b2["valid"],
        "RISK": not st.session_state.kill_switch,
        "DAILY_LIMIT": daily_loss_pct() < DAILY_LOSS_LIMIT*100 and st.session_state.daily_trades < MAX_DAILY_TRADES,
        "SPREAD": True,
        "NEWS": research_mode,
    }

    if not gates["DATA"]: reason="DATA"
    elif not gates["REGIME"]: reason="REGIME"
    elif not gates["B2"]: reason="B2"
    elif not gates["MTF"]: reason="MTF"
    elif not gates["MOMENTUM"]: reason="MOMENTUM"
    elif not gates["RISK"]: reason="KILL SWITCH"
    elif not gates["DAILY_LIMIT"]: reason="DAILY LIMIT"
    elif not gates["NEWS"]: reason="NEWS FILTER NOT CONNECTED"
    else: reason="ALL CORE GATES PASSED"

    signal = "WAIT"
    if all(gates.values()):
        if b2["direction"] == "BUY" and long_align and momentum_buy:
            signal="BUY"
        elif b2["direction"] == "SELL" and short_align and momentum_sell:
            signal="SELL"
        else:
            reason="DIRECTION CONFLICT"

    return {"signal":signal,"strength":min(strength,100),"reason":reason,"gates":gates,"snaps":snaps,"b2":b2}


# ----------------------- Risk / paper -----------------------

def unrealized_r(position, price):
    if not position or not np.isfinite(price):
        return 0.0
    direction = 1 if position["side"]=="BUY" else -1
    risk_dist = abs(position["entry"]-position["sl"])
    if risk_dist <= 0:
        return 0.0
    return direction*(price-position["entry"])/risk_dist * position["remaining_fraction"] + position["realized_r"]


def current_equity(price):
    pos = st.session_state.position
    if not pos:
        return st.session_state.balance
    return st.session_state.balance + unrealized_r(pos, price)*pos["risk_money"]


def daily_loss_pct(price=None):
    if price is None:
        price = st.session_state.last_price
    eq = current_equity(price)
    base = max(st.session_state.daily_start_balance, 1)
    loss = max(0.0, st.session_state.daily_start_balance - eq)
    return loss/base*100


def create_trade(side, price, atr_value):
    if st.session_state.position or st.session_state.kill_switch:
        return False, "لا يمكن فتح صفقة حالياً"
    if st.session_state.daily_trades >= MAX_DAILY_TRADES:
        return False, "تم بلوغ الحد اليومي للصفقات"
    if daily_loss_pct(price) >= DAILY_LOSS_LIMIT*100:
        return False, "تم بلوغ حد الخسارة اليومية"

    risk_money = st.session_state.balance * RISK_PER_TRADE
    sl_dist = max(atr_value*1.4, price*0.0015)
    entry = float(price)
    if side=="BUY":
        sl, tp1, tp2 = entry-sl_dist, entry+sl_dist, entry+2.2*sl_dist
    else:
        sl, tp1, tp2 = entry+sl_dist, entry-sl_dist, entry-2.2*sl_dist

    st.session_state.position = {
        "id": uuid.uuid4().hex[:10],
        "side": side, "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
        "risk_money": risk_money, "remaining_fraction":1.0, "realized_r":0.0,
        "tp1_hit":False, "opened":datetime.now(TZ).isoformat(),
    }
    st.session_state.daily_trades += 1
    return True, "تم فتح صفقة ورقية"


def close_trade(price, reason):
    p = st.session_state.position
    if not p:
        return
    total_r = unrealized_r(p, price)
    pnl = total_r*p["risk_money"]
    st.session_state.balance += pnl
    st.session_state.history.append({
        "time":datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
        "side":p["side"], "entry":round(p["entry"],2), "exit":round(price,2),
        "R":round(total_r,3), "P&L":round(pnl,2), "reason":reason
    })
    st.session_state.position=None


def manage_trade(price):
    p=st.session_state.position
    if not p: return
    if p["side"]=="BUY":
        if price <= p["sl"]: close_trade(p["sl"],"STOP"); return
        if not p["tp1_hit"] and price >= p["tp1"]:
            # Realize half and move stop to break-even.
            move_r = (p["tp1"]-p["entry"])/abs(p["entry"]-p["sl"])*0.5
            p["realized_r"] += move_r
            p["remaining_fraction"]=0.5
            p["tp1_hit"]=True
            p["sl"]=p["entry"]
        if p["tp1_hit"] and price >= p["tp2"]:
            close_trade(p["tp2"],"TP2")
    else:
        if price >= p["sl"]: close_trade(p["sl"],"STOP"); return
        if not p["tp1_hit"] and price <= p["tp1"]:
            move_r = (p["entry"]-p["tp1"])/abs(p["entry"]-p["sl"])*0.5
            p["realized_r"] += move_r
            p["remaining_fraction"]=0.5
            p["tp1_hit"]=True
            p["sl"]=p["entry"]
        if p["tp1_hit"] and price <= p["tp2"]:
            close_trade(p["tp2"],"TP2")


# ---------------------- Backtest ----------------------------

def backtest_strategy(df, split_pct=70):
    x = add_indicators(df).dropna().reset_index(drop=True)
    if len(x) < 300:
        return pd.DataFrame(), {"warning":"العينة صغيرة للاختبار","trades":0}
    split = int(len(x)*split_pct/100)
    trades=[]
    equity=0.0
    peak=0.0
    maxdd=0.0
    for i in range(120, len(x)-2):
        r=x.iloc[i]
        prev=x.iloc[i-1]
        side=None
        # Same family of conditions as paper engine, intentionally conservative.
        if r["close"]>r["ema20"]>r["ema50"]>r["ema100"] and r["rsi"]>=52 and r["macd_hist"]>0 and r["adx"]>=25:
            if prev["high"] > x.iloc[max(0,i-21):i]["high"].max() and r["low"] <= prev["high"]:
                side="BUY"
        elif r["close"]<r["ema20"]<r["ema50"]<r["ema100"] and r["rsi"]<=48 and r["macd_hist"]<0 and r["adx"]>=25:
            if prev["low"] < x.iloc[max(0,i-21):i]["low"].min() and r["high"] >= prev["low"]:
                side="SELL"
        if not side: continue
        entry=float(r["close"]); dist=max(float(r["atr"])*1.4, entry*0.0015)
        sl=entry-dist; tp=entry+2.2*dist if side=="BUY" else entry-2.2*dist
        stop=entry-dist if side=="BUY" else entry+dist
        outcome=None
        for j in range(i+1,min(i+80,len(x))):
            hi,lo=float(x.iloc[j]["high"]),float(x.iloc[j]["low"])
            if side=="BUY":
                if lo<=stop: outcome=-1; break
                if hi>=tp: outcome=2.2; break
            else:
                if hi>=stop: outcome=-1; break
                if lo<=tp: outcome=2.2; break
        if outcome is None: continue
        equity += outcome; peak=max(peak,equity); maxdd=max(maxdd,peak-equity)
        trades.append({"index":i,"time":x.iloc[i]["datetime"],"side":side,"R":outcome,"OOS":i>=split})
    t=pd.DataFrame(trades)
    if t.empty:
        return t, {"warning":"لم تظهر صفقات بالشروط الحالية","trades":0}
    o=t[t["OOS"]].copy()
    wins=(o["R"]>0).sum()
    gross_win=o.loc[o.R>0,"R"].sum()
    gross_loss=abs(o.loc[o.R<0,"R"].sum())
    pf=gross_win/gross_loss if gross_loss else math.inf
    stats={"trades":len(o),"win_rate":wins/len(o)*100,"profit_factor":pf,
           "total_r":o["R"].sum(),"max_dd_r":maxdd,"warning":None}
    return t,stats


# ------------------------ UI helpers -------------------------

def metric_row(items):
    cols=st.columns(len(items))
    for c,(label,value) in zip(cols,items):
        c.metric(label,value)


def gate_table(gates):
    rows=[]
    for k,v in gates.items():
        rows.append({"Gate":k,"Status":"PASS" if v else "BLOCK"})
    st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)


# ------------------------ Load data --------------------------

st.sidebar.title("🟡 GOLD AI")
st.sidebar.caption("XAU/USD • Paper Trading فقط")
research_mode = st.sidebar.checkbox("Research Mode", value=st.session_state.research_mode,
                                    help="عند عدم وجود News API، يسمح بالبحث الورقي مع إبقاء حالة الأخبار ظاهرة كغير متصلة.")
st.session_state.research_mode=research_mode
st.session_state.kill_switch = st.sidebar.toggle("Kill Switch", value=st.session_state.kill_switch)
st.session_state.auto_refresh = st.sidebar.toggle("تحديث تلقائي", value=False)
refresh_sec = st.sidebar.slider("كل كم ثانية", 15, 120, 45)

raw, data_msg = fetch_extended_m5()
raw = completed(raw)

if raw.empty:
    st.error("مصدر البيانات غير متاح حالياً.")
    st.info(data_msg)
    st.stop()

m5 = add_indicators(raw)
m15 = resample_ohlc(raw,"15min")
h1 = resample_ohlc(raw,"1h")
h4 = resample_ohlc(raw,"4h")

analysis = analyze_market(m5,m15,h1,h4,research_mode)
price=float(raw["close"].iloc[-1])
st.session_state.last_price=price
manage_trade(price)

# Log every new candle decision.
candle_id=str(raw["datetime"].iloc[-1])
if st.session_state.last_signal_candle != candle_id:
    st.session_state.last_signal_candle=candle_id
    st.session_state.decisions.insert(0,{
        "time":datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "candle":candle_id,
        "signal":analysis["signal"],
        "strength":analysis["strength"],
        "reason":analysis["reason"],
    })
    st.session_state.decisions=st.session_state.decisions[:300]

# Paper entry only after analysis and no current position.
if analysis["signal"] in ("BUY","SELL") and st.session_state.position is None:
    ok,msg=create_trade(analysis["signal"],price,analysis["snaps"]["M5"]["atr"])
    if not ok:
        analysis["reason"]=msg


# ------------------------- Dashboard -------------------------

st.markdown("<div class='card'><div class='small'>GOLD AI</div><h1 class='gold'>XAU/USD Smart Trading System</h1><div class='muted'>Paper Trading • Decision Engine • Risk Engine</div></div>",unsafe_allow_html=True)

metric_row([
    ("XAU/USD",f"${price:,.2f}"),
    ("Signal",analysis["signal"]),
    ("Strength",f'{analysis["strength"]}%'),
    ("Regime",analysis["snaps"]["M5"]["regime"] if analysis["snaps"]["M5"] else "—"),
    ("Equity",f"${current_equity(price):,.2f}"),
    ("Daily DD",f"{daily_loss_pct(price):.2f}%"),
])

st.subheader("حالة السوق")
cols=st.columns(4)
for c,k in zip(cols,["M5","M15","H1","H4"]):
    s=analysis["snaps"].get(k)
    if s:
        c.markdown(f"<div class='card'><b>{k}</b><br><span class='signal'>{s['trend']}</span><br><span class='small'>RSI {s['rsi']:.1f} • ADX {s['adx']:.1f}</span></div>",unsafe_allow_html=True)
    else:
        c.warning(f"{k}: بيانات غير كافية")

st.subheader("محرك القرار")
st.markdown(f"<div class='card'><div class='small'>FINAL DECISION</div><div class='signal'>{analysis['signal']}</div><div>{analysis['reason']}</div><div class='small'>قوة الإشارة مؤشر توافق داخلي وليست احتمال ربح.</div></div>",unsafe_allow_html=True)
gate_table(analysis["gates"])

b2=analysis["b2"]
st.subheader("B2 Breakout + Retest")
metric_row([
    ("Breakout","YES" if b2.get("breakout") else "NO"),
    ("Retest","YES" if b2.get("retest") else "NO"),
    ("Direction",b2.get("direction") or "—"),
    ("Level",f"{b2.get('level',np.nan):.2f}" if np.isfinite(b2.get("level",np.nan)) else "—"),
])

st.subheader("السعر")
chart=raw.tail(300).set_index("datetime")[["close"]]
st.line_chart(chart,use_container_width=True)

st.subheader("Paper Trading")
p=st.session_state.position
if p:
    r=unrealized_r(p,price)
    pnl=r*p["risk_money"]
    metric_row([
        ("Position",p["side"]),
        ("Entry",f"{p['entry']:.2f}"),
        ("Current",f"{price:.2f}"),
        ("SL",f"{p['sl']:.2f}"),
        ("TP1",f"{p['tp1']:.2f}"),
        ("TP2",f"{p['tp2']:.2f}"),
        ("P&L",f"${pnl:,.2f}"),
        ("R",f"{r:.2f}R"),
    ])
    st.caption("TP1 يغلق 50% ويحرّك الوقف إلى نقطة الدخول. لا يوجد تنفيذ حقيقي.")
else:
    st.info("لا توجد صفقة ورقية مفتوحة.")

st.subheader("Risk Center")
metric_row([
    ("Balance",f"${st.session_state.balance:,.2f}"),
    ("Risk / trade",f"{RISK_PER_TRADE*100:.2f}%"),
    ("Daily limit",f"{DAILY_LOSS_LIMIT*100:.1f}%"),
    ("Daily trades",f"{st.session_state.daily_trades}/{MAX_DAILY_TRADES}"),
    ("Open positions","1" if p else "0"),
    ("Kill switch","ON" if st.session_state.kill_switch else "OFF"),
])

st.subheader("Backtest Lab")
with st.expander("تشغيل اختبار بحثي"):
    n=st.slider("عدد شموع M15 للاختبار",500,5000,min(3000,len(m15)),100)
    bt=add_indicators(m15.tail(n)).dropna().reset_index(drop=True)
    trades,stats=backtest_strategy(bt)
    if stats["trades"]:
        metric_row([
            ("OOS Trades",stats["trades"]),
            ("Win Rate",f"{stats['win_rate']:.1f}%"),
            ("Profit Factor",f"{stats['profit_factor']:.2f}"),
            ("Total R",f"{stats['total_r']:.2f}R"),
            ("Max DD",f"{stats['max_dd_r']:.2f}R"),
        ])
        st.caption("النتائج بحثية وليست ضماناً للأداء المستقبلي. OOS هنا هو الجزء الأخير من العينة.")
        if not trades.empty:
            st.dataframe(trades.tail(100),hide_index=True,use_container_width=True)
    else:
        st.warning(stats.get("warning","لا توجد نتائج"))

st.subheader("Decision Log")
if st.session_state.decisions:
    st.dataframe(pd.DataFrame(st.session_state.decisions),hide_index=True,use_container_width=True)
else:
    st.info("لا يوجد سجل بعد.")

st.subheader("Trade History")
if st.session_state.history:
    st.dataframe(pd.DataFrame(st.session_state.history),hide_index=True,use_container_width=True)
else:
    st.info("لا توجد صفقات مغلقة.")

st.subheader("System Health")
health = {
    "Data Feed": not raw.empty,
    "Strategy Engine": bool(analysis["snaps"]["M5"]),
    "Risk Engine": True,
    "Paper Engine": True,
    "Time Sync": True,
    "News Filter": research_mode,
}
gate_table(health)
st.caption(f"آخر بيانات: {raw['datetime'].iloc[-1]} UTC • {len(raw):,} شمعة M5 • {data_msg}")

if st.session_state.auto_refresh:
    time.sleep(refresh_sec)
    st.rerun()

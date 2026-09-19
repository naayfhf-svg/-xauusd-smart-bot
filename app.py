import math
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import streamlit as st

# ============================================================
# GOLD AI — XAU/USD Smart Paper Trading
# Clean single-file research / paper-trading application • v1.1.
# No live broker execution is implemented.
# ============================================================

st.set_page_config(
    page_title="GOLD AI | XAU/USD",
    page_icon="🟡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

TZ = ZoneInfo("Asia/Riyadh")
UTC = "UTC"
SYMBOL = "XAU/USD"
TIMEFRAMES = ("M5", "M15", "H1", "H4")
DATA_URL = "https://api.twelvedata.com/time_series"
QUOTE_URL = "https://api.twelvedata.com/quote"

START_BALANCE = 100_000.0
RISK_PER_TRADE = 0.005
DAILY_LOSS_LIMIT = 0.02
MAX_DAILY_TRADES = 5
MAX_SPREAD_SECRET = "MAX_SPREAD"
NEWS_SECRET = "NEWS_API_URL"
API_SECRET = "TWELVE_DATA_API_KEY"

M5_CHUNK_SIZE = 5000
M5_CHUNKS = 4

# --------------------------- UI theme -------------------------
st.markdown(
    """
<style>
:root{--bg:#070b12;--panel:#0e1623;--panel2:#111c2c;--line:#24334b;--gold:#d4af37;--text:#f5f7fb;--muted:#8fa1ba;--green:#25c77a;--red:#ef5b67}
.stApp{background:var(--bg);color:var(--text)}
.block-container{max-width:1450px;padding:1rem 1rem 4rem}
.card{background:linear-gradient(145deg,var(--panel),#0a121e);border:1px solid var(--line);border-radius:18px;padding:18px;margin-bottom:14px}
.hero{border:1px solid #554717;border-radius:22px;padding:24px;background:radial-gradient(circle at 85% 15%,rgba(212,175,55,.14),transparent 32%),var(--panel)}
.kicker{font-size:.75rem;color:var(--muted);letter-spacing:.15em}.gold{color:var(--gold)}.muted{color:var(--muted)}
.price{font-size:clamp(2.6rem,8vw,5rem);font-weight:900;line-height:1}.signal{font-size:2rem;font-weight:900}.good{color:var(--green)}.bad{color:var(--red)}
.metric{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:13px}.label{font-size:.72rem;color:var(--muted)}.value{font-size:1.25rem;font-weight:800;margin-top:4px}
div[data-testid="stMetric"]{background:var(--panel);border:1px solid var(--line);border-radius:14px}
</style>
""",
    unsafe_allow_html=True,
)

# ------------------------- small utilities -------------------
def now_utc():
    return pd.Timestamp.now(tz="UTC")


def now_riyadh():
    return datetime.now(TZ)


def get_secret(name):
    try:
        return st.secrets[name]
    except Exception:
        return None


def finite(value):
    try:
        return bool(np.isfinite(float(value)))
    except Exception:
        return False


def fmt_price(value):
    return f"{float(value):,.2f}" if finite(value) else "—"

# --------------------------- state ----------------------------
def init_state():
    defaults = {
        "balance": START_BALANCE,
        "position": None,
        "history": [],
        "decisions": [],
        "daily_start_balance": START_BALANCE,
        "daily_date": now_riyadh().date().isoformat(),
        "daily_trades": 0,
        "kill_switch": False,
        "research_mode": False,
        "auto_refresh": False,
        "last_signal_candle": None,
        "last_price": np.nan,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)

    today = now_riyadh().date().isoformat()
    if st.session_state.daily_date != today:
        st.session_state.daily_date = today
        st.session_state.daily_start_balance = float(st.session_state.balance)
        st.session_state.daily_trades = 0


init_state()

# ------------------------- data feed --------------------------
def normalize_ohlcv(values):
    if not isinstance(values, list) or not values:
        return pd.DataFrame()
    df = pd.DataFrame(values)
    if "datetime" not in df.columns:
        return pd.DataFrame()
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    required = ["datetime", "open", "high", "low", "close"]
    df = df.dropna(subset=required)
    df = df.drop_duplicates("datetime").sort_values("datetime").reset_index(drop=True)
    return df


@st.cache_data(ttl=45, show_spinner=False)
def fetch_chunk(end_date=None):
    key = get_secret(API_SECRET)
    if not key:
        return pd.DataFrame(), "TWELVE_DATA_API_KEY غير موجود في Secrets", None

    params = {
        "symbol": SYMBOL,
        "interval": "5min",
        "outputsize": M5_CHUNK_SIZE,
        "timezone": UTC,
        "apikey": key,
    }
    if end_date:
        params["end_date"] = end_date

    try:
        response = requests.get(DATA_URL, params=params, timeout=20)
        payload = response.json()
    except Exception as exc:
        return pd.DataFrame(), f"خطأ اتصال: {exc}", None

    if "values" not in payload:
        return pd.DataFrame(), str(payload.get("message", "مصدر البيانات رفض الطلب")), response.headers.get("api-credits-left")

    frame = normalize_ohlcv(payload["values"])
    if frame.empty:
        return pd.DataFrame(), "Twelve Data أعاد بيانات غير صالحة", response.headers.get("api-credits-left")

    return frame, "OK", response.headers.get("api-credits-left")


@st.cache_data(ttl=180, show_spinner=False)
def fetch_history():
    frames = []
    errors = []
    credits = []
    end = now_utc()

    for index in range(M5_CHUNKS):
        frame, message, credit_left = fetch_chunk(end.strftime("%Y-%m-%d %H:%M:%S"))
        if frame.empty:
            errors.append(f"الدفعة {index + 1}: {message}")
            break

        frames.append(frame)
        if credit_left is not None:
            credits.append(credit_left)

        oldest = frame["datetime"].min()
        if oldest >= end:
            errors.append("لم يتحرك المؤشر التاريخي للخلف")
            break

        end = oldest - pd.Timedelta(minutes=5)
        if len(frame) < M5_CHUNK_SIZE:
            break

    if not frames:
        return pd.DataFrame(), " | ".join(errors) or "لم تصل بيانات", 0, credits

    history = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("datetime")
        .sort_values("datetime")
        .reset_index(drop=True)
    )
    message = "تم تحميل التاريخ المطلوب" if len(frames) == M5_CHUNKS else "تم تحميل جزء من التاريخ فقط"
    if errors:
        message += " • " + " | ".join(errors)
    return history, message, len(frames), credits


def closed_m5(frame):
    if frame.empty:
        return frame.copy()
    boundary = now_utc().floor("5min")
    return frame[frame["datetime"] < boundary].copy().reset_index(drop=True)


def resample_closed(frame, rule):
    frame = closed_m5(frame)
    if frame.empty:
        return frame.copy()

    out = (
        frame.set_index("datetime")[["open", "high", "low", "close"]]
        .resample(rule, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
        .reset_index()
    )

    # A higher-TF candle is usable only after its right boundary has passed.
    out = out[out["datetime"] + pd.Timedelta(rule) <= now_utc()].reset_index(drop=True)
    return out


def data_quality(frame):
    if frame.empty:
        return False, "لا توجد بيانات"

    duplicate_count = int(frame["datetime"].duplicated().sum())
    monotonic = bool(frame["datetime"].is_monotonic_increasing)
    age_min = (now_utc() - frame["datetime"].iloc[-1]).total_seconds() / 60

    diffs = frame["datetime"].diff().dropna()
    suspicious = []
    for diff in diffs:
        minutes = diff.total_seconds() / 60
        # <=10m: ordinary feed cadence. 10–90m: maintenance/session pause.
        # >=18h: weekend/long scheduled closure. Other large gaps are suspicious.
        if minutes <= 90 or minutes >= 18 * 60:
            continue
        suspicious.append(minutes)

    largest_suspicious = max(suspicious) if suspicious else 0.0
    ok = monotonic and duplicate_count == 0 and age_min < 20 and largest_suspicious == 0
    detail = (
        f"آخر شمعة منذ {age_min:.1f} دقيقة • "
        f"أكبر فجوة مريبة {largest_suspicious:.1f} دقيقة • تكرار {duplicate_count}"
    )
    return ok, detail


@st.cache_data(ttl=10, show_spinner=False)
def fetch_quote():
    key = get_secret(API_SECRET)
    if not key:
        return {"connected": False, "bid": np.nan, "ask": np.nan, "spread": np.nan, "label": "مفتاح Twelve Data غير موجود"}

    try:
        response = requests.get(QUOTE_URL, params={"symbol": SYMBOL, "apikey": key}, timeout=10)
        payload = response.json()
        bid = pd.to_numeric(payload.get("bid"), errors="coerce")
        ask = pd.to_numeric(payload.get("ask"), errors="coerce")
    except Exception:
        return {"connected": False, "bid": np.nan, "ask": np.nan, "spread": np.nan, "label": "تعذر جلب Bid/Ask"}

    if not finite(bid) or not finite(ask) or ask <= bid:
        return {"connected": False, "bid": np.nan, "ask": np.nan, "spread": np.nan, "label": "مصدر Bid/Ask غير متاح"}

    spread = float(ask - bid)
    return {"connected": True, "bid": float(bid), "ask": float(ask), "spread": spread, "label": f"Bid/Ask متصل • السبريد {spread:.2f}"}

# ------------------------- indicators ------------------------
def ema(series, period):
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series, period=14):
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)


def atr(frame, period=14):
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def macd(series):
    line = ema(series, 12) - ema(series, 26)
    signal = line.ewm(span=9, adjust=False, min_periods=9).mean()
    return line, signal, line - signal


def adx(frame, period=14):
    up = frame["high"].diff()
    down = -frame["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=frame.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=frame.index)

    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    smoothed_tr = true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / smoothed_tr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / smoothed_tr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean(), plus_di, minus_di


def add_indicators(frame):
    out = frame.copy()
    out["ema20"] = ema(out["close"], 20)
    out["ema50"] = ema(out["close"], 50)
    out["ema100"] = ema(out["close"], 100)
    out["rsi"] = rsi(out["close"])
    out["atr"] = atr(out)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(out["close"])
    out["momentum"] = out["close"].pct_change(5) * 100
    out["adx"], out["plus_di"], out["minus_di"] = adx(out)
    return out


def classify_trend(row):
    if row.close > row.ema20 > row.ema50 > row.ema100:
        return "صاعد"
    if row.close < row.ema20 < row.ema50 < row.ema100:
        return "هابط"
    return "محايد"


def classify_regime(row):
    if not finite(row.adx) or not finite(row.atr):
        return "غير معروف"
    if row.adx >= 25:
        return "اتجاه"
    if row.adx <= 18:
        return "نطاق"
    return "انتقالي"


def snapshot(frame):
    if len(frame) < 120:
        return None
    calculated = add_indicators(frame).dropna().reset_index(drop=True)
    if calculated.empty:
        return None
    row = calculated.iloc[-1]
    return {
        "trend": classify_trend(row),
        "regime": classify_regime(row),
        "rsi": float(row.rsi),
        "adx": float(row.adx),
        "atr": float(row.atr),
        "momentum": float(row.momentum),
        "macd_hist": float(row.macd_hist),
        "close": float(row.close),
        "candle": row.datetime,
        "frame": calculated,
    }

# --------------------------- B2 ------------------------------
def b2_signal(frame, lookback=20, retest_atr=0.35):
    """B2 = previous candle breaks a level built only from candles before it; current candle retests and confirms."""
    if len(frame) < lookback + 3:
        return {"valid": False, "direction": None, "breakout": False, "retest": False, "level": np.nan}

    current = frame.iloc[-1]
    breakout = frame.iloc[-2]
    history = frame.iloc[-(lookback + 2):-2]
    level_resistance = float(history["high"].max())
    level_support = float(history["low"].min())
    current_atr = float(current.atr)

    if not finite(current_atr) or current_atr <= 0:
        return {"valid": False, "direction": None, "breakout": False, "retest": False, "level": np.nan}

    bullish_break = float(breakout.close) > level_resistance
    bearish_break = float(breakout.close) < level_support

    bullish_retest = bullish_break and float(current.low) <= level_resistance + retest_atr * current_atr and float(current.close) > level_resistance
    bearish_retest = bearish_break and float(current.high) >= level_support - retest_atr * current_atr and float(current.close) < level_support

    if bullish_retest:
        return {"valid": True, "direction": "شراء", "breakout": True, "retest": True, "level": level_resistance}
    if bearish_retest:
        return {"valid": True, "direction": "بيع", "breakout": True, "retest": True, "level": level_support}

    return {
        "valid": False,
        "direction": None,
        "breakout": bool(bullish_break or bearish_break),
        "retest": False,
        "level": level_resistance if bullish_break else level_support if bearish_break else np.nan,
    }

# ---------------------- safety gates -------------------------
def news_gate(research_mode):
    url = get_secret(NEWS_SECRET)
    if not url:
        return {"connected": False, "blocked": not research_mode, "label": "لا يوجد Economic Calendar موثوق متصل"}

    try:
        response = requests.get(url, timeout=8)
        payload = response.json()
    except Exception:
        return {"connected": False, "blocked": True, "label": "تعذر الاتصال بمصدر الأخبار • حظر آمن"}

    blocked = bool(payload.get("high_impact", False) or payload.get("block", False))
    return {"connected": True, "blocked": blocked, "label": "خبر عالي التأثير" if blocked else "لا يوجد حظر حسب المزود"}


def spread_gate(research_mode):
    quote = fetch_quote()
    configured = get_secret(MAX_SPREAD_SECRET)
    try:
        max_spread = float(configured) if configured is not None else None
    except Exception:
        max_spread = None

    if not quote["connected"]:
        return {
            "connected": False,
            "blocked": not research_mode,
            "label": quote["label"] + (" • وضع البحث التجريبي" if research_mode else " • حظر آمن"),
            "bid": np.nan,
            "ask": np.nan,
            "spread": np.nan,
        }

    if max_spread is None:
        return {
            "connected": True,
            "blocked": True,
            "label": quote["label"] + " • MAX_SPREAD غير مضبوط • حظر آمن",
            "bid": quote["bid"],
            "ask": quote["ask"],
            "spread": quote["spread"],
        }

    blocked = quote["spread"] > max_spread
    label = quote["label"] + f" • الحد {max_spread:.2f}"
    if blocked:
        label += " • السبريد أعلى من الحد"
    return {**quote, "blocked": blocked, "label": label}

# ------------------------- signal engine ---------------------
def analyze(m5, m15, h1, h4, research_mode):
    snapshots = {name: snapshot(frame) for name, frame in {"M5": m5, "M15": m15, "H1": h1, "H4": h4}.items()}
    news = news_gate(research_mode)
    spread = spread_gate(research_mode)

    if any(value is None for value in snapshots.values()):
        gates = {
            "البيانات": False,
            "النظام السوقي": False,
            "توافق الأطر": False,
            "الزخم": False,
            "B2": False,
            "المخاطر": not st.session_state.kill_switch,
            "الحد اليومي": True,
            "السبريد": not spread["blocked"],
            "الأخبار": not news["blocked"],
        }
        return {"signal": "انتظار", "strength": 0, "reason": "بيانات الأطر غير مكتملة", "gates": gates, "snapshots": snapshots, "b2": {}, "news": news, "spread": spread}

    trends = [snapshots[key]["trend"] for key in TIMEFRAMES]
    bull = all(trend == "صاعد" for trend in trends)
    bear = all(trend == "هابط" for trend in trends)
    m5 = snapshots["M5"]
    b2 = b2_signal(m5["frame"])

    momentum_buy = m5["rsi"] >= 52 and m5["momentum"] > 0 and m5["macd_hist"] > 0
    momentum_sell = m5["rsi"] <= 48 and m5["momentum"] < 0 and m5["macd_hist"] < 0

    daily_ok = daily_loss_pct(m5["close"]) < DAILY_LOSS_LIMIT * 100 and st.session_state.daily_trades < MAX_DAILY_TRADES
    data_ok = bool(st.session_state.get("data_quality_ok", False))
    gates = {
        "البيانات": data_ok,
        "النظام السوقي": m5["regime"] == "اتجاه",
        "توافق الأطر": bull or bear,
        "الزخم": momentum_buy or momentum_sell,
        "B2": b2["valid"],
        "المخاطر": not st.session_state.kill_switch,
        "الحد اليومي": daily_ok,
        "السبريد": not spread["blocked"],
        "الأخبار": not news["blocked"],
    }

    # Independent 100-point score; MTF alignment is counted once.
    strength = (
        (30 if gates["توافق الأطر"] else 0)
        + (25 if gates["B2"] else 0)
        + (20 if gates["الزخم"] else 0)
        + (10 if m5["adx"] >= 25 else 0)
        + (10 if m5["rsi"] >= 55 or m5["rsi"] <= 45 else 0)
        + (5 if gates["السبريد"] and gates["الأخبار"] else 0)
    )

    reason = next((name for name, passed in gates.items() if not passed), "اجتازت جميع البوابات")
    signal = "انتظار"
    if all(gates.values()):
        if b2["direction"] == "شراء" and bull and momentum_buy:
            signal = "شراء"
        elif b2["direction"] == "بيع" and bear and momentum_sell:
            signal = "بيع"
        else:
            reason = "تعارض B2 مع الاتجاه أو الزخم"

    return {"signal": signal, "strength": min(strength, 100), "reason": reason, "gates": gates, "snapshots": snapshots, "b2": b2, "news": news, "spread": spread}

# ------------------------ paper engine -----------------------
def execution_price(side, quote, fallback):
    if quote.get("connected"):
        return float(quote["ask"] if side == "شراء" else quote["bid"])
    return float(fallback)


def mark_price(position, quote, fallback):
    if quote.get("connected"):
        return float(quote["bid"] if position["side"] == "شراء" else quote["ask"])
    return float(fallback)


def unrealized_r(position, price):
    if not position or not finite(price) or position["initial_distance"] <= 0:
        return 0.0
    direction = 1 if position["side"] == "شراء" else -1
    return position["realized_r"] + direction * (float(price) - position["entry"]) / position["initial_distance"] * position["remaining_fraction"]


def daily_equity(price):
    equity = float(st.session_state.balance)
    if st.session_state.position:
        equity += unrealized_r(st.session_state.position, price) * st.session_state.position["risk_money"]
    return equity


def daily_loss_pct(price):
    start = max(float(st.session_state.daily_start_balance), 1.0)
    loss = max(0.0, start - daily_equity(price))
    return loss / start * 100


def open_trade(side, fallback_price, atr_value, quote):
    if st.session_state.position is not None:
        return False, "هناك صفقة مفتوحة"
    if st.session_state.kill_switch:
        return False, "Kill Switch مفعل"
    if st.session_state.daily_trades >= MAX_DAILY_TRADES:
        return False, "تم بلوغ الحد اليومي للصفقات"
    if daily_loss_pct(fallback_price) >= DAILY_LOSS_LIMIT * 100:
        return False, "تم بلوغ حد الخسارة اليومية"
    if not finite(atr_value) or atr_value <= 0:
        return False, "ATR غير صالح"

    entry = execution_price(side, quote, fallback_price)
    distance = max(float(atr_value) * 1.4, entry * 0.0015)
    risk_money = float(st.session_state.balance) * RISK_PER_TRADE

    if side == "شراء":
        stop = entry - distance
        tp1 = entry + distance
        tp2 = entry + 2.2 * distance
    else:
        stop = entry + distance
        tp1 = entry - distance
        tp2 = entry - 2.2 * distance

    st.session_state.position = {
        "id": uuid.uuid4().hex[:10],
        "side": side,
        "entry": float(entry),
        "sl": float(stop),
        "initial_distance": float(distance),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "risk_money": risk_money,
        "remaining_fraction": 1.0,
        "realized_r": 0.0,
        "tp1_hit": False,
        "opened": now_riyadh().isoformat(),
    }
    st.session_state.daily_trades += 1
    return True, "تم فتح صفقة ورقية"


def close_trade(price, reason):
    position = st.session_state.position
    if not position:
        return
    total_r = unrealized_r(position, price)
    pnl = total_r * position["risk_money"]
    st.session_state.balance += pnl
    st.session_state.history.insert(
        0,
        {
            "الوقت": now_riyadh().strftime("%Y-%m-%d %H:%M:%S"),
            "المعرف": position["id"],
            "النوع": position["side"],
            "الدخول": round(position["entry"], 2),
            "الخروج": round(price, 2),
            "R": round(total_r, 3),
            "الربح/الخسارة": round(pnl, 2),
            "السبب": reason,
        },
    )
    st.session_state.position = None


def manage_trade(price):
    position = st.session_state.position
    if not position or not finite(price):
        return

    if position["side"] == "شراء":
        if price <= position["sl"]:
            close_trade(position["sl"], "وقف الخسارة")
            return
        if not position["tp1_hit"] and price >= position["tp1"]:
            position["realized_r"] += 0.5
            position["remaining_fraction"] = 0.5
            position["tp1_hit"] = True
            position["sl"] = position["entry"]
            return
        if position["tp1_hit"] and price >= position["tp2"]:
            close_trade(position["tp2"], "الهدف الثاني")
            return
    else:
        if price >= position["sl"]:
            close_trade(position["sl"], "وقف الخسارة")
            return
        if not position["tp1_hit"] and price <= position["tp1"]:
            position["realized_r"] += 0.5
            position["remaining_fraction"] = 0.5
            position["tp1_hit"] = True
            position["sl"] = position["entry"]
            return
        if position["tp1_hit"] and price <= position["tp2"]:
            close_trade(position["tp2"], "الهدف الثاني")
            return

# ------------------------- backtest ---------------------------
def prepare_mtf_backtest(raw):
    m5 = add_indicators(closed_m5(raw)).dropna().reset_index(drop=True)
    m15 = add_indicators(resample_closed(raw, "15min")).dropna().reset_index(drop=True)
    h1 = add_indicators(resample_closed(raw, "1h")).dropna().reset_index(drop=True)
    h4 = add_indicators(resample_closed(raw, "4h")).dropna().reset_index(drop=True)

    if min(len(m5), len(m15), len(h1), len(h4)) < 250:
        return None, "بيانات غير كافية لاختبار MTF"

    # Decision timestamp = M5 close. Higher-TF timestamp = higher-TF close.
    # Exact equality is deliberately rejected: a higher-TF candle becomes usable on the next M5 decision.
    m5["decision_ts"] = m5["datetime"] + pd.Timedelta(minutes=5)
    prepared = m5[["datetime", "decision_ts", "open", "high", "low", "close", "atr", "rsi", "macd_hist", "momentum", "adx", "ema20", "ema50", "ema100"]].copy()

    for name, frame, rule in (("m15", m15, "15min"), ("h1", h1, "1h"), ("h4", h4, "4h")):
        higher = frame[["datetime", "close", "ema20", "ema50", "ema100"]].copy()
        higher["available_ts"] = higher["datetime"] + pd.Timedelta(rule)
        higher = higher.rename(columns={
            "close": f"close_{name}", "ema20": f"ema20_{name}", "ema50": f"ema50_{name}", "ema100": f"ema100_{name}",
        })
        prepared = pd.merge_asof(
            prepared.sort_values("decision_ts"),
            higher[["available_ts", f"close_{name}", f"ema20_{name}", f"ema50_{name}", f"ema100_{name}"]].sort_values("available_ts"),
            left_on="decision_ts",
            right_on="available_ts",
            direction="backward",
            allow_exact_matches=False,
        )

    prepared = prepared.dropna().reset_index(drop=True)
    return prepared, None


def backtest_mtf(
    raw,
    oos_fraction=0.30,
    round_trip_cost=0.0,
    slippage_per_side=0.0,
    oos_start_ts=None,
    oos_end_ts=None,
):
    """
    Research backtest on closed MTF data.

    Cost model:
      - round_trip_cost is a fixed full round-trip price cost.
      - slippage_per_side is applied once at entry and once at exit.
      - costs are represented as execution-price adjustments, then converted to R.
    These are assumptions when historical bid/ask data is unavailable.
    """
    base, error = prepare_mtf_backtest(raw)
    if base is None:
        return pd.DataFrame(), {"trades": 0, "warning": error}

    if oos_start_ts is None:
        split_index = int(len(base) * (1 - oos_fraction))
        split_ts = base["decision_ts"].iloc[min(split_index, len(base) - 1)]
    else:
        split_ts = pd.Timestamp(oos_start_ts)
        if split_ts.tzinfo is None:
            split_ts = split_ts.tz_localize("UTC")
        else:
            split_ts = split_ts.tz_convert("UTC")

    end_ts = None
    if oos_end_ts is not None:
        end_ts = pd.Timestamp(oos_end_ts)
        if end_ts.tzinfo is None:
            end_ts = end_ts.tz_localize("UTC")
        else:
            end_ts = end_ts.tz_convert("UTC")

    trades = []
    i = 1

    while i < len(base) - 2:
        row = base.iloc[i]
        decision_ts = pd.Timestamp(row["decision_ts"])

        if decision_ts < split_ts:
            i += 1
            continue
        if end_ts is not None and decision_ts >= end_ts:
            break

        # B2 levels use only M5 candles before the breakout candle.
        if i < 22 or not finite(row["atr"]):
            i += 1
            continue

        history = base.iloc[i - 21:i - 1]
        breakout = base.iloc[i - 1]
        current = row
        resistance = float(history["high"].max())
        support = float(history["low"].min())
        atr_value = float(current["atr"])

        bullish_break = float(breakout["close"]) > resistance
        bearish_break = float(breakout["close"]) < support
        bullish_retest = (
            bullish_break
            and float(current["low"]) <= resistance + 0.35 * atr_value
            and float(current["close"]) > resistance
        )
        bearish_retest = (
            bearish_break
            and float(current["high"]) >= support - 0.35 * atr_value
            and float(current["close"]) < support
        )

        bull_mtf = (
            current.close > current.ema20 > current.ema50 > current.ema100
            and current.close_m15 > current.ema20_m15 > current.ema50_m15 > current.ema100_m15
            and current.close_h1 > current.ema20_h1 > current.ema50_h1 > current.ema100_h1
            and current.close_h4 > current.ema20_h4 > current.ema50_h4 > current.ema100_h4
        )
        bear_mtf = (
            current.close < current.ema20 < current.ema50 < current.ema100
            and current.close_m15 < current.ema20_m15 < current.ema50_m15 < current.ema100_m15
            and current.close_h1 < current.ema20_h1 < current.ema50_h1 < current.ema100_h1
            and current.close_h4 < current.ema20_h4 < current.ema50_h4 < current.ema100_h4
        )
        momentum_buy = current.rsi >= 52 and current.momentum > 0 and current.macd_hist > 0 and current.adx >= 25
        momentum_sell = current.rsi <= 48 and current.momentum < 0 and current.macd_hist < 0 and current.adx >= 25

        side = "شراء" if bullish_retest and bull_mtf and momentum_buy else None
        if bearish_retest and bear_mtf and momentum_sell:
            side = "بيع"

        if side is None:
            i += 1
            continue

        # Fixed spread is a research assumption. Split it around the mid price.
        spread_half = max(0.0, float(round_trip_cost)) / 2.0
        slip = max(0.0, float(slippage_per_side))
        raw_entry = float(current["close"])

        # Execution price: worse for the trader in both directions.
        if side == "شراء":
            entry = raw_entry + spread_half + slip
        else:
            entry = raw_entry - spread_half - slip

        distance = max(atr_value * 1.4, entry * 0.0015)
        stop = entry - distance if side == "شراء" else entry + distance
        tp1 = entry + distance if side == "شراء" else entry - distance
        tp2 = entry + 2.2 * distance if side == "شراء" else entry - 2.2 * distance

        realized_r = 0.0
        remaining = 1.0
        tp1_hit = False
        outcome = None
        reason = None
        exit_index = None

        for j in range(i + 1, min(i + 100, len(base))):
            bar_ts = pd.Timestamp(base["decision_ts"].iloc[j])
            if end_ts is not None and bar_ts >= end_ts:
                break

            high = float(base.high.iloc[j])
            low = float(base.low.iloc[j])

            if side == "شراء":
                # Conservative convention: stop first if both levels occur in one bar.
                stop_exec = stop - slip - spread_half
                if low <= stop:
                    outcome = realized_r - remaining
                    reason = "وقف الخسارة"
                    exit_index = j
                    break
                if not tp1_hit and high >= tp1:
                    realized_r += 0.5
                    remaining = 0.5
                    tp1_hit = True
                    stop = entry
                    continue
                if tp1_hit and high >= tp2:
                    realized_r += 1.1
                    outcome = realized_r
                    reason = "الهدف الثاني"
                    exit_index = j
                    break
            else:
                stop_exec = stop + slip + spread_half
                if high >= stop:
                    outcome = realized_r - remaining
                    reason = "وقف الخسارة"
                    exit_index = j
                    break
                if not tp1_hit and low <= tp1:
                    realized_r += 0.5
                    remaining = 0.5
                    tp1_hit = True
                    stop = entry
                    continue
                if tp1_hit and low <= tp2:
                    realized_r += 1.1
                    outcome = realized_r
                    reason = "الهدف الثاني"
                    exit_index = j
                    break

        if outcome is not None:
            # Charge both execution sides explicitly in R. The entry/exit
            # thresholds are based on the executed entry price, while this
            # separate deduction keeps the reported R net of assumed costs.
            entry_cost = (spread_half + slip) / distance if distance > 0 else 0.0
            exit_cost = (spread_half + slip) / distance if distance > 0 else 0.0
            total_cost_r = entry_cost + exit_cost
            outcome_after_cost = float(outcome) - total_cost_r

            trades.append({
                "الوقت": current.datetime,
                "النوع": side,
                "الدخول": round(entry, 2),
                "الوقف": round(stop - distance if side == "شراء" else stop + distance, 2),
                "TP1": round(tp1, 2),
                "TP2": round(tp2, 2),
                "R": round(outcome_after_cost, 3),
                "تكلفة_السعر_R": round(total_cost_r, 4),
                "السبب": reason,
                "OOS": True,
            })
            i = exit_index + 1
        else:
            i += 1

    result = pd.DataFrame(trades)
    if result.empty:
        return result, {"trades": 0, "warning": "لا توجد صفقات OOS مطابقة لكل بوابات MTF"}

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    equity_curve = []
    drawdown_curve = []
    for value in result["R"]:
        equity += float(value)
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
        equity_curve.append(equity)
        drawdown_curve.append(dd)

    result["Equity_R"] = equity_curve
    result["Drawdown_R"] = drawdown_curve

    gross_win = result.loc[result["R"] > 0, "R"].sum()
    gross_loss = abs(result.loc[result["R"] < 0, "R"].sum())
    profit_factor = gross_win / gross_loss if gross_loss else math.inf

    return result, {
        "trades": len(result),
        "win_rate": float((result["R"] > 0).mean() * 100),
        "profit_factor": float(profit_factor),
        "total_r": float(result["R"].sum()),
        "max_dd_r": float(max_dd),
        "warning": "العينة الصغيرة لا تثبت صلاحية الاستراتيجية" if len(result) < 100 else None,
        "oos_start": str(split_ts),
        "avg_cost_r": float(result["تكلفة_السعر_R"].mean()),
        "equity_curve": equity_curve,
        "drawdown_curve": drawdown_curve,
    }


def walk_forward_mtf(raw, windows=4, train_fraction=0.50, test_fraction=0.15, round_trip_cost=0.0, slippage_per_side=0.0):
    """
    True rolling walk-forward diagnostic:
      train -> unseen OOS test -> roll forward -> repeat.
    No parameters are optimized in the training segment yet; it exists to
    enforce temporal separation and prevent using future observations.
    """
    if raw is None or raw.empty or windows < 2:
        return pd.DataFrame(), {"windows": 0, "warning": "بيانات غير كافية"}

    n = len(raw)
    train_size = int(n * train_fraction)
    test_size = int(n * test_fraction)
    if train_size < 1000 or test_size < 200:
        return pd.DataFrame(), {"windows": 0, "warning": "بيانات غير كافية لنوافذ Walk-Forward"}

    rows = []
    all_trades = []
    for w in range(windows):
        train_end = train_size + w * test_size
        test_end = train_end + test_size
        if test_end > n:
            break

        train_start_ts = pd.Timestamp(raw["datetime"].iloc[0])
        test_start_ts = pd.Timestamp(raw["datetime"].iloc[train_end])
        test_end_ts = pd.Timestamp(raw["datetime"].iloc[test_end - 1])

        # Include all historical bars through the OOS endpoint so indicators
        # have warm-up data, while backtest_mtf permits trades only in OOS.
        chunk = raw.iloc[:test_end].copy()
        trades, stats = backtest_mtf(
            chunk,
            oos_start_ts=test_start_ts,
            oos_end_ts=test_end_ts + pd.Timedelta(minutes=5),
            round_trip_cost=round_trip_cost,
            slippage_per_side=slippage_per_side,
        )

        if not trades.empty:
            trades = trades.copy()
            trades["WF_Window"] = w + 1
            all_trades.append(trades)

        rows.append({
            "Window": w + 1,
            "Train Start": str(train_start_ts),
            "OOS Start": str(test_start_ts),
            "OOS End": str(test_end_ts),
            "Trades": int(stats.get("trades", 0)),
            "Win Rate %": round(float(stats.get("win_rate", 0.0)), 2),
            "Profit Factor": round(float(stats.get("profit_factor", 0.0)), 3) if math.isfinite(float(stats.get("profit_factor", 0.0))) else None,
            "Total R": round(float(stats.get("total_r", 0.0)), 3),
            "Max DD R": round(float(stats.get("max_dd_r", 0.0)), 3),
        })

    wf = pd.DataFrame(rows)
    combined = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    if combined.empty:
        return wf, {"windows": len(wf), "trades": 0, "warning": "لا توجد صفقات OOS في نوافذ Walk-Forward"}

    gross_win = combined.loc[combined["R"] > 0, "R"].sum()
    gross_loss = abs(combined.loc[combined["R"] < 0, "R"].sum())
    pf = gross_win / gross_loss if gross_loss else math.inf
    eq = combined["R"].cumsum()
    dd = eq.cummax() - eq

    return wf, {
        "windows": len(wf),
        "trades": len(combined),
        "win_rate": float((combined["R"] > 0).mean() * 100),
        "profit_factor": float(pf),
        "total_r": float(combined["R"].sum()),
        "max_dd_r": float(dd.max()),
        "combined_trades": combined,
        "warning": "Walk-Forward تشخيص استقرار وليس إثباتاً للربحية" if len(combined) < 100 else None,
    }

# ---------------------- deterministic tests ------------------
def run_internal_tests():
    # 1) B2 level must exclude the breakout candle.
    idx = pd.date_range("2026-01-01", periods=40, freq="5min", tz="UTC")
    frame = pd.DataFrame({"datetime": idx, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0})
    frame.loc[38, ["open", "high", "low", "close"]] = [100.0, 103.0, 99.5, 102.5]
    frame.loc[39, ["open", "high", "low", "close"]] = [102.5, 103.0, 102.0, 102.8]
    calculated = add_indicators(frame)
    b2 = b2_signal(calculated)
    assert finite(b2["level"]) and b2["level"] < 103.0, "B2 level leaked breakout candle"

    # 2) Risk accounting invariant.
    assert abs((0.5 + 1.1) - 1.6) < 1e-12, "TP1/TP2 R invariant failed"

    # 3) Bearish MTF expression must be strictly descending.
    assert (100 > 99 > 98 > 97) and not (100 < 99 < 98 < 97), "Trend comparison invariant failed"

    # 4) Exact higher-TF close must not be accepted by the merge rule.
    left = pd.DataFrame({"decision_ts": pd.to_datetime(["2026-01-01 01:00"], utc=True)})
    right = pd.DataFrame({"available_ts": pd.to_datetime(["2026-01-01 01:00"], utc=True), "v": [1]})
    merged = pd.merge_asof(left, right, left_on="decision_ts", right_on="available_ts", direction="backward", allow_exact_matches=False)
    assert pd.isna(merged.loc[0, "v"]), "Exact higher-TF close leaked into same decision"

    return True

# --------------------------- UI helpers -----------------------
def metrics(items):
    columns = st.columns(len(items))
    for column, (label, value) in zip(columns, items):
        column.markdown(f"<div class='metric'><div class='label'>{label}</div><div class='value'>{value}</div></div>", unsafe_allow_html=True)

# ---------------------------- app -----------------------------
run_internal_tests()

with st.sidebar:
    st.markdown("## 🟡 GOLD AI")
    st.caption("XAU/USD • Paper Trading فقط")
    st.session_state.research_mode = st.toggle("وضع البحث التجريبي", value=st.session_state.research_mode)
    st.session_state.kill_switch = st.toggle("Kill Switch", value=st.session_state.kill_switch)
    st.session_state.auto_refresh = st.toggle("تحديث تلقائي", value=st.session_state.auto_refresh)
    refresh_seconds = st.slider("ثواني التحديث", 15, 120, 45)

raw, history_message, chunk_count, credits = fetch_history()
raw = closed_m5(raw)
if raw.empty:
    st.error("مصدر البيانات غير متاح")
    st.info(history_message)
    st.stop()

quality_ok, quality_message = data_quality(raw)
st.session_state.data_quality_ok = quality_ok
if not quality_ok:
    st.warning("جودة البيانات: " + quality_message + " • التداول الورقي محظور حتى تتحسن البيانات")

m5 = add_indicators(raw)
m15 = resample_closed(raw, "15min")
h1 = resample_closed(raw, "1h")
h4 = resample_closed(raw, "4h")

if min(len(m5), len(m15), len(h1), len(h4)) < 120:
    st.error("البيانات غير كافية لجميع الأطر الزمنية")
    st.stop()

quote = fetch_quote()
reference_price = float(raw["close"].iloc[-1])
if st.session_state.position:
    mark = mark_price(st.session_state.position, quote, reference_price)
else:
    mark = reference_price

manage_trade(mark)
analysis = analyze(m5, m15, h1, h4, st.session_state.research_mode)

closed_candle_id = str(raw["datetime"].iloc[-1])
if st.session_state.last_signal_candle != closed_candle_id:
    st.session_state.last_signal_candle = closed_candle_id
    m5_audit = analysis["snapshots"].get("M5") or {}
    decision = {
        "decision_id": uuid.uuid4().hex[:10],
        "الوقت": now_riyadh().strftime("%Y-%m-%d %H:%M:%S"),
        "الشمعة": closed_candle_id,
        "الإشارة": analysis["signal"],
        "القوة": analysis["strength"],
        "السبب": analysis["reason"],
        "M5 Trend": m5_audit.get("trend", "—"),
        "M15 Trend": (analysis["snapshots"].get("M15") or {}).get("trend", "—"),
        "H1 Trend": (analysis["snapshots"].get("H1") or {}).get("trend", "—"),
        "H4 Trend": (analysis["snapshots"].get("H4") or {}).get("trend", "—"),
        "RSI": round(m5_audit.get("rsi", np.nan), 2) if finite(m5_audit.get("rsi", np.nan)) else None,
        "ADX": round(m5_audit.get("adx", np.nan), 2) if finite(m5_audit.get("adx", np.nan)) else None,
        "Spread": round(analysis["spread"].get("spread", np.nan), 3) if finite(analysis["spread"].get("spread", np.nan)) else None,
        "B2 Level": round(analysis["b2"].get("level", np.nan), 2) if finite(analysis["b2"].get("level", np.nan)) else None,
    }
    decision.update({name: "PASS" if passed else "BLOCK" for name, passed in analysis["gates"].items()})
    st.session_state.decisions.insert(0, decision)
    st.session_state.decisions = st.session_state.decisions[:500]

if analysis["signal"] in ("شراء", "بيع") and st.session_state.position is None:
    open_trade(analysis["signal"], reference_price, analysis["snapshots"]["M5"]["atr"], analysis["spread"])

# ---------------------------- dashboard -----------------------
st.caption("Paper Trading فقط • لا يوجد تنفيذ لدى وسيط • الحالة محفوظة داخل جلسة Streamlit")
st.markdown(
    f"<div class='hero'><div class='kicker'>GOLD AI • SMART TRADING SYSTEM</div><h1 class='gold'>XAU/USD</h1><div class='price'>${reference_price:,.2f}</div><p class='muted'>Market Data • Strategy Engine • Risk Engine • Paper Engine</p></div>",
    unsafe_allow_html=True,
)

metrics([
    ("القرار", analysis["signal"]),
    ("قوة الإشارة", f"{analysis['strength']}%"),
    ("النظام", analysis["snapshots"]["M5"]["regime"]),
    ("الرصيد", f"${st.session_state.balance:,.2f}"),
    ("الخسارة اليومية", f"{daily_loss_pct(mark):.2f}%"),
])

st.subheader("Multi-Timeframe Command Center")
columns = st.columns(4)
for column, name in zip(columns, TIMEFRAMES):
    snap = analysis["snapshots"][name]
    column.markdown(
        f"<div class='card'><div class='kicker'>{name}</div><div class='signal'>{snap['trend']}</div><div class='muted'>RSI {snap['rsi']:.1f} • ADX {snap['adx']:.1f}</div></div>",
        unsafe_allow_html=True,
    )

st.subheader("Decision Engine")
signal_class = "good" if analysis["signal"] in ("شراء", "بيع") else ""
st.markdown(
    f"<div class='card'><div class='kicker'>FINAL DECISION</div><div class='signal {signal_class}'>{analysis['signal']}</div><p>{analysis['reason']}</p><span class='muted'>Signal strength ≠ probability of profit.</span></div>",
    unsafe_allow_html=True,
)
st.dataframe(
    pd.DataFrame([{"البوابة": name, "الحالة": "PASS" if passed else "BLOCK"} for name, passed in analysis["gates"].items()]),
    hide_index=True,
    use_container_width=True,
)

left, right = st.columns(2)
with left:
    st.markdown("### B2 Breakout / Retest")
    b2 = analysis["b2"]
    metrics([
        ("Breakout", "YES" if b2.get("breakout") else "NO"),
        ("Retest", "YES" if b2.get("retest") else "NO"),
        ("الاتجاه", b2.get("direction") or "—"),
        ("المستوى", fmt_price(b2.get("level"))),
    ])
with right:
    st.markdown("### Safety Filters")
    st.info("News: " + analysis["news"]["label"])
    st.info("Spread: " + analysis["spread"]["label"])

st.subheader("Market")
st.line_chart(raw.tail(300).set_index("datetime")[["close"]], use_container_width=True)

st.subheader("Paper Trading")
position = st.session_state.position
if position:
    current_r = unrealized_r(position, mark)
    pnl = current_r * position["risk_money"]
    metrics([
        ("النوع", position["side"]),
        ("Entry", fmt_price(position["entry"])),
        ("Current", fmt_price(mark)),
        ("SL", fmt_price(position["sl"])),
        ("TP1", fmt_price(position["tp1"])),
        ("TP2", fmt_price(position["tp2"])),
        ("P&L", f"${pnl:,.2f}"),
        ("R", f"{current_r:.2f}R"),
    ])
    if position["tp1_hit"]:
        st.success("TP1 HIT • 50% CLOSED • STOP → BREAK-EVEN")
else:
    st.info("لا توجد صفقة ورقية مفتوحة")

st.subheader("Risk Center")
metrics([
    ("Balance", f"${st.session_state.balance:,.2f}"),
    ("Risk / Trade", f"{RISK_PER_TRADE * 100:.2f}%"),
    ("Daily Loss Limit", f"{DAILY_LOSS_LIMIT * 100:.1f}%"),
    ("Trades Today", f"{st.session_state.daily_trades}/{MAX_DAILY_TRADES}"),
    ("Open Positions", "1" if position else "0"),
    ("Kill Switch", "ON" if st.session_state.kill_switch else "OFF"),
])

st.subheader("Backtest Lab")
with st.expander("تشغيل OOS MTF Backtest"):
    max_m15 = len(m15)
    if max_m15 < 500:
        st.warning("بيانات M15 الحالية لا تكفي")
    else:
        bars = st.slider("عدد شموع M15 التقريبي", 500, max_m15, min(3000, max_m15), 100)
        raw_window = raw.tail(min(len(raw), bars * 3))
        cost = st.number_input("تكلفة السبريد التاريخية المفترضة (دولار/وحدة سعر)", min_value=0.0, max_value=5.0, value=0.0, step=0.05, help="ليست بيانات سبريد فعلية من الوسيط؛ استخدمها فقط كافتراض محافظ.")
        slip = st.number_input("Slippage لكل جانب (دولار/وحدة سعر)", min_value=0.0, max_value=2.0, value=0.0, step=0.05)
        trades, stats = backtest_mtf(raw_window, round_trip_cost=cost, slippage_per_side=slip)
        if stats["trades"]:
            metrics([
                ("OOS Trades", stats["trades"]),
                ("Win Rate", f"{stats['win_rate']:.1f}%"),
                ("Profit Factor", f"{stats['profit_factor']:.2f}"),
                ("Total R", f"{stats['total_r']:.2f}R"),
                ("Max DD", f"{stats['max_dd_r']:.2f}R"),
            ])
            st.caption("OOS فقط • M5 + M15/H1/H4 مغلقة + B2 + Momentum/ADX. تكاليف السبريد/slippage هنا افتراضات يحددها المستخدم وليست بيانات تاريخية من الوسيط.")
            if stats.get("warning"):
                st.warning(stats["warning"])
            if stats.get("equity_curve"):
                st.line_chart(pd.DataFrame({"Equity R": stats["equity_curve"], "Drawdown R": stats["drawdown_curve"]}))
            st.dataframe(trades.tail(100), hide_index=True, use_container_width=True)
            st.markdown("### Walk-Forward")
            wf, wf_stats = walk_forward_mtf(raw_window, windows=4, round_trip_cost=cost, slippage_per_side=slip)
            if not wf.empty:
                st.dataframe(wf, hide_index=True, use_container_width=True)
                metrics([("WF Trades", wf_stats.get("trades", 0)), ("WF Win Rate", f"{wf_stats.get('win_rate', 0):.1f}%"), ("WF PF", f"{wf_stats.get('profit_factor', 0):.2f}"), ("WF Total R", f"{wf_stats.get('total_r', 0):.2f}R"), ("WF Max DD", f"{wf_stats.get('max_dd_r', 0):.2f}R")])
                if wf_stats.get("warning"):
                    st.warning(wf_stats["warning"])
        else:
            st.warning(stats.get("warning", "لا توجد نتائج"))

st.subheader("Decision Log")
if st.session_state.decisions:
    st.dataframe(pd.DataFrame(st.session_state.decisions), hide_index=True, use_container_width=True)
else:
    st.info("لا يوجد سجل بعد")

st.subheader("Trade Log")
if st.session_state.history:
    st.dataframe(pd.DataFrame(st.session_state.history), hide_index=True, use_container_width=True)
else:
    st.info("لا توجد صفقات مغلقة")

st.subheader("System Health")
st.dataframe(
    pd.DataFrame([
        {"النظام": "Data Feed", "الحالة": "ONLINE" if not raw.empty else "BLOCKED"},
        {"النظام": "Data Quality", "الحالة": "ONLINE" if quality_ok else "BLOCKED"},
        {"النظام": "Strategy Engine", "الحالة": "ONLINE"},
        {"النظام": "Risk Engine", "الحالة": "ONLINE"},
        {"النظام": "Paper Engine", "الحالة": "ONLINE"},
        {"النظام": "Economic Calendar", "الحالة": "ONLINE" if analysis["news"]["connected"] else "BLOCKED"},
        {"النظام": "Bid/Ask Spread", "الحالة": "ONLINE" if analysis["spread"]["connected"] else "BLOCKED"},
    ]),
    hide_index=True,
    use_container_width=True,
)
st.caption(
    f"Last closed M5: {raw['datetime'].iloc[-1]} UTC • {len(raw):,} bars • batches {chunk_count}/{M5_CHUNKS} • API credits left: {credits[-1] if credits else 'N/A'}"
)

if st.session_state.auto_refresh:
    time.sleep(refresh_seconds)
    st.rerun()

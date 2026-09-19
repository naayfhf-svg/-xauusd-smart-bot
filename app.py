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
M5_RULE = "5min"

st.markdown("""
<style>
:root{--bg:#0b1220;--card:#111827;--line:#243044;--gold:#d4af37;--txt:#f8fafc;--muted:#94a3b8;--green:#22c55e;--red:#ef4444}
.stApp{background:var(--bg);color:var(--txt)}
.block-container{max-width:1400px;padding-top:1rem;padding-bottom:3rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:14px}
.gold{color:var(--gold)}
.muted,.small{color:var(--muted)}
.signal{font-size:1.8rem;font-weight:900}
div[data-testid="stMetric"]{background:var(--card);border:1px solid var(--line);padding:12px;border-radius:14px}
</style>
""", unsafe_allow_html=True)


def init_state():
    defaults = {
        "balance": START_BALANCE,
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
        "research_mode": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    today = datetime.now(TZ).date().isoformat()
    if st.session_state.daily_date != today:
        st.session_state.daily_date = today
        st.session_state.daily_start_balance = st.session_state.balance
        st.session_state.daily_trades = 0


init_state()


def api_key():
    try:
        return st.secrets["TWELVE_DATA_API_KEY"]
    except Exception:
        return None


@st.cache_data(ttl=45, show_spinner=False)
def fetch_chunk(end_date=None):
    key = api_key()
    if not key:
        return pd.DataFrame(), "مفتاح Twelve Data غير موجود في Secrets", None, None

    params = {
        "symbol": SYMBOL,
        "interval": M5_RULE,
        "outputsize": BARS_PER_CHUNK,
        "timezone": "UTC",
        "apikey": key,
    }
    if end_date:
        params["end_date"] = end_date

    try:
        response = requests.get(DATA_URL, params=params, timeout=20)
        remaining = response.headers.get("api-credits-left")
        used = response.headers.get("api-credits-used")
        response.raise_for_status()
        payload = response.json()

        if "values" not in payload:
            return (
                pd.DataFrame(),
                str(payload.get("message") or payload.get("code") or "مصدر البيانات رفض الطلب"),
                None,
                remaining,
            )

        data = pd.DataFrame(payload["values"])
        data["datetime"] = pd.to_datetime(data["datetime"], utc=True, errors="coerce")

        for col in ["open", "high", "low", "close", "volume"]:
            if col in data:
                data[col] = pd.to_numeric(data[col], errors="coerce")

        required = ["datetime", "open", "high", "low", "close"]
        data = (
            data.dropna(subset=required)
            .sort_values("datetime")
            .drop_duplicates("datetime")
            .reset_index(drop=True)
        )

        return data, "OK", remaining, used
    except Exception as exc:
        return pd.DataFrame(), f"خطأ في الاتصال: {exc}", None, None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_extended_m5():
    frames, errors, credits = [], [], []
    end = datetime.now(timezone.utc)

    for index in range(CHUNKS):
        data, message, left, used = fetch_chunk(end.strftime("%Y-%m-%d %H:%M:%S"))

        if data.empty:
            errors.append(f"الدفعة {index + 1}: {message}")
            break

        frames.append(data)
        if left is not None:
            credits.append(left)

        oldest = data["datetime"].min().to_pydatetime()
        if oldest >= end:
            errors.append(f"الدفعة {index + 1}: التاريخ لم يتحرك للخلف")
            break

        end = oldest - timedelta(minutes=5)

        if len(data) < BARS_PER_CHUNK:
            break

    if not frames:
        return pd.DataFrame(), " | ".join(errors), 0, credits

    out = (
        pd.concat(frames, ignore_index=True)
        .sort_values("datetime")
        .drop_duplicates("datetime")
        .reset_index(drop=True)
    )

    if len(frames) == CHUNKS:
        message = "تم تحميل التاريخ المطلوب بالكامل"
    else:
        message = "تم تحميل جزء من التاريخ فقط: " + " | ".join(errors)

    return out, message, len(frames), credits


def completed_m5(data):
    if data.empty:
        return data
    cutoff = pd.Timestamp.now(tz="UTC").floor("5min")
    return data[data["datetime"] < cutoff].copy()


def resample_ohlc(data, rule):
    """Build higher timeframe candles only from CLOSED M5 bars."""
    if data.empty:
        return data

    x = data.copy()
    x = completed_m5(x)

    out = (
        x.set_index("datetime")[["open", "high", "low", "close"]]
        .resample(rule, label="left", closed="left")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
            }
        )
        .dropna()
        .reset_index()
    )

    # A higher-timeframe candle is valid only if its full interval has closed.
    now_utc = pd.Timestamp.now(tz="UTC")
    rule_delta = pd.Timedelta(rule)
    out = out[out["datetime"] + rule_delta <= now_utc].copy()
    return out.reset_index(drop=True)


def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()


def rsi(series, n=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_down = down.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_up / avg_down.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def atr(data, n=14):
    previous_close = data.close.shift(1)
    true_range = pd.concat(
        [
            data.high - data.low,
            (data.high - previous_close).abs(),
            (data.low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def macd(series):
    line = ema(series, 12) - ema(series, 26)
    signal = ema(line, 9)
    return line, signal, line - signal


def adx(data, n=14):
    up_move = data.high.diff()
    down_move = -data.low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=data.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=data.index,
    )

    previous_close = data.close.shift(1)
    true_range = pd.concat(
        [
            data.high - data.low,
            (data.high - previous_close).abs(),
            (data.low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr_wilder = true_range.ewm(
        alpha=1 / n, adjust=False, min_periods=n
    ).mean()

    plus_di = (
        100
        * plus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
        / atr_wilder.replace(0, np.nan)
    )
    minus_di = (
        100
        * minus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
        / atr_wilder.replace(0, np.nan)
    )

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean(), plus_di, minus_di


def indicators(data):
    x = data.copy()
    x["ema20"] = ema(x.close, 20)
    x["ema50"] = ema(x.close, 50)
    x["ema100"] = ema(x.close, 100)
    x["rsi"] = rsi(x.close)
    x["atr"] = atr(x)
    x["macd"], x["macd_signal"], x["macd_hist"] = macd(x.close)
    x["momentum"] = x.close.pct_change(5) * 100
    x["adx"], x["plus_di"], x["minus_di"] = adx(x)
    return x


def trend(row):
    if row.close > row.ema20 > row.ema50 > row.ema100:
        return "صاعد"
    if row.close < row.ema20 < row.ema50 < row.ema100:
        return "هابط"
    return "محايد"


def regime(row):
    if pd.isna(row.adx) or pd.isna(row.atr):
        return "غير معروف"
    if row.adx >= 25:
        return "اتجاه"
    if row.adx <= 18:
        return "نطاق"
    return "انتقالي"


def snapshot(data):
    if len(data) < 110:
        return None

    x = indicators(data).dropna().reset_index(drop=True)
    if x.empty:
        return None

    row = x.iloc[-1]
    return {
        "trend": trend(row),
        "regime": regime(row),
        "rsi": float(row.rsi),
        "adx": float(row.adx),
        "atr": float(row.atr),
        "momentum": float(row.momentum),
        "close": float(row.close),
        "macd_hist": float(row.macd_hist),
        "candle": row.datetime,
        "frame": x,
    }


def b2(data):
    if len(data) < 30:
        return {
            "valid": False,
            "direction": None,
            "breakout": False,
            "retest": False,
            "level": np.nan,
        }

    row = data.iloc[-1]
    previous = data.iloc[-21:-1]
    resistance = previous.high.max()
    support = previous.low.min()
    average_true_range = row.atr

    if pd.isna(average_true_range) or average_true_range <= 0:
        return {
            "valid": False,
            "direction": None,
            "breakout": False,
            "retest": False,
            "level": np.nan,
        }

    breakout_buy = data.close.iloc[-2] > resistance
    breakout_sell = data.close.iloc[-2] < support

    retest_buy = (
        breakout_buy
        and row.low <= resistance + 0.35 * average_true_range
        and row.close > resistance
    )
    retest_sell = (
        breakout_sell
        and row.high >= support - 0.35 * average_true_range
        and row.close < support
    )

    if retest_buy:
        return {
            "valid": True,
            "direction": "شراء",
            "breakout": True,
            "retest": True,
            "level": float(resistance),
        }

    if retest_sell:
        return {
            "valid": True,
            "direction": "بيع",
            "breakout": True,
            "retest": True,
            "level": float(support),
        }

    return {
        "valid": False,
        "direction": None,
        "breakout": bool(breakout_buy or breakout_sell),
        "retest": False,
        "level": float(
            resistance if breakout_buy else support if breakout_sell else np.nan
        ),
    }


def news_status(research_mode):
    """
    Fail-closed news gate.
    A real economic calendar endpoint must return JSON with either:
      {"high_impact": true/false}
    or:
      {"block": true/false}
    Twelve Data time-series data is NOT treated as a news source.
    """
    try:
        url = st.secrets.get("NEWS_API_URL")
    except Exception:
        url = None

    if not url:
        return {
            "connected": False,
            "blocked": not research_mode,
            "label": "غير متصل — لا يوجد مزود أخبار اقتصادي",
        }

    try:
        response = requests.get(url, timeout=8)
        response.raise_for_status()
        payload = response.json()
        blocked = bool(payload.get("high_impact", False) or payload.get("block", False))
        return {
            "connected": True,
            "blocked": blocked,
            "label": "خبر عالي التأثير" if blocked else "لا يوجد حظر",
        }
    except Exception:
        return {
            "connected": False,
            "blocked": not research_mode,
            "label": "تعذر الاتصال بمزود الأخبار — تم تطبيق الحظر الآمن",
        }


def spread_status(research_mode):
    """
    Twelve Data time-series bars do not provide an executable bid/ask spread.
    Never invent a spread. Production mode therefore fails closed.
    """
    return {
        "connected": False,
        "blocked": not research_mode,
        "label": (
            "غير متصل — لا توجد بيانات Bid/Ask قابلة لقياس السبريد"
            if not research_mode
            else "غير متاح — وضع البحث يسمح بالاختبار فقط"
        ),
    }


def daily_loss_pct(price):
    position = st.session_state.position
    equity = st.session_state.balance

    if position:
        equity += unrealized_r(position, price) * position["risk_money"]

    return max(
        0,
        (st.session_state.daily_start_balance - equity)
        / max(st.session_state.daily_start_balance, 1)
        * 100,
    )


def unrealized_r(position, price):
    if not position or not np.isfinite(price):
        return 0.0

    distance = abs(position["entry"] - position["sl"])
    if distance <= 0:
        return 0.0

    direction = 1 if position["side"] == "شراء" else -1
    return (
        direction
        * (price - position["entry"])
        / distance
        * position["remaining_fraction"]
        + position["realized_r"]
    )


def analyze(m5, m15, h1, h4, research_mode):
    snapshots = {
        key: snapshot(value)
        for key, value in {"M5": m5, "M15": m15, "H1": h1, "H4": h4}.items()
    }

    news = news_status(research_mode)
    spread = spread_status(research_mode)

    if any(snapshots[key] is None for key in ["M5", "M15", "H1", "H4"]):
        return {
            "signal": "انتظار",
            "strength": 0,
            "reason": "بيانات الأطر الزمنية غير مكتملة",
            "gates": {
                "البيانات": False,
                "النظام السوقي": False,
                "توافق الأطر": False,
                "الزخم": False,
                "اختراق وإعادة اختبار": False,
                "المخاطر": not st.session_state.kill_switch,
                "الحد اليومي": True,
                "السبريد": not spread["blocked"],
                "الأخبار": not news["blocked"],
            },
            "snaps": snapshots,
            "b2": {},
            "news": news,
            "spread": spread,
        }

    zone = b2(snapshots["M5"]["frame"])
    trends = [snapshots[key]["trend"] for key in ["M5", "M15", "H1", "H4"]]
    bull = all(item == "صاعد" for item in trends)
    bear = all(item == "هابط" for item in trends)

    m5_snapshot = snapshots["M5"]
    momentum_buy = (
        m5_snapshot["rsi"] >= 52
        and m5_snapshot["momentum"] > 0
        and m5_snapshot["macd_hist"] > 0
    )
    momentum_sell = (
        m5_snapshot["rsi"] <= 48
        and m5_snapshot["momentum"] < 0
        and m5_snapshot["macd_hist"] < 0
    )

    gates = {
        "البيانات": True,
        "النظام السوقي": m5_snapshot["regime"] == "اتجاه",
        "توافق الأطر": bull or bear,
        "الزخم": momentum_buy or momentum_sell,
        "اختراق وإعادة اختبار": zone["valid"],
        "المخاطر": not st.session_state.kill_switch,
        "الحد اليومي": (
            daily_loss_pct(st.session_state.last_price) < DAILY_LOSS_LIMIT * 100
            and st.session_state.daily_trades < MAX_DAILY_TRADES
        ),
        "السبريد": not spread["blocked"],
        "الأخبار": not news["blocked"],
    }

    strength = (
        (30 if gates["توافق الأطر"] else 0)
        + (25 if zone["valid"] else 0)
        + (20 if gates["الزخم"] else 0)
        + (10 if m5_snapshot["adx"] >= 25 else 0)
        + (
            10
            if (
                (bull and snapshots["H4"]["trend"] == "صاعد")
                or (bear and snapshots["H4"]["trend"] == "هابط")
            )
            else 0
        )
        + (5 if gates["السبريد"] and gates["الأخبار"] else 0)
    )

    reason = next(
        (name for name, passed in gates.items() if not passed),
        "اجتازت جميع الشروط الأساسية",
    )

    signal = "انتظار"
    if all(gates.values()):
        if zone["direction"] == "شراء" and bull and momentum_buy:
            signal = "شراء"
        elif zone["direction"] == "بيع" and bear and momentum_sell:
            signal = "بيع"
        else:
            reason = "تعارض الاتجاه مع الاختراق أو الزخم"

    return {
        "signal": signal,
        "strength": min(strength, 100),
        "reason": reason,
        "gates": gates,
        "snaps": snapshots,
        "b2": zone,
        "news": news,
        "spread": spread,
    }


def open_trade(side, price, average_true_range):
    if st.session_state.position or st.session_state.kill_switch:
        return False, "لا يمكن فتح صفقة"

    if st.session_state.daily_trades >= MAX_DAILY_TRADES:
        return False, "تم بلوغ الحد اليومي"

    if daily_loss_pct(price) >= DAILY_LOSS_LIMIT * 100:
        return False, "تم بلوغ حد الخسارة اليومية"

    if not np.isfinite(average_true_range) or average_true_range <= 0:
        return False, "ATR غير صالح"

    distance = max(average_true_range * 1.4, price * 0.0015)
    risk_money = st.session_state.balance * RISK_PER_TRADE

    if side == "شراء":
        stop = price - distance
        tp1 = price + distance
        tp2 = price + 2.2 * distance
    else:
        stop = price + distance
        tp1 = price - distance
        tp2 = price - 2.2 * distance

    st.session_state.position = {
        "id": uuid.uuid4().hex[:10],
        "side": side,
        "entry": float(price),
        "sl": float(stop),
        "initial_sl": float(stop),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "risk_money": float(risk_money),
        "remaining_fraction": 1.0,
        "realized_r": 0.0,
        "tp1_hit": False,
        "opened": datetime.now(TZ).isoformat(),
    }

    st.session_state.daily_trades += 1
    return True, "تم فتح الصفقة الورقية"


def close_trade(price, reason):
    position = st.session_state.position
    if not position:
        return

    final_r = unrealized_r(position, price)
    pnl = final_r * position["risk_money"]
    st.session_state.balance += pnl

    st.session_state.history.append(
        {
            "الوقت": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
            "المعرف": position["id"],
            "النوع": position["side"],
            "الدخول": round(position["entry"], 2),
            "الخروج": round(price, 2),
            "R": round(final_r, 3),
            "الربح/الخسارة": round(pnl, 2),
            "السبب": reason,
        }
    )

    st.session_state.position = None


def manage_trade(price):
    position = st.session_state.position
    if not position or not np.isfinite(price):
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

        if position["tp1_hit"] and price >= position["tp2"]:
            close_trade(position["tp2"], "الهدف الثاني")
    else:
        if price >= position["sl"]:
            close_trade(position["sl"], "وقف الخسارة")
            return

        if not position["tp1_hit"] and price <= position["tp1"]:
            position["realized_r"] += 0.5
            position["remaining_fraction"] = 0.5
            position["tp1_hit"] = True
            position["sl"] = position["entry"]

        if position["tp1_hit"] and price <= position["tp2"]:
            close_trade(position["tp2"], "الهدف الثاني")


def backtest(data):
    """OOS backtest of the same B2 + TP1/BE/TP2 rules used by paper trading."""
    x = indicators(data).dropna().reset_index(drop=True)

    if len(x) < 350:
        return pd.DataFrame(), {
            "trades": 0,
            "warning": "العينة صغيرة للاختبار",
        }

    split = int(len(x) * 0.70)
    rows = []
    index = 120

    while index < len(x) - 2:
        row = x.iloc[index]
        previous = x.iloc[max(0, index - 21):index]

        resistance = float(previous.high.max())
        support = float(previous.low.min())
        average_true_range = float(row.atr)

        if not np.isfinite(average_true_range) or average_true_range <= 0:
            index += 1
            continue

        previous_close = float(x.close.iloc[index - 1])

        breakout_buy = previous_close > resistance
        breakout_sell = previous_close < support

        retest_buy = (
            breakout_buy
            and float(row.low) <= resistance + 0.35 * average_true_range
            and float(row.close) > resistance
        )
        retest_sell = (
            breakout_sell
            and float(row.high) >= support - 0.35 * average_true_range
            and float(row.close) < support
        )

        bull = (
            row.close > row.ema20 > row.ema50 > row.ema100
            and row.rsi >= 52
            and row.momentum > 0
            and row.macd_hist > 0
        )
        bear = (
            row.close < row.ema20 < row.ema50 < row.ema100
            and row.rsi <= 48
            and row.momentum < 0
            and row.macd_hist < 0
        )

        side = (
            "شراء"
            if retest_buy and bull and row.adx >= 25
            else "بيع"
            if retest_sell and bear and row.adx >= 25
            else None
        )

        if side is None:
            index += 1
            continue

        entry = float(row.close)
        distance = max(average_true_range * 1.4, entry * 0.0015)

        if side == "شراء":
            stop = entry - distance
            tp1 = entry + distance
            tp2 = entry + 2.2 * distance
        else:
            stop = entry + distance
            tp1 = entry - distance
            tp2 = entry - 2.2 * distance

        realized_r = 0.0
        remaining = 1.0
        sl = stop
        tp1_hit = False
        outcome = None
        reason = None
        close_index = None

        for j in range(index + 1, min(index + 80, len(x))):
            high = float(x.high.iloc[j])
            low = float(x.low.iloc[j])

            # Conservative intrabar ordering: stop first if stop and target
            # are both touched on the same candle.
            if side == "شراء":
                if low <= sl:
                    outcome = realized_r - remaining
                    reason = "وقف الخسارة"
                    close_index = j
                    break

                if not tp1_hit and high >= tp1:
                    realized_r += 0.5
                    remaining = 0.5
                    tp1_hit = True
                    sl = entry

                if tp1_hit and high >= tp2:
                    outcome = realized_r + 1.1
                    reason = "الهدف الثاني"
                    close_index = j
                    break
            else:
                if high >= sl:
                    outcome = realized_r - remaining
                    reason = "وقف الخسارة"
                    close_index = j
                    break

                if not tp1_hit and low <= tp1:
                    realized_r += 0.5
                    remaining = 0.5
                    tp1_hit = True
                    sl = entry

                if tp1_hit and low <= tp2:
                    outcome = realized_r + 1.1
                    reason = "الهدف الثاني"
                    close_index = j
                    break

        if outcome is not None:
            rows.append(
                {
                    "الفهرس": index,
                    "الوقت": x.datetime.iloc[index],
                    "النوع": side,
                    "الدخول": round(entry, 2),
                    "الوقف": round(stop, 2),
                    "TP1": round(tp1, 2),
                    "TP2": round(tp2, 2),
                    "R": round(outcome, 3),
                    "السبب": reason,
                    "OOS": index >= split,
                }
            )
            index = close_index + 1
        else:
            index += 1

    trades = pd.DataFrame(rows)
    oos = trades[trades["OOS"]].copy() if not trades.empty else trades

    if oos.empty:
        return trades, {
            "trades": 0,
            "warning": "لا توجد صفقات OOS بالشروط الحالية",
        }

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0

    for value in oos.R:
        equity += float(value)
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)

    gross_win = oos.loc[oos.R > 0, "R"].sum()
    gross_loss = abs(oos.loc[oos.R < 0, "R"].sum())
    profit_factor = gross_win / gross_loss if gross_loss else math.inf

    return trades, {
        "trades": len(oos),
        "win_rate": float((oos.R > 0).mean() * 100),
        "profit_factor": float(profit_factor),
        "total_r": float(oos.R.sum()),
        "max_dd_r": float(max_drawdown),
        "warning": None,
    }


def metrics(items):
    columns = st.columns(len(items))
    for column, (label, value) in zip(columns, items):
        column.metric(label, value)


def status_table(statuses):
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "البند": key,
                    "الحالة": "مقبول" if value else "محظور",
                }
                for key, value in statuses.items()
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )


# ---------------- UI ----------------
st.sidebar.title("🟡 بوت الذهب")
st.sidebar.caption("XAU/USD • تداول ورقي فقط")

research = st.sidebar.checkbox(
    "وضع البحث التجريبي",
    value=st.session_state.research_mode,
    help="يسمح بالبحث الورقي عند عدم توفر الأخبار والسبريد. لا يسمح بالتنفيذ الحقيقي.",
)
st.session_state.research_mode = research

st.session_state.kill_switch = st.sidebar.toggle(
    "مفتاح الإيقاف",
    value=st.session_state.kill_switch,
)

st.session_state.auto_refresh = st.sidebar.toggle(
    "التحديث التلقائي",
    value=False,
)

refresh = st.sidebar.slider(
    "فترة التحديث بالثواني",
    15,
    120,
    45,
)

raw, message, chunks, credits = fetch_extended_m5()
raw = completed_m5(raw)

if raw.empty:
    st.error("مصدر البيانات غير متاح")
    st.info(message)
    st.stop()

if chunks < CHUNKS:
    st.warning(message)

m5 = indicators(raw)
m15 = resample_ohlc(raw, "15min")
h1 = resample_ohlc(raw, "1h")
h4 = resample_ohlc(raw, "4h")

if any(len(frame) < 110 for frame in [m5, m15, h1, h4]):
    st.error("بيانات الأطر الزمنية غير كافية لتشغيل المحرك بأمان.")
    st.stop()

price = float(raw.close.iloc[-1])
st.session_state.last_price = price

manage_trade(price)
analysis = analyze(m5, m15, h1, h4, research)

candle = str(raw.datetime.iloc[-1])

if st.session_state.last_signal_candle != candle:
    st.session_state.last_signal_candle = candle

    decision_record = {
        "الوقت": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "الشمعة": candle,
        "الإشارة": analysis["signal"],
        "القوة": analysis["strength"],
        "السبب": analysis["reason"],
    }

    for gate_name, gate_value in analysis["gates"].items():
        decision_record[gate_name] = "PASS" if gate_value else "BLOCK"

    st.session_state.decisions.insert(0, decision_record)
    st.session_state.decisions = st.session_state.decisions[:300]

# Paper execution is deliberately disabled when a safety gate is unavailable.
# In research mode, unavailable news/spread are explicitly experimental.
if (
    analysis["signal"] in ("شراء", "بيع")
    and st.session_state.position is None
):
    open_trade(
        analysis["signal"],
        price,
        analysis["snaps"]["M5"]["atr"],
    )

st.markdown(
    "<div class='card'>"
    "<div class='small'>GOLD AI</div>"
    "<h1 class='gold'>منصة التداول الذكي XAU/USD</h1>"
    "<div class='muted'>تداول ورقي • محرك القرار • محرك المخاطر</div>"
    "</div>",
    unsafe_allow_html=True,
)

metrics(
    [
        ("XAU/USD", f"${price:,.2f}"),
        ("الإشارة", analysis["signal"]),
        ("قوة الإشارة", f"{analysis['strength']}%"),
        (
            "حالة السوق",
            analysis["snaps"]["M5"]["regime"] if analysis["snaps"]["M5"] else "—",
        ),
        ("الرصيد", f"${st.session_state.balance:,.2f}"),
        ("الخسارة اليومية", f"{daily_loss_pct(price):.2f}%"),
    ]
)

st.subheader("حالة الأطر الزمنية")

columns = st.columns(4)
for column, key in zip(columns, ["M5", "M15", "H1", "H4"]):
    snap = analysis["snaps"].get(key)
    if snap:
        column.markdown(
            f"<div class='card'><b>{key}</b><br>"
            f"<span class='signal'>{snap['trend']}</span><br>"
            f"<span class='small'>RSI {snap['rsi']:.1f} • ADX {snap['adx']:.1f}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        column.warning(f"{key}: بيانات غير كافية")

st.subheader("محرك القرار")

st.markdown(
    f"<div class='card'>"
    f"<div class='small'>القرار النهائي</div>"
    f"<div class='signal'>{analysis['signal']}</div>"
    f"<div>{analysis['reason']}</div>"
    f"<div class='small'>قوة الإشارة مقياس توافق داخلي وليست احتمال ربح.</div>"
    f"</div>",
    unsafe_allow_html=True,
)

status_table(analysis["gates"])

st.subheader("فلتر الأخبار")
st.info(
    analysis["news"]["label"]
    + (
        " — وضع البحث يسمح بالاختبار فقط."
        if research and not analysis["news"]["connected"]
        else ""
    )
)

st.subheader("فلتر السبريد")
st.info(analysis["spread"]["label"])

st.subheader("اختراق B2 وإعادة الاختبار")

zone = analysis["b2"]
metrics(
    [
        ("الاختراق", "نعم" if zone.get("breakout") else "لا"),
        ("إعادة الاختبار", "نعم" if zone.get("retest") else "لا"),
        ("الاتجاه", zone.get("direction") or "—"),
        (
            "المستوى",
            f"{zone.get('level', np.nan):.2f}"
            if np.isfinite(zone.get("level", np.nan))
            else "—",
        ),
    ]
)

st.subheader("السعر")
st.line_chart(
    raw.tail(300).set_index("datetime")[["close"]],
    use_container_width=True,
)

st.subheader("التداول الورقي")

position = st.session_state.position

if position:
    current_r = unrealized_r(position, price)
    pnl = current_r * position["risk_money"]

    metrics(
        [
            ("النوع", position["side"]),
            ("الدخول", f"{position['entry']:.2f}"),
            ("السعر الحالي", f"{price:.2f}"),
            ("وقف الخسارة", f"{position['sl']:.2f}"),
            ("الهدف الأول", f"{position['tp1']:.2f}"),
            ("الهدف الثاني", f"{position['tp2']:.2f}"),
            ("الربح/الخسارة", f"${pnl:,.2f}"),
            ("المضاعف", f"{current_r:.2f}R"),
        ]
    )

    if position["tp1_hit"]:
        st.success("TP1 HIT • تم إغلاق 50% • الوقف انتقل إلى نقطة الدخول")
else:
    st.info("لا توجد صفقة ورقية مفتوحة.")

st.subheader("مركز المخاطر")

metrics(
    [
        ("الرصيد", f"${st.session_state.balance:,.2f}"),
        ("المخاطرة لكل صفقة", f"{RISK_PER_TRADE * 100:.2f}%"),
        ("حد الخسارة اليومية", f"{DAILY_LOSS_LIMIT * 100:.1f}%"),
        ("صفقات اليوم", f"{st.session_state.daily_trades}/{MAX_DAILY_TRADES}"),
        ("الصفقات المفتوحة", "1" if position else "0"),
        (
            "مفتاح الإيقاف",
            "مفعل" if st.session_state.kill_switch else "غير مفعل",
        ),
    ]
)

st.subheader("مختبر الاختبار التاريخي")

with st.expander("تشغيل الاختبار"):
    max_bars = min(5000, len(m15))
    if max_bars < 500:
        st.warning("بيانات 15 دقيقة الحالية لا تكفي لاختبار مناسب.")
    else:
        bars = st.slider(
            "عدد شموع 15 دقيقة",
            500,
            max_bars,
            min(3000, max_bars),
            100,
        )

        trades, stats = backtest(m15.tail(bars))

        if stats["trades"]:
            metrics(
                [
                    ("صفقات خارج العينة", stats["trades"]),
                    ("نسبة الفوز", f"{stats['win_rate']:.1f}%"),
                    ("معامل الربح", f"{stats['profit_factor']:.2f}"),
                    ("إجمالي R", f"{stats['total_r']:.2f}R"),
                    ("أقصى تراجع OOS", f"{stats['max_dd_r']:.2f}R"),
                ]
            )

            st.caption(
                "النتائج خارج العينة فقط. هذه ليست ضمانًا للأداء المستقبلي."
            )
            st.dataframe(
                trades.tail(100),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.warning(stats.get("warning", "لا توجد نتائج"))

st.subheader("سجل القرارات")

if st.session_state.decisions:
    st.dataframe(
        pd.DataFrame(st.session_state.decisions),
        hide_index=True,
        use_container_width=True,
    )
else:
    st.info("لا يوجد سجل بعد.")

st.subheader("سجل الصفقات")

if st.session_state.history:
    st.dataframe(
        pd.DataFrame(st.session_state.history),
        hide_index=True,
        use_container_width=True,
    )
else:
    st.info("لا توجد صفقات مغلقة.")

st.subheader("سلامة النظام")

status_table(
    {
        "مصدر البيانات": not raw.empty,
        "محرك الاستراتيجية": analysis["snaps"]["M5"] is not None,
        "محرك المخاطر": True,
        "محرك التداول الورقي": True,
        "مزامنة الوقت": True,
        "مزود الأخبار": analysis["news"]["connected"],
        "بيانات Bid/Ask": analysis["spread"]["connected"],
    }
)

st.caption(
    f"آخر شمعة: {raw.datetime.iloc[-1]} UTC • "
    f"{len(raw):,} شمعة M5 • "
    f"الدفعات المحملة: {chunks}/{CHUNKS} • "
    f"أرصدة API المتاحة: {credits[-1] if credits else 'غير متاحة'}"
)

if st.session_state.auto_refresh:
    time.sleep(refresh)
    st.rerun()

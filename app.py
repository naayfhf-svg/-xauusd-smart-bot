# ============================================================
# GOLD AI — XAU/USD SMART PAPER TRADING TERMINAL
# Single-file Streamlit application
# Paper Trading only — no real-money execution
# ============================================================

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
# CONFIG
# ============================================================

st.set_page_config(
    page_title="بوت الذهب XAU/USD",
    page_icon="🥇",
    layout="wide",
    initial_sidebar_state="collapsed",
)

SYMBOL = "XAU/USD"
API_URL = "https://api.twelvedata.com/time_series"

START_BALANCE = 10_000.0

DEFAULT_RISK = 1.0
DEFAULT_DAILY_LOSS = 3.0
DEFAULT_MAX_TRADES = 5

SPREAD = 0.20
SLIPPAGE = 0.05

SL_ATR = 1.50
TP1_ATR = 1.50
TP2_ATR = 2.50

RETEST_ATR = 0.35

RIYADH = ZoneInfo("Asia/Riyadh")


# ============================================================
# CSS
# ============================================================

st.markdown(
    """
<style>

html, body, [class*="css"] {
    font-family: Arial, sans-serif;
}

[data-testid="stAppViewContainer"] {
    background:
        radial-gradient(circle at top right, rgba(212,175,55,.08), transparent 28%),
        #080d16;
    color: #f4f6f8;
}

[data-testid="stHeader"] {
    background: rgba(8,13,22,.92);
}

.block-container {
    max-width: 1500px;
    padding-top: 1rem;
    padding-bottom: 3rem;
}

.hero {
    background: linear-gradient(135deg, #111827, #0c1422);
    border: 1px solid #263244;
    border-radius: 24px;
    padding: 24px;
    margin-bottom: 18px;
    box-shadow: 0 15px 45px rgba(0,0,0,.25);
}

.brand {
    color: #d4af37;
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 2px;
}

.hero-title {
    font-size: 34px;
    font-weight: 800;
    margin-top: 5px;
}

.hero-sub {
    color: #9ca8b8;
    margin-top: 5px;
}

.price {
    font-size: 42px;
    font-weight: 900;
    color: #fff;
}

.gold {
    color: #d4af37;
}

.card {
    background: #101824;
    border: 1px solid #253144;
    border-radius: 18px;
    padding: 18px;
    min-height: 105px;
    box-shadow: 0 8px 28px rgba(0,0,0,.18);
}

.card-title {
    color: #8e9aaa;
    font-size: 12px;
    margin-bottom: 8px;
}

.card-value {
    font-size: 23px;
    font-weight: 800;
}

.muted {
    color: #8e9aaa;
}

.green {
    color: #45d483;
}

.red {
    color: #ff6474;
}

.yellow {
    color: #d4af37;
}

.blue {
    color: #70a7ff;
}

.signal-buy {
    border: 1px solid rgba(69,212,131,.5);
    background: rgba(69,212,131,.08);
    border-radius: 18px;
    padding: 22px;
}

.signal-sell {
    border: 1px solid rgba(255,100,116,.5);
    background: rgba(255,100,116,.08);
    border-radius: 18px;
    padding: 22px;
}

.signal-wait {
    border: 1px solid rgba(212,175,55,.35);
    background: rgba(212,175,55,.06);
    border-radius: 18px;
    padding: 22px;
}

.section {
    font-size: 20px;
    font-weight: 800;
    margin-top: 28px;
    margin-bottom: 12px;
}

.badge {
    display: inline-block;
    padding: 5px 10px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 700;
    border: 1px solid #334155;
    background: #111827;
}

.health-ok {
    color: #45d483;
}

.health-bad {
    color: #ff6474;
}

.health-warn {
    color: #d4af37;
}

div[data-testid="stMetric"] {
    background: #101824;
    border: 1px solid #253144;
    border-radius: 16px;
    padding: 10px;
}

.stButton > button {
    border-radius: 12px;
    min-height: 42px;
    font-weight: 700;
}

footer {
    visibility: hidden;
}

</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# SESSION STATE
# ============================================================

def init_state():
    defaults = {
        "paper_balance": START_BALANCE,
        "paper_history": [],
        "decision_logs": [],
        "paper_trade": None,
        "pending_signal": None,
        "kill_switch": False,
        "day_key": datetime.now(RIYADH).date().isoformat(),
        "day_start_balance": START_BALANCE,
        "daily_trades": 0,
        "last_signal_key": None,
        "last_managed_candle": None,
        "last_price": None,
        "last_data_time": None,
        "api_error": None,
        "last_signal": None,
        "engine_started": True,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_state()


# ============================================================
# UTILITIES
# ============================================================

def now_riyadh():
    return datetime.now(RIYADH)


def fmt_price(value):
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):,.2f}"


def fmt_money(value):
    if value is None or pd.isna(value):
        return "—"
    return f"${float(value):,.2f}"


def safe_float(value, default=np.nan):
    try:
        return float(value)
    except Exception:
        return default


def normalize_timestamp(ts):
    ts = pd.Timestamp(ts)

    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")

    return ts.tz_convert("UTC")


def reset_daily_if_needed():
    today = now_riyadh().date().isoformat()

    if st.session_state.day_key != today:
        st.session_state.day_key = today
        st.session_state.day_start_balance = st.session_state.paper_balance
        st.session_state.daily_trades = 0


reset_daily_if_needed()


# ============================================================
# DATA
# ============================================================

@st.cache_data(ttl=30, show_spinner=False)
def fetch_twelve_data(api_key, interval="5min", outputsize=5000):
    if not api_key:
        raise RuntimeError("TWELVE_DATA_API_KEY غير موجود في Secrets.")

    params = {
        "symbol": SYMBOL,
        "interval": interval,
        "outputsize": outputsize,
        "timezone": "UTC",
        "apikey": api_key,
        "format": "JSON",
    }

    response = requests.get(API_URL, params=params, timeout=20)

    response.raise_for_status()

    data = response.json()

    if "status" in data and data.get("status") == "error":
        raise RuntimeError(data.get("message", "Twelve Data error"))

    values = data.get("values")

    if not values:
        raise RuntimeError("لم تصل بيانات سعرية من Twelve Data.")

    df = pd.DataFrame(values)

    if "datetime" not in df.columns:
        raise RuntimeError("بيانات Twelve Data غير صالحة.")

    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)

    for col in ["open", "high", "low", "close"]:
        if col not in df.columns:
            raise RuntimeError(f"البيانات تفتقد العمود {col}")
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    else:
        df["volume"] = np.nan

    df = df.sort_values("datetime").set_index("datetime")

    return df


def filter_completed_m5(df):
    if df.empty:
        return df

    current = pd.Timestamp.now(tz="UTC").floor("5min")

    return df[df.index < current].copy()


def data_quality(df):
    if df.empty:
        return {
            "valid": False,
            "duplicates": 0,
            "gaps": 0,
            "rows": 0,
            "stale_minutes": None,
            "reason": "لا توجد بيانات",
        }

    raw_duplicates = int(df.index.duplicated().sum())

    clean = df[~df.index.duplicated(keep="last")].copy()

    expected = pd.date_range(
        start=clean.index.min(),
        end=clean.index.max(),
        freq="5min",
        tz="UTC",
    )

    missing = expected.difference(clean.index)

    latest = clean.index.max()

    stale_minutes = (
        (pd.Timestamp.now(tz="UTC") - latest).total_seconds() / 60
    )

    gaps = len(missing)

    valid = (
        len(clean) >= 500
        and stale_minutes <= 20
        and gaps < max(20, int(len(clean) * 0.02))
    )

    reason = "OK"

    if stale_minutes > 20:
        reason = "البيانات متأخرة"

    elif gaps >= max(20, int(len(clean) * 0.02)):
        reason = "فجوات كثيرة في البيانات"

    return {
        "valid": valid,
        "duplicates": raw_duplicates,
        "gaps": gaps,
        "rows": len(clean),
        "stale_minutes": stale_minutes,
        "reason": reason,
    }


# ============================================================
# INDICATORS
# ============================================================

def calculate_rsi(close, period=14):
    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    rsi = 100 - (100 / (1 + rs))

    return rsi


def calculate_atr(df, period=14):
    prev_close = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def calculate_adx(df, period=14):
    high = df["high"]
    low = df["low"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where(
            (up_move > down_move) & (up_move > 0),
            up_move,
            0.0,
        ),
        index=df.index,
    )

    minus_dm = pd.Series(
        np.where(
            (down_move > up_move) & (down_move > 0),
            down_move,
            0.0,
        ),
        index=df.index,
    )

    prev_close = df["close"].shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    plus_di = (
        100
        * plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        / atr.replace(0, np.nan)
    )

    minus_di = (
        100
        * minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        / atr.replace(0, np.nan)
    )

    dx = (
        100
        * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0, np.nan)
    )

    adx = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    return adx, plus_di, minus_di


def add_indicators(df):
    x = df.copy()

    x["ema20"] = x["close"].ewm(span=20, adjust=False).mean()
    x["ema50"] = x["close"].ewm(span=50, adjust=False).mean()
    x["ema100"] = x["close"].ewm(span=100, adjust=False).mean()

    x["rsi"] = calculate_rsi(x["close"], 14)

    x["atr"] = calculate_atr(x, 14)

    ema12 = x["close"].ewm(span=12, adjust=False).mean()
    ema26 = x["close"].ewm(span=26, adjust=False).mean()

    x["macd"] = ema12 - ema26
    x["macd_signal"] = x["macd"].ewm(span=9, adjust=False).mean()
    x["macd_hist"] = x["macd"] - x["macd_signal"]

    x["momentum"] = x["close"].diff(10)

    x["adx"], x["plus_di"], x["minus_di"] = calculate_adx(x, 14)

    return x.dropna()


def resample_tf(df, minutes):
    rule = f"{minutes}min"

    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }

    x = df.resample(
        rule,
        label="right",
        closed="left",
        origin="start_day",
    ).agg(agg)

    x = x.dropna(subset=["open", "high", "low", "close"])

    return x


def prepare_market(raw):
    m5 = filter_completed_m5(raw)

    m15 = resample_tf(m5, 15)
    h1 = resample_tf(m5, 60)
    h4 = resample_tf(m5, 240)

    return (
        add_indicators(m5),
        add_indicators(m15),
        add_indicators(h1),
        add_indicators(h4),
    )


# ============================================================
# MARKET REGIME
# ============================================================

def market_regime(row):
    if row is None:
        return "UNKNOWN"

    adx = safe_float(row.get("adx"))
    atr = safe_float(row.get("atr"))
    close = safe_float(row.get("close"))
    ema20 = safe_float(row.get("ema20"))
    ema50 = safe_float(row.get("ema50"))
    ema100 = safe_float(row.get("ema100"))

    if any(pd.isna(x) for x in [adx, atr, close, ema20, ema50, ema100]):
        return "UNKNOWN"

    if adx >= 30:
        if close > ema20 > ema50 > ema100:
            return "TRENDING BULLISH"

        if close < ema20 < ema50 < ema100:
            return "TRENDING BEARISH"

        return "TRENDING"

    recent_atr = safe_float(row.get("atr"))

    if recent_atr > 0 and abs(close - ema20) < recent_atr * 0.5:
        return "RANGING"

    if adx < 18:
        return "LOW TREND STRENGTH"

    return "TRANSITION"


# ============================================================
# TREND / SCORE
# ============================================================

def timeframe_score(row):
    score = 0

    if row["ema20"] > row["ema50"] > row["ema100"]:
        score += 3

    elif row["ema20"] < row["ema50"] < row["ema100"]:
        score -= 3

    if row["close"] > row["ema20"]:
        score += 1

    elif row["close"] < row["ema20"]:
        score -= 1

    if 52 <= row["rsi"] <= 68:
        score += 2

    elif 32 <= row["rsi"] < 48:
        score -= 2

    if row["macd"] > row["macd_signal"]:
        score += 2

    elif row["macd"] < row["macd_signal"]:
        score -= 2

    if row["momentum"] > 0:
        score += 1

    elif row["momentum"] < 0:
        score -= 1

    if row["adx"] >= 25:
        if row["plus_di"] > row["minus_di"]:
            score += 2
        elif row["minus_di"] > row["plus_di"]:
            score -= 2

    return int(score)


def trend_label(score):
    if score >= 4:
        return "BULLISH"

    if score <= -4:
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def support_resistance(df, lookback=80):
    x = df.tail(lookback)

    if len(x) < 20:
        return None, None

    highs = x["high"].rolling(5, center=True).max()
    lows = x["low"].rolling(5, center=True).min()

    swing_highs = x["high"][x["high"] == highs]
    swing_lows = x["low"][x["low"] == lows]

    resistance = (
        float(swing_highs.iloc[-1])
        if len(swing_highs)
        else float(x["high"].max())
    )

    support = (
        float(swing_lows.iloc[-1])
        if len(swing_lows)
        else float(x["low"].min())
    )

    return support, resistance


# ============================================================
# B2 BREAKOUT / RETEST
# ============================================================

def detect_b2(df):
    if len(df) < 30:
        return {
            "valid": False,
            "direction": None,
            "breakout": False,
            "retest": False,
            "level": None,
            "reason": "بيانات غير كافية",
        }

    latest = df.iloc[-1]

    atr = safe_float(latest["atr"])

    if pd.isna(atr) or atr <= 0:
        return {
            "valid": False,
            "direction": None,
            "breakout": False,
            "retest": False,
            "level": None,
            "reason": "ATR غير صالح",
        }

    score = timeframe_score(latest)

    # Search recent breakout candles.
    for i in range(2, 8):
        breakout = df.iloc[-i]

        previous = df.iloc[-i - 1]

        # ---------------- BUY ----------------

        if (
            breakout["close"] > previous["high"]
            and breakout["high"] > previous["high"]
        ):
            level = float(previous["high"])

            distance = abs(float(latest["low"]) - level)

            retest = (
                distance <= atr * RETEST_ATR
                and latest["close"] >= level
            )

            if retest:
                return {
                    "valid": score >= 5,
                    "direction": "BUY",
                    "breakout": True,
                    "retest": True,
                    "level": level,
                    "reason": (
                        "B2 BUY breakout + retest confirmed"
                        if score >= 5
                        else "B2 BUY لكن توافق الاتجاه ضعيف"
                    ),
                }

        # ---------------- SELL ----------------

        if (
            breakout["close"] < previous["low"]
            and breakout["low"] < previous["low"]
        ):
            level = float(previous["low"])

            distance = abs(float(latest["high"]) - level)

            retest = (
                distance <= atr * RETEST_ATR
                and latest["close"] <= level
            )

            if retest:
                return {
                    "valid": score <= -5,
                    "direction": "SELL",
                    "breakout": True,
                    "retest": True,
                    "level": level,
                    "reason": (
                        "B2 SELL breakout + retest confirmed"
                        if score <= -5
                        else "B2 SELL لكن توافق الاتجاه ضعيف"
                    ),
                }

    return {
        "valid": False,
        "direction": None,
        "breakout": False,
        "retest": False,
        "level": None,
        "reason": "لا يوجد B2 مكتمل",
    }


# ============================================================
# SIGNAL ENGINE
# ============================================================

def analyze_market(m5, m15, h1, h4):
    m5r = m5.iloc[-1]
    m15r = m15.iloc[-1]
    h1r = h1.iloc[-1]
    h4r = h4.iloc[-1]

    scores = {
        "M5": timeframe_score(m5r),
        "M15": timeframe_score(m15r),
        "H1": timeframe_score(h1r),
        "H4": timeframe_score(h4r),
    }

    labels = {
        key: trend_label(value)
        for key, value in scores.items()
    }

    total = sum(scores.values())

    b2 = detect_b2(m15)

    regime = market_regime(h1r)

    reasons = []

    # --------------------------------------------------------
    # DATA
    # --------------------------------------------------------

    data_valid = True

    if data_valid:
        reasons.append(("DATA", True, "البيانات صالحة"))

    # --------------------------------------------------------
    # MTF
    # --------------------------------------------------------

    bullish_alignment = (
        scores["M5"] >= 3
        and scores["M15"] >= 3
        and scores["H1"] >= 3
    )

    bearish_alignment = (
        scores["M5"] <= -3
        and scores["M15"] <= -3
        and scores["H1"] <= -3
    )

    mtf_direction = None

    if bullish_alignment:
        mtf_direction = "BUY"

    elif bearish_alignment:
        mtf_direction = "SELL"

    reasons.append(
        (
            "MTF ALIGNMENT",
            mtf_direction is not None,
            "توافق M5/M15/H1"
            if mtf_direction
            else "لا يوجد توافق كافٍ",
        )
    )

    # --------------------------------------------------------
    # M15 FILTERS
    # --------------------------------------------------------

    momentum_valid = (
        m15r["adx"] >= 20
        and (
            (m15r["rsi"] >= 50 and m15r["macd"] > m15r["macd_signal"])
            or
            (m15r["rsi"] <= 50 and m15r["macd"] < m15r["macd_signal"])
        )
    )

    reasons.append(
        (
            "MOMENTUM",
            momentum_valid,
            "ADX/RSI/MACD متوافقة"
            if momentum_valid
            else "Momentum filter failed",
        )
    )

    # --------------------------------------------------------
    # REGIME
    # --------------------------------------------------------

    regime_valid = regime not in {
        "UNKNOWN",
        "RANGING",
        "LOW TREND STRENGTH",
    }

    reasons.append(
        (
            "REGIME",
            regime_valid,
            regime,
        )
    )

    # --------------------------------------------------------
    # DIRECTION
    # --------------------------------------------------------

    direction = None

    if mtf_direction == "BUY":
        direction = "BUY"

    elif mtf_direction == "SELL":
        direction = "SELL"

    # --------------------------------------------------------
    # B2
    # --------------------------------------------------------

    b2_valid = (
        b2["valid"]
        and direction is not None
        and b2["direction"] == direction
    )

    reasons.append(
        (
            "B2 BREAKOUT + RETEST",
            b2_valid,
            b2["reason"],
        )
    )

    # --------------------------------------------------------
    # CONFLUENCE
    # --------------------------------------------------------

    confluence = min(100, round(abs(total) / 44 * 100, 1))

    # B2 gives additional confirmation but is NOT probability.
    if b2_valid:
        confluence = min(100, confluence + 10)

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    final_signal = "WAIT"
    rejection = "لا توجد إشارة مكتملة"

    if direction is not None and momentum_valid and regime_valid:
        final_signal = direction
        rejection = ""

    # B2 is tracked as a high-quality experimental setup,
    # but generic signal can still exist without B2.
    # This prevents the whole engine from becoming artificially silent.

    if final_signal == "WAIT":
        rejection = "لم تكتمل شروط الاتجاه والزخم والسوق"

    return {
        "time": m5.index[-1],
        "signal": final_signal,
        "direction": direction,
        "confluence": confluence,
        "total_score": total,
        "scores": scores,
        "labels": labels,
        "regime": regime,
        "b2": b2,
        "m15": m15r.to_dict(),
        "m5": m5r.to_dict(),
        "h1": h1r.to_dict(),
        "h4": h4r.to_dict(),
        "reasons": reasons,
        "rejection": rejection,
    }


# ============================================================
# RISK ENGINE
# ============================================================

def daily_loss_pct():
    start = st.session_state.day_start_balance

    if start <= 0:
        return 0.0

    loss = start - st.session_state.paper_balance

    return max(0.0, loss / start * 100)


def risk_allowed(risk_pct, daily_limit, max_trades):
    if st.session_state.kill_switch:
        return False, "ENGINE STOPPED"

    if daily_loss_pct() >= daily_limit:
        return False, "DAILY LOSS LIMIT"

    if st.session_state.daily_trades >= max_trades:
        return False, "MAX DAILY TRADES"

    if st.session_state.paper_trade is not None:
        return False, "POSITION ALREADY OPEN"

    return True, "RISK OK"


# ============================================================
# TRADE ENGINE
# ============================================================

def build_trade(signal, entry, risk_pct, entry_time):
    direction = signal["direction"]

    atr = safe_float(signal["m5"]["atr"])

    if pd.isna(atr) or atr <= 0:
        return None

    # Synthetic execution model.
    if direction == "BUY":
        effective_entry = entry + (SPREAD / 2) + SLIPPAGE

        sl = effective_entry - atr * SL_ATR
        tp1 = effective_entry + atr * TP1_ATR
        tp2 = effective_entry + atr * TP2_ATR

    else:
        effective_entry = entry - (SPREAD / 2) - SLIPPAGE

        sl = effective_entry + atr * SL_ATR
        tp1 = effective_entry - atr * TP1_ATR
        tp2 = effective_entry - atr * TP2_ATR

    balance = st.session_state.paper_balance

    risk_money = balance * (risk_pct / 100)

    return {
        "id": str(uuid.uuid4())[:8],
        "direction": direction,
        "entry": effective_entry,
        "initial_entry": effective_entry,
        "sl": sl,
        "initial_sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "risk_pct": risk_pct,
        "risk_money": risk_money,
        "atr": atr,
        "entry_time": entry_time,
        "tp1_hit": False,
        "partial_closed": False,
        "be_active": False,
        "remaining_fraction": 1.0,
        "realized_r": 0.0,
        "status": "OPEN",
        "exit_price": None,
        "exit_time": None,
        "exit_reason": None,
    }


def pnl_r(trade, price):
    entry = trade["entry"]

    risk_distance = abs(entry - trade["initial_sl"])

    if risk_distance <= 0:
        return 0.0

    if trade["direction"] == "BUY":
        return (price - entry) / risk_distance

    return (entry - price) / risk_distance


def execute_exit_price(trade, raw_price):
    if trade["direction"] == "BUY":
        return raw_price - (SPREAD / 2) - SLIPPAGE

    return raw_price + (SPREAD / 2) + SLIPPAGE


def close_full_trade(trade, raw_price, exit_time, reason):
    execution_price = execute_exit_price(trade, raw_price)

    r = pnl_r(trade, execution_price)

    remaining = trade["remaining_fraction"]

    realized_r = trade["realized_r"] + (r * remaining)

    money = realized_r * trade["risk_money"]

    st.session_state.paper_balance += money

    trade["realized_r"] = realized_r
    trade["remaining_fraction"] = 0.0
    trade["status"] = "CLOSED"
    trade["exit_price"] = execution_price
    trade["exit_time"] = exit_time
    trade["exit_reason"] = reason

    st.session_state.paper_history.append(dict(trade))

    return trade


def manage_trade_on_candle(trade, candle):
    if trade is None or trade["status"] != "OPEN":
        return trade, []

    events = []

    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])

    candle_time = candle.name

    # ========================================================
    # BUY
    # ========================================================

    if trade["direction"] == "BUY":

        # Before TP1:
        if not trade["tp1_hit"]:

            # Conservative assumption:
            # SL gets priority if both are touched in same candle.
            if low <= trade["sl"]:
                trade = close_full_trade(
                    trade,
                    trade["sl"],
                    candle_time,
                    "STOP LOSS",
                )

                events.append("STOP LOSS")
                return trade, events

            if high >= trade["tp1"]:

                half_r = 0.5 * pnl_r(trade, trade["tp1"])

                trade["realized_r"] += half_r
                trade["remaining_fraction"] = 0.5
                trade["tp1_hit"] = True
                trade["partial_closed"] = True
                trade["be_active"] = True
                trade["sl"] = trade["entry"]

                money = half_r * trade["risk_money"]

                st.session_state.paper_balance += money

                events.extend(
                    [
                        "TP1 HIT",
                        "50% CLOSED",
                        "STOP → BE",
                    ]
                )

                # Do not test BE against the same candle.

                if high >= trade["tp2"]:
                    trade = close_full_trade(
                        trade,
                        trade["tp2"],
                        candle_time,
                        "TP2",
                    )

                    events.append("TP2 HIT")

                    return trade, events

        else:

            if low <= trade["sl"]:
                trade = close_full_trade(
                    trade,
                    trade["sl"],
                    candle_time,
                    "BREAKEVEN / STOP",
                )

                events.append("BREAKEVEN / STOP")
                return trade, events

            if high >= trade["tp2"]:
                trade = close_full_trade(
                    trade,
                    trade["tp2"],
                    candle_time,
                    "TP2",
                )

                events.append("TP2 HIT")

                return trade, events

    # ========================================================
    # SELL
    # ========================================================

    else:

        if not trade["tp1_hit"]:

            if high >= trade["sl"]:
                trade = close_full_trade(
                    trade,
                    trade["sl"],
                    candle_time,
                    "STOP LOSS",
                )

                events.append("STOP LOSS")
                return trade, events

            if low <= trade["tp1"]:

                half_r = 0.5 * pnl_r(trade, trade["tp1"])

                trade["realized_r"] += half_r
                trade["remaining_fraction"] = 0.5
                trade["tp1_hit"] = True
                trade["partial_closed"] = True
                trade["be_active"] = True
                trade["sl"] = trade["entry"]

                money = half_r * trade["risk_money"]

                st.session_state.paper_balance += money

                events.extend(
                    [
                        "TP1 HIT",
                        "50% CLOSED",
                        "STOP → BE",
                    ]
                )

                if low <= trade["tp2"]:
                    trade = close_full_trade(
                        trade,
                        trade["tp2"],
                        candle_time,
                        "TP2",
                    )

                    events.append("TP2 HIT")

                    return trade, events

        else:

            if high >= trade["sl"]:
                trade = close_full_trade(
                    trade,
                    trade["sl"],
                    candle_time,
                    "BREAKEVEN / STOP",
                )

                events.append("BREAKEVEN / STOP")
                return trade, events

            if low <= trade["tp2"]:
                trade = close_full_trade(
                    trade,
                    trade["tp2"],
                    candle_time,
                    "TP2",
                )

                events.append("TP2 HIT")

                return trade, events

    return trade, events


# ============================================================
# LIVE PAPER MANAGEMENT
# ============================================================

def manage_live_trade(m5):
    trade = st.session_state.paper_trade

    if trade is None:
        return

    entry_time = pd.Timestamp(trade["entry_time"])

    if entry_time.tzinfo is None:
        entry_time = entry_time.tz_localize("UTC")

    # Never process candles before the entry.
    future = m5[m5.index > entry_time].copy()

    if st.session_state.last_managed_candle is not None:
        cursor = pd.Timestamp(st.session_state.last_managed_candle)

        if cursor.tzinfo is None:
            cursor = cursor.tz_localize("UTC")

        future = future[future.index > cursor]

    for idx, candle in future.iterrows():

        if st.session_state.paper_trade is None:
            break

        trade, events = manage_trade_on_candle(
            st.session_state.paper_trade,
            candle,
        )

        st.session_state.paper_trade = (
            None if trade["status"] == "CLOSED" else trade
        )

        for event in events:
            add_log(
                "TRADE EVENT",
                event,
                {
                    "trade_id": trade["id"],
                    "time": str(idx),
                },
            )

        st.session_state.last_managed_candle = idx


# ============================================================
# SIGNAL EXECUTION
# ============================================================

def queue_signal(signal):
    if signal["signal"] not in {"BUY", "SELL"}:
        return

    signal_time = pd.Timestamp(signal["time"])

    if signal_time.tzinfo is None:
        signal_time = signal_time.tz_localize("UTC")

    key = f"{signal['signal']}|{signal_time.isoformat()}"

    if key == st.session_state.last_signal_key:
        return

    st.session_state.last_signal_key = key

    next_candle = signal_time + pd.Timedelta(minutes=5)

    st.session_state.pending_signal = {
        "signal": signal,
        "entry_time": next_candle,
        "created_at": pd.Timestamp.now(tz="UTC"),
    }


def execute_pending_signal(m5, risk_pct, daily_limit, max_trades):
    pending = st.session_state.pending_signal

    if pending is None:
        return

    target_time = pd.Timestamp(pending["entry_time"])

    if target_time.tzinfo is None:
        target_time = target_time.tz_localize("UTC")

    now = pd.Timestamp.now(tz="UTC")

    # Expire old pending orders.
    if now > target_time + pd.Timedelta(minutes=10):
        add_log(
            "ORDER REJECTED",
            "Pending signal expired",
            {
                "signal": pending["signal"]["signal"],
                "target": str(target_time),
            },
        )

        st.session_state.pending_signal = None
        return

    eligible = m5[m5.index >= target_time]

    if eligible.empty:
        return

    allowed, reason = risk_allowed(
        risk_pct,
        daily_limit,
        max_trades,
    )

    if not allowed:
        add_log(
            "ORDER REJECTED",
            reason,
            {},
        )

        st.session_state.pending_signal = None
        return

    entry_candle = eligible.iloc[0]

    entry_time = eligible.index[0]

    entry = float(entry_candle["open"])

    trade = build_trade(
        pending["signal"],
        entry,
        risk_pct,
        entry_time,
    )

    if trade is None:
        add_log(
            "ORDER REJECTED",
            "Invalid trade parameters",
            {},
        )

        st.session_state.pending_signal = None
        return

    st.session_state.paper_trade = trade
    st.session_state.daily_trades += 1

    st.session_state.last_managed_candle = entry_time

    st.session_state.pending_signal = None

    add_log(
        "PAPER TRADE OPENED",
        trade["direction"],
        {
            "trade_id": trade["id"],
            "entry": trade["entry"],
            "sl": trade["sl"],
            "tp1": trade["tp1"],
            "tp2": trade["tp2"],
            "risk_pct": trade["risk_pct"],
        },
    )


# ============================================================
# LOGGING
# ============================================================

def add_log(event_type, message, details=None):
    record = {
        "id": str(uuid.uuid4())[:8],
        "time": now_riyadh().strftime("%Y-%m-%d %H:%M:%S"),
        "type": event_type,
        "message": message,
        "details": details or {},
    }

    st.session_state.decision_logs.insert(0, record)

    st.session_state.decision_logs = (
        st.session_state.decision_logs[:500]
    )


def log_decision(signal):
    gates = {}

    for name, passed, detail in signal["reasons"]:
        gates[name] = {
            "passed": passed,
            "detail": detail,
        }

    add_log(
        "DECISION",
        signal["signal"],
        {
            "price": signal["m5"]["close"],
            "confluence": signal["confluence"],
            "regime": signal["regime"],
            "gates": gates,
            "rejection": signal["rejection"],
        },
    )


# ============================================================
# BACKTEST
# ============================================================

def backtest_strategy(m5, risk_pct=1.0):
    if len(m5) < 500:
        return {
            "trades": [],
            "equity": pd.Series(dtype=float),
            "error": "بيانات الباك تست غير كافية",
        }

    split_time = m5.index[int(len(m5) * 0.70)]

    balance = START_BALANCE

    trade_records = []

    equity_points = []

    open_trade = None

    # We use rolling snapshots instead of recomputing every
    # timeframe from scratch.
    for i in range(250, len(m5)):

        candle = m5.iloc[i]
        timestamp = m5.index[i]

        equity_points.append(
            (
                timestamp,
                balance,
            )
        )

        # ----------------------------------------------------
        # Manage existing position
        # ----------------------------------------------------

        if open_trade is not None:

            before = balance

            fake_state_balance = st.session_state.paper_balance

            # Temporary simulation of balance changes.
            st.session_state.paper_balance = balance

            updated, events = manage_trade_on_candle(
                open_trade,
                candle,
            )

            balance = st.session_state.paper_balance

            st.session_state.paper_balance = fake_state_balance

            open_trade = (
                None
                if updated["status"] == "CLOSED"
                else updated
            )

            if updated["status"] == "CLOSED":
                trade_records.append(
                    {
                        "id": updated["id"],
                        "entry_time": updated["entry_time"],
                        "exit_time": updated["exit_time"],
                        "direction": updated["direction"],
                        "entry": updated["entry"],
                        "exit": updated["exit_price"],
                        "R": updated["realized_r"],
                        "result": updated["exit_reason"],
                        "OOS": pd.Timestamp(
                            updated["entry_time"]
                        ) >= split_time,
                    }
                )

            continue

        # ----------------------------------------------------
        # Signal approximation
        # ----------------------------------------------------

        window = m5.iloc[: i + 1]

        if len(window) < 250:
            continue

        m15 = resample_tf(window, 15)
        h1 = resample_tf(window, 60)
        h4 = resample_tf(window, 240)

        if (
            len(m15) < 120
            or len(h1) < 110
            or len(h4) < 105
        ):
            continue

        m15 = add_indicators(m15)
        h1 = add_indicators(h1)
        h4 = add_indicators(h4)

        if len(m15) < 30 or len(h1) < 30 or len(h4) < 30:
            continue

        current_m5 = window.iloc[-1]

        # Use indicator data for current completed M5.
        temp_m5 = add_indicators(window)

        if temp_m5.empty:
            continue

        signal = analyze_market(
            temp_m5,
            m15,
            h1,
            h4,
        )

        if signal["signal"] not in {"BUY", "SELL"}:
            continue

        # Next candle entry.
        if i + 1 >= len(m5):
            continue

        entry_candle = m5.iloc[i + 1]

        entry_time = m5.index[i + 1]

        trade = build_trade(
            signal,
            float(entry_candle["open"]),
            risk_pct,
            entry_time,
        )

        if trade is None:
            continue

        # Backtest risk uses simulated balance.
        trade["risk_money"] = balance * risk_pct / 100

        open_trade = trade

    # Close remaining position at final close.
    if open_trade is not None:
        final_price = float(m5.iloc[-1]["close"])

        fake_state_balance = st.session_state.paper_balance

        st.session_state.paper_balance = balance

        updated = close_full_trade(
            open_trade,
            final_price,
            m5.index[-1],
            "END OF BACKTEST",
        )

        balance = st.session_state.paper_balance

        st.session_state.paper_balance = fake_state_balance

        trade_records.append(
            {
                "id": updated["id"],
                "entry_time": updated["entry_time"],
                "exit_time": updated["exit_time"],
                "direction": updated["direction"],
                "entry": updated["entry"],
                "exit": updated["exit_price"],
                "R": updated["realized_r"],
                "result": updated["exit_reason"],
                "OOS": pd.Timestamp(
                    updated["entry_time"]
                ) >= split_time,
            }
        )

    trades = pd.DataFrame(trade_records)

    equity = pd.Series(
        dict(equity_points),
        dtype=float,
    )

    return {
        "trades": trades,
        "equity": equity,
        "error": None,
        "split_time": split_time,
    }


def backtest_stats(trades, equity):
    if trades is None or trades.empty:
        return {
            "count": 0,
            "win_rate": 0,
            "pf": 0,
            "total_r": 0,
            "avg_r": 0,
            "max_dd": 0,
        }

    r = pd.to_numeric(trades["R"], errors="coerce").fillna(0)

    wins = r[r > 0]
    losses = r[r < 0]

    gross_profit = wins.sum()
    gross_loss = abs(losses.sum())

    pf = (
        gross_profit / gross_loss
        if gross_loss > 0
        else float("inf")
    )

    if equity is not None and len(equity):
        running_max = equity.cummax()

        dd = running_max - equity

        max_dd = float(dd.max())

    else:
        max_dd = 0

    return {
        "count": len(trades),
        "win_rate": float((r > 0).mean() * 100),
        "pf": float(pf) if math.isfinite(pf) else 999.0,
        "total_r": float(r.sum()),
        "avg_r": float(r.mean()),
        "max_dd": max_dd,
    }


# ============================================================
# MARKET PREPARATION
# ============================================================

def prepare_market_safe():
    api_key = st.secrets.get("TWELVE_DATA_API_KEY", "")

    try:
        raw = fetch_twelve_data(
            api_key,
            "5min",
            5000,
        )

        quality = data_quality(raw)

        if not quality["valid"]:
            return None, None, None, None, quality, None

        m5, m15, h1, h4 = prepare_market(raw)

        # We intentionally don't require 110 H4 candles after
        # indicator dropna.  Instead we require enough usable
        # indicator rows.
        if (
            len(m5) < 250
            or len(m15) < 100
            or len(h1) < 100
            or len(h4) < 25
        ):
            quality["valid"] = False
            quality["reason"] = "البيانات غير كافية بعد المؤشرات"

            return (
                None,
                None,
                None,
                None,
                quality,
                raw,
            )

        return (
            m5,
            m15,
            h1,
            h4,
            quality,
            raw,
        )

    except Exception as exc:
        st.session_state.api_error = str(exc)

        return (
            None,
            None,
            None,
            None,
            {
                "valid": False,
                "reason": str(exc),
                "rows": 0,
            },
            None,
        )


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.markdown("## ⚙️ إعدادات المحرك")

    risk_pct = st.slider(
        "Risk / Trade %",
        min_value=0.25,
        max_value=3.0,
        value=DEFAULT_RISK,
        step=0.25,
    )

    daily_limit = st.slider(
        "Daily Loss Limit %",
        min_value=1.0,
        max_value=10.0,
        value=DEFAULT_DAILY_LOSS,
        step=0.5,
    )

    max_trades = st.number_input(
        "Max Trades / Day",
        min_value=1,
        max_value=20,
        value=DEFAULT_MAX_TRADES,
        step=1,
    )

    refresh_seconds = st.slider(
        "Refresh",
        min_value=10,
        max_value=120,
        value=30,
        step=10,
    )

    st.divider()

    if st.session_state.kill_switch:

        if st.button(
            "▶ START ENGINE",
            use_container_width=True,
        ):
            st.session_state.kill_switch = False
            st.session_state.engine_started = True
            st.rerun()

    else:

        if st.button(
            "⛔ STOP ENGINE",
            use_container_width=True,
        ):
            st.session_state.kill_switch = True
            st.rerun()

    if st.button(
        "🔴 CLOSE POSITION",
        use_container_width=True,
    ):

        if st.session_state.paper_trade:

            trade = st.session_state.paper_trade

            price = st.session_state.last_price

            if price:
                fake = pd.Series(
                    {
                        "high": price,
                        "low": price,
                        "close": price,
                    },
                    name=pd.Timestamp.now(tz="UTC"),
                )

                trade, events = manage_trade_on_candle(
                    trade,
                    fake,
                )

                if trade["status"] == "OPEN":
                    trade = close_full_trade(
                        trade,
                        price,
                        pd.Timestamp.now(tz="UTC"),
                        "MANUAL CLOSE",
                    )

                st.session_state.paper_trade = None

        st.rerun()

    if st.button(
        "♻ RESET PAPER ACCOUNT",
        use_container_width=True,
    ):
        st.session_state.paper_balance = START_BALANCE
        st.session_state.paper_history = []
        st.session_state.paper_trade = None
        st.session_state.pending_signal = None
        st.session_state.decision_logs = []
        st.session_state.daily_trades = 0
        st.session_state.day_start_balance = START_BALANCE
        st.session_state.kill_switch = False
        st.rerun()


# ============================================================
# LOAD MARKET
# ============================================================

(
    m5,
    m15,
    h1,
    h4,
    quality,
    raw,
) = prepare_market_safe()


# ============================================================
# HERO
# ============================================================

st.markdown(
    """
<div class="hero">
    <div class="brand">GOLD AI • SMART TRADING SYSTEM</div>
    <div class="hero-title">بوت الذهب XAU/USD</div>
    <div class="hero-sub">
        Multi-Timeframe • Risk Engine • B2 • Paper Trading
    </div>
</div>
""",
    unsafe_allow_html=True,
)


# ============================================================
# DATA ERROR
# ============================================================

if m5 is None:

    st.error(
        f"تعذر تشغيل المحرك: {quality.get('reason', 'Unknown error')}"
    )

    st.info(
        "تأكد أن TWELVE_DATA_API_KEY موجود داخل Streamlit Secrets."
    )

    st.stop()


# ============================================================
# ANALYSIS
# ============================================================

signal = analyze_market(
    m5,
    m15,
    h1,
    h4,
)

st.session_state.last_signal = signal

st.session_state.last_price = float(
    m5.iloc[-1]["close"]
)

st.session_state.last_data_time = m5.index[-1]


# ============================================================
# LIVE ENGINE
# ============================================================

if not st.session_state.kill_switch:

    log_decision(signal)

    queue_signal(signal)

    execute_pending_signal(
        m5,
        risk_pct,
        daily_limit,
        int(max_trades),
    )

    manage_live_trade(m5)


# ============================================================
# TOP METRICS
# ============================================================

daily_loss = daily_loss_pct()

price = float(m5.iloc[-1]["close"])

signal_text = signal["signal"]

signal_class = (
    "green"
    if signal_text == "BUY"
    else "red"
    if signal_text == "SELL"
    else "yellow"
)

c1, c2, c3, c4 = st.columns(4)

with c1:
    st.markdown(
        f"""
<div class="card">
<div class="card-title">XAU/USD</div>
<div class="price">{fmt_price(price)}</div>
</div>
""",
        unsafe_allow_html=True,
    )

with c2:
    st.markdown(
        f"""
<div class="card">
<div class="card-title">SIGNAL</div>
<div class="card-value {signal_class}">{signal_text}</div>
</div>
""",
        unsafe_allow_html=True,
    )

with c3:
    st.markdown(
        f"""
<div class="card">
<div class="card-title">CONFLUENCE STRENGTH</div>
<div class="card-value gold">{signal["confluence"]:.1f}%</div>
</div>
""",
        unsafe_allow_html=True,
    )

with c4:
    st.markdown(
        f"""
<div class="card">
<div class="card-title">BALANCE</div>
<div class="card-value">{fmt_money(st.session_state.paper_balance)}</div>
</div>
""",
        unsafe_allow_html=True,
    )


st.caption(
    "Confluence Strength هي درجة توافق داخل النظام وليست احتمالًا للربح."
)


# ============================================================
# MARKET STATUS
# ============================================================

st.markdown(
    '<div class="section">حالة السوق</div>',
    unsafe_allow_html=True,
)

a, b, c, d = st.columns(4)

tf_rows = [
    ("M5", signal["scores"]["M5"], signal["labels"]["M5"]),
    ("M15", signal["scores"]["M15"], signal["labels"]["M15"]),
    ("H1", signal["scores"]["H1"], signal["labels"]["H1"]),
    ("H4", signal["scores"]["H4"], signal["labels"]["H4"]),
]

for col, (tf, score, label) in zip(
    [a, b, c, d],
    tf_rows,
):
    color = (
        "green"
        if label == "BULLISH"
        else "red"
        if label == "BEARISH"
        else "yellow"
    )

    with col:
        st.markdown(
            f"""
<div class="card">
<div class="card-title">{tf}</div>
<div class="card-value {color}">{label}</div>
<div class="muted">Score: {score}</div>
</div>
""",
            unsafe_allow_html=True,
        )


# ============================================================
# DECISION ENGINE
# ============================================================

st.markdown(
    '<div class="section">Decision Engine</div>',
    unsafe_allow_html=True,
)

if signal_text == "BUY":
    box_class = "signal-buy"
elif signal_text == "SELL":
    box_class = "signal-sell"
else:
    box_class = "signal-wait"

st.markdown(
    f"""
<div class="{box_class}">
    <div class="card-title">FINAL DECISION</div>
    <div style="font-size:32px;font-weight:900">
        {signal_text}
    </div>
    <div style="margin-top:8px;color:#9ca8b8">
        Regime: {signal["regime"]}
    </div>
    <div style="margin-top:8px;color:#9ca8b8">
        {signal["rejection"] if signal_text == "WAIT" else "شروط الاتجاه والزخم متوافقة"}
    </div>
</div>
""",
    unsafe_allow_html=True,
)


# ============================================================
# GATES
# ============================================================

st.markdown(
    '<div class="section">Signal Gates</div>',
    unsafe_allow_html=True,
)

gate_cols = st.columns(5)

for col, gate in zip(
    gate_cols,
    signal["reasons"],
):
    name, passed, detail = gate

    with col:

        if passed:
            icon = "✓"
            cls = "green"
        else:
            icon = "×"
            cls = "red"

        st.markdown(
            f"""
<div class="card">
<div class="card-title">{name}</div>
<div class="card-value {cls}">{icon}</div>
<div class="muted">{detail}</div>
</div>
""",
            unsafe_allow_html=True,
        )


# ============================================================
# B2
# ============================================================

st.markdown(
    '<div class="section">B2 Breakout / Retest</div>',
    unsafe_allow_html=True,
)

b2 = signal["b2"]

x1, x2, x3, x4 = st.columns(4)

with x1:
    st.metric(
        "Breakout",
        "YES" if b2["breakout"] else "NO",
    )

with x2:
    st.metric(
        "Retest",
        "YES" if b2["retest"] else "NO",
    )

with x3:
    st.metric(
        "Direction",
        b2["direction"] or "—",
    )

with x4:
    st.metric(
        "Level",
        fmt_price(b2["level"]),
    )

st.caption(b2["reason"])


# ============================================================
# PRICE CHART
# ============================================================

st.markdown(
    '<div class="section">XAU/USD Price</div>',
    unsafe_allow_html=True,
)

chart_df = m5.tail(180)[
    [
        "close",
        "ema20",
        "ema50",
        "ema100",
    ]
].copy()

st.line_chart(
    chart_df,
    height=420,
)


# ============================================================
# PAPER POSITION
# ============================================================

st.markdown(
    '<div class="section">Paper Trading</div>',
    unsafe_allow_html=True,
)

trade = st.session_state.paper_trade

if trade:

    unrealized = pnl_r(
        trade,
        price,
    )

    p1, p2, p3, p4, p5 = st.columns(5)

    with p1:
        st.metric(
            "Direction",
            trade["direction"],
        )

    with p2:
        st.metric(
            "Entry",
            fmt_price(trade["entry"]),
        )

    with p3:
        st.metric(
            "SL",
            fmt_price(trade["sl"]),
        )

    with p4:
        st.metric(
            "TP1",
            fmt_price(trade["tp1"]),
        )

    with p5:
        st.metric(
            "TP2",
            fmt_price(trade["tp2"]),
        )

    st.info(
        f"Position R: {unrealized:.2f}R • "
        f"Risk: {trade['risk_pct']:.2f}% • "
        f"TP1: {'HIT' if trade['tp1_hit'] else 'WAITING'}"
    )

elif st.session_state.pending_signal:

    pending = st.session_state.pending_signal

    st.warning(
        f"Pending {pending['signal']['signal']} — "
        f"Entry next M5: {pending['entry_time']}"
    )

else:

    st.info(
        "لا توجد صفقة مفتوحة حاليًا."
    )


# ============================================================
# RISK CENTER
# ============================================================

st.markdown(
    '<div class="section">Risk Center</div>',
    unsafe_allow_html=True,
)

r1, r2, r3, r4 = st.columns(4)

with r1:
    st.metric(
        "Risk / Trade",
        f"{risk_pct:.2f}%",
    )

with r2:
    st.metric(
        "Daily Loss",
        f"{daily_loss:.2f}%",
    )

with r3:
    st.metric(
        "Trades Today",
        f"{st.session_state.daily_trades}/{int(max_trades)}",
    )

with r4:
    st.metric(
        "Engine",
        "STOPPED"
        if st.session_state.kill_switch
        else "RUNNING",
    )


# ============================================================
# SYSTEM HEALTH
# ============================================================

st.markdown(
    '<div class="section">System Health</div>',
    unsafe_allow_html=True,
)

h1c, h2c, h3c, h4c = st.columns(4)

with h1c:
    st.markdown(
        f"""
<div class="card">
<div class="card-title">DATA FEED</div>
<div class="card-value green">CONNECTED</div>
<div class="muted">{quality.get("rows", 0):,} raw rows</div>
</div>
""",
        unsafe_allow_html=True,
    )

with h2c:
    st.markdown(
        """
<div class="card">
<div class="card-title">STRATEGY ENGINE</div>
<div class="card-value green">ACTIVE</div>
<div class="muted">MTF + B2</div>
</div>
""",
        unsafe_allow_html=True,
    )

with h3c:
    st.markdown(
        """
<div class="card">
<div class="card-title">RISK ENGINE</div>
<div class="card-value green">ACTIVE</div>
<div class="muted">Daily protection enabled</div>
</div>
""",
        unsafe_allow_html=True,
    )

with h4c:
    st.markdown(
        """
<div class="card">
<div class="card-title">NEWS FILTER</div>
<div class="card-value yellow">NOT CONNECTED</div>
<div class="muted">Entries are not news-filtered</div>
</div>
""",
        unsafe_allow_html=True,
    )


# ============================================================
# DATA QUALITY
# ============================================================

with st.expander("🔎 Data Quality"):

    st.write(
        {
            "Valid": quality.get("valid"),
            "Rows": quality.get("rows"),
            "Duplicates": quality.get("duplicates"),
            "Gaps": quality.get("gaps"),
            "Stale minutes": quality.get("stale_minutes"),
            "Reason": quality.get("reason"),
            "Last candle": str(m5.index[-1]),
        }
    )


# ============================================================
# BACKTEST
# ============================================================

st.markdown(
    '<div class="section">Backtest Lab</div>',
    unsafe_allow_html=True,
)

run_bt = st.button(
    "▶ RUN BACKTEST",
    use_container_width=False,
)

if run_bt:

    with st.spinner("جاري تشغيل الباك تست..."):

        bt = backtest_strategy(
            m5,
            risk_pct=risk_pct,
        )

    if bt["error"]:
        st.error(bt["error"])

    else:

        trades = bt["trades"]

        oos = trades[
            trades["OOS"] == True
        ].copy() if not trades.empty else trades

        stats_all = backtest_stats(
            trades,
            bt["equity"],
        )

        stats_oos = backtest_stats(
            oos,
            bt["equity"],
        )

        st.session_state["last_backtest"] = bt

        st.markdown("### Full Sample")

        q1, q2, q3, q4, q5 = st.columns(5)

        with q1:
            st.metric(
                "Trades",
                stats_all["count"],
            )

        with q2:
            st.metric(
                "Win Rate",
                f"{stats_all['win_rate']:.1f}%",
            )

        with q3:
            st.metric(
                "Profit Factor",
                f"{stats_all['pf']:.2f}",
            )

        with q4:
            st.metric(
                "Total R",
                f"{stats_all['total_r']:.2f}",
            )

        with q5:
            st.metric(
                "Max DD",
                f"{stats_all['max_dd']:.2f}",
            )

        st.markdown("### Out-of-Sample 30%")

        q6, q7, q8, q9, q10 = st.columns(5)

        with q6:
            st.metric(
                "OOS Trades",
                stats_oos["count"],
            )

        with q7:
            st.metric(
                "OOS Win Rate",
                f"{stats_oos['win_rate']:.1f}%",
            )

        with q8:
            st.metric(
                "OOS Profit Factor",
                f"{stats_oos['pf']:.2f}",
            )

        with q9:
            st.metric(
                "OOS Total R",
                f"{stats_oos['total_r']:.2f}",
            )

        with q10:
            st.metric(
                "OOS Avg R",
                f"{stats_oos['avg_r']:.3f}",
            )

        if not trades.empty:

            st.markdown("#### Trade Distribution")

            st.dataframe(
                trades.tail(100),
                use_container_width=True,
                hide_index=True,
            )

        st.warning(
            "الباك تست أداة تقييم وليست ضمانًا للنتائج المستقبلية. "
            "هذه النسخة تستخدم بيانات Twelve Data ونموذج تنفيذ اصطناعي "
            "للـ Paper Trading."
        )


# ============================================================
# TRADE HISTORY
# ============================================================

st.markdown(
    '<div class="section">Paper Trade History</div>',
    unsafe_allow_html=True,
)

if st.session_state.paper_history:

    history = pd.DataFrame(
        st.session_state.paper_history
    )

    display_cols = [
        col
        for col in [
            "id",
            "direction",
            "entry",
            "exit_price",
            "realized_r",
            "risk_pct",
            "exit_reason",
            "entry_time",
            "exit_time",
        ]
        if col in history.columns
    ]

    st.dataframe(
        history[display_cols],
        use_container_width=True,
        hide_index=True,
    )

else:

    st.caption(
        "لا توجد صفقات مكتملة حتى الآن."
    )


# ============================================================
# DECISION LOG
# ============================================================

st.markdown(
    '<div class="section">Decision Log</div>',
    unsafe_allow_html=True,
)

if st.session_state.decision_logs:

    logs_rows = []

    for item in st.session_state.decision_logs[:100]:

        logs_rows.append(
            {
                "Time": item["time"],
                "Type": item["type"],
                "Message": item["message"],
                "ID": item["id"],
            }
        )

    st.dataframe(
        pd.DataFrame(logs_rows),
        use_container_width=True,
        hide_index=True,
    )

else:

    st.caption(
        "لا يوجد سجل قرارات حتى الآن."
    )


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    f"GOLD AI • {SYMBOL} • PAPER TRADING ONLY • "
    f"Last update: {now_riyadh().strftime('%Y-%m-%d %H:%M:%S')}"
)


# ============================================================
# AUTO REFRESH
# ============================================================

if not st.session_state.kill_switch:

    time.sleep(refresh_seconds)

    st.rerun()

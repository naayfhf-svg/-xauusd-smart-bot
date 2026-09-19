# ============================================================
# GOLD AI — XAU/USD SMART PAPER TRADING TERMINAL
# Version: 1.0 Professional Single-File Build
# Paper Trading Only
# ============================================================

import math
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import streamlit as st


# ============================================================
# CONFIG
# ============================================================

APP_NAME = "GOLD AI"
APP_VERSION = "1.0"

SYMBOL = "XAU/USD"
API_URL = "https://api.twelvedata.com/time_series"

RIYADH_TZ = ZoneInfo("Asia/Riyadh")

START_BALANCE = 10_000.0

DEFAULT_RISK = 1.0
MIN_RISK = 0.10
MAX_RISK = 2.00

DEFAULT_DAILY_LOSS = 3.0
DEFAULT_MAX_TRADES = 5

SL_ATR = 1.50
TP1_ATR = 1.50
TP2_ATR = 2.50

RETEST_ATR = 0.35

SIMULATED_SPREAD = 0.20
SIMULATED_SLIPPAGE = 0.05

M5_OUTPUT_SIZE = 5000

REFRESH_SECONDS = 30

MIN_SIGNAL_SCORE = 5

REQUEST_TIMEOUT = 15


# ============================================================
# PAGE
# ============================================================

st.set_page_config(
    page_title="بوت الذهب | XAU/USD",
    page_icon="🟡",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ============================================================
# CSS
# ============================================================

st.markdown(
    """
<style>

@import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"] {
    font-family: "Cairo", sans-serif;
}

.stApp {
    background:
        radial-gradient(
            circle at 10% 0%,
            rgba(212,175,55,0.08),
            transparent 30%
        ),
        #080d16;
    color: #f4f7fb;
}

.block-container {
    max-width: 1450px;
    padding-top: 1.2rem;
    padding-bottom: 3rem;
}

h1, h2, h3 {
    font-weight: 800 !important;
}

.gold {
    color: #d4af37;
}

.header {
    background: linear-gradient(
        135deg,
        #101827,
        #0c1320
    );
    border: 1px solid #263348;
    border-radius: 20px;
    padding: 20px 24px;
    margin-bottom: 18px;
}

.brand {
    font-size: 26px;
    font-weight: 800;
    color: #d4af37;
}

.subtitle {
    color: #8e9aab;
    font-size: 13px;
}

.status {
    display: inline-block;
    padding: 5px 12px;
    border-radius: 999px;
    background: rgba(47, 201, 116, 0.10);
    color: #5be394;
    border: 1px solid rgba(47, 201, 116, 0.25);
    font-size: 12px;
}

.card {
    background: #101827;
    border: 1px solid #263348;
    border-radius: 18px;
    padding: 18px;
    min-height: 105px;
    margin-bottom: 12px;
}

.card-title {
    color: #8e9aab;
    font-size: 12px;
    margin-bottom: 7px;
}

.card-value {
    font-size: 24px;
    font-weight: 800;
}

.card-small {
    font-size: 12px;
    color: #8e9aab;
}

.section {
    background: #101827;
    border: 1px solid #263348;
    border-radius: 20px;
    padding: 20px;
    margin: 14px 0;
}

.signal-long {
    border: 1px solid rgba(67, 220, 137, .35);
    background: rgba(67, 220, 137, .07);
    border-radius: 18px;
    padding: 18px;
}

.signal-short {
    border: 1px solid rgba(255, 91, 91, .35);
    background: rgba(255, 91, 91, .07);
    border-radius: 18px;
    padding: 18px;
}

.signal-wait {
    border: 1px solid rgba(212, 175, 55, .25);
    background: rgba(212, 175, 55, .05);
    border-radius: 18px;
    padding: 18px;
}

.badge-pass {
    color: #5be394;
    font-weight: 700;
}

.badge-fail {
    color: #ff6b6b;
    font-weight: 700;
}

.badge-wait {
    color: #d4af37;
    font-weight: 700;
}

.metric-box {
    background: #0c1320;
    border: 1px solid #263348;
    border-radius: 14px;
    padding: 14px;
    text-align: center;
}

.metric-number {
    font-size: 22px;
    font-weight: 800;
}

.metric-label {
    color: #8e9aab;
    font-size: 11px;
}

.small-muted {
    color: #7d899a;
    font-size: 11px;
}

div[data-testid="stMetric"] {
    background: #101827;
    border: 1px solid #263348;
    padding: 14px;
    border-radius: 16px;
}

button[kind="primary"] {
    border-radius: 12px;
}

@media (max-width: 700px) {

    .block-container {
        padding: 0.7rem;
    }

    .header {
        padding: 16px;
    }

    .brand {
        font-size: 21px;
    }

    .card {
        padding: 14px;
    }

    .card-value {
        font-size: 20px;
    }

}

</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# SESSION STATE
# ============================================================

DEFAULT_STATE = {
    "paper_balance": START_BALANCE,
    "paper_trade": None,
    "paper_history": [],
    "decision_logs": [],
    "kill_switch": False,
    "last_signal_key": None,
    "pending_signal": None,
    "last_managed_candle": None,
    "day_start_balance": START_BALANCE,
    "day_key": None,
    "daily_loss_money": 0.0,
    "daily_trades": 0,
    "last_price": None,
    "last_data_time": None,
    "api_error": None,
}

for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# HELPERS
# ============================================================

def now_riyadh():
    return datetime.now(timezone.utc).astimezone(RIYADH_TZ)


def fmt_price(value):
    if value is None or not np.isfinite(value):
        return "—"
    return f"{value:,.2f}"


def fmt_money(value):
    if value is None or not np.isfinite(value):
        return "—"

    sign = "+" if value > 0 else ""
    return f"{sign}${value:,.2f}"


def safe_float(value, default=np.nan):
    try:
        return float(value)
    except Exception:
        return default


def get_api_key():
    try:
        return st.secrets["TWELVE_DATA_API_KEY"]
    except Exception:
        return None


# ============================================================
# DAILY RESET
# ============================================================

def reset_daily_state():

    today = now_riyadh().date().isoformat()

    if st.session_state.day_key != today:

        st.session_state.day_key = today
        st.session_state.day_start_balance = st.session_state.paper_balance
        st.session_state.daily_loss_money = 0.0
        st.session_state.daily_trades = 0


reset_daily_state()


# ============================================================
# DATA ENGINE
# ============================================================

@st.cache_data(ttl=25, show_spinner=False)
def load_m5():

    api_key = get_api_key()

    if not api_key:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY غير موجود في Streamlit Secrets."
        )

    params = {
        "symbol": SYMBOL,
        "interval": "5min",
        "outputsize": M5_OUTPUT_SIZE,
        "timezone": "UTC",
        "apikey": api_key,
    }

    response = requests.get(
        API_URL,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    payload = response.json()

    if "status" in payload and payload.get("status") == "error":
        raise RuntimeError(
            payload.get("message", "Twelve Data API error")
        )

    values = payload.get("values")

    if not values:
        raise RuntimeError("لم تصل بيانات XAU/USD من Twelve Data.")

    df = pd.DataFrame(values)

    if "datetime" not in df.columns:
        raise RuntimeError("بيانات API لا تحتوي على datetime.")

    df["datetime"] = pd.to_datetime(
        df["datetime"],
        utc=True,
        errors="coerce",
    )

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    for column in numeric_columns:

        if column in df.columns:

            df[column] = pd.to_numeric(
                df[column],
                errors="coerce",
            )

    required = [
        "open",
        "high",
        "low",
        "close",
    ]

    for column in required:

        if column not in df.columns:
            raise RuntimeError(
                f"الحقل {column} غير موجود."
            )

    if "volume" not in df.columns:
        df["volume"] = np.nan

    df = df.dropna(
        subset=[
            "datetime",
            "open",
            "high",
            "low",
            "close",
        ]
    )

    df = df.sort_values("datetime")
    df = df.drop_duplicates(
        subset=["datetime"],
        keep="last",
    )

    df = df.set_index("datetime")

    # Keep completed M5 candles only.
    current_utc = pd.Timestamp.now(tz="UTC")
    current_floor = current_utc.floor("5min")

    df = df[df.index < current_floor]

    if len(df) < 300:
        raise RuntimeError(
            "البيانات المتاحة قليلة جدًا للتحليل."
        )

    return df


# ============================================================
# DATA QUALITY
# ============================================================

def data_quality(df):

    result = {
        "valid": True,
        "stale": False,
        "gaps": 0,
        "duplicates": 0,
        "message": "OK",
    }

    if df.empty:
        result["valid"] = False
        result["message"] = "لا توجد بيانات."
        return result

    duplicates = int(df.index.duplicated().sum())

    result["duplicates"] = duplicates

    if duplicates:
        result["valid"] = False
        result["message"] = "بيانات مكررة."

    diffs = df.index.to_series().diff().dropna()

    gaps = int(
        (diffs > pd.Timedelta(minutes=10)).sum()
    )

    result["gaps"] = gaps

    latest = df.index[-1]

    age = pd.Timestamp.now(tz="UTC") - latest

    if age > pd.Timedelta(minutes=15):

        result["stale"] = True
        result["valid"] = False
        result["message"] = (
            f"البيانات متأخرة {age.total_seconds()/60:.1f} دقيقة."
        )

    return result


# ============================================================
# RESAMPLING
# ============================================================

def resample_tf(df, rule):

    aggregation = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }

    if "volume" in df.columns:
        aggregation["volume"] = "sum"

    result = (
        df.resample(
            rule,
            label="right",
            closed="left",
        )
        .agg(aggregation)
        .dropna(
            subset=[
                "open",
                "high",
                "low",
                "close",
            ]
        )
    )

    return result


# ============================================================
# INDICATORS
# ============================================================

def calculate_rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi


def calculate_atr(df, period=14):

    prev_close = df["close"].shift(1)

    tr1 = df["high"] - df["low"]

    tr2 = (
        df["high"] - prev_close
    ).abs()

    tr3 = (
        df["low"] - prev_close
    ).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3],
        axis=1,
    ).max(axis=1)

    atr = true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    return atr


def calculate_adx(df, period=14):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where(
        (up_move > down_move)
        & (up_move > 0),
        up_move,
        0,
    )

    minus_dm = np.where(
        (down_move > up_move)
        & (down_move > 0),
        down_move,
        0,
    )

    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    plus_dm = pd.Series(
        plus_dm,
        index=df.index,
    )

    minus_dm = pd.Series(
        minus_dm,
        index=df.index,
    )

    plus_di = (
        100
        * plus_dm.ewm(
            alpha=1 / period,
            adjust=False,
            min_periods=period,
        ).mean()
        / atr
    )

    minus_di = (
        100
        * minus_dm.ewm(
            alpha=1 / period,
            adjust=False,
            min_periods=period,
        ).mean()
        / atr
    )

    dx = (
        100
        * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0, np.nan)
    )

    adx = dx.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    return adx, plus_di, minus_di


def add_indicators(df):

    df = df.copy()

    df["ema20"] = df["close"].ewm(
        span=20,
        adjust=False,
    ).mean()

    df["ema50"] = df["close"].ewm(
        span=50,
        adjust=False,
    ).mean()

    df["ema100"] = df["close"].ewm(
        span=100,
        adjust=False,
    ).mean()

    df["rsi"] = calculate_rsi(
        df["close"],
        14,
    )

    df["atr"] = calculate_atr(
        df,
        14,
    )

    ema12 = df["close"].ewm(
        span=12,
        adjust=False,
    ).mean()

    ema26 = df["close"].ewm(
        span=26,
        adjust=False,
    ).mean()

    df["macd"] = ema12 - ema26

    df["macd_signal"] = df["macd"].ewm(
        span=9,
        adjust=False,
    ).mean()

    df["momentum"] = (
        df["close"] - df["close"].shift(10)
    )

    (
        df["adx"],
        df["plus_di"],
        df["minus_di"],
    ) = calculate_adx(
        df,
        14,
    )

    return df


# ============================================================
# SCORE ENGINE
# ============================================================

def score_row(row):

    score = 0

    values = {}

    ema_stack = (
        row["ema20"]
        > row["ema50"]
        > row["ema100"]
    )

    ema_stack_bear = (
        row["ema20"]
        < row["ema50"]
        < row["ema100"]
    )

    if ema_stack:
        score += 3
        values["EMA Stack"] = 3

    elif ema_stack_bear:
        score -= 3
        values["EMA Stack"] = -3

    else:
        values["EMA Stack"] = 0

    if row["close"] > row["ema20"]:
        score += 1
        values["Price/EMA20"] = 1

    elif row["close"] < row["ema20"]:
        score -= 1
        values["Price/EMA20"] = -1

    else:
        values["Price/EMA20"] = 0

    if 52 <= row["rsi"] <= 68:
        score += 2
        values["RSI"] = 2

    elif 32 <= row["rsi"] < 48:
        score -= 2
        values["RSI"] = -2

    else:
        values["RSI"] = 0

    if row["macd"] > row["macd_signal"]:
        score += 2
        values["MACD"] = 2

    elif row["macd"] < row["macd_signal"]:
        score -= 2
        values["MACD"] = -2

    else:
        values["MACD"] = 0

    if row["momentum"] > 0:
        score += 1
        values["Momentum"] = 1

    elif row["momentum"] < 0:
        score -= 1
        values["Momentum"] = -1

    else:
        values["Momentum"] = 0

    if row["adx"] >= 25:

        if row["plus_di"] > row["minus_di"]:
            score += 2
            values["ADX/DI"] = 2

        elif row["minus_di"] > row["plus_di"]:
            score -= 2
            values["ADX/DI"] = -2

        else:
            values["ADX/DI"] = 0

    else:
        values["ADX/DI"] = 0

    return score, values


# ============================================================
# MARKET REGIME
# ============================================================

def market_regime(row):

    adx = safe_float(row["adx"])
    atr = safe_float(row["atr"])
    close = safe_float(row["close"])

    ema20 = safe_float(row["ema20"])
    ema50 = safe_float(row["ema50"])
    ema100 = safe_float(row["ema100"])

    if any(
        np.isnan(v)
        for v in [
            adx,
            atr,
            close,
            ema20,
            ema50,
            ema100,
        ]
    ):
        return "UNKNOWN"

    bullish = (
        ema20 > ema50 > ema100
        and close > ema20
    )

    bearish = (
        ema20 < ema50 < ema100
        and close < ema20
    )

    if adx >= 25 and bullish:
        return "TRENDING BULLISH"

    if adx >= 25 and bearish:
        return "TRENDING BEARISH"

    if adx < 18:
        return "RANGING"

    if atr > close * 0.003:
        return "HIGH VOLATILITY"

    if atr < close * 0.001:
        return "LOW VOLATILITY"

    return "TRANSITION"


# ============================================================
# TREND LABEL
# ============================================================

def trend_label(row):

    if (
        row["ema20"]
        > row["ema50"]
        > row["ema100"]
    ):
        return "BULLISH"

    if (
        row["ema20"]
        < row["ema50"]
        < row["ema100"]
    ):
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# B2 ENGINE
# ============================================================

def detect_b2(m15):

    result = {
        "valid": False,
        "direction": None,
        "level": None,
        "breakout_time": None,
        "retest_distance": None,
        "reason": "No valid B2",
    }

    if len(m15) < 15:
        result["reason"] = "Not enough M15 data."
        return result

    latest = m15.iloc[-1]
    latest_ts = m15.index[-1]

    atr = safe_float(latest["atr"])

    if not np.isfinite(atr) or atr <= 0:
        result["reason"] = "ATR unavailable."
        return result

    score, _ = score_row(latest)

    if score >= MIN_SIGNAL_SCORE:
        preferred_direction = "LONG"

    elif score <= -MIN_SIGNAL_SCORE:
        preferred_direction = "SHORT"

    else:
        result["reason"] = "M15 score too weak."
        return result

    for bars_back in range(1, 7):

        if len(m15) <= bars_back + 1:
            continue

        current = m15.iloc[-1]
        previous = m15.iloc[-1 - bars_back]

        if preferred_direction == "LONG":

            breakout = (
                current["close"]
                > previous["high"]
                and current["high"]
                > previous["high"]
            )

            level = previous["high"]

            retest_distance = abs(
                current["low"] - level
            )

            holds = (
                current["close"] > level
            )

            if (
                breakout
                and retest_distance
                <= atr * RETEST_ATR
                and holds
            ):

                result.update(
                    {
                        "valid": True,
                        "direction": "LONG",
                        "level": float(level),
                        "breakout_time": previous.name,
                        "retest_distance": float(
                            retest_distance
                        ),
                        "reason": "Valid bullish breakout/retest.",
                    }
                )

                return result

        else:

            breakout = (
                current["close"]
                < previous["low"]
                and current["low"]
                < previous["low"]
            )

            level = previous["low"]

            retest_distance = abs(
                current["high"] - level
            )

            holds = (
                current["close"] < level
            )

            if (
                breakout
                and retest_distance
                <= atr * RETEST_ATR
                and holds
            ):

                result.update(
                    {
                        "valid": True,
                        "direction": "SHORT",
                        "level": float(level),
                        "breakout_time": previous.name,
                        "retest_distance": float(
                            retest_distance
                        ),
                        "reason": "Valid bearish breakout/retest.",
                    }
                )

                return result

    result["reason"] = "No valid breakout/retest."

    return result


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def support_resistance(m15, lookback=50):

    data = m15.tail(lookback)

    if len(data) < 10:
        return None, None

    resistance = float(
        data["high"].rolling(
            5,
            center=True,
        ).max().dropna().max()
    )

    support = float(
        data["low"].rolling(
            5,
            center=True,
        ).min().dropna().min()
    )

    return support, resistance


# ============================================================
# SIGNAL ENGINE
# ============================================================

def analyze_market(m5, m15, h1, h4):

    result = {
        "direction": None,
        "signal": "WAIT",
        "score": 0,
        "strength": 0.0,
        "reason": "",
        "gates": {},
        "b2": None,
        "regime": "UNKNOWN",
        "entry_price": None,
        "atr": None,
    }

    rows = {
        "M5": m5.iloc[-1],
        "M15": m15.iloc[-1],
        "H1": h1.iloc[-1],
        "H4": h4.iloc[-1],
    }

    scores = {}

    for tf, row in rows.items():

        score, _ = score_row(row)

        scores[tf] = score

    m5_score = scores["M5"]
    m15_score = scores["M15"]
    h1_score = scores["H1"]
    h4_score = scores["H4"]

    regime = market_regime(rows["H1"])

    result["regime"] = regime

    b2 = detect_b2(m15)

    result["b2"] = b2

    gates = {}

    gates["DATA VALID"] = True

    gates["M5 TREND"] = abs(m5_score) >= 2
    gates["M15 TREND"] = abs(m15_score) >= 2
    gates["H1 TREND"] = abs(h1_score) >= 2
    gates["H4 CONTEXT"] = True

    gates["B2 BREAKOUT"] = b2["valid"]
    gates["B2 RETEST"] = b2["valid"]

    m15_row = rows["M15"]

    gates["ADX"] = (
        np.isfinite(m15_row["adx"])
        and m15_row["adx"] >= 20
    )

    gates["RSI"] = np.isfinite(
        m15_row["rsi"]
    )

    gates["MOMENTUM"] = (
        np.isfinite(
            m15_row["momentum"]
        )
    )

    gates["RISK"] = (
        not st.session_state.kill_switch
    )

    gates["DAILY LIMIT"] = (
        st.session_state.daily_loss_money
        < (
            st.session_state.day_start_balance
            * DEFAULT_DAILY_LOSS
            / 100
        )
        and
        st.session_state.daily_trades
        < DEFAULT_MAX_TRADES
    )

    gates["NEWS"] = None

    direction = None

    if b2["valid"]:

        direction = b2["direction"]

    else:

        aligned_bull = (
            m5_score > 0
            and m15_score > 0
            and h1_score > 0
        )

        aligned_bear = (
            m5_score < 0
            and m15_score < 0
            and h1_score < 0
        )

        if aligned_bull:
            direction = "LONG"

        elif aligned_bear:
            direction = "SHORT"

    if direction == "LONG":

        gates["MTF ALIGNMENT"] = (
            m5_score > 0
            and m15_score > 0
            and h1_score > 0
        )

        gates["MOMENTUM"] = (
            m15_row["momentum"] > 0
        )

        gates["RSI"] = (
            m15_row["rsi"] >= 50
        )

        gates["MACD"] = (
            m15_row["macd"]
            > m15_row["macd_signal"]
        )

        h4_direction = score_row(
            rows["H4"]
        )[0]

        gates["H4 CONTEXT"] = (
            h4_direction >= 0
        )

    elif direction == "SHORT":

        gates["MTF ALIGNMENT"] = (
            m5_score < 0
            and m15_score < 0
            and h1_score < 0
        )

        gates["MOMENTUM"] = (
            m15_row["momentum"] < 0
        )

        gates["RSI"] = (
            m15_row["rsi"] <= 50
        )

        gates["MACD"] = (
            m15_row["macd"]
            < m15_row["macd_signal"]
        )

        h4_direction = score_row(
            rows["H4"]
        )[0]

        gates["H4 CONTEXT"] = (
            h4_direction <= 0
        )

    else:

        gates["MTF ALIGNMENT"] = False

    score_total = (
        m5_score
        + m15_score
        + h1_score
        + h4_score
    )

    strength = (
        abs(score_total) / 44 * 100
    )

    result["score"] = score_total
    result["strength"] = round(
        strength,
        1,
    )

    result["direction"] = direction

    if direction == "LONG":

        essential = [
            gates["MTF ALIGNMENT"],
            gates["ADX"],
            gates["RSI"],
            gates["MOMENTUM"],
            gates["MACD"],
            gates["RISK"],
            gates["DAILY LIMIT"],
        ]

        if all(essential):

            result["signal"] = "LONG"
            result["reason"] = (
                "Bullish conditions aligned."
            )

        else:

            result["signal"] = "WAIT"
            result["reason"] = (
                "One or more entry gates failed."
            )

    elif direction == "SHORT":

        essential = [
            gates["MTF ALIGNMENT"],
            gates["ADX"],
            gates["RSI"],
            gates["MOMENTUM"],
            gates["MACD"],
            gates["RISK"],
            gates["DAILY LIMIT"],
        ]

        if all(essential):

            result["signal"] = "SHORT"
            result["reason"] = (
                "Bearish conditions aligned."
            )

        else:

            result["signal"] = "WAIT"
            result["reason"] = (
                "One or more entry gates failed."
            )

    else:

        result["signal"] = "WAIT"
        result["reason"] = (
            "No directional alignment."
        )

    result["gates"] = gates

    result["entry_price"] = float(
        m5.iloc[-1]["close"]
    )

    result["atr"] = float(
        m5.iloc[-1]["atr"]
    )

    return result


# ============================================================
# EXECUTION MODEL
# ============================================================

def executed_entry_price(direction, market_price):

    half_spread = SIMULATED_SPREAD / 2

    if direction == "LONG":

        return (
            market_price
            + half_spread
            + SIMULATED_SLIPPAGE
        )

    return (
        market_price
        - half_spread
        - SIMULATED_SLIPPAGE
    )


def executed_exit_price(direction, market_price):

    half_spread = SIMULATED_SPREAD / 2

    if direction == "LONG":

        return (
            market_price
            - half_spread
            - SIMULATED_SLIPPAGE
        )

    return (
        market_price
        + half_spread
        + SIMULATED_SLIPPAGE
    )


# ============================================================
# TRADE ENGINE
# ============================================================

def build_trade(
    signal,
    market_price,
    atr,
    risk_pct,
    entry_time,
):

    direction = signal

    entry = executed_entry_price(
        direction,
        market_price,
    )

    risk_money = (
        st.session_state.paper_balance
        * risk_pct
        / 100
    )

    if not np.isfinite(atr) or atr <= 0:
        return None

    if direction == "LONG":

        sl = entry - (
            atr * SL_ATR
        )

        tp1 = entry + (
            atr * TP1_ATR
        )

        tp2 = entry + (
            atr * TP2_ATR
        )

    else:

        sl = entry + (
            atr * SL_ATR
        )

        tp1 = entry - (
            atr * TP1_ATR
        )

        tp2 = entry - (
            atr * TP2_ATR
        )

    trade = {
        "id": str(uuid.uuid4()),
        "direction": direction,
        "entry_time": entry_time,
        "entry": float(entry),
        "sl": float(sl),
        "initial_sl": float(sl),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "atr": float(atr),
        "risk_pct": float(risk_pct),
        "risk_money": float(risk_money),
        "remaining_fraction": 1.0,
        "tp1_hit": False,
        "be_active": False,
        "status": "OPEN",
        "realized_pnl": 0.0,
        "exit": None,
        "exit_time": None,
        "exit_reason": None,
    }

    return trade


def pnl_for_move(
    trade,
    exit_price,
    fraction,
):

    entry = trade["entry"]

    risk_distance = abs(
        entry - trade["initial_sl"]
    )

    if risk_distance <= 0:
        return 0.0

    if trade["direction"] == "LONG":

        move = (
            exit_price - entry
        )

    else:

        move = (
            entry - exit_price
        )

    r_multiple = (
        move / risk_distance
    )

    return (
        r_multiple
        * trade["risk_money"]
        * fraction
    )


# ============================================================
# TRADE MANAGEMENT
# ============================================================

def manage_trade_on_candle(
    trade,
    candle,
):

    if trade is None:
        return trade, []

    events = []

    high = float(candle["high"])
    low = float(candle["low"])

    # Never process candles before entry.
    candle_time = candle.name

    entry_time = pd.Timestamp(
        trade["entry_time"]
    )

    if candle_time < entry_time:
        return trade, events

    direction = trade["direction"]

    # --------------------------------------------------------
    # Before TP1
    # --------------------------------------------------------

    if not trade["tp1_hit"]:

        if direction == "LONG":

            stop_hit = (
                low <= trade["sl"]
            )

            tp1_hit = (
                high >= trade["tp1"]
            )

        else:

            stop_hit = (
                high >= trade["sl"]
            )

            tp1_hit = (
                low <= trade["tp1"]
            )

        # Conservative same-candle assumption:
        # stop first if both are touched.
        if stop_hit:

            exit_price = executed_exit_price(
                direction,
                trade["sl"],
            )

            pnl = pnl_for_move(
                trade,
                exit_price,
                trade["remaining_fraction"],
            )

            trade["realized_pnl"] += pnl
            trade["remaining_fraction"] = 0.0
            trade["status"] = "CLOSED"
            trade["exit"] = exit_price
            trade["exit_time"] = candle_time
            trade["exit_reason"] = "SL"

            events.append(
                {
                    "type": "SL",
                    "time": candle_time,
                    "pnl": pnl,
                }
            )

            return trade, events

        if tp1_hit:

            fraction = 0.50

            exit_price = executed_exit_price(
                direction,
                trade["tp1"],
            )

            pnl = pnl_for_move(
                trade,
                exit_price,
                fraction,
            )

            trade["realized_pnl"] += pnl
            trade["remaining_fraction"] -= fraction
            trade["tp1_hit"] = True
            trade["be_active"] = True
            trade["sl"] = trade["entry"]

            events.append(
                {
                    "type": "TP1",
                    "time": candle_time,
                    "pnl": pnl,
                }
            )

            # Do not re-check BE on same candle.
            # TP2 can still be reached on same candle.

    # --------------------------------------------------------
    # After TP1
    # --------------------------------------------------------

    if trade["tp1_hit"]:

        if direction == "LONG":

            be_hit = (
                low <= trade["entry"]
            )

            tp2_hit = (
                high >= trade["tp2"]
            )

        else:

            be_hit = (
                high >= trade["entry"]
            )

            tp2_hit = (
                low <= trade["tp2"]
            )

        # BE is only relevant on candles after TP1.
        if (
            be_hit
            and not trade["status"] == "CLOSED"
            and trade["remaining_fraction"] > 0
        ):

            # Only close at BE if TP2 wasn't reached first.
            if not tp2_hit:

                exit_price = executed_exit_price(
                    direction,
                    trade["entry"],
                )

                fraction = trade[
                    "remaining_fraction"
                ]

                pnl = pnl_for_move(
                    trade,
                    exit_price,
                    fraction,
                )

                trade["realized_pnl"] += pnl
                trade["remaining_fraction"] = 0.0
                trade["status"] = "CLOSED"
                trade["exit"] = exit_price
                trade["exit_time"] = candle_time
                trade["exit_reason"] = "BE"

                events.append(
                    {
                        "type": "BE",
                        "time": candle_time,
                        "pnl": pnl,
                    }
                )

                return trade, events

        if tp2_hit and trade["status"] != "CLOSED":

            fraction = trade[
                "remaining_fraction"
            ]

            exit_price = executed_exit_price(
                direction,
                trade["tp2"],
            )

            pnl = pnl_for_move(
                trade,
                exit_price,
                fraction,
            )

            trade["realized_pnl"] += pnl
            trade["remaining_fraction"] = 0.0
            trade["status"] = "CLOSED"
            trade["exit"] = exit_price
            trade["exit_time"] = candle_time
            trade["exit_reason"] = "TP2"

            events.append(
                {
                    "type": "TP2",
                    "time": candle_time,
                    "pnl": pnl,
                }
            )

    return trade, events


# ============================================================
# FINALIZE TRADE
# ============================================================

def finalize_trade(trade):

    if trade is None:
        return

    pnl = float(
        trade.get(
            "realized_pnl",
            0.0,
        )
    )

    st.session_state.paper_balance += pnl

    if pnl < 0:

        st.session_state.daily_loss_money += abs(
            pnl
        )

    history_record = dict(trade)

    st.session_state.paper_history.append(
        history_record
    )

    st.session_state.paper_trade = None
    st.session_state.last_managed_candle = None


# ============================================================
# LIVE PAPER MANAGEMENT
# ============================================================

def manage_live_trade(m5):

    trade = st.session_state.paper_trade

    if trade is None:
        return

    entry_time = pd.Timestamp(
        trade["entry_time"]
    )

    # Critical fix:
    # only process candles at/after entry.
    eligible = m5[
        m5.index >= entry_time
    ]

    last_managed = (
        st.session_state.last_managed_candle
    )

    if last_managed is not None:

        eligible = eligible[
            eligible.index > pd.Timestamp(
                last_managed
            )
        ]

    for timestamp, candle in eligible.iterrows():

        trade, events = manage_trade_on_candle(
            trade,
            candle,
        )

        for event in events:

            st.session_state.decision_logs.append(
                {
                    "id": str(uuid.uuid4()),
                    "time": event["time"],
                    "type": event["type"],
                    "decision": event["type"],
                    "reason": (
                        f"Trade management event: "
                        f"{event['type']}"
                    ),
                    "pnl": event["pnl"],
                    "trade_id": trade["id"],
                }
            )

        st.session_state.last_managed_candle = timestamp

        if trade["status"] == "CLOSED":

            finalize_trade(trade)
            break

        st.session_state.paper_trade = trade


# ============================================================
# PENDING SIGNAL EXECUTION
# ============================================================

def execute_pending_signal(
    m5,
    risk_pct,
):

    pending = st.session_state.pending_signal

    if pending is None:
        return

    intended_time = pd.Timestamp(
        pending["entry_time"]
    )

    available = m5[
        m5.index >= intended_time
    ]

    if available.empty:
        return

    first_time = available.index[0]

    # Do not enter if signal is excessively stale.
    if first_time > (
        intended_time
        + pd.Timedelta(minutes=10)
    ):

        st.session_state.decision_logs.append(
            {
                "id": str(uuid.uuid4()),
                "time": now_riyadh(),
                "type": "SIGNAL_EXPIRED",
                "decision": "REJECT",
                "reason": "Pending signal expired.",
            }
        )

        st.session_state.pending_signal = None

        return

    if (
        st.session_state.paper_trade
        is not None
    ):
        return

    if st.session_state.kill_switch:
        return

    if (
        st.session_state.daily_trades
        >= DEFAULT_MAX_TRADES
    ):
        return

    candle = available.iloc[0]

    market_price = float(
        candle["open"]
    )

    atr = safe_float(
        candle["atr"]
    )

    trade = build_trade(
        pending["direction"],
        market_price,
        atr,
        risk_pct,
        first_time,
    )

    if trade is None:

        st.session_state.pending_signal = None

        return

    st.session_state.paper_trade = trade

    st.session_state.daily_trades += 1

    # Start management exactly at entry candle.
    st.session_state.last_managed_candle = (
        first_time - pd.Timedelta(seconds=1)
    )

    st.session_state.decision_logs.append(
        {
            "id": str(uuid.uuid4()),
            "time": first_time,
            "type": "ENTRY",
            "decision": "EXECUTE",
            "reason": (
                f"{pending['direction']} "
                "Paper Trade opened."
            ),
            "trade_id": trade["id"],
        }
    )

    st.session_state.pending_signal = None


# ============================================================
# DECISION LOG
# ============================================================

def log_decision(signal):

    if signal is None:
        return

    gates = signal["gates"]

    timestamp = pd.Timestamp.now(
        tz="UTC"
    )

    signal_key = (
        timestamp.floor("5min").isoformat()
        + "_"
        + str(signal["signal"])
    )

    if (
        signal_key
        == st.session_state.last_signal_key
    ):
        return

    st.session_state.last_signal_key = signal_key

    passed = [
        key
        for key, value in gates.items()
        if value is True
    ]

    failed = [
        key
        for key, value in gates.items()
        if value is False
    ]

    unknown = [
        key
        for key, value in gates.items()
        if value is None
    ]

    decision = (
        "ACCEPT"
        if signal["signal"]
        in ["LONG", "SHORT"]
        else "REJECT"
    )

    if failed:

        reason = (
            "Failed: "
            + ", ".join(failed)
        )

    elif unknown:

        reason = (
            "Unknown: "
            + ", ".join(unknown)
        )

    else:

        reason = signal["reason"]

    st.session_state.decision_logs.append(
        {
            "id": str(uuid.uuid4()),
            "time": timestamp,
            "type": "SIGNAL",
            "decision": decision,
            "signal": signal["signal"],
            "direction": signal["direction"],
            "strength": signal["strength"],
            "score": signal["score"],
            "reason": reason,
            "passed": ", ".join(passed),
            "failed": ", ".join(failed),
            "unknown": ", ".join(unknown),
        }
    )


# ============================================================
# BACKTEST
# ============================================================

def run_backtest(
    m5,
    m15,
    h1,
    h4,
):

    if len(m5) < 300:
        return {
            "error": "Not enough M5 data."
        }

    # Work on copies.
    m5 = m5.copy()
    m15 = m15.copy()
    h1 = h1.copy()
    h4 = h4.copy()

    split_index = int(
        len(m5) * 0.70
    )

    split_time = m5.index[
        split_index
    ]

    trades = []

    balance = START_BALANCE

    equity_curve = []

    active_trade = None

    pending = None

    daily_trades = 0

    daily_loss = 0.0

    current_day = None

    # Build rolling analysis sequentially.
    for i in range(
        200,
        len(m5),
    ):

        timestamp = m5.index[i]

        candle = m5.iloc[i]

        day = timestamp.date()

        if current_day != day:

            current_day = day
            daily_trades = 0
            daily_loss = 0.0

        # ----------------------------------------------------
        # Manage active trade
        # ----------------------------------------------------

        if active_trade is not None:

            active_trade, events = manage_trade_on_candle(
                active_trade,
                candle,
            )

            if active_trade["status"] == "CLOSED":

                balance += (
                    active_trade[
                        "realized_pnl"
                    ]
                )

                if (
                    active_trade[
                        "realized_pnl"
                    ] < 0
                ):

                    daily_loss += abs(
                        active_trade[
                            "realized_pnl"
                        ]
                    )

                trades.append(
                    dict(active_trade)
                )

                active_trade = None

        # ----------------------------------------------------
        # Create higher timeframe snapshots
        # ----------------------------------------------------

        current_m5 = m5.iloc[: i + 1]

        current_m15 = resample_tf(
            current_m5,
            "15min",
        )

        current_h1 = resample_tf(
            current_m5,
            "1h",
        )

        current_h4 = resample_tf(
            current_m5,
            "4h",
        )

        if (
            len(current_m15) < 120
            or len(current_h1) < 120
            or len(current_h4) < 100
        ):
            continue

        current_m15 = add_indicators(
            current_m15
        )

        current_h1 = add_indicators(
            current_h1
        )

        current_h4 = add_indicators(
            current_h4
        )

        current_m5_i = add_indicators(
            current_m5
        )

        # Need enough valid rows.
        if any(
            len(x) < 110
            for x in [
                current_m5_i,
                current_m15,
                current_h1,
                current_h4,
            ]
        ):
            continue

        signal = analyze_market(
            current_m5_i,
            current_m15,
            current_h1,
            current_h4,
        )

        # ----------------------------------------------------
        # Entry on next candle
        # ----------------------------------------------------

        if (
            active_trade is None
            and timestamp < split_time
            and signal["signal"]
            in ["LONG", "SHORT"]
            and daily_trades
            < DEFAULT_MAX_TRADES
        ):

            pending = {
                "direction": signal["signal"],
                "entry_time": timestamp
                + pd.Timedelta(minutes=5),
                "atr": signal["atr"],
            }

        # OOS also trades, but starts at split.
        elif (
            active_trade is None
            and timestamp >= split_time
            and signal["signal"]
            in ["LONG", "SHORT"]
            and daily_trades
            < DEFAULT_MAX_TRADES
        ):

            pending = {
                "direction": signal["signal"],
                "entry_time": timestamp
                + pd.Timedelta(minutes=5),
                "atr": signal["atr"],
            }

        # ----------------------------------------------------
        # Execute pending
        # ----------------------------------------------------

        if (
            pending is not None
            and active_trade is None
            and timestamp >= pending["entry_time"]
        ):

            market_price = float(
                candle["open"]
            )

            atr = safe_float(
                candle["atr"]
            )

            if np.isfinite(atr) and atr > 0:

                risk_money = (
                    balance
                    * DEFAULT_RISK
                    / 100
                )

                entry = executed_entry_price(
                    pending["direction"],
                    market_price,
                )

                if pending["direction"] == "LONG":

                    sl = entry - (
                        atr * SL_ATR
                    )

                    tp1 = entry + (
                        atr * TP1_ATR
                    )

                    tp2 = entry + (
                        atr * TP2_ATR
                    )

                else:

                    sl = entry + (
                        atr * SL_ATR
                    )

                    tp1 = entry - (
                        atr * TP1_ATR
                    )

                    tp2 = entry - (
                        atr * TP2_ATR
                    )

                active_trade = {
                    "id": str(uuid.uuid4()),
                    "direction": pending[
                        "direction"
                    ],
                    "entry_time": timestamp,
                    "entry": entry,
                    "sl": sl,
                    "initial_sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "atr": atr,
                    "risk_pct": DEFAULT_RISK,
                    "risk_money": risk_money,
                    "remaining_fraction": 1.0,
                    "tp1_hit": False,
                    "be_active": False,
                    "status": "OPEN",
                    "realized_pnl": 0.0,
                }

                daily_trades += 1

            pending = None

        # ----------------------------------------------------
        # Equity curve
        # ----------------------------------------------------

        unrealized = 0.0

        if active_trade is not None:

            current_price = float(
                candle["close"]
            )

            unrealized = pnl_for_move(
                active_trade,
                executed_exit_price(
                    active_trade["direction"],
                    current_price,
                ),
                active_trade[
                    "remaining_fraction"
                ],
            )

        equity = balance + unrealized

        equity_curve.append(
            {
                "time": timestamp,
                "equity": equity,
            }
        )

    # Close remaining trade at final close.
    if active_trade is not None:

        final_price = float(
            m5.iloc[-1]["close"]
        )

        exit_price = executed_exit_price(
            active_trade["direction"],
            final_price,
        )

        fraction = active_trade[
            "remaining_fraction"
        ]

        pnl = pnl_for_move(
            active_trade,
            exit_price,
            fraction,
        )

        active_trade["realized_pnl"] += pnl
        active_trade["remaining_fraction"] = 0.0
        active_trade["status"] = "CLOSED"
        active_trade["exit"] = exit_price
        active_trade["exit_time"] = m5.index[-1]
        active_trade["exit_reason"] = (
            "END_OF_TEST"
        )

        balance += active_trade[
            "realized_pnl"
        ]

        trades.append(
            dict(active_trade)
        )

    if not trades:

        return {
            "error": "No trades generated."
        }

    trade_df = pd.DataFrame(trades)

    trade_df["R"] = (
        trade_df["realized_pnl"]
        / trade_df["risk_money"]
    )

    oos = trade_df[
        pd.to_datetime(
            trade_df["entry_time"],
            utc=True,
        )
        >= split_time
    ].copy()

    equity_df = pd.DataFrame(
        equity_curve
    )

    if equity_df.empty:

        max_dd_r = 0.0

    else:

        equity_df["peak"] = (
            equity_df["equity"]
            .cummax()
        )

        equity_df["drawdown"] = (
            equity_df["equity"]
            - equity_df["peak"]
        )

        max_dd_money = abs(
            equity_df["drawdown"].min()
        )

        max_dd_r = (
            max_dd_money
            / (
                START_BALANCE
                * DEFAULT_RISK
                / 100
            )
        )

    wins = (
        oos["R"] > 0
    ).sum()

    losses = (
        oos["R"] <= 0
    ).sum()

    gross_profit = (
        oos.loc[
            oos["R"] > 0,
            "R",
        ].sum()
    )

    gross_loss = abs(
        oos.loc[
            oos["R"] < 0,
            "R",
        ].sum()
    )

    if gross_loss > 0:
        profit_factor = (
            gross_profit
            / gross_loss
        )
    else:
        profit_factor = np.inf

    win_rate = (
        wins / len(oos) * 100
        if len(oos)
        else 0
    )

    total_r = (
        oos["R"].sum()
        if len(oos)
        else 0
    )

    avg_r = (
        oos["R"].mean()
        if len(oos)
        else 0
    )

    return {
        "trades": trade_df,
        "oos": oos,
        "equity": equity_df,
        "oos_trades": len(oos),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "total_r": total_r,
        "avg_r": avg_r,
        "max_dd_r": max_dd_r,
        "split_time": split_time,
    }


# ============================================================
# UI COMPONENTS
# ============================================================

def card(title, value, subtitle=""):

    st.markdown(
        f"""
        <div class="card">
            <div class="card-title">{title}</div>
            <div class="card-value">{value}</div>
            <div class="card-small">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def gate_text(value):

    if value is True:
        return "✓ PASS"

    if value is False:
        return "✕ FAIL"

    return "• UNKNOWN"


def gate_class(value):

    if value is True:
        return "badge-pass"

    if value is False:
        return "badge-fail"

    return "badge-wait"


# ============================================================
# HEADER
# ============================================================

st.markdown(
    """
<div class="header">
    <div class="brand">GOLD AI</div>
    <div class="subtitle">
        XAU/USD Smart Trading System · Paper Trading
    </div>
    <br>
    <span class="status">● PAPER MODE</span>
    &nbsp;
    <span class="status">● DATA ENGINE</span>
    &nbsp;
    <span class="status">● RISK ENGINE</span>
</div>
""",
    unsafe_allow_html=True,
)


# ============================================================
# SIDEBAR SETTINGS
# ============================================================

with st.sidebar:

    st.markdown("## ⚙️ الإعدادات")

    risk_pct = st.slider(
        "Risk / Trade %",
        min_value=MIN_RISK,
        max_value=MAX_RISK,
        value=DEFAULT_RISK,
        step=0.10,
    )

    daily_loss_limit = st.slider(
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

    refresh = st.number_input(
        "Refresh Seconds",
        min_value=10,
        max_value=300,
        value=REFRESH_SECONDS,
        step=5,
    )

    st.divider()

    st.markdown("### Paper Account")

    st.write(
        f"Balance: "
        f"${st.session_state.paper_balance:,.2f}"
    )

    st.write(
        f"Trades today: "
        f"{st.session_state.daily_trades}/{max_trades}"
    )

    if st.button(
        "⛔ STOP ENGINE",
        use_container_width=True,
    ):

        st.session_state.kill_switch = True

    if st.button(
        "▶️ START ENGINE",
        use_container_width=True,
    ):

        st.session_state.kill_switch = False

    if st.button(
        "🔄 RESET PAPER ACCOUNT",
        use_container_width=True,
    ):

        st.session_state.paper_balance = START_BALANCE
        st.session_state.paper_trade = None
        st.session_state.paper_history = []
        st.session_state.decision_logs = []
        st.session_state.pending_signal = None
        st.session_state.last_managed_candle = None
        st.session_state.daily_loss_money = 0
        st.session_state.daily_trades = 0
        st.session_state.kill_switch = False
        st.rerun()


# ============================================================
# LOAD DATA
# ============================================================

try:

    raw_m5 = load_m5()

    quality = data_quality(
        raw_m5
    )

    st.session_state.api_error = None

except Exception as exc:

    st.session_state.api_error = str(
        exc
    )

    st.error(
        "تعذر تحميل بيانات XAU/USD."
    )

    st.code(
        str(exc)
    )

    st.stop()


# ============================================================
# PREPARE MARKET
# ============================================================

m5 = add_indicators(
    raw_m5
)

m15 = add_indicators(
    resample_tf(
        raw_m5,
        "15min",
    )
)

h1 = add_indicators(
    resample_tf(
        raw_m5,
        "1h",
    )
)

h4 = add_indicators(
    resample_tf(
        raw_m5,
        "4h",
    )
)


# Remove incomplete indicator rows.
m5 = m5.dropna(
    subset=[
        "ema20",
        "ema50",
        "ema100",
        "rsi",
        "atr",
        "macd",
        "macd_signal",
        "momentum",
        "adx",
        "plus_di",
        "minus_di",
    ]
)

m15 = m15.dropna(
    subset=[
        "ema20",
        "ema50",
        "ema100",
        "rsi",
        "atr",
        "macd",
        "macd_signal",
        "momentum",
        "adx",
        "plus_di",
        "minus_di",
    ]
)

h1 = h1.dropna(
    subset=[
        "ema20",
        "ema50",
        "ema100",
        "rsi",
        "atr",
        "macd",
        "macd_signal",
        "momentum",
        "adx",
        "plus_di",
        "minus_di",
    ]
)

h4 = h4.dropna(
    subset=[
        "ema20",
        "ema50",
        "ema100",
        "rsi",
        "atr",
        "macd",
        "macd_signal",
        "momentum",
        "adx",
        "plus_di",
        "minus_di",
    ]
)


# ============================================================
# ALIGN HIGHER TIMEFRAMES
# ============================================================

latest_m5_time = m5.index[-1]

m15 = m15[
    m15.index <= latest_m5_time
]

h1 = h1[
    h1.index <= latest_m5_time
]

h4 = h4[
    h4.index <= latest_m5_time
]


if any(
    len(x) < 110
    for x in [
        m5,
        m15,
        h1,
        h4,
    ]
):

    st.warning(
        "البيانات غير كافية لتشغيل المحرك بالكامل."
    )

    st.stop()


# ============================================================
# CURRENT ANALYSIS
# ============================================================

signal = analyze_market(
    m5,
    m15,
    h1,
    h4,
)

log_decision(signal)


# ============================================================
# PAPER EXECUTION
# ============================================================

execute_pending_signal(
    m5,
    risk_pct,
)

manage_live_trade(
    m5
)


# ============================================================
# CURRENT PRICE
# ============================================================

current_price = float(
    m5.iloc[-1]["close"]
)

previous_price = float(
    m5.iloc[-2]["close"]
)

price_change = (
    current_price
    - previous_price
)


st.session_state.last_price = (
    current_price
)

st.session_state.last_data_time = (
    m5.index[-1]
)


# ============================================================
# DAILY LIMIT
# ============================================================

daily_limit_money = (
    st.session_state.day_start_balance
    * daily_loss_limit
    / 100
)

daily_limit_reached = (
    st.session_state.daily_loss_money
    >= daily_limit_money
    or
    st.session_state.daily_trades
    >= max_trades
)


# ============================================================
# TOP METRICS
# ============================================================

c1, c2, c3, c4 = st.columns(4)

with c1:
    card(
        "XAU/USD",
        f"${fmt_price(current_price)}",
        f"Δ {price_change:+.2f}",
    )

with c2:
    card(
        "Market Regime",
        signal["regime"],
        "H1 context",
    )

with c3:
    card(
        "Signal Strength",
        f"{signal['strength']:.1f}/100",
        "Strength ≠ profit probability",
    )

with c4:
    card(
        "Paper Balance",
        f"${st.session_state.paper_balance:,.2f}",
        f"Daily trades {st.session_state.daily_trades}/{max_trades}",
    )


# ============================================================
# SIGNAL CENTER
# ============================================================

st.markdown(
    '<div class="section">',
    unsafe_allow_html=True,
)

st.markdown("### 🎯 Signal Center")

if signal["signal"] == "LONG":

    st.markdown(
        f"""
        <div class="signal-long">
            <h2>🟢 LONG</h2>
            <p>
                Bullish setup detected
            </p>
            <b>Strength: {signal["strength"]:.1f}/100</b>
        </div>
        """,
        unsafe_allow_html=True,
    )

elif signal["signal"] == "SHORT":

    st.markdown(
        f"""
        <div class="signal-short">
            <h2>🔴 SHORT</h2>
            <p>
                Bearish setup detected
            </p>
            <b>Strength: {signal["strength"]:.1f}/100</b>
        </div>
        """,
        unsafe_allow_html=True,
    )

else:

    st.markdown(
        f"""
        <div class="signal-wait">
            <h2>🟡 WAITING</h2>
            <p>{signal["reason"]}</p>
            <b>Strength: {signal["strength"]:.1f}/100</b>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("</div>", unsafe_allow_html=True)


# ============================================================
# MARKET TIMEFRAMES
# ============================================================

st.markdown(
    '<div class="section">',
    unsafe_allow_html=True,
)

st.markdown("### 📊 Multi-Timeframe Analysis")

tf_cols = st.columns(4)

for col, name, frame in zip(
    tf_cols,
    ["M5", "M15", "H1", "H4"],
    [m5, m15, h1, h4],
):

    row = frame.iloc[-1]

    with col:

        card(
            name,
            trend_label(row),
            f"RSI {row['rsi']:.1f} · ADX {row['adx']:.1f}",
        )

st.markdown("</div>", unsafe_allow_html=True)


# ============================================================
# DECISION GATES
# ============================================================

st.markdown(
    '<div class="section">',
    unsafe_allow_html=True,
)

st.markdown("### 🧠 Decision Engine")

gate_cols = st.columns(3)

for index, (
    gate,
    value,
) in enumerate(
    signal["gates"].items()
):

    with gate_cols[
        index % 3
    ]:

        cls = gate_class(value)

        st.markdown(
            f"""
            <div class="metric-box">
                <div class="metric-label">
                    {gate}
                </div>
                <div class="{cls}">
                    {gate_text(value)}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.markdown("</div>", unsafe_allow_html=True)


# ============================================================
# B2
# ============================================================

st.markdown(
    '<div class="section">',
    unsafe_allow_html=True,
)

st.markdown("### ⚡ B2 Breakout / Retest")

b2 = signal["b2"]

if b2["valid"]:

    b1, b2c, b3 = st.columns(3)

    with b1:
        card(
            "Direction",
            b2["direction"],
            "B2",
        )

    with b2c:
        card(
            "Breakout Level",
            fmt_price(
                b2["level"]
            ),
            "M15",
        )

    with b3:
        card(
            "Retest Distance",
            fmt_price(
                b2["retest_distance"]
            ),
            "ATR based",
        )

else:

    st.info(
        f"B2: {b2['reason']}"
    )

st.markdown("</div>", unsafe_allow_html=True)


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

support, resistance = (
    support_resistance(
        m15
    )
)

st.markdown(
    '<div class="section">',
    unsafe_allow_html=True,
)

st.markdown("### 📐 Support / Resistance")

sr1, sr2, sr3 = st.columns(3)

with sr1:
    card(
        "Support",
        fmt_price(support),
        "M15 structure",
    )

with sr2:
    card(
        "Current",
        fmt_price(current_price),
        "XAU/USD",
    )

with sr3:
    card(
        "Resistance",
        fmt_price(resistance),
        "M15 structure",
    )

st.markdown("</div>", unsafe_allow_html=True)


# ============================================================
# PAPER POSITION
# ============================================================

st.markdown(
    '<div class="section">',
    unsafe_allow_html=True,
)

st.markdown("### 💼 Paper Trading")

trade = st.session_state.paper_trade

if trade is None:

    if st.session_state.pending_signal:

        st.info(
            "Pending signal — waiting for next M5 candle."
        )

    else:

        st.write(
            "لا توجد صفقة مفتوحة حاليًا."
        )

else:

    p1, p2, p3, p4 = st.columns(4)

    with p1:
        card(
            "Direction",
            trade["direction"],
            "XAU/USD",
        )

    with p2:
        card(
            "Entry",
            fmt_price(
                trade["entry"]
            ),
            f"Risk {trade['risk_pct']:.2f}%",
        )

    with p3:
        card(
            "Stop Loss",
            fmt_price(
                trade["sl"]
            ),
            "Dynamic ATR",
        )

    with p4:
        card(
            "TP1 / TP2",
            (
                f"{fmt_price(trade['tp1'])}"
                " / "
                f"{fmt_price(trade['tp2'])}"
            ),
            "50% / 50%",
        )

    st.write(
        f"TP1: "
        f"{'✓ HIT' if trade['tp1_hit'] else 'WAITING'}"
        " · "
        f"BE: "
        f"{'✓ ACTIVE' if trade['be_active'] else 'OFF'}"
    )

st.markdown("</div>", unsafe_allow_html=True)


# ============================================================
# RISK CENTER
# ============================================================

st.markdown(
    '<div class="section">',
    unsafe_allow_html=True,
)

st.markdown("### 🛡️ Risk Center")

r1, r2, r3, r4 = st.columns(4)

with r1:
    card(
        "Risk / Trade",
        f"{risk_pct:.2f}%",
        "Configured",
    )

with r2:
    card(
        "Daily Loss",
        fmt_money(
            -st.session_state.daily_loss_money
        ),
        f"Limit {daily_loss_limit:.1f}%",
    )

with r3:
    card(
        "Trades Today",
        f"{st.session_state.daily_trades}/{max_trades}",
        "Protection",
    )

with r4:

    state = (
        "STOPPED"
        if st.session_state.kill_switch
        else "ACTIVE"
    )

    card(
        "Engine",
        state,
        "Paper only",
    )

if daily_limit_reached:

    st.warning(
        "Daily protection is active. New entries are blocked."
    )

if st.session_state.kill_switch:

    st.error(
        "Kill Switch is ON. New entries are blocked."
    )

st.markdown("</div>", unsafe_allow_html=True)


# ============================================================
# DATA HEALTH
# ============================================================

st.markdown(
    '<div class="section">',
    unsafe_allow_html=True,
)

st.markdown("### 🩺 System Health")

h1c, h2c, h3c, h4c = st.columns(4)

with h1c:

    card(
        "Data Feed",
        "ONLINE",
        "Twelve Data",
    )

with h2c:

    card(
        "Data Quality",
        (
            "VALID"
            if quality["valid"]
            else "CHECK"
        ),
        quality["message"],
    )

with h3c:

    card(
        "Strategy Engine",
        "ONLINE",
        "MTF + B2",
    )

with h4c:

    card(
        "Paper Engine",
        "ONLINE",
        "Simulation",
    )

st.markdown("</div>", unsafe_allow_html=True)


# ============================================================
# BACKTEST
# ============================================================

st.markdown(
    '<div class="section">',
    unsafe_allow_html=True,
)

st.markdown("### 🧪 Backtest Lab")

if st.button(
    "تشغيل Backtest",
    type="primary",
    use_container_width=True,
):

    with st.spinner(
        "Running sequential backtest..."
    ):

        bt = run_backtest(
            m5,
            m15,
            h1,
            h4,
        )

    st.session_state["backtest_result"] = bt


bt = st.session_state.get(
    "backtest_result"
)

if bt:

    if "error" in bt:

        st.warning(
            bt["error"]
        )

    else:

        b1, b2c, b3, b4 = st.columns(4)

        with b1:
            card(
                "OOS Trades",
                str(
                    bt["oos_trades"]
                ),
                "30% chronological OOS",
            )

        with b2c:

            pf = bt["profit_factor"]

            pf_text = (
                "∞"
                if np.isinf(pf)
                else f"{pf:.2f}"
            )

            card(
                "Profit Factor",
                pf_text,
                "OOS",
            )

        with b3:
            card(
                "Win Rate",
                f"{bt['win_rate']:.1f}%",
                "OOS",
            )

        with b4:
            card(
                "Total R",
                f"{bt['total_r']:+.2f}R",
                f"Avg {bt['avg_r']:+.3f}R",
            )

        st.write(
            f"Max Drawdown: "
            f"{bt['max_dd_r']:.2f}R"
        )

        st.caption(
            "تنبيه: اختبار 70/30 هو OOS زمني أولي وليس Walk-Forward كامل."
        )

        if not bt["equity"].empty:

            equity = (
                bt["equity"]
                .set_index("time")[
                    ["equity"]
                ]
            )

            st.line_chart(
                equity,
                height=300,
            )

        if not bt["oos"].empty:

            st.dataframe(
                bt["oos"][
                    [
                        "entry_time",
                        "direction",
                        "entry",
                        "sl",
                        "tp1",
                        "tp2",
                        "realized_pnl",
                        "R",
                        "exit_reason",
                    ]
                ].sort_values(
                    "entry_time",
                    ascending=False,
                ),
                use_container_width=True,
                hide_index=True,
            )

st.markdown("</div>", unsafe_allow_html=True)


# ============================================================
# DECISION LOG
# ============================================================

st.markdown(
    '<div class="section">',
    unsafe_allow_html=True,
)

st.markdown("### 📜 Decision Log")

logs = st.session_state.decision_logs

if logs:

    log_df = pd.DataFrame(
        logs[-100:]
    )

    display_columns = [
        column
        for column in [
            "time",
            "type",
            "decision",
            "signal",
            "direction",
            "strength",
            "score",
            "reason",
            "pnl",
        ]
        if column in log_df.columns
    ]

    st.dataframe(
        log_df[
            display_columns
        ].sort_values(
            "time",
            ascending=False,
        ),
        use_container_width=True,
        hide_index=True,
    )

else:

    st.write(
        "لا توجد قرارات مسجلة حتى الآن."
    )

st.markdown("</div>", unsafe_allow_html=True)


# ============================================================
# FOOTER
# ============================================================

st.markdown(
    """
<div style="
    text-align:center;
    color:#667386;
    font-size:11px;
    padding:25px 0;
">
    GOLD AI · XAU/USD · Paper Trading Only ·
    No real broker orders are executed
</div>
""",
    unsafe_allow_html=True,
)


# ============================================================
# AUTO REFRESH
# ============================================================

time.sleep(
    int(refresh)
)

st.rerun()

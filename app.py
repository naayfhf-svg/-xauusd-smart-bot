import streamlit as st
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timezone

# =========================================================
# CONFIG
# =========================================================

st.set_page_config(
    page_title="بوت الذهب XAU/USD",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

SYMBOL = "XAU/USD"
API_URL = "https://api.twelvedata.com/time_series"
START_BALANCE = 10000.0

# =========================================================
# PROFESSIONAL TRADING UI
# =========================================================

st.markdown("""
<style>
html, body, [class*="css"] {
    font-family: Arial, sans-serif;
}

.stApp {
    background: #f4f6f8;
    color: #111827;
}

.block-container {
    max-width: 1400px;
    padding-top: 1rem;
    padding-bottom: 3rem;
}

h1, h2, h3, h4 {
    color: #111827 !important;
}

.main-title {
    font-size: 30px;
    font-weight: 800;
    color: #111827;
    margin-bottom: 4px;
}

.sub-title {
    color: #667085;
    font-size: 14px;
    margin-bottom: 18px;
}

.card {
    background: #ffffff;
    border: 1px solid #d9dee5;
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 12px;
    box-shadow: 0 1px 2px rgba(0,0,0,.04);
}

.metric-title {
    color: #667085;
    font-size: 13px;
    margin-bottom: 6px;
}

.metric-value {
    color: #111827;
    font-size: 25px;
    font-weight: 800;
}

.buy {
    color: #087443 !important;
    font-weight: 800;
}

.sell {
    color: #c62828 !important;
    font-weight: 800;
}

.neutral {
    color: #475467 !important;
    font-weight: 800;
}

.blue {
    color: #175cd3 !important;
    font-weight: 800;
}

.small {
    color: #667085;
    font-size: 12px;
}

.signal-box {
    border: 2px solid #d9dee5;
    background: #fff;
    border-radius: 12px;
    padding: 20px;
    text-align: center;
}

.signal-buy {
    border-color: #12b76a;
}

.signal-sell {
    border-color: #f04438;
}

.signal-neutral {
    border-color: #98a2b3;
}

.trade-open {
    background: #ecfdf3;
    border: 1px solid #12b76a;
    border-radius: 10px;
    padding: 14px;
}

.trade-closed {
    background: #fff;
    border: 1px solid #d9dee5;
    border-radius: 10px;
    padding: 14px;
}

.warning {
    background: #fffaeb;
    border: 1px solid #fedf89;
    border-radius: 8px;
    padding: 10px;
    color: #92400e;
}

.danger {
    background: #fef3f2;
    border: 1px solid #fecdca;
    border-radius: 8px;
    padding: 10px;
    color: #b42318;
}

.success {
    background: #ecfdf3;
    border: 1px solid #abefc6;
    border-radius: 8px;
    padding: 10px;
    color: #067647;
}

div[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid #d9dee5;
    padding: 10px;
    border-radius: 8px;
}

div[data-testid="stMetricLabel"] {
    color: #667085;
}

div[data-testid="stMetricValue"] {
    color: #111827;
}

button {
    border-radius: 7px !important;
}

[data-testid="stDataFrame"] {
    border: 1px solid #d9dee5;
}

footer {
    visibility: hidden;
}
</style>
""", unsafe_allow_html=True)

# =========================================================
# SESSION STATE
# =========================================================

defaults = {
    "paper_balance": START_BALANCE,
    "paper_trade": None,
    "paper_history": [],
    "logs": [],
    "kill_switch": False,
    "last_processed_signal_time": None,
    "last_live_price": None,
    "last_refresh": None,
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

# =========================================================
# HELPERS
# =========================================================

def now_utc():
    return datetime.now(timezone.utc)


def add_log(message, level="INFO"):
    st.session_state.logs.insert(
        0,
        {
            "time": now_utc().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "level": level,
            "message": message,
        },
    )

    st.session_state.logs = st.session_state.logs[:300]


def fmt(value, digits=2):
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):,.{digits}f}"


def trend_label(score):
    if score >= 5:
        return "صاعد"
    if score <= -5:
        return "هابط"
    return "محايد"


def trend_class(score):
    if score >= 5:
        return "buy"
    if score <= -5:
        return "sell"
    return "neutral"


# =========================================================
# TWELVE DATA
# =========================================================

def get_api_key():
    try:
        return st.secrets["TWELVE_DATA_API_KEY"]
    except Exception:
        return None


@st.cache_data(ttl=50, show_spinner=False)
def get_twelve_data(interval="5min", outputsize=5000):
    api_key = get_api_key()

    if not api_key:
        raise RuntimeError(
            "لم يتم العثور على TWELVE_DATA_API_KEY في Streamlit Secrets."
        )

    params = {
        "symbol": SYMBOL,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": api_key,
        "timezone": "UTC",
        "format": "JSON",
    }

    response = requests.get(
        API_URL,
        params=params,
        timeout=20,
    )

    response.raise_for_status()

    data = response.json()

    if "status" in data and data["status"] == "error":
        raise RuntimeError(data.get("message", "Twelve Data error"))

    values = data.get("values")

    if not values:
        raise RuntimeError("لم تصل بيانات XAU/USD.")

    df = pd.DataFrame(values)

    if "datetime" not in df.columns:
        raise RuntimeError("بيانات Twelve Data غير صحيحة.")

    df["datetime"] = pd.to_datetime(
        df["datetime"],
        utc=True,
        errors="coerce",
    )

    df = df.dropna(subset=["datetime"])

    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col],
                errors="coerce",
            )

    if "volume" not in df.columns:
        df["volume"] = 0.0

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    )

    df = df.sort_values("datetime")
    df = df.set_index("datetime")

    return df


# =========================================================
# COMPLETED M5
# =========================================================

def get_completed_m5(df):
    if df is None or df.empty:
        return df

    out = df.copy()

    now = pd.Timestamp.now(tz="UTC")

    current_bucket = now.floor("5min")

    out = out[out.index < current_bucket]

    return out


# =========================================================
# RESAMPLING
# =========================================================

def resample_ohlcv(df, rule):
    if df is None or df.empty:
        return pd.DataFrame()

    out = (
        df.resample(
            rule,
            label="right",
            closed="left",
            origin="start_day",
        )
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna(subset=["open", "high", "low", "close"])
    )

    return out


def latest_before(df, timestamp):
    if df is None or df.empty:
        return None

    valid = df.loc[df.index <= timestamp]

    if valid.empty:
        return None

    return valid.iloc[-1]


# =========================================================
# INDICATORS
# =========================================================

def ema(series, period):
    return series.ewm(
        span=period,
        adjust=False,
        min_periods=period,
    ).mean()


def rsi(series, period=14):
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

    return 100 - (100 / (1 + rs))


def atr(df, period=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()


def macd(series):
    fast = ema(series, 12)
    slow = ema(series, 26)

    line = fast - slow
    signal = line.ewm(
        span=9,
        adjust=False,
        min_periods=9,
    ).mean()

    hist = line - signal

    return line, signal, hist


def adx(df, period=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where(
            (up_move > down_move) & (up_move > 0),
            up_move,
            0,
        ),
        index=df.index,
    )

    minus_dm = pd.Series(
        np.where(
            (down_move > up_move) & (down_move > 0),
            down_move,
            0,
        ),
        index=df.index,
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

    atr_w = tr.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    plus_di = (
        100
        * plus_dm.ewm(
            alpha=1 / period,
            adjust=False,
            min_periods=period,
        ).mean()
        / atr_w
    )

    minus_di = (
        100
        * minus_dm.ewm(
            alpha=1 / period,
            adjust=False,
            min_periods=period,
        ).mean()
        / atr_w
    )

    dx = (
        100
        * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0, np.nan)
    )

    adx_value = dx.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    return adx_value, plus_di, minus_di


def add_indicators(df):
    df = df.copy()

    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["ema100"] = ema(df["close"], 100)

    df["rsi"] = rsi(df["close"], 14)

    (
        df["macd"],
        df["macd_signal"],
        df["macd_hist"],
    ) = macd(df["close"])

    df["atr"] = atr(df, 14)

    df["momentum"] = df["close"].diff(10)

    (
        df["adx"],
        df["plus_di"],
        df["minus_di"],
    ) = adx(df, 14)

    return df


# =========================================================
# SCORE
# =========================================================

def calculate_score(row):
    score = 0

    # EMA structure
    if (
        row["ema20"] > row["ema50"]
        and row["ema50"] > row["ema100"]
    ):
        score += 3

    elif (
        row["ema20"] < row["ema50"]
        and row["ema50"] < row["ema100"]
    ):
        score -= 3

    # Price vs EMA20
    if row["close"] > row["ema20"]:
        score += 1
    else:
        score -= 1

    # RSI
    if 52 <= row["rsi"] <= 68:
        score += 2

    elif 32 <= row["rsi"] < 48:
        score -= 2

    # MACD
    if (
        row["macd"] > row["macd_signal"]
        and row["macd_hist"] > 0
    ):
        score += 2

    elif (
        row["macd"] < row["macd_signal"]
        and row["macd_hist"] < 0
    ):
        score -= 2

    # Momentum
    if row["momentum"] > 0:
        score += 1
    else:
        score -= 1

    # ADX directional component
    if row["adx"] >= 25:
        if row["plus_di"] > row["minus_di"]:
            score += 2
        elif row["minus_di"] > row["plus_di"]:
            score -= 2

    return int(score)


def prepare_tf(df):
    if df is None or len(df) < 120:
        return df

    out = add_indicators(df)
    out["score"] = out.apply(calculate_score, axis=1)

    return out


# =========================================================
# SUPPORT / RESISTANCE
# =========================================================

def get_support_resistance(df, lookback=40):
    if df is None or df.empty:
        return None, None

    x = df.tail(lookback)

    support = x["low"].min()
    resistance = x["high"].max()

    return float(support), float(resistance)


# =========================================================
# B2 BREAKOUT + RETEST
# =========================================================

def detect_b2_signal(m15, signal_time):
    if m15 is None or len(m15) < 10:
        return None

    data = m15.loc[m15.index <= signal_time].copy()

    if len(data) < 10:
        return None

    # The last completed M15 candle is the possible retest.
    retest = data.iloc[-1]
    retest_time = data.index[-1]

    atr_value = retest.get("atr", np.nan)

    if pd.isna(atr_value) or atr_value <= 0:
        return None

    # Look back 1-6 M15 candles for a breakout.
    start = max(1, len(data) - 7)

    for i in range(len(data) - 2, start - 1, -1):
        breakout = data.iloc[i]
        breakout_time = data.index[i]

        # Breakout must happen before retest.
        if breakout_time >= retest_time:
            continue

        previous = data.iloc[i - 1]

        previous_high = previous["high"]
        previous_low = previous["low"]

        breakout_up = (
            breakout["close"] > previous_high
            and breakout["high"] > previous_high
        )

        breakout_down = (
            breakout["close"] < previous_low
            and breakout["low"] < previous_low
        )

        if breakout_up:
            level = previous_high

            touched = (
                retest["low"]
                <= level + (0.35 * atr_value)
                and retest["low"]
                >= level - (0.35 * atr_value)
            )

            held = retest["close"] > level

            aligned = (
                retest["score"] >= 5
                and retest["adx"] >= 20
                and retest["rsi"] >= 50
                and retest["macd"] > 0
            )

            if touched and held and aligned:
                return {
                    "direction": "BUY",
                    "level": float(level),
                    "breakout_time": breakout_time,
                    "retest_time": retest_time,
                }

        if breakout_down:
            level = previous_low

            touched = (
                retest["high"]
                >= level - (0.35 * atr_value)
                and retest["high"]
                <= level + (0.35 * atr_value)
            )

            held = retest["close"] < level

            aligned = (
                retest["score"] <= -5
                and retest["adx"] >= 20
                and retest["rsi"] <= 50
                and retest["macd"] < 0
            )

            if touched and held and aligned:
                return {
                    "direction": "SELL",
                    "level": float(level),
                    "breakout_time": breakout_time,
                    "retest_time": retest_time,
                }

    return None


# =========================================================
# LIVE ANALYSIS
# =========================================================

def analyze_live(m5_raw):
    m5 = get_completed_m5(m5_raw)

    if m5 is None or len(m5) < 150:
        return None

    m15 = resample_ohlcv(m5, "15min")
    h1 = resample_ohlcv(m5, "1h")
    h4 = resample_ohlcv(m5, "4h")

    m15 = prepare_tf(m15)
    h1 = prepare_tf(h1)
    h4 = prepare_tf(h4)
    m5 = prepare_tf(m5)

    if (
        m15.empty
        or h1.empty
        or h4.empty
        or m5.empty
    ):
        return None

    current_time = m5.index[-1]
    current = m5.iloc[-1]

    m15_row = latest_before(m15, current_time)
    h1_row = latest_before(h1, current_time)
    h4_row = latest_before(h4, current_time)

    if (
        m15_row is None
        or h1_row is None
        or h4_row is None
    ):
        return None

    scores = {
        "M5": int(current["score"]),
        "M15": int(m15_row["score"]),
        "H1": int(h1_row["score"]),
        "H4": int(h4_row["score"]),
    }

    buy_basic = (
        scores["M5"] >= 5
        and scores["M15"] >= 5
        and scores["H1"] >= 5
        and m15_row["adx"] >= 20
        and m15_row["rsi"] >= 50
        and m15_row["macd"] > 0
    )

    sell_basic = (
        scores["M5"] <= -5
        and scores["M15"] <= -5
        and scores["H1"] <= -5
        and m15_row["adx"] >= 20
        and m15_row["rsi"] <= 50
        and m15_row["macd"] < 0
    )

    b2 = detect_b2_signal(
        m15,
        current_time,
    )

    signal = "WAIT"

    if (
        buy_basic
        and b2 is not None
        and b2["direction"] == "BUY"
    ):
        signal = "BUY"

    elif (
        sell_basic
        and b2 is not None
        and b2["direction"] == "SELL"
    ):
        signal = "SELL"

    total_score = (
        scores["M5"]
        + scores["M15"]
        + scores["H1"]
        + scores["H4"]
    )

    confidence = (
        abs(total_score) / 44
    ) * 100

    atr_value = float(current["atr"])

    if pd.isna(atr_value) or atr_value <= 0:
        return None

    entry_available = (
        pd.Timestamp.now(tz="UTC").floor("5min")
        > current_time
    )

    if signal == "BUY":
        if entry_available:
            next_open = float(m5_raw.loc[
                m5_raw.index > current_time,
                "open"
            ].iloc[0]) if not m5_raw.loc[
                m5_raw.index > current_time
            ].empty else float(current["close"])
        else:
            next_open = float(current["close"])

        entry = next_open
        sl = entry - (1.5 * atr_value)
        tp1 = entry + (1.5 * atr_value)
        tp2 = entry + (2.5 * atr_value)

    elif signal == "SELL":
        if entry_available:
            next_rows = m5_raw.loc[
                m5_raw.index > current_time
            ]

            next_open = (
                float(next_rows["open"].iloc[0])
                if not next_rows.empty
                else float(current["close"])
            )
        else:
            next_open = float(current["close"])

        entry = next_open
        sl = entry + (1.5 * atr_value)
        tp1 = entry - (1.5 * atr_value)
        tp2 = entry - (2.5 * atr_value)

    else:
        entry = float(current["close"])
        sl = None
        tp1 = None
        tp2 = None

    support, resistance = get_support_resistance(
        m15,
        40,
    )

    return {
        "signal": signal,
        "signal_time": current_time,
        "price": float(current["close"]),
        "entry": float(entry),
        "entry_available": bool(entry_available),
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "atr": atr_value,
        "scores": scores,
        "confidence": float(confidence),
        "support": support,
        "resistance": resistance,
        "m5": current,
        "m15": m15_row,
        "h1": h1_row,
        "h4": h4_row,
        "b2": b2,
    }


# =========================================================
# PAPER TRADE
# =========================================================

def open_paper_trade(live, risk_percent=1.0):
    if st.session_state.paper_trade is not None:
        return False

    if live["signal"] not in ["BUY", "SELL"]:
        return False

    if not live["entry_available"]:
        return False

    entry = live["entry"]
    sl = live["sl"]
    tp1 = live["tp1"]
    tp2 = live["tp2"]

    risk_amount = (
        st.session_state.paper_balance
        * (risk_percent / 100)
    )

    trade = {
        "id": len(st.session_state.paper_history) + 1,
        "direction": live["signal"],
        "signal_time": live["signal_time"],
        "entry_time": now_utc(),
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "risk_amount": risk_amount,
        "risk_percent": risk_percent,
        "tp1_hit": False,
        "status": "OPEN",
        "exit": None,
        "exit_time": None,
        "result_r": None,
        "pnl": None,
        "exit_reason": None,
    }

    st.session_state.paper_trade = trade

    add_log(
        f"فتح صفقة Paper {trade['direction']} "
        f"Entry={entry:.2f} SL={sl:.2f} TP2={tp2:.2f}",
        "TRADE",
    )

    return True


def close_paper_trade(
    exit_price,
    reason,
):
    trade = st.session_state.paper_trade

    if trade is None:
        return

    direction = trade["direction"]

    if direction == "BUY":
        result_r = (
            (exit_price - trade["entry"])
            / (trade["entry"] - trade["sl"])
        )
    else:
        result_r = (
            (trade["entry"] - exit_price)
            / (trade["sl"] - trade["entry"])
        )

    pnl = trade["risk_amount"] * result_r

    st.session_state.paper_balance += pnl

    trade["exit"] = float(exit_price)
    trade["exit_time"] = now_utc()
    trade["result_r"] = float(result_r)
    trade["pnl"] = float(pnl)
    trade["status"] = "CLOSED"
    trade["exit_reason"] = reason

    st.session_state.paper_history.append(trade)

    add_log(
        f"إغلاق Paper {direction} "
        f"R={result_r:.2f} "
        f"PnL={pnl:.2f} "
        f"Reason={reason}",
        "TRADE",
    )

    st.session_state.paper_trade = None


def update_paper_trade(candle):
    trade = st.session_state.paper_trade

    if trade is None:
        return

    high = float(candle["high"])
    low = float(candle["low"])

    direction = trade["direction"]

    # SL FIRST if both SL and TP are touched
    if direction == "BUY":
        if low <= trade["sl"]:
            close_paper_trade(
                trade["sl"],
                "SL",
            )
            return

        if (
            not trade["tp1_hit"]
            and high >= trade["tp1"]
        ):
            trade["tp1_hit"] = True

        if high >= trade["tp2"]:
            close_paper_trade(
                trade["tp2"],
                "TP2",
            )
            return

    else:
        if high >= trade["sl"]:
            close_paper_trade(
                trade["sl"],
                "SL",
            )
            return

        if (
            not trade["tp1_hit"]
            and low <= trade["tp1"]
        ):
            trade["tp1_hit"] = True

        if low <= trade["tp2"]:
            close_paper_trade(
                trade["tp2"],
                "TP2",
            )
            return


# =========================================================
# BACKTEST
# =========================================================

def backtest_strategy(
    m5_full,
    bars=10000,
):
    if m5_full is None or m5_full.empty:
        return None

    data = m5_full.tail(bars).copy()

    if len(data) < 500:
        return None

    m15 = resample_ohlcv(data, "15min")
    h1 = resample_ohlcv(data, "1h")
    h4 = resample_ohlcv(data, "4h")

    m5 = prepare_tf(data)
    m15 = prepare_tf(m15)
    h1 = prepare_tf(h1)
    h4 = prepare_tf(h4)

    if (
        len(m5) < 200
        or len(m15) < 120
        or len(h1) < 120
        or len(h4) < 120
    ):
        return None

    split = int(len(m5) * 0.70)

    is_start = m5.index[0]
    is_end = m5.index[split - 1]

    oos_start = m5.index[split]

    trades = []

    open_trade = None

    for i in range(200, len(m5) - 1):
        timestamp = m5.index[i]
        candle = m5.iloc[i]

        m15_row = latest_before(
            m15,
            timestamp,
        )

        h1_row = latest_before(
            h1,
            timestamp,
        )

        h4_row = latest_before(
            h4,
            timestamp,
        )

        if (
            m15_row is None
            or h1_row is None
            or h4_row is None
        ):
            continue

        scores = {
            "M5": int(candle["score"]),
            "M15": int(m15_row["score"]),
            "H1": int(h1_row["score"]),
            "H4": int(h4_row["score"]),
        }

        buy_basic = (
            scores["M5"] >= 5
            and scores["M15"] >= 5
            and scores["H1"] >= 5
            and m15_row["adx"] >= 20
            and m15_row["rsi"] >= 50
            and m15_row["macd"] > 0
        )

        sell_basic = (
            scores["M5"] <= -5
            and scores["M15"] <= -5
            and scores["H1"] <= -5
            and m15_row["adx"] >= 20
            and m15_row["rsi"] <= 50
            and m15_row["macd"] < 0
        )

        b2 = detect_b2_signal(
            m15,
            timestamp,
        )

        signal = None

        if (
            buy_basic
            and b2 is not None
            and b2["direction"] == "BUY"
        ):
            signal = "BUY"

        elif (
            sell_basic
            and b2 is not None
            and b2["direction"] == "SELL"
        ):
            signal = "SELL"

        # Manage existing trade
        if open_trade is not None:
            direction = open_trade["direction"]

            high = float(candle["high"])
            low = float(candle["low"])

            exit_price = None
            reason = None

            if direction == "BUY":
                if low <= open_trade["sl"]:
                    exit_price = open_trade["sl"]
                    reason = "SL"

                elif high >= open_trade["tp"]:
                    exit_price = open_trade["tp"]
                    reason = "TP"

            else:
                if high >= open_trade["sl"]:
                    exit_price = open_trade["sl"]
                    reason = "SL"

                elif low <= open_trade["tp"]:
                    exit_price = open_trade["tp"]
                    reason = "TP"

            if exit_price is not None:
                if direction == "BUY":
                    risk = (
                        open_trade["entry"]
                        - open_trade["sl"]
                    )
                    result_r = (
                        exit_price
                        - open_trade["entry"]
                    ) / risk
                else:
                    risk = (
                        open_trade["sl"]
                        - open_trade["entry"]
                    )
                    result_r = (
                        open_trade["entry"]
                        - exit_price
                    ) / risk

                open_trade["exit"] = exit_price
                open_trade["exit_time"] = timestamp
                open_trade["result_r"] = result_r
                open_trade["reason"] = reason

                trades.append(open_trade)

                open_trade = None

                continue

        # Only one trade at a time
        if open_trade is None and signal is not None:
            if i + 1 >= len(m5):
                continue

            entry_time = m5.index[i + 1]
            entry = float(m5.iloc[i + 1]["open"])

            atr_value = float(candle["atr"])

            if pd.isna(atr_value) or atr_value <= 0:
                continue

            if signal == "BUY":
                sl = entry - (1.5 * atr_value)
                tp = entry + (2.5 * atr_value)

            else:
                sl = entry + (1.5 * atr_value)
                tp = entry - (2.5 * atr_value)

            open_trade = {
                "direction": signal,
                "signal_time": timestamp,
                "entry_time": entry_time,
                "entry": entry,
                "sl": sl,
                "tp": tp,
            }

    # Close remaining trade at final close
    if open_trade is not None:
        final_price = float(m5.iloc[-1]["close"])

        if open_trade["direction"] == "BUY":
            risk = (
                open_trade["entry"]
                - open_trade["sl"]
            )

            result_r = (
                final_price
                - open_trade["entry"]
            ) / risk

        else:
            risk = (
                open_trade["sl"]
                - open_trade["entry"]
            )

            result_r = (
                open_trade["entry"]
                - final_price
            ) / risk

        open_trade["exit"] = final_price
        open_trade["exit_time"] = m5.index[-1]
        open_trade["result_r"] = result_r
        open_trade["reason"] = "END"

        trades.append(open_trade)

    if not trades:
        return None

    df = pd.DataFrame(trades)

    df["period"] = np.where(
        pd.to_datetime(df["signal_time"]) < oos_start,
        "IS",
        "OOS",
    )

    return {
        "trades": df,
        "is_start": is_start,
        "is_end": is_end,
        "oos_start": oos_start,
    }


# =========================================================
# BACKTEST METRICS
# =========================================================

def calculate_metrics(trades):
    if trades is None or trades.empty:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "total_r": 0,
            "avg_r": 0,
            "max_dd": 0,
        }

    r = trades["result_r"].astype(float)

    wins = r[r > 0]
    losses = r[r < 0]

    gross_profit = wins.sum()
    gross_loss = abs(losses.sum())

    if gross_loss == 0:
        pf = np.inf if gross_profit > 0 else 0
    else:
        pf = gross_profit / gross_loss

    equity = r.cumsum()

    running_max = equity.cummax()
    drawdown = running_max - equity
    max_dd = drawdown.max()

    return {
        "trades": len(r),
        "wins": int((r > 0).sum()),
        "losses": int((r < 0).sum()),
        "win_rate": float((r > 0).mean() * 100),
        "profit_factor": float(pf),
        "total_r": float(r.sum()),
        "avg_r": float(r.mean()),
        "max_dd": float(max_dd),
    }


# =========================================================
# DISPLAY METRICS
# =========================================================

def show_metrics(metrics):
    cols = st.columns(6)

    cols[0].metric(
        "الصفقات",
        metrics["trades"],
    )

    cols[1].metric(
        "Win Rate",
        f"{metrics['win_rate']:.1f}%",
    )

    pf = metrics["profit_factor"]

    cols[2].metric(
        "Profit Factor",
        "∞" if np.isinf(pf) else f"{pf:.2f}",
    )

    cols[3].metric(
        "Total R",
        f"{metrics['total_r']:.2f}",
    )

    cols[4].metric(
        "Avg R",
        f"{metrics['avg_r']:.3f}",
    )

    cols[5].metric(
        "Max DD",
        f"{metrics['max_dd']:.2f}R",
    )


# =========================================================
# HEADER
# =========================================================

st.markdown(
    '<div class="main-title">بوت الذهب XAU/USD</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="sub-title">'
    'Smart Paper Trading • M5 / M15 / H1 / H4 • B2 Breakout + Retest'
    '</div>',
    unsafe_allow_html=True,
)

# =========================================================
# SIDEBAR
# =========================================================

with st.sidebar:
    st.header("إعدادات البوت")

    risk_percent = st.number_input(
        "المخاطرة لكل صفقة %",
        min_value=0.1,
        max_value=5.0,
        value=1.0,
        step=0.1,
    )

    backtest_bars = st.selectbox(
        "عدد شموع الباك تست",
        [5000, 10000, 15000, 30000],
        index=1,
    )

    st.divider()

    if st.session_state.kill_switch:
        st.error("Kill Switch مفعل")

        if st.button(
            "إلغاء Kill Switch",
            use_container_width=True,
        ):
            st.session_state.kill_switch = False
            add_log(
                "تم إلغاء Kill Switch",
                "SYSTEM",
            )
            st.rerun()

    else:
        if st.button(
            "إيقاف التداول Paper",
            use_container_width=True,
        ):
            st.session_state.kill_switch = True
            add_log(
                "تم تفعيل Kill Switch",
                "SYSTEM",
            )
            st.rerun()

    st.divider()

    if st.button(
        "تصفير Paper Trading",
        use_container_width=True,
    ):
        st.session_state.paper_balance = START_BALANCE
        st.session_state.paper_trade = None
        st.session_state.paper_history = []
        st.session_state.logs = []
        st.session_state.last_processed_signal_time = None

        add_log(
            "تم تصفير حساب Paper Trading",
            "SYSTEM",
        )

        st.rerun()

# =========================================================
# LOAD DATA
# =========================================================

try:
    m5_raw = get_twelve_data(
        interval="5min",
        outputsize=5000,
    )

    st.session_state.last_live_price = float(
        m5_raw["close"].iloc[-1]
    )

except Exception as e:
    st.error(
        f"تعذر تحميل بيانات XAU/USD: {e}"
    )

    st.stop()

# =========================================================
# TABS
# =========================================================

tab_live, tab_backtest, tab_paper, tab_logs = st.tabs(
    [
        "📊 التداول",
        "🧪 Backtest",
        "💰 Paper Trading",
        "📋 السجل",
    ]
)

# =========================================================
# LIVE TAB
# =========================================================

with tab_live:

    @st.fragment(run_every="60s")
    def live_fragment():

        try:
            raw = get_twelve_data(
                interval="5min",
                outputsize=5000,
            )

            completed_m5 = get_completed_m5(raw)

            live = analyze_live(raw)

            if live is None:
                st.warning(
                    "جاري تجهيز البيانات والمؤشرات..."
                )
                return

            st.session_state.last_refresh = now_utc()

            # -------------------------------------------------
            # Update existing paper trade
            # -------------------------------------------------

            if (
                not completed_m5.empty
                and st.session_state.paper_trade is not None
            ):
                last_completed = completed_m5.iloc[-1]

                update_paper_trade(
                    last_completed
                )

            # -------------------------------------------------
            # Open new paper trade
            # -------------------------------------------------

            if (
                live["signal"] in ["BUY", "SELL"]
                and not st.session_state.kill_switch
                and st.session_state.paper_trade is None
            ):

                signal_time = live["signal_time"]

                if (
                    st.session_state.last_processed_signal_time
                    != signal_time
                ):

                    if live["entry_available"]:

                        opened = open_paper_trade(
                            live,
                            risk_percent,
                        )

                        st.session_state.last_processed_signal_time = (
                            signal_time
                        )

                        if not opened:
                            add_log(
                                "تعذر فتح Paper trade",
                                "WARNING",
                            )

            # -------------------------------------------------
            # Main price
            # -------------------------------------------------

            c1, c2, c3, c4 = st.columns(4)

            c1.metric(
                "XAU/USD",
                fmt(live["price"]),
            )

            c2.metric(
                "Confluence",
                f"{live['confidence']:.1f}%",
            )

            c3.metric(
                "ATR M5",
                fmt(live["atr"]),
            )

            c4.metric(
                "Paper Balance",
                f"${st.session_state.paper_balance:,.2f}",
            )

            # -------------------------------------------------
            # Signal
            # -------------------------------------------------

            signal = live["signal"]

            if signal == "BUY":
                signal_class = "signal-buy"
                signal_text = "BUY"
            elif signal == "SELL":
                signal_class = "signal-sell"
                signal_text = "SELL"
            else:
                signal_class = "signal-neutral"
                signal_text = "WAIT"

            st.markdown(
                f"""
                <div class="signal-box {signal_class}">
                    <div class="small">الإشارة الحالية</div>
                    <div style="font-size:38px;font-weight:900;">
                        {signal_text}
                    </div>
                    <div class="small">
                        {live["signal_time"].strftime("%Y-%m-%d %H:%M UTC")}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            st.write("")

            # -------------------------------------------------
            # Timeframes
            # -------------------------------------------------

            st.subheader("اتجاه الفريمات")

            tf_cols = st.columns(4)

            for col, tf in zip(
                tf_cols,
                ["M5", "M15", "H1", "H4"],
            ):
                score = live["scores"][tf]

                col.markdown(
                    f"""
                    <div class="card">
                        <div class="metric-title">{tf}</div>
                        <div class="metric-value {trend_class(score)}">
                            {trend_label(score)}
                        </div>
                        <div class="small">
                            Score: {score}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            # -------------------------------------------------
            # Entry / SL / TP
            # -------------------------------------------------

            st.subheader("خطة الصفقة")

            plan_cols = st.columns(4)

            plan_cols[0].metric(
                "Entry",
                fmt(live["entry"]),
            )

            plan_cols[1].metric(
                "Stop Loss",
                fmt(live["sl"]),
            )

            plan_cols[2].metric(
                "TP1",
                fmt(live["tp1"]),
            )

            plan_cols[3].metric(
                "TP2",
                fmt(live["tp2"]),
            )

            if signal in ["BUY", "SELL"]:

                if live["entry_available"]:
                    st.success(
                        "الدخول محسوب من افتتاح شمعة M5 التالية للإشارة."
                    )
                else:
                    st.info(
                        "الإشارة تنتظر افتتاح شمعة M5 التالية."
                    )

            else:
                st.info(
                    "لا توجد إشارة مكتملة حالياً."
                )

            # -------------------------------------------------
            # B2
            # -------------------------------------------------

            st.subheader("Breakout + Retest")

            if live["b2"] is None:

                st.markdown(
                    """
                    <div class="warning">
                    لا يوجد Breakout + Retest مكتمل حالياً.
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            else:

                direction = live["b2"]["direction"]

                st.markdown(
                    f"""
                    <div class="success">
                        B2: <b>{direction}</b><br>
                        مستوى الاختراق:
                        <b>{live["b2"]["level"]:.2f}</b><br>
                        Breakout:
                        {live["b2"]["breakout_time"]}<br>
                        Retest:
                        {live["b2"]["retest_time"]}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            # -------------------------------------------------
            # Indicators
            # -------------------------------------------------

            st.subheader("المؤشرات")

            ind_cols = st.columns(6)

            ind_cols[0].metric(
                "RSI M15",
                fmt(live["m15"]["rsi"], 1),
            )

            ind_cols[1].metric(
                "MACD M15",
                fmt(live["m15"]["macd"], 3),
            )

            ind_cols[2].metric(
                "ADX M15",
                fmt(live["m15"]["adx"], 1),
            )

            ind_cols[3].metric(
                "EMA20 M15",
                fmt(live["m15"]["ema20"]),
            )

            ind_cols[4].metric(
                "EMA50 M15",
                fmt(live["m15"]["ema50"]),
            )

            ind_cols[5].metric(
                "EMA100 M15",
                fmt(live["m15"]["ema100"]),
            )

            # -------------------------------------------------
            # S/R
            # -------------------------------------------------

            st.subheader("Support / Resistance")

            sr1, sr2 = st.columns(2)

            sr1.metric(
                "Support",
                fmt(live["support"]),
            )

            sr2.metric(
                "Resistance",
                fmt(live["resistance"]),
            )

            # -------------------------------------------------
            # Current Paper trade
            # -------------------------------------------------

            st.subheader("الصفقة المفتوحة")

            trade = st.session_state.paper_trade

            if trade is None:

                st.info(
                    "لا توجد صفقة Paper مفتوحة."
                )

            else:

                st.markdown(
                    f"""
                    <div class="trade-open">
                        <b>{trade["direction"]}</b><br>
                        Entry: {trade["entry"]:.2f}<br>
                        SL: {trade["sl"]:.2f}<br>
                        TP1: {trade["tp1"]:.2f}<br>
                        TP2: {trade["tp2"]:.2f}<br>
                        TP1 Hit:
                        {"نعم" if trade["tp1_hit"] else "لا"}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            # -------------------------------------------------
            # System state
            # -------------------------------------------------

            st.subheader("حالة النظام")

            if st.session_state.kill_switch:
                st.error(
                    "Kill Switch مفعل — لن يتم فتح صفقات جديدة."
                )
            else:
                st.success(
                    "النظام يعمل Paper Trading."
                )

            st.caption(
                "التحديث التلقائي كل 60 ثانية أثناء بقاء الصفحة مفتوحة."
            )

        except Exception as e:

            st.error(
                f"خطأ أثناء تحديث التداول: {e}"
            )

    live_fragment()

# =========================================================
# BACKTEST TAB
# =========================================================

with tab_backtest:

    st.subheader("Backtest — B2 Breakout + Retest")

    st.markdown(
        """
        <div class="warning">
        نتائج الباك تست تاريخية وليست ضماناً للأداء المستقبلي.
        النموذج الحالي لا يحاكي السبريد والانزلاق والعمولات أو الأخبار.
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button(
        "تشغيل Backtest",
        type="primary",
        use_container_width=True,
    ):

        with st.spinner("جاري تشغيل الباك تست..."):

            try:

                result = backtest_strategy(
                    m5_raw,
                    backtest_bars,
                )

                if result is None:
                    st.error(
                        "لم تتوفر بيانات كافية للباك تست."
                    )

                else:

                    trades = result["trades"]

                    is_trades = trades[
                        trades["period"] == "IS"
                    ]

                    oos_trades = trades[
                        trades["period"] == "OOS"
                    ]

                    is_metrics = calculate_metrics(
                        is_trades
                    )

                    oos_metrics = calculate_metrics(
                        oos_trades
                    )

                    st.subheader("In-Sample")

                    show_metrics(
                        is_metrics
                    )

                    st.subheader("Out-of-Sample")

                    show_metrics(
                        oos_metrics
                    )

                    if oos_metrics["trades"] < 50:

                        st.markdown(
                            f"""
                            <div class="warning">
                            عدد صفقات OOS هو
                            <b>{oos_metrics["trades"]}</b>.
                            العينة صغيرة للحكم القوي على الاستراتيجية.
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                    # -----------------------------------------
                    # Equity
                    # -----------------------------------------

                    st.subheader(
                        "OOS Equity — R"
                    )

                    if not oos_trades.empty:

                        equity = (
                            oos_trades["result_r"]
                            .cumsum()
                        )

                        chart = pd.DataFrame(
                            {
                                "Equity R": equity.values
                            }
                        )

                        st.line_chart(
                            chart,
                            use_container_width=True,
                        )

                    # -----------------------------------------
                    # OOS trades
                    # -----------------------------------------

                    st.subheader(
                        "OOS Trades"
                    )

                    if oos_trades.empty:

                        st.info(
                            "لا توجد صفقات OOS."
                        )

                    else:

                        display = oos_trades.copy()

                        display["signal_time"] = (
                            display["signal_time"]
                            .astype(str)
                        )

                        display["entry_time"] = (
                            display["entry_time"]
                            .astype(str)
                        )

                        display["exit_time"] = (
                            display["exit_time"]
                            .astype(str)
                        )

                        st.dataframe(
                            display[
                                [
                                    "direction",
                                    "signal_time",
                                    "entry_time",
                                    "entry",
                                    "sl",
                                    "tp",
                                    "exit",
                                    "result_r",
                                    "reason",
                                ]
                            ],
                            use_container_width=True,
                            hide_index=True,
                        )

            except Exception as e:

                st.error(
                    f"خطأ في Backtest: {e}"
                )

# =========================================================
# PAPER TRADING TAB
# =========================================================

with tab_paper:

    st.subheader("Paper Trading")

    balance = st.session_state.paper_balance

    total_pnl = (
        balance - START_BALANCE
    )

    closed = st.session_state.paper_history

    wins = sum(
        1
        for x in closed
        if x["result_r"] is not None
        and x["result_r"] > 0
    )

    losses = sum(
        1
        for x in closed
        if x["result_r"] is not None
        and x["result_r"] < 0
    )

    win_rate = (
        wins / len(closed) * 100
        if closed
        else 0
    )

    p1, p2, p3, p4 = st.columns(4)

    p1.metric(
        "Balance",
        f"${balance:,.2f}",
    )

    p2.metric(
        "P&L",
        f"${total_pnl:,.2f}",
    )

    p3.metric(
        "Trades",
        len(closed),
    )

    p4.metric(
        "Win Rate",
        f"{win_rate:.1f}%",
    )

    st.divider()

    if st.session_state.paper_trade:

        trade = st.session_state.paper_trade

        st.markdown(
            f"""
            <div class="trade-open">
                <h3>صفقة مفتوحة — {trade["direction"]}</h3>
                Entry: {trade["entry"]:.2f}<br>
                SL: {trade["sl"]:.2f}<br>
                TP1: {trade["tp1"]:.2f}<br>
                TP2: {trade["tp2"]:.2f}<br>
                Risk: {trade["risk_percent"]:.2f}%
            </div>
            """,
            unsafe_allow_html=True,
        )

    else:

        st.info(
            "لا توجد صفقة Paper مفتوحة."
        )

    st.subheader("سجل Paper Trades")

    if not closed:

        st.info(
            "لم يتم إغلاق أي صفقة Paper حتى الآن."
        )

    else:

        paper_df = pd.DataFrame(
            closed
        )

        cols = [
            "id",
            "direction",
            "entry",
            "sl",
            "tp1",
            "tp2",
            "exit",
            "result_r",
            "pnl",
            "exit_reason",
        ]

        st.dataframe(
            paper_df[
                [
                    c
                    for c in cols
                    if c in paper_df.columns
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.caption(
        "Paper Trading فقط — لا يتم إرسال أي أمر حقيقي إلى وسيط."
    )

# =========================================================
# LOG TAB
# =========================================================

with tab_logs:

    st.subheader("System Logs")

    if not st.session_state.logs:

        st.info(
            "لا توجد سجلات حتى الآن."
        )

    else:

        log_df = pd.DataFrame(
            st.session_state.logs
        )

        st.dataframe(
            log_df,
            use_container_width=True,
            hide_index=True,
        )

# =========================================================
# FOOTER
# =========================================================

st.divider()

st.caption(
    "بوت الذهب XAU/USD — Paper Trading | "
    "M5/M15/H1/H4 | EMA | RSI | MACD | ATR | ADX | "
    "Breakout + Retest"
)

st.caption(
    "تنبيه: هذه النسخة لا تنفذ صفقات حقيقية ولا تتصل بإرسال أوامر إلى Derayah."
)

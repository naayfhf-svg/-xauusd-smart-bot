import streamlit as st
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timezone

# ============================================================
# BOT CONFIG
# ============================================================

st.set_page_config(
    page_title="بوت الذهب XAU/USD",
    page_icon="🥇",
    layout="wide",
    initial_sidebar_state="expanded",
)

SYMBOL = "XAU/USD"
API_URL = "https://api.twelvedata.com/time_series"

# ============================================================
# STYLE
# ============================================================

st.markdown("""
<style>
    .stApp {
        background: #0b0f14;
        color: #f5f5f5;
    }

    section[data-testid="stSidebar"] {
        background: #10151c;
    }

    .main-title {
        font-size: 34px;
        font-weight: 800;
        margin-bottom: 2px;
    }

    .sub-title {
        color: #9da7b3;
        font-size: 14px;
        margin-bottom: 20px;
    }

    .gold-box {
        border: 1px solid #7f641d;
        background: linear-gradient(135deg, #15130d, #0f1115);
        border-radius: 14px;
        padding: 18px;
        margin-bottom: 15px;
    }

    .status-good {
        color: #41d98b;
        font-weight: 700;
    }

    .status-bad {
        color: #ff6262;
        font-weight: 700;
    }

    .status-neutral {
        color: #d7d7d7;
        font-weight: 700;
    }

    div[data-testid="stMetric"] {
        background: #11161d;
        border: 1px solid #252c35;
        padding: 12px;
        border-radius: 12px;
    }

    .small-note {
        color: #89929e;
        font-size: 12px;
    }

    .signal-buy {
        color: #36dc87;
        font-size: 28px;
        font-weight: 800;
    }

    .signal-sell {
        color: #ff5c67;
        font-size: 28px;
        font-weight: 800;
    }

    .signal-wait {
        color: #d5d8dc;
        font-size: 28px;
        font-weight: 800;
    }
</style>
""", unsafe_allow_html=True)

# ============================================================
# SESSION STATE
# ============================================================

defaults = {
    "paper_balance": 10000.0,
    "paper_start_balance": 10000.0,
    "paper_trade": None,
    "paper_history": [],
    "decision_log": [],
    "kill_switch": False,
    "last_signal_time": None,
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

# ============================================================
# API KEY
# ============================================================

try:
    API_KEY = st.secrets["TWELVE_DATA_API_KEY"]
except Exception:
    API_KEY = ""

# ============================================================
# HELPERS
# ============================================================

def log_event(event, details="", level="INFO"):
    row = {
        "time": datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        ),
        "level": level,
        "event": event,
        "details": details,
    }

    st.session_state.decision_log.append(row)

    if len(st.session_state.decision_log) > 1000:
        st.session_state.decision_log = (
            st.session_state.decision_log[-1000:]
        )


def safe_float(value, default=np.nan):
    try:
        return float(value)
    except Exception:
        return default


def clean_ohlc(df):
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    df.columns = [
        str(c).strip().lower()
        for c in df.columns
    ]

    # Twelve Data may not return volume for XAU/USD
    if "volume" not in df.columns:
        df["volume"] = 0.0
    else:
        df["volume"] = pd.to_numeric(
            df["volume"],
            errors="coerce"
        ).fillna(0.0)

    required = [
        "open",
        "high",
        "low",
        "close",
    ]

    for col in required:
        if col not in df.columns:
            raise ValueError(
                f"البيانات ناقصة: العمود {col} غير موجود."
            )

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    if df.empty:
        return df

    df = df[
        (df["high"] >= df["low"]) &
        (df["high"] >= df["open"]) &
        (df["high"] >= df["close"]) &
        (df["low"] <= df["open"]) &
        (df["low"] <= df["close"])
    ]

    df = df[
        ~df.index.duplicated(
            keep="last"
        )
    ]

    return df.sort_index()


# ============================================================
# TWELVE DATA
# ============================================================

@st.cache_data(ttl=60)
def get_candles(
    interval="5min",
    outputsize=500
):
    if not API_KEY:
        raise RuntimeError(
            "لم يتم العثور على TWELVE_DATA_API_KEY "
            "داخل Streamlit Secrets."
        )

    params = {
        "symbol": SYMBOL,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": API_KEY,
        "format": "JSON",
        "timezone": "UTC",
    }

    try:
        response = requests.get(
            API_URL,
            params=params,
            timeout=20,
        )
    except requests.RequestException as e:
        raise RuntimeError(
            f"فشل الاتصال بـ Twelve Data: {e}"
        )

    if response.status_code != 200:
        raise RuntimeError(
            f"Twelve Data HTTP {response.status_code}"
        )

    data = response.json()

    if data.get("status") == "error":
        raise RuntimeError(
            data.get(
                "message",
                "خطأ غير معروف من Twelve Data"
            )
        )

    values = data.get("values")

    if not values:
        raise RuntimeError(
            "Twelve Data لم ترجع بيانات."
        )

    df = pd.DataFrame(values)

    if "datetime" not in df.columns:
        raise RuntimeError(
            "البيانات لا تحتوي على datetime."
        )

    df["datetime"] = pd.to_datetime(
        df["datetime"],
        errors="coerce",
        utc=True,
    )

    df = df.dropna(
        subset=["datetime"]
    )

    df = df.set_index("datetime")

    df = clean_ohlc(df)

    if len(df) < 100:
        raise RuntimeError(
            f"عدد الشموع غير كافٍ: {len(df)}"
        )

    return df


# ============================================================
# RESAMPLING
# ============================================================

def resample_ohlc(df, rule):
    if df is None or df.empty:
        return pd.DataFrame()

    result = df.resample(
        rule,
        label="right",
        closed="left",
        origin="start_day",
    ).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })

    result = result.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    return result


# ============================================================
# INDICATORS
# ============================================================

def ema(series, period):
    return series.ewm(
        span=period,
        adjust=False
    ).mean()


def rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    rs = (
        avg_gain /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    result = (
        100 -
        (100 / (1 + rs))
    )

    return result.fillna(50)


def atr(df, period=14):
    prev_close = df["close"].shift(1)

    tr1 = (
        df["high"] -
        df["low"]
    )

    tr2 = (
        df["high"] -
        prev_close
    ).abs()

    tr3 = (
        df["low"] -
        prev_close
    ).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()


def macd(series):
    fast = ema(
        series,
        12
    )

    slow = ema(
        series,
        26
    )

    line = fast - slow

    signal = ema(
        line,
        9
    )

    hist = line - signal

    return (
        line,
        signal,
        hist,
    )


def adx(df, period=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where(
            (
                (up_move > down_move) &
                (up_move > 0)
            ),
            up_move,
            0.0,
        ),
        index=df.index,
    )

    minus_dm = pd.Series(
        np.where(
            (
                (down_move > up_move) &
                (down_move > 0)
            ),
            down_move,
            0.0,
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

    atr_value = tr.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    plus_di = (
        100 *
        plus_dm.ewm(
            alpha=1 / period,
            adjust=False
        ).mean() /
        atr_value.replace(
            0,
            np.nan
        )
    )

    minus_di = (
        100 *
        minus_dm.ewm(
            alpha=1 / period,
            adjust=False
        ).mean() /
        atr_value.replace(
            0,
            np.nan
        )
    )

    dx = (
        100 *
        (plus_di - minus_di).abs() /
        (
            plus_di +
            minus_di
        ).replace(
            0,
            np.nan
        )
    )

    adx_value = dx.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    return (
        adx_value.fillna(0),
        plus_di.fillna(0),
        minus_di.fillna(0),
    )


def add_indicators(df):
    df = df.copy()

    df["ema20"] = ema(
        df["close"],
        20
    )

    df["ema50"] = ema(
        df["close"],
        50
    )

    df["ema100"] = ema(
        df["close"],
        100
    )

    df["rsi"] = rsi(
        df["close"],
        14
    )

    (
        df["macd"],
        df["macd_signal"],
        df["macd_hist"],
    ) = macd(
        df["close"]
    )

    df["atr"] = atr(
        df,
        14
    )

    (
        df["adx"],
        df["plus_di"],
        df["minus_di"],
    ) = adx(
        df,
        14
    )

    df["momentum"] = (
        df["close"].diff(10)
    )

    return df


# ============================================================
# SCORE
# ============================================================

def calculate_score(row):
    score = 0

    if (
        row["ema20"] >
        row["ema50"] >
        row["ema100"]
    ):
        score += 3

    elif (
        row["ema20"] <
        row["ema50"] <
        row["ema100"]
    ):
        score -= 3

    if row["close"] > row["ema20"]:
        score += 1
    else:
        score -= 1

    if (
        52 <= row["rsi"] <= 68
    ):
        score += 2

    elif (
        32 <= row["rsi"] < 48
    ):
        score -= 2

    if (
        row["macd"] >
        row["macd_signal"]
        and row["macd_hist"] > 0
    ):
        score += 2

    elif (
        row["macd"] <
        row["macd_signal"]
        and row["macd_hist"] < 0
    ):
        score -= 2

    if row["momentum"] > 0:
        score += 1

    elif row["momentum"] < 0:
        score -= 1

    if row["adx"] >= 25:

        if (
            row["plus_di"] >
            row["minus_di"]
        ):
            score += 2

        elif (
            row["minus_di"] >
            row["plus_di"]
        ):
            score -= 2

    return score


def add_scores(df):
    df = df.copy()

    df["score"] = df.apply(
        calculate_score,
        axis=1
    )

    df["trend"] = np.select(
        [
            df["score"] >= 5,
            df["score"] <= -5,
        ],
        [
            "صاعد",
            "هابط",
        ],
        default="محايد",
    )

    return df


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def get_support_resistance(
    df,
    lookback=40
):
    recent = df.tail(
        lookback
    )

    if recent.empty:
        return (
            np.nan,
            np.nan
        )

    return (
        recent["low"].min(),
        recent["high"].max(),
    )


# ============================================================
# TRUE BREAKOUT + RETEST B2
# ============================================================

def detect_breakout_retest(
    df,
    lookback=20,
    tolerance_atr=0.35,
    max_retest_bars=6,
):
    """
    B2:
    1. Breakout occurs first.
    2. Retest may happen 1-6 M15 candles later.
    3. No future candle is used.
    4. Retest must hold the breakout level.
    """

    df = df.copy()

    df["breakout_up"] = False
    df["breakout_down"] = False
    df["retest_buy"] = False
    df["retest_sell"] = False

    active_up = None
    active_down = None

    for i in range(
        lookback,
        len(df)
    ):
        row = df.iloc[i]

        previous = df.iloc[
            i - lookback:i
        ]

        resistance = (
            previous["high"].max()
        )

        support = (
            previous["low"].min()
        )

        current_atr = safe_float(
            row["atr"]
        )

        if (
            not np.isfinite(
                current_atr
            )
            or current_atr <= 0
        ):
            continue

        # ----------------------------------------
        # Detect new breakout
        # ----------------------------------------

        breakout_up = (
            row["close"] >
            resistance
        )

        breakout_down = (
            row["close"] <
            support
        )

        if breakout_up:
            df.loc[
                df.index[i],
                "breakout_up"
            ] = True

            active_up = {
                "index": i,
                "level": float(
                    resistance
                ),
            }

            active_down = None

        if breakout_down:
            df.loc[
                df.index[i],
                "breakout_down"
            ] = True

            active_down = {
                "index": i,
                "level": float(
                    support
                ),
            }

            active_up = None

        # ----------------------------------------
        # Buy retest
        # ----------------------------------------

        if active_up is not None:

            bars_since = (
                i -
                active_up["index"]
            )

            if (
                1 <= bars_since <=
                max_retest_bars
            ):

                level = (
                    active_up["level"]
                )

                distance = abs(
                    row["low"] -
                    level
                )

                if (
                    distance <=
                    current_atr *
                    tolerance_atr
                    and
                    row["close"] >=
                    level
                ):
                    df.loc[
                        df.index[i],
                        "retest_buy"
                    ] = True

                    active_up = None

            elif (
                bars_since >
                max_retest_bars
            ):
                active_up = None

        # ----------------------------------------
        # Sell retest
        # ----------------------------------------

        if active_down is not None:

            bars_since = (
                i -
                active_down["index"]
            )

            if (
                1 <= bars_since <=
                max_retest_bars
            ):

                level = (
                    active_down["level"]
                )

                distance = abs(
                    row["high"] -
                    level
                )

                if (
                    distance <=
                    current_atr *
                    tolerance_atr
                    and
                    row["close"] <=
                    level
                ):
                    df.loc[
                        df.index[i],
                        "retest_sell"
                    ] = True

                    active_down = None

            elif (
                bars_since >
                max_retest_bars
            ):
                active_down = None

    return df


# ============================================================
# BUILD MULTI-TIMEFRAME DATA
# ============================================================

def build_timeframes(m5):
    m5 = add_indicators(
        m5
    )

    m5 = add_scores(
        m5
    )

    m15 = resample_ohlc(
        m5,
        "15min"
    )

    h1 = resample_ohlc(
        m5,
        "1h"
    )

    h4 = resample_ohlc(
        m5,
        "4h"
    )

    m15 = add_indicators(
        m15
    )

    h1 = add_indicators(
        h1
    )

    h4 = add_indicators(
        h4
    )

    m15 = add_scores(
        m15
    )

    h1 = add_scores(
        h1
    )

    h4 = add_scores(
        h4
    )

    m15 = detect_breakout_retest(
        m15
    )

    return (
        m5,
        m15,
        h1,
        h4,
    )


# ============================================================
# HIGHER TIMEFRAME ALIGNMENT
# ============================================================

def latest_before(
    df,
    timestamp
):
    if df.empty:
        return None

    available = df.loc[
        df.index <= timestamp
    ]

    if available.empty:
        return None

    return available.iloc[-1]


# ============================================================
# LIVE ANALYSIS
# ============================================================

def get_completed_m5(m5):
    """
    Keep only M5 candles that are fully closed.
    """

    now_utc = pd.Timestamp.now(
        tz="UTC"
    )

    completed_mask = (
        m5.index +
        pd.Timedelta(minutes=5)
        <= now_utc
    )

    completed = m5.loc[
        completed_mask
    ].copy()

    return completed


def analyze_live(m5):
    # ----------------------------------------
    # Only completed M5 candles
    # ----------------------------------------

    m5 = get_completed_m5(
        m5
    )

    if len(m5) < 120:
        raise RuntimeError(
            "لا توجد شموع M5 مكتملة كافية للتحليل."
        )

    (
        m5,
        m15,
        h1,
        h4,
    ) = build_timeframes(
        m5
    )

    current_time = m5.index[-1]
    current = m5.iloc[-1]

    # The M5 timestamp represents its close
    row15 = latest_before(
        m15,
        current_time
    )

    row1h = latest_before(
        h1,
        current_time
    )

    row4h = latest_before(
        h4,
        current_time
    )

    if (
        row15 is None or
        row1h is None or
        row4h is None
    ):
        raise RuntimeError(
            "تعذر محاذاة الفريمات."
        )

    score5 = float(
        current["score"]
    )

    score15 = float(
        row15["score"]
    )

    score1h = float(
        row1h["score"]
    )

    score4h = float(
        row4h["score"]
    )

    total = (
        score5 +
        score15 +
        score1h +
        score4h
    )

    # Maximum possible score:
    # 11 points per timeframe x 4 = 44
    strength = min(
        100,
        abs(total) / 44 * 100
    )

    signal = "WAIT"
    strategy = "NONE"

    # ========================================================
    # STRATEGY A
    # ========================================================

    buy_a = (
        score5 >= 5
        and score15 >= 5
        and score1h >= 5
        and row15["adx"] >= 20
        and row15["rsi"] >= 50
        and row15["macd"] > 0
    )

    sell_a = (
        score5 <= -5
        and score15 <= -5
        and score1h <= -5
        and row15["adx"] >= 20
        and row15["rsi"] <= 50
        and row15["macd"] < 0
    )

    # ========================================================
    # STRATEGY B2
    # ========================================================

    buy_b = (
        bool(
            row15.get(
                "retest_buy",
                False
            )
        )
        and score5 >= 5
        and score15 >= 5
        and score1h >= 5
        and row15["adx"] >= 20
        and row15["rsi"] >= 50
        and row15["macd"] > 0
    )

    sell_b = (
        bool(
            row15.get(
                "retest_sell",
                False
            )
        )
        and score5 <= -5
        and score15 <= -5
        and score1h <= -5
        and row15["adx"] >= 20
        and row15["rsi"] <= 50
        and row15["macd"] < 0
    )

    if buy_b:
        signal = "BUY"
        strategy = "B2"

    elif sell_b:
        signal = "SELL"
        strategy = "B2"

    elif buy_a:
        signal = "BUY"
        strategy = "A"

    elif sell_a:
        signal = "SELL"
        strategy = "A"

    atr_value = safe_float(
        row15["atr"]
    )

    # Signal is generated from the completed candle.
    # Entry is the NEXT M5 candle open.
    next_entry = (
        m5["open"].iloc[-1]
        if len(m5) >= 1
        else current["close"]
    )

    entry = safe_float(
        next_entry
    )

    if (
        np.isfinite(atr_value)
        and atr_value > 0
        and signal in [
            "BUY",
            "SELL",
        ]
    ):

        if signal == "BUY":
            sl = (
                entry -
                1.5 * atr_value
            )

            tp1 = (
                entry +
                1.5 * atr_value
            )

            tp2 = (
                entry +
                2.5 * atr_value
            )

        else:
            sl = (
                entry +
                1.5 * atr_value
            )

            tp1 = (
                entry -
                1.5 * atr_value
            )

            tp2 = (
                entry -
                2.5 * atr_value
            )

    else:
        sl = np.nan
        tp1 = np.nan
        tp2 = np.nan

    support, resistance = (
        get_support_resistance(
            m15
        )
    )

    regime = "محايد"

    if (
        score1h >= 5
        and score4h >= 5
    ):
        regime = "صاعد"

    elif (
        score1h <= -5
        and score4h <= -5
    ):
        regime = "هابط"

    return {
        "time": current_time,
        "price": float(
            current["close"]
        ),
        "signal": signal,
        "strategy": strategy,
        "strength": strength,
        "score5": score5,
        "score15": score15,
        "score1h": score1h,
        "score4h": score4h,
        "trend5": current["trend"],
        "trend15": row15["trend"],
        "trend1h": row1h["trend"],
        "trend4h": row4h["trend"],
        "regime": regime,
        "rsi": float(
            row15["rsi"]
        ),
        "adx": float(
            row15["adx"]
        ),
        "atr": atr_value,
        "support": support,
        "resistance": resistance,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "breakout_buy": bool(
            row15.get(
                "retest_buy",
                False
            )
        ),
        "breakout_sell": bool(
            row15.get(
                "retest_sell",
                False
            )
        ),
    }


# ============================================================
# PAPER TRADING
# ============================================================

def open_paper_trade(
    live,
    risk_percent
):
    if (
        live["signal"]
        not in [
            "BUY",
            "SELL",
        ]
    ):
        return

    if (
        not np.isfinite(
            live["entry"]
        )
        or not np.isfinite(
            live["sl"]
        )
        or not np.isfinite(
            live["tp2"]
        )
    ):
        return

    if (
        st.session_state.paper_trade
        is not None
    ):
        return

    balance = float(
        st.session_state.paper_balance
    )

    risk_amount = (
        balance *
        risk_percent /
        100
    )

    risk_distance = abs(
        live["entry"] -
        live["sl"]
    )

    if (
        risk_distance <= 0
        or not np.isfinite(
            risk_distance
        )
    ):
        return

    trade = {
        "opened_at": datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        ),
        "signal_time": str(
            live["time"]
        ),
        "side": live["signal"],
        "strategy": live["strategy"],
        "entry": float(
            live["entry"]
        ),
        "sl": float(
            live["sl"]
        ),
        "tp1": float(
            live["tp1"]
        ),
        "tp2": float(
            live["tp2"]
        ),
        "risk_percent": float(
            risk_percent
        ),
        "risk_amount": float(
            risk_amount
        ),
        "risk_distance": float(
            risk_distance
        ),
    }

    st.session_state.paper_trade = (
        trade
    )

    log_event(
        "PAPER_OPEN",
        (
            f"{live['signal']} | "
            f"Strategy={live['strategy']} | "
            f"Entry={live['entry']:.2f} | "
            f"SL={live['sl']:.2f} | "
            f"TP2={live['tp2']:.2f}"
        ),
        "INFO",
    )


def update_paper_trade(
    candle
):
    trade = (
        st.session_state.paper_trade
    )

    if trade is None:
        return

    high = float(
        candle["high"]
    )

    low = float(
        candle["low"]
    )

    close = float(
        candle["close"]
    )

    side = trade["side"]

    result_r = None
    exit_price = None
    exit_reason = None

    if side == "BUY":

        # Conservative assumption:
        # SL gets priority if both are hit
        if low <= trade["sl"]:
            exit_price = (
                trade["sl"]
            )
            exit_reason = "SL"
            result_r = -1.0

        elif high >= trade["tp2"]:
            exit_price = (
                trade["tp2"]
            )
            exit_reason = "TP2"
            result_r = 2.5

    elif side == "SELL":

        if high >= trade["sl"]:
            exit_price = (
                trade["sl"]
            )
            exit_reason = "SL"
            result_r = -1.0

        elif low <= trade["tp2"]:
            exit_price = (
                trade["tp2"]
            )
            exit_reason = "TP2"
            result_r = 2.5

    if result_r is None:
        return

    pnl = (
        trade["risk_amount"] *
        result_r
    )

    st.session_state.paper_balance += (
        pnl
    )

    history_row = {
        "opened_at": trade[
            "opened_at"
        ],
        "closed_at": datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        ),
        "side": side,
        "strategy": trade[
            "strategy"
        ],
        "entry": trade[
            "entry"
        ],
        "exit": exit_price,
        "SL": trade[
            "sl"
        ],
        "TP2": trade[
            "tp2"
        ],
        "result": exit_reason,
        "R": result_r,
        "PnL": pnl,
        "balance": (
            st.session_state.paper_balance
        ),
    }

    st.session_state.paper_history.append(
        history_row
    )

    log_event(
        "PAPER_CLOSE",
        (
            f"{side} | "
            f"{exit_reason} | "
            f"R={result_r:+.2f} | "
            f"PnL=${pnl:+.2f}"
        ),
        "INFO",
    )

    st.session_state.paper_trade = None


# ============================================================
# BACKTEST PREPARATION
# ============================================================

def prepare_backtest_data(m5):
    return build_timeframes(
        m5
    )


# ============================================================
# BACKTEST
# ============================================================

def simulate_strategy(
    m5,
    m15,
    h1,
    h4,
    strategy="A",
    start=None,
    end=None,
):
    trades = []

    if (
        start is None
    ):
        start = m5.index[0]

    if (
        end is None
    ):
        end = m5.index[-1]

    m5_slice = m5.loc[
        (m5.index >= start) &
        (m5.index <= end)
    ].copy()

    if m5_slice.empty:
        return pd.DataFrame()

    open_trade = None

    for i in range(
        len(m5_slice)
    ):
        timestamp = (
            m5_slice.index[i]
        )

        row = m5_slice.iloc[i]

        # ====================================================
        # MANAGE OPEN TRADE
        # ====================================================

        if open_trade is not None:

            side = open_trade[
                "side"
            ]

            sl = open_trade[
                "sl"
            ]

            tp = open_trade[
                "tp"
            ]

            high = float(
                row["high"]
            )

            low = float(
                row["low"]
            )

            exit_price = None
            result_r = None
            reason = None

            if side == "BUY":

                if low <= sl:
                    exit_price = sl
                    result_r = -1.0
                    reason = "SL"

                elif high >= tp:
                    exit_price = tp
                    result_r = 2.5
                    reason = "TP2"

            else:

                if high >= sl:
                    exit_price = sl
                    result_r = -1.0
                    reason = "SL"

                elif low <= tp:
                    exit_price = tp
                    result_r = 2.5
                    reason = "TP2"

            if result_r is not None:

                trades.append({
                    "entry_time":
                        open_trade[
                            "entry_time"
                        ],
                    "exit_time":
                        timestamp,
                    "side": side,
                    "strategy":
                        open_trade[
                            "strategy"
                        ],
                    "entry":
                        open_trade[
                            "entry"
                        ],
                    "exit":
                        exit_price,
                    "sl": sl,
                    "tp":
                        open_trade[
                            "tp"
                        ],
                    "result_r":
                        result_r,
                    "reason": reason,
                })

                open_trade = None

                continue

        # ====================================================
        # NEED NEXT CANDLE FOR ENTRY
        # ====================================================

        if (
            i + 1 >=
            len(m5_slice)
        ):
            continue

        # Signal is generated on current completed M5.
        # Entry is next M5 OPEN.
        signal_time = timestamp

        signal_row = row

        row15 = latest_before(
            m15,
            signal_time
        )

        row1h = latest_before(
            h1,
            signal_time
        )

        row4h = latest_before(
            h4,
            signal_time
        )

        if (
            row15 is None or
            row1h is None or
            row4h is None
        ):
            continue

        score5 = float(
            signal_row["score"]
        )

        score15 = float(
            row15["score"]
        )

        score1h = float(
            row1h["score"]
        )

        score4h = float(
            row4h["score"]
        )

        atr_value = safe_float(
            row15["atr"]
        )

        if (
            not np.isfinite(
                atr_value
            )
            or atr_value <= 0
        ):
            continue

        buy_a = (
            score5 >= 5
            and score15 >= 5
            and score1h >= 5
            and row15["adx"] >= 20
            and row15["rsi"] >= 50
            and row15["macd"] > 0
        )

        sell_a = (
            score5 <= -5
            and score15 <= -5
            and score1h <= -5
            and row15["adx"] >= 20
            and row15["rsi"] <= 50
            and row15["macd"] < 0
        )

        buy_b = (
            bool(
                row15.get(
                    "retest_buy",
                    False
                )
            )
            and score5 >= 5
            and score15 >= 5
            and score1h >= 5
            and row15["adx"] >= 20
            and row15["rsi"] >= 50
            and row15["macd"] > 0
        )

        sell_b = (
            bool(
                row15.get(
                    "retest_sell",
                    False
                )
            )
            and score5 <= -5
            and score15 <= -5
            and score1h <= -5
            and row15["adx"] >= 20
            and row15["rsi"] <= 50
            and row15["macd"] < 0
        )

        signal = None

        if strategy == "A":

            if buy_a:
                signal = "BUY"

            elif sell_a:
                signal = "SELL"

        elif strategy == "B2":

            if buy_b:
                signal = "BUY"

            elif sell_b:
                signal = "SELL"

        if signal is None:
            continue

        next_row = (
            m5_slice.iloc[i + 1]
        )

        entry = float(
            next_row["open"]
        )

        if signal == "BUY":

            sl = (
                entry -
                1.5 * atr_value
            )

            tp = (
                entry +
                2.5 * atr_value
            )

        else:

            sl = (
                entry +
                1.5 * atr_value
            )

            tp = (
                entry -
                2.5 * atr_value
            )

        open_trade = {
            "entry_time":
                m5_slice.index[i + 1],
            "side": signal,
            "strategy": strategy,
            "entry": entry,
            "sl": sl,
            "tp": tp,
        }

    # ========================================================
    # CLOSE REMAINING TRADE AT LAST CLOSE
    # ========================================================

    if open_trade is not None:

        final_time = (
            m5_slice.index[-1]
        )

        final_close = float(
            m5_slice.iloc[-1][
                "close"
            ]
        )

        side = open_trade[
            "side"
        ]

        entry = open_trade[
            "entry"
        ]

        risk_distance = abs(
            entry -
            open_trade["sl"]
        )

        if side == "BUY":
            result_r = (
                final_close -
                entry
            ) / risk_distance

        else:
            result_r = (
                entry -
                final_close
            ) / risk_distance

        trades.append({
            "entry_time":
                open_trade[
                    "entry_time"
                ],
            "exit_time":
                final_time,
            "side": side,
            "strategy":
                open_trade[
                    "strategy"
                ],
            "entry": entry,
            "exit": final_close,
            "sl":
                open_trade[
                    "sl"
                ],
            "tp":
                open_trade[
                    "tp"
                ],
            "result_r":
                float(
                    result_r
                ),
            "reason": "END",
        })

    return pd.DataFrame(
        trades
    )


# ============================================================
# METRICS
# ============================================================

def calculate_metrics(
    trades
):
    if (
        trades is None
        or trades.empty
    ):
        return {
            "trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "total_r": 0.0,
            "avg_r": 0.0,
            "max_dd": 0.0,
        }

    result = trades[
        "result_r"
    ].astype(float)

    wins = result[
        result > 0
    ]

    losses = result[
        result < 0
    ]

    gross_profit = (
        wins.sum()
    )

    gross_loss = abs(
        losses.sum()
    )

    if gross_loss > 0:
        profit_factor = (
            gross_profit /
            gross_loss
        )

    elif gross_profit > 0:
        profit_factor = np.inf

    else:
        profit_factor = 0.0

    equity = result.cumsum()

    running_max = (
        equity.cummax()
    )

    drawdown = (
        running_max -
        equity
    )

    max_dd = (
        drawdown.max()
        if not drawdown.empty
        else 0.0
    )

    return {
        "trades": len(
            trades
        ),
        "win_rate": (
            result.gt(0).mean()
            * 100
        ),
        "profit_factor":
            float(
                profit_factor
            ),
        "total_r":
            float(
                result.sum()
            ),
        "avg_r":
            float(
                result.mean()
            ),
        "max_dd":
            float(
                max_dd
            ),
    }


# ============================================================
# UI HEADER
# ============================================================

st.markdown(
    '<div class="main-title">🥇 بوت الذهب XAU/USD</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="sub-title">'
    'Multi-Timeframe Smart Paper Trading System'
    '</div>',
    unsafe_allow_html=True,
)

# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.markdown(
        "## ⚙️ إعدادات البوت"
    )

    risk_percent = st.slider(
        "المخاطرة لكل صفقة %",
        min_value=0.25,
        max_value=2.0,
        value=1.0,
        step=0.25,
    )

    st.markdown("---")

    if st.button(
        "🔄 تحديث البيانات",
        use_container_width=True,
    ):
        get_candles.clear()
        st.rerun()

    if st.button(
        "🛑 Kill Switch",
        use_container_width=True,
    ):
        st.session_state.kill_switch = True

        log_event(
            "KILL_SWITCH",
            "تم إيقاف Paper Trading يدويًا.",
            "WARNING",
        )

    if st.button(
        "▶️ تشغيل البوت",
        use_container_width=True,
    ):
        st.session_state.kill_switch = False

        log_event(
            "BOT_RESUMED",
            "تم تشغيل Paper Trading.",
            "INFO",
        )

    st.markdown("---")

    if st.session_state.kill_switch:
        st.error(
            "🛑 البوت متوقف"
        )
    else:
        st.success(
            "🟢 البوت يعمل"
        )

    st.caption(
        "Paper Trading فقط — لا توجد أوامر حقيقية."
    )

# ============================================================
# TABS
# ============================================================

tab_live, tab_paper, tab_backtest, tab_logs = (
    st.tabs(
        [
            "📡 Live Analysis",
            "💰 Paper Trading",
            "🧪 Backtest",
            "📋 Logs",
        ]
    )
)

# ============================================================
# LIVE TAB
# ============================================================

with tab_live:

    st.markdown(
        "## 📡 التحليل المباشر"
    )

    try:

        m5_raw = get_candles(
            "5min",
            500
        )

        live = analyze_live(
            m5_raw
        )

        # ====================================================
        # PAPER UPDATE
        # ====================================================

        completed_m5 = get_completed_m5(
            m5_raw
        )

        if (
            not completed_m5.empty
            and
            st.session_state.paper_trade
            is not None
        ):
            last_completed = (
                completed_m5.iloc[-1]
            )

            update_paper_trade(
                last_completed
            )

        # ====================================================
        # HEADER METRICS
        # ====================================================

        c1, c2, c3, c4 = (
            st.columns(4)
        )

        with c1:
            st.metric(
                "XAU/USD",
                f"{live['price']:,.2f}"
            )

        with c2:

            if live["signal"] == "BUY":
                text = "🟢 BUY"

            elif live["signal"] == "SELL":
                text = "🔴 SELL"

            else:
                text = "⚪ WAIT"

            st.metric(
                "الإشارة",
                text,
            )

        with c3:
            st.metric(
                "قوة التوافق",
                f"{live['strength']:.1f}%"
            )

        with c4:
            st.metric(
                "Regime",
                live["regime"]
            )

        # ====================================================
        # SIGNAL
        # ====================================================

        if live["signal"] == "BUY":

            st.markdown(
                '<div class="signal-buy">🟢 BUY</div>',
                unsafe_allow_html=True,
            )

        elif live["signal"] == "SELL":

            st.markdown(
                '<div class="signal-sell">🔴 SELL</div>',
                unsafe_allow_html=True,
            )

        else:

            st.markdown(
                '<div class="signal-wait">⚪ WAIT</div>',
                unsafe_allow_html=True,
            )

        st.caption(
            f"Strategy: {live['strategy']} | "
            f"آخر شمعة مكتملة: {live['time']}"
        )

        st.caption(
            "التحديث: بيانات جديدة كل 60 ثانية أثناء فتح الصفحة."
        )

        # ====================================================
        # LEVELS
        # ====================================================

        st.markdown(
            "### 🎯 مستويات الصفقة"
        )

        l1, l2, l3, l4 = (
            st.columns(4)
        )

        l1.metric(
            "Entry",
            f"{live['entry']:,.2f}"
        )

        l2.metric(
            "SL",
            (
                f"{live['sl']:,.2f}"
                if np.isfinite(
                    live["sl"]
                )
                else "-"
            )
        )

        l3.metric(
            "TP1",
            (
                f"{live['tp1']:,.2f}"
                if np.isfinite(
                    live["tp1"]
                )
                else "-"
            )
        )

        l4.metric(
            "TP2",
            (
                f"{live['tp2']:,.2f}"
                if np.isfinite(
                    live["tp2"]
                )
                else "-"
            )
        )

        # ====================================================
        # TIMEFRAMES
        # ====================================================

        st.markdown(
            "### 🧭 تحليل الفريمات"
        )

        tf = pd.DataFrame(
            [
                [
                    "M5",
                    live["trend5"],
                    live["score5"],
                ],
                [
                    "M15",
                    live["trend15"],
                    live["score15"],
                ],
                [
                    "H1",
                    live["trend1h"],
                    live["score1h"],
                ],
                [
                    "H4",
                    live["trend4h"],
                    live["score4h"],
                ],
            ],
            columns=[
                "Timeframe",
                "Trend",
                "Score",
            ],
        )

        st.dataframe(
            tf,
            use_container_width=True,
            hide_index=True,
        )

        # ====================================================
        # INDICATORS
        # ====================================================

        st.markdown(
            "### 📐 المؤشرات"
        )

        i1, i2, i3, i4 = (
            st.columns(4)
        )

        i1.metric(
            "RSI M15",
            f"{live['rsi']:.2f}"
        )

        i2.metric(
            "ADX M15",
            f"{live['adx']:.2f}"
        )

        i3.metric(
            "ATR M15",
            f"{live['atr']:.2f}"
        )

        total_score = (
            live["score5"] +
            live["score15"] +
            live["score1h"] +
            live["score4h"]
        )

        i4.metric(
            "Confluence",
            f"{total_score:+.0f}"
        )

        # ====================================================
        # SUPPORT / RESISTANCE
        # ====================================================

        s1, s2 = (
            st.columns(2)
        )

        s1.metric(
            "Support",
            f"{live['support']:,.2f}"
        )

        s2.metric(
            "Resistance",
            f"{live['resistance']:,.2f}"
        )

        # ====================================================
        # PAPER AUTO OPEN
        # ====================================================

        if (
            live["signal"]
            in [
                "BUY",
                "SELL",
            ]
            and not
            st.session_state.kill_switch
            and
            st.session_state.paper_trade
            is None
        ):

            # Prevent repeatedly opening the same signal
            signal_time = str(
                live["time"]
            )

            if (
                st.session_state.last_signal_time
                != signal_time
            ):

                open_paper_trade(
                    live,
                    risk_percent,
                )

                st.session_state.last_signal_time = (
                    signal_time
                )

        # ====================================================
        # CURRENT PAPER POSITION
        # ====================================================

        if (
            st.session_state.paper_trade
            is not None
        ):

            trade = (
                st.session_state.paper_trade
            )

            st.markdown(
                "### 📌 Paper Trade الحالية"
            )

            open_df = pd.DataFrame(
                [
                    {
                        "Side":
                            trade["side"],
                        "Strategy":
                            trade["strategy"],
                        "Entry":
                            trade["entry"],
                        "SL":
                            trade["sl"],
                        "TP1":
                            trade["tp1"],
                        "TP2":
                            trade["tp2"],
                        "Risk $":
                            trade["risk_amount"],
                    }
                ]
            )

            st.dataframe(
                open_df,
                use_container_width=True,
                hide_index=True,
            )

        # ====================================================
        # CHART
        # ====================================================

        st.markdown(
            "### 📈 حركة XAU/USD"
        )

        chart_df = (
            completed_m5[
                ["close"]
            ].tail(150)
        )

        st.line_chart(
            chart_df,
            use_container_width=True,
        )

        # ====================================================
        # INFO
        # ====================================================

        st.info(
            "تنبيه: قوة التوافق مؤشر فني وليست احتمال ربح. "
            "النظام تجريبي Paper Trading فقط ولا ينفذ أوامر حقيقية."
        )

    except Exception as e:

        log_event(
            "DATA_ERROR",
            str(e),
            "ERROR",
        )

        st.error(
            f"خطأ في البيانات: {e}"
        )

# ============================================================
# PAPER TRADING TAB
# ============================================================

with tab_paper:

    st.markdown(
        "## 💰 Paper Trading"
    )

    pnl = (
        st.session_state.paper_balance -
        st.session_state.paper_start_balance
    )

    p1, p2, p3, p4 = (
        st.columns(4)
    )

    p1.metric(
        "الرصيد",
        f"${st.session_state.paper_balance:,.2f}"
    )

    p2.metric(
        "P&L",
        f"${pnl:+,.2f}"
    )

    p3.metric(
        "Trades",
        len(
            st.session_state.paper_history
        )
    )

    status = (
        "🛑 STOPPED"
        if st.session_state.kill_switch
        else "🟢 RUNNING"
    )

    p4.metric(
        "Status",
        status
    )

    if (
        st.session_state.paper_trade
        is not None
    ):

        trade = (
            st.session_state.paper_trade
        )

        st.markdown(
            "### 📌 الصفقة المفتوحة"
        )

        open_df = pd.DataFrame(
            [
                {
                    "Side":
                        trade["side"],
                    "Strategy":
                        trade["strategy"],
                    "Entry":
                        trade["entry"],
                    "SL":
                        trade["sl"],
                    "TP1":
                        trade["tp1"],
                    "TP2":
                        trade["tp2"],
                    "Risk $":
                        trade["risk_amount"],
                }
            ]
        )

        st.dataframe(
            open_df,
            use_container_width=True,
            hide_index=True,
        )

    else:

        st.info(
            "لا توجد صفقة Paper مفتوحة."
        )

    if st.session_state.paper_history:

        st.markdown(
            "### 📚 سجل الصفقات"
        )

        history = pd.DataFrame(
            st.session_state.paper_history
        )

        st.dataframe(
            history,
            use_container_width=True,
            hide_index=True,
        )

        csv = history.to_csv(
            index=False
        ).encode("utf-8")

        st.download_button(
            "⬇️ تحميل سجل Paper Trading CSV",
            data=csv,
            file_name="paper_trading_history.csv",
            mime="text/csv",
            use_container_width=True,
        )

# ============================================================
# BACKTEST TAB
# ============================================================

with tab_backtest:

    st.markdown(
        "## 🧪 Backtest"
    )

    bars = st.selectbox(
        "عدد شموع M5",
        [
            5000,
            10000,
            15000,
            30000,
        ],
        index=0,
    )

    run_backtest = st.button(
        "▶️ تشغيل Backtest",
        use_container_width=True,
    )

    if run_backtest:

        with st.spinner(
            "جاري تحميل البيانات وتشغيل الاختبار..."
        ):

            try:

                historical = get_candles(
                    "5min",
                    bars,
                )

                if len(historical) < 1000:

                    st.warning(
                        "عدد البيانات أقل من المطلوب لاختبار قوي."
                    )

                (
                    m5_bt,
                    m15_bt,
                    h1_bt,
                    h4_bt,
                ) = prepare_backtest_data(
                    historical
                )

                split_index = int(
                    len(m5_bt) * 0.70
                )

                split_time = (
                    m5_bt.index[
                        split_index
                    ]
                )

                is_start = (
                    m5_bt.index[0]
                )

                is_end = (
                    split_time
                )

                oos_start = (
                    split_time
                )

                oos_end = (
                    m5_bt.index[-1]
                )

                # =================================================
                # Strategy A
                # =================================================

                is_a = simulate_strategy(
                    m5_bt,
                    m15_bt,
                    h1_bt,
                    h4_bt,
                    strategy="A",
                    start=is_start,
                    end=is_end,
                )

                oos_a = simulate_strategy(
                    m5_bt,
                    m15_bt,
                    h1_bt,
                    h4_bt,
                    strategy="A",
                    start=oos_start,
                    end=oos_end,
                )

                # =================================================
                # Strategy B2
                # =================================================

                is_b = simulate_strategy(
                    m5_bt,
                    m15_bt,
                    h1_bt,
                    h4_bt,
                    strategy="B2",
                    start=is_start,
                    end=is_end,
                )

                oos_b = simulate_strategy(
                    m5_bt,
                    m15_bt,
                    h1_bt,
                    h4_bt,
                    strategy="B2",
                    start=oos_start,
                    end=oos_end,
                )

                is_a_m = calculate_metrics(
                    is_a
                )

                oos_a_m = calculate_metrics(
                    oos_a
                )

                is_b_m = calculate_metrics(
                    is_b
                )

                oos_b_m = calculate_metrics(
                    oos_b
                )

                st.success(
                    f"الفترة: "
                    f"{m5_bt.index[0]} → "
                    f"{m5_bt.index[-1]}"
                )

                st.info(
                    f"OOS يبدأ: {split_time}"
                )

                # =================================================
                # A RESULTS
                # =================================================

                st.markdown(
                    "### A — Trend Confluence"
                )

                a1, a2, a3, a4, a5 = (
                    st.columns(5)
                )

                a1.metric(
                    "OOS Trades",
                    oos_a_m["trades"]
                )

                a2.metric(
                    "Win Rate",
                    f"{oos_a_m['win_rate']:.1f}%"
                )

                pf_text = (
                    "∞"
                    if np.isinf(
                        oos_a_m[
                            "profit_factor"
                        ]
                    )
                    else
                    f"{oos_a_m['profit_factor']:.2f}"
                )

                a3.metric(
                    "Profit Factor",
                    pf_text
                )

                a4.metric(
                    "Total R",
                    f"{oos_a_m['total_r']:+.2f}R"
                )

                a5.metric(
                    "Max DD",
                    f"{oos_a_m['max_dd']:.2f}R"
                )

                # =================================================
                # B2 RESULTS
                # =================================================

                st.markdown(
                    "### B2 — True Breakout + Retest"
                )

                b1, b2, b3, b4, b5 = (
                    st.columns(5)
                )

                b1.metric(
                    "OOS Trades",
                    oos_b_m["trades"]
                )

                b2.metric(
                    "Win Rate",
                    f"{oos_b_m['win_rate']:.1f}%"
                )

                pf_text_b = (
                    "∞"
                    if np.isinf(
                        oos_b_m[
                            "profit_factor"
                        ]
                    )
                    else
                    f"{oos_b_m['profit_factor']:.2f}"
                )

                b3.metric(
                    "Profit Factor",
                    pf_text_b
                )

                b4.metric(
                    "Total R",
                    f"{oos_b_m['total_r']:+.2f}R"
                )

                b5.metric(
                    "Max DD",
                    f"{oos_b_m['max_dd']:.2f}R"
                )

                # =================================================
                # COMPARISON
                # =================================================

                st.markdown(
                    "### 📊 مقارنة الاختبار"
                )

                comparison = pd.DataFrame(
                    [
                        {
                            "Strategy":
                                "A",
                            "IS Trades":
                                is_a_m["trades"],
                            "IS Win Rate":
                                f"{is_a_m['win_rate']:.1f}%",
                            "IS PF":
                                (
                                    "∞"
                                    if np.isinf(
                                        is_a_m[
                                            "profit_factor"
                                        ]
                                    )
                                    else
                                    f"{is_a_m['profit_factor']:.2f}"
                                ),
                            "IS Total R":
                                f"{is_a_m['total_r']:+.2f}",
                            "OOS Trades":
                                oos_a_m["trades"],
                            "OOS Win Rate":
                                f"{oos_a_m['win_rate']:.1f}%",
                            "OOS PF":
                                (
                                    "∞"
                                    if np.isinf(
                                        oos_a_m[
                                            "profit_factor"
                                        ]
                                    )
                                    else
                                    f"{oos_a_m['profit_factor']:.2f}"
                                ),
                            "OOS Total R":
                                f"{oos_a_m['total_r']:+.2f}",
                            "OOS Max DD":
                                f"{oos_a_m['max_dd']:.2f}",
                        },
                        {
                            "Strategy":
                                "B2",
                            "IS Trades":
                                is_b_m["trades"],
                            "IS Win Rate":
                                f"{is_b_m['win_rate']:.1f}%",
                            "IS PF":
                                (
                                    "∞"
                                    if np.isinf(
                                        is_b_m[
                                            "profit_factor"
                                        ]
                                    )
                                    else
                                    f"{is_b_m['profit_factor']:.2f}"
                                ),
                            "IS Total R":
                                f"{is_b_m['total_r']:+.2f}",
                            "OOS Trades":
                                oos_b_m["trades"],
                            "OOS Win Rate":
                                f"{oos_b_m['win_rate']:.1f}%",
                            "OOS PF":
                                (
                                    "∞"
                                    if np.isinf(
                                        oos_b_m[
                                            "profit_factor"
                                        ]
                                    )
                                    else
                                    f"{oos_b_m['profit_factor']:.2f}"
                                ),
                            "OOS Total R":
                                f"{oos_b_m['total_r']:+.2f}",
                            "OOS Max DD":
                                f"{oos_b_m['max_dd']:.2f}",
                        },
                    ]
                )

                st.dataframe(
                    comparison,
                    use_container_width=True,
                    hide_index=True,
                )

                # =================================================
                # EQUITY
                # =================================================

                st.markdown(
                    "### 📈 OOS Equity"
                )

                eq_a = (
                    oos_a[
                        "result_r"
                    ].cumsum()
                    if not oos_a.empty
                    else pd.Series(
                        dtype=float
                    )
                )

                eq_b = (
                    oos_b[
                        "result_r"
                    ].cumsum()
                    if not oos_b.empty
                    else pd.Series(
                        dtype=float
                    )
                )

                equity = pd.DataFrame(
                    {
                        "A":
                            eq_a.reset_index(
                                drop=True
                            ),
                        "B2":
                            eq_b.reset_index(
                                drop=True
                            ),
                    }
                )

                st.line_chart(
                    equity,
                    use_container_width=True,
                )

                # =================================================
                # WARNINGS
                # =================================================

                if (
                    oos_a_m["trades"] <
                    100
                ):
                    st.warning(
                        "OOS Strategy A أقل من 100 صفقة: "
                        "العينة محدودة."
                    )

                if (
                    oos_b_m["trades"] <
                    100
                ):
                    st.warning(
                        "OOS Strategy B2 أقل من 100 صفقة: "
                        "لا تعتبر النتيجة إثباتًا نهائيًا."
                    )

                st.caption(
                    "الاختبار لا يحاكي تنفيذ الوسيط الحقيقي، "
                    "ولا يشمل حاليًا السبريد والعمولة والانزلاق السعري."
                )

                # =================================================
                # TRADE TABLES
                # =================================================

                with st.expander(
                    "عرض صفقات OOS — A"
                ):

                    if oos_a.empty:
                        st.info(
                            "لا توجد صفقات."
                        )

                    else:
                        st.dataframe(
                            oos_a,
                            use_container_width=True,
                            hide_index=True,
                        )

                with st.expander(
                    "عرض صفقات OOS — B2"
                ):

                    if oos_b.empty:
                        st.info(
                            "لا توجد صفقات."
                        )

                    else:
                        st.dataframe(
                            oos_b,
                            use_container_width=True,
                            hide_index=True,
                        )

            except Exception as e:

                log_event(
                    "BACKTEST_ERROR",
                    str(e),
                    "ERROR",
                )

                st.error(
                    f"خطأ في Backtest: {e}"
                )

# ============================================================
# LOGS TAB
# ============================================================

with tab_logs:

    st.markdown(
        "## 📋 Decision & System Logs"
    )

    if not st.session_state.decision_log:

        st.info(
            "لا توجد سجلات حتى الآن."
        )

    else:

        logs = pd.DataFrame(
            st.session_state.decision_log
        )

        st.dataframe(
            logs.iloc[::-1],
            use_container_width=True,
            hide_index=True,
        )

        csv = logs.to_csv(
            index=False
        ).encode("utf-8")

        st.download_button(
            "⬇️ تحميل Logs CSV",
            data=csv,
            file_name="bot_logs.csv",
            mime="text/csv",
            use_container_width=True,
        )

# ============================================================
# FOOTER
# ============================================================

st.markdown("---")

st.markdown(
    """
<div class="small-note">
<b>بوت الذهب XAU/USD</b><br>
Research / Paper Trading فقط — لا يتم إرسال أي أوامر حقيقية إلى وسيط.
النتائج التاريخية لا تضمن النتائج المستقبلية.
</div>
""",
    unsafe_allow_html=True,
)

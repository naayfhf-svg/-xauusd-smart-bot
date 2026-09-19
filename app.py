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
# GOLD AI — XAU/USD SMART PAPER TRADING TERMINAL
# PAPER TRADING ONLY — NO REAL MONEY EXECUTION
# ============================================================

st.set_page_config(
    page_title="بوت الذهب XAU/USD",
    page_icon="🥇",
    layout="wide",
)


# ============================================================
# CONFIG
# ============================================================

SYMBOL = "XAU/USD"
API_URL = "https://api.twelvedata.com/time_series"

START_BALANCE = 10_000.0

RIYADH = ZoneInfo("Asia/Riyadh")

DEFAULT_RISK = 1.0
DEFAULT_DAILY_LOSS = 3.0
DEFAULT_MAX_TRADES = 5

SPREAD = 0.20
SLIPPAGE = 0.05

SL_ATR = 1.50
TP1_ATR = 1.50
TP2_ATR = 2.50

RETEST_ATR = 0.35


# ============================================================
# STYLE
# ============================================================

st.markdown(
    """
<style>

[data-testid="stAppViewContainer"] {
    background:
        radial-gradient(
            circle at top right,
            rgba(212,175,55,.08),
            transparent 28%
        ),
        #080d16;
    color: #f4f6f8;
}

[data-testid="stHeader"] {
    background: rgba(8,13,22,.92);
}

.block-container {
    max-width: 1450px;
    padding-top: 1rem;
    padding-bottom: 3rem;
}

.hero {
    background:
        linear-gradient(
            135deg,
            #111827,
            #0b1220
        );
    border: 1px solid #263244;
    border-radius: 22px;
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
    font-size: 36px;
    font-weight: 900;
    margin-top: 6px;
}

.hero-sub {
    color: #8e9aaa;
    margin-top: 6px;
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
    font-size: 24px;
    font-weight: 900;
}

.price {
    font-size: 42px;
    font-weight: 900;
    color: #ffffff;
}

.title {
    font-size: 21px;
    font-weight: 900;
    margin-top: 28px;
    margin-bottom: 12px;
}

.muted {
    color: #8e9aaa;
}

.gold {
    color: #d4af37;
}

.green {
    color: #45d483;
}

.red {
    color: #ff6474;
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

.badge {
    display: inline-block;
    padding: 5px 10px;
    border-radius: 999px;
    border: 1px solid #334155;
    background: #111827;
    font-size: 11px;
    font-weight: 700;
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
    font-weight: 800;
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
        "balance": START_BALANCE,
        "history": [],
        "logs": [],
        "position": None,
        "pending": None,
        "kill": False,
        "last_candle": None,
        "last_price": None,
        "last_log_key": None,
        "day": datetime.now(RIYADH).date().isoformat(),
        "day_start": START_BALANCE,
        "daily_trades": 0,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_state()


# ============================================================
# UTILITIES
# ============================================================

def now():
    return datetime.now(RIYADH)


def fprice(value):
    if value is None or pd.isna(value):
        return "—"

    return f"{float(value):,.2f}"


def fmoney(value):
    if value is None or pd.isna(value):
        return "—"

    return f"${float(value):,.2f}"


def reset_day():
    today = now().date().isoformat()

    if st.session_state.day != today:
        st.session_state.day = today
        st.session_state.day_start = st.session_state.balance
        st.session_state.daily_trades = 0


reset_day()


# ============================================================
# DATA
# ============================================================

@st.cache_data(ttl=30, show_spinner=False)
def fetch_data(api_key, outputsize=5000):

    if not api_key:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY غير موجود في Secrets."
        )

    params = {
        "symbol": SYMBOL,
        "interval": "5min",
        "outputsize": outputsize,
        "timezone": "UTC",
        "apikey": api_key,
        "format": "JSON",
    }

    response = requests.get(
        API_URL,
        params=params,
        timeout=20,
    )

    response.raise_for_status()

    data = response.json()

    if data.get("status") == "error":
        raise RuntimeError(
            data.get(
                "message",
                "Twelve Data error",
            )
        )

    values = data.get("values")

    if not values:
        raise RuntimeError(
            "لم تصل بيانات سعرية من Twelve Data."
        )

    df = pd.DataFrame(values)

    if "datetime" not in df.columns:
        raise RuntimeError(
            "بيانات Twelve Data غير صالحة."
        )

    df["datetime"] = pd.to_datetime(
        df["datetime"],
        utc=True,
    )

    for column in [
        "open",
        "high",
        "low",
        "close",
    ]:
        if column not in df.columns:
            raise RuntimeError(
                f"البيانات تفتقد العمود {column}"
            )

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(
            df["volume"],
            errors="coerce",
        )
    else:
        df["volume"] = np.nan

    df = (
        df
        .dropna(
            subset=[
                "open",
                "high",
                "low",
                "close",
            ]
        )
        .sort_values("datetime")
        .drop_duplicates(
            "datetime"
        )
        .set_index("datetime")
    )

    return df


def completed(df):

    if df.empty:
        return df

    current = (
        pd.Timestamp
        .now(tz="UTC")
        .floor("5min")
    )

    return df[
        df.index < current
    ].copy()


def data_quality(df):

    if df.empty:
        return {
            "valid": False,
            "rows": 0,
            "gaps": 0,
            "stale": None,
            "reason": "لا توجد بيانات",
        }

    index = df.index.sort_values()

    differences = (
        index.to_series()
        .diff()
        .dt.total_seconds()
        .div(60)
    )

    # Count suspicious intraday gaps only.
    # Long closures are ignored.
    gaps = int(
        (
            (differences > 5)
            & (differences <= 180)
        ).sum()
    )

    stale = (
        (
            pd.Timestamp.now(tz="UTC")
            - index[-1]
        )
        .total_seconds()
        / 60
    )

    valid = (
        len(df) >= 500
        and stale <= 20
        and gaps < max(
            20,
            int(len(df) * 0.02),
        )
    )

    if valid:
        reason = "OK"

    elif stale > 20:
        reason = "البيانات متأخرة"

    else:
        reason = "جودة البيانات غير كافية"

    return {
        "valid": valid,
        "rows": len(df),
        "gaps": gaps,
        "stale": stale,
        "reason": reason,
    }


# ============================================================
# INDICATORS
# ============================================================

def calculate_rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()

    rs = (
        avg_gain
        / avg_loss.replace(
            0,
            np.nan,
        )
    )

    return 100 - (
        100 / (1 + rs)
    )


def add_indicators(df):

    x = df.copy()

    x["ema20"] = (
        x["close"]
        .ewm(
            span=20,
            adjust=False,
        )
        .mean()
    )

    x["ema50"] = (
        x["close"]
        .ewm(
            span=50,
            adjust=False,
        )
        .mean()
    )

    x["ema100"] = (
        x["close"]
        .ewm(
            span=100,
            adjust=False,
        )
        .mean()
    )

    x["rsi"] = calculate_rsi(
        x["close"],
        14,
    )

    previous_close = x[
        "close"
    ].shift(1)

    true_range = pd.concat(
        [
            x["high"] - x["low"],
            (
                x["high"]
                - previous_close
            ).abs(),
            (
                x["low"]
                - previous_close
            ).abs(),
        ],
        axis=1,
    ).max(axis=1)

    x["atr"] = (
        true_range
        .ewm(
            alpha=1 / 14,
            min_periods=14,
            adjust=False,
        )
        .mean()
    )

    ema12 = (
        x["close"]
        .ewm(
            span=12,
            adjust=False,
        )
        .mean()
    )

    ema26 = (
        x["close"]
        .ewm(
            span=26,
            adjust=False,
        )
        .mean()
    )

    x["macd"] = (
        ema12 - ema26
    )

    x["macd_signal"] = (
        x["macd"]
        .ewm(
            span=9,
            adjust=False,
        )
        .mean()
    )

    x["momentum"] = (
        x["close"].diff(10)
    )

    up_move = x["high"].diff()

    down_move = -x["low"].diff()

    plus_dm = pd.Series(
        np.where(
            (
                (up_move > down_move)
                & (up_move > 0)
            ),
            up_move,
            0.0,
        ),
        index=x.index,
    )

    minus_dm = pd.Series(
        np.where(
            (
                (down_move > up_move)
                & (down_move > 0)
            ),
            down_move,
            0.0,
        ),
        index=x.index,
    )

    atr = x["atr"]

    x["plus_di"] = (
        100
        * plus_dm.ewm(
            alpha=1 / 14,
            min_periods=14,
            adjust=False,
        ).mean()
        / atr.replace(
            0,
            np.nan,
        )
    )

    x["minus_di"] = (
        100
        * minus_dm.ewm(
            alpha=1 / 14,
            min_periods=14,
            adjust=False,
        ).mean()
        / atr.replace(
            0,
            np.nan,
        )
    )

    x["adx"] = (
        100
        * (
            x["plus_di"]
            - x["minus_di"]
        ).abs()
        / (
            x["plus_di"]
            + x["minus_di"]
        ).replace(
            0,
            np.nan,
        )
    )

    x["adx"] = (
        x["adx"]
        .ewm(
            alpha=1 / 14,
            min_periods=14,
            adjust=False,
        )
        .mean()
    )

    return x.dropna()


# ============================================================
# TIMEFRAME RESAMPLING
# ============================================================

def timeframe(df, minutes):

    result = (
        df.resample(
            f"{minutes}min",
            label="right",
            closed="left",
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
    )

    return result.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )


# ============================================================
# SCORING
# ============================================================

def timeframe_score(row):

    score = 0

    if (
        row.ema20
        > row.ema50
        > row.ema100
    ):
        score += 3

    elif (
        row.ema20
        < row.ema50
        < row.ema100
    ):
        score -= 3

    if row.close > row.ema20:
        score += 1

    else:
        score -= 1

    if 52 <= row.rsi <= 68:
        score += 2

    elif 32 <= row.rsi < 48:
        score -= 2

    if row.macd > row.macd_signal:
        score += 2

    else:
        score -= 2

    if row.momentum > 0:
        score += 1

    else:
        score -= 1

    if row.adx >= 25:

        if row.plus_di > row.minus_di:
            score += 2

        elif row.minus_di > row.plus_di:
            score -= 2

    return int(score)


def trend_label(score):

    if score >= 4:
        return "BULLISH"

    if score <= -4:
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# MARKET REGIME
# ============================================================

def market_regime(row):

    if (
        row.adx >= 30
        and row.close
        > row.ema20
        > row.ema50
        > row.ema100
    ):
        return "TRENDING BULLISH"

    if (
        row.adx >= 30
        and row.close
        < row.ema20
        < row.ema50
        < row.ema100
    ):
        return "TRENDING BEARISH"

    if row.adx < 18:
        return "LOW TREND STRENGTH"

    return "TRANSITION"


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def support_resistance(
    df,
    lookback=80,
):

    x = df.tail(lookback)

    if len(x) < 20:
        return None, None

    support = float(
        x["low"].min()
    )

    resistance = float(
        x["high"].max()
    )

    return support, resistance


# ============================================================
# B2 BREAKOUT / RETEST
# ============================================================

def detect_b2(df):

    if len(df) < 35:

        return {
            "valid": False,
            "direction": None,
            "breakout": False,
            "retest": False,
            "level": None,
            "reason": "بيانات غير كافية",
        }

    latest = df.iloc[-1]

    atr = float(
        latest["atr"]
    )

    if atr <= 0:

        return {
            "valid": False,
            "direction": None,
            "breakout": False,
            "retest": False,
            "level": None,
            "reason": "ATR غير صالح",
        }

    for i in range(2, 8):

        breakout = df.iloc[-i]

        previous = df.iloc[
            -i - 1
        ]

        # ---------------- BUY ----------------

        if (
            breakout["close"]
            > previous["high"]
            and breakout["high"]
            > previous["high"]
        ):

            level = float(
                previous["high"]
            )

            retest = (
                latest["low"]
                <= level
                + atr * RETEST_ATR
                and latest["close"]
                >= level
            )

            if retest:

                return {
                    "valid": True,
                    "direction": "BUY",
                    "breakout": True,
                    "retest": True,
                    "level": level,
                    "reason":
                        "B2 BUY breakout + retest",
                }

        # ---------------- SELL ----------------

        if (
            breakout["close"]
            < previous["low"]
            and breakout["low"]
            < previous["low"]
        ):

            level = float(
                previous["low"]
            )

            retest = (
                latest["high"]
                >= level
                - atr * RETEST_ATR
                and latest["close"]
                <= level
            )

            if retest:

                return {
                    "valid": True,
                    "direction": "SELL",
                    "breakout": True,
                    "retest": True,
                    "level": level,
                    "reason":
                        "B2 SELL breakout + retest",
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

def analyze_market(
    m5,
    m15,
    h1,
    h4,
):

    rows = {
        "M5": m5.iloc[-1],
        "M15": m15.iloc[-1],
        "H1": h1.iloc[-1],
        "H4": h4.iloc[-1],
    }

    scores = {
        key: timeframe_score(
            value
        )
        for key, value in rows.items()
    }

    labels = {
        key: trend_label(
            value
        )
        for key, value in scores.items()
    }

    regime = market_regime(
        rows["H1"]
    )

    b2 = detect_b2(
        m15
    )

    buy_alignment = (
        scores["M5"] >= 4
        and scores["M15"] >= 4
        and scores["H1"] >= 4
    )

    sell_alignment = (
        scores["M5"] <= -4
        and scores["M15"] <= -4
        and scores["H1"] <= -4
    )

    buy_momentum = (
        rows["M15"].rsi > 50
        and rows["M15"].macd
        > rows["M15"].macd_signal
        and rows["M15"].momentum > 0
    )

    sell_momentum = (
        rows["M15"].rsi < 50
        and rows["M15"].macd
        < rows["M15"].macd_signal
        and rows["M15"].momentum < 0
    )

    # No news provider is connected.
    # Therefore the news gate BLOCKS entries.
    news_valid = False

    reasons = [
        (
            "DATA",
            True,
            "بيانات موجودة",
        ),
        (
            "REGIME",
            regime.startswith(
                "TRENDING"
            ),
            regime,
        ),
        (
            "MTF",
            buy_alignment
            or sell_alignment,
            "M5 / M15 / H1",
        ),
        (
            "MOMENTUM",
            buy_momentum
            or sell_momentum,
            "M15 momentum",
        ),
        (
            "B2",
            b2["valid"],
            b2["reason"],
        ),
        (
            "NEWS",
            news_valid,
            "News Engine غير متصل — BLOCK",
        ),
    ]

    signal = "WAIT"
    direction = None

    if all(
        passed
        for _, passed, _ in reasons
    ):

        if (
            b2["direction"]
            == "BUY"
            and buy_alignment
            and buy_momentum
        ):
            signal = "BUY"
            direction = "BUY"

        elif (
            b2["direction"]
            == "SELL"
            and sell_alignment
            and sell_momentum
        ):
            signal = "SELL"
            direction = "SELL"

    rejection = next(
        (
            detail
            for _, passed, detail in reasons
            if not passed
        ),
        "الشروط غير مكتملة",
    )

    support, resistance = (
        support_resistance(m15)
    )

    strength = min(
        100,
        max(
            0,
            50
            + sum(scores.values()) * 3,
        ),
    )

    return {
        "signal": signal,
        "direction": direction,
        "scores": scores,
        "labels": labels,
        "regime": regime,
        "b2": b2,
        "reasons": reasons,
        "rejection": rejection,
        "support": support,
        "resistance": resistance,
        "strength": strength,
    }


# ============================================================
# RISK
# ============================================================

def pnl_r(
    position,
    price,
):

    risk_distance = max(
        position["risk_distance"],
        1e-9,
    )

    if position["direction"] == "BUY":

        movement = (
            price
            - position["entry"]
        )

    else:

        movement = (
            position["entry"]
            - price
        )

    return (
        movement
        / risk_distance
        * position["remaining"]
    )


def daily_loss_pct():

    start = (
        st.session_state.day_start
    )

    pnl = (
        st.session_state.balance
        - start
    )

    position = (
        st.session_state.position
    )

    if (
        position
        and st.session_state.last_price
    ):

        unrealized = (
            pnl_r(
                position,
                st.session_state.last_price,
            )
            * position["risk_money"]
        )

        pnl += unrealized

    if pnl >= 0:
        return 0.0

    return (
        -pnl
        / start
        * 100
    )


# ============================================================
# PAPER TRADE ENGINE
# ============================================================

def create_trade(
    direction,
    price,
    risk_pct,
    atr,
):

    risk_money = (
        st.session_state.balance
        * risk_pct
        / 100
    )

    risk_distance = (
        atr
        * SL_ATR
    )

    if direction == "BUY":

        entry = (
            price
            + SPREAD / 2
            + SLIPPAGE
        )

        sl = (
            entry
            - risk_distance
        )

        tp1 = (
            entry
            + atr * TP1_ATR
        )

        tp2 = (
            entry
            + atr * TP2_ATR
        )

    else:

        entry = (
            price
            - SPREAD / 2
            - SLIPPAGE
        )

        sl = (
            entry
            + risk_distance
        )

        tp1 = (
            entry
            - atr * TP1_ATR
        )

        tp2 = (
            entry
            - atr * TP2_ATR
        )

    return {
        "id": str(
            uuid.uuid4()
        )[:8],

        "direction": direction,

        "entry": entry,

        "sl": sl,

        "tp1": tp1,

        "tp2": tp2,

        "risk_distance":
            risk_distance,

        "risk_money":
            risk_money,

        "risk_pct":
            risk_pct,

        "remaining": 1.0,

        "realized_r": 0.0,

        "tp1_hit": False,

        "entry_time":
            str(now()),

        "events": [],
    }


def close_trade(
    position,
    price,
    reason,
):

    final_r = pnl_r(
        position,
        price,
    )

    total_r = (
        position["realized_r"]
        + final_r
    )

    st.session_state.balance += (
        total_r
        * position["risk_money"]
    )

    position["realized_r"] = (
        total_r
    )

    position["exit_price"] = (
        price
    )

    position["exit_reason"] = (
        reason
    )

    position["exit_time"] = (
        str(now())
    )

    st.session_state.history.insert(
        0,
        position.copy(),
    )

    st.session_state.position = (
        None
    )


def manage_trade(
    position,
    candle,
):

    price = float(
        candle["close"]
    )

    if position["direction"] == "BUY":

        # Stop gets priority if both are touched.
        if (
            candle["low"]
            <= position["sl"]
        ):

            close_trade(
                position,
                position["sl"],
                "STOP LOSS",
            )

            return

        if (
            not position["tp1_hit"]
            and candle["high"]
            >= position["tp1"]
        ):

            tp1_r = (
                (
                    position["tp1"]
                    - position["entry"]
                )
                / position["risk_distance"]
            )

            position["realized_r"] += (
                0.5 * tp1_r
            )

            position["remaining"] = (
                0.5
            )

            position["tp1_hit"] = (
                True
            )

            position["sl"] = (
                position["entry"]
            )

            position["events"].append(
                "TP1 HIT • 50% CLOSED • SL→BE"
            )

        if (
            position["tp1_hit"]
            and candle["high"]
            >= position["tp2"]
        ):

            close_trade(
                position,
                position["tp2"],
                "TP2",
            )

            return

    else:

        if (
            candle["high"]
            >= position["sl"]
        ):

            close_trade(
                position,
                position["sl"],
                "STOP LOSS",
            )

            return

        if (
            not position["tp1_hit"]
            and candle["low"]
            <= position["tp1"]
        ):

            tp1_r = (
                (
                    position["entry"]
                    - position["tp1"]
                )
                / position["risk_distance"]
            )

            position["realized_r"] += (
                0.5 * tp1_r
            )

            position["remaining"] = (
                0.5
            )

            position["tp1_hit"] = (
                True
            )

            position["sl"] = (
                position["entry"]
            )

            position["events"].append(
                "TP1 HIT • 50% CLOSED • SL→BE"
            )

        if (
            position["tp1_hit"]
            and candle["low"]
            <= position["tp2"]
        ):

            close_trade(
                position,
                position["tp2"],
                "TP2",
            )


# ============================================================
# DECISION LOG
# ============================================================

def log_decision(signal):

    key = (
        f"{signal['signal']}-"
        f"{st.session_state.last_price}-"
        f"{signal['regime']}"
    )

    if (
        key
        == st.session_state.last_log_key
    ):
        return

    st.session_state.last_log_key = (
        key
    )

    st.session_state.logs.insert(
        0,
        {
            "time": str(now()),
            "signal": signal["signal"],
            "reason": signal["rejection"],
            "id": str(
                uuid.uuid4()
            )[:8],
        },
    )


# ============================================================
# BACKTEST
# ============================================================

def backtest_strategy(
    df,
    risk_pct=1.0,
):

    if len(df) < 500:

        return {
            "error":
                "بيانات الباك تست غير كافية",
            "trades":
                pd.DataFrame(),
        }

    data = df.copy()

    m15 = add_indicators(
        timeframe(
            data,
            15,
        )
    )

    h1 = add_indicators(
        timeframe(
            data,
            60,
        )
    )

    rows = []

    equity_rows = []

    for i in range(
        250,
        len(data),
    ):

        timestamp = data.index[i]

        m15_history = m15[
            m15.index <= timestamp
        ]

        h1_history = h1[
            h1.index <= timestamp
        ]

        if (
            len(m15_history) < 40
            or len(h1_history) < 110
        ):
            continue

        m15_row = (
            m15_history.iloc[-1]
        )

        h1_row = (
            h1_history.iloc[-1]
        )

        m15_score = (
            timeframe_score(
                m15_row
            )
        )

        h1_score = (
            timeframe_score(
                h1_row
            )
        )

        # Simplified research model.
        # This is not live execution.
        if (
            m15_score >= 6
            and h1_score >= 4
            and m15_row.rsi > 52
            and m15_row.macd
            > m15_row.macd_signal
        ):

            entry = float(
                data.iloc[i]["open"]
            )

            atr = float(
                m15_row["atr"]
            )

            risk_distance = (
                atr * SL_ATR
            )

            sl = (
                entry
                - risk_distance
            )

            tp = (
                entry
                + atr * TP2_ATR
            )

            future = data.iloc[
                i + 1:
                i + 31
            ]

            result = None

            for _, candle in (
                future.iterrows()
            ):

                if (
                    candle["low"]
                    <= sl
                ):

                    result = -1.0
                    break

                if (
                    candle["high"]
                    >= tp
                ):

                    result = TP2_ATR / SL_ATR
                    break

            if result is not None:

                rows.append(
                    {
                        "time":
                            str(timestamp),
                        "direction":
                            "BUY",
                        "R":
                            result,
                    }
                )

        equity_rows.append(
            {
                "time":
                    timestamp,
                "equity":
                    START_BALANCE,
            }
        )

    trades = pd.DataFrame(
        rows
    )

    equity = pd.DataFrame(
        equity_rows
    )

    if trades.empty:

        return {
            "error":
                "لم ينتج النظام صفقات في العينة الحالية",
            "trades":
                trades,
            "equity":
                equity,
        }

    split = int(
        len(trades) * 0.70
    )

    trades["OOS"] = (
        trades.index >= split
    )

    return {
        "error": None,
        "trades": trades,
        "equity": equity,
    }


# ============================================================
# SETTINGS
# ============================================================

api_key = st.secrets.get(
    "TWELVE_DATA_API_KEY",
    "",
)

with st.sidebar:

    st.markdown(
        "## ⚙️ إعدادات المحرك"
    )

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

    max_trades = int(
        st.number_input(
            "Max Trades / Day",
            min_value=1,
            max_value=20,
            value=DEFAULT_MAX_TRADES,
            step=1,
        )
    )

    refresh = st.slider(
        "Refresh",
        min_value=10,
        max_value=120,
        value=30,
        step=10,
    )

    st.divider()

    if st.session_state.kill:

        if st.button(
            "▶ START ENGINE",
            use_container_width=True,
        ):

            st.session_state.kill = (
                False
            )

            st.rerun()

    else:

        if st.button(
            "⛔ STOP ENGINE",
            use_container_width=True,
        ):

            st.session_state.kill = (
                True
            )

            st.rerun()

    if st.button(
        "♻ RESET PAPER ACCOUNT",
        use_container_width=True,
    ):

        st.session_state.balance = (
            START_BALANCE
        )

        st.session_state.history = []

        st.session_state.logs = []

        st.session_state.position = (
            None
        )

        st.session_state.pending = (
            None
        )

        st.session_state.daily_trades = (
            0
        )

        st.session_state.day_start = (
            START_BALANCE
        )

        st.session_state.last_candle = (
            None
        )

        st.session_state.kill = False

        st.rerun()


# ============================================================
# LOAD DATA
# ============================================================

try:

    raw = completed(
        fetch_data(api_key)
    )

    quality = data_quality(
        raw
    )

    m5 = add_indicators(
        raw
    )

    m15 = add_indicators(
        timeframe(
            raw,
            15,
        )
    )

    h1 = add_indicators(
        timeframe(
            raw,
            60,
        )
    )

    h4 = add_indicators(
        timeframe(
            raw,
            240,
        )
    )

    if min(
        len(m5),
        len(m15),
        len(h1),
        len(h4),
    ) < 25:

        raise RuntimeError(
            "التاريخ المتاح غير كافٍ لبناء جميع الأطر الزمنية."
        )

except Exception as error:

    st.markdown(
        """
<div class="hero">
    <div class="brand">
        GOLD AI • XAU/USD
    </div>

    <div class="hero-title">
        بوت الذهب
    </div>

    <div class="hero-sub">
        Paper Trading Only
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    st.error(
        f"تعذر تشغيل البيانات: {error}"
    )

    st.info(
        "تأكد من وجود "
        "TWELVE_DATA_API_KEY "
        "داخل Streamlit Secrets."
    )

    st.stop()


# ============================================================
# MARKET ANALYSIS
# ============================================================

signal = analyze_market(
    m5,
    m15,
    h1,
    h4,
)

price = float(
    m5.iloc[-1]["close"]
)

st.session_state.last_price = (
    price
)


# ============================================================
# LIVE PAPER ENGINE
# ============================================================

if not st.session_state.kill:

    log_decision(
        signal
    )

    current_candle = str(
        m5.index[-1]
    )

    if (
        st.session_state.position
        and
        st.session_state.last_candle
        != current_candle
    ):

        manage_trade(
            st.session_state.position,
            m5.iloc[-1],
        )

        st.session_state.last_candle = (
            current_candle
        )

    daily_loss = (
        daily_loss_pct()
    )

    if (
        signal["signal"]
        in ["BUY", "SELL"]
        and not st.session_state.position
        and daily_loss < daily_limit
        and st.session_state.daily_trades
        < max_trades
        and st.session_state.last_candle
        != current_candle
    ):

        st.session_state.position = (
            create_trade(
                signal["signal"],
                price,
                risk_pct,
                float(
                    m5.iloc[-1]["atr"]
                ),
            )
        )

        st.session_state.daily_trades += (
            1
        )

        st.session_state.last_candle = (
            current_candle
        )


# ============================================================
# HERO
# ============================================================

st.markdown(
    """
<div class="hero">

    <div class="brand">
        GOLD AI • SMART TRADING SYSTEM
    </div>

    <div class="hero-title">
        بوت الذهب XAU/USD
    </div>

    <div class="hero-sub">
        Multi-Timeframe • B2 • Risk Engine • Paper Trading Only
    </div>

</div>
""",
    unsafe_allow_html=True,
)


# ============================================================
# TOP METRICS
# ============================================================

col1, col2, col3, col4 = (
    st.columns(4)
)

col1.markdown(
    f"""
<div class="card">

<div class="card-title">
XAU/USD
</div>

<div class="price">
{fprice(price)}
</div>

</div>
""",
    unsafe_allow_html=True,
)

signal_class = {
    "BUY": "green",
    "SELL": "red",
    "WAIT": "gold",
}.get(
    signal["signal"],
    "gold",
)

col2.markdown(
    f"""
<div class="card">

<div class="card-title">
SIGNAL
</div>

<div class="card-value {signal_class}">
{signal["signal"]}
</div>

</div>
""",
    unsafe_allow_html=True,
)

col3.markdown(
    f"""
<div class="card">

<div class="card-title">
CONFLUENCE
</div>

<div class="card-value gold">
{signal["strength"]:.0f}%
</div>

</div>
""",
    unsafe_allow_html=True,
)

col4.markdown(
    f"""
<div class="card">

<div class="card-title">
PAPER BALANCE
</div>

<div class="card-value">
{fmoney(st.session_state.balance)}
</div>

</div>
""",
    unsafe_allow_html=True,
)

st.caption(
    "درجة التوافق داخل النظام وليست احتمالًا للربح."
)


# ============================================================
# MARKET STATUS
# ============================================================

st.markdown(
    '<div class="title">حالة السوق</div>',
    unsafe_allow_html=True,
)

market_cols = st.columns(4)

for column, timeframe_name in zip(
    market_cols,
    ["M5", "M15", "H1", "H4"],
):

    score_value = signal[
        "scores"
    ][timeframe_name]

    trend = signal[
        "labels"
    ][timeframe_name]

    if trend == "BULLISH":
        trend_class = "green"

    elif trend == "BEARISH":
        trend_class = "red"

    else:
        trend_class = "gold"

    column.markdown(
        f"""
<div class="card">

<div class="card-title">
{timeframe_name}
</div>

<div class="card-value {trend_class}">
{trend}
</div>

<div class="muted">
Score: {score_value}
</div>

</div>
""",
        unsafe_allow_html=True,
    )


# ============================================================
# DECISION ENGINE
# ============================================================

st.markdown(
    '<div class="title">Decision Engine</div>',
    unsafe_allow_html=True,
)

if signal["signal"] == "BUY":

    st.markdown(
        """
<div class="signal-buy">

<div class="card-title">
FINAL DECISION
</div>

<div style="font-size:32px;font-weight:900;">
BUY
</div>

<div class="muted">
شروط الاتجاه والزخم وB2 مكتملة
</div>

</div>
""",
        unsafe_allow_html=True,
    )

elif signal["signal"] == "SELL":

    st.markdown(
        """
<div class="signal-sell">

<div class="card-title">
FINAL DECISION
</div>

<div style="font-size:32px;font-weight:900;">
SELL
</div>

<div class="muted">
شروط الاتجاه والزخم وB2 مكتملة
</div>

</div>
""",
        unsafe_allow_html=True,
    )

else:

    st.markdown(
        f"""
<div class="signal-wait">

<div class="card-title">
FINAL DECISION
</div>

<div style="font-size:32px;font-weight:900;">
WAIT
</div>

<div class="muted">
{signal["rejection"]}
</div>

</div>
""",
        unsafe_allow_html=True,
    )


# ============================================================
# SIGNAL GATES
# ============================================================

st.markdown(
    '<div class="title">Signal Gates</div>',
    unsafe_allow_html=True,
)

gate_columns = st.columns(
    len(signal["reasons"])
)

for column, gate in zip(
    gate_columns,
    signal["reasons"],
):

    name, passed, detail = gate

    if passed:

        icon = "✓"
        cls = "green"

    else:

        icon = "×"
        cls = "red"

    column.markdown(
        f"""
<div class="card">

<div class="card-title">
{name}
</div>

<div class="card-value {cls}">
{icon}
</div>

<div class="muted">
{detail}
</div>

</div>
""",
        unsafe_allow_html=True,
    )


# ============================================================
# B2 + SUPPORT / RESISTANCE
# ============================================================

st.markdown(
    '<div class="title">B2 + Support / Resistance</div>',
    unsafe_allow_html=True,
)

b2 = signal["b2"]

b2a, b2b, b2c = st.columns(3)

b2a.metric(
    "B2 Breakout",
    "YES"
    if b2["breakout"]
    else "NO",
)

b2b.metric(
    "B2 Retest",
    "YES"
    if b2["retest"]
    else "NO",
)

b2c.metric(
    "B2 Level",
    fprice(
        b2["level"]
    ),
)

st.caption(
    f"""
Support: {fprice(signal["support"])}
•
Resistance: {fprice(signal["resistance"])}
•
{b2["reason"]}
"""
)


# ============================================================
# PRICE CHART
# ============================================================

st.markdown(
    '<div class="title">XAU/USD Price</div>',
    unsafe_allow_html=True,
)

chart_data = m5.tail(180)[
    [
        "close",
        "ema20",
        "ema50",
        "ema100",
    ]
].copy()

st.line_chart(
    chart_data,
    height=380,
)


# ============================================================
# PAPER TRADING
# ============================================================

st.markdown(
    '<div class="title">Paper Trading</div>',
    unsafe_allow_html=True,
)

position = (
    st.session_state.position
)

if position:

    unrealized_r = pnl_r(
        position,
        price,
    )

    position_cols = st.columns(5)

    position_cols[0].metric(
        "Direction",
        position["direction"],
    )

    position_cols[1].metric(
        "Entry",
        fprice(
            position["entry"]
        ),
    )

    position_cols[2].metric(
        "SL",
        fprice(
            position["sl"]
        ),
    )

    position_cols[3].metric(
        "TP1",
        fprice(
            position["tp1"]
        ),
    )

    position_cols[4].metric(
        "TP2",
        fprice(
            position["tp2"]
        ),
    )

    st.info(
        f"""
Position: {unrealized_r:.2f}R
•
Risk: {position["risk_pct"]:.2f}%
•
TP1:
{"HIT" if position["tp1_hit"] else "WAITING"}
"""
    )

    if position["events"]:

        st.success(
            " | ".join(
                position["events"]
            )
        )

else:

    st.info(
        "لا توجد صفقة مفتوحة حاليًا."
    )


# ============================================================
# RISK CENTER
# ============================================================

st.markdown(
    '<div class="title">Risk Center</div>',
    unsafe_allow_html=True,
)

risk_cols = st.columns(4)

risk_cols[0].metric(
    "Risk / Trade",
    f"{risk_pct:.2f}%",
)

risk_cols[1].metric(
    "Daily Loss",
    f"{daily_loss_pct():.2f}%",
)

risk_cols[2].metric(
    "Trades Today",
    f"{st.session_state.daily_trades}/{max_trades}",
)

risk_cols[3].metric(
    "Engine",
    "STOPPED"
    if st.session_state.kill
    else "RUNNING",
)


# ============================================================
# SYSTEM HEALTH
# ============================================================

st.markdown(
    '<div class="title">System Health</div>',
    unsafe_allow_html=True,
)

health_cols = st.columns(4)

health_cols[0].metric(
    "Data Feed",
    "CONNECTED"
    if quality["valid"]
    else "WARNING",
)

health_cols[1].metric(
    "Strategy",
    "ACTIVE",
)

health_cols[2].metric(
    "Risk Engine",
    "ACTIVE",
)

health_cols[3].metric(
    "News Filter",
    "BLOCKED",
)

with st.expander(
    "🔎 Data Quality"
):

    st.write(
        {
            "Valid":
                quality["valid"],
            "Rows":
                quality["rows"],
            "Gaps":
                quality["gaps"],
            "Stale Minutes":
                quality["stale"],
            "Reason":
                quality["reason"],
            "Last Candle":
                str(
                    m5.index[-1]
                ),
        }
    )


# ============================================================
# BACKTEST LAB
# ============================================================

st.markdown(
    '<div class="title">Backtest Lab</div>',
    unsafe_allow_html=True,
)

run_backtest = st.button(
    "▶ RUN BACKTEST"
)

if run_backtest:

    with st.spinner(
        "جاري تشغيل الباك تست..."
    ):

        result = backtest_strategy(
            m5,
            risk_pct,
        )

    if result["error"]:

        st.warning(
            result["error"]
        )

    else:

        trades = result[
            "trades"
        ]

        oos = trades[
            trades["OOS"]
            == True
        ].copy()

        for title, sample in [
            (
                "Full Sample",
                trades,
            ),
            (
                "OOS 30%",
                oos,
            ),
        ]:

            st.markdown(
                f"### {title}"
            )

            if sample.empty:

                st.info(
                    "لا توجد نتائج."
                )

                continue

            wins = int(
                (
                    sample["R"]
                    > 0
                ).sum()
            )

            losses = int(
                (
                    sample["R"]
                    < 0
                ).sum()
            )

            gross_profit = float(
                sample.loc[
                    sample["R"] > 0,
                    "R",
                ].sum()
            )

            gross_loss = float(
                -sample.loc[
                    sample["R"] < 0,
                    "R",
                ].sum()
            )

            if gross_loss > 0:

                profit_factor = (
                    gross_profit
                    / gross_loss
                )

            else:

                profit_factor = (
                    math.inf
                )

            stats = st.columns(
                4
            )

            stats[0].metric(
                "Trades",
                len(sample),
            )

            stats[1].metric(
                "Win Rate",
                f"""
{wins / len(sample) * 100:.1f}%
""",
            )

            stats[2].metric(
                "Profit Factor",
                f"{profit_factor:.2f}",
            )

            stats[3].metric(
                "Total R",
                f"{sample['R'].sum():.2f}",
            )

            st.dataframe(
                sample,
                use_container_width=True,
                hide_index=True,
            )

        st.warning(
            "نتائج الباك تست تاريخية وتجريبية "
            "وليست ضمانًا للنتائج المستقبلية."
        )


# ============================================================
# PAPER TRADE HISTORY
# ============================================================

st.markdown(
    '<div class="title">Paper Trade History</div>',
    unsafe_allow_html=True,
)

if st.session_state.history:

    history_df = pd.DataFrame(
        st.session_state.history
    )

    display_columns = [
        column
        for column in [
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
        if column
        in history_df.columns
    ]

    st.dataframe(
        history_df[
            display_columns
        ],
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
    '<div class="title">Decision Log</div>',
    unsafe_allow_html=True,
)

if st.session_state.logs:

    log_df = pd.DataFrame(
        st.session_state.logs
    )

    st.dataframe(
        log_df,
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
    f"""
GOLD AI • {SYMBOL}
•
PAPER TRADING ONLY
•
Last update:
{now().strftime("%Y-%m-%d %H:%M:%S")}
"""
)


# ============================================================
# AUTO REFRESH
# ============================================================

if not st.session_state.kill:

    time.sleep(
        refresh
    )

    st.rerun()

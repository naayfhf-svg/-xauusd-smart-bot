import streamlit as st
import pandas as pd
import numpy as np
import requests
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# =========================================================
# إعداد الصفحة
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
DEFAULT_RISK = 1.0

MAX_DAILY_LOSS = 3.0
MAX_TRADES_PER_DAY = 5

SL_ATR = 1.5
TP1_ATR = 1.5
TP2_ATR = 2.5

RETEST_ATR = 0.35

SPREAD = 0.20
SLIPPAGE = 0.05

M5_OUTPUT_SIZE = 5000

RIYADH = ZoneInfo("Asia/Riyadh")


# =========================================================
# CSS
# =========================================================
st.markdown(
    """
<style>
html, body, [class*="css"] {
    direction: rtl;
    text-align: right;
}

.stApp {
    background: #f4f6f8;
    color: #17202a;
}

.block-container {
    max-width: 1400px;
    padding-top: 1.2rem;
    padding-bottom: 3rem;
}

h1, h2, h3 {
    color: #17202a !important;
}

.card {
    background: white;
    border: 1px solid #e2e6ea;
    border-radius: 14px;
    padding: 18px;
    margin-bottom: 14px;
    box-shadow: 0 2px 8px rgba(0,0,0,.04);
}

.metric-card {
    background: white;
    border: 1px solid #e2e6ea;
    border-radius: 14px;
    padding: 16px;
    min-height: 105px;
}

.metric-title {
    color: #68737d;
    font-size: 13px;
    margin-bottom: 7px;
}

.metric-value {
    color: #17202a;
    font-size: 25px;
    font-weight: 700;
}

.buy-box {
    background: #eaf7ef;
    border: 1px solid #b9e3c7;
    color: #14733c;
    border-radius: 14px;
    padding: 20px;
    text-align: center;
    font-size: 27px;
    font-weight: 800;
}

.sell-box {
    background: #fff0f0;
    border: 1px solid #efc0c0;
    color: #b42318;
    border-radius: 14px;
    padding: 20px;
    text-align: center;
    font-size: 27px;
    font-weight: 800;
}

.neutral-box {
    background: #f2f4f6;
    border: 1px solid #d9dee3;
    color: #59636d;
    border-radius: 14px;
    padding: 20px;
    text-align: center;
    font-size: 27px;
    font-weight: 800;
}

.paper-banner {
    background: #fff8e6;
    border: 1px solid #f1d58a;
    color: #735313;
    border-radius: 14px;
    padding: 14px 18px;
    margin-bottom: 18px;
    font-weight: 700;
}

.small-muted {
    color: #68737d;
    font-size: 13px;
}

.status-good {
    color: #14733c;
    font-weight: 700;
}

.status-bad {
    color: #b42318;
    font-weight: 700;
}

hr {
    border: none;
    border-top: 1px solid #e2e6ea;
    margin: 18px 0;
}
</style>
""",
    unsafe_allow_html=True,
)


# =========================================================
# Session State
# =========================================================
def init_state():
    defaults = {
        "paper_balance": START_BALANCE,
        "paper_trade": None,
        "paper_history": [],
        "logs": [],
        "kill_switch": False,
        "last_signal_time": None,
        "last_signal_key": None,
        "pending_signal": None,
        "last_managed_candle": None,
        "day_start_balance": START_BALANCE,
        "day_key": None,
        "daily_loss_money": 0.0,
        "daily_trades": 0,
        "last_price": None,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_state()


# =========================================================
# Helpers
# =========================================================
def now_riyadh():
    return datetime.now(RIYADH)


def log_event(message):
    timestamp = now_riyadh().strftime("%Y-%m-%d %H:%M:%S")
    st.session_state.logs.insert(0, f"{timestamp} — {message}")

    if len(st.session_state.logs) > 200:
        st.session_state.logs = st.session_state.logs[:200]


def reset_daily_limits():
    today = now_riyadh().date()

    if st.session_state.day_key != today:
        st.session_state.day_key = today
        st.session_state.day_start_balance = st.session_state.paper_balance
        st.session_state.daily_loss_money = 0.0
        st.session_state.daily_trades = 0


def daily_loss_pct():
    start = float(st.session_state.day_start_balance)

    if start <= 0:
        return 0.0

    return abs(min(st.session_state.daily_loss_money, 0.0)) / start * 100.0


def risk_money(balance, risk_pct):
    return balance * (risk_pct / 100.0)


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return np.nan


def fmt_price(value):
    if value is None or not np.isfinite(value):
        return "-"
    return f"{value:,.2f}"


# =========================================================
# Twelve Data
# =========================================================
@st.cache_data(ttl=45, show_spinner=False)
def load_m5():
    try:
        key = st.secrets["TWELVE_DATA_API_KEY"]
    except Exception:
        raise RuntimeError(
            "لم يتم العثور على TWELVE_DATA_API_KEY داخل Streamlit Secrets."
        )

    params = {
        "symbol": SYMBOL,
        "interval": "5min",
        "outputsize": M5_OUTPUT_SIZE,
        "apikey": key,
        "timezone": "UTC",
        "format": "JSON",
    }

    response = requests.get(API_URL, params=params, timeout=20)

    if response.status_code != 200:
        raise RuntimeError(
            f"خطأ Twelve Data HTTP {response.status_code}"
        )

    data = response.json()

    if "status" in data and data["status"] == "error":
        raise RuntimeError(
            data.get("message", "خطأ غير معروف من Twelve Data")
        )

    values = data.get("values")

    if not values:
        raise RuntimeError("لم تصل بيانات XAU/USD من Twelve Data.")

    df = pd.DataFrame(values)

    required = ["datetime", "open", "high", "low", "close"]

    for col in required:
        if col not in df.columns:
            raise RuntimeError(f"البيانات ناقصة: {col}")

    df["datetime"] = pd.to_datetime(
        df["datetime"],
        utc=True,
        errors="coerce",
    )

    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(
            df["volume"],
            errors="coerce",
        )

    df = df.dropna(
        subset=["datetime", "open", "high", "low", "close"]
    )

    df = df.sort_values("datetime")
    df = df.drop_duplicates("datetime")
    df = df.set_index("datetime")

    return df


def completed_m5(df):
    boundary = pd.Timestamp.now(tz="UTC").floor("5min")

    return df[df.index < boundary].copy()


# =========================================================
# Resampling
# =========================================================
def resample_tf(df, rule):
    if df.empty:
        return pd.DataFrame()

    result = (
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

    return result


# =========================================================
# Indicators
# =========================================================
def add_indicators(df):
    df = df.copy()

    if len(df) < 10:
        return df

    close = df["close"]
    high = df["high"]
    low = df["low"]

    df["ema20"] = close.ewm(
        span=20,
        adjust=False,
        min_periods=20,
    ).mean()

    df["ema50"] = close.ewm(
        span=50,
        adjust=False,
        min_periods=50,
    ).mean()

    df["ema100"] = close.ewm(
        span=100,
        adjust=False,
        min_periods=100,
    ).mean()

    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14,
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    df["rsi"] = 100 - (100 / (1 + rs))

    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    df["atr"] = tr.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14,
    ).mean()

    ema12 = close.ewm(
        span=12,
        adjust=False,
        min_periods=12,
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False,
        min_periods=26,
    ).mean()

    df["macd"] = ema12 - ema26

    df["macd_signal"] = df["macd"].ewm(
        span=9,
        adjust=False,
        min_periods=9,
    ).mean()

    df["macd_hist"] = (
        df["macd"] - df["macd_signal"]
    )

    df["momentum"] = close.diff(10)

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

    atr14 = df["atr"]

    plus_di = (
        100
        * plus_dm.ewm(
            alpha=1 / 14,
            adjust=False,
            min_periods=14,
        ).mean()
        / atr14.replace(0, np.nan)
    )

    minus_di = (
        100
        * minus_dm.ewm(
            alpha=1 / 14,
            adjust=False,
            min_periods=14,
        ).mean()
        / atr14.replace(0, np.nan)
    )

    dx = (
        100
        * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0, np.nan)
    )

    df["adx"] = dx.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14,
    ).mean()

    df["plus_di"] = plus_di
    df["minus_di"] = minus_di

    return df


def prepare(df):
    return add_indicators(df)


# =========================================================
# Score
# =========================================================
def score_row(row):
    needed = [
        "close",
        "ema20",
        "ema50",
        "ema100",
        "rsi",
        "macd",
        "macd_signal",
        "macd_hist",
        "momentum",
        "adx",
        "plus_di",
        "minus_di",
    ]

    for col in needed:
        if col not in row or not np.isfinite(row[col]):
            return np.nan

    score = 0

    # EMA structure
    if (
        row["ema20"]
        > row["ema50"]
        > row["ema100"]
    ):
        score += 3

    elif (
        row["ema20"]
        < row["ema50"]
        < row["ema100"]
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

    # ADX + DI
    if row["adx"] >= 25:
        if row["plus_di"] > row["minus_di"]:
            score += 2
        elif row["minus_di"] > row["plus_di"]:
            score -= 2

    return score


def trend_label(score):
    if not np.isfinite(score):
        return "غير جاهز"

    if score >= 5:
        return "صاعد"

    if score <= -5:
        return "هابط"

    return "محايد"


# =========================================================
# Latest completed timeframe row
# =========================================================
def latest_before(df, ts):
    if df.empty:
        return None

    data = df.loc[df.index <= ts]

    if data.empty:
        return None

    return data.iloc[-1]


# =========================================================
# B2 Breakout + Retest
# =========================================================
def b2_signal(m15, ts):
    if m15.empty:
        return None

    available = m15.loc[m15.index <= ts].copy()

    if len(available) < 10:
        return None

    latest = available.iloc[-1]

    latest_time = available.index[-1]

    if latest_time != ts:
        # We only want the latest completed M15 candle
        # to be the actual retest candle.
        return None

    latest_atr = latest.get("atr", np.nan)

    if not np.isfinite(latest_atr) or latest_atr <= 0:
        return None

    latest_score = score_row(latest)

    if not np.isfinite(latest_score):
        return None

    # Search 1..6 M15 candles backward for breakout
    for bars_back in range(1, 7):
        if len(available) <= bars_back + 1:
            continue

        breakout_idx = -1 - bars_back
        prev_idx = -2 - bars_back

        breakout = available.iloc[breakout_idx]
        previous = available.iloc[prev_idx]

        breakout_time = available.index[breakout_idx]

        level_buy = previous["high"]
        level_sell = previous["low"]

        # -------------------------
        # BUY breakout
        # -------------------------
        buy_breakout = (
            breakout["close"] > level_buy
            and breakout["high"] > level_buy
        )

        if buy_breakout:
            retest_distance = abs(
                latest["low"] - level_buy
            )

            buy_retest = (
                retest_distance
                <= RETEST_ATR * latest_atr
                and latest["close"] > level_buy
            )

            buy_alignment = (
                latest_score >= 5
                and latest["adx"] >= 20
                and latest["rsi"] >= 50
                and latest["macd"] > 0
            )

            if buy_retest and buy_alignment:
                return {
                    "direction": "BUY",
                    "level": float(level_buy),
                    "breakout_time": breakout_time,
                    "retest_time": latest_time,
                    "atr": float(latest_atr),
                    "score": float(latest_score),
                }

        # -------------------------
        # SELL breakout
        # -------------------------
        sell_breakout = (
            breakout["close"] < level_sell
            and breakout["low"] < level_sell
        )

        if sell_breakout:
            retest_distance = abs(
                latest["high"] - level_sell
            )

            sell_retest = (
                retest_distance
                <= RETEST_ATR * latest_atr
                and latest["close"] < level_sell
            )

            sell_alignment = (
                latest_score <= -5
                and latest["adx"] >= 20
                and latest["rsi"] <= 50
                and latest["macd"] < 0
            )

            if sell_retest and sell_alignment:
                return {
                    "direction": "SELL",
                    "level": float(level_sell),
                    "breakout_time": breakout_time,
                    "retest_time": latest_time,
                    "atr": float(latest_atr),
                    "score": float(latest_score),
                }

    return None


# =========================================================
# Generic signal
# =========================================================
def get_signal(m5, m15, h1, h4, ts):
    m5_row = latest_before(m5, ts)
    m15_row = latest_before(m15, ts)
    h1_row = latest_before(h1, ts)
    h4_row = latest_before(h4, ts)

    if any(
        row is None
        for row in [m5_row, m15_row, h1_row, h4_row]
    ):
        return None

    scores = {
        "M5": score_row(m5_row),
        "M15": score_row(m15_row),
        "H1": score_row(h1_row),
        "H4": score_row(h4_row),
    }

    if any(
        not np.isfinite(v)
        for v in scores.values()
    ):
        return None

    b2 = b2_signal(m15, ts)

    if not b2:
        return None

    direction = b2["direction"]

    if direction == "BUY":
        valid = (
            scores["M5"] >= 5
            and scores["M15"] >= 5
            and scores["H1"] >= 5
            and m15_row["adx"] >= 20
            and m15_row["rsi"] >= 50
            and m15_row["macd"] > 0
        )
    else:
        valid = (
            scores["M5"] <= -5
            and scores["M15"] <= -5
            and scores["H1"] <= -5
            and m15_row["adx"] >= 20
            and m15_row["rsi"] <= 50
            and m15_row["macd"] < 0
        )

    if not valid:
        return None

    total_score = sum(scores.values())

    confluence = (
        abs(total_score) / 44.0 * 100.0
    )

    atr = float(m5_row["atr"])

    if not np.isfinite(atr) or atr <= 0:
        return None

    signal = {
        "direction": direction,
        "signal_time": ts,
        "atr": atr,
        "confluence": float(confluence),
        "scores": scores,
        "m5": m5_row,
        "m15": m15_row,
        "h1": h1_row,
        "h4": h4_row,
        "b2": b2,
    }

    return signal


# =========================================================
# Create trade
# =========================================================
def build_trade(
    signal,
    entry,
    risk_pct,
    entry_time,
):
    direction = signal["direction"]
    atr = float(signal["atr"])

    if direction == "BUY":
        effective_entry = (
            entry
            + SPREAD / 2
            + SLIPPAGE
        )

        sl = effective_entry - SL_ATR * atr
        tp1 = effective_entry + TP1_ATR * atr
        tp2 = effective_entry + TP2_ATR * atr

    else:
        effective_entry = (
            entry
            - SPREAD / 2
            - SLIPPAGE
        )

        sl = effective_entry + SL_ATR * atr
        tp1 = effective_entry - TP1_ATR * atr
        tp2 = effective_entry - TP2_ATR * atr

    risk_distance = abs(effective_entry - sl)

    balance = float(st.session_state.paper_balance)

    money_risk = risk_money(
        balance,
        risk_pct,
    )

    return {
        "id": str(uuid.uuid4())[:8],
        "direction": direction,
        "entry": float(effective_entry),
        "original_sl": float(sl),
        "sl": float(sl),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "risk_pct": float(risk_pct),
        "risk_money": float(money_risk),
        "risk_distance": float(risk_distance),
        "signal_time": signal["signal_time"],
        "entry_time": entry_time,
        "entry_price_raw": float(entry),
        "tp1_hit": False,
        "be_active": False,
        "realized_pnl": 0.0,
        "remaining_fraction": 1.0,
        "status": "OPEN",
        "last_managed_candle": None,
        "b2": signal["b2"],
        "confluence": signal["confluence"],
    }


# =========================================================
# P&L calculation
# =========================================================
def pnl_for_move(
    trade,
    exit_price,
    fraction,
):
    if trade["direction"] == "BUY":
        move = exit_price - trade["entry"]
    else:
        move = trade["entry"] - exit_price

    if trade["risk_distance"] <= 0:
        return 0.0

    r = move / trade["risk_distance"]

    return (
        r
        * trade["risk_money"]
        * fraction
    )


# =========================================================
# Manage trade on one candle
# =========================================================
def manage_trade_on_candle(
    trade,
    candle,
):
    if trade is None:
        return trade, []

    events = []

    high = float(candle["high"])
    low = float(candle["low"])
    candle_time = candle.name

    if (
        trade["last_managed_candle"] is not None
        and candle_time <= trade["last_managed_candle"]
    ):
        return trade, events

    trade["last_managed_candle"] = candle_time

    # =====================================================
    # Before TP1
    # =====================================================
    if not trade["tp1_hit"]:

        if trade["direction"] == "BUY":
            sl_hit = low <= trade["sl"]
            tp1_hit = high >= trade["tp1"]
        else:
            sl_hit = high >= trade["sl"]
            tp1_hit = low <= trade["tp1"]

        # Conservative:
        # if SL and TP1 happen in same candle,
        # SL is assumed first.
        if sl_hit:
            exit_price = trade["sl"]

            pnl = pnl_for_move(
                trade,
                exit_price,
                1.0,
            )

            trade["realized_pnl"] += pnl
            trade["remaining_fraction"] = 0.0
            trade["status"] = "SL"

            events.append(
                {
                    "type": "CLOSE",
                    "reason": "SL",
                    "price": exit_price,
                    "pnl": pnl,
                    "time": candle_time,
                }
            )

            return trade, events

        if tp1_hit:
            fraction = 0.5

            pnl = pnl_for_move(
                trade,
                trade["tp1"],
                fraction,
            )

            trade["realized_pnl"] += pnl
            trade["remaining_fraction"] = 0.5
            trade["tp1_hit"] = True
            trade["be_active"] = True

            trade["sl"] = trade["entry"]

            events.append(
                {
                    "type": "TP1",
                    "reason": "TP1",
                    "price": trade["tp1"],
                    "pnl": pnl,
                    "time": candle_time,
                }
            )

            # IMPORTANT:
            # We do NOT re-check this same candle
            # against the newly moved BE stop.
            #
            # But TP2 is allowed if it was also reached
            # after TP1 in this deterministic model.
            if trade["direction"] == "BUY":
                tp2_hit = high >= trade["tp2"]
            else:
                tp2_hit = low <= trade["tp2"]

            if tp2_hit:
                fraction = trade["remaining_fraction"]

                pnl = pnl_for_move(
                    trade,
                    trade["tp2"],
                    fraction,
                )

                trade["realized_pnl"] += pnl
                trade["remaining_fraction"] = 0.0
                trade["status"] = "TP2"

                events.append(
                    {
                        "type": "CLOSE",
                        "reason": "TP2",
                        "price": trade["tp2"],
                        "pnl": pnl,
                        "time": candle_time,
                    }
                )

        return trade, events

    # =====================================================
    # After TP1 / Break-even
    # =====================================================
    if trade["remaining_fraction"] <= 0:
        return trade, events

    if trade["direction"] == "BUY":
        be_hit = low <= trade["entry"]
        tp2_hit = high >= trade["tp2"]
    else:
        be_hit = high >= trade["entry"]
        tp2_hit = low <= trade["tp2"]

    # Conservative:
    # BE first if both BE and TP2 happen same candle.
    if be_hit:
        fraction = trade["remaining_fraction"]

        pnl = pnl_for_move(
            trade,
            trade["entry"],
            fraction,
        )

        trade["realized_pnl"] += pnl
        trade["remaining_fraction"] = 0.0
        trade["status"] = "BE"

        events.append(
            {
                "type": "CLOSE",
                "reason": "BE",
                "price": trade["entry"],
                "pnl": pnl,
                "time": candle_time,
            }
        )

        return trade, events

    if tp2_hit:
        fraction = trade["remaining_fraction"]

        pnl = pnl_for_move(
            trade,
            trade["tp2"],
            fraction,
        )

        trade["realized_pnl"] += pnl
        trade["remaining_fraction"] = 0.0
        trade["status"] = "TP2"

        events.append(
            {
                "type": "CLOSE",
                "reason": "TP2",
                "price": trade["tp2"],
                "pnl": pnl,
                "time": candle_time,
            }
        )

    return trade, events


# =========================================================
# Close trade
# =========================================================
def finalize_trade(
    trade,
    close_reason=None,
    close_price=None,
    close_time=None,
):
    if trade is None:
        return

    if trade["remaining_fraction"] > 0:

        if close_price is None:
            close_price = trade["entry"]

        pnl = pnl_for_move(
            trade,
            close_price,
            trade["remaining_fraction"],
        )

        trade["realized_pnl"] += pnl
        trade["remaining_fraction"] = 0.0

    if close_reason:
        trade["status"] = close_reason

    record = {
        "id": trade["id"],
        "direction": trade["direction"],
        "signal_time": trade["signal_time"],
        "entry_time": trade["entry_time"],
        "entry": trade["entry"],
        "sl": trade["original_sl"],
        "tp1": trade["tp1"],
        "tp2": trade["tp2"],
        "exit": close_price,
        "exit_time": close_time,
        "pnl": trade["realized_pnl"],
        "risk_money": trade["risk_money"],
        "risk_pct": trade["risk_pct"],
        "status": trade["status"],
        "confluence": trade["confluence"],
    }

    st.session_state.paper_history.append(record)

    st.session_state.paper_balance += trade["realized_pnl"]

    if trade["realized_pnl"] < 0:
        st.session_state.daily_loss_money += (
            trade["realized_pnl"]
        )

    st.session_state.paper_trade = None

    log_event(
        f"إغلاق {trade['direction']} "
        f"| {trade['status']} "
        f"| P&L {trade['realized_pnl']:.2f} ريال"
    )


# =========================================================
# Manage all new live candles
# =========================================================
def manage_live_trade(m5):
    trade = st.session_state.paper_trade

    if trade is None:
        return

    if m5.empty:
        return

    candles = m5.copy()

    if trade["last_managed_candle"] is not None:
        candles = candles.loc[
            candles.index > trade["last_managed_candle"]
        ]

    for ts, candle in candles.iterrows():

        if st.session_state.paper_trade is None:
            break

        current_trade = st.session_state.paper_trade

        current_trade, events = manage_trade_on_candle(
            current_trade,
            candle,
        )

        st.session_state.paper_trade = current_trade

        for event in events:
            if event["type"] == "TP1":
                log_event(
                    f"TP1 تحقق | "
                    f"{current_trade['direction']} | "
                    f"{event['price']:.2f}"
                )

            elif event["type"] == "CLOSE":
                finalize_trade(
                    current_trade,
                    close_reason=event["reason"],
                    close_price=event["price"],
                    close_time=event["time"],
                )

    st.session_state.paper_trade = (
        None
        if st.session_state.paper_trade is None
        else st.session_state.paper_trade
    )


# =========================================================
# Execute pending signal at next M5 open
# =========================================================
def execute_pending_signal(m5):
    pending = st.session_state.pending_signal

    if pending is None:
        return

    if st.session_state.paper_trade is not None:
        st.session_state.pending_signal = None
        return

    if st.session_state.kill_switch:
        st.session_state.pending_signal = None
        return

    if daily_loss_pct() >= MAX_DAILY_LOSS:
        st.session_state.pending_signal = None
        log_event("تم رفض الصفقة: تجاوز حد الخسارة اليومية.")
        return

    if st.session_state.daily_trades >= MAX_TRADES_PER_DAY:
        st.session_state.pending_signal = None
        log_event("تم رفض الصفقة: تم الوصول للحد اليومي للصفقات.")
        return

    intended_time = pd.Timestamp(
        pending["intended_entry_time"]
    )

    # Need the exact next candle.
    if intended_time not in m5.index:
        # If it became too old because app was closed,
        # do not fabricate an entry.
        latest = m5.index[-1]

        if latest > intended_time + pd.Timedelta(minutes=10):
            st.session_state.pending_signal = None
            log_event(
                "تم إلغاء الإشارة المعلقة: وقت الدخول فات."
            )

        return

    candle = m5.loc[intended_time]

    entry = float(candle["open"])

    trade = build_trade(
        pending["signal"],
        entry,
        DEFAULT_RISK,
        intended_time,
    )

    st.session_state.paper_trade = trade
    st.session_state.daily_trades += 1
    st.session_state.pending_signal = None

    log_event(
        f"فتح صفقة Paper {trade['direction']} "
        f"| دخول {trade['entry']:.2f} "
        f"| SL {trade['sl']:.2f} "
        f"| TP1 {trade['tp1']:.2f} "
        f"| TP2 {trade['tp2']:.2f}"
    )


# =========================================================
# Prepare all timeframes
# =========================================================
def prepare_market(raw):
    m5 = completed_m5(raw)

    if len(m5) < 200:
        raise RuntimeError(
            "عدد شموع M5 المكتملة غير كافٍ للتحليل."
        )

    m15 = resample_tf(m5, "15min")
    h1 = resample_tf(m5, "1h")
    h4 = resample_tf(m5, "4h")

    m5 = prepare(m5)
    m15 = prepare(m15)
    h1 = prepare(h1)
    h4 = prepare(h4)

    return m5, m15, h1, h4


# =========================================================
# Analyze
# =========================================================
def analyze(raw):
    m5, m15, h1, h4 = prepare_market(raw)

    ts = m5.index[-1]

    signal = get_signal(
        m5,
        m15,
        h1,
        h4,
        ts,
    )

    rows = {
        "M5": latest_before(m5, ts),
        "M15": latest_before(m15, ts),
        "H1": latest_before(h1, ts),
        "H4": latest_before(h4, ts),
    }

    scores = {
        tf: score_row(row)
        for tf, row in rows.items()
    }

    total_score = sum(
        v for v in scores.values()
        if np.isfinite(v)
    )

    confluence = (
        abs(total_score) / 44.0 * 100.0
    )

    return {
        "m5": m5,
        "m15": m15,
        "h1": h1,
        "h4": h4,
        "ts": ts,
        "price": float(m5.iloc[-1]["close"]),
        "signal": signal,
        "rows": rows,
        "scores": scores,
        "confluence": confluence,
    }


# =========================================================
# Backtest
# =========================================================
def run_backtest(m5):
    if len(m5) < 500:
        return pd.DataFrame(), {}

    data = m5.copy()

    # Full higher timeframe datasets.
    m15 = prepare(
        resample_tf(data, "15min")
    )

    h1 = prepare(
        resample_tf(data, "1h")
    )

    h4 = prepare(
        resample_tf(data, "4h")
    )

    split_index = int(len(data) * 0.70)
    oos_start = data.index[split_index]

    trades = []

    position = None
    pending = None
    last_signal_key = None

    equity = START_BALANCE

    peak_equity = equity
    max_dd_money = 0.0

    for i in range(len(data)):

        ts = data.index[i]
        candle = data.iloc[i]

        # =================================================
        # Execute pending signal at current candle open
        # =================================================
        if pending is not None and ts == pending["entry_time"]:

            position = build_trade(
                pending["signal"],
                float(candle["open"]),
                DEFAULT_RISK,
                ts,
            )

            pending = None

        # =================================================
        # Manage open trade
        # =================================================
        if position is not None:

            position, events = manage_trade_on_candle(
                position,
                candle,
            )

            for event in events:

                if event["type"] == "CLOSE":
                    equity += event["pnl"]

                    record = {
                        "direction": position["direction"],
                        "signal_time": position["signal_time"],
                        "entry_time": position["entry_time"],
                        "entry": position["entry"],
                        "sl": position["original_sl"],
                        "tp1": position["tp1"],
                        "tp2": position["tp2"],
                        "exit": event["price"],
                        "exit_time": event["time"],
                        "pnl": position["realized_pnl"],
                        "risk_money": position["risk_money"],
                        "status": event["reason"],
                        "confluence": position["confluence"],
                    }

                    trades.append(record)

                    position = None

                    break

        # =================================================
        # Equity / DD
        # =================================================
        peak_equity = max(
            peak_equity,
            equity,
        )

        dd = peak_equity - equity

        max_dd_money = max(
            max_dd_money,
            dd,
        )

        # =================================================
        # No position -> evaluate signal
        # =================================================
        if position is not None:
            continue

        if pending is not None:
            continue

        # Need next candle for entry.
        if i + 1 >= len(data):
            continue

        signal = get_signal(
            data.iloc[: i + 1],
            m15,
            h1,
            h4,
            ts,
        )

        if signal is None:
            continue

        retest_time = signal["b2"]["retest_time"]

        # Only one signal per retest M15 candle.
        signal_key = (
            signal["direction"],
            retest_time,
        )

        if signal_key == last_signal_key:
            continue

        last_signal_key = signal_key

        entry_time = data.index[i + 1]

        pending = {
            "signal": signal,
            "entry_time": entry_time,
        }

    # =====================================================
    # Close remaining trade at final close
    # =====================================================
    if position is not None:

        final_price = float(data.iloc[-1]["close"])

        pnl = pnl_for_move(
            position,
            final_price,
            position["remaining_fraction"],
        )

        equity += pnl
        position["realized_pnl"] += pnl

        trades.append(
            {
                "direction": position["direction"],
                "signal_time": position["signal_time"],
                "entry_time": position["entry_time"],
                "entry": position["entry"],
                "sl": position["original_sl"],
                "tp1": position["tp1"],
                "tp2": position["tp2"],
                "exit": final_price,
                "exit_time": data.index[-1],
                "pnl": position["realized_pnl"],
                "risk_money": position["risk_money"],
                "status": "END",
                "confluence": position["confluence"],
            }
        )

    trades_df = pd.DataFrame(trades)

    if trades_df.empty:
        return trades_df, {
            "is_trades": 0,
            "oos_trades": 0,
            "is_win_rate": 0.0,
            "oos_win_rate": 0.0,
            "is_pf": 0.0,
            "oos_pf": 0.0,
            "is_r": 0.0,
            "oos_r": 0.0,
            "max_dd": max_dd_money,
            "oos_start": oos_start,
        }

    trades_df["entry_time"] = pd.to_datetime(
        trades_df["entry_time"],
        utc=True,
    )

    trades_df["pnl"] = pd.to_numeric(
        trades_df["pnl"],
        errors="coerce",
    )

    trades_df["r"] = (
        trades_df["pnl"]
        / trades_df["risk_money"].replace(
            0,
            np.nan,
        )
    )

    is_df = trades_df[
        trades_df["entry_time"] < oos_start
    ].copy()

    oos_df = trades_df[
        trades_df["entry_time"] >= oos_start
    ].copy()

    def stats(df):
        if df.empty:
            return {
                "trades": 0,
                "win_rate": 0.0,
                "pf": 0.0,
                "r": 0.0,
            }

        wins = df[df["pnl"] > 0]["pnl"].sum()
        losses = abs(
            df[df["pnl"] < 0]["pnl"].sum()
        )

        pf = (
            wins / losses
            if losses > 0
            else np.inf
        )

        return {
            "trades": len(df),
            "win_rate": (
                (df["pnl"] > 0).mean() * 100
            ),
            "pf": pf,
            "r": df["r"].sum(),
        }

    is_stats = stats(is_df)
    oos_stats = stats(oos_df)

    return trades_df, {
        "is_trades": is_stats["trades"],
        "oos_trades": oos_stats["trades"],
        "is_win_rate": is_stats["win_rate"],
        "oos_win_rate": oos_stats["win_rate"],
        "is_pf": is_stats["pf"],
        "oos_pf": oos_stats["pf"],
        "is_r": is_stats["r"],
        "oos_r": oos_stats["r"],
        "max_dd": max_dd_money,
        "oos_start": oos_start,
    }


# =========================================================
# UI helpers
# =========================================================
def metric_card(title, value):
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-title">{title}</div>
            <div class="metric-value">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def signal_box(signal):
    if signal is None:
        st.markdown(
            """
            <div class="neutral-box">
                لا توجد إشارة مؤكدة حالياً
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    if signal["direction"] == "BUY":
        st.markdown(
            """
            <div class="buy-box">
                🟢 BUY
            </div>
            """,
            unsafe_allow_html=True,
        )

    else:
        st.markdown(
            """
            <div class="sell-box">
                🔴 SELL
            </div>
            """,
            unsafe_allow_html=True,
        )


# =========================================================
# Main
# =========================================================
reset_daily_limits()

st.markdown(
    """
# 📈 بوت الذهب XAU/USD
"""
)

st.markdown(
    """
<div class="paper-banner">
🟡 وضع Paper Trading فقط — لا يوجد أي تنفيذ حقيقي على حساب الوسيط.
النظام يحلل XAU/USD ويجري محاكاة للصفقات فقط.
</div>
""",
    unsafe_allow_html=True,
)


# =========================================================
# Sidebar
# =========================================================
with st.sidebar:

    st.header("إعدادات البوت")

    risk_pct = st.number_input(
        "المخاطرة لكل صفقة %",
        min_value=0.1,
        max_value=5.0,
        value=DEFAULT_RISK,
        step=0.1,
    )

    refresh = st.slider(
        "التحديث بالثواني",
        min_value=30,
        max_value=300,
        value=60,
        step=30,
    )

    st.divider()

    if st.button(
        "🛑 إيقاف البوت",
        use_container_width=True,
    ):
        st.session_state.kill_switch = True
        st.session_state.pending_signal = None
        log_event("تم تفعيل Kill Switch.")

    if st.button(
        "▶️ تشغيل البوت",
        use_container_width=True,
    ):
        st.session_state.kill_switch = False
        log_event("تم إلغاء Kill Switch.")

    if st.button(
        "♻️ إعادة Paper Trading",
        use_container_width=True,
    ):
        st.session_state.paper_balance = START_BALANCE
        st.session_state.paper_trade = None
        st.session_state.paper_history = []
        st.session_state.pending_signal = None
        st.session_state.daily_loss_money = 0.0
        st.session_state.daily_trades = 0
        st.session_state.day_start_balance = START_BALANCE
        st.session_state.last_signal_key = None
        st.session_state.last_managed_candle = None
        log_event("تمت إعادة Paper Trading.")


# =========================================================
# Load
# =========================================================
try:

    raw = load_m5()

    market = analyze(raw)

except Exception as exc:

    st.error(
        f"تعذر تشغيل البوت: {exc}"
    )

    st.stop()


m5 = market["m5"]
m15 = market["m15"]
h1 = market["h1"]
h4 = market["h4"]

ts = market["ts"]

price = market["price"]

st.session_state.last_price = price


# =========================================================
# Live trade management
# =========================================================
manage_live_trade(m5)


# =========================================================
# Pending signal execution
# =========================================================
execute_pending_signal(m5)


# =========================================================
# Generate new signal
# =========================================================
signal = market["signal"]

if not st.session_state.kill_switch:

    if signal is not None:

        signal_key = (
            signal["direction"],
            signal["b2"]["retest_time"],
        )

        if (
            signal_key
            != st.session_state.last_signal_key
            and st.session_state.paper_trade is None
            and st.session_state.pending_signal is None
            and daily_loss_pct() < MAX_DAILY_LOSS
            and st.session_state.daily_trades
            < MAX_TRADES_PER_DAY
        ):

            intended_entry_time = (
                ts + pd.Timedelta(minutes=5)
            )

            st.session_state.pending_signal = {
                "signal": signal,
                "intended_entry_time": intended_entry_time,
            }

            st.session_state.last_signal_key = signal_key
            st.session_state.last_signal_time = ts

            log_event(
                f"إشارة {signal['direction']} "
                f"جاهزة — الدخول سيكون على شمعة M5 التالية."
            )


# =========================================================
# Top metrics
# =========================================================
c1, c2, c3, c4 = st.columns(4)

with c1:
    metric_card(
        "سعر XAU/USD",
        fmt_price(price),
    )

with c2:
    metric_card(
        "قوة التوافق",
        f"{market['confluence']:.1f}%",
    )

with c3:
    metric_card(
        "رصيد Paper",
        f"{st.session_state.paper_balance:,.2f}",
    )

with c4:
    metric_card(
        "خسارة اليوم",
        f"{daily_loss_pct():.2f}%",
    )


st.markdown("### الإشارة الحالية")

signal_box(signal)


# =========================================================
# Status
# =========================================================
status_cols = st.columns(4)

status_cols[0].metric(
    "الحالة",
    "متوقف" if st.session_state.kill_switch else "يعمل",
)

status_cols[1].metric(
    "صفقات اليوم",
    f"{st.session_state.daily_trades}/{MAX_TRADES_PER_DAY}",
)

status_cols[2].metric(
    "حد الخسارة اليومية",
    f"{MAX_DAILY_LOSS:.1f}%",
)

status_cols[3].metric(
    "آخر شمعة",
    ts.strftime("%H:%M"),
)


# =========================================================
# Timeframes
# =========================================================
st.markdown("### تحليل الفريمات")

tf_cols = st.columns(4)

for col, tf in zip(
    tf_cols,
    ["M5", "M15", "H1", "H4"],
):

    score = market["scores"][tf]

    row = market["rows"][tf]

    with col:

        if np.isfinite(score):
            trend = trend_label(score)
            score_text = f"{score:+.0f}"
        else:
            trend = "غير جاهز"
            score_text = "-"

        st.markdown(
            f"""
            <div class="card">
                <h3>{tf}</h3>
                <div><b>الاتجاه:</b> {trend}</div>
                <div><b>Score:</b> {score_text}</div>
                <div><b>RSI:</b> {row["rsi"]:.1f}</div>
                <div><b>ADX:</b> {row["adx"]:.1f}</div>
                <div><b>MACD:</b> {row["macd"]:.3f}</div>
                <div><b>ATR:</b> {row["atr"]:.3f}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# =========================================================
# B2
# =========================================================
st.markdown("### Breakout + Retest B2")

b2 = (
    signal["b2"]
    if signal is not None
    else None
)

if b2:

    b1, b2c, b3, b4 = st.columns(4)

    b1.metric(
        "الاتجاه",
        b2["direction"],
    )

    b2c.metric(
        "مستوى الاختراق",
        fmt_price(b2["level"]),
    )

    b3.metric(
        "وقت الاختراق",
        b2["breakout_time"].strftime(
            "%H:%M"
        ),
    )

    b4.metric(
        "وقت إعادة الاختبار",
        b2["retest_time"].strftime(
            "%H:%M"
        ),
    )

else:

    st.info(
        "لا يوجد Breakout + Retest B2 مكتمل على آخر شمعة M15."
    )


# =========================================================
# Pending signal
# =========================================================
if st.session_state.pending_signal:

    pending = st.session_state.pending_signal

    st.markdown(
        f"""
        <div class="card">
            <h3>⏳ إشارة معلقة</h3>
            <p>
            الاتجاه:
            <b>{pending["signal"]["direction"]}</b>
            </p>
            <p>
            وقت الدخول المتوقع:
            <b>
            {pd.Timestamp(pending["intended_entry_time"]).strftime("%H:%M")}
            </b>
            </p>
            <p class="small-muted">
            سيتم استخدام افتتاح شمعة M5 التالية بدلاً من استخدام السعر الحالي.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )


# =========================================================
# Current trade
# =========================================================
st.markdown("### صفقة Paper الحالية")

trade = st.session_state.paper_trade

if trade is None:

    st.info("لا توجد صفقة مفتوحة حالياً.")

else:

    t1, t2, t3, t4 = st.columns(4)

    t1.metric(
        "الاتجاه",
        trade["direction"],
    )

    t2.metric(
        "الدخول",
        fmt_price(trade["entry"]),
    )

    t3.metric(
        "SL",
        fmt_price(trade["sl"]),
    )

    t4.metric(
        "TP2",
        fmt_price(trade["tp2"]),
    )

    st.markdown(
        f"""
        <div class="card">
            <b>TP1:</b> {fmt_price(trade["tp1"])}
            &nbsp;&nbsp; | &nbsp;&nbsp;
            <b>Break-even:</b>
            {"مفعل" if trade["be_active"] else "غير مفعل"}
            &nbsp;&nbsp; | &nbsp;&nbsp;
            <b>Confluence:</b>
            {trade["confluence"]:.1f}%
        </div>
        """,
        unsafe_allow_html=True,
    )


# =========================================================
# Paper statistics
# =========================================================
st.markdown("### إحصائيات Paper Trading")

history = pd.DataFrame(
    st.session_state.paper_history
)

if history.empty:

    p1, p2, p3, p4 = st.columns(4)

    p1.metric("الصفقات", "0")
    p2.metric("Win Rate", "0%")
    p3.metric("P&L", "0.00")
    p4.metric("Profit Factor", "0.00")

else:

    wins = history[
        history["pnl"] > 0
    ]

    losses = history[
        history["pnl"] < 0
    ]

    win_rate = (
        len(wins) / len(history) * 100
    )

    gross_profit = wins["pnl"].sum()
    gross_loss = abs(losses["pnl"].sum())

    pf = (
        gross_profit / gross_loss
        if gross_loss > 0
        else np.inf
    )

    total_pnl = history["pnl"].sum()

    p1, p2, p3, p4 = st.columns(4)

    p1.metric(
        "الصفقات",
        len(history),
    )

    p2.metric(
        "Win Rate",
        f"{win_rate:.1f}%",
    )

    p3.metric(
        "P&L",
        f"{total_pnl:,.2f}",
    )

    p4.metric(
        "Profit Factor",
        (
            f"{pf:.2f}"
            if np.isfinite(pf)
            else "∞"
        ),
    )

    st.dataframe(
        history.sort_values(
            "entry_time",
            ascending=False,
        ),
        use_container_width=True,
        hide_index=True,
    )


# =========================================================
# Backtest
# =========================================================
st.markdown("### Backtest")

st.info(
    "الاختبار يستخدم البيانات التاريخية المتاحة حالياً من Twelve Data "
    "ويفصل 70% In-Sample و30% Out-of-Sample. "
    "النتائج استكشافية وليست ضماناً للأداء المستقبلي."
)

if len(m5) < 1500:

    st.warning(
        "عدد البيانات المتاحة محدود نسبياً. "
        "لا تعتمد على النتائج كتحقق نهائي للنظام."
    )

if st.button(
    "▶️ تشغيل Backtest",
    use_container_width=True,
):

    with st.spinner("جاري تشغيل الاختبار..."):

        bt_trades, bt = run_backtest(
            m5
        )

    if bt_trades.empty:

        st.warning(
            "لم ينتج الاختبار أي صفقات."
        )

    else:

        st.markdown("#### In-Sample")

        i1, i2, i3, i4 = st.columns(4)

        i1.metric(
            "Trades",
            bt["is_trades"],
        )

        i2.metric(
            "Win Rate",
            f"{bt['is_win_rate']:.1f}%",
        )

        i3.metric(
            "Profit Factor",
            (
                f"{bt['is_pf']:.2f}"
                if np.isfinite(bt["is_pf"])
                else "∞"
            ),
        )

        i4.metric(
            "Total R",
            f"{bt['is_r']:.2f}R",
        )

        st.markdown("#### Out-of-Sample")

        o1, o2, o3, o4 = st.columns(4)

        o1.metric(
            "Trades",
            bt["oos_trades"],
        )

        o2.metric(
            "Win Rate",
            f"{bt['oos_win_rate']:.1f}%",
        )

        o3.metric(
            "Profit Factor",
            (
                f"{bt['oos_pf']:.2f}"
                if np.isfinite(bt["oos_pf"])
                else "∞"
            ),
        )

        o4.metric(
            "Total R",
            f"{bt['oos_r']:.2f}R",
        )

        st.metric(
            "Max Drawdown",
            f"{bt['max_dd']:,.2f}",
        )

        if bt["oos_trades"] < 30:

            st.warning(
                "عدد صفقات OOS أقل من 30، لذلك العينة صغيرة "
                "ولا تكفي للحكم الإحصائي على الاستراتيجية."
            )

        elif bt["oos_trades"] < 50:

            st.warning(
                "عدد صفقات OOS أقل من 50. "
                "النتيجة أفضل من عينة صغيرة جداً لكنها ما زالت تحتاج بيانات أكثر."
            )

        st.markdown("#### صفقات OOS")

        oos_start = pd.Timestamp(
            bt["oos_start"]
        )

        oos_table = bt_trades[
            pd.to_datetime(
                bt_trades["entry_time"],
                utc=True,
            ) >= oos_start
        ].copy()

        if not oos_table.empty:

            st.dataframe(
                oos_table.sort_values(
                    "entry_time",
                    ascending=False,
                ),
                use_container_width=True,
                hide_index=True,
            )


# =========================================================
# Logs
# =========================================================
st.markdown("### سجل القرارات")

if st.session_state.logs:

    st.dataframe(
        pd.DataFrame(
            {
                "السجل": st.session_state.logs
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

else:

    st.info("لا توجد سجلات حتى الآن.")


# =========================================================
# Data quality notice
# =========================================================
st.markdown(
    """
<div class="card">
    <h3>⚠️ ملاحظة البيانات</h3>
    <p>
    البيانات الحالية تعتمد على آخر 5000 شمعة M5 متاحة من Twelve Data.
    هذا مناسب للتشغيل التجريبي ومراقبة البوت، لكنه ليس تاريخاً كافياً
    لإثبات قوة الاستراتيجية على المدى الطويل، خصوصاً مع استخدام H4 وEMA100.
    </p>
    <p class="small-muted">
    قوة التوافق Confluence ليست احتمال ربح وليست Win Probability.
    </p>
</div>
""",
    unsafe_allow_html=True,
)


# =========================================================
# Auto refresh
# =========================================================
time.sleep(refresh)
st.rerun()

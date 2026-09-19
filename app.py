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

# Risk controls
DEFAULT_RISK = 1.0
MAX_DAILY_LOSS = 3.0
MAX_TRADES_PER_DAY = 5

# Strategy
SL_ATR = 1.5
TP1_ATR = 1.5
TP2_ATR = 2.5
RETEST_ATR = 0.35

# Simulation
SPREAD = 0.20
SLIPPAGE = 0.05


# =========================================================
# UI
# =========================================================

st.markdown("""
<style>
.stApp {
    background:#f4f6f8;
    color:#101828;
}

.block-container {
    max-width:1400px;
    padding-top:1rem;
}

h1,h2,h3,h4 {
    color:#101828 !important;
}

.title {
    font-size:30px;
    font-weight:800;
    color:#101828;
}

.subtitle {
    color:#667085;
    font-size:14px;
    margin-bottom:18px;
}

.card {
    background:#fff;
    border:1px solid #d0d5dd;
    border-radius:10px;
    padding:15px;
    margin-bottom:12px;
}

.buy {
    color:#067647 !important;
    font-weight:800;
}

.sell {
    color:#b42318 !important;
    font-weight:800;
}

.neutral {
    color:#475467 !important;
    font-weight:800;
}

.small {
    color:#667085;
    font-size:12px;
}

.signal {
    background:#fff;
    border:2px solid #d0d5dd;
    border-radius:12px;
    padding:20px;
    text-align:center;
}

.signal-buy {
    border-color:#12b76a;
}

.signal-sell {
    border-color:#f04438;
}

.signal-wait {
    border-color:#98a2b3;
}

.warning {
    background:#fffaeb;
    border:1px solid #fedf89;
    border-radius:8px;
    padding:10px;
    color:#92400e;
}

.success {
    background:#ecfdf3;
    border:1px solid #abefc6;
    border-radius:8px;
    padding:10px;
    color:#067647;
}

.danger {
    background:#fef3f2;
    border:1px solid #fecdca;
    border-radius:8px;
    padding:10px;
    color:#b42318;
}

.trade-open {
    background:#ecfdf3;
    border:1px solid #12b76a;
    border-radius:10px;
    padding:15px;
}

.trade-closed {
    background:#fff;
    border:1px solid #d0d5dd;
    border-radius:10px;
    padding:15px;
}

div[data-testid="stMetric"] {
    background:#fff;
    border:1px solid #d0d5dd;
    border-radius:8px;
    padding:10px;
}

footer {
    visibility:hidden;
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
    "last_signal_time": None,
    "day_start_balance": START_BALANCE,
    "day_key": None,
    "daily_trades": 0,
    "daily_loss": 0.0,
    "last_price": None,
}

for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


# =========================================================
# HELPERS
# =========================================================

def utc_now():
    return datetime.now(timezone.utc)


def log_event(message, level="INFO"):
    st.session_state.logs.insert(
        0,
        {
            "time": utc_now().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "level": level,
            "message": message,
        },
    )
    st.session_state.logs = st.session_state.logs[:500]


def reset_daily_limits_if_needed():
    today = utc_now().strftime("%Y-%m-%d")

    if st.session_state.day_key != today:
        st.session_state.day_key = today
        st.session_state.day_start_balance = (
            st.session_state.paper_balance
        )
        st.session_state.daily_trades = 0
        st.session_state.daily_loss = 0.0

        log_event(
            f"بدأ يوم تداول جديد: {today}",
            "SYSTEM",
        )


def fmt(x, d=2):
    if x is None:
        return "-"
    try:
        if pd.isna(x):
            return "-"
    except Exception:
        pass
    return f"{float(x):,.{d}f}"


def trend(score):
    if score >= 5:
        return "صاعد"
    if score <= -5:
        return "هابط"
    return "محايد"


def trend_cls(score):
    if score >= 5:
        return "buy"
    if score <= -5:
        return "sell"
    return "neutral"


# =========================================================
# DATA
# =========================================================

def get_api_key():
    try:
        return st.secrets["TWELVE_DATA_API_KEY"]
    except Exception:
        return None


@st.cache_data(ttl=45, show_spinner=False)
def load_m5():
    key = get_api_key()

    if not key:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY غير موجود في Streamlit Secrets."
        )

    params = {
        "symbol": SYMBOL,
        "interval": "5min",
        "outputsize": 5000,
        "apikey": key,
        "timezone": "UTC",
        "format": "JSON",
    }

    r = requests.get(
        API_URL,
        params=params,
        timeout=20,
    )

    r.raise_for_status()

    data = r.json()

    if data.get("status") == "error":
        raise RuntimeError(
            data.get("message", "Twelve Data error")
        )

    values = data.get("values")

    if not values:
        raise RuntimeError(
            "لم تصل بيانات XAU/USD."
        )

    df = pd.DataFrame(values)

    df["datetime"] = pd.to_datetime(
        df["datetime"],
        utc=True,
        errors="coerce",
    )

    for c in ["open", "high", "low", "close", "volume"]:
        if c in df:
            df[c] = pd.to_numeric(
                df[c],
                errors="coerce",
            )

    if "volume" not in df:
        df["volume"] = 0

    df = (
        df.dropna(
            subset=[
                "datetime",
                "open",
                "high",
                "low",
                "close",
            ]
        )
        .sort_values("datetime")
        .set_index("datetime")
    )

    return df


def completed_m5(df):
    if df.empty:
        return df

    boundary = pd.Timestamp.now(
        tz="UTC"
    ).floor("5min")

    return df[df.index < boundary].copy()


# =========================================================
# RESAMPLE
# =========================================================

def resample_tf(df, rule):
    if df.empty:
        return pd.DataFrame()

    return (
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
        .dropna(
            subset=[
                "open",
                "high",
                "low",
                "close",
            ]
        )
    )


def latest_before(df, ts):
    if df.empty:
        return None

    x = df[df.index <= ts]

    if x.empty:
        return None

    return x.iloc[-1]


# =========================================================
# INDICATORS
# =========================================================

def ema(s, n):
    return s.ewm(
        span=n,
        adjust=False,
        min_periods=n,
    ).mean()


def rsi(s, n=14):
    delta = s.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    ag = gain.ewm(
        alpha=1 / n,
        adjust=False,
        min_periods=n,
    ).mean()

    al = loss.ewm(
        alpha=1 / n,
        adjust=False,
        min_periods=n,
    ).mean()

    rs = ag / al.replace(0, np.nan)

    return 100 - 100 / (1 + rs)


def atr(df, n=14):
    pc = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - pc).abs(),
            (df["low"] - pc).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / n,
        adjust=False,
        min_periods=n,
    ).mean()


def macd(s):
    fast = ema(s, 12)
    slow = ema(s, 26)

    line = fast - slow

    signal = line.ewm(
        span=9,
        adjust=False,
        min_periods=9,
    ).mean()

    return line, signal, line - signal


def adx(df, n=14):
    up = df["high"].diff()
    down = -df["low"].diff()

    plus_dm = pd.Series(
        np.where(
            (up > down) & (up > 0),
            up,
            0,
        ),
        index=df.index,
    )

    minus_dm = pd.Series(
        np.where(
            (down > up) & (down > 0),
            down,
            0,
        ),
        index=df.index,
    )

    pc = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - pc).abs(),
            (df["low"] - pc).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr_w = tr.ewm(
        alpha=1 / n,
        adjust=False,
        min_periods=n,
    ).mean()

    plus_di = (
        100
        * plus_dm.ewm(
            alpha=1 / n,
            adjust=False,
            min_periods=n,
        ).mean()
        / atr_w
    )

    minus_di = (
        100
        * minus_dm.ewm(
            alpha=1 / n,
            adjust=False,
            min_periods=n,
        ).mean()
        / atr_w
    )

    dx = (
        100
        * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(
            0,
            np.nan,
        )
    )

    adx_v = dx.ewm(
        alpha=1 / n,
        adjust=False,
        min_periods=n,
    ).mean()

    return adx_v, plus_di, minus_di


def indicators(df):
    x = df.copy()

    x["ema20"] = ema(x["close"], 20)
    x["ema50"] = ema(x["close"], 50)
    x["ema100"] = ema(x["close"], 100)

    x["rsi"] = rsi(x["close"])

    (
        x["macd"],
        x["macd_signal"],
        x["macd_hist"],
    ) = macd(x["close"])

    x["atr"] = atr(x)

    x["momentum"] = x["close"].diff(10)

    (
        x["adx"],
        x["plus_di"],
        x["minus_di"],
    ) = adx(x)

    return x


def score_row(r):
    score = 0

    if (
        r["ema20"] > r["ema50"]
        and r["ema50"] > r["ema100"]
    ):
        score += 3

    elif (
        r["ema20"] < r["ema50"]
        and r["ema50"] < r["ema100"]
    ):
        score -= 3

    score += 1 if r["close"] > r["ema20"] else -1

    if 52 <= r["rsi"] <= 68:
        score += 2

    elif 32 <= r["rsi"] < 48:
        score -= 2

    if (
        r["macd"] > r["macd_signal"]
        and r["macd_hist"] > 0
    ):
        score += 2

    elif (
        r["macd"] < r["macd_signal"]
        and r["macd_hist"] < 0
    ):
        score -= 2

    score += 1 if r["momentum"] > 0 else -1

    if r["adx"] >= 25:
        if r["plus_di"] > r["minus_di"]:
            score += 2
        elif r["minus_di"] > r["plus_di"]:
            score -= 2

    return int(score)


def prepare(df):
    x = indicators(df)

    x["score"] = x.apply(
        score_row,
        axis=1,
    )

    return x


# =========================================================
# B2 BREAKOUT / RETEST
# =========================================================

def b2_signal(m15, ts):
    if len(m15) < 20:
        return None

    d = m15[m15.index <= ts]

    if len(d) < 10:
        return None

    retest = d.iloc[-1]
    retest_time = d.index[-1]

    a = retest["atr"]

    if pd.isna(a) or a <= 0:
        return None

    first = max(
        1,
        len(d) - 7,
    )

    for i in range(
        len(d) - 2,
        first - 1,
        -1,
    ):
        breakout = d.iloc[i]
        previous = d.iloc[i - 1]

        breakout_time = d.index[i]

        if breakout_time >= retest_time:
            continue

        # BUY breakout
        if (
            breakout["close"]
            > previous["high"]
            and breakout["high"]
            > previous["high"]
        ):

            level = previous["high"]

            touched = (
                retest["low"]
                <= level + RETEST_ATR * a
                and retest["low"]
                >= level - RETEST_ATR * a
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

        # SELL breakout
        if (
            breakout["close"]
            < previous["low"]
            and breakout["low"]
            < previous["low"]
        ):

            level = previous["low"]

            touched = (
                retest["high"]
                >= level - RETEST_ATR * a
                and retest["high"]
                <= level + RETEST_ATR * a
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
# LIVE ENGINE
# =========================================================

def analyze(raw):
    m5 = completed_m5(raw)

    if len(m5) < 200:
        return None

    m15 = prepare(
        resample_tf(m5, "15min")
    )

    h1 = prepare(
        resample_tf(m5, "1h")
    )

    h4 = prepare(
        resample_tf(m5, "4h")
    )

    m5 = prepare(m5)

    if (
        len(m15) < 20
        or len(h1) < 10
        or len(h4) < 5
    ):
        return None

    ts = m5.index[-1]

    r5 = m5.iloc[-1]
    r15 = latest_before(m15, ts)
    r1 = latest_before(h1, ts)
    r4 = latest_before(h4, ts)

    if (
        r15 is None
        or r1 is None
        or r4 is None
    ):
        return None

    scores = {
        "M5": int(r5["score"]),
        "M15": int(r15["score"]),
        "H1": int(r1["score"]),
        "H4": int(r4["score"]),
    }

    buy = (
        scores["M5"] >= 5
        and scores["M15"] >= 5
        and scores["H1"] >= 5
        and r15["adx"] >= 20
        and r15["rsi"] >= 50
        and r15["macd"] > 0
    )

    sell = (
        scores["M5"] <= -5
        and scores["M15"] <= -5
        and scores["H1"] <= -5
        and r15["adx"] >= 20
        and r15["rsi"] <= 50
        and r15["macd"] < 0
    )

    b2 = b2_signal(
        m15,
        ts,
    )

    signal = "WAIT"

    if (
        buy
        and b2
        and b2["direction"] == "BUY"
    ):
        signal = "BUY"

    elif (
        sell
        and b2
        and b2["direction"] == "SELL"
    ):
        signal = "SELL"

    total = sum(scores.values())

    confidence = (
        abs(total) / 44
    ) * 100

    a = float(r5["atr"])

    if pd.isna(a) or a <= 0:
        return None

    future = raw[
        raw.index > ts
    ]

    entry_available = not future.empty

    if entry_available:
        next_open = float(
            future.iloc[0]["open"]
        )
    else:
        next_open = float(
            r5["close"]
        )

    if signal == "BUY":
        entry = (
            next_open
            + SPREAD / 2
            + SLIPPAGE
        )

        sl = entry - SL_ATR * a
        tp1 = entry + TP1_ATR * a
        tp2 = entry + TP2_ATR * a

    elif signal == "SELL":
        entry = (
            next_open
            - SPREAD / 2
            - SLIPPAGE
        )

        sl = entry + SL_ATR * a
        tp1 = entry - TP1_ATR * a
        tp2 = entry - TP2_ATR * a

    else:
        entry = float(r5["close"])
        sl = None
        tp1 = None
        tp2 = None

    support = float(
        m15["low"].tail(40).min()
    )

    resistance = float(
        m15["high"].tail(40).max()
    )

    return {
        "signal": signal,
        "time": ts,
        "price": float(r5["close"]),
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "atr": a,
        "scores": scores,
        "confidence": confidence,
        "support": support,
        "resistance": resistance,
        "m5": r5,
        "m15": r15,
        "h1": r1,
        "h4": r4,
        "b2": b2,
        "entry_available": entry_available,
    }


# =========================================================
# RISK ENGINE
# =========================================================

def risk_blocked():
    reset_daily_limits_if_needed()

    if st.session_state.kill_switch:
        return True, "Kill Switch مفعل"

    if (
        st.session_state.daily_loss
        >= MAX_DAILY_LOSS
    ):
        return True, (
            "تم الوصول إلى حد الخسارة اليومية"
        )

    if (
        st.session_state.daily_trades
        >= MAX_TRADES_PER_DAY
    ):
        return True, (
            "تم الوصول إلى الحد اليومي للصفقات"
        )

    return False, ""


def open_trade(live, risk_pct):
    if st.session_state.paper_trade:
        return False

    blocked, reason = risk_blocked()

    if blocked:
        log_event(
            f"رفض فتح الصفقة: {reason}",
            "REJECT",
        )
        return False

    if live["signal"] not in [
        "BUY",
        "SELL",
    ]:
        log_event(
            "رفض: لا توجد إشارة",
            "REJECT",
        )
        return False

    risk_money = (
        st.session_state.paper_balance
        * risk_pct
        / 100
    )

    trade = {
        "id": len(
            st.session_state.paper_history
        ) + 1,
        "direction": live["signal"],
        "signal_time": live["time"],
        "entry_time": utc_now(),
        "entry": live["entry"],
        "sl": live["sl"],
        "original_sl": live["sl"],
        "tp1": live["tp1"],
        "tp2": live["tp2"],
        "risk_money": risk_money,
        "risk_pct": risk_pct,
        "tp1_hit": False,
        "be_active": False,
        "trailing_active": False,
        "status": "OPEN",
        "exit": None,
        "exit_time": None,
        "result_r": None,
        "pnl": None,
        "reason": None,
    }

    st.session_state.paper_trade = trade
    st.session_state.daily_trades += 1

    log_event(
        f"OPEN {trade['direction']} "
        f"Entry={trade['entry']:.2f} "
        f"SL={trade['sl']:.2f} "
        f"TP1={trade['tp1']:.2f} "
        f"TP2={trade['tp2']:.2f}",
        "TRADE",
    )

    return True


def close_trade(price, reason):
    trade = st.session_state.paper_trade

    if not trade:
        return

    direction = trade["direction"]

    if direction == "BUY":
        risk = (
            trade["original_sl"]
            and trade["entry"]
            - trade["original_sl"]
        )

        r = (
            price - trade["entry"]
        ) / risk

    else:
        risk = (
            trade["original_sl"]
            - trade["entry"]
        )

        r = (
            trade["entry"] - price
        ) / risk

    # TP1 partial realization:
    # 50% closes at TP1, remaining 50% continues.
    if trade["tp1_hit"]:
        r = (
            0.5 * 1.0
            + 0.5 * r
        )

    pnl = (
        trade["risk_money"] * r
    )

    st.session_state.paper_balance += pnl

    if pnl < 0:
        loss_pct = (
            abs(pnl)
            / st.session_state.day_start_balance
            * 100
        )
        st.session_state.daily_loss += loss_pct

    trade["exit"] = price
    trade["exit_time"] = utc_now()
    trade["result_r"] = r
    trade["pnl"] = pnl
    trade["reason"] = reason
    trade["status"] = "CLOSED"

    st.session_state.paper_history.append(
        trade.copy()
    )

    log_event(
        f"CLOSE {direction} "
        f"Reason={reason} "
        f"R={r:.2f} "
        f"PnL={pnl:.2f}",
        "TRADE",
    )

    st.session_state.paper_trade = None


def update_trade(candle):
    trade = st.session_state.paper_trade

    if not trade:
        return

    high = float(candle["high"])
    low = float(candle["low"])

    direction = trade["direction"]

    # -----------------------------------------------------
    # SL FIRST
    # -----------------------------------------------------

    if direction == "BUY":

        if low <= trade["sl"]:
            close_trade(
                trade["sl"],
                "SL",
            )
            return

        # TP1
        if (
            not trade["tp1_hit"]
            and high >= trade["tp1"]
        ):
            trade["tp1_hit"] = True
            trade["be_active"] = True

            # Move SL to break-even
            trade["sl"] = trade["entry"]

            log_event(
                "TP1 hit — 50% realized — SL moved to BE",
                "MANAGEMENT",
            )

        # TP2
        if high >= trade["tp2"]:
            close_trade(
                trade["tp2"],
                "TP2",
            )
            return

    else:

        if high >= trade["sl"]:
            close_trade(
                trade["sl"],
                "SL",
            )
            return

        if (
            not trade["tp1_hit"]
            and low <= trade["tp1"]
        ):
            trade["tp1_hit"] = True
            trade["be_active"] = True

            trade["sl"] = trade["entry"]

            log_event(
                "TP1 hit — 50% realized — SL moved to BE",
                "MANAGEMENT",
            )

        if low <= trade["tp2"]:
            close_trade(
                trade["tp2"],
                "TP2",
            )
            return


# =========================================================
# METRICS
# =========================================================

def metrics(df):
    if df is None or df.empty:
        return {
            "trades": 0,
            "win_rate": 0,
            "pf": 0,
            "total_r": 0,
            "avg_r": 0,
            "dd": 0,
        }

    r = df["result_r"].astype(float)

    wins = r[r > 0]
    losses = r[r < 0]

    gp = wins.sum()
    gl = abs(losses.sum())

    pf = (
        gp / gl
        if gl > 0
        else np.inf
    )

    equity = r.cumsum()

    dd = (
        equity.cummax()
        - equity
    ).max()

    return {
        "trades": len(r),
        "win_rate": (
            (r > 0).mean() * 100
        ),
        "pf": pf,
        "total_r": r.sum(),
        "avg_r": r.mean(),
        "dd": dd,
    }


# =========================================================
# BACKTEST
# =========================================================

def run_backtest(raw):
    data = completed_m5(raw)

    if len(data) < 1000:
        return None

    m5 = prepare(data)

    m15 = prepare(
        resample_tf(
            data,
            "15min",
        )
    )

    h1 = prepare(
        resample_tf(
            data,
            "1h",
        )
    )

    h4 = prepare(
        resample_tf(
            data,
            "4h",
        )
    )

    split = int(
        len(m5) * 0.70
    )

    trades = []

    open_position = None

    for i in range(
        200,
        len(m5) - 1,
    ):

        ts = m5.index[i]
        candle = m5.iloc[i]

        r15 = latest_before(
            m15,
            ts,
        )

        r1 = latest_before(
            h1,
            ts,
        )

        r4 = latest_before(
            h4,
            ts,
        )

        if (
            r15 is None
            or r1 is None
            or r4 is None
        ):
            continue

        scores = {
            "M5": int(candle["score"]),
            "M15": int(r15["score"]),
            "H1": int(r1["score"]),
            "H4": int(r4["score"]),
        }

        buy = (
            scores["M5"] >= 5
            and scores["M15"] >= 5
            and scores["H1"] >= 5
            and r15["adx"] >= 20
            and r15["rsi"] >= 50
            and r15["macd"] > 0
        )

        sell = (
            scores["M5"] <= -5
            and scores["M15"] <= -5
            and scores["H1"] <= -5
            and r15["adx"] >= 20
            and r15["rsi"] <= 50
            and r15["macd"] < 0
        )

        b2 = b2_signal(
            m15,
            ts,
        )

        signal = None

        if (
            buy
            and b2
            and b2["direction"] == "BUY"
        ):
            signal = "BUY"

        elif (
            sell
            and b2
            and b2["direction"] == "SELL"
        ):
            signal = "SELL"

        # -------------------------------------------------
        # MANAGE
        # -------------------------------------------------

        if open_position:

            high = float(
                candle["high"]
            )

            low = float(
                candle["low"]
            )

            direction = (
                open_position["direction"]
            )

            exit_price = None
            reason = None

            if direction == "BUY":

                if low <= open_position["sl"]:
                    exit_price = (
                        open_position["sl"]
                    )
                    reason = "SL"

                elif high >= open_position["tp"]:
                    exit_price = (
                        open_position["tp"]
                    )
                    reason = "TP"

            else:

                if high >= open_position["sl"]:
                    exit_price = (
                        open_position["sl"]
                    )
                    reason = "SL"

                elif low <= open_position["tp"]:
                    exit_price = (
                        open_position["tp"]
                    )
                    reason = "TP"

            if exit_price is not None:

                if direction == "BUY":

                    risk = (
                        open_position["entry"]
                        - open_position["sl"]
                    )

                    r = (
                        exit_price
                        - open_position["entry"]
                    ) / risk

                else:

                    risk = (
                        open_position["sl"]
                        - open_position["entry"]
                    )

                    r = (
                        open_position["entry"]
                        - exit_price
                    ) / risk

                trades.append(
                    {
                        "direction": direction,
                        "signal_time": open_position[
                            "signal_time"
                        ],
                        "entry": open_position[
                            "entry"
                        ],
                        "exit": exit_price,
                        "result_r": r,
                        "reason": reason,
                    }
                )

                open_position = None

                continue

        # -------------------------------------------------
        # OPEN
        # -------------------------------------------------

        if (
            open_position is None
            and signal
        ):

            next_candle = m5.iloc[i + 1]

            entry = (
                float(next_candle["open"])
            )

            a = float(
                candle["atr"]
            )

            if signal == "BUY":

                entry += (
                    SPREAD / 2
                    + SLIPPAGE
                )

                sl = (
                    entry
                    - SL_ATR * a
                )

                tp = (
                    entry
                    + TP2_ATR * a
                )

            else:

                entry -= (
                    SPREAD / 2
                    + SLIPPAGE
                )

                sl = (
                    entry
                    + SL_ATR * a
                )

                tp = (
                    entry
                    - TP2_ATR * a
                )

            open_position = {
                "direction": signal,
                "signal_time": ts,
                "entry": entry,
                "sl": sl,
                "tp": tp,
            }

    if open_position:

        final_price = float(
            m5.iloc[-1]["close"]
        )

        if (
            open_position["direction"]
            == "BUY"
        ):

            risk = (
                open_position["entry"]
                - open_position["sl"]
            )

            r = (
                final_price
                - open_position["entry"]
            ) / risk

        else:

            risk = (
                open_position["sl"]
                - open_position["entry"]
            )

            r = (
                open_position["entry"]
                - final_price
            ) / risk

        trades.append(
            {
                "direction":
                    open_position["direction"],
                "signal_time":
                    open_position["signal_time"],
                "entry":
                    open_position["entry"],
                "exit":
                    final_price,
                "result_r": r,
                "reason": "END",
            }
        )

    if not trades:
        return None

    df = pd.DataFrame(trades)

    oos_start = m5.index[split]

    df["period"] = np.where(
        pd.to_datetime(
            df["signal_time"]
        ) < oos_start,
        "IS",
        "OOS",
    )

    return df


# =========================================================
# HEADER
# =========================================================

reset_daily_limits_if_needed()

st.markdown(
    '<div class="title">بوت الذهب XAU/USD</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="subtitle">'
    'Smart Paper Trading • M5 / M15 / H1 / H4 • '
    'B2 Breakout + Retest'
    '</div>',
    unsafe_allow_html=True,
)


# =========================================================
# SIDEBAR
# =========================================================

with st.sidebar:

    st.header("إعدادات التداول")

    risk_pct = st.number_input(
        "المخاطرة لكل صفقة %",
        min_value=0.1,
        max_value=3.0,
        value=DEFAULT_RISK,
        step=0.1,
    )

    st.divider()

    st.write(
        f"حد الخسارة اليومية: {MAX_DAILY_LOSS}%"
    )

    st.write(
        f"الحد الأقصى للصفقات اليومية: "
        f"{MAX_TRADES_PER_DAY}"
    )

    st.write(
        f"Spread simulation: {SPREAD}"
    )

    st.write(
        f"Slippage simulation: {SLIPPAGE}"
    )

    st.divider()

    if st.session_state.kill_switch:

        st.error(
            "Kill Switch مفعل"
        )

        if st.button(
            "إلغاء Kill Switch",
            use_container_width=True,
        ):
            st.session_state.kill_switch = False
            log_event(
                "تم إلغاء Kill Switch",
                "SYSTEM",
            )
            st.rerun()

    else:

        if st.button(
            "إيقاف التداول",
            use_container_width=True,
        ):
            st.session_state.kill_switch = True

            log_event(
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
        st.session_state.last_signal_time = None
        st.session_state.daily_trades = 0
        st.session_state.daily_loss = 0
        st.session_state.day_start_balance = START_BALANCE

        log_event(
            "تم تصفير Paper Trading",
            "SYSTEM",
        )

        st.rerun()


# =========================================================
# DATA
# =========================================================

try:
    raw = load_m5()
except Exception as e:

    st.error(
        f"خطأ في تحميل البيانات: {e}"
    )

    st.stop()


st.session_state.last_price = float(
    raw["close"].iloc[-1]
)


# =========================================================
# TABS
# =========================================================

live_tab, paper_tab, backtest_tab, logs_tab = st.tabs(
    [
        "📊 التداول",
        "💰 Paper Trading",
        "🧪 Backtest",
        "📋 السجل",
    ]
)


# =========================================================
# LIVE
# =========================================================

with live_tab:

    @st.fragment(run_every="60s")
    def live_panel():

        try:

            data = load_m5()

            live = analyze(data)

            if live is None:

                st.warning(
                    "جاري تجهيز بيانات الفريمات..."
                )

                return

            # -------------------------------------------------
            # Manage existing paper trade
            # -------------------------------------------------

            completed = completed_m5(data)

            if (
                st.session_state.paper_trade
                and not completed.empty
            ):

                update_trade(
                    completed.iloc[-1]
                )

            # -------------------------------------------------
            # Auto-open
            # -------------------------------------------------

            if (
                live["signal"]
                in ["BUY", "SELL"]
                and live["entry_available"]
                and st.session_state.paper_trade
                is None
            ):

                if (
                    st.session_state.last_signal_time
                    != live["time"]
                ):

                    open_trade(
                        live,
                        risk_pct,
                    )

                    st.session_state.last_signal_time = (
                        live["time"]
                    )

            # -------------------------------------------------
            # Main metrics
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
                "Paper Balance",
                f"${st.session_state.paper_balance:,.2f}",
            )

            c4.metric(
                "Daily Loss",
                f"{st.session_state.daily_loss:.2f}%",
            )

            signal = live["signal"]

            if signal == "BUY":
                cls = "signal signal-buy"
            elif signal == "SELL":
                cls = "signal signal-sell"
            else:
                cls = "signal signal-wait"

            st.markdown(
                f"""
                <div class="{cls}">
                    <div class="small">
                        الإشارة الحالية
                    </div>
                    <div style="
                        font-size:40px;
                        font-weight:900;
                    ">
                        {signal}
                    </div>
                    <div class="small">
                        {live["time"]}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # -------------------------------------------------
            # Timeframes
            # -------------------------------------------------

            st.subheader(
                "اتجاه الفريمات"
            )

            cols = st.columns(4)

            for col, tf in zip(
                cols,
                ["M5", "M15", "H1", "H4"],
            ):

                s = live["scores"][tf]

                col.markdown(
                    f"""
                    <div class="card">
                        <div class="small">
                            {tf}
                        </div>
                        <div class="{trend_cls(s)}"
                             style="
                             font-size:24px;
                             ">
                            {trend(s)}
                        </div>
                        <div class="small">
                            Score: {s}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            # -------------------------------------------------
            # Trade plan
            # -------------------------------------------------

            st.subheader(
                "خطة الصفقة"
            )

            a, b, c, d = st.columns(4)

            a.metric(
                "Entry",
                fmt(live["entry"]),
            )

            b.metric(
                "SL",
                fmt(live["sl"]),
            )

            c.metric(
                "TP1",
                fmt(live["tp1"]),
            )

            d.metric(
                "TP2",
                fmt(live["tp2"]),
            )

            if signal in [
                "BUY",
                "SELL",
            ]:

                st.success(
                    "الإشارة مكتملة. "
                    "الدخول محسوب من افتتاح شمعة M5 التالية."
                )

            else:

                st.info(
                    "لا توجد إشارة مكتملة حالياً."
                )

            # -------------------------------------------------
            # B2
            # -------------------------------------------------

            st.subheader(
                "Breakout + Retest"
            )

            if live["b2"]:

                st.markdown(
                    f"""
                    <div class="success">
                    <b>{live["b2"]["direction"]}</b><br>
                    Level:
                    {live["b2"]["level"]:.2f}<br>
                    Breakout:
                    {live["b2"]["breakout_time"]}<br>
                    Retest:
                    {live["b2"]["retest_time"]}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            else:

                st.info(
                    "لا يوجد B2 مكتمل."
                )

            # -------------------------------------------------
            # Indicators
            # -------------------------------------------------

            st.subheader(
                "المؤشرات"
            )

            x1, x2, x3, x4, x5, x6 = st.columns(6)

            x1.metric(
                "RSI M15",
                fmt(live["m15"]["rsi"], 1),
            )

            x2.metric(
                "MACD M15",
                fmt(live["m15"]["macd"], 3),
            )

            x3.metric(
                "ADX M15",
                fmt(live["m15"]["adx"], 1),
            )

            x4.metric(
                "EMA20",
                fmt(live["m15"]["ema20"]),
            )

            x5.metric(
                "EMA50",
                fmt(live["m15"]["ema50"]),
            )

            x6.metric(
                "EMA100",
                fmt(live["m15"]["ema100"]),
            )

            # -------------------------------------------------
            # S/R
            # -------------------------------------------------

            st.subheader(
                "Support / Resistance"
            )

            s1, s2 = st.columns(2)

            s1.metric(
                "Support",
                fmt(live["support"]),
            )

            s2.metric(
                "Resistance",
                fmt(live["resistance"]),
            )

            # -------------------------------------------------
            # Open trade
            # -------------------------------------------------

            st.subheader(
                "الصفقة الحالية"
            )

            trade = (
                st.session_state.paper_trade
            )

            if trade:

                st.markdown(
                    f"""
                    <div class="trade-open">
                    <b>{trade["direction"]}</b><br>
                    Entry: {trade["entry"]:.2f}<br>
                    SL: {trade["sl"]:.2f}<br>
                    TP1:
                    {trade["tp1"]:.2f}
                    —
                    {"تم" if trade["tp1_hit"] else "لم يتم"}<br>
                    TP2:
                    {trade["tp2"]:.2f}<br>
                    Break-even:
                    {"فعال" if trade["be_active"] else "غير فعال"}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            else:

                st.info(
                    "لا توجد صفقة مفتوحة."
                )

            # -------------------------------------------------
            # Risk status
            # -------------------------------------------------

            blocked, reason = risk_blocked()

            if blocked:

                st.markdown(
                    f"""
                    <div class="danger">
                    <b>التداول متوقف:</b>
                    {reason}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            else:

                st.markdown(
                    """
                    <div class="success">
                    نظام Paper Trading جاهز.
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            st.caption(
                "التحديث التلقائي كل 60 ثانية أثناء بقاء الصفحة مفتوحة."
            )

        except Exception as e:

            st.error(
                f"خطأ في محرك التداول: {e}"
            )

    live_panel()


# =========================================================
# PAPER TAB
# =========================================================

with paper_tab:

    st.subheader(
        "Paper Trading"
    )

    history = (
        st.session_state.paper_history
    )

    wins = sum(
        x["result_r"] > 0
        for x in history
        if x["result_r"] is not None
    )

    win_rate = (
        wins / len(history) * 100
        if history
        else 0
    )

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Balance",
        f"${st.session_state.paper_balance:,.2f}",
    )

    c2.metric(
        "P&L",
        f"${st.session_state.paper_balance - START_BALANCE:,.2f}",
    )

    c3.metric(
        "Trades",
        len(history),
    )

    c4.metric(
        "Win Rate",
        f"{win_rate:.1f}%",
    )

    if history:

        df = pd.DataFrame(
            history
        )

        st.dataframe(
            df[
                [
                    "id",
                    "direction",
                    "entry",
                    "sl",
                    "tp1",
                    "tp2",
                    "exit",
                    "result_r",
                    "pnl",
                    "reason",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    else:

        st.info(
            "لم تغلق أي صفقة Paper حتى الآن."
        )


# =========================================================
# BACKTEST TAB
# =========================================================

with backtest_tab:

    st.subheader(
        "Backtest"
    )

    st.markdown(
        """
        <div class="warning">
        الباك تست يستخدم البيانات التاريخية المتاحة من Twelve Data.
        لا يعتبر ضماناً للأداء المستقبلي.
        تتم محاكاة Spread وSlippage.
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button(
        "تشغيل Backtest",
        type="primary",
        use_container_width=True,
    ):

        with st.spinner(
            "جاري الاختبار..."
        ):

            try:

                result = run_backtest(
                    raw
                )

                if result is None:

                    st.error(
                        "لا توجد بيانات كافية."
                    )

                else:

                    is_df = result[
                        result["period"]
                        == "IS"
                    ]

                    oos_df = result[
                        result["period"]
                        == "OOS"
                    ]

                    is_m = metrics(
                        is_df
                    )

                    oos_m = metrics(
                        oos_df
                    )

                    st.subheader(
                        "In-Sample"
                    )

                    a, b, c, d, e, f = st.columns(6)

                    a.metric(
                        "Trades",
                        is_m["trades"],
                    )

                    b.metric(
                        "Win Rate",
                        f"{is_m['win_rate']:.1f}%",
                    )

                    c.metric(
                        "PF",
                        (
                            "∞"
                            if np.isinf(
                                is_m["pf"]
                            )
                            else f"{is_m['pf']:.2f}"
                        ),
                    )

                    d.metric(
                        "Total R",
                        f"{is_m['total_r']:.2f}",
                    )

                    e.metric(
                        "Avg R",
                        f"{is_m['avg_r']:.3f}",
                    )

                    f.metric(
                        "Max DD",
                        f"{is_m['dd']:.2f}R",
                    )

                    st.subheader(
                        "Out-of-Sample"
                    )

                    a, b, c, d, e, f = st.columns(6)

                    a.metric(
                        "Trades",
                        oos_m["trades"],
                    )

                    b.metric(
                        "Win Rate",
                        f"{oos_m['win_rate']:.1f}%",
                    )

                    c.metric(
                        "PF",
                        (
                            "∞"
                            if np.isinf(
                                oos_m["pf"]
                            )
                            else f"{oos_m['pf']:.2f}"
                        ),
                    )

                    d.metric(
                        "Total R",
                        f"{oos_m['total_r']:.2f}",
                    )

                    e.metric(
                        "Avg R",
                        f"{oos_m['avg_r']:.3f}",
                    )

                    f.metric(
                        "Max DD",
                        f"{oos_m['dd']:.2f}R",
                    )

                    if oos_m["trades"] < 50:

                        st.markdown(
                            f"""
                            <div class="warning">
                            OOS يحتوي على
                            <b>{oos_m["trades"]}</b>
                            صفقة فقط.
                            العينة صغيرة ولا تكفي للحكم النهائي.
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                    if not oos_df.empty:

                        equity = (
                            oos_df[
                                "result_r"
                            ]
                            .cumsum()
                        )

                        st.subheader(
                            "OOS Equity"
                        )

                        st.line_chart(
                            equity,
                            use_container_width=True,
                        )

                        st.subheader(
                            "OOS Trades"
                        )

                        st.dataframe(
                            oos_df[
                                [
                                    "direction",
                                    "signal_time",
                                    "entry",
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
                    f"Backtest error: {e}"
                )


# =========================================================
# LOGS
# =========================================================

with logs_tab:

    st.subheader(
        "System Logs"
    )

    if st.session_state.logs:

        st.dataframe(
            pd.DataFrame(
                st.session_state.logs
            ),
            use_container_width=True,
            hide_index=True,
        )

    else:

        st.info(
            "لا توجد سجلات حتى الآن."
        )


# =========================================================
# FOOTER
# =========================================================

st.divider()

st.caption(
    "بوت الذهب XAU/USD | Paper Trading فقط"
)

st.caption(
    "لا يتم إرسال أوامر حقيقية إلى Derayah أو أي وسيط."
)

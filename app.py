import streamlit as st
import pandas as pd
import numpy as np
import requests
from datetime import datetime

# =========================================================
# PAGE CONFIG
# =========================================================

st.set_page_config(
    page_title="بوت الذهب XAU/USD",
    page_icon="🟡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

SYMBOL = "XAU/USD"
API_URL = "https://api.twelvedata.com/time_series"

st.title("🟡 بوت الذهب XAU/USD")
st.caption(
    "Smart Paper Trading — Multi-Timeframe + Backtest + Out-of-Sample"
)

# =========================================================
# API KEY
# =========================================================

try:
    API_KEY = st.secrets["TWELVE_DATA_API_KEY"]
except Exception:
    API_KEY = ""

if not API_KEY:
    st.error(
        "لم يتم العثور على TWELVE_DATA_API_KEY في Streamlit Secrets."
    )
    st.stop()


# =========================================================
# DATA — TWELVE DATA
# =========================================================

@st.cache_data(ttl=65, show_spinner=False)
def get_candles(interval, outputsize=300, end_date=None):

    params = {
        "symbol": SYMBOL,
        "interval": interval,
        "outputsize": outputsize,
        "order": "asc",
        "timezone": "UTC",
        "apikey": API_KEY
    }

    if end_date is not None:
        params["end_date"] = end_date

    try:

        response = requests.get(
            API_URL,
            params=params,
            timeout=30
        )

        data = response.json()

        message = str(
            data.get("message", "")
        )

        # -------------------------------------------------
        # RATE LIMIT
        # -------------------------------------------------

        if (
            data.get("code") == 429
            or "run out of api credits" in message.lower()
            or "current limit" in message.lower()
        ):

            return pd.DataFrame()

        # -------------------------------------------------
        # API ERROR
        # -------------------------------------------------

        if data.get("status") != "ok":
            return pd.DataFrame()

        values = data.get("values", [])

        if not values:
            return pd.DataFrame()

        df = pd.DataFrame(values)

        required = [
            "datetime",
            "open",
            "high",
            "low",
            "close"
        ]

        if not all(
            col in df.columns
            for col in required
        ):
            return pd.DataFrame()

        df["datetime"] = pd.to_datetime(
            df["datetime"],
            errors="coerce"
        )

        for col in [
            "open",
            "high",
            "low",
            "close"
        ]:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df = (
            df
            .dropna(
                subset=[
                    "datetime",
                    "open",
                    "high",
                    "low",
                    "close"
                ]
            )
            .drop_duplicates("datetime")
            .sort_values("datetime")
            .reset_index(drop=True)
        )

        return df

    except Exception:
        return pd.DataFrame()


# =========================================================
# HISTORICAL M5
# =========================================================

@st.cache_data(ttl=300, show_spinner=False)
def get_historical_m5(total_bars=15000):

    chunks = []

    remaining = total_bars
    end_date = None

    while remaining > 0:

        request_size = min(
            5000,
            remaining
        )

        df = get_candles(
            "5min",
            outputsize=request_size,
            end_date=end_date
        )

        if df.empty:
            break

        chunks.append(df)

        earliest = df["datetime"].min()

        new_end = (
            earliest
            - pd.Timedelta(minutes=5)
        )

        if end_date is not None:

            old_end = pd.to_datetime(
                end_date
            )

            if new_end >= old_end:
                break

        end_date = new_end.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        remaining -= len(df)

        if len(df) < request_size:
            break

    if not chunks:
        return pd.DataFrame()

    result = (
        pd.concat(
            chunks,
            ignore_index=True
        )
        .drop_duplicates("datetime")
        .sort_values("datetime")
        .tail(total_bars)
        .reset_index(drop=True)
    )

    return result


# =========================================================
# INDICATORS
# =========================================================

def calculate_indicators(df):

    df = df.copy()

    close = df["close"]
    high = df["high"]
    low = df["low"]

    # -----------------------------------------------------
    # EMA
    # -----------------------------------------------------

    df["ema20"] = (
        close
        .ewm(
            span=20,
            adjust=False
        )
        .mean()
    )

    df["ema50"] = (
        close
        .ewm(
            span=50,
            adjust=False
        )
        .mean()
    )

    df["ema100"] = (
        close
        .ewm(
            span=100,
            adjust=False
        )
        .mean()
    )

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------

    delta = close.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = (
        gain
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
    )

    avg_loss = (
        loss
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
    )

    rs = (
        avg_gain
        /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    df["rsi"] = (
        100
        -
        (
            100
            /
            (1 + rs)
        )
    )

    # -----------------------------------------------------
    # MACD
    # -----------------------------------------------------

    ema12 = (
        close
        .ewm(
            span=12,
            adjust=False
        )
        .mean()
    )

    ema26 = (
        close
        .ewm(
            span=26,
            adjust=False
        )
        .mean()
    )

    df["macd"] = (
        ema12 - ema26
    )

    df["macd_signal"] = (
        df["macd"]
        .ewm(
            span=9,
            adjust=False
        )
        .mean()
    )

    df["macd_hist"] = (
        df["macd"]
        -
        df["macd_signal"]
    )

    # -----------------------------------------------------
    # ATR
    # -----------------------------------------------------

    previous_close = close.shift(1)

    tr1 = high - low

    tr2 = (
        high - previous_close
    ).abs()

    tr3 = (
        low - previous_close
    ).abs()

    true_range = tr1.copy()

    true_range = true_range.where(
        tr2 <= true_range,
        tr2
    )

    true_range = true_range.where(
        tr3 <= true_range,
        tr3
    )

    df["atr"] = (
        true_range
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
    )

    # -----------------------------------------------------
    # MOMENTUM
    # -----------------------------------------------------

    df["momentum"] = (
        close.diff(10)
    )

    # -----------------------------------------------------
    # ADX / DI
    # -----------------------------------------------------

    up_move = high.diff()

    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where(
            (
                (up_move > down_move)
                &
                (up_move > 0)
            ),
            up_move,
            0
        ),
        index=df.index
    )

    minus_dm = pd.Series(
        np.where(
            (
                (down_move > up_move)
                &
                (down_move > 0)
            ),
            down_move,
            0
        ),
        index=df.index
    )

    atr_safe = (
        df["atr"]
        .replace(
            0,
            np.nan
        )
    )

    df["plus_di"] = (
        100
        *
        plus_dm
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
        /
        atr_safe
    )

    df["minus_di"] = (
        100
        *
        minus_dm
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
        /
        atr_safe
    )

    di_sum = (
        df["plus_di"]
        +
        df["minus_di"]
    ).replace(
        0,
        np.nan
    )

    dx = (
        100
        *
        (
            df["plus_di"]
            -
            df["minus_di"]
        ).abs()
        /
        di_sum
    )

    df["adx"] = (
        dx
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
    )

    return df


# =========================================================
# RESAMPLE
# =========================================================

def resample_ohlc(df, rule):

    result = (
        df
        .set_index("datetime")
        [
            [
                "open",
                "high",
                "low",
                "close"
            ]
        ]
        .resample(
            rule,
            label="left",
            closed="left"
        )
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last"
            }
        )
        .dropna()
        .reset_index()
    )

    return result


# =========================================================
# SCORE
# =========================================================

def score_timeframe(
    row,
    prefix=""
):

    def value(name):

        return row.get(
            f"{prefix}{name}",
            np.nan
        )

    ema20 = value("ema20")
    ema50 = value("ema50")
    ema100 = value("ema100")
    close = value("close")
    rsi = value("rsi")
    macd = value("macd")
    macd_signal = value(
        "macd_signal"
    )
    macd_hist = value(
        "macd_hist"
    )
    momentum = value(
        "momentum"
    )
    adx = value("adx")
    plus_di = value(
        "plus_di"
    )
    minus_di = value(
        "minus_di"
    )

    values = [
        ema20,
        ema50,
        ema100,
        close,
        rsi,
        macd,
        macd_signal,
        macd_hist,
        momentum,
        adx,
        plus_di,
        minus_di
    ]

    if any(
        pd.isna(v)
        for v in values
    ):
        return 0

    score = 0

    # EMA structure

    if (
        ema20
        >
        ema50
        >
        ema100
    ):
        score += 3

    elif (
        ema20
        <
        ema50
        <
        ema100
    ):
        score -= 3

    # Price / EMA20

    if close > ema20:
        score += 1

    elif close < ema20:
        score -= 1

    # RSI

    if (
        52
        <=
        rsi
        <=
        68
    ):
        score += 2

    elif (
        32
        <=
        rsi
        <
        48
    ):
        score -= 2

    # MACD

    if (
        macd > macd_signal
        and macd_hist > 0
    ):
        score += 2

    elif (
        macd < macd_signal
        and macd_hist < 0
    ):
        score -= 2

    # Momentum

    if momentum > 0:
        score += 1

    elif momentum < 0:
        score -= 1

    # ADX + DI

    if adx >= 25:

        if plus_di > minus_di:
            score += 2

        elif minus_di > plus_di:
            score -= 2

    return score


def trend_from_score(score):

    if score >= 5:
        return "صاعد 📈"

    if score <= -5:
        return "هابط 📉"

    return "محايد ↔️"


# =========================================================
# SUPPORT / RESISTANCE
# =========================================================

def get_support_resistance(m15):

    if len(m15) < 50:
        return np.nan, np.nan

    support = (
        m15["low"]
        .iloc[-50:]
        .min()
    )

    resistance = (
        m15["high"]
        .iloc[-50:]
        .max()
    )

    return support, resistance


# =========================================================
# BREAKOUT / RETEST
# =========================================================

def breakout_retest(m15):

    if len(m15) < 25:
        return "لا توجد بيانات كافية"

    current = m15.iloc[-1]

    previous = m15.iloc[-2]

    resistance = (
        m15["high"]
        .iloc[-21:-1]
        .max()
    )

    support = (
        m15["low"]
        .iloc[-21:-1]
        .min()
    )

    atr = current["atr"]

    if (
        pd.isna(atr)
        or atr <= 0
    ):
        return "لا يوجد"

    if (
        current["close"]
        >
        resistance
        and
        previous["close"]
        <=
        resistance
    ):
        return "كسر مقاومة 📈"

    if (
        current["close"]
        <
        support
        and
        previous["close"]
        >=
        support
    ):
        return "كسر دعم 📉"

    if (
        abs(
            current["close"]
            -
            resistance
        )
        <=
        atr * 0.5
    ):
        return "إعادة اختبار مقاومة"

    if (
        abs(
            current["close"]
            -
            support
        )
        <=
        atr * 0.5
    ):
        return "إعادة اختبار دعم"

    return "لا يوجد كسر أو إعادة اختبار واضحة"


# =========================================================
# LIVE DATA
# =========================================================

@st.cache_data(
    ttl=65,
    show_spinner=False
)
def get_live_market_data():

    # -----------------------------------------------------
    # IMPORTANT:
    # ONE API REQUEST ONLY
    # M5 is used to construct M15 and H1 locally.
    # -----------------------------------------------------

    m5 = get_candles(
        "5min",
        300
    )

    if m5.empty:
        return None

    # Build higher timeframes locally

    m15_raw = resample_ohlc(
        m5,
        "15min"
    )

    h1_raw = resample_ohlc(
        m5,
        "1h"
    )

    if (
        m15_raw.empty
        or h1_raw.empty
    ):
        return None

    m5 = calculate_indicators(
        m5
    )

    m15 = calculate_indicators(
        m15_raw
    )

    h1 = calculate_indicators(
        h1_raw
    )

    return (
        m5,
        m15,
        h1
    )


# =========================================================
# LIVE ANALYSIS
# =========================================================

def live_analysis():

    data = get_live_market_data()

    if data is None:
        return None

    m5, m15, h1 = data

    a5 = m5.iloc[-1]
    a15 = m15.iloc[-1]
    ah1 = h1.iloc[-1]

    s5 = score_timeframe(
        a5
    )

    s15 = score_timeframe(
        a15
    )

    sh1 = score_timeframe(
        ah1
    )

    trend5 = trend_from_score(
        s5
    )

    trend15 = trend_from_score(
        s15
    )

    trendh1 = trend_from_score(
        sh1
    )

    total_score = (
        s5
        +
        s15
        +
        sh1
    )

    confidence = min(
        100,
        round(
            abs(total_score)
            /
            30
            *
            100
        )
    )

    # -----------------------------------------------------
    # BUY
    # -----------------------------------------------------

    buy_conditions = (
        s5 >= 5
        and
        s15 >= 5
        and
        sh1 >= 5
        and
        a15["adx"] >= 20
        and
        a15["rsi"] >= 50
        and
        a15["macd"] > 0
    )

    # -----------------------------------------------------
    # SELL
    # -----------------------------------------------------

    sell_conditions = (
        s5 <= -5
        and
        s15 <= -5
        and
        sh1 <= -5
        and
        a15["adx"] >= 20
        and
        a15["rsi"] <= 50
        and
        a15["macd"] < 0
    )

    signal = "WAIT"

    if buy_conditions:
        signal = "BUY"

    elif sell_conditions:
        signal = "SELL"

    price = float(
        a5["close"]
    )

    atr = float(
        a15["atr"]
    )

    sl = np.nan
    tp1 = np.nan
    tp2 = np.nan

    if signal == "BUY":

        sl = (
            price
            -
            1.5 * atr
        )

        tp1 = (
            price
            +
            1.5 * atr
        )

        tp2 = (
            price
            +
            2.5 * atr
        )

    elif signal == "SELL":

        sl = (
            price
            +
            1.5 * atr
        )

        tp1 = (
            price
            -
            1.5 * atr
        )

        tp2 = (
            price
            -
            2.5 * atr
        )

    support, resistance = (
        get_support_resistance(
            m15
        )
    )

    return {
        "price": price,
        "signal": signal,
        "confidence": confidence,
        "trend5": trend5,
        "trend15": trend15,
        "trendh1": trendh1,
        "score5": s5,
        "score15": s15,
        "scoreh1": sh1,
        "total_score": total_score,
        "rsi": float(a15["rsi"]),
        "adx": float(a15["adx"]),
        "macd": float(a15["macd"]),
        "atr": atr,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "support": support,
        "resistance": resistance,
        "breakout": breakout_retest(m15),
        "m5": m5,
        "m15": m15,
        "h1": h1
    }


# =========================================================
# BACKTEST PREPARATION
# =========================================================

def prepare_backtest_data(
    m5_raw
):

    m5 = calculate_indicators(
        m5_raw.copy()
    )

    m15_raw = resample_ohlc(
        m5_raw,
        "15min"
    )

    h1_raw = resample_ohlc(
        m5_raw,
        "1h"
    )

    m15 = calculate_indicators(
        m15_raw
    )

    h1 = calculate_indicators(
        h1_raw
    )

    # -----------------------------------------------------
    # Previous support / resistance
    # -----------------------------------------------------

    m15["prev_resistance"] = (
        m15["high"]
        .shift(1)
        .rolling(20)
        .max()
    )

    m15["prev_support"] = (
        m15["low"]
        .shift(1)
        .rolling(20)
        .min()
    )

    m15["breakout_buy"] = (
        (
            m15["close"]
            >
            m15["prev_resistance"]
        )
        &
        (
            m15["close"].shift(1)
            <=
            m15["prev_resistance"].shift(1)
        )
    )

    m15["breakout_sell"] = (
        (
            m15["close"]
            <
            m15["prev_support"]
        )
        &
        (
            m15["close"].shift(1)
            >=
            m15["prev_support"].shift(1)
        )
    )

    # -----------------------------------------------------
    # Higher timeframe availability
    # -----------------------------------------------------

    m15a = m15.copy()

    m15a["available_at"] = (
        m15a["datetime"]
        +
        pd.Timedelta(
            minutes=15
        )
    )

    h1a = h1.copy()

    h1a["available_at"] = (
        h1a["datetime"]
        +
        pd.Timedelta(
            hours=1
        )
    )

    m15_cols = [
        "close",
        "ema20",
        "ema50",
        "ema100",
        "rsi",
        "macd",
        "macd_signal",
        "macd_hist",
        "atr",
        "momentum",
        "adx",
        "plus_di",
        "minus_di",
        "prev_resistance",
        "prev_support",
        "breakout_buy",
        "breakout_sell"
    ]

    m15a = m15a[
        ["available_at"]
        +
        m15_cols
    ]

    m15a = m15a.rename(
        columns={
            c: f"m15_{c}"
            for c in m15_cols
        }
    )

    h1_cols = [
        "close",
        "ema20",
        "ema50",
        "ema100",
        "rsi",
        "macd",
        "macd_signal",
        "macd_hist",
        "atr",
        "momentum",
        "adx",
        "plus_di",
        "minus_di"
    ]

    h1a = h1a[
        ["available_at"]
        +
        h1_cols
    ]

    h1a = h1a.rename(
        columns={
            c: f"h1_{c}"
            for c in h1_cols
        }
    )

    # -----------------------------------------------------
    # M5 signal available after candle closes
    # -----------------------------------------------------

    base = m5.copy()

    base["signal_time"] = (
        base["datetime"]
        +
        pd.Timedelta(
            minutes=5
        )
    )

    base = base.sort_values(
        "signal_time"
    )

    m15a = m15a.sort_values(
        "available_at"
    )

    h1a = h1a.sort_values(
        "available_at"
    )

    merged = pd.merge_asof(
        base,
        m15a,
        left_on="signal_time",
        right_on="available_at",
        direction="backward"
    )

    merged = pd.merge_asof(
        merged.sort_values(
            "signal_time"
        ),
        h1a,
        left_on="signal_time",
        right_on="available_at",
        direction="backward"
    )

    merged = merged.dropna(
        subset=[
            "ema20",
            "m15_ema20",
            "h1_ema20",
            "m15_atr"
        ]
    )

    return (
        merged.reset_index(drop=True),
        m15,
        h1
    )


# =========================================================
# BACKTEST SIGNAL
# =========================================================

def backtest_signal(row):

    score5 = score_timeframe(
        row
    )

    score15 = score_timeframe(
        row,
        "m15_"
    )

    scoreh1 = score_timeframe(
        row,
        "h1_"
    )

    total_score = (
        score5
        +
        score15
        +
        scoreh1
    )

    buy = (
        score5 >= 5
        and
        score15 >= 5
        and
        scoreh1 >= 5
        and
        row["m15_adx"] >= 20
        and
        row["m15_rsi"] >= 50
        and
        row["m15_macd"] > 0
    )

    sell = (
        score5 <= -5
        and
        score15 <= -5
        and
        scoreh1 <= -5
        and
        row["m15_adx"] >= 20
        and
        row["m15_rsi"] <= 50
        and
        row["m15_macd"] < 0
    )

    if buy:
        return "BUY", total_score

    if sell:
        return "SELL", total_score

    return "WAIT", total_score


# =========================================================
# TRADE SIMULATION
# =========================================================

def simulate_segment(
    df,
    start_idx,
    end_idx
):

    trades = []

    i = start_idx

    while i < end_idx - 1:

        row = df.iloc[i]

        signal, total_score = (
            backtest_signal(row)
        )

        if signal == "WAIT":

            i += 1
            continue

        entry_idx = i + 1

        if entry_idx >= end_idx:
            break

        entry_row = df.iloc[
            entry_idx
        ]

        entry_price = float(
            entry_row["open"]
        )

        atr = float(
            row["m15_atr"]
        )

        if (
            not np.isfinite(atr)
            or
            atr <= 0
        ):

            i += 1
            continue

        risk = (
            1.5 * atr
        )

        if signal == "BUY":

            sl = (
                entry_price
                -
                risk
            )

            tp1 = (
                entry_price
                +
                1.5 * atr
            )

            tp2 = (
                entry_price
                +
                2.5 * atr
            )

        else:

            sl = (
                entry_price
                +
                risk
            )

            tp1 = (
                entry_price
                -
                1.5 * atr
            )

            tp2 = (
                entry_price
                -
                2.5 * atr
            )

        exit_idx = None
        exit_price = None
        result = None

        # -------------------------------------------------
        # Candle-by-candle execution
        # -------------------------------------------------

        for j in range(
            entry_idx,
            end_idx
        ):

            future = df.iloc[j]

            high = float(
                future["high"]
            )

            low = float(
                future["low"]
            )

            if signal == "BUY":

                hit_sl = (
                    low <= sl
                )

                hit_tp = (
                    high >= tp2
                )

                # Conservative:
                # SL first if both occur
                # inside the same candle.

                if hit_sl and hit_tp:

                    exit_idx = j
                    exit_price = sl
                    result = "SL"

                    break

                if hit_sl:

                    exit_idx = j
                    exit_price = sl
                    result = "SL"

                    break

                if hit_tp:

                    exit_idx = j
                    exit_price = tp2
                    result = "TP2"

                    break

            else:

                hit_sl = (
                    high >= sl
                )

                hit_tp = (
                    low <= tp2
                )

                if hit_sl and hit_tp:

                    exit_idx = j
                    exit_price = sl
                    result = "SL"

                    break

                if hit_sl:

                    exit_idx = j
                    exit_price = sl
                    result = "SL"

                    break

                if hit_tp:

                    exit_idx = j
                    exit_price = tp2
                    result = "TP2"

                    break

        # -------------------------------------------------
        # Close at segment end
        # -------------------------------------------------

        if exit_idx is None:

            exit_idx = (
                end_idx - 1
            )

            exit_price = float(
                df.iloc[
                    exit_idx
                ]["close"]
            )

            result = "END"

        # -------------------------------------------------
        # R MULTIPLE
        # -------------------------------------------------

        if signal == "BUY":

            r_multiple = (
                exit_price
                -
                entry_price
            ) / risk

        else:

            r_multiple = (
                entry_price
                -
                exit_price
            ) / risk

        trades.append(
            {
                "direction": signal,
                "signal_time": row[
                    "signal_time"
                ],
                "entry_time": entry_row[
                    "datetime"
                ],
                "exit_time": df.iloc[
                    exit_idx
                ]["datetime"],
                "entry": round(
                    entry_price,
                    3
                ),
                "SL": round(
                    sl,
                    3
                ),
                "TP1": round(
                    tp1,
                    3
                ),
                "TP2": round(
                    tp2,
                    3
                ),
                "exit": round(
                    exit_price,
                    3
                ),
                "result": result,
                "R": round(
                    float(
                        r_multiple
                    ),
                    4
                ),
                "score": total_score
            }
        )

        # Prevent overlapping trades

        i = (
            exit_idx
            +
            1
        )

    return pd.DataFrame(
        trades
    )


# =========================================================
# METRICS
# =========================================================

def calculate_metrics(
    trades
):

    if trades.empty:

        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "total_r": 0,
            "average_r": 0,
            "max_drawdown": 0,
            "buy_trades": 0,
            "sell_trades": 0
        }

    r = (
        trades["R"]
        .astype(float)
    )

    wins = int(
        (r > 0).sum()
    )

    losses = int(
        (r < 0).sum()
    )

    total = len(trades)

    win_rate = (
        wins
        /
        total
        *
        100
    )

    gross_profit = (
        r[r > 0].sum()
    )

    gross_loss = abs(
        r[r < 0].sum()
    )

    if gross_loss > 0:

        profit_factor = (
            gross_profit
            /
            gross_loss
        )

    else:

        profit_factor = (
            np.inf
            if gross_profit > 0
            else 0
        )

    equity = r.cumsum()

    running_max = (
        equity.cummax()
    )

    drawdown = (
        equity
        -
        running_max
    )

    max_drawdown = abs(
        drawdown.min()
    )

    return {
        "trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "total_r": r.sum(),
        "average_r": r.mean(),
        "max_drawdown": max_drawdown,
        "buy_trades": int(
            (
                trades["direction"]
                ==
                "BUY"
            ).sum()
        ),
        "sell_trades": int(
            (
                trades["direction"]
                ==
                "SELL"
            ).sum()
        )
    }


# =========================================================
# OOS
# =========================================================

@st.cache_data(
    ttl=600,
    show_spinner=False
)
def run_oos_test(
    total_bars
):

    historical = (
        get_historical_m5(
            total_bars
        )
    )

    if historical.empty:
        return None

    prepared, m15, h1 = (
        prepare_backtest_data(
            historical
        )
    )

    if len(prepared) < 300:
        return None

    # -----------------------------------------------------
    # 70 / 30 chronological split
    # -----------------------------------------------------

    split_index = int(
        len(prepared)
        *
        0.70
    )

    is_data = (
        prepared
        .iloc[:split_index]
        .copy()
    )

    oos_data = (
        prepared
        .iloc[split_index:]
        .copy()
    )

    is_trades = simulate_segment(
        prepared,
        0,
        split_index
    )

    oos_trades = simulate_segment(
        prepared,
        split_index,
        len(prepared)
    )

    is_metrics = (
        calculate_metrics(
            is_trades
        )
    )

    oos_metrics = (
        calculate_metrics(
            oos_trades
        )
    )

    return {
        "historical": historical,
        "prepared": prepared,
        "m15": m15,
        "h1": h1,
        "is_data": is_data,
        "oos_data": oos_data,
        "is_trades": is_trades,
        "oos_trades": oos_trades,
        "is_metrics": is_metrics,
        "oos_metrics": oos_metrics,
        "split_time": oos_data[
            "signal_time"
        ].iloc[0]
    }


# =========================================================
# LIVE DASHBOARD
# =========================================================

st.divider()

st.subheader(
    "📡 التحليل المباشر"
)

# ---------------------------------------------------------
# Refresh button
# ---------------------------------------------------------

if st.button(
    "🔄 تحديث التحليل",
    use_container_width=True
):

    get_candles.clear()
    get_live_market_data.clear()
    live_analysis.clear()

    st.rerun()


live = live_analysis()


if live is None:

    st.warning(
        "⚠️ تعذر الحصول على بيانات السوق الحالية."
    )

    st.info(
        "إذا ظهر Rate Limit من Twelve Data، "
        "انتظر دقيقة ثم اضغط تحديث التحليل مرة واحدة."
    )

else:

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "XAU/USD",
        f"${live['price']:,.2f}"
    )

    c2.metric(
        "Confidence",
        f"{live['confidence']}%"
    )

    signal_text = {
        "BUY": "🟢 BUY",
        "SELL": "🔴 SELL",
        "WAIT": "🟡 WAIT"
    }[
        live["signal"]
    ]

    c3.metric(
        "Signal",
        signal_text
    )

    # -----------------------------------------------------
    # Trends
    # -----------------------------------------------------

    st.markdown(
        "### الاتجاهات"
    )

    t1, t2, t3 = st.columns(3)

    t1.info(
        f"M5\n\n"
        f"{live['trend5']}\n\n"
        f"Score: {live['score5']}"
    )

    t2.info(
        f"M15\n\n"
        f"{live['trend15']}\n\n"
        f"Score: {live['score15']}"
    )

    t3.info(
        f"H1\n\n"
        f"{live['trendh1']}\n\n"
        f"Score: {live['scoreh1']}"
    )

    # -----------------------------------------------------
    # Levels
    # -----------------------------------------------------

    st.markdown(
        "### المستويات"
    )

    l1, l2, l3, l4 = st.columns(4)

    l1.metric(
        "Support M15",
        f"{live['support']:,.2f}"
    )

    l2.metric(
        "Resistance M15",
        f"{live['resistance']:,.2f}"
    )

    l3.metric(
        "ATR M15",
        f"{live['atr']:,.2f}"
    )

    l4.metric(
        "Breakout / Retest",
        live["breakout"]
    )

    # -----------------------------------------------------
    # Trade levels
    # -----------------------------------------------------

    if live["signal"] != "WAIT":

        st.markdown(
            "### 🎯 مستويات الصفقة"
        )

        p1, p2, p3 = st.columns(3)

        p1.metric(
            "SL",
            f"{live['sl']:,.2f}"
        )

        p2.metric(
            "TP1",
            f"{live['tp1']:,.2f}"
        )

        p3.metric(
            "TP2",
            f"{live['tp2']:,.2f}"
        )

    else:

        st.warning(
            "⛔ لم تتحقق شروط الدخول الكاملة."
        )

    # -----------------------------------------------------
    # M15 indicators
    # -----------------------------------------------------

    st.markdown(
        "### المؤشرات M15"
    )

    ind = pd.DataFrame(
        {
            "المؤشر": [
                "RSI",
                "ADX",
                "MACD",
                "ATR",
                "Momentum"
            ],
            "القيمة": [
                round(
                    live["rsi"],
                    3
                ),
                round(
                    live["adx"],
                    3
                ),
                round(
                    live["macd"],
                    3
                ),
                round(
                    live["atr"],
                    3
                ),
                round(
                    live["m15"][
                        "momentum"
                    ].iloc[-1],
                    3
                )
            ]
        }
    )

    st.dataframe(
        ind,
        use_container_width=True,
        hide_index=True
    )


# =========================================================
# BACKTEST
# =========================================================

st.divider()

st.subheader(
    "📊 Backtest + Out-of-Sample"
)

st.write(
    """
الاختبار يستخدم البيانات التاريخية زمنيًا:
70% In-Sample و30% Out-of-Sample.
الإشارة تُحسب عند إغلاق M5، والدخول يتم من افتتاح الشمعة التالية.
"""
)

st.caption(
    "Confidence = قوة توافق المؤشرات، وليس احتمال ربح."
)

bars_option = st.selectbox(
    "حجم البيانات التاريخية",
    options=[
        5000,
        10000,
        15000,
        30000
    ],
    index=2,
    format_func=lambda x:
        f"{x:,} شمعة M5"
)

st.caption(
    "زيادة البيانات تعني فترة تاريخية أطول وقد تتطلب عدة طلبات من Twelve Data."
)

run_test = st.button(
    "🚀 تشغيل اختبار OOS",
    use_container_width=True
)

if run_test:

    with st.spinner(
        "جاري تحميل البيانات وتشغيل الاختبار..."
    ):

        result = run_oos_test(
            bars_option
        )

    if result is None:

        st.error(
            "لم تتوفر بيانات تاريخية كافية لإجراء الاختبار."
        )

    else:

        historical = (
            result["historical"]
        )

        is_metrics = (
            result["is_metrics"]
        )

        oos_metrics = (
            result["oos_metrics"]
        )

        st.success(
            "تم تشغيل الاختبار بنجاح."
        )

        start_date = (
            historical[
                "datetime"
            ].min()
        )

        end_date = (
            historical[
                "datetime"
            ].max()
        )

        st.info(
            f"الفترة المستخدمة: "
            f"{start_date} → {end_date}"
        )

        st.info(
            f"نقطة فصل OOS: "
            f"{result['split_time']}"
        )

        # =================================================
        # IS
        # =================================================

        st.markdown(
            "## 🧪 In-Sample — 70%"
        )

        a, b, c, d = st.columns(4)

        a.metric(
            "Trades",
            is_metrics["trades"]
        )

        b.metric(
            "Win Rate",
            f"{is_metrics['win_rate']:.1f}%"
        )

        c.metric(
            "Profit Factor",
            (
                f"{is_metrics['profit_factor']:.2f}"
                if np.isfinite(
                    is_metrics[
                        "profit_factor"
                    ]
                )
                else "∞"
            )
        )

        d.metric(
            "Total R",
            f"{is_metrics['total_r']:.2f}R"
        )

        e, f, g, h = st.columns(4)

        e.metric(
            "Average R",
            f"{is_metrics['average_r']:.3f}R"
        )

        f.metric(
            "Max Drawdown",
            f"{is_metrics['max_drawdown']:.2f}R"
        )

        g.metric(
            "BUY",
            is_metrics["buy_trades"]
        )

        h.metric(
            "SELL",
            is_metrics["sell_trades"]
        )

        # =================================================
        # OOS
        # =================================================

        st.markdown(
            "## 🔬 Out-of-Sample — 30%"
        )

        a, b, c, d = st.columns(4)

        a.metric(
            "Trades",
            oos_metrics["trades"]
        )

        b.metric(
            "Win Rate",
            f"{oos_metrics['win_rate']:.1f}%"
        )

        c.metric(
            "Profit Factor",
            (
                f"{oos_metrics['profit_factor']:.2f}"
                if np.isfinite(
                    oos_metrics[
                        "profit_factor"
                    ]
                )
                else "∞"
            )
        )

        d.metric(
            "Total R",
            f"{oos_metrics['total_r']:.2f}R"
        )

        e, f, g, h = st.columns(4)

        e.metric(
            "Average R",
            f"{oos_metrics['average_r']:.3f}R"
        )

        f.metric(
            "Max Drawdown",
            f"{oos_metrics['max_drawdown']:.2f}R"
        )

        g.metric(
            "BUY",
            oos_metrics["buy_trades"]
        )

        h.metric(
            "SELL",
            oos_metrics["sell_trades"]
        )

        # =================================================
        # COMPARISON
        # =================================================

        st.markdown(
            "## ⚖️ مقارنة IS / OOS"
        )

        comparison = pd.DataFrame(
            {
                "المقياس": [
                    "عدد الصفقات",
                    "Win Rate %",
                    "Profit Factor",
                    "Total R",
                    "Average R",
                    "Max Drawdown R",
                    "BUY",
                    "SELL"
                ],
                "In-Sample": [
                    is_metrics[
                        "trades"
                    ],
                    round(
                        is_metrics[
                            "win_rate"
                        ],
                        2
                    ),
                    (
                        round(
                            is_metrics[
                                "profit_factor"
                            ],
                            3
                        )
                        if np.isfinite(
                            is_metrics[
                                "profit_factor"
                            ]
                        )
                        else "∞"
                    ),
                    round(
                        is_metrics[
                            "total_r"
                        ],
                        3
                    ),
                    round(
                        is_metrics[
                            "average_r"
                        ],
                        3
                    ),
                    round(
                        is_metrics[
                            "max_drawdown"
                        ],
                        3
                    ),
                    is_metrics[
                        "buy_trades"
                    ],
                    is_metrics[
                        "sell_trades"
                    ]
                ],
                "Out-of-Sample": [
                    oos_metrics[
                        "trades"
                    ],
                    round(
                        oos_metrics[
                            "win_rate"
                        ],
                        2
                    ),
                    (
                        round(
                            oos_metrics[
                                "profit_factor"
                            ],
                            3
                        )
                        if np.isfinite(
                            oos_metrics[
                                "profit_factor"
                            ]
                        )
                        else "∞"
                    ),
                    round(
                        oos_metrics[
                            "total_r"
                        ],
                        3
                    ),
                    round(
                        oos_metrics[
                            "average_r"
                        ],
                        3
                    ),
                    round(
                        oos_metrics[
                            "max_drawdown"
                        ],
                        3
                    ),
                    oos_metrics[
                        "buy_trades"
                    ],
                    oos_metrics[
                        "sell_trades"
                    ]
                ]
            }
        )

        st.dataframe(
            comparison,
            use_container_width=True,
            hide_index=True
        )

        # =================================================
        # OOS EQUITY
        # =================================================

        if not result[
            "oos_trades"
        ].empty:

            st.markdown(
                "## 📈 منحنى OOS Equity"
            )

            equity = result[
                "oos_trades"
            ][
                [
                    "exit_time",
                    "R"
                ]
            ].copy()

            equity[
                "Equity R"
            ] = (
                equity["R"]
                .cumsum()
            )

            equity = (
                equity
                .set_index(
                    "exit_time"
                )[
                    [
                        "Equity R"
                    ]
                ]
            )

            st.line_chart(
                equity
            )

        # =================================================
        # OOS TRADES
        # =================================================

        st.markdown(
            "## 📋 صفقات Out-of-Sample"
        )

        if result[
            "oos_trades"
        ].empty:

            st.warning(
                "لم تظهر أي صفقات في فترة OOS."
            )

        else:

            display_trades = (
                result[
                    "oos_trades"
                ].copy()
            )

            display_trades[
                "R"
            ] = (
                display_trades[
                    "R"
                ].round(3)
            )

            st.dataframe(
                display_trades,
                use_container_width=True,
                hide_index=True
            )

        # =================================================
        # SAMPLE SIZE
        # =================================================

        if (
            oos_metrics[
                "trades"
            ]
            <
            30
        ):

            st.warning(
                "⚠️ عدد صفقات OOS أقل من 30. "
                "العينة صغيرة جدًا للتحليل الإحصائي."
            )

        elif (
            oos_metrics[
                "trades"
            ]
            <
            100
        ):

            st.warning(
                "⚠️ عدد صفقات OOS بين 30 و99. "
                "النتيجة مبدئية وتحتاج عينة تاريخية أكبر."
            )

        else:

            st.success(
                "حجم عينة OOS تجاوز 100 صفقة."
            )

        # =================================================
        # COST WARNING
        # =================================================

        st.warning(
            "هذا الاختبار لا يتضمن Spread أو Slippage أو Commission. "
            "لذلك لا يمثل صافي الربح الفعلي القابل للتنفيذ."
        )

        st.info(
            "Breakout/Retest يتم حسابه وعرضه حاليًا، "
            "لكنه ليس شرط دخول إلزاميًا في الاستراتيجية الحالية."
        )


# =========================================================
# PAPER TRADING
# =========================================================

st.divider()

st.success(
    "🟢 PAPER TRADING ONLY — لا يتم إرسال أي أمر شراء أو بيع حقيقي."
)

st.caption(
    "آخر تحديث للواجهة: "
    +
    datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )
)

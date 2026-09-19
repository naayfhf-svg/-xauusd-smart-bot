import streamlit as st
import pandas as pd
import numpy as np
import requests
from datetime import datetime

# =========================================================
# PAGE
# =========================================================

st.set_page_config(
    page_title="بوت الذهب XAU/USD",
    page_icon="🟡",
    layout="wide"
)

SYMBOL = "XAU/USD"
API_URL = "https://api.twelvedata.com/time_series"

st.title("🟡 بوت الذهب XAU/USD")
st.caption("Smart Paper Trading — Multi-Timeframe + OOS Comparison")

# =========================================================
# API KEY
# =========================================================

try:
    API_KEY = st.secrets["TWELVE_DATA_API_KEY"]
except Exception:
    API_KEY = ""

if not API_KEY:
    st.error("لم يتم العثور على TWELVE_DATA_API_KEY في Streamlit Secrets.")
    st.stop()


# =========================================================
# DATA
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

    if end_date:
        params["end_date"] = end_date

    try:
        response = requests.get(
            API_URL,
            params=params,
            timeout=30
        )

        data = response.json()

        message = str(data.get("message", ""))

        if (
            data.get("code") == 429
            or "api credits" in message.lower()
        ):
            return pd.DataFrame()

        if (
            data.get("status") != "ok"
            or not data.get("values")
        ):
            return pd.DataFrame()

        df = pd.DataFrame(data["values"])

        required = [
            "datetime",
            "open",
            "high",
            "low",
            "close"
        ]

        if not all(c in df.columns for c in required):
            return pd.DataFrame()

        df["datetime"] = pd.to_datetime(
            df["datetime"],
            errors="coerce"
        )

        for c in required[1:]:
            df[c] = pd.to_numeric(
                df[c],
                errors="coerce"
            )

        df = (
            df
            .dropna(subset=required)
            .drop_duplicates("datetime")
            .sort_values("datetime")
            .reset_index(drop=True)
        )

        return df

    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def get_historical_m5(total_bars=15000):

    chunks = []
    remaining = total_bars
    end_date = None

    while remaining > 0:

        size = min(5000, remaining)

        df = get_candles(
            "5min",
            size,
            end_date
        )

        if df.empty:
            break

        chunks.append(df)

        earliest = df["datetime"].min()

        end_date = (
            earliest - pd.Timedelta(minutes=5)
        ).strftime("%Y-%m-%d %H:%M:%S")

        remaining -= len(df)

        if len(df) < size:
            break

    if not chunks:
        return pd.DataFrame()

    result = (
        pd.concat(chunks, ignore_index=True)
        .drop_duplicates("datetime")
        .sort_values("datetime")
        .tail(total_bars)
        .reset_index(drop=True)
    )

    return result


# =========================================================
# INDICATORS
# =========================================================

def indicators(df):

    df = df.copy()

    close = df["close"]
    high = df["high"]
    low = df["low"]

    # EMA
    df["ema20"] = close.ewm(
        span=20,
        adjust=False
    ).mean()

    df["ema50"] = close.ewm(
        span=50,
        adjust=False
    ).mean()

    df["ema100"] = close.ewm(
        span=100,
        adjust=False
    ).mean()

    # RSI
    delta = close.diff()

    gain = delta.clip(lower=0).ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    loss = (-delta.clip(upper=0)).ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    rs = gain / loss.replace(0, np.nan)

    df["rsi"] = (
        100 -
        (100 / (1 + rs))
    )

    # MACD
    ema12 = close.ewm(
        span=12,
        adjust=False
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False
    ).mean()

    df["macd"] = ema12 - ema26

    df["macd_signal"] = df["macd"].ewm(
        span=9,
        adjust=False
    ).mean()

    df["macd_hist"] = (
        df["macd"] -
        df["macd_signal"]
    )

    # ATR
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

    df["atr"] = true_range.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    # Momentum
    df["momentum"] = close.diff(10)

    # ADX
    up = high.diff()
    down = -low.diff()

    plus_dm = pd.Series(
        np.where(
            (up > down) & (up > 0),
            up,
            0
        ),
        index=df.index
    )

    minus_dm = pd.Series(
        np.where(
            (down > up) & (down > 0),
            down,
            0
        ),
        index=df.index
    )

    atr_safe = df["atr"].replace(
        0,
        np.nan
    )

    df["plus_di"] = (
        100 *
        plus_dm.ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        /
        atr_safe
    )

    df["minus_di"] = (
        100 *
        minus_dm.ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        /
        atr_safe
    )

    di_sum = (
        df["plus_di"] +
        df["minus_di"]
    ).replace(0, np.nan)

    dx = (
        100 *
        (
            df["plus_di"] -
            df["minus_di"]
        ).abs()
        /
        di_sum
    )

    df["adx"] = dx.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    return df


# =========================================================
# RESAMPLE
# =========================================================

def resample_ohlc(df, rule):

    return (
        df
        .set_index("datetime")
        [["open", "high", "low", "close"]]
        .resample(
            rule,
            label="left",
            closed="left"
        )
        .agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last"
        })
        .dropna()
        .reset_index()
    )


# =========================================================
# SCORE
# =========================================================

def score(row, prefix=""):

    def v(name):
        return row.get(
            prefix + name,
            np.nan
        )

    values = [
        v("ema20"),
        v("ema50"),
        v("ema100"),
        v("close"),
        v("rsi"),
        v("macd"),
        v("macd_signal"),
        v("macd_hist"),
        v("momentum"),
        v("adx"),
        v("plus_di"),
        v("minus_di")
    ]

    if any(pd.isna(x) for x in values):
        return 0

    (
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
    ) = values

    s = 0

    if ema20 > ema50 > ema100:
        s += 3

    elif ema20 < ema50 < ema100:
        s -= 3

    s += 1 if close > ema20 else -1

    if 52 <= rsi <= 68:
        s += 2

    elif 32 <= rsi < 48:
        s -= 2

    if macd > macd_signal and macd_hist > 0:
        s += 2

    elif macd < macd_signal and macd_hist < 0:
        s -= 2

    s += 1 if momentum > 0 else -1

    if adx >= 25:

        if plus_di > minus_di:
            s += 2

        elif minus_di > plus_di:
            s -= 2

    return s


def trend(s):

    if s >= 5:
        return "صاعد 📈"

    if s <= -5:
        return "هابط 📉"

    return "محايد ↔️"


# =========================================================
# LIVE DATA
# =========================================================

@st.cache_data(ttl=65, show_spinner=False)
def live_data():

    m5 = get_candles(
        "5min",
        300
    )

    if m5.empty:
        return None

    m15 = resample_ohlc(
        m5,
        "15min"
    )

    h1 = resample_ohlc(
        m5,
        "1h"
    )

    if m15.empty or h1.empty:
        return None

    return (
        indicators(m5),
        indicators(m15),
        indicators(h1)
    )


def breakout_text(m15):

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

    if pd.isna(atr) or atr <= 0:
        return "لا يوجد"

    if (
        current["close"] > resistance
        and
        previous["close"] <= resistance
    ):
        return "كسر مقاومة 📈"

    if (
        current["close"] < support
        and
        previous["close"] >= support
    ):
        return "كسر دعم 📉"

    if (
        abs(current["close"] - resistance)
        <= atr * 0.5
    ):
        return "إعادة اختبار مقاومة"

    if (
        abs(current["close"] - support)
        <= atr * 0.5
    ):
        return "إعادة اختبار دعم"

    return "لا يوجد كسر أو إعادة اختبار واضحة"


def live_analysis():

    data = live_data()

    if data is None:
        return None

    m5, m15, h1 = data

    a5 = m5.iloc[-1]
    a15 = m15.iloc[-1]
    ah1 = h1.iloc[-1]

    s5 = score(a5)
    s15 = score(a15)
    sh1 = score(ah1)

    total = s5 + s15 + sh1

    buy = (
        s5 >= 5
        and s15 >= 5
        and sh1 >= 5
        and a15["adx"] >= 20
        and a15["rsi"] >= 50
        and a15["macd"] > 0
    )

    sell = (
        s5 <= -5
        and s15 <= -5
        and sh1 <= -5
        and a15["adx"] >= 20
        and a15["rsi"] <= 50
        and a15["macd"] < 0
    )

    if buy:
        signal = "BUY"

    elif sell:
        signal = "SELL"

    else:
        signal = "WAIT"

    price = float(a5["close"])
    atr = float(a15["atr"])

    sl = np.nan
    tp1 = np.nan
    tp2 = np.nan

    if signal == "BUY":

        sl = price - 1.5 * atr
        tp1 = price + 1.5 * atr
        tp2 = price + 2.5 * atr

    elif signal == "SELL":

        sl = price + 1.5 * atr
        tp1 = price - 1.5 * atr
        tp2 = price - 2.5 * atr

    return {
        "price": price,
        "signal": signal,
        "confidence": min(
            100,
            round(
                abs(total) / 30 * 100
            )
        ),
        "trend5": trend(s5),
        "trend15": trend(s15),
        "trendh1": trend(sh1),
        "score5": s5,
        "score15": s15,
        "scoreh1": sh1,
        "rsi": float(a15["rsi"]),
        "adx": float(a15["adx"]),
        "macd": float(a15["macd"]),
        "atr": atr,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "support": m15["low"].iloc[-50:].min(),
        "resistance": m15["high"].iloc[-50:].max(),
        "breakout": breakout_text(m15),
        "m15": m15
    }


# =========================================================
# BACKTEST PREPARATION
# =========================================================

def prepare_backtest(raw):

    m5 = indicators(raw.copy())

    m15 = indicators(
        resample_ohlc(
            raw,
            "15min"
        )
    )

    h1 = indicators(
        resample_ohlc(
            raw,
            "1h"
        )
    )

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
        (m15["close"] > m15["prev_resistance"])
        &
        (
            m15["close"].shift(1)
            <=
            m15["prev_resistance"].shift(1)
        )
    )

    m15["breakout_sell"] = (
        (m15["close"] < m15["prev_support"])
        &
        (
            m15["close"].shift(1)
            >=
            m15["prev_support"].shift(1)
        )
    )

    m15["available_at"] = (
        m15["datetime"] +
        pd.Timedelta(minutes=15)
    )

    h1["available_at"] = (
        h1["datetime"] +
        pd.Timedelta(hours=1)
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

    m15a = (
        m15[["available_at"] + m15_cols]
        .rename(
            columns={
                c: "m15_" + c
                for c in m15_cols
            }
        )
        .sort_values("available_at")
    )

    h1a = (
        h1[["available_at"] + h1_cols]
        .rename(
            columns={
                c: "h1_" + c
                for c in h1_cols
            }
        )
        .sort_values("available_at")
    )

    base = m5.copy()

    base["signal_time"] = (
        base["datetime"] +
        pd.Timedelta(minutes=5)
    )

    base = base.sort_values("signal_time")

    merged = pd.merge_asof(
        base,
        m15a,
        left_on="signal_time",
        right_on="available_at",
        direction="backward"
    )

    merged = pd.merge_asof(
        merged.sort_values("signal_time"),
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

    return merged.reset_index(drop=True)


# =========================================================
# BACKTEST SIGNAL
# =========================================================

def backtest_signal(
    row,
    require_breakout_retest=False
):

    s5 = score(row)
    s15 = score(row, "m15_")
    sh1 = score(row, "h1_")

    total = s5 + s15 + sh1

    buy = (
        s5 >= 5
        and s15 >= 5
        and sh1 >= 5
        and row["m15_adx"] >= 20
        and row["m15_rsi"] >= 50
        and row["m15_macd"] > 0
    )

    sell = (
        s5 <= -5
        and s15 <= -5
        and sh1 <= -5
        and row["m15_adx"] >= 20
        and row["m15_rsi"] <= 50
        and row["m15_macd"] < 0
    )

    if require_breakout_retest:

        close = row["m15_close"]
        atr = row["m15_atr"]

        resistance = row["m15_prev_resistance"]
        support = row["m15_prev_support"]

        buy_breakout = bool(
            row["m15_breakout_buy"]
        )

        sell_breakout = bool(
            row["m15_breakout_sell"]
        )

        near_resistance = (
            pd.notna(resistance)
            and
            abs(close - resistance)
            <= atr * 0.5
        )

        near_support = (
            pd.notna(support)
            and
            abs(close - support)
            <= atr * 0.5
        )

        buy = buy and (
            buy_breakout
            or
            near_resistance
        )

        sell = sell and (
            sell_breakout
            or
            near_support
        )

    if buy:
        return "BUY", total

    if sell:
        return "SELL", total

    return "WAIT", total


# =========================================================
# SIMULATION
# =========================================================

def simulate(
    df,
    start,
    end,
    require_breakout_retest=False
):

    trades = []

    i = start

    while i < end - 2:

        row = df.iloc[i]

        signal, total_score = backtest_signal(
            row,
            require_breakout_retest
        )

        if signal == "WAIT":
            i += 1
            continue

        entry_i = i + 1

        if entry_i >= end:
            break

        entry = float(
            df.iloc[entry_i]["open"]
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

        risk = 1.5 * atr

        if signal == "BUY":

            sl = entry - risk
            tp1 = entry + 1.5 * atr
            tp2 = entry + 2.5 * atr

        else:

            sl = entry + risk
            tp1 = entry - 1.5 * atr
            tp2 = entry - 2.5 * atr

        exit_i = None
        exit_price = None
        result = None

        for j in range(entry_i, end):

            high = float(
                df.iloc[j]["high"]
            )

            low = float(
                df.iloc[j]["low"]
            )

            if signal == "BUY":

                hit_sl = low <= sl
                hit_tp = high >= tp2

            else:

                hit_sl = high >= sl
                hit_tp = low <= tp2

            # Conservative assumption:
            # if SL and TP happen on same candle,
            # SL is assumed first.

            if hit_sl:

                exit_i = j
                exit_price = sl
                result = "SL"
                break

            if hit_tp:

                exit_i = j
                exit_price = tp2
                result = "TP2"
                break

        if exit_i is None:

            exit_i = end - 1

            exit_price = float(
                df.iloc[end - 1]["close"]
            )

            result = "END"

        if signal == "BUY":

            r = (
                exit_price - entry
            ) / risk

        else:

            r = (
                entry - exit_price
            ) / risk

        trades.append({
            "direction": signal,
            "signal_time": df.iloc[i]["signal_time"],
            "entry_time": df.iloc[entry_i]["datetime"],
            "exit_time": df.iloc[exit_i]["datetime"],
            "entry": round(entry, 3),
            "SL": round(sl, 3),
            "TP1": round(tp1, 3),
            "TP2": round(tp2, 3),
            "exit": round(exit_price, 3),
            "result": result,
            "R": round(float(r), 4),
            "score": total_score
        })

        i = exit_i + 1

    return pd.DataFrame(trades)


# =========================================================
# METRICS
# =========================================================

def metrics(trades):

    if trades.empty:

        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "total_r": 0.0,
            "average_r": 0.0,
            "max_drawdown": 0.0,
            "buy_trades": 0,
            "sell_trades": 0
        }

    r = trades["R"].astype(float)

    gross_profit = r[r > 0].sum()

    gross_loss = abs(
        r[r < 0].sum()
    )

    equity = r.cumsum()

    drawdown = (
        equity -
        equity.cummax()
    )

    max_drawdown = abs(
        drawdown.min()
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

    return {
        "trades": len(trades),
        "wins": int((r > 0).sum()),
        "losses": int((r < 0).sum()),
        "win_rate": float(
            (r > 0).mean() * 100
        ),
        "profit_factor": float(
            profit_factor
        ),
        "total_r": float(r.sum()),
        "average_r": float(r.mean()),
        "max_drawdown": float(max_drawdown),
        "buy_trades": int(
            (trades["direction"] == "BUY").sum()
        ),
        "sell_trades": int(
            (trades["direction"] == "SELL").sum()
        )
    }


# =========================================================
# STRATEGY TEST
# =========================================================

@st.cache_data(
    ttl=600,
    show_spinner=False
)
def run_strategy(
    total_bars,
    require_breakout_retest=False
):

    raw = get_historical_m5(
        total_bars
    )

    if raw.empty:
        return None

    df = prepare_backtest(raw)

    if len(df) < 300:
        return None

    split = int(
        len(df) * 0.70
    )

    is_trades = simulate(
        df,
        0,
        split,
        require_breakout_retest
    )

    oos_trades = simulate(
        df,
        split,
        len(df),
        require_breakout_retest
    )

    return {
        "raw": raw,
        "df": df,
        "split_time": df.iloc[split]["signal_time"],
        "is_trades": is_trades,
        "oos_trades": oos_trades,
        "is_metrics": metrics(is_trades),
        "oos_metrics": metrics(oos_trades)
    }


@st.cache_data(
    ttl=600,
    show_spinner=False
)
def run_comparison(total_bars):

    current = run_strategy(
        total_bars,
        False
    )

    breakout = run_strategy(
        total_bars,
        True
    )

    if current is None or breakout is None:
        return None

    return {
        "current": current,
        "breakout": breakout
    }


# =========================================================
# LIVE DASHBOARD
# =========================================================

st.divider()

st.subheader("📡 التحليل المباشر")

if st.button(
    "🔄 تحديث التحليل",
    use_container_width=True
):

    get_candles.clear()
    get_historical_m5.clear()
    live_data.clear()
    run_strategy.clear()
    run_comparison.clear()

    st.rerun()


live = live_analysis()

if live is None:

    st.warning(
        "⚠️ تعذر الحصول على بيانات السوق الحالية."
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

    signal_display = {
        "BUY": "🟢 BUY",
        "SELL": "🔴 SELL",
        "WAIT": "🟡 WAIT"
    }

    c3.metric(
        "Signal",
        signal_display[live["signal"]]
    )

    st.markdown("### الاتجاهات")

    a, b, c = st.columns(3)

    a.info(
        f"M5\n\n"
        f"{live['trend5']}\n\n"
        f"Score: {live['score5']}"
    )

    b.info(
        f"M15\n\n"
        f"{live['trend15']}\n\n"
        f"Score: {live['score15']}"
    )

    c.info(
        f"H1\n\n"
        f"{live['trendh1']}\n\n"
        f"Score: {live['scoreh1']}"
    )

    st.markdown("### المستويات")

    a, b, c, d = st.columns(4)

    a.metric(
        "Support M15",
        f"{live['support']:,.2f}"
    )

    b.metric(
        "Resistance M15",
        f"{live['resistance']:,.2f}"
    )

    c.metric(
        "ATR M15",
        f"{live['atr']:,.2f}"
    )

    d.metric(
        "Breakout / Retest",
        live["breakout"]
    )

    if live["signal"] != "WAIT":

        st.markdown("### 🎯 مستويات الصفقة")

        a, b, c = st.columns(3)

        a.metric(
            "SL",
            f"{live['sl']:,.2f}"
        )

        b.metric(
            "TP1",
            f"{live['tp1']:,.2f}"
        )

        c.metric(
            "TP2",
            f"{live['tp2']:,.2f}"
        )

    else:

        st.warning(
            "⛔ لم تتحقق شروط الدخول الكاملة."
        )

    st.markdown("### مؤشرات M15")

    indicator_table = pd.DataFrame({
        "المؤشر": [
            "RSI",
            "ADX",
            "MACD",
            "ATR",
            "Momentum"
        ],
        "القيمة": [
            round(live["rsi"], 3),
            round(live["adx"], 3),
            round(live["macd"], 3),
            round(live["atr"], 3),
            round(
                live["m15"]["momentum"].iloc[-1],
                3
            )
        ]
    })

    st.dataframe(
        indicator_table,
        use_container_width=True,
        hide_index=True
    )


# =========================================================
# OOS COMPARISON
# =========================================================

st.divider()

st.subheader(
    "📊 مقارنة الاستراتيجيتين — OOS"
)

st.write(
    """
A = الاستراتيجية الحالية.

B = نفس الاستراتيجية مع اشتراط Breakout + Retest.

كلاهما يستخدم نفس البيانات ونفس تقسيم
70% In-Sample و30% Out-of-Sample.
"""
)

st.caption(
    "الدخول عند افتتاح شمعة M5 التالية بعد إغلاق شمعة الإشارة. "
    "Confidence ليس احتمال ربح."
)

bars = st.selectbox(
    "حجم البيانات التاريخية",
    [
        5000,
        10000,
        15000,
        30000
    ],
    index=2,
    format_func=lambda x:
        f"{x:,} شمعة M5"
)

if st.button(
    "🚀 تشغيل المقارنة",
    use_container_width=True
):

    with st.spinner(
        "جاري تحميل البيانات وتشغيل النسختين..."
    ):

        result = run_comparison(bars)

    if result is None:

        st.error(
            "لم تتوفر بيانات تاريخية كافية."
        )

    else:

        A = result["current"]
        B = result["breakout"]

        raw = A["raw"]

        st.success(
            "تم تشغيل النسختين على نفس البيانات."
        )

        st.info(
            f"الفترة: "
            f"{raw['datetime'].min()} → "
            f"{raw['datetime'].max()}"
        )

        st.info(
            f"فصل OOS: {A['split_time']}"
        )

        # =================================================
        # NEW MOBILE-FRIENDLY SUMMARY
        # =================================================

        st.markdown(
            "## 🔬 نتائج OOS — بشكل واضح"
        )

        ma = A["oos_metrics"]
        mb = B["oos_metrics"]

        st.markdown("### 🅰️ A — الاستراتيجية الحالية")

        a1, a2, a3 = st.columns(3)

        a1.metric(
            "الصفقات",
            f"{ma['trades']}"
        )

        a2.metric(
            "Win Rate",
            f"{ma['win_rate']:.1f}%"
        )

        a3.metric(
            "Profit Factor",
            (
                f"{ma['profit_factor']:.2f}"
                if np.isfinite(ma["profit_factor"])
                else "∞"
            )
        )

        a4, a5, a6 = st.columns(3)

        a4.metric(
            "Total R",
            f"{ma['total_r']:+.2f}R"
        )

        a5.metric(
            "Average R",
            f"{ma['average_r']:+.3f}R"
        )

        a6.metric(
            "Max Drawdown",
            f"{ma['max_drawdown']:.2f}R"
        )

        st.caption(
            f"BUY: {ma['buy_trades']}   |   "
            f"SELL: {ma['sell_trades']}"
        )

        st.markdown("### 🅱️ B — Breakout + Retest")

        b1, b2, b3 = st.columns(3)

        b1.metric(
            "الصفقات",
            f"{mb['trades']}"
        )

        b2.metric(
            "Win Rate",
            f"{mb['win_rate']:.1f}%"
        )

        b3.metric(
            "Profit Factor",
            (
                f"{mb['profit_factor']:.2f}"
                if np.isfinite(mb["profit_factor"])
                else "∞"
            )
        )

        b4, b5, b6 = st.columns(3)

        b4.metric(
            "Total R",
            f"{mb['total_r']:+.2f}R"
        )

        b5.metric(
            "Average R",
            f"{mb['average_r']:+.3f}R"
        )

        b6.metric(
            "Max Drawdown",
            f"{mb['max_drawdown']:.2f}R"
        )

        st.caption(
            f"BUY: {mb['buy_trades']}   |   "
            f"SELL: {mb['sell_trades']}"
        )

        # =================================================
        # SIDE-BY-SIDE TABLE
        # =================================================

        st.markdown(
            "### 📊 المقارنة الرقمية"
        )

        comparison = pd.DataFrame({
            "المقياس": [
                "عدد الصفقات",
                "Win Rate %",
                "Profit Factor",
                "Total R",
                "Average R",
                "Max Drawdown",
                "BUY",
                "SELL"
            ],

            "A — الحالية": [
                ma["trades"],
                round(ma["win_rate"], 2),
                (
                    round(ma["profit_factor"], 3)
                    if np.isfinite(ma["profit_factor"])
                    else "∞"
                ),
                round(ma["total_r"], 3),
                round(ma["average_r"], 3),
                round(ma["max_drawdown"], 3),
                ma["buy_trades"],
                ma["sell_trades"]
            ],

            "B — Breakout + Retest": [
                mb["trades"],
                round(mb["win_rate"], 2),
                (
                    round(mb["profit_factor"], 3)
                    if np.isfinite(mb["profit_factor"])
                    else "∞"
                ),
                round(mb["total_r"], 3),
                round(mb["average_r"], 3),
                round(mb["max_drawdown"], 3),
                mb["buy_trades"],
                mb["sell_trades"]
            ]
        })

        st.dataframe(
            comparison,
            use_container_width=True,
            hide_index=True
        )

        # =================================================
        # EQUITY CURVE
        # =================================================

        curves = []

        for label, item in [
            ("A — الحالية", A),
            ("B — Breakout + Retest", B)
        ]:

            trades = item["oos_trades"]

            if not trades.empty:

                curve = trades[
                    ["exit_time", "R"]
                ].copy()

                curve["Equity R"] = (
                    curve["R"].cumsum()
                )

                curve = (
                    curve
                    .set_index("exit_time")
                    [["Equity R"]]
                    .rename(
                        columns={
                            "Equity R": label
                        }
                    )
                )

                curves.append(curve)

        if curves:

            st.markdown(
                "### 📈 منحنى OOS"
            )

            st.line_chart(
                pd.concat(
                    curves,
                    axis=1
                ).ffill()
            )

        # =================================================
        # TRADES
        # =================================================

        with st.expander(
            "📋 صفقات OOS — A الحالية"
        ):

            st.dataframe(
                A["oos_trades"],
                use_container_width=True,
                hide_index=True
            )

        with st.expander(
            "📋 صفقات OOS — B Breakout + Retest"
        ):

            st.dataframe(
                B["oos_trades"],
                use_container_width=True,
                hide_index=True
            )

        # =================================================
        # SAMPLE WARNING
        # =================================================

        maximum_oos_trades = max(
            ma["trades"],
            mb["trades"]
        )

        if maximum_oos_trades < 30:

            st.warning(
                "⚠️ عينة OOS أقل من 30 صفقة؛ "
                "صغيرة جدًا."
            )

        elif maximum_oos_trades < 100:

            st.warning(
                "⚠️ عينة OOS بين 30 و99 صفقة؛ "
                "النتيجة مبدئية وتحتاج بيانات أطول."
            )

        else:

            st.success(
                "حجم عينة OOS تجاوز 100 صفقة."
            )

        st.warning(
            "لا يوجد Spread أو Slippage أو Commission "
            "في هذا الاختبار، لذلك لا يمثل صافي الربح الفعلي."
        )

        st.info(
            "تعريف B الحالي محافظ: يعتمد على حدوث كسر "
            "لمستوى M15 السابق أو قرب السعر من المستوى "
            "ضمن 0.5 ATR مع تحقق شروط الاتجاه."
        )

        st.info(
            "لا نستخدم OOS لتحسين المعلمات؛ "
            "المقارنة اختبار فرضية فقط."
        )


# =========================================================
# PAPER TRADING
# =========================================================

st.divider()

st.success(
    "🟢 PAPER TRADING ONLY — "
    "لا يتم إرسال أي أمر شراء أو بيع حقيقي."
)

st.caption(
    "آخر تحديث للواجهة: "
    +
    datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )
)

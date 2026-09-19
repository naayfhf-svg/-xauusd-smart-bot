import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime

# =========================================================
# PAGE
# =========================================================

st.set_page_config(
    page_title="بوت الذهب XAU/USD",
    page_icon="🟡",
    layout="wide"
)

st.title("🟡 بوت الذهب XAU/USD")
st.caption("Smart Paper Trading — Multi-Timeframe Signal Engine")

# =========================================================
# SETTINGS
# =========================================================

SYMBOL = "XAU/USD"
API_KEY = st.secrets.get("TWELVE_DATA_API_KEY", "")

if not API_KEY:
    st.error("❌ TWELVE_DATA_API_KEY غير موجود في Secrets")
    st.stop()


# =========================================================
# DATA
# =========================================================

@st.cache_data(ttl=30)
def get_candles(interval, outputsize=300):

    url = "https://api.twelvedata.com/time_series"

    params = {
        "symbol": SYMBOL,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": API_KEY,
        "format": "JSON"
    }

    response = requests.get(
        url,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if "values" not in data:
        raise ValueError(
            data.get(
                "message",
                "لم يتم استلام بيانات صحيحة"
            )
        )

    df = pd.DataFrame(data["values"])

    required = [
        "datetime",
        "open",
        "high",
        "low",
        "close"
    ]

    for column in required:

        if column not in df.columns:
            raise ValueError(
                f"العمود مفقود: {column}"
            )

    for column in [
        "open",
        "high",
        "low",
        "close"
    ]:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df["datetime"] = pd.to_datetime(
        df["datetime"],
        errors="coerce"
    )

    df = df.dropna(
        subset=[
            "datetime",
            "open",
            "high",
            "low",
            "close"
        ]
    )

    df = df.sort_values(
        "datetime"
    ).reset_index(drop=True)

    if len(df) < 100:
        raise ValueError(
            f"البيانات غير كافية: {len(df)} شمعة"
        )

    return df


# =========================================================
# INDICATORS
# =========================================================

def add_indicators(df):

    df = df.copy()

    close = df["close"]
    high = df["high"]
    low = df["low"]

    # -----------------------------------------------------
    # EMA
    # -----------------------------------------------------

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

    avg_gain = gain.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    rs = (
        avg_gain /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    df["rsi"] = (
        100 -
        (
            100 /
            (1 + rs)
        )
    )

    # -----------------------------------------------------
    # MACD
    # -----------------------------------------------------

    ema12 = close.ewm(
        span=12,
        adjust=False
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False
    ).mean()

    df["macd"] = (
        ema12 -
        ema26
    )

    df["macd_signal"] = df[
        "macd"
    ].ewm(
        span=9,
        adjust=False
    ).mean()

    df["macd_hist"] = (
        df["macd"] -
        df["macd_signal"]
    )

    # -----------------------------------------------------
    # ATR
    # -----------------------------------------------------

    previous_close = close.shift(1)

    tr1 = high - low

    tr2 = (
        high -
        previous_close
    ).abs()

    tr3 = (
        low -
        previous_close
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
        span=14,
        adjust=False
    ).mean()

    # -----------------------------------------------------
    # MOMENTUM
    # -----------------------------------------------------

    df["momentum"] = (
        close -
        close.shift(10)
    )

    # -----------------------------------------------------
    # ADX
    # -----------------------------------------------------

    up_move = high.diff()

    down_move = -low.diff()

    plus_dm = up_move.where(
        (up_move > down_move) &
        (up_move > 0),
        0.0
    )

    minus_dm = down_move.where(
        (down_move > up_move) &
        (down_move > 0),
        0.0
    )

    atr_adx = true_range.ewm(
        span=14,
        adjust=False
    ).mean()

    plus_di = (
        100 *
        plus_dm.ewm(
            span=14,
            adjust=False
        ).mean() /
        atr_adx.replace(
            0,
            np.nan
        )
    )

    minus_di = (
        100 *
        minus_dm.ewm(
            span=14,
            adjust=False
        ).mean() /
        atr_adx.replace(
            0,
            np.nan
        )
    )

    di_sum = (
        plus_di +
        minus_di
    ).replace(
        0,
        np.nan
    )

    dx = (
        100 *
        (
            plus_di -
            minus_di
        ).abs() /
        di_sum
    )

    df["adx"] = dx.ewm(
        span=14,
        adjust=False
    ).mean()

    df["plus_di"] = plus_di
    df["minus_di"] = minus_di

    # -----------------------------------------------------
    # VOLATILITY REGIME
    # -----------------------------------------------------

    df["atr_percent"] = (
        df["atr"] /
        close *
        100
    )

    return df


# =========================================================
# SUPPORT / RESISTANCE
# =========================================================

def get_levels(df, lookback=50):

    recent = df.tail(
        lookback
    )

    support = float(
        recent["low"].min()
    )

    resistance = float(
        recent["high"].max()
    )

    return support, resistance


# =========================================================
# TIMEFRAME ANALYSIS
# =========================================================

def analyze(df):

    last = df.iloc[-1]

    price = float(
        last["close"]
    )

    score = 0
    reasons = []

    # -----------------------------------------------------
    # EMA STRUCTURE
    # -----------------------------------------------------

    if (
        last["ema20"] >
        last["ema50"] >
        last["ema100"]
    ):

        score += 3

        reasons.append(
            "ترتيب EMA صاعد"
        )

    elif (
        last["ema20"] <
        last["ema50"] <
        last["ema100"]
    ):

        score -= 3

        reasons.append(
            "ترتيب EMA هابط"
        )

    else:

        reasons.append(
            "ترتيب EMA غير مكتمل"
        )

    # -----------------------------------------------------
    # PRICE vs EMA20
    # -----------------------------------------------------

    if price > last["ema20"]:

        score += 1

        reasons.append(
            "السعر فوق EMA20"
        )

    elif price < last["ema20"]:

        score -= 1

        reasons.append(
            "السعر تحت EMA20"
        )

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------

    rsi = float(
        last["rsi"]
    )

    if 52 <= rsi <= 68:

        score += 2

        reasons.append(
            "RSI يدعم الشراء"
        )

    elif 32 <= rsi < 48:

        score -= 2

        reasons.append(
            "RSI يدعم البيع"
        )

    elif rsi > 70:

        reasons.append(
            "RSI مرتفع — خطر مطاردة السعر"
        )

    elif rsi < 30:

        reasons.append(
            "RSI منخفض — احتمال تشبع بيعي"
        )

    # -----------------------------------------------------
    # MACD
    # -----------------------------------------------------

    if (
        last["macd"] >
        last["macd_signal"] and
        last["macd_hist"] > 0
    ):

        score += 2

        reasons.append(
            "MACD صاعد"
        )

    elif (
        last["macd"] <
        last["macd_signal"] and
        last["macd_hist"] < 0
    ):

        score -= 2

        reasons.append(
            "MACD هابط"
        )

    # -----------------------------------------------------
    # MOMENTUM
    # -----------------------------------------------------

    if last["momentum"] > 0:

        score += 1

        reasons.append(
            "Momentum إيجابي"
        )

    elif last["momentum"] < 0:

        score -= 1

        reasons.append(
            "Momentum سلبي"
        )

    # -----------------------------------------------------
    # ADX
    # -----------------------------------------------------

    adx = float(
        last["adx"]
    )

    if adx >= 25:

        if (
            last["plus_di"] >
            last["minus_di"]
        ):

            score += 2

            reasons.append(
                "ADX +DI يدعم الصعود"
            )

        elif (
            last["minus_di"] >
            last["plus_di"]
        ):

            score -= 2

            reasons.append(
                "ADX -DI يدعم الهبوط"
            )

    else:

        reasons.append(
            "ADX أقل من 25 — الاتجاه غير قوي"
        )

    # -----------------------------------------------------
    # TREND
    # -----------------------------------------------------

    if score >= 5:

        trend = "صاعد 📈"

    elif score <= -5:

        trend = "هابط 📉"

    else:

        trend = "محايد ↔️"

    return {
        "price": price,
        "score": score,
        "trend": trend,
        "rsi": rsi,
        "adx": adx,
        "atr": float(last["atr"]),
        "reasons": reasons
    }


# =========================================================
# BREAKOUT / RETEST
# =========================================================

def breakout_retest(df):

    if len(df) < 30:
        return "NONE", "بيانات غير كافية"

    recent = df.iloc[-21:-1]

    last = df.iloc[-1]
    previous = df.iloc[-2]

    resistance = float(
        recent["high"].max()
    )

    support = float(
        recent["low"].min()
    )

    price = float(
        last["close"]
    )

    atr = float(
        last["atr"]
    )

    # -----------------------------------------------------
    # Bullish breakout
    # -----------------------------------------------------

    bullish_breakout = (
        previous["close"] <= resistance
        and
        price > resistance
    )

    if bullish_breakout:

        return (
            "BUY_BREAKOUT",
            "اختراق مقاومة"
        )

    # -----------------------------------------------------
    # Bearish breakout
    # -----------------------------------------------------

    bearish_breakout = (
        previous["close"] >= support
        and
        price < support
    )

    if bearish_breakout:

        return (
            "SELL_BREAKOUT",
            "كسر دعم"
        )

    # -----------------------------------------------------
    # Retest bullish
    # -----------------------------------------------------

    bullish_retest = (
        price > resistance
        and
        abs(
            price - resistance
        ) <= atr * 0.5
    )

    if bullish_retest:

        return (
            "BUY_RETEST",
            "إعادة اختبار مقاومة مخترقة"
        )

    # -----------------------------------------------------
    # Retest bearish
    # -----------------------------------------------------

    bearish_retest = (
        price < support
        and
        abs(
            price - support
        ) <= atr * 0.5
    )

    if bearish_retest:

        return (
            "SELL_RETEST",
            "إعادة اختبار دعم مكسور"
        )

    return (
        "NONE",
        "لا يوجد كسر أو إعادة اختبار واضحة"
    )


# =========================================================
# MAIN SIGNAL ENGINE
# =========================================================

def signal_engine():

    m5 = add_indicators(
        get_candles("5min")
    )

    m15 = add_indicators(
        get_candles("15min")
    )

    h1 = add_indicators(
        get_candles("1h")
    )

    a5 = analyze(m5)
    a15 = analyze(m15)
    ah1 = analyze(h1)

    # -----------------------------------------------------
    # Levels
    # -----------------------------------------------------

    support, resistance = get_levels(
        m15
    )

    # -----------------------------------------------------
    # Breakout
    # -----------------------------------------------------

    breakout_type, breakout_reason = (
        breakout_retest(m15)
    )

    # -----------------------------------------------------
    # Trend agreement
    # -----------------------------------------------------

    bullish_alignment = (
        a5["trend"] == "صاعد 📈"
        and
        a15["trend"] == "صاعد 📈"
        and
        ah1["trend"] == "صاعد 📈"
    )

    bearish_alignment = (
        a5["trend"] == "هابط 📉"
        and
        a15["trend"] == "هابط 📉"
        and
        ah1["trend"] == "هابط 📉"
    )

    # -----------------------------------------------------
    # Combined score
    # -----------------------------------------------------

    total_score = (
        a5["score"] +
        a15["score"] +
        ah1["score"]
    )

    # -----------------------------------------------------
    # Signal
    # -----------------------------------------------------

    signal = "WAIT"

    decision_reason = []

    # Strong BUY
    if bullish_alignment:

        if a15["adx"] >= 20:

            if (
                a15["rsi"] >= 50
                and
                a15["macd"] >
                0
            ):

                signal = "BUY"

                decision_reason.append(
                    "M5/M15/H1 متوافقة صعوداً"
                )

                decision_reason.append(
                    "ADX يدعم وجود اتجاه"
                )

                decision_reason.append(
                    "RSI وMACD يدعمان الحركة"
                )

    # Strong SELL
    if bearish_alignment:

        if a15["adx"] >= 20:

            if (
                a15["rsi"] <= 50
                and
                a15["macd"] <
                0
            ):

                signal = "SELL"

                decision_reason.append(
                    "M5/M15/H1 متوافقة هبوطاً"
                )

                decision_reason.append(
                    "ADX يدعم وجود اتجاه"
                )

                decision_reason.append(
                    "RSI وMACD يدعمان الحركة"
                )

    # -----------------------------------------------------
    # Breakout confirmation
    # -----------------------------------------------------

    if signal == "BUY":

        if breakout_type in [
            "BUY_BREAKOUT",
            "BUY_RETEST"
        ]:

            decision_reason.append(
                breakout_reason
            )

    elif signal == "SELL":

        if breakout_type in [
            "SELL_BREAKOUT",
            "SELL_RETEST"
        ]:

            decision_reason.append(
                breakout_reason
            )

    # -----------------------------------------------------
    # Confidence / agreement
    # -----------------------------------------------------

    max_possible = 30

    confidence = int(
        min(
            100,
            max(
                0,
                abs(total_score) /
                max_possible *
                100
            )
        )
    )

    # -----------------------------------------------------
    # Entry
    # -----------------------------------------------------

    entry = float(
        a5["price"]
    )

    atr = float(
        a15["atr"]
    )

    # -----------------------------------------------------
    # SL / TP
    # -----------------------------------------------------

    if signal == "BUY":

        stop_loss = entry - (
            atr * 1.5
        )

        tp1 = entry + (
            atr * 1.5
        )

        tp2 = entry + (
            atr * 2.5
        )

    elif signal == "SELL":

        stop_loss = entry + (
            atr * 1.5
        )

        tp1 = entry - (
            atr * 1.5
        )

        tp2 = entry - (
            atr * 2.5
        )

    else:

        stop_loss = None
        tp1 = None
        tp2 = None

    return {
        "m5": a5,
        "m15": a15,
        "h1": ah1,
        "signal": signal,
        "confidence": confidence,
        "total_score": total_score,
        "entry": entry,
        "stop_loss": stop_loss,
        "tp1": tp1,
        "tp2": tp2,
        "support": support,
        "resistance": resistance,
        "breakout_type": breakout_type,
        "breakout_reason": breakout_reason,
        "decision_reason": decision_reason
    }


# =========================================================
# UI
# =========================================================

if st.button(
    "🔍 تحليل XAU/USD",
    use_container_width=True
):

    with st.spinner(
        "جاري تحليل M5 / M15 / H1..."
    ):

        try:

            result = signal_engine()

            # -------------------------------------------------
            # TOP CARDS
            # -------------------------------------------------

            c1, c2, c3, c4 = st.columns(4)

            with c1:

                st.metric(
                    "سعر XAU/USD",
                    f"${result['entry']:,.2f}"
                )

            with c2:

                trends = [
                    result["m5"]["trend"],
                    result["m15"]["trend"],
                    result["h1"]["trend"]
                ]

                if trends.count(
                    "صاعد 📈"
                ) >= 2:

                    overall = "صاعد 📈"

                elif trends.count(
                    "هابط 📉"
                ) >= 2:

                    overall = "هابط 📉"

                else:

                    overall = "محايد ↔️"

                st.metric(
                    "الاتجاه العام",
                    overall
                )

            with c3:

                st.metric(
                    "قوة التوافق",
                    f"{result['confidence']}%"
                )

            with c4:

                if result["signal"] == "BUY":

                    signal_display = "🟢 BUY"

                elif result["signal"] == "SELL":

                    signal_display = "🔴 SELL"

                else:

                    signal_display = "🟡 WAIT"

                st.metric(
                    "الإشارة",
                    signal_display
                )

            # -------------------------------------------------
            # SIGNAL
            # -------------------------------------------------

            st.divider()

            if result["signal"] == "BUY":

                st.success(
                    "🟢 BUY — شروط الاتجاه والدخول متوافقة."
                )

            elif result["signal"] == "SELL":

                st.error(
                    "🔴 SELL — شروط الاتجاه والدخول متوافقة."
                )

            else:

                st.warning(
                    "🟡 WAIT — لا يوجد توافق كافٍ لفتح صفقة Paper Trading."
                )

            # -------------------------------------------------
            # TRADE LEVELS
            # -------------------------------------------------

            if result["signal"] != "WAIT":

                st.subheader(
                    "🎯 مستويات الصفقة"
                )

                c1, c2, c3, c4 = st.columns(4)

                with c1:
                    st.metric(
                        "Entry",
                        f"{result['entry']:,.2f}"
                    )

                with c2:
                    st.metric(
                        "Stop Loss",
                        f"{result['stop_loss']:,.2f}"
                    )

                with c3:
                    st.metric(
                        "TP1",
                        f"{result['tp1']:,.2f}"
                    )

                with c4:
                    st.metric(
                        "TP2",
                        f"{result['tp2']:,.2f}"
                    )

            # -------------------------------------------------
            # SUPPORT / RESISTANCE
            # -------------------------------------------------

            st.divider()

            c1, c2 = st.columns(2)

            with c1:

                st.metric(
                    "الدعم M15",
                    f"{result['support']:,.2f}"
                )

            with c2:

                st.metric(
                    "المقاومة M15",
                    f"{result['resistance']:,.2f}"
                )

            # -------------------------------------------------
            # BREAKOUT
            # -------------------------------------------------

            st.subheader(
                "📐 Breakout / Retest"
            )

            st.write(
                result["breakout_reason"]
            )

            # -------------------------------------------------
            # TIMEFRAMES
            # -------------------------------------------------

            st.divider()

            st.subheader(
                "📊 توافق الأطر الزمنية"
            )

            table = pd.DataFrame([
                {
                    "الإطار": "M5",
                    "الاتجاه": result["m5"]["trend"],
                    "Score": result["m5"]["score"],
                    "RSI": round(
                        result["m5"]["rsi"],
                        2
                    ),
                    "ADX": round(
                        result["m5"]["adx"],
                        2
                    )
                },
                {
                    "الإطار": "M15",
                    "الاتجاه": result["m15"]["trend"],
                    "Score": result["m15"]["score"],
                    "RSI": round(
                        result["m15"]["rsi"],
                        2
                    ),
                    "ADX": round(
                        result["m15"]["adx"],
                        2
                    )
                },
                {
                    "الإطار": "H1",
                    "الاتجاه": result["h1"]["trend"],
                    "Score": result["h1"]["score"],
                    "RSI": round(
                        result["h1"]["rsi"],
                        2
                    ),
                    "ADX": round(
                        result["h1"]["adx"],
                        2
                    )
                }
            ])

            st.dataframe(
                table,
                use_container_width=True,
                hide_index=True
            )

            # -------------------------------------------------
            # DECISION LOG
            # -------------------------------------------------

            st.divider()

            st.subheader(
                "🧠 منطق القرار"
            )

            if result["decision_reason"]:

                for reason in result[
                    "decision_reason"
                ]:

                    st.write(
                        f"✅ {reason}"
                    )

            else:

                st.write(
                    "⛔ لم تتحقق شروط الدخول."
                )

            # -------------------------------------------------
            # INDICATOR DETAILS
            # -------------------------------------------------

            st.divider()

            st.subheader(
                "🔬 تفاصيل المؤشرات"
            )

            for name, data in [
                ("M5", result["m5"]),
                ("M15", result["m15"]),
                ("H1", result["h1"])
            ]:

                with st.expander(
                    f"{name} — {data['trend']}"
                ):

                    st.write(
                        f"**Score:** {data['score']}"
                    )

                    st.write(
                        f"**RSI:** {data['rsi']:.2f}"
                    )

                    st.write(
                        f"**ADX:** {data['adx']:.2f}"
                    )

                    st.write(
                        f"**ATR:** {data['atr']:.2f}"
                    )

                    for reason in data[
                        "reasons"
                    ]:

                        st.write(
                            f"• {reason}"
                        )

            # -------------------------------------------------
            # PAPER TRADING
            # -------------------------------------------------

            st.divider()

            st.subheader(
                "🧪 Paper Trading"
            )

            st.info(
                "النظام تجريبي فقط. "
                "لا يتم إرسال أي أوامر حقيقية."
            )

            st.caption(
                "آخر تحليل: "
                + datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )

        except Exception as e:

            st.error(
                "❌ حدث خطأ أثناء التحليل"
            )

            st.code(
                str(e)
            )

else:

    st.info(
        "اضغط «🔍 تحليل XAU/USD» لبدء التحليل."
    )

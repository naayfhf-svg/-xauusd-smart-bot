import streamlit as st
import requests
import pandas as pd

st.set_page_config(
    page_title="بوت الذهب XAU/USD",
    page_icon="🥇",
    layout="wide"
)

st.title("🥇 بوت الذهب XAU/USD")
st.caption("Smart Paper Trading • Multi-Timeframe Confluence Engine")

API_KEY = st.secrets.get("TWELVE_DATA_API_KEY", "")

# =========================
# DATA
# =========================

def get_candles(interval, outputsize=200):

    response = requests.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol": "XAU/USD",
            "interval": interval,
            "outputsize": outputsize,
            "apikey": API_KEY,
            "format": "JSON",
            "order": "ASC"
        },
        timeout=15
    )

    data = response.json()

    if "values" not in data:
        raise RuntimeError(str(data))

    df = pd.DataFrame(data["values"])

    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    return (
        df
        .dropna()
        .sort_values("datetime")
        .reset_index(drop=True)
    )


# =========================
# INDICATORS
# =========================

def calculate_indicators(df):

    df = df.copy()

    # EMA
    df["ema20"] = (
        df["close"]
        .ewm(span=20, adjust=False)
        .mean()
    )

    df["ema50"] = (
        df["close"]
        .ewm(span=50, adjust=False)
        .mean()
    )

    # RSI
    delta = df["close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        pd.NA
    )

    df["rsi"] = (
        100 - (100 / (1 + rs))
    ).fillna(50)

    # MACD
    ema12 = (
        df["close"]
        .ewm(span=12, adjust=False)
        .mean()
    )

    ema26 = (
        df["close"]
        .ewm(span=26, adjust=False)
        .mean()
    )

    df["macd"] = ema12 - ema26

    df["macd_signal"] = (
        df["macd"]
        .ewm(span=9, adjust=False)
        .mean()
    )

    # ATR
    previous_close = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (
                df["high"]
                - previous_close
            ).abs(),
            (
                df["low"]
                - previous_close
            ).abs()
        ],
        axis=1
    ).max(axis=1)

    df["atr"] = (
        tr
        .ewm(alpha=1 / 14, adjust=False)
        .mean()
    )

    # =========================
    # ADX
    # =========================

    high_diff = df["high"].diff()
    low_diff = -df["low"].diff()

    plus_dm = high_diff.where(
        (high_diff > low_diff)
        & (high_diff > 0),
        0
    )

    minus_dm = low_diff.where(
        (low_diff > high_diff)
        & (low_diff > 0),
        0
    )

    atr14 = (
        tr
        .ewm(alpha=1 / 14, adjust=False)
        .mean()
    )

    plus_di = (
        100
        * plus_dm.ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        / atr14
    )

    minus_di = (
        100
        * minus_dm.ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        / atr14
    )

    dx = (
        100
        * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(
            0,
            pd.NA
        )
    )

    df["adx"] = (
        dx
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
        .fillna(0)
    )

    df["plus_di"] = plus_di
    df["minus_di"] = minus_di

    # Momentum
    df["momentum"] = (
        df["close"]
        .diff(5)
    )

    return df


# =========================
# ANALYSIS
# =========================

def analyze_timeframe(df):

    df = calculate_indicators(df)

    x = df.iloc[-1]

    score = 0
    reasons = []

    # Trend
    if x["ema20"] > x["ema50"]:

        score += 2
        reasons.append(
            "EMA صاعد"
        )

    else:

        score -= 2
        reasons.append(
            "EMA هابط"
        )

    # RSI
    if 55 <= x["rsi"] <= 70:

        score += 1
        reasons.append(
            "RSI يدعم الصعود"
        )

    elif 30 <= x["rsi"] <= 45:

        score -= 1
        reasons.append(
            "RSI يدعم الهبوط"
        )

    # MACD
    if x["macd"] > x["macd_signal"]:

        score += 1
        reasons.append(
            "MACD صاعد"
        )

    else:

        score -= 1
        reasons.append(
            "MACD هابط"
        )

    # ADX
    if x["adx"] >= 25:

        if x["plus_di"] > x["minus_di"]:

            score += 2
            reasons.append(
                "ADX + اتجاه قوي"
            )

        else:

            score -= 2
            reasons.append(
                "ADX - اتجاه قوي"
            )

    else:

        reasons.append(
            "ADX ضعيف / سوق متذبذب"
        )

    # Momentum
    if x["momentum"] > 0:

        score += 1
        reasons.append(
            "Momentum موجب"
        )

    else:

        score -= 1
        reasons.append(
            "Momentum سالب"
        )

    return df, score, reasons


# =========================
# SUPPORT / RESISTANCE
# =========================

def support_resistance(df):

    recent = df.tail(30)

    support = float(
        recent["low"].min()
    )

    resistance = float(
        recent["high"].max()
    )

    return support, resistance


# =========================
# MAIN
# =========================

if not API_KEY:

    st.error(
        "مفتاح Twelve Data غير موجود."
    )

    st.stop()


st.metric(
    "حالة البوت",
    "Paper Trading"
)

st.metric(
    "الرصيد التجريبي",
    "$10,000"
)


if st.button(
    "🔄 تحليل الذهب الآن",
    type="primary"
):

    try:

        frames = {}
        scores = {}
        reasons_all = {}

        for label, interval in [
            ("M5", "5min"),
            ("M15", "15min"),
            ("H1", "1h")
        ]:

            raw = get_candles(
                interval
            )

            frames[label], scores[label], reasons_all[label] = (
                analyze_timeframe(raw)
            )


        # =========================
        # PRICE
        # =========================

        latest = frames["M5"].iloc[-1]

        price = float(
            latest["close"]
        )

        atr = float(
            latest["atr"]
        )


        # =========================
        # SUPPORT / RESISTANCE
        # =========================

        support, resistance = (
            support_resistance(
                frames["M15"]
            )
        )


        # =========================
        # MULTI-TIMEFRAME
        # =========================

        total_score = sum(
            scores.values()
        )

        bullish_frames = sum(
            1
            for s in scores.values()
            if s > 0
        )

        bearish_frames = sum(
            1
            for s in scores.values()
            if s < 0
        )


        # =========================
        # TREND
        # =========================

        if bullish_frames >= 2:

            trend = "صاعد 📈"

        elif bearish_frames >= 2:

            trend = "هابط 📉"

        else:

            trend = "مختلط ↔️"


        # =========================
        # SIGNAL ENGINE
        # =========================

        signal = "WAIT"

        if (
            total_score >= 8
            and bullish_frames >= 2
            and latest["adx"] >= 20
            and price > support
        ):

            signal = "BUY"


        elif (
            total_score <= -8
            and bearish_frames >= 2
            and latest["adx"] >= 20
            and price < resistance
        ):

            signal = "SELL"


        # =========================
        # STRENGTH
        # =========================

        strength = min(
            100,
            int(
                50
                + abs(total_score) * 5
            )
        )


        # =========================
        # TRADE LEVELS
        # =========================

        entry = price

        stop_loss = None
        target1 = None
        target2 = None


        if signal == "BUY":

            stop_loss = (
                entry
                - 1.5 * atr
            )

            target1 = (
                entry
                + 1.5 * atr
            )

            target2 = (
                entry
                + 2.5 * atr
            )


        elif signal == "SELL":

            stop_loss = (
                entry
                + 1.5 * atr
            )

            target1 = (
                entry
                - 1.5 * atr
            )

            target2 = (
                entry
                - 2.5 * atr
            )


        # =========================
        # DASHBOARD
        # =========================

        st.divider()

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "XAU/USD",
            f"${price:,.2f}"
        )

        c2.metric(
            "الاتجاه",
            trend
        )

        c3.metric(
            "قوة التوافق",
            f"{strength}%"
        )

        c4.metric(
            "ADX",
            f"{latest['adx']:.1f}"
        )


        if signal == "BUY":

            st.success(
                "🟢 BUY — إشارة شراء"
            )

        elif signal == "SELL":

            st.error(
                "🔴 SELL — إشارة بيع"
            )

        else:

            st.warning(
                "🟡 WAIT — لا يوجد توافق كافٍ للدخول"
            )


        # =========================
        # TRADE PLAN
        # =========================

        st.subheader(
            "🎯 خطة الصفقة"
        )

        p1, p2, p3, p4 = st.columns(4)

        p1.metric(
            "الدخول",
            f"${entry:,.2f}"
        )

        p2.metric(
            "وقف الخسارة",
            f"${stop_loss:,.2f}"
            if stop_loss
            else "—"
        )

        p3.metric(
            "الهدف 1",
            f"${target1:,.2f}"
            if target1
            else "—"
        )

        p4.metric(
            "الهدف 2",
            f"${target2:,.2f}"
            if target2
            else "—"
        )


        # =========================
        # MARKET STRUCTURE
        # =========================

        st.subheader(
            "📐 هيكل السوق"
        )

        m1, m2, m3 = st.columns(3)

        m1.metric(
            "الدعم",
            f"${support:,.2f}"
        )

        m2.metric(
            "المقاومة",
            f"${resistance:,.2f}"
        )

        m3.metric(
            "ATR",
            f"{atr:.2f}"
        )


        # =========================
        # TIMEFRAMES
        # =========================

        st.subheader(
            "📊 التحليل متعدد الأطر"
        )

        rows = []

        for tf in [
            "M5",
            "M15",
            "H1"
        ]:

            x = frames[tf].iloc[-1]

            rows.append({

                "الإطار": tf,

                "Score":
                    scores[tf],

                "RSI":
                    round(
                        float(x["rsi"]),
                        1
                    ),

                "ADX":
                    round(
                        float(x["adx"]),
                        1
                    ),

                "MACD":
                    "صاعد"
                    if x["macd"]
                    > x["macd_signal"]
                    else "هابط",

                "EMA":
                    "صاعد"
                    if x["ema20"]
                    > x["ema50"]
                    else "هابط",

                "Momentum":
                    "موجب"
                    if x["momentum"] > 0
                    else "سالب"
            })


        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True
        )


        # =========================
        # REASONS
        # =========================

        st.subheader(
            "🧠 أسباب التحليل"
        )

        for tf in [
            "M5",
            "M15",
            "H1"
        ]:

            st.write(
                f"**{tf}:** "
                + " • ".join(
                    reasons_all[tf]
                )
            )


        st.divider()

        st.info(
            "Paper Trading فقط. "
            "المحرك لا يضمن الربح، وقوة التوافق ليست "
            "احتمالًا للربح. سيتم استخدام النتائج "
            "لاحقًا في الاختبار الخلفي وقياس الأداء."
        )


    except Exception as e:

        st.error(
            "حدث خطأ أثناء التحليل: "
            + str(e)
        )


else:

    st.info(
        "اضغط «🔄 تحليل الذهب الآن» لبدء التحليل."
    )

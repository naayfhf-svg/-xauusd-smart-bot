import streamlit as st
import requests
import pandas as pd

# ==========================================
# إعداد الصفحة
# ==========================================

st.set_page_config(
    page_title="بوت الذهب XAU/USD",
    page_icon="🥇",
    layout="wide"
)

st.title("🥇 بوت الذهب XAU/USD")
st.caption(
    "Smart Paper Trading • Multi-Timeframe Confluence Engine"
)

API_KEY = st.secrets.get(
    "TWELVE_DATA_API_KEY",
    ""
)


# ==========================================
# جلب البيانات
# ==========================================

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

        message = data.get(
            "message",
            "تعذر الحصول على بيانات الذهب"
        )

        raise RuntimeError(message)

    df = pd.DataFrame(
        data["values"]
    )

    # تحويل البيانات الرقمية بشكل صريح
    numeric_columns = [
        "open",
        "high",
        "low",
        "close"
    ]

    for column in numeric_columns:

        if column not in df.columns:
            raise RuntimeError(
                f"العمود {column} غير موجود في البيانات"
            )

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df = df.dropna(
        subset=numeric_columns
    )

    df = df.sort_values(
        "datetime"
    )

    df = df.reset_index(
        drop=True
    )

    if len(df) < 60:

        raise RuntimeError(
            "عدد بيانات الشموع غير كافٍ للتحليل"
        )

    return df


# ==========================================
# المؤشرات
# ==========================================

def calculate_indicators(df):

    df = df.copy()

    # التأكد من النوع الرقمي
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

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close"
        ]
    )

    # --------------------------------------
    # EMA
    # --------------------------------------

    df["ema20"] = (
        df["close"]
        .ewm(
            span=20,
            adjust=False
        )
        .mean()
    )

    df["ema50"] = (
        df["close"]
        .ewm(
            span=50,
            adjust=False
        )
        .mean()
    )

    # --------------------------------------
    # RSI
    # --------------------------------------

    delta = df["close"].diff()

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
        / avg_loss.replace(
            0,
            pd.NA
        )
    )

    df["rsi"] = (
        100
        - (
            100
            / (1 + rs)
        )
    )

    df["rsi"] = pd.to_numeric(
        df["rsi"],
        errors="coerce"
    )

    df["rsi"] = (
        df["rsi"]
        .fillna(50)
    )

    # --------------------------------------
    # MACD
    # --------------------------------------

    ema12 = (
        df["close"]
        .ewm(
            span=12,
            adjust=False
        )
        .mean()
    )

    ema26 = (
        df["close"]
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

    # --------------------------------------
    # ATR
    # --------------------------------------

    previous_close = (
        df["close"]
        .shift(1)
    )

    tr1 = (
        df["high"]
        - df["low"]
    )

    tr2 = (
        df["high"]
        - previous_close
    ).abs()

    tr3 = (
        df["low"]
        - previous_close
    ).abs()

    true_range = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(
        axis=1
    )

    df["atr"] = (
        true_range
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
    )

    # --------------------------------------
    # ADX
    # --------------------------------------

    high_diff = (
        df["high"]
        .diff()
    )

    low_diff = (
        -df["low"]
        .diff()
    )

    plus_dm = high_diff.where(
        (
            (high_diff > low_diff)
            & (high_diff > 0)
        ),
        0
    )

    minus_dm = low_diff.where(
        (
            (low_diff > high_diff)
            & (low_diff > 0)
        ),
        0
    )

    atr14 = (
        true_range
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
    )

    plus_di = (
        100
        * plus_dm
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
        / atr14.replace(
            0,
            pd.NA
        )
    )

    minus_di = (
        100
        * minus_dm
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
        / atr14.replace(
            0,
            pd.NA
        )
    )

    denominator = (
        plus_di
        + minus_di
    ).replace(
        0,
        pd.NA
    )

    dx = (
        100
        * (
            plus_di
            - minus_di
        ).abs()
        / denominator
    )

    df["adx"] = (
        dx
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
    )

    df["plus_di"] = plus_di
    df["minus_di"] = minus_di

    # --------------------------------------
    # Momentum
    # --------------------------------------

    df["momentum"] = (
        df["close"]
        .diff(5)
    )

    # تحويل نهائي
    indicator_columns = [
        "ema20",
        "ema50",
        "rsi",
        "macd",
        "macd_signal",
        "atr",
        "adx",
        "plus_di",
        "minus_di",
        "momentum"
    ]

    for column in indicator_columns:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    return df


# ==========================================
# تحليل الإطار الزمني
# ==========================================

def analyze_timeframe(df):

    df = calculate_indicators(
        df
    )

    x = df.iloc[-1]

    score = 0

    reasons = []

    # --------------------------------------
    # EMA
    # --------------------------------------

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

    # --------------------------------------
    # RSI
    # --------------------------------------

    if (
        x["rsi"] >= 55
        and x["rsi"] <= 70
    ):

        score += 1

        reasons.append(
            "RSI يدعم الصعود"
        )

    elif (
        x["rsi"] >= 30
        and x["rsi"] <= 45
    ):

        score -= 1

        reasons.append(
            "RSI يدعم الهبوط"
        )

    else:

        reasons.append(
            "RSI محايد"
        )

    # --------------------------------------
    # MACD
    # --------------------------------------

    if (
        x["macd"]
        > x["macd_signal"]
    ):

        score += 1

        reasons.append(
            "MACD صاعد"
        )

    else:

        score -= 1

        reasons.append(
            "MACD هابط"
        )

    # --------------------------------------
    # ADX
    # --------------------------------------

    if x["adx"] >= 25:

        if (
            x["plus_di"]
            > x["minus_di"]
        ):

            score += 2

            reasons.append(
                "ADX يدعم الاتجاه الصاعد"
            )

        else:

            score -= 2

            reasons.append(
                "ADX يدعم الاتجاه الهابط"
            )

    else:

        reasons.append(
            "ADX ضعيف"
        )

    # --------------------------------------
    # Momentum
    # --------------------------------------

    if x["momentum"] > 0:

        score += 1

        reasons.append(
            "Momentum موجب"
        )

    elif x["momentum"] < 0:

        score -= 1

        reasons.append(
            "Momentum سالب"
        )

    else:

        reasons.append(
            "Momentum محايد"
        )

    return (
        df,
        score,
        reasons
    )


# ==========================================
# الدعم والمقاومة
# ==========================================

def support_resistance(df):

    recent = df.tail(
        30
    ).copy()

    recent["low"] = pd.to_numeric(
        recent["low"],
        errors="coerce"
    )

    recent["high"] = pd.to_numeric(
        recent["high"],
        errors="coerce"
    )

    lows = (
        recent["low"]
        .dropna()
    )

    highs = (
        recent["high"]
        .dropna()
    )

    if lows.empty:

        raise RuntimeError(
            "بيانات الدعم غير صالحة"
        )

    if highs.empty:

        raise RuntimeError(
            "بيانات المقاومة غير صالحة"
        )

    support = float(
        lows.min()
    )

    resistance = float(
        highs.max()
    )

    return (
        support,
        resistance
    )


# ==========================================
# واجهة البداية
# ==========================================

if not API_KEY:

    st.error(
        "مفتاح Twelve Data غير موجود في Streamlit Secrets."
    )

    st.stop()


top1, top2 = st.columns(2)

top1.metric(
    "حالة البوت",
    "Paper Trading"
)

top2.metric(
    "الرصيد التجريبي",
    "$10,000"
)


# ==========================================
# تشغيل التحليل
# ==========================================

if st.button(
    "🔄 تحليل الذهب الآن",
    type="primary"
):

    try:

        frames = {}

        scores = {}

        reasons_all = {}

        # ----------------------------------
        # M5 / M15 / H1
        # ----------------------------------

        timeframes = [
            ("M5", "5min"),
            ("M15", "15min"),
            ("H1", "1h")
        ]

        for label, interval in timeframes:

            raw = get_candles(
                interval
            )

            (
                frames[label],
                scores[label],
                reasons_all[label]
            ) = analyze_timeframe(
                raw
            )

        # ----------------------------------
        # السعر
        # ----------------------------------

        latest = (
            frames["M5"]
            .iloc[-1]
        )

        price = float(
            latest["close"]
        )

        atr = float(
            latest["atr"]
        )

        # ----------------------------------
        # الدعم والمقاومة
        # ----------------------------------

        support, resistance = (
            support_resistance(
                frames["M15"]
            )
        )

        # ----------------------------------
        # مجموع النقاط
        # ----------------------------------

        total_score = int(
            sum(
                scores.values()
            )
        )

        bullish_frames = sum(
            1
            for value in scores.values()
            if value > 0
        )

        bearish_frames = sum(
            1
            for value in scores.values()
            if value < 0
        )

        # ----------------------------------
        # الاتجاه
        # ----------------------------------

        if bullish_frames >= 2:

            trend = "صاعد 📈"

        elif bearish_frames >= 2:

            trend = "هابط 📉"

        else:

            trend = "مختلط ↔️"

        # ----------------------------------
        # محرك الإشارة
        # ----------------------------------

        signal = "WAIT"

        if (
            total_score >= 8
            and bullish_frames >= 2
            and float(latest["adx"]) >= 20
            and price > support
        ):

            signal = "BUY"

        elif (
            total_score <= -8
            and bearish_frames >= 2
            and float(latest["adx"]) >= 20
            and price < resistance
        ):

            signal = "SELL"

        # ----------------------------------
        # قوة التوافق
        # ----------------------------------

        strength = min(
            100,
            50
            + abs(total_score) * 5
        )

        # ----------------------------------
        # مستويات الصفقة
        # ----------------------------------

        entry = price

        stop_loss = None
        target1 = None
        target2 = None

        if signal == "BUY":

            stop_loss = (
                entry
                - (
                    1.5 * atr
                )
            )

            target1 = (
                entry
                + (
                    1.5 * atr
                )
            )

            target2 = (
                entry
                + (
                    2.5 * atr
                )
            )

        elif signal == "SELL":

            stop_loss = (
                entry
                + (
                    1.5 * atr
                )
            )

            target1 = (
                entry
                - (
                    1.5 * atr
                )
            )

            target2 = (
                entry
                - (
                    2.5 * atr
                )
            )

        # ==================================
        # الشاشة
        # ==================================

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
            f"{float(latest['adx']):.1f}"
        )

        # ----------------------------------
        # الإشارة
        # ----------------------------------

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

        # ----------------------------------
        # خطة الصفقة
        # ----------------------------------

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
            (
                f"${stop_loss:,.2f}"
                if stop_loss is not None
                else "—"
            )
        )

        p3.metric(
            "الهدف 1",
            (
                f"${target1:,.2f}"
                if target1 is not None
                else "—"
            )
        )

        p4.metric(
            "الهدف 2",
            (
                f"${target2:,.2f}"
                if target2 is not None
                else "—"
            )
        )

        # ----------------------------------
        # هيكل السوق
        # ----------------------------------

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

        # ----------------------------------
        # تحليل الأطر
        # ----------------------------------

        st.subheader(
            "📊 التحليل متعدد الأطر"
        )

        rows = []

        for timeframe in [
            "M5",
            "M15",
            "H1"
        ]:

            x = (
                frames[timeframe]
                .iloc[-1]
            )

            rows.append(
                {
                    "الإطار": timeframe,

                    "Score":
                        int(
                            scores[timeframe]
                        ),

                    "RSI":
                        round(
                            float(
                                x["rsi"]
                            ),
                            1
                        ),

                    "ADX":
                        round(
                            float(
                                x["adx"]
                            ),
                            1
                        ),

                    "MACD":
                        (
                            "صاعد"
                            if x["macd"]
                            > x["macd_signal"]
                            else "هابط"
                        ),

                    "EMA":
                        (
                            "صاعد"
                            if x["ema20"]
                            > x["ema50"]
                            else "هابط"
                        ),

                    "Momentum":
                        (
                            "موجب"
                            if x["momentum"] > 0
                            else "سالب"
                        )
                }
            )

        result_table = pd.DataFrame(
            rows
        )

        st.dataframe(
            result_table,
            use_container_width=True,
            hide_index=True
        )

        # ----------------------------------
        # أسباب التحليل
        # ----------------------------------

        st.subheader(
            "🧠 أسباب التحليل"
        )

        for timeframe in [
            "M5",
            "M15",
            "H1"
        ]:

            st.write(
                f"**{timeframe}:** "
                + " • ".join(
                    reasons_all[
                        timeframe
                    ]
                )
            )

        # ----------------------------------
        # المحرك
        # ----------------------------------

        st.divider()

        st.write(
            f"**مجموع نقاط التوافق:** "
            f"{total_score}"
        )

        st.write(
            f"**الاتجاه العام:** "
            f"{trend}"
        )

        st.write(
            f"**الإشارة الحالية:** "
            f"{signal}"
        )

        st.info(
            "Paper Trading فقط. "
            "قوة التوافق ليست احتمالًا للربح، "
            "ولا يوجد ضمان لنتيجة أي صفقة. "
            "سيتم اختبار المحرك تاريخيًا قبل استخدامه "
            "في أي تداول حقيقي."
        )

    except Exception as e:

        st.error(
            "حدث خطأ أثناء التحليل: "
            + str(e)
        )

else:

    st.info(
        "اضغط «🔄 تحليل الذهب الآن» "
        "لبدء التحليل."
    )

import streamlit as st
import requests
import pandas as pd

st.set_page_config(
    page_title="بوت الذهب XAU/USD",
    page_icon="🥇",
    layout="wide"
)

st.title("🥇 بوت الذهب XAU/USD")
st.caption("Smart Paper Trading • تحليل فني متعدد الأطر • بدون تداول حقيقي")

API_KEY = st.secrets.get("TWELVE_DATA_API_KEY", "")

def get_candles(interval, outputsize=150):
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
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.dropna().sort_values("datetime").reset_index(drop=True)


def indicators(df):
    df = df.copy()

    # EMA
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()

    # RSI
    delta = df["close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)

    df["rsi"] = (
        100 - (100 / (1 + rs))
    ).fillna(50)

    # MACD
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()

    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(
        span=9,
        adjust=False
    ).mean()

    # ATR
    previous_close = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs()
        ],
        axis=1
    ).max(axis=1)

    df["atr"] = tr.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    return df


def analyze(df):

    df = indicators(df)

    x = df.iloc[-1]

    score = 0

    # EMA
    if x["ema20"] > x["ema50"]:
        score += 2
    else:
        score -= 2

    # RSI
    if x["rsi"] >= 55:
        score += 1

    elif x["rsi"] <= 45:
        score -= 1

    # MACD
    if x["macd"] > x["macd_signal"]:
        score += 1
    else:
        score -= 1

    return df, score


if not API_KEY:

    st.error(
        "مفتاح Twelve Data غير موجود في Streamlit Secrets."
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

        # تحليل الأطر الزمنية
        for label, interval in [
            ("M5", "5min"),
            ("M15", "15min"),
            ("H1", "1h")
        ]:

            df = get_candles(interval)

            frames[label], scores[label] = analyze(df)


        # السعر الحالي
        price = float(
            frames["M5"].iloc[-1]["close"]
        )

        atr = float(
            frames["M5"].iloc[-1]["atr"]
        )


        # مجموع الإشارات
        total_score = sum(
            scores.values()
        )


        # الإشارة
        if total_score >= 7:

            signal = "BUY"

            signal_ar = "شراء"

        elif total_score <= -7:

            signal = "SELL"

            signal_ar = "بيع"

        else:

            signal = "WAIT"

            signal_ar = "انتظار"


        # الاتجاه
        votes = []

        for timeframe in ["M5", "M15", "H1"]:

            if scores[timeframe] > 0:
                votes.append(1)

            elif scores[timeframe] < 0:
                votes.append(-1)

            else:
                votes.append(0)


        if sum(votes) >= 2:

            trend = "صاعد 📈"

        elif sum(votes) <= -2:

            trend = "هابط 📉"

        else:

            trend = "متذبذب ↔️"


        # قوة الإشارة
        confidence = min(
            95,
            50 + abs(total_score) * 5
        )


        # مستويات الصفقة
        entry = price

        stop_loss = None
        target1 = None
        target2 = None


        if signal == "BUY":

            stop_loss = entry - (
                1.5 * atr
            )

            target1 = entry + (
                1.5 * atr
            )

            target2 = entry + (
                2.5 * atr
            )


        elif signal == "SELL":

            stop_loss = entry + (
                1.5 * atr
            )

            target1 = entry - (
                1.5 * atr
            )

            target2 = entry - (
                2.5 * atr
            )


        # ==========================
        # DASHBOARD
        # ==========================

        st.divider()

        col1, col2, col3, col4 = st.columns(4)

        col1.metric(
            "XAU/USD",
            f"${price:,.2f}"
        )

        col2.metric(
            "الاتجاه",
            trend
        )

        col3.metric(
            "قوة الإشارة",
            f"{confidence}%"
        )

        col4.metric(
            "الحالة",
            "Paper Trading"
        )


        # الإشارة
        if signal == "BUY":

            st.success(
                "🟢 إشارة شراء"
            )

        elif signal == "SELL":

            st.error(
                "🔴 إشارة بيع"
            )

        else:

            st.warning(
                "🟡 انتظار — لا يوجد توافق كافٍ"
            )


        # ==========================
        # TRADE PLAN
        # ==========================

        st.subheader(
            "🎯 خطة الصفقة"
        )

        p1, p2, p3, p4 = st.columns(4)

        p1.metric(
            "الدخول",
            f"${entry:,.2f}"
        )

        if stop_loss:

            p2.metric(
                "وقف الخسارة",
                f"${stop_loss:,.2f}"
            )

            p3.metric(
                "الهدف 1",
                f"${target1:,.2f}"
            )

            p4.metric(
                "الهدف 2",
                f"${target2:,.2f}"
            )

        else:

            p2.metric(
                "وقف الخسارة",
                "—"
            )

            p3.metric(
                "الهدف 1",
                "—"
            )

            p4.metric(
                "الهدف 2",
                "—"
            )


        # ==========================
        # TIMEFRAMES
        # ==========================

        st.subheader(
            "📊 تحليل الأطر الزمنية"
        )

        rows = []


        for timeframe in [
            "M5",
            "M15",
            "H1"
        ]:

            x = frames[
                timeframe
            ].iloc[-1]


            rows.append({

                "الإطار":
                    timeframe,

                "النتيجة":
                    scores[timeframe],

                "RSI":
                    round(
                        float(x["rsi"]),
                        1
                    ),

                "MACD":
                    "صاعد"
                    if x["macd"]
                    > x["macd_signal"]
                    else "هابط",

                "EMA20/50":
                    "صاعد"
                    if x["ema20"]
                    > x["ema50"]
                    else "هابط",

                "ATR":
                    round(
                        float(x["atr"]),
                        2
                    )
            })


        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True
        )


        # ==========================
        # STATUS
        # ==========================

        st.subheader(
            "🧠 حالة المحرك"
        )

        st.write(
            f"مجموع نقاط التوافق: **{total_score}**"
        )

        st.write(
            f"الاتجاه العام: **{trend}**"
        )

        st.write(
            f"الإشارة الحالية: **{signal_ar}**"
        )


        st.info(
            "هذا النظام Paper Trading تجريبي. "
            "مستويات الدخول ووقف الخسارة والأهداف "
            "محسوبة آليًا باستخدام ATR والمؤشرات الفنية، "
            "ولا تمثل ضمانًا للربح أو توصية استثمارية."
        )


    except Exception as e:

        st.error(
            "تعذر إكمال التحليل: "
            + str(e)
        )


else:

    st.info(
        "اضغط «🔄 تحليل الذهب الآن» "
        "لبدء التحليل متعدد الأطر."
    )

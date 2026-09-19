import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime

# =========================================================
# إعداد الصفحة
# =========================================================

st.set_page_config(
    page_title="بوت الذهب XAU/USD",
    page_icon="🟡",
    layout="wide"
)

st.title("🟡 بوت الذهب XAU/USD")
st.caption("Smart Paper Trading — تحليل متعدد الأطر الزمنية")

# =========================================================
# الإعدادات
# =========================================================

SYMBOL = "XAU/USD"
API_KEY = st.secrets.get("TWELVE_DATA_API_KEY", "")

if not API_KEY:
    st.error("❌ لم يتم العثور على TWELVE_DATA_API_KEY في Secrets")
    st.stop()

# =========================================================
# جلب البيانات
# =========================================================

@st.cache_data(ttl=30)
def get_candles(interval, outputsize=200):

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
        error_message = data.get(
            "message",
            "لم يتم استلام بيانات صحيحة من Twelve Data"
        )
        raise ValueError(error_message)

    df = pd.DataFrame(data["values"])

    required = [
        "datetime",
        "open",
        "high",
        "low",
        "close"
    ]

    missing = [
        column for column in required
        if column not in df.columns
    ]

    if missing:
        raise ValueError(
            f"أعمدة ناقصة من البيانات: {missing}"
        )

    # تحويل البيانات الرقمية بشكل صريح
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

    # حذف الصفوف غير الصالحة
    df = df.dropna(
        subset=[
            "datetime",
            "open",
            "high",
            "low",
            "close"
        ]
    )

    # ترتيب زمني
    df = df.sort_values(
        "datetime"
    ).reset_index(drop=True)

    if len(df) < 60:
        raise ValueError(
            f"عدد الشموع غير كافٍ للتحليل: {len(df)}"
        )

    return df


# =========================================================
# المؤشرات الفنية
# =========================================================

def calculate_indicators(df):

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

    # -----------------------------------------------------
    # RSI 14
    # -----------------------------------------------------

    delta = close.diff()

    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    avg_gain = gains.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    avg_loss = losses.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan
    )

    df["rsi"] = 100 - (
        100 / (1 + rs)
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

    df["macd"] = ema12 - ema26

    df["macd_signal"] = df["macd"].ewm(
        span=9,
        adjust=False
    ).mean()

    df["macd_hist"] = (
        df["macd"] -
        df["macd_signal"]
    )

    # -----------------------------------------------------
    # ATR 14
    # بدون pd.concat().max()
    # -----------------------------------------------------

    previous_close = close.shift(1)

    tr_a = high - low

    tr_b = (
        high -
        previous_close
    ).abs()

    tr_c = (
        low -
        previous_close
    ).abs()

    true_range = tr_a.copy()

    true_range = true_range.where(
        tr_b <= true_range,
        tr_b
    )

    true_range = true_range.where(
        tr_c <= true_range,
        tr_c
    )

    df["atr"] = true_range.ewm(
        span=14,
        adjust=False
    ).mean()

    # -----------------------------------------------------
    # Momentum
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

    atr_for_adx = true_range.ewm(
        span=14,
        adjust=False
    ).mean()

    plus_di = (
        100 *
        plus_dm.ewm(
            span=14,
            adjust=False
        ).mean() /
        atr_for_adx.replace(0, np.nan)
    )

    minus_di = (
        100 *
        minus_dm.ewm(
            span=14,
            adjust=False
        ).mean() /
        atr_for_adx.replace(0, np.nan)
    )

    dx_denominator = (
        plus_di +
        minus_di
    ).replace(
        0,
        np.nan
    )

    dx = (
        100 *
        (plus_di - minus_di).abs() /
        dx_denominator
    )

    df["adx"] = dx.ewm(
        span=14,
        adjust=False
    ).mean()

    df["plus_di"] = plus_di
    df["minus_di"] = minus_di

    return df


# =========================================================
# دعم ومقاومة
# =========================================================

def get_support_resistance(df, lookback=40):

    recent = df.tail(lookback)

    support = float(
        recent["low"].min()
    )

    resistance = float(
        recent["high"].max()
    )

    return support, resistance


# =========================================================
# تحليل الإطار الزمني
# =========================================================

def analyze_timeframe(df):

    last = df.iloc[-1]

    price = float(last["close"])

    score = 0
    reasons = []

    # -----------------------------------------------------
    # الاتجاه EMA
    # -----------------------------------------------------

    if (
        last["ema20"] >
        last["ema50"]
    ):
        score += 2
        reasons.append("EMA صاعد")

    elif (
        last["ema20"] <
        last["ema50"]
    ):
        score -= 2
        reasons.append("EMA هابط")

    # -----------------------------------------------------
    # السعر مقابل EMA20
    # -----------------------------------------------------

    if price > last["ema20"]:
        score += 1
        reasons.append("السعر فوق EMA20")

    elif price < last["ema20"]:
        score -= 1
        reasons.append("السعر تحت EMA20")

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------

    rsi = float(last["rsi"])

    if 50 <= rsi < 70:
        score += 1
        reasons.append("RSI إيجابي")

    elif 30 < rsi < 50:
        score -= 1
        reasons.append("RSI سلبي")

    # تجنب شراء مبالغ فيه عند RSI شديد الارتفاع
    if rsi >= 75:
        score -= 1
        reasons.append("RSI مرتفع جداً")

    # تجنب بيع مبالغ فيه عند RSI شديد الانخفاض
    if rsi <= 25:
        score += 1
        reasons.append("RSI منخفض جداً")

    # -----------------------------------------------------
    # MACD
    # -----------------------------------------------------

    if (
        last["macd"] >
        last["macd_signal"]
    ):
        score += 1
        reasons.append("MACD إيجابي")

    else:
        score -= 1
        reasons.append("MACD سلبي")

    # -----------------------------------------------------
    # Momentum
    # -----------------------------------------------------

    if last["momentum"] > 0:
        score += 1
        reasons.append("Momentum إيجابي")

    elif last["momentum"] < 0:
        score -= 1
        reasons.append("Momentum سلبي")

    # -----------------------------------------------------
    # ADX
    # -----------------------------------------------------

    adx = float(last["adx"])

    if adx >= 25:

        if last["plus_di"] > last["minus_di"]:
            score += 1
            reasons.append("ADX يدعم الاتجاه الصاعد")

        elif last["minus_di"] > last["plus_di"]:
            score -= 1
            reasons.append("ADX يدعم الاتجاه الهابط")

    else:
        reasons.append("ADX ضعيف")

    # -----------------------------------------------------
    # تحديد الاتجاه
    # -----------------------------------------------------

    if score >= 3:
        trend = "صاعد 📈"

    elif score <= -3:
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
# التحليل الرئيسي
# =========================================================

def full_analysis():

    m5 = calculate_indicators(
        get_candles("5min")
    )

    m15 = calculate_indicators(
        get_candles("15min")
    )

    h1 = calculate_indicators(
        get_candles("1h")
    )

    analysis_m5 = analyze_timeframe(m5)
    analysis_m15 = analyze_timeframe(m15)
    analysis_h1 = analyze_timeframe(h1)

    # -----------------------------------------------------
    # مجموع النقاط
    # -----------------------------------------------------

    total_score = (
        analysis_m5["score"] +
        analysis_m15["score"] +
        analysis_h1["score"]
    )

    # -----------------------------------------------------
    # الاتجاهات
    # -----------------------------------------------------

    trends = [
        analysis_m5["trend"],
        analysis_m15["trend"],
        analysis_h1["trend"]
    ]

    bullish = trends.count("صاعد 📈")
    bearish = trends.count("هابط 📉")

    if bullish >= 2:
        overall_trend = "صاعد 📈"

    elif bearish >= 2:
        overall_trend = "هابط 📉"

    else:
        overall_trend = "محايد ↔️"

    # -----------------------------------------------------
    # قوة التوافق
    # -----------------------------------------------------

    max_score = 21

    strength = int(
        min(
            100,
            max(
                0,
                abs(total_score) /
                max_score *
                100
            )
        )
    )

    # -----------------------------------------------------
    # القرار
    # -----------------------------------------------------

    signal = "WAIT"

    if (
        overall_trend == "صاعد 📈"
        and total_score >= 7
        and analysis_m15["adx"] >= 18
    ):
        signal = "BUY"

    elif (
        overall_trend == "هابط 📉"
        and total_score <= -7
        and analysis_m15["adx"] >= 18
    ):
        signal = "SELL"

    # -----------------------------------------------------
    # السعر
    # -----------------------------------------------------

    entry = analysis_m5["price"]

    atr = analysis_m15["atr"]

    # -----------------------------------------------------
    # وقف الخسارة والأهداف
    # -----------------------------------------------------

    if signal == "BUY":

        stop_loss = entry - (
            atr * 1.5
        )

        take_profit_1 = entry + (
            atr * 1.5
        )

        take_profit_2 = entry + (
            atr * 2.5
        )

    elif signal == "SELL":

        stop_loss = entry + (
            atr * 1.5
        )

        take_profit_1 = entry - (
            atr * 1.5
        )

        take_profit_2 = entry - (
            atr * 2.5
        )

    else:

        stop_loss = None
        take_profit_1 = None
        take_profit_2 = None

    # -----------------------------------------------------
    # دعم ومقاومة
    # -----------------------------------------------------

    support, resistance = get_support_resistance(
        m15
    )

    return {
        "m5": analysis_m5,
        "m15": analysis_m15,
        "h1": analysis_h1,
        "total_score": total_score,
        "overall_trend": overall_trend,
        "strength": strength,
        "signal": signal,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit_1": take_profit_1,
        "take_profit_2": take_profit_2,
        "support": support,
        "resistance": resistance
    }


# =========================================================
# واجهة التطبيق
# =========================================================

st.divider()

if st.button(
    "🔍 تحليل XAU/USD",
    use_container_width=True
):

    with st.spinner(
        "جاري تحليل الذهب عبر M5 / M15 / H1..."
    ):

        try:

            result = full_analysis()

            # =============================================
            # البيانات الرئيسية
            # =============================================

            col1, col2, col3, col4 = st.columns(4)

            with col1:
                st.metric(
                    "سعر XAU/USD",
                    f"${result['entry']:,.2f}"
                )

            with col2:
                st.metric(
                    "الاتجاه العام",
                    result["overall_trend"]
                )

            with col3:
                st.metric(
                    "قوة التوافق",
                    f"{result['strength']}%"
                )

            with col4:

                if result["signal"] == "BUY":
                    signal_text = "🟢 BUY"

                elif result["signal"] == "SELL":
                    signal_text = "🔴 SELL"

                else:
                    signal_text = "🟡 WAIT"

                st.metric(
                    "الإشارة",
                    signal_text
                )

            # =============================================
            # تفاصيل الصفقة
            # =============================================

            st.divider()

            if result["signal"] != "WAIT":

                c1, c2, c3 = st.columns(3)

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
                        f"{result['take_profit_1']:,.2f}"
                    )

                st.metric(
                    "TP2",
                    f"{result['take_profit_2']:,.2f}"
                )

            else:

                st.info(
                    "🟡 انتظار — لا يوجد توافق كافٍ "
                    "لفتح صفقة Paper Trading."
                )

            # =============================================
            # الدعم والمقاومة
            # =============================================

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

            # =============================================
            # جدول الأطر الزمنية
            # =============================================

            st.divider()

            st.subheader(
                "📊 توافق الأطر الزمنية"
            )

            timeframe_table = pd.DataFrame([
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
                    ),
                    "ATR": round(
                        result["m5"]["atr"],
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
                    ),
                    "ATR": round(
                        result["m15"]["atr"],
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
                    ),
                    "ATR": round(
                        result["h1"]["atr"],
                        2
                    )
                }
            ])

            st.dataframe(
                timeframe_table,
                use_container_width=True,
                hide_index=True
            )

            # =============================================
            # أسباب التحليل
            # =============================================

            st.divider()

            st.subheader(
                "🧠 أسباب التحليل"
            )

            for timeframe, data in [
                ("M5", result["m5"]),
                ("M15", result["m15"]),
                ("H1", result["h1"])
            ]:

                with st.expander(
                    f"{timeframe} — {data['trend']}"
                ):

                    for reason in data["reasons"]:
                        st.write(
                            f"• {reason}"
                        )

            # =============================================
            # Paper Trading
            # =============================================

            st.divider()

            st.subheader(
                "🧪 Paper Trading"
            )

            st.info(
                "هذا النظام تحليلي وتجريبي فقط. "
                "لا يتم إرسال أوامر حقيقية إلى الوسيط."
            )

            # =============================================
            # وقت التحليل
            # =============================================

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

            st.info(
                "إذا استمر الخطأ، أرسل لي نص الخطأ كما يظهر "
                "بالضبط وسأحدد مكانه."
            )

else:

    st.info(
        "اضغط «🔍 تحليل XAU/USD» لبدء التحليل."
    )

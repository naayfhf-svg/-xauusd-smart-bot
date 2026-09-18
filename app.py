import streamlit as st
import requests

st.set_page_config(
    page_title="بوت الذهب XAU/USD",
    page_icon="🥇",
    layout="centered"
)

st.title("🥇 بوت الذهب XAU/USD")
st.caption("Smart Paper Trading Bot")

API_KEY = st.secrets.get("TWELVE_DATA_API_KEY", "")

st.metric("حالة البوت", "Paper Trading")
st.metric("الرصيد التجريبي", "$10,000")

if not API_KEY:
    st.error("مفتاح Twelve Data غير موجود في Streamlit Secrets")
else:
    st.success("مفتاح البيانات متصل ✅")

    if st.button("جلب سعر الذهب الآن"):
        try:
            response = requests.get(
                "https://api.twelvedata.com/price",
                params={
                    "symbol": "XAU/USD",
                    "apikey": API_KEY
                },
                timeout=10
            )

            data = response.json()

            if "price" in data:
                price = float(data["price"])
                st.metric("XAU/USD", f"${price:,.2f}")
            else:
                st.error("تعذر جلب السعر: " + str(data))

        except Exception as e:
            st.error("حدث خطأ: " + str(e))

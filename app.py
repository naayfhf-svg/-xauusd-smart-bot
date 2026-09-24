from __future__ import annotations

import os
import time
from typing import Any

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="GOLD AI — مضاربة الذهب",
    page_icon="🟡",
    layout="centered",
)

VERSION = "7.0.0-gold-lite-stable"
TZ = "Asia/Riyadh"
SYMBOL = "XAU/USD"
QUOTE_URL = "https://api.twelvedata.com/quote"
HISTORY_URL = "https://api.twelvedata.com/time_series"


def secret(name: str, default: Any = None) -> Any:
    try:
        return st.secrets[name]
    except Exception:
        return os.getenv(name, default)


def finite(value: Any) -> bool:
    try:
        return pd.notna(value) and float(value) > 0
    except Exception:
        return False


def now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def parse_ts(value: Any) -> pd.Timestamp | None:
    try:
        ts = pd.to_datetime(value, utc=True, errors="coerce")
        return None if pd.isna(ts) else pd.Timestamp(ts)
    except Exception:
        return None


def fmt(value: Any, decimals: int = 3) -> str:
    try:
        if value is None or pd.isna(value):
            return "—"
        return f"{float(value):,.{decimals}f}"
    except Exception:
        return "—"


@st.cache_data(ttl=9, show_spinner=False)
def fetch_quote() -> dict[str, Any]:
    key = str(secret("TWELVE_DATA_API_KEY", "") or "").strip()
    if not key:
        return {"ok": False, "error": "TWELVE_DATA_API_KEY غير موجود"}

    started = time.perf_counter()
    try:
        r = requests.get(
            QUOTE_URL,
            params={
                "symbol": SYMBOL,
                "interval": "1min",
                "timezone": "UTC",
                "apikey": key,
            },
            timeout=4,
        )
        received = now_utc()
        payload = r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    if r.status_code >= 400 or not isinstance(payload, dict):
        return {"ok": False, "error": f"HTTP {r.status_code}"}

    last = payload.get("close")
    bid = payload.get("bid")
    ask = payload.get("ask")
    ts = parse_ts(
        payload.get("last_update_at")
        or payload.get("last_quote_at")
        or payload.get("timestamp")
        or payload.get("datetime")
    )
    if not finite(last):
        return {"ok": False, "error": str(payload.get("message") or "سعر غير صالح")}

    return {
        "ok": True,
        "last": float(last),
        "bid": float(bid) if finite(bid) else None,
        "ask": float(ask) if finite(ask) else None,
        "timestamp": ts,
        "received_at": received,
        "latency_ms": (time.perf_counter() - started) * 1000.0,
        "source": "Twelve Data",
    }


@st.cache_data(ttl=55, show_spinner=False)
def fetch_history() -> pd.DataFrame:
    key = str(secret("TWELVE_DATA_API_KEY", "") or "").strip()
    if not key:
        return pd.DataFrame()

    try:
        r = requests.get(
            HISTORY_URL,
            params={
                "symbol": SYMBOL,
                "interval": "5min",
                "outputsize": 1200,
                "timezone": "UTC",
                "apikey": key,
            },
            timeout=6,
        )
        payload = r.json()
    except Exception:
        return pd.DataFrame()

    values = payload.get("values") if isinstance(payload, dict) else None
    if not isinstance(values, list) or not values:
        return pd.DataFrame()

    df = pd.DataFrame(values)
    needed = {"datetime", "open", "high", "low", "close"}
    if not needed.issubset(df.columns):
        return pd.DataFrame()

    df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    return df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)


@st.cache_data(ttl=9, show_spinner=False)
def fetch_goldapi() -> dict[str, Any]:
    key = str(secret("GOLDAPI_KEY", "") or "").strip()
    if not key:
        return {"ok": False, "configured": False}

    try:
        r = requests.get(
            "https://www.goldapi.io/api/price/XAU/USD",
            headers={"x-access-token": key, "Content-Type": "application/json"},
            timeout=4,
        )
        payload = r.json()
    except Exception as exc:
        return {"ok": False, "configured": True, "error": str(exc)}

    price = payload.get("price") if isinstance(payload, dict) else None
    return {
        "ok": bool(r.status_code < 400 and finite(price)),
        "configured": True,
        "price": float(price) if finite(price) else None,
        "timestamp": parse_ts(payload.get("timestamp") if isinstance(payload, dict) else None),
    }


def indicators(frame: pd.DataFrame) -> pd.DataFrame:
    x = frame.copy()
    x["ema20"] = x["close"].ewm(span=20, adjust=False).mean()
    x["ema50"] = x["close"].ewm(span=50, adjust=False).mean()

    delta = x["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, pd.NA)
    x["rsi"] = 100 - (100 / (1 + rs))

    tr = pd.concat(
        [
            (x["high"] - x["low"]).abs(),
            (x["high"] - x["close"].shift()).abs(),
            (x["low"] - x["close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    x["atr"] = tr.rolling(14).mean()
    return x


def resample(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    x = frame.set_index("datetime")
    out = (
        x.resample(rule, label="right", closed="right")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
        .reset_index()
    )
    return indicators(out)


def analyze(frame: pd.DataFrame) -> dict[str, Any]:
    if len(frame) < 120:
        return {"signal": "WAIT", "reason": "بيانات غير كافية", "strength": 0}

    m5 = indicators(frame)
    m15 = resample(frame, "15min")
    h1 = resample(frame, "1h")
    if len(m15) < 55 or len(h1) < 55:
        return {"signal": "WAIT", "reason": "انتظر اكتمال الفريمات", "strength": 0}

    a = m5.iloc[-1]
    b = m15.iloc[-1]
    c = h1.iloc[-1]
    prior = m5.iloc[-7:-1]

    buy_checks = [
        c["close"] > c["ema20"] > c["ema50"],
        b["close"] > b["ema20"] > b["ema50"],
        a["close"] > a["ema20"] > a["ema50"],
        50 <= float(a["rsi"]) <= 68 if pd.notna(a["rsi"]) else False,
        a["close"] > float(prior["high"].max()),
    ]
    sell_checks = [
        c["close"] < c["ema20"] < c["ema50"],
        b["close"] < b["ema20"] < b["ema50"],
        a["close"] < a["ema20"] < a["ema50"],
        32 <= float(a["rsi"]) <= 50 if pd.notna(a["rsi"]) else False,
        a["close"] < float(prior["low"].min()),
    ]

    buy_score = round(sum(bool(x) for x in buy_checks) / len(buy_checks) * 100)
    sell_score = round(sum(bool(x) for x in sell_checks) / len(sell_checks) * 100)

    if all(buy_checks):
        signal = "BUY"
        reason = "اتجاه صاعد واختراق مؤكد"
    elif all(sell_checks):
        signal = "SELL"
        reason = "اتجاه هابط وكسر مؤكد"
    else:
        signal = "WAIT"
        reason = "الشروط غير مكتملة"

    return {
        "signal": signal,
        "reason": reason,
        "strength": max(buy_score, sell_score),
        "buy_score": buy_score,
        "sell_score": sell_score,
        "atr": float(a["atr"]) if finite(a["atr"]) else None,
        "buy_trigger": float(prior["high"].max()),
        "sell_trigger": float(prior["low"].min()),
    }


def consensus(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    if not primary.get("ok"):
        return {"ok": False, "count": 0, "reason": "المصدر الرئيسي غير جاهز"}

    prices = [float(primary["last"])]
    if secondary.get("ok") and finite(secondary.get("price")):
        prices.append(float(secondary["price"]))

    if len(prices) < 2:
        return {"ok": False, "count": 1, "reason": "يلزم مصدر ثانٍ لتأكيد الدخول"}

    mid = sum(prices) / len(prices)
    spread_pct = (max(prices) - min(prices)) / mid * 100 if mid else 999
    return {
        "ok": spread_pct <= 0.20,
        "count": len(prices),
        "spread_pct": spread_pct,
        "reason": "المصادر متفقة" if spread_pct <= 0.20 else "اختلاف المصادر مرتفع",
    }


def quote_age(quote: dict[str, Any]) -> float | None:
    ts = quote.get("received_at")
    if ts is None:
        return None
    try:
        return max(0.0, (now_utc() - pd.Timestamp(ts)).total_seconds())
    except Exception:
        return None


def trade_plan(signal: str, quote: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any] | None:
    atr = analysis.get("atr")
    if signal not in {"BUY", "SELL"} or not finite(atr):
        return None

    last = float(quote["last"])
    if signal == "BUY":
        entry = float(quote["ask"]) if finite(quote.get("ask")) else last
        stop = entry - float(atr) * 1.25
        risk = entry - stop
        return {"entry": entry, "stop": stop, "tp1": entry + risk, "tp2": entry + 2 * risk}

    entry = float(quote["bid"]) if finite(quote.get("bid")) else last
    stop = entry + float(atr) * 1.25
    risk = stop - entry
    return {"entry": entry, "stop": stop, "tp1": entry - risk, "tp2": entry - 2 * risk}


st.markdown(
    """
    <style>
    .block-container{max-width:760px;padding-top:1rem}
    .hero{padding:20px;border:1px solid #3a4658;border-radius:20px;background:#0d1521;margin-bottom:14px}
    .hero h1{margin:0;color:#d4af37;font-size:2.2rem}
    .hero p{margin:.35rem 0 0;color:#a9b4c5}
    .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
    .box{background:#0e1622;border:1px solid #34445b;border-radius:16px;padding:14px}
    .box .l{color:#97a5b7;font-size:.82rem}
    .box .v{font-size:1.35rem;font-weight:800;margin-top:5px}
    .ok{color:#55d68a}.wait{color:#f2c15d}.bad{color:#ff7c7c}
    @media(max-width:600px){.block-container{padding:.6rem}.box{padding:12px}.box .v{font-size:1.12rem}}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"<div class='hero'><h1>🟡 XAU/USD</h1><p>مضاربة الذهب — قرار مختصر وواضح • v{VERSION}</p></div>",
    unsafe_allow_html=True,
)

if not str(secret("TWELVE_DATA_API_KEY", "") or "").strip():
    st.error("أضف TWELVE_DATA_API_KEY في Streamlit Secrets لتشغيل الأسعار.")
else:
    history = fetch_history()

    @st.fragment(run_every=3)
    def quick_panel() -> None:
        try:
            quote = fetch_quote()
            second = fetch_goldapi()
            check = consensus(quote, second)
            age = quote_age(quote)

            if history.empty or not quote.get("ok"):
                decision = "انتظر"
                state = "wait"
                analysis = {"strength": 0, "reason": quote.get("error") or "البيانات غير جاهزة"}
                plan = None
                watch = "—"
            else:
                analysis = analyze(history)
                signal = analysis.get("signal", "WAIT")
                fresh = age is not None and age <= 12

                confirmed = bool(
                    signal in {"BUY", "SELL"}
                    and fresh
                    and check.get("ok")
                )
                if confirmed:
                    decision = "شراء الآن" if signal == "BUY" else "بيع الآن"
                    state = "ok"
                    plan = trade_plan(signal, quote, analysis)
                    watch = fmt(plan.get("entry") if plan else None)
                else:
                    decision = "لا تدخل الآن"
                    state = "wait"
                    plan = None
                    if analysis.get("buy_score", 0) >= analysis.get("sell_score", 0):
                        watch = "فوق " + fmt(analysis.get("buy_trigger"))
                    else:
                        watch = "تحت " + fmt(analysis.get("sell_trigger"))

            entry = fmt(plan.get("entry")) if plan else watch
            tp1 = fmt(plan.get("tp1")) if plan else "—"
            stop = fmt(plan.get("stop")) if plan else "—"
            strength = f"{int(analysis.get('strength', 0))}%"
            price = fmt(quote.get("last"))
            age_text = "—" if age is None else f"{age:.1f} ث"

            html = f"""
            <div class='grid'>
              <div class='box'><div class='l'>وش أسوي؟</div><div class='v {state}'>{decision}</div></div>
              <div class='box'><div class='l'>السعر الآن</div><div class='v'>{price}</div></div>
              <div class='box'><div class='l'>الدخول / المراقبة</div><div class='v'>{entry}</div></div>
              <div class='box'><div class='l'>خذ الربح عند</div><div class='v ok'>{tp1}</div></div>
              <div class='box'><div class='l'>وقف الخسارة</div><div class='v bad'>{stop}</div></div>
              <div class='box'><div class='l'>الجاهزية</div><div class='v'>{strength}</div></div>
            </div>
            """
            st.markdown(html, unsafe_allow_html=True)

            source_text = (
                f"المصادر {check.get('count', 0)}/2 • {check.get('reason', '')}"
                + f" • عمر آخر سعر {age_text}"
            )
            st.caption(source_text)
            st.caption(str(analysis.get("reason") or ""))

            if plan:
                st.caption(f"الهدف الثاني: {fmt(plan.get('tp2'))}")

            if not second.get("configured", False):
                st.info("لرفع دقة التأكيد أضف GOLDAPI_KEY كمصدر سعر ثانٍ.")
        except Exception as exc:
            st.error(f"تعذر تحديث القرار: {type(exc).__name__}")
            st.caption("تم منع الخطأ من إسقاط التطبيق. سيحاول التحديث تلقائيًا.")

    quick_panel()

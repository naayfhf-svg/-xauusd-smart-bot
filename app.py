from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import streamlit as st

st.set_page_config(
    page_title="GOLD AI — مضاربة الذهب",
    page_icon="🟡",
    layout="centered",
)

VERSION = "7.1.0-no-deps-stable"
SYMBOL = "XAU/USD"
QUOTE_URL = "https://api.twelvedata.com/quote"
HISTORY_URL = "https://api.twelvedata.com/time_series"
GOLDAPI_URL = "https://www.goldapi.io/api/price/XAU/USD"


def secret(name: str, default: Any = None) -> Any:
    try:
        return st.secrets[name]
    except Exception:
        return os.getenv(name, default)


def now_ts() -> float:
    return time.time()


def finite(value: Any) -> bool:
    try:
        x = float(value)
        return x > 0 and x != float("inf") and x != float("-inf")
    except Exception:
        return False


def fmt(value: Any, decimals: int = 3) -> str:
    try:
        return f"{float(value):,.{decimals}f}"
    except Exception:
        return "—"


def http_json(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int = 5) -> tuple[int, dict[str, Any]]:
    full = url
    if params:
        full += ("&" if "?" in full else "?") + urlencode(params)
    req = Request(full, headers=headers or {})
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
        return int(getattr(resp, "status", 200)), json.loads(body)


@st.cache_data(ttl=8, show_spinner=False)
def fetch_quote() -> dict[str, Any]:
    key = str(secret("TWELVE_DATA_API_KEY", "") or "").strip()
    if not key:
        return {"ok": False, "error": "TWELVE_DATA_API_KEY غير موجود"}

    started = time.perf_counter()
    try:
        status, payload = http_json(
            QUOTE_URL,
            {
                "symbol": SYMBOL,
                "interval": "1min",
                "timezone": "UTC",
                "apikey": key,
            },
            timeout=4,
        )
        received = now_ts()
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if status >= 400 or not isinstance(payload, dict):
        return {"ok": False, "error": f"HTTP {status}"}

    last = payload.get("close")
    if not finite(last):
        return {"ok": False, "error": str(payload.get("message") or "سعر غير صالح")}

    bid = payload.get("bid")
    ask = payload.get("ask")
    return {
        "ok": True,
        "last": float(last),
        "bid": float(bid) if finite(bid) else None,
        "ask": float(ask) if finite(ask) else None,
        "received_at": received,
        "latency_ms": (time.perf_counter() - started) * 1000.0,
        "source": "Twelve Data",
    }


@st.cache_data(ttl=55, show_spinner=False)
def fetch_history() -> list[dict[str, Any]]:
    key = str(secret("TWELVE_DATA_API_KEY", "") or "").strip()
    if not key:
        return []

    try:
        status, payload = http_json(
            HISTORY_URL,
            {
                "symbol": SYMBOL,
                "interval": "5min",
                "outputsize": 1200,
                "timezone": "UTC",
                "apikey": key,
            },
            timeout=6,
        )
    except Exception:
        return []

    if status >= 400 or not isinstance(payload, dict):
        return []
    values = payload.get("values")
    if not isinstance(values, list):
        return []

    out: list[dict[str, Any]] = []
    for row in values:
        try:
            dt = datetime.fromisoformat(str(row["datetime"]).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            item = {
                "ts": dt.timestamp(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
            if min(item["open"], item["high"], item["low"], item["close"]) <= 0:
                continue
            out.append(item)
        except Exception:
            continue

    out.sort(key=lambda x: x["ts"])
    dedup: dict[float, dict[str, Any]] = {x["ts"]: x for x in out}
    return list(dedup.values())


@st.cache_data(ttl=8, show_spinner=False)
def fetch_goldapi() -> dict[str, Any]:
    key = str(secret("GOLDAPI_KEY", "") or "").strip()
    if not key:
        return {"ok": False, "configured": False}

    try:
        status, payload = http_json(
            GOLDAPI_URL,
            headers={"x-access-token": key, "Content-Type": "application/json"},
            timeout=4,
        )
    except Exception as exc:
        return {"ok": False, "configured": True, "error": f"{type(exc).__name__}: {exc}"}

    price = payload.get("price") if isinstance(payload, dict) else None
    return {
        "ok": bool(status < 400 and finite(price)),
        "configured": True,
        "price": float(price) if finite(price) else None,
    }


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1.0 - alpha) * out[-1])
    return out


def rolling_rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(len(values) - period, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr(rows: list[dict[str, Any]], period: int = 14) -> float | None:
    if len(rows) < period + 1:
        return None
    trs = []
    for i in range(len(rows) - period, len(rows)):
        cur = rows[i]
        prev = rows[i - 1]
        trs.append(
            max(
                cur["high"] - cur["low"],
                abs(cur["high"] - prev["close"]),
                abs(cur["low"] - prev["close"]),
            )
        )
    return sum(trs) / len(trs)


def resample(rows: list[dict[str, Any]], seconds: int) -> list[dict[str, Any]]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        key = int(row["ts"] // seconds) * seconds
        buckets.setdefault(key, []).append(row)
    out = []
    for key in sorted(buckets):
        group = buckets[key]
        out.append(
            {
                "ts": float(key),
                "open": group[0]["open"],
                "high": max(x["high"] for x in group),
                "low": min(x["low"] for x in group),
                "close": group[-1]["close"],
            }
        )
    return out


def trend(rows: list[dict[str, Any]]) -> str:
    closes = [x["close"] for x in rows]
    if len(closes) < 55:
        return "FLAT"
    e20 = ema(closes, 20)[-1]
    e50 = ema(closes, 50)[-1]
    close = closes[-1]
    if close > e20 > e50:
        return "UP"
    if close < e20 < e50:
        return "DOWN"
    return "FLAT"


def analyze(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) < 120:
        return {"signal": "WAIT", "strength": 0, "reason": "بيانات M5 غير كافية"}

    m15 = resample(rows, 15 * 60)
    h1 = resample(rows, 60 * 60)
    if len(m15) < 55 or len(h1) < 55:
        return {"signal": "WAIT", "strength": 0, "reason": "انتظر اكتمال M15 و H1"}

    closes = [x["close"] for x in rows]
    e20 = ema(closes, 20)[-1]
    e50 = ema(closes, 50)[-1]
    rsi = rolling_rsi(closes, 14)
    current = rows[-1]
    prior = rows[-7:-1]
    buy_trigger = max(x["high"] for x in prior)
    sell_trigger = min(x["low"] for x in prior)

    buy_checks = [
        trend(h1) == "UP",
        trend(m15) == "UP",
        current["close"] > e20 > e50,
        rsi is not None and 50 <= rsi <= 68,
        current["close"] > buy_trigger,
    ]
    sell_checks = [
        trend(h1) == "DOWN",
        trend(m15) == "DOWN",
        current["close"] < e20 < e50,
        rsi is not None and 32 <= rsi <= 50,
        current["close"] < sell_trigger,
    ]

    buy_score = round(sum(1 for x in buy_checks if x) / len(buy_checks) * 100)
    sell_score = round(sum(1 for x in sell_checks if x) / len(sell_checks) * 100)

    if all(buy_checks):
        signal, reason = "BUY", "اتجاه صاعد واختراق مؤكد"
    elif all(sell_checks):
        signal, reason = "SELL", "اتجاه هابط وكسر مؤكد"
    else:
        signal, reason = "WAIT", "الشروط لم تكتمل"

    return {
        "signal": signal,
        "strength": max(buy_score, sell_score),
        "buy_score": buy_score,
        "sell_score": sell_score,
        "reason": reason,
        "atr": atr(rows, 14),
        "buy_trigger": buy_trigger,
        "sell_trigger": sell_trigger,
    }


def consensus(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    if not primary.get("ok"):
        return {"ok": False, "count": 0, "reason": "المصدر الرئيسي غير جاهز"}

    prices = [float(primary["last"])]
    if secondary.get("ok") and finite(secondary.get("price")):
        prices.append(float(secondary["price"]))

    if len(prices) < 2:
        return {"ok": False, "count": 1, "reason": "يلزم مصدر ثانٍ"}

    mid = sum(prices) / len(prices)
    spread_pct = (max(prices) - min(prices)) / mid * 100 if mid else 999.0
    return {
        "ok": spread_pct <= 0.20,
        "count": 2,
        "spread_pct": spread_pct,
        "reason": "المصادر متفقة" if spread_pct <= 0.20 else "اختلاف المصادر مرتفع",
    }


def trade_plan(signal: str, quote: dict[str, Any], analysis: dict[str, Any]) -> dict[str, float] | None:
    a = analysis.get("atr")
    if signal not in {"BUY", "SELL"} or not finite(a):
        return None
    last = float(quote["last"])
    risk_distance = float(a) * 1.25

    if signal == "BUY":
        entry = float(quote["ask"]) if finite(quote.get("ask")) else last
        stop = entry - risk_distance
        return {"entry": entry, "stop": stop, "tp1": entry + risk_distance, "tp2": entry + 2 * risk_distance}

    entry = float(quote["bid"]) if finite(quote.get("bid")) else last
    stop = entry + risk_distance
    return {"entry": entry, "stop": stop, "tp1": entry - risk_distance, "tp2": entry - 2 * risk_distance}


st.markdown(
    """
    <style>
    .block-container{max-width:760px;padding-top:.8rem}
    .hero{padding:18px;border:1px solid #3a4658;border-radius:20px;background:#0d1521;margin-bottom:12px}
    .hero h1{margin:0;color:#d4af37;font-size:2.05rem}
    .hero p{margin:.35rem 0 0;color:#a9b4c5}
    .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}
    .box{background:#0e1622;border:1px solid #34445b;border-radius:16px;padding:13px}
    .box .l{color:#97a5b7;font-size:.78rem}
    .box .v{font-size:1.25rem;font-weight:800;margin-top:5px}
    .ok{color:#55d68a}.wait{color:#f2c15d}.bad{color:#ff7c7c}
    @media(max-width:600px){.block-container{padding:.55rem}.box{padding:11px}.box .v{font-size:1.08rem}}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"<div class='hero'><h1>🟡 XAU/USD</h1><p>قرار مضاربة مختصر • v{VERSION}</p></div>",
    unsafe_allow_html=True,
)

if not str(secret("TWELVE_DATA_API_KEY", "") or "").strip():
    st.error("أضف TWELVE_DATA_API_KEY في Secrets لتشغيل الأسعار.")
else:
    history = fetch_history()

    @st.fragment(run_every=3)
    def quick_panel() -> None:
        try:
            quote = fetch_quote()
            secondary = fetch_goldapi()
            source_check = consensus(quote, secondary)
            analysis = analyze(history) if history else {"signal": "WAIT", "strength": 0, "reason": "البيانات غير جاهزة"}

            age = None
            if quote.get("received_at"):
                age = max(0.0, now_ts() - float(quote["received_at"]))

            signal = analysis.get("signal", "WAIT")
            fresh = age is not None and age <= 12.0
            confirmed = bool(
                quote.get("ok")
                and signal in {"BUY", "SELL"}
                and fresh
                and source_check.get("ok")
            )
            plan = trade_plan(signal, quote, analysis) if confirmed else None

            if confirmed and signal == "BUY":
                decision, state = "شراء الآن", "ok"
            elif confirmed and signal == "SELL":
                decision, state = "بيع الآن", "ok"
            else:
                decision, state = "لا تدخل الآن", "wait"

            if plan:
                watch = fmt(plan["entry"])
            elif analysis.get("buy_score", 0) >= analysis.get("sell_score", 0):
                watch = "فوق " + fmt(analysis.get("buy_trigger"))
            else:
                watch = "تحت " + fmt(analysis.get("sell_trigger"))

            price = fmt(quote.get("last"))
            tp1 = fmt(plan.get("tp1")) if plan else "—"
            stop = fmt(plan.get("stop")) if plan else "—"
            strength = f"{int(analysis.get('strength', 0))}%"

            st.markdown(
                f"""
                <div class='grid'>
                  <div class='box'><div class='l'>وش أسوي؟</div><div class='v {state}'>{decision}</div></div>
                  <div class='box'><div class='l'>السعر الآن</div><div class='v'>{price}</div></div>
                  <div class='box'><div class='l'>الدخول / المراقبة</div><div class='v'>{watch}</div></div>
                  <div class='box'><div class='l'>خذ الربح عند</div><div class='v ok'>{tp1}</div></div>
                  <div class='box'><div class='l'>وقف الخسارة</div><div class='v bad'>{stop}</div></div>
                  <div class='box'><div class='l'>الجاهزية</div><div class='v'>{strength}</div></div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            age_text = "—" if age is None else f"{age:.1f} ث"
            st.caption(
                f"المصادر {source_check.get('count', 0)}/2 • {source_check.get('reason', '')} • عمر السعر {age_text}"
            )
            st.caption(str(analysis.get("reason") or ""))

            if plan:
                st.caption(f"الهدف الثاني: {fmt(plan['tp2'])}")
            elif not secondary.get("configured", False):
                st.info("أضف GOLDAPI_KEY لتفعيل تأكيد السعر من مصدرين.")
        except Exception as exc:
            st.error(f"تعذر تحديث القرار: {type(exc).__name__}")
            st.caption("تم منع الخطأ من إسقاط التطبيق وسيحاول التحديث تلقائيًا.")

    quick_panel()

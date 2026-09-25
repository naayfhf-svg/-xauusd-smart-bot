from __future__ import annotations

# redeploy-marker: no-deps-stable-2026-09-25-0400

import csv
import io
import threading
import math
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

VERSION = "7.4.1-data-diagnostics"
INSTRUMENTS = {
    "الذهب الفوري — XAU/USD": {
        "symbol": "XAU/USD",
        "label": "XAU/USD",
        "kind": "spot_gold",
    },
    "GLD — صندوق ذهب أمريكي (للبحث في سهم)": {
        "symbol": "GLD",
        "label": "GLD",
        "kind": "gold_etf",
    },
}
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
        return x > 0 and math.isfinite(x)
    except Exception:
        return False


def parse_time(value: Any) -> float | None:
    try:
        if isinstance(value, (int, float)) or str(value).replace('.', '', 1).isdigit():
            value = float(value)
            if value >= 100_000_000_000:
                value /= 1000.0
            return value if finite(value) else None
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def entry_gate(quote: dict, rows: list, clock: float) -> tuple[bool, str]:
    if not quote.get('ok'):
        return False, 'تعذر جلب السعر'
    stamp = quote.get('updated_at')
    if stamp is None or not 0 <= clock - stamp <= 5:
        return False, 'توقيت السعر غير مؤكد أو تجاوز 5 ثوانٍ'
    if not quote.get('market_open'):
        return False, 'فتح السوق غير مؤكد'
    if not rows or not 0 <= clock - (rows[-1]['ts'] + 300) <= 360:
        return False, 'الشموع غير حديثة'
    bid, ask = quote.get('bid'), quote.get('ask')
    if not finite(bid) or not finite(ask) or ask < bid:
        return False, 'سعر الشراء والبيع غير متاح أو غير صالح'
    if (ask - bid) / quote['last'] > 0.0005:
        return False, 'السبريد يتجاوز فلتر الدخول'
    return True, 'اجتازت البيانات فحوص الدخول'


def position_status(position: dict, quote: dict, clock: float) -> str:
    stamp = quote.get('updated_at')
    if not quote.get('ok') or stamp is None or not 0 <= clock - stamp <= 5:
        return 'المتابعة متوقفة: تحقق من السعر والصفقة لدى وسيطك'
    price = quote.get('bid') if position['side'] == 'شراء' else quote.get('ask')
    if not finite(price):
        return 'سعر الخروج غير متاح: راجع وسيطك'
    if (position['side'] == 'شراء' and price <= position['stop']) or (position['side'] == 'بيع' and price >= position['stop']):
        return 'تنبيه خروج: السعر وصل وقف الخسارة أو تجاوزه'
    if (position['side'] == 'شراء' and price >= position['target']) or (position['side'] == 'بيع' and price <= position['target']):
        return 'تنبيه خروج: السعر وصل الهدف أو تجاوزه'
    return 'متابعة: لم يصل السعر المرصود إلى الوقف أو الهدف'


# Simulation assumptions, not broker lot specifications or a live FX conversion.
PAPER_SAR_PER_USD = 3.75
PAPER_SLIPPAGE_USD = 0.10


def new_wallet() -> dict:
    return dict(initial=5000.0, balance=5000.0, position=None, trades=[], next_id=1)


def paper_quote_ready(q: dict, clock: float) -> bool:
    stamp = q.get('updated_at')
    return bool(q.get('ok') and stamp is not None and 0 <= clock - stamp <= 5
                and q.get('market_open') and finite(q.get('bid'))
                and finite(q.get('ask')) and q['ask'] >= q['bid'])


def paper_daily_loss(wallet: dict, clock: float) -> float:
    day = datetime.fromtimestamp(clock, timezone.utc).date().isoformat()
    return sum(max(0.0, -t['pnl_sar']) for t in wallet['trades'] if t['day'] == day)


def paper_open(wallet: dict, signal: str, plan: dict | None, quote: dict, clock: float) -> bool:
    if wallet['position'] or not plan or signal not in ('BUY', 'SELL'):
        return False
    if not paper_quote_ready(quote, clock) or paper_daily_loss(wallet, clock) >= 75:
        return False
    entry = quote['ask'] + PAPER_SLIPPAGE_USD if signal == 'BUY' else quote['bid'] - PAPER_SLIPPAGE_USD
    stop, target = plan['stop'], plan['tp1']
    if not all(finite(v) for v in (entry, stop, target)):
        return False
    if not (stop < entry < target if signal == 'BUY' else target < entry < stop):
        return False
    risk = min(25.0, 75.0 - paper_daily_loss(wallet, clock), max(0.0, wallet['balance']) * 0.005)
    # Fractional virtual ounces; no leverage and no claim of broker-executable size.
    ounces = min(risk / ((abs(entry - stop) + PAPER_SLIPPAGE_USD) * PAPER_SAR_PER_USD),
                 max(0.0, wallet['balance']) / (entry * PAPER_SAR_PER_USD))
    if not finite(ounces):
        return False
    wallet['position'] = dict(id=wallet['next_id'], side=signal, entry=entry, stop=stop,
                              target=target, ounces=ounces, opened_at=clock,
                              risk_sar=ounces * (abs(entry-stop)+PAPER_SLIPPAGE_USD) * PAPER_SAR_PER_USD)
    wallet['next_id'] += 1
    return True


def paper_exit_price(position: dict, q: dict) -> float:
    return q['bid'] - PAPER_SLIPPAGE_USD if position['side'] == 'BUY' else q['ask'] + PAPER_SLIPPAGE_USD


def paper_pnl(position: dict, price: float) -> float:
    direction = 1 if position['side'] == 'BUY' else -1
    return (price - position['entry']) * direction * position['ounces'] * PAPER_SAR_PER_USD


def paper_close(wallet: dict, q: dict, clock: float, manual: bool = False) -> str | None:
    position = wallet['position']
    if not position or not paper_quote_ready(q, clock):
        return None
    observed = q['bid'] if position['side'] == 'BUY' else q['ask']
    stop_hit = observed <= position['stop'] if position['side'] == 'BUY' else observed >= position['stop']
    target_hit = observed >= position['target'] if position['side'] == 'BUY' else observed <= position['target']
    if not (manual or stop_hit or target_hit):
        return None
    reason = 'وقف' if stop_hit else 'هدف' if target_hit else 'إغلاق يدوي'
    price = paper_exit_price(position, q)
    pnl = paper_pnl(position, price)
    wallet['balance'] += pnl
    wallet['trades'].append(dict(position, exit=price, pnl_sar=pnl, reason=reason,
                                  closed_at=clock, day=datetime.fromtimestamp(clock, timezone.utc).date().isoformat()))
    wallet['position'] = None
    return reason


def render_wallet(quote: dict, signal: str, plan: dict | None) -> None:
    if 'paper_wallet' not in st.session_state:
        st.session_state['paper_wallet'] = new_wallet()
    wallet = st.session_state['paper_wallet']
    clock = now_ts()
    closed = paper_close(wallet, quote, clock)
    st.subheader('محفظتي التجريبية — بدون أموال حقيقية')
    if closed:
        st.info(f'أُغلقت الصفقة الافتراضية: {closed}')
    position = wallet['position']
    unrealized = paper_pnl(position, paper_exit_price(position, quote)) if position and paper_quote_ready(quote, clock) else None
    a, b, c = st.columns(3)
    a.metric('الرصيد الافتراضي', f"{wallet['balance']:,.2f} ر.س")
    b.metric('النتيجة المحققة', f"{wallet['balance'] - wallet['initial']:+,.2f} ر.س")
    c.metric('صفقات مغلقة', len(wallet['trades']))
    if position:
        side = 'شراء' if position['side'] == 'BUY' else 'بيع'
        st.write(f"{side} • الدخول {fmt(position['entry'])} • الوقف {fmt(position['stop'])} • الهدف {fmt(position['target'])}")
        st.caption(f"الكمية {position['ounces']:.4f} أونصة افتراضية • الخسارة المخططة {position['risk_sar']:.2f} ريال؛ قد تتجاوزها القفزات")
        if unrealized is None:
            st.warning('تقييم الصفقة متوقف: ننتظر سعر شراء وبيع حديثًا. لا نستخدم سعرًا قديمًا للإغلاق.')
        else:
            st.metric('النتيجة العائمة التقديرية', f'{unrealized:+,.2f} ر.س')
        if st.button('إغلاق صفقتي الافتراضية', disabled=not paper_quote_ready(quote, clock)):
            paper_close(wallet, quote, now_ts(), manual=True)
            st.rerun()
    else:
        blocked = paper_daily_loss(wallet, clock) >= 75
        if blocked:
            st.warning('توقف التجربة لبقية اليوم UTC: بلغت الخسائر المحققة 75 ريالًا أو أكثر.')
        if st.button('جرّب الإشارة بمحفظتي الافتراضية', disabled=not plan or blocked):
            if paper_open(wallet, signal, plan, quote, now_ts()):
                st.rerun()
            else:
                st.warning('لم تُفتح الصفقة: الإشارة أو السعر لم يعد صالحًا.')
        if not plan:
            st.caption('انتظار إشارة مستوفية للشروط؛ ما تحتاج تكتب سعرًا أو تحول فلوس.')
    with st.expander('سجل التجربة وطريقة الحساب'):
        st.caption('رصيد البداية 5,000 ريال افتراضي. حد المخاطرة المخططة 25 ريالًا للصفقة، وتوقف بعد 75 ريالًا من خسائر اليوم. ليست توصية بإيداع حقيقي.')
        st.caption('محاكاة كسور أونصة دون رافعة: 3.75 ريال للدولار كافتراض حسابي، مع فرق الشراء والبيع وانزلاق افتراضي 0.10 دولار للأونصة لكل تنفيذ. لا تشمل عمولة أو تمويل وسيطك، وليست اختبار ربحية تاريخيًا.')
        st.caption('المتابعة أثناء فتح الجلسة فقط، وقد تفوت حركة بين تحديثين. الرصيد والسجل مؤقتان وقد يضيعان عند إعادة تحميل الصفحة أو انقطاع الجلسة. نزّل السجل قبل المغادرة.')
        if wallet['trades']:
            rows = [{'رقم': t['id'], 'الاتجاه': t['side'], 'الدخول': t['entry'], 'الخروج': t['exit'], 'النتيجة بالريال': round(t['pnl_sar'],2), 'السبب': t['reason'], 'التاريخ UTC': t['day']} for t in wallet['trades']]
            st.dataframe(rows, hide_index=True)
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
            st.download_button('تنزيل سجل الصفقات CSV', buffer.getvalue().encode('utf-8-sig'), 'gold-paper-trades.csv', 'text/csv')


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
def fetch_quote(symbol: str) -> dict[str, Any]:
    key = str(secret("TWELVE_DATA_API_KEY", "") or "").strip()
    if not key:
        return {"ok": False, "error": "TWELVE_DATA_API_KEY غير موجود"}

    started = time.perf_counter()
    try:
        status, payload = http_json(
            QUOTE_URL,
            {
                "symbol": symbol,
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
        "updated_at": parse_time(payload.get("last_update_at")),
        "market_open": payload.get("is_market_open") is True,
        "latency_ms": (time.perf_counter() - started) * 1000.0,
        "source": "Twelve Data",
    }


@st.cache_data(ttl=55, show_spinner=False)
def fetch_history(symbol: str) -> list[dict[str, Any]]:
    key = str(secret("TWELVE_DATA_API_KEY", "") or "").strip()
    if not key:
        return []

    try:
        status, payload = http_json(
            HISTORY_URL,
            {
                "symbol": symbol,
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
            if not all(finite(item[k]) for k in ("open", "high", "low", "close")):
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
        "updated_at": parse_time(payload.get("timestamp")) if isinstance(payload, dict) else None,
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
        expected = seconds // 300
        if len(group) != expected or any(r["ts"] != key + i * 300 for i, r in enumerate(group)):
            continue
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

    h1_trend, m15_trend = trend(h1), trend(m15)
    buy_checks = [
        h1_trend == "UP",
        m15_trend == "UP",
        current["close"] > e20 > e50,
        rsi is not None and 50 <= rsi <= 68,
        current["close"] > buy_trigger,
    ]
    sell_checks = [
        h1_trend == "DOWN",
        m15_trend == "DOWN",
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
    if (secondary.get("ok") and finite(secondary.get("price"))
            and secondary.get("updated_at") is not None
            and 0 <= now_ts() - secondary["updated_at"] <= 5):
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


class GoldStream:
    """One shared stream per API credential; idle leases stop unused connections."""
    def __init__(self, key: str):
        self.key = key
        self.lock = threading.Lock()
        self.worker = None
        self.touched = time.monotonic()
        self.latest = None
        self.status = 'بانتظار الاتصال'

    def snapshot(self):
        with self.lock:
            self.touched = time.monotonic()
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self.run, daemon=True)
                self.worker.start()
            return dict(self.latest) if self.latest else None, self.status

    def accept(self, event):
        if not isinstance(event, dict):
            return
        if event.get('event') != 'price' or event.get('symbol') != 'XAU/USD':
            return
        stamp = parse_time(event.get('timestamp'))
        if stamp is None or not finite(event.get('price')) or stamp > now_ts() + 1:
            return
        with self.lock:
            if self.latest and stamp < self.latest['timestamp']:
                return
            self.latest = dict(price=float(event['price']), timestamp=stamp, received=now_ts())
            self.status = 'متصل'

    def run(self):
        try:
            from websockets.sync.client import connect
        except ImportError:
            with self.lock:
                self.status = 'مكتبة البث غير متاحة في بيئة التشغيل'
            return
        delay = 2
        while time.monotonic() - self.touched < 90:
            try:
                with connect('wss://ws.twelvedata.com/v1/quotes/price?' + urlencode({'apikey': self.key}),
                             open_timeout=8, close_timeout=2, max_size=65536) as ws:
                    ws.send(json.dumps({'action': 'subscribe', 'params': {'symbols': 'XAU/USD'}}))
                    with self.lock:
                        self.status = 'متصل — ننتظر تحديث الذهب من المصدر'
                    beat = time.monotonic()
                    last_price_received = time.monotonic()
                    while time.monotonic() - self.touched < 90:
                        if time.monotonic() - last_price_received >= 30:
                            with self.lock:
                                self.status = 'لا توجد تحديثات ذهب منذ 30 ثانية — نحاول إعادة الاتصال'
                            break
                        if time.monotonic() - beat >= 10:
                            ws.send(json.dumps({'action': 'heartbeat'}))
                            beat = time.monotonic()
                        try:
                            event = json.loads(ws.recv(timeout=2))
                        except TimeoutError:
                            continue
                        if isinstance(event, dict) and (event.get('event') == 'error' or
                            (event.get('event') == 'subscribe-status' and event.get('status') != 'ok')):
                            with self.lock:
                                self.status = 'لم يقبل المصدر الاشتراك؛ تحقق من صلاحية بث XAU/USD'
                            time.sleep(30)
                            break
                        self.accept(event)
                        if isinstance(event, dict) and event.get('event') == 'price' and event.get('symbol') == 'XAU/USD':
                            last_price_received = time.monotonic()
                        delay = 2
            except Exception:
                # Never expose exception text: a URL can contain the API key.
                with self.lock:
                    self.status = 'انقطع البث — إعادة اتصال تدريجية'
            time.sleep(delay)
            delay = min(30, delay * 2)
        with self.lock:
            self.status = 'متوقف لعدم وجود متابعة'


@st.cache_resource(show_spinner=False)
def gold_stream(key: str):
    return GoldStream(key)


@st.fragment(run_every=0.5)
def live_gold_panel():
    key = str(secret('TWELVE_DATA_API_KEY', '') or '').strip()
    if not key:
        return
    tick, status = gold_stream(key).snapshot()
    st.caption('بث XAU/USD من Twelve Data — سعر مرجعي، وليس سعر تنفيذ وسيطك')
    if tick:
        age = now_ts() - tick['timestamp']
        received_age = now_ts() - tick['received']
        st.caption(f'حالة البث: {status} • عمر سعر المصدر {age:.1f} ث • منذ وصول الرسالة {received_age:.1f} ث')
        st.metric('آخر سعر ذهب من البث — دولار / أونصة', fmt(tick['price']))
        if 0 <= age <= 5:
            st.caption(f'عمر تحديث المصدر {age:.1f} ثانية • {status}')
        else:
            st.warning('السعر المعروض قديم — لا تعتمد عليه للدخول')
    else:
        st.info(status)
    st.caption('فحص العرض كل نصف ثانية؛ وصول حركة جديدة يعتمد على المصدر والاشتراك والشبكة. لا نضمن تأخيرًا أقل من ثانية.')


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

selected_name = st.selectbox(
    "الأداة",
    list(INSTRUMENTS.keys()),
    index=0,
)
instrument = INSTRUMENTS[selected_name]
ACTIVE_SYMBOL = instrument["symbol"]
ACTIVE_LABEL = instrument["label"]
ACTIVE_KIND = instrument["kind"]

st.markdown(
    f"<div class='hero'><h1>🟡 {ACTIVE_LABEL}</h1><p>قرار مضاربة مختصر • v{VERSION}</p></div>",
    unsafe_allow_html=True,
)

if ACTIVE_KIND == "gold_etf":
    st.caption(
        "GLD صندوق أمريكي يتتبع الذهب. تأكد من ظهوره وقابليته للتداول داخل حسابك في سهم قبل أي تنفيذ."
    )

if not str(secret("TWELVE_DATA_API_KEY", "") or "").strip():
    st.error("أضف TWELVE_DATA_API_KEY في Secrets لتشغيل الأسعار.")
else:
    if ACTIVE_KIND == "spot_gold":
        live_gold_panel()

    @st.fragment(run_every=3)
    def quick_panel() -> None:
        try:
            clock = now_ts()
            history = [r for r in fetch_history(ACTIVE_SYMBOL) if r["ts"] + 300 <= clock]
            quote = fetch_quote(ACTIVE_SYMBOL)
            secondary = fetch_goldapi() if ACTIVE_KIND == "spot_gold" else {"ok": False, "configured": False}
            source_check = consensus(quote, secondary)
            analysis_started = time.perf_counter()
            analysis = analyze(history) if history else {"signal": "WAIT", "strength": 0, "reason": "البيانات غير جاهزة"}

            analysis_ms = (time.perf_counter() - analysis_started) * 1000
            age = None
            if quote.get("updated_at"):
                age = now_ts() - float(quote["updated_at"])

            signal = analysis.get("signal", "WAIT")
            fresh, gate_reason = entry_gate(quote, history, now_ts())
            confirmed = bool(
                quote.get("ok")
                and signal in {"BUY", "SELL"}
                and fresh
                and source_check.get("ok")
            )
            if ACTIVE_KIND == "gold_etf":
                confirmed = False
            plan = trade_plan(signal, quote, analysis) if confirmed else None

            data_ready = fresh and source_check.get('ok') and ACTIVE_KIND == 'spot_gold'
            if not data_ready:
                decision, state = "البيانات غير جاهزة", "bad"
            elif confirmed and signal == "BUY":
                decision, state = "فرصة شراء تجريبية", "ok"
            elif confirmed and signal == "SELL":
                decision, state = "فرصة بيع تجريبية", "ok"
            else:
                decision, state = "لا تدخل الآن", "wait"

            if plan:
                watch = fmt(plan["entry"])
            elif analysis.get("buy_score", 0) >= analysis.get("sell_score", 0):
                watch = "فوق " + fmt(analysis.get("buy_trigger"))
            else:
                watch = "تحت " + fmt(analysis.get("sell_trigger"))
            if not data_ready:
                watch = "موقوف حتى اكتمال البيانات"

            price = fmt(quote.get("last"))
            tp1 = fmt(plan.get("tp1")) if plan else "—"
            stop = fmt(plan.get("stop")) if plan else "—"
            strength = f"{int(analysis.get('strength', 0))}%"

            st.markdown(
                f"""
                <div class='grid'>
                  <div class='box'><div class='l'>وش أسوي؟</div><div class='v {state}'>{decision}</div></div>
                  <div class='box'><div class='l'>آخر سعر REST — راجع حداثته</div><div class='v'>{price}</div></div>
                  <div class='box'><div class='l'>الدخول / المراقبة</div><div class='v'>{watch}</div></div>
                  <div class='box'><div class='l'>خذ الربح عند</div><div class='v ok'>{tp1}</div></div>
                  <div class='box'><div class='l'>وقف الخسارة</div><div class='v bad'>{stop}</div></div>
                  <div class='box'><div class='l'>اكتمال الشروط — ليس احتمال ربح</div><div class='v'>{strength}</div></div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            age_text = "—" if age is None else f"{age:.1f} ث"
            if ACTIVE_KIND == "spot_gold":
                st.caption(
                    f"المصادر {source_check.get('count', 0)}/2 • {source_check.get('reason', '')} • عمر السعر {age_text}"
                )
            else:
                st.caption(
                    f"GLD • مصدر السعر الحالي Twelve Data • عمر السعر {age_text} • مراقبة فقط حتى نربط مصدرًا ثانيًا أو سعر الوسيط"
                )
            if not data_ready:
                st.error('تعذر تقييم الدخول حاليًا بسبب البيانات؛ هذه ليست حالة انتظار فرصة سوقية.')
            with st.expander('تشخيص البيانات — سبب توقف الإشارات'):
                issues = []
                if not quote.get('ok'):
                    issues.append('تعذر جلب السعر من المصدر الرئيسي')
                if age is None:
                    issues.append('المصدر الرئيسي لم يوفر توقيت تحديث سعر قابلًا للتحقق')
                elif age < 0 or age > 5:
                    issues.append(f'عمر سعر المصدر الرئيسي {age:.1f} ثانية؛ الحد 5 ثوانٍ')
                if not finite(quote.get('bid')) or not finite(quote.get('ask')):
                    issues.append('المصدر الرئيسي لا يوفر حاليًا Bid/Ask؛ البث المرجعي وحده لا يعوض سعر التنفيذ')
                if not quote.get('market_open'):
                    issues.append('حالة فتح السوق غير مؤكدة من المزود')
                if ACTIVE_KIND == 'spot_gold' and not secondary.get('configured'):
                    issues.append('GOLDAPI_KEY غير مضبوط؛ لا يوجد تأكيد من مصدر ثانٍ')
                elif ACTIVE_KIND == 'spot_gold' and not source_check.get('ok'):
                    issues.append('تأكيد المصدر الثاني غير صالح: قد يكون متأخرًا أو مختلفًا أو غير متاح')
                if not history:
                    issues.append('بيانات الشموع غير متاحة')
                for issue in issues:
                    st.write('• ' + issue)
                if not issues:
                    st.write(gate_reason)
                st.caption('تغيير سرعة العرض لا يسرّع المزود. لا نعتبر وقت وصول الرد بديلًا عن وقت السعر، ولا نختلق Bid/Ask.')
                report = dict(version=VERSION, instrument=ACTIVE_SYMBOL,
                              checked_at=datetime.now(timezone.utc).isoformat(),
                              rest_ok=bool(quote.get('ok')), rest_source_age_seconds=age,
                              bid_available=finite(quote.get('bid')), ask_available=finite(quote.get('ask')),
                              market_open=quote.get('market_open'), history_count=len(history),
                              second_source_configured=bool(secondary.get('configured')),
                              source_confirmation=bool(source_check.get('ok')), entry_gate=gate_reason,
                              issues=issues)
                st.download_button('تنزيل تقرير التشخيص بدون مفاتيح',
                                   json.dumps(report, ensure_ascii=False, indent=2),
                                   'gold-diagnostics.json', 'application/json')
            st.caption(gate_reason)
            st.caption(str(analysis.get("reason") or ""))
            st.caption("تحديث اللوحة كل 3 ثوانٍ؛ سرعة المصدر والخطة تحددان وصول السعر. لا تنفيذ آلي ولا ضمان ربح.")
            st.caption(f'زمن الحساب المحلي {analysis_ms:.1f} مللي ثانية — لا يشمل وصول بيانات السوق')
            if ACTIVE_KIND == 'spot_gold':
                render_wallet(quote, signal, plan)
            position_key = 'manual_position_' + ACTIVE_SYMBOL
            position = st.session_state.get(position_key)
            if position:
                st.subheader('صفقتي المسجلة')
                st.write(f"{position['side']} • الدخول {fmt(position['entry'])} • الوقف {fmt(position['stop'])} • الهدف {fmt(position['target'])}")
                st.warning(position_status(position, quote, now_ts()))
                if st.button('إنهاء المتابعة — أغلقت الصفقة عند الوسيط'):
                    st.session_state.pop(position_key, None)
                    st.rerun()
            else:
                with st.expander('متقدم: متابعة صفقة موجودة عند الوسيط'):
                    unit_text = "للأونصة" if ACTIVE_KIND == "spot_gold" else "للسهم"
                    st.caption(f'تسجيل ومتابعة فقط، لا يرسل أمرًا للوسيط. القيم بالدولار {unit_text}.')
                    with st.form('manual_trade'):
                        side = st.selectbox('الاتجاه', ['شراء', 'بيع'])
                        entry = st.number_input('سعر التنفيذ بالدولار — ليس رأس المال', min_value=0.0, value=0.0)
                        stop_value = st.number_input('وقف الخسارة عند الوسيط', min_value=0.0, value=0.0)
                        target = st.number_input('هدف الربح', min_value=0.0, value=0.0)
                        if st.form_submit_button('بدء المتابعة'):
                            valid = all(finite(v) for v in (entry, stop_value, target))
                            valid = valid and (stop_value < entry < target if side == 'شراء' else target < entry < stop_value)
                            if not valid:
                                st.error('تحقق من الأسعار وترتيب الوقف والدخول والهدف وفق الاتجاه.')
                            else:
                                st.session_state[position_key] = dict(side=side, entry=entry, stop=stop_value, target=target)
                                st.rerun()
            st.caption('المتابعة أثناء فتح هذه الجلسة فقط؛ لا توجد تنبيهات خلفية. القفزات بين التحديثات قد تفوتنا، والوقف يجب وضعه لدى الوسيط.')

            if plan:
                st.caption(f"الهدف الثاني: {fmt(plan['tp2'])}")
            elif ACTIVE_KIND == "spot_gold" and not secondary.get("configured", False):
                st.info("أضف GOLDAPI_KEY لتفعيل تأكيد السعر من مصدرين.")
            elif ACTIVE_KIND == "gold_etf":
                st.info("تم إدراج GLD للتحليل والمراقبة. لن يظهر دخول مؤكد حتى نربط مصدر سعر مستقل أو سعر وسيط.")
        except Exception as exc:
            st.error(f"تعذر تحديث القرار: {type(exc).__name__}")
            st.caption("تم منع الخطأ من إسقاط التطبيق وسيحاول التحديث تلقائيًا.")

    quick_panel()



"""Local, demo-only MT5 reader. No order API and no stored broker passwords."""
from __future__ import annotations

import math
import threading
import time

_LOCK = threading.Lock()


class ReadError(Exception):
    pass


def positive(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ReadError("قيمة سعر أو توقيت غير صالحة")
    return value


def normalize_tick(tick, clock):
    if tick is None:
        raise ReadError("لا توجد قراءة سعر")
    bid, ask = positive(tick.bid), positive(tick.ask)
    stamp = positive(tick.time_msc) / 1000
    if ask < bid:
        raise ReadError("سعر الطلب أقل من العرض")
    age = clock - stamp
    if not 0 <= age <= 5:
        raise ReadError("السعر قديم أو توقيته في المستقبل؛ أوقفنا القراءة المعتمدة")
    return dict(bid=bid, ask=ask, updated_at=stamp, age=age,
                spread=ask-bid, midpoint=(bid+ask)/2)


def normalize_bars(rates, clock):
    if rates is None or len(rates) == 0:
        raise ReadError("الشموع غير متاحة")
    rows = []
    for r in rates:
        stamp = positive(r["time"])
        if stamp + 300 > clock:
            continue
        o, h, low, c = [positive(r[k]) for k in ("open", "high", "low", "close")]
        if not low <= min(o, c) <= max(o, c) <= h:
            raise ReadError("شمعة غير صالحة")
        if rows and stamp <= rows[-1]["ts"]:
            raise ReadError("ترتيب الشموع غير صالح")
        rows.append(dict(ts=stamp, open=o, high=h, low=low, close=c))
    if len(rows) < 120 or not 0 <= clock-(rows[-1]["ts"]+300) <= 360:
        raise ReadError("نحتاج 120 شمعة مغلقة وحديثة على إطار خمس دقائق")
    return rows


def read_snapshot(api, expected_login, expected_server, symbol="GOLD", clock=None):
    """Use the terminal's existing demo session, never log in or send orders."""
    if not str(expected_login).isdigit() or not expected_server.strip():
        raise ReadError("حدد رقم الحساب التجريبي والخادم المتوقعين")
    if not symbol.strip() or len(symbol) > 64:
        raise ReadError("رمز غير صالح")
    with _LOCK:
        try:
            if not api.initialize(timeout=10000):
                raise ReadError("تعذر الاتصال ببرنامج MT5 المحلي")
            terminal = api.terminal_info()
            account = api.account_info()
            if terminal is None or not terminal.connected or account is None:
                raise ReadError("برنامج MT5 غير متصل")
            if account.login != int(expected_login) or account.server != expected_server:
                raise ReadError("الحساب أو الخادم لا يطابق إعدادات التجربة")
            if account.trade_mode != api.ACCOUNT_TRADE_MODE_DEMO:
                raise ReadError("هذه النسخة تقبل الحساب التجريبي فقط")
            info = api.symbol_info(symbol)
            if info is None or not info.visible:
                raise ReadError("أضف رمز الذهب إلى قائمة الأسعار في MT5 أولاً")
            if info.currency_base != "XAU" or info.currency_profit != "USD":
                raise ReadError("الرمز ليس ذهباً مقابل الدولار حسب مواصفات الوسيط")
            tick = api.symbol_info_tick(symbol)
            rates = api.copy_rates_from_pos(symbol, api.TIMEFRAME_M5, 1, 1200)
            check_time = time.time() if clock is None else clock
            quote = normalize_tick(tick, check_time)
            bars = normalize_bars(rates, check_time)
            # Detect account switching during the read.
            after = api.account_info()
            if (after is None or after.login != account.login
                    or after.server != account.server
                    or after.trade_mode != api.ACCOUNT_TRADE_MODE_DEMO):
                raise ReadError("تغير الحساب أثناء القراءة")
            return dict(quote=quote, bars=bars, symbol=symbol,
                        source="MT5 demo", execution_enabled=False)
        finally:
            api.shutdown()


def render_mt5_panel(st, config):
    st.subheader("اختبار ربط XM / MT5 — قراءة فقط")
    st.caption("لا أوامر ولا إشارات دخول هنا. يتطلب تشغيل هذه الصفحة على ويندوز بجوار MT5 وحساب تجريبي.")
    if str(config("MT5_READ_ONLY_ENABLED", "")).lower() not in ("1", "true"):
        st.info("الاختبار غير مفعّل. راجع docs/mt5-read-only.md؛ لا تضف كلمة مرور الوسيط إلى الموقع.")
        return
    import sys
    if sys.platform != "win32":
        st.warning("هذا الموصل محلي لويندوز؛ لا يعمل داخل Streamlit Cloud ولا داخل تطبيق الآيفون.")
        return
    if st.button("فحص اتصال الحساب التجريبي وجلب الذهب", key="mt5_read_only_check"):
        try:
            import MetaTrader5 as mt5
            result = read_snapshot(
                mt5, str(config("MT5_EXPECTED_LOGIN", "")),
                str(config("MT5_EXPECTED_SERVER", "")),
                str(config("MT5_GOLD_SYMBOL", "GOLD")),
            )
            q = result["quote"]
            st.success("نجحت القراءة وقت الفحص — التنفيذ معطّل")
            st.metric("Bid", f'{q["bid"]:.3f}')
            st.metric("Ask", f'{q["ask"]:.3f}')
            st.caption(f'عمر السعر عند الفحص: {q["age"]:.2f} ثانية؛ هذه لقطة وليست بثاً مستمراً.')
            st.dataframe(result["bars"][-20:])
        except ReadError as exc:
            st.error(str(exc))
        except ImportError:
            st.error("ثبّت requirements-mt5.txt على ويندوز")
        except Exception:
            st.error("فشل الفحص. لم تُرسل أي أوامر. راجع اتصال MT5 وإعدادات الحساب التجريبي.")

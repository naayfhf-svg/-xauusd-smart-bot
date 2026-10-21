"""Demo analysis and session-only paper tracking. Never places broker orders."""
from __future__ import annotations

import math
import time

from mt5_readonly import ReadError, read_snapshot


def prepare(snapshot, analyzer, planner, clock):
    q = snapshot["quote"]
    age = clock - q["updated_at"]
    if not 0 <= age <= 5:
        raise ReadError("السعر غير حديث؛ انتظار تحديث جديد")
    quote = dict(ok=True, last=q["midpoint"], bid=q["bid"], ask=q["ask"],
                 updated_at=q["updated_at"])
    bars = snapshot["bars"]
    if not bars or not 0 <= clock-(bars[-1]["ts"]+300) <= 360:
        raise ReadError("الشموع غير حديثة")
    analysis = analyzer(bars)
    signal = analysis.get("signal", "WAIT")
    spread_ok = (q["ask"]-q["bid"])/q["midpoint"] <= 0.0005
    plan = planner(signal, quote, analysis) if spread_ok else None
    if plan:
        prices = [float(plan[k]) for k in ("entry", "stop", "tp1")]
        if not all(math.isfinite(v) and v > 0 for v in prices):
            plan = None
        elif signal == "BUY" and not prices[1] < prices[0] < prices[2]:
            plan = None
        elif signal == "SELL" and not prices[2] < prices[0] < prices[1]:
            plan = None
    return dict(quote=quote, analysis=analysis, plan=plan,
                signal=signal, spread_ok=spread_ok,
                signal_id=f'{snapshot["symbol"]}:{bars[-1]["ts"]}:{signal}')


def open_virtual(state, decision, clock):
    q, plan = decision["quote"], decision["plan"]
    if (state.get("position") or plan is None
            or decision["signal"] not in ("BUY", "SELL")
            or not decision["spread_ok"]
            or state.get("last_signal_id") == decision["signal_id"]
            or not 0 <= clock-q["updated_at"] <= 5):
        return False
    state["position"] = dict(side=decision["signal"], entry=plan["entry"],
                             stop=plan["stop"], target=plan["tp1"], opened_at=clock)
    state["last_signal_id"] = decision["signal_id"]
    return True


def close_virtual(state, quote, clock, manual=False):
    pos = state.get("position")
    if not pos or not 0 <= clock-quote["updated_at"] <= 5:
        return None
    buy = pos["side"] == "BUY"
    exit_price = quote["bid"] if buy else quote["ask"]
    stop = exit_price <= pos["stop"] if buy else exit_price >= pos["stop"]
    target = exit_price >= pos["target"] if buy else exit_price <= pos["target"]
    if not (manual or stop or target):
        return None
    # Observed executable side, not an assumed fill at the target or stop.
    trade = dict(pos, exit=exit_price, closed_at=clock,
                 reason="وقف افتراضي" if stop else "هدف افتراضي" if target else "إغلاق يدوي افتراضي",
                 change_per_ounce=(exit_price-pos["entry"])*(1 if buy else -1))
    state.setdefault("trades", []).append(trade)
    state["position"] = None
    return trade


def render(st, config, analyzer, planner):
    st.info("XM — تحليل وتجربة افتراضية فقط")
    st.caption("يستخدم سعر حساب تجريبي واحد. لا يرسل صفقات إلى XM، ولا يضمن نتائج أو توقيت تنفيذ.")
    import sys
    if sys.platform != "win32":
        st.warning("شغّل هذه النسخة على ويندوز بجوار MT5. الآيفون يعرض جلسة ويندوز السحابي فقط.")
        return
    if str(config("MT5_READ_ONLY_ENABLED", "")).lower() not in ("true", "1"):
        st.info("ابدأ عبر start-mt5-demo.cmd على ويندوز لتفعيل تجربة القراءة.")
        return
    with st.form("mt5_demo_config"):
        login = st.text_input("رقم حساب XM التجريبي", value=str(config("MT5_EXPECTED_LOGIN", "")))
        server = st.text_input("خادم الحساب التجريبي", value=str(config("MT5_EXPECTED_SERVER", "")))
        symbol = st.text_input("رمز الذهب لدى الوسيط", value=str(config("MT5_GOLD_SYMBOL", "GOLD")))
        start = st.form_submit_button("بدء تحليل الحساب التجريبي")
    st.caption("سجّل الدخول في برنامج MT5 على ويندوز. لا نطلب كلمة المرور هنا. سيُرفض الحساب الحقيقي.")
    if start:
        settings = (login.strip(), server.strip(), symbol.strip())
        old = st.session_state.get("mt5_lab_config")
        if old != settings:
            if st.session_state.get("mt5_virtual", {}).get("position"):
                st.error("أغلق المتابعة الافتراضية الحالية قبل تغيير إعدادات الحساب.")
                return
            st.session_state["mt5_virtual"] = dict(position=None, trades=[])
        st.session_state["mt5_lab_config"] = settings
    if "mt5_lab_config" not in st.session_state:
        return

    @st.fragment(run_every=3)
    def live():
        state = st.session_state.setdefault("mt5_virtual", dict(position=None, trades=[]))
        try:
            import MetaTrader5 as mt5
            login, server, symbol = st.session_state["mt5_lab_config"]
            snapshot = read_snapshot(mt5, login, server, symbol)
            decision = prepare(snapshot, analyzer, planner, time.time())
        except ImportError:
            st.error("مكتبة MT5 غير مثبتة؛ شغّل start-mt5-demo.cmd.")
            return
        except ReadError as exc:
            st.error(str(exc))
            if state.get("position"):
                st.warning("متابعة الصفقة الافتراضية متوقفة حتى عودة بيانات حديثة.")
            return
        except Exception:
            st.error("تعذر تحديث التحليل. تحقق من اتصال MT5؛ لا توجد أوامر مرسلة.")
            return
        q = decision["quote"]
        st.caption(f'آخر قراءة مصدر UTC: {time.strftime("%H:%M:%S", time.gmtime(q["updated_at"]))} • الرمز {symbol}')
        left, right = st.columns(2)
        left.metric("Bid", f'{q["bid"]:.3f}')
        right.metric("Ask", f'{q["ask"]:.3f}')
        st.caption(f'عمر السعر {time.time()-q["updated_at"]:.1f} ثانية • السبريد {q["ask"]-q["bid"]:.3f}')
        close_virtual(state, q, time.time())
        a, plan = decision["analysis"], decision["plan"]
        label = {"BUY": "شراء افتراضي", "SELL": "بيع افتراضي", "WAIT": "انتظار"}
        st.subheader(label.get(decision["signal"], "انتظار") if plan else "انتظار")
        st.write(a.get("reason", "الشروط غير مكتملة"))
        if not decision["spread_ok"]:
            st.warning("السبريد مرتفع؛ تسجيل دخول افتراضي جديد معطّل.")
        if a.get("checks"):
            st.write(a["checks"])
        if plan:
            st.write({"دخول افتراضي": plan["entry"], "وقف افتراضي": plan["stop"], "هدف افتراضي": plan["tp1"]})
        if st.button("تسجيل دخول افتراضي", key="mt5_virtual_open",
                     disabled=plan is None or bool(state.get("position"))):
            if open_virtual(state, decision, time.time()):
                st.success("بدأت المتابعة الافتراضية داخل الجلسة.")
            else:
                st.info("تعذر فتح متابعة جديدة أو سبق تسجيل هذه الإشارة.")
        if state.get("position"):
            st.write("المتابعة الافتراضية الحالية")
            st.write(state["position"])
            if st.button("إغلاق المتابعة افتراضيًا", key="mt5_virtual_close"):
                close_virtual(state, q, time.time(), manual=True)
        if state["trades"]:
            st.dataframe(state["trades"])
            import json
            st.download_button("تنزيل سجل التجربة", json.dumps(state["trades"], ensure_ascii=False, indent=2),
                               file_name="mt5-demo-trades.json", mime="application/json")
        st.caption("السجل مؤقت لهذه الجلسة. التغير لكل أونصة قبل العمولات والانزلاق؛ ليس ربح حسابك. المتابعة تعمل أثناء فتح الصفحة وقد تفوتها حركات بين التحديثات.")
    live()

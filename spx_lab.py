"""SPX research only: indicative options and uploaded historical index bars.

No order endpoints, live recommendations, synthetic index or estimated Greeks.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import csv
import io
import math
import re
import time
import hashlib

import streamlit as st

NY = ZoneInfo('America/New_York')


def number(value):
    try:
        v = float(value)
        return v if math.isfinite(v) and v >= 0 else None
    except (TypeError, ValueError):
        return None


def contract_info(symbol):
    match = re.fullmatch(r'(SPXW|SPX)(\d{6})([CP])(\d{8})', symbol)
    if not match:
        raise ValueError('رمز العقد ليس SPX أو SPXW صالحًا')
    root, expiry, side, strike = match.groups()
    try:
        day = datetime.strptime(expiry, '%y%m%d').date()
    except ValueError:
        raise ValueError('تاريخ انتهاء العقد غير صالح') from None
    return dict(symbol=symbol, root=root, expiry=day.isoformat(),
                side='Call' if side == 'C' else 'Put', strike=int(strike)/1000,
                settlement='PM' if root == 'SPXW' else 'AM')


def quote_view(symbol, snapshot, clock):
    result = contract_info(symbol)
    q = snapshot.get('latestQuote') or {}
    bid, ask = number(q.get('bp')), number(q.get('ap'))
    age = None
    try:
        stamp = datetime.fromisoformat(q['t'].replace('Z', '+00:00'))
        if stamp.tzinfo is not None:
            age = clock - stamp.timestamp()
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    valid = bid is not None and ask is not None and ask > 0 and bid <= ask
    result.update(bid=bid if valid else None, ask=ask if valid else None,
                  spread=ask-bid if valid else None, age_seconds=age,
                  quote_time=q.get('t'), feed='indicative', live_entry_allowed=False)
    return result


def long_option_cost(premium, quantity, fee):
    premium, fee = number(premium), number(fee)
    if premium is None or premium <= 0 or fee is None:
        raise ValueError('السعر يجب أن يكون موجبًا والرسوم غير سالبة')
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise ValueError('عدد العقود يجب أن يكون عددًا صحيحًا موجبًا')
    debit = premium * 100 * quantity
    return dict(premium_cost=debit, entry_fees=fee*quantity,
                maximum_loss=debit+fee*quantity)


@st.cache_data(ttl=60, show_spinner=False)
def load_chain(identity, expiry, _request, _credentials):
    """Only read indicative snapshots, capped at three pages, no paid fallback."""
    key, secret = _credentials
    if not key or not secret:
        return dict(rows=[], error='مفاتيح Alpaca غير موجودة', partial=False)
    params = dict(feed='indicative', expiration_date=expiry, limit=1000)
    snapshots = {}
    partial = False
    try:
        for _ in range(3):
            status, payload = _request(
                'https://data.alpaca.markets/v1beta1/options/snapshots/SPX',
                params=params.copy(), headers={'APCA-API-KEY-ID': key,
                                              'APCA-API-SECRET-KEY': secret}, timeout=6)
            if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get('snapshots'), dict):
                return dict(rows=[], error='المزود لم يُتح بيانات SPX الإرشادية لهذا الحساب أو لهذا الطلب', partial=False)
            snapshots.update(payload['snapshots'])
            token = payload.get('next_page_token')
            partial = bool(token)
            if not token:
                break
            params['page_token'] = token
    except Exception:
        # Never surface raw exceptions, headers or credentials.
        return dict(rows=[], error='تعذر الاتصال ببيانات الخيارات؛ لا تغيير للاشتراك', partial=False)
    rows = []
    for symbol, snapshot in snapshots.items():
        try:
            row = quote_view(symbol, snapshot, time.time())
            if row['expiry'] == expiry:
                rows.append(row)
        except (ValueError, TypeError, AttributeError):
            continue
    return dict(rows=sorted(rows, key=lambda r: (r['strike'], r['side'], r['root'])),
                error=None, partial=partial, fetched_at=datetime.now(timezone.utc).isoformat())


def read_bars(text, clock):
    """Strict SPX-only, completed regular-session five-minute bars; reject gaps."""
    if len(text.encode('utf-8')) > 2_000_000:
        raise ValueError('الملف أكبر من 2 ميغابايت')
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    required = {'symbol', 'timestamp', 'open', 'high', 'low', 'close'}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError('الأعمدة المطلوبة: symbol,timestamp,open,high,low,close')
    rows = []
    for row in reader:
        if len(rows) >= 5000:
            raise ValueError('الحد 5000 شمعة')
        if row['symbol'] != 'SPX':
            raise ValueError('هذا القسم يقبل SPX فقط؛ لا يقبل SPY أو أسعار العقود')
        try:
            dt = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
            if dt.tzinfo is None:
                raise ValueError()
            ts = dt.timestamp()
            local = dt.astimezone(NY)
            minute = local.hour*60+local.minute
            values = {k: number(row[k]) for k in ('open', 'high', 'low', 'close')}
            if any(v is None or v <= 0 for v in values.values()):
                raise ValueError()
            if not values['low'] <= min(values['open'], values['close']) <= max(values['open'], values['close']) <= values['high']:
                raise ValueError()
            if ts+300 > clock or local.weekday() > 4 or not 570 <= minute < 960 or minute % 5 or dt.second or dt.microsecond:
                raise ValueError()
        except (ValueError, TypeError, KeyError, OverflowError):
            raise ValueError('شمعة غير صالحة: تحقق من OHLC وتوقيت بداية 5 دقائق مع المنطقة الزمنية، داخل الجلسة ومن دون شموع مستقبلية') from None
        rows.append(dict(ts=ts, day=local.date().isoformat(), **values))
    rows.sort(key=lambda r: r['ts'])
    if len(rows) < 60:
        raise ValueError('يلزم 60 شمعة مكتملة على الأقل')
    for a, b in zip(rows, rows[1:]):
        if a['ts'] == b['ts'] or (a['day'] == b['day'] and b['ts']-a['ts'] != 300):
            raise ValueError('الملف يحتوي شموعًا مكررة أو فجوات داخل الجلسة')
    return rows


def historical_analysis(rows):
    def ema(period):
        value = rows[0]['close']
        for r in rows[1:]:
            value += 2/(period+1)*(r['close']-value)
        return value
    last = rows[-1]['close']
    fast, slow = ema(20), ema(50)
    tr = [max(b['high']-b['low'], abs(b['high']-a['close']), abs(b['low']-a['close']))
          for a, b in zip(rows[-15:-1], rows[-14:])]
    changes = [b['close']-a['close'] for a, b in zip(rows[-15:-1], rows[-14:])]
    gain, loss = sum(max(v, 0) for v in changes), sum(max(-v, 0) for v in changes)
    rsi = 50 if gain == loss == 0 else 100 if loss == 0 else 100-100/(1+gain/loss)
    previous = rows[-7:-1]
    return dict(mode='historical_only', live_entry_allowed=False,
                direction='صاعد' if last > fast > slow else 'هابط' if last < fast < slow else 'متذبذب',
                last=last, ema20=fast, ema50=slow, rsi14=rsi,
                atr14=sum(tr)/14, resistance=max(r['high'] for r in previous),
                support=min(r['low'] for r in previous), timestamp=rows[-1]['ts'])


def render_spx(request, credentials):
    st.warning('SPX — وضع بحث وتجربة فقط • الدخول المباشر غير مفعّل')
    st.write('سعر مؤشر SPX المباشر غير مربوط. بيانات الخيارات المجانية إرشادية: عروض معدّلة وصفقات متأخرة 15 دقيقة؛ لا تستخدمها كسعر تنفيذ.')
    st.caption('SPX مؤشر؛ SPY صندوق مختلف؛ وأسعار Call وPut ليست مستوى المؤشر. لا نرسل أوامر أو نغيّر اشتراكك.')
    st.link_button('مرجع SPX لدى Cboe — بيانات متأخرة', 'https://www.cboe.com/delayed_quotes/spx')
    research, calculator, history = st.tabs(['عقود للتعلّم', 'تكلفة ومخاطرة', 'تحليل تاريخي'])
    with research:
        expiry = st.date_input('تاريخ انتهاء العقود', value=datetime.now(NY).date(), key='spx_expiry')
        st.caption('انتهاء اليوم قد لا تتوفر له عقود. SPX عادة تسوية صباحية، وSPXW مسائية؛ لا نستنتج قابلية التداول من التاريخ وحده.')
        if st.button('عرض عقود SPX الإرشادية', disabled=not all(credentials), key='spx_load'):
            identity = hashlib.sha256(('\0'.join(credentials)).encode()).hexdigest()
            st.session_state['spx_chain'] = (expiry.isoformat(), load_chain(identity, expiry.isoformat(), request, credentials))
        if not all(credentials):
            st.info('يستخدم هذا القسم مفاتيح Alpaca الحالية عند توفرها. لا تحتاج مشاركتها هنا.')
        saved = st.session_state.get('spx_chain')
        if saved and saved[0] == expiry.isoformat():
            result = saved[1]
            if result['error']:
                st.info(result['error'])
            elif not result['rows']:
                st.info('لم يُرجع المصدر عقودًا لهذا التاريخ. لا يعني ذلك أن السوق مغلق.')
            else:
                if result['partial']:
                    st.warning('القائمة جزئية؛ ليست كل العقود المتاحة.')
                st.caption('وقت جلب القائمة UTC: '+result['fetched_at']+' • لقطة ثابتة؛ اضغط العرض لتحديثها بعد دقيقة')
                st.dataframe([{'العقد': r['symbol'], 'النوع': r['side'], 'سعر التنفيذ Strike': r['strike'],
                               'التسوية': r['settlement'], 'Bid إرشادي': r['bid'], 'Ask إرشادي': r['ask'],
                               'السبريد الإرشادي': r['spread'], 'توقيت المصدر': r['quote_time']}
                              for r in result['rows']], hide_index=True)
                st.caption('سعر التنفيذ Strike يختلف عن تكلفة شراء العقد. لا توجد توصية بأفضل عقد من هذه القائمة.')
    with calculator:
        st.write('حاسبة شراء Call أو Put فقط — أرقام تدخلها للتجربة، وليست أسعار سوق')
        premium = st.number_input('علاوة العقد بالدولار لكل وحدة', min_value=0.01, value=5.0, step=0.05, key='spx_premium')
        qty = st.number_input('عدد العقود', min_value=1, max_value=1000, value=1, step=1, key='spx_qty')
        fee = st.number_input('رسوم الدخول لكل عقد بالدولار — أدخل رسوم وسيطك', min_value=0.0, value=0.0, key='spx_fee')
        cost = long_option_cost(premium, qty, fee)
        a, b = st.columns(2)
        a.metric('تكلفة العلاوة', f"${cost['premium_cost']:,.2f}")
        b.metric('أقصى خسارة للشراء شامل رسوم الدخول', f"${cost['maximum_loss']:,.2f}")
        st.caption('المضاعف 100. قد تخسر كامل العلاوة. لا تشمل الحسبة رسوم الإغلاق أو التسوية؛ الصفر لا يعني أن وسيطك مجاني. لا تنطبق على بيع العقود أو السبريد.')
    with history:
        st.write('تحليل ملف SPX تاريخي — لا يولّد دخولًا مباشرًا أو أسعار عقود')
        st.caption('شموع 5 دقائق مكتملة، بتوقيت بداية الشمعة والمنطقة الزمنية. 60 شمعة على الأقل. المصدر والرمز في الملف مسؤولية مُصدّره؛ لا يمكن توثيقهما من CSV وحده.')
        st.download_button('تنزيل قالب الأعمدة CSV', 'symbol,timestamp,open,high,low,close\n', 'spx-template.csv', 'text/csv')
        uploaded = st.file_uploader('ملف الشموع التاريخية', type=['csv'], key='spx_bars')
        if uploaded:
            try:
                if uploaded.size > 2_000_000:
                    raise ValueError('الحد 2 ميغابايت')
                rows = read_bars(uploaded.getvalue().decode('utf-8-sig'), time.time())
                view = historical_analysis(rows)
                st.info('الاتجاه التاريخي: '+view['direction']+' • ليس توصية دخول')
                st.caption('آخر شمعة في الملف: '+datetime.fromtimestamp(view['timestamp'], NY).isoformat())
                st.dataframe([{'المقياس': label, 'القيمة': round(view[key], 2)} for key, label in
                              [('last', 'آخر إغلاق'), ('ema20', 'EMA20'), ('ema50', 'EMA50'),
                               ('rsi14', 'RSI14 بمتوسط بسيط'), ('atr14', 'ATR14 بمتوسط بسيط'),
                               ('support', 'أدنى 6 شموع سابقة'), ('resistance', 'أعلى 6 شموع سابقة')]], hide_index=True)
                st.caption('لا نحسب VWAP أو حجم تداول للمؤشر من حجم SPY، ولا نحول مستوى SPX إلى سعر عقد. لا توجد نسبة نجاح مثبتة.')
            except (ValueError, UnicodeError):
                st.error('تعذر قبول الملف. يلزم SPX فقط و60 شمعة OHLC صحيحة، كل 5 دقائق داخل الجلسة، بتوقيت صريح، دون تكرار أو فجوات أو بيانات مستقبلية.')

from __future__ import annotations

import re
import traceback

import streamlit as st


def _redact(text: str) -> str:
    value = str(text)
    value = re.sub(r"(?i)(apikey|api_key|token|access_token)=([^&\s]+)", r"\1=[REDACTED]", value)
    value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+", r"\1[REDACTED]", value)
    return value[:1200]


try:
    import app_core  # noqa: F401
except Exception as exc:
    tb = traceback.extract_tb(exc.__traceback__)
    last = tb[-1] if tb else None
    st.markdown("## وضع الاسترداد")
    st.error(f"{type(exc).__name__}: {_redact(str(exc))}")
    if last is not None:
        st.caption(
            f"المكان: {last.filename.split('/')[-1]} • السطر {last.lineno} • {last.name}"
        )
    st.info(
        "تم منع التطبيق من الانهيار الكامل. هذه الشاشة تعني أن العطل داخل التطبيق الأساسي، "
        "وليس في تشغيل Streamlit نفسه."
    )

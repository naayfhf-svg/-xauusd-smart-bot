"""Expose the full stock terminal through Streamlit's page navigation."""

from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parents[1] / "app_core.py"))

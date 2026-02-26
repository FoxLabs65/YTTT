"""
Shorts Engine Dashboard - Main Entry Point

Launch: streamlit run ui/app.py
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from version import __version__

# Configure logging for UI and backend traceability (errors -> logs/errors.log)
from config.logging_config import configure_logging

configure_logging(log_to_ui=True)

from models.database import init_db

init_db()

st.set_page_config(
    page_title="Shorts Engine",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

from ui.style import inject_css
inject_css()

from ui.pages import dashboard, setup, discovery, content, review, uploads, cleanup, scheduler, reaction

pages = {
    "Overview": [
        st.Page(dashboard.render, title="Dashboard", icon="📊", default=True, url_path="dashboard"),
    ],
    "Pipeline": [
        st.Page(discovery.render, title="Discovery", icon="🔍", url_path="discovery"),
        st.Page(content.render, title="Content Studio", icon="✍️", url_path="content"),
        st.Page(review.render, title="Review", icon="🎬", url_path="review"),
        st.Page(uploads.render, title="Uploads", icon="📤", url_path="uploads"),
        st.Page(reaction.render, title="Reaction Shorts", icon="🎯", url_path="reaction"),
    ],
    "Settings": [
        st.Page(setup.render, title="Setup", icon="⚙️", url_path="setup"),
        st.Page(cleanup.render, title="Cleanup", icon="🧹", url_path="cleanup"),
        st.Page(scheduler.render, title="Scheduler", icon="📅", url_path="scheduler"),
    ],
}

pg = st.navigation(pages)

with st.sidebar:
    st.caption(f"Shorts Engine v{__version__}")

    @st.fragment(run_every=2)
    def _sidebar_status():
        from ui.components import running_indicator
        running_indicator()

    _sidebar_status()

pg.run()

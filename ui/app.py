"""
Shorts Engine Dashboard - Main Entry Point

Launch: streamlit run ui/app.py
"""

import logging
import os
import sys
from pathlib import Path

import streamlit as st

logger = logging.getLogger("ui.app")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from version import __version__

# Configure logging for UI and backend traceability (errors -> logs/errors.log)
from config.logging_config import configure_logging

configure_logging(log_to_ui=True)

from models.database import init_db

init_db()

# Run category consistency check at startup (cached for session)
if "category_check_warnings" not in st.session_state:
    try:
        from config.validation import validate_category_config

        st.session_state.category_check_warnings = validate_category_config()
    except Exception:
        st.session_state.category_check_warnings = []

st.set_page_config(
    page_title="Shorts Engine",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

from ui.style import inject_css
inject_css()

from ui.pages import dashboard, setup, content, review, uploads, cleanup, scheduler, reaction

pages = {
    "Overview": [
        st.Page(dashboard.render, title="Dashboard", icon="📊", default=True, url_path="dashboard"),
    ],
    "Pipeline": [
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

    # Category checklist warning (shown at startup if issues found)
    if st.session_state.get("category_check_warnings"):
        with st.expander("⚠️ Category config", expanded=False):
            for w in st.session_state["category_check_warnings"]:
                st.caption(f"• {w}")
            st.caption("Go to **Setup** to fix.")

    # Compact running indicator only (no log — live log kept in Content Studio)
    @st.fragment(run_every=5)
    def _sidebar_status():
        from ui.components import running_indicator
        running_indicator()
    _sidebar_status()

    st.divider()

    # Graceful exit: stop pipeline, scheduler, and quit
    if st.session_state.get("_showing_exit_confirm"):
        st.warning("The server will shut down. Close this tab manually after the connection is lost.")
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("Confirm Exit", type="primary", key="exit_confirm_yes"):
                st.session_state["_exit_requested"] = True
                st.session_state.pop("_showing_exit_confirm", None)
                st.rerun()
        with col_b:
            if st.button("Cancel", key="exit_confirm_no"):
                st.session_state.pop("_showing_exit_confirm", None)
                st.rerun()
    elif st.button(
        "Exit Application",
        type="secondary",
        use_container_width=True,
        key="exit_app",
        help="Stop all tasks and shut down the server. After the connection is lost, close this tab manually.",
    ):
        st.session_state["_showing_exit_confirm"] = True
        st.rerun()

# Perform graceful shutdown if exit was requested
if st.session_state.get("_exit_requested"):
    try:
        from ui.runner import get_runner
        runner = get_runner()
        if runner.is_running:
            runner.stop()
            logger.info("Stopped running pipeline task")
        pid = st.session_state.get("scheduler_pid")
        if pid:
            try:
                import signal
                os.kill(pid, signal.SIGTERM)
                logger.info("Stopped scheduler (pid=%s)", pid)
            except (OSError, ProcessLookupError):
                pass
            st.session_state.scheduler_pid = None
        logger.info("Graceful shutdown complete")
    except Exception as e:
        logger.exception("Shutdown error: %s", e)
    finally:
        st.session_state.pop("_exit_requested", None)
        os._exit(0)

pg.run()

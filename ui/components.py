"""Shared reusable widgets for the Shorts Engine dashboard."""

from __future__ import annotations

import streamlit as st


def metric_card(label: str, value, icon: str = ""):
    """Render a styled metric card."""
    icon_html = f'<span style="font-size:1.4rem">{icon}</span> ' if icon else ""
    st.markdown(
        f"""<div class="metric-card">
            <div class="value">{icon_html}{value}</div>
            <div class="label">{label}</div>
        </div>""",
        unsafe_allow_html=True,
    )


STATUS_STYLES = {
    "success": "badge-success",
    "completed": "badge-success",
    "uploaded": "badge-success",
    "approved": "badge-success",
    "archived": "badge-success",
    "running": "badge-warning",
    "pending": "badge-warning",
    "composing": "badge-warning",
    "pending_assets": "badge-warning",
    "assets_ready": "badge-info",
    "composed": "badge-info",
    "failed": "badge-danger",
    "rejected": "badge-danger",
    "error": "badge-danger",
    "idle": "badge-neutral",
}


def status_badge(status: str) -> str:
    """Return HTML for a status badge."""
    css_class = STATUS_STYLES.get(status, "badge-neutral")
    return f'<span class="badge {css_class}">{status}</span>'


def render_status_badge(status: str):
    """Render a status badge directly."""
    st.markdown(status_badge(status), unsafe_allow_html=True)


def log_viewer(text: str, height: int = 400):
    """Render a styled log viewer."""
    if not text:
        st.info("No log output yet.")
        return
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    st.markdown(
        f'<div class="log-viewer" style="max-height:{height}px">{escaped}</div>',
        unsafe_allow_html=True,
    )


def section_header(text: str):
    """Render a section header with bottom border."""
    st.markdown(f'<div class="section-header">{text}</div>', unsafe_allow_html=True)


def confirm_action(label: str, key: str) -> bool:
    """Two-step confirmation: first click shows a confirm button."""
    confirm_key = f"_confirm_{key}"
    if st.session_state.get(confirm_key):
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button(f"Confirm {label}", key=f"{key}_yes", type="primary"):
                st.session_state[confirm_key] = False
                return True
        with col2:
            if st.button("Cancel", key=f"{key}_cancel"):
                st.session_state[confirm_key] = False
                st.rerun()
        return False
    else:
        if st.button(label, key=key):
            st.session_state[confirm_key] = True
            st.rerun()
        return False


def running_indicator():
    """Show a pulsing indicator when a background task is running.
    Static version - updates only on rerun."""
    from ui.runner import get_runner
    runner = get_runner()
    if runner.is_running:
        st.markdown(
            f'<div class="pipeline-status">'
            f'<span class="phase-dot running"></span> '
            f'<span style="color:#fbbf24;font-weight:600">'
            f'{runner.task_name} running ({runner.elapsed})</span></div>',
            unsafe_allow_html=True,
        )
        return True
    return False


def live_running_indicator():
    """Show a pulsing indicator with live-updating elapsed time.
    Uses a fragment with run_every when a task is running."""
    from ui.runner import get_runner
    r = get_runner()
    run_every = 5 if r.is_running else None

    @st.fragment(run_every=run_every)
    def _indicator_fragment():
        r2 = get_runner()
        if r2.is_running:
            st.markdown(
                f'<div class="pipeline-status">'
                f'<span class="phase-dot running"></span> '
                f'<span style="color:#fbbf24;font-weight:600">'
                f'{r2.task_name} running ({r2.elapsed})</span></div>',
                unsafe_allow_html=True,
            )

    _indicator_fragment()


def live_log_viewer(task_name: str | None = None, lines: int = 50, auto_refresh: bool = True):
    """Show log with optional auto-refresh when a task is running.
    Uses dynamic run_every (only when running) to avoid fragment orphan errors."""
    from ui.runner import get_runner
    r = get_runner()
    run_every = 5 if (auto_refresh and r.is_running) else None

    @st.fragment(run_every=run_every)
    def _log_fragment():
        r2 = get_runner()
        if r2.is_running and (task_name is None or r2.task_name == task_name):
            st.caption(f"Running... {r2.elapsed} — live updates")
            log_viewer(r2.get_log_tail(lines))
        elif r2.status in ("completed", "failed") and r2.log_path and r2.log_path.exists():
            st.caption(f"Finished: {r2.status} ({r2.elapsed})")
            log_viewer(r2.get_log_tail(lines))
        else:
            st.info("No active task. Start a task to see logs.")

    _log_fragment()


def static_log_viewer(task_name: str | None = None, lines: int = 50):
    """Show current log snapshot without auto-refresh. Safe to use on any page.
    Use when a task is running — log updates on next rerun. For live updates,
    use the sidebar (always visible)."""
    from ui.runner import get_runner
    r = get_runner()
    if r.is_running and (task_name is None or r.task_name == task_name):
        st.caption(f"Running... {r.elapsed} — refresh page for updates, or view sidebar for live log")
        log_viewer(r.get_log_tail(lines))
    elif r.status in ("completed", "failed") and r.log_path and r.log_path.exists():
        st.caption(f"Finished: {r.status} ({r.elapsed})")
        log_viewer(r.get_log_tail(lines))
    else:
        st.info("No active task. Start a task to see logs.")

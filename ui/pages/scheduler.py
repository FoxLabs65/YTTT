"""Scheduler page - per-phase toggles, time pickers, timezone, start/stop daemon."""

from __future__ import annotations

import copy
import subprocess
import sys
from datetime import time as dt_time
from pathlib import Path

# Ensure project root on path (Streamlit may run pages in isolation)
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from models.config import load_config, save_config, reload_config
from ui.components import section_header, metric_card, log_viewer
from ui.runner import get_runner, PROJECT_ROOT

TIMEZONES = [
    "Europe/London", "Europe/Paris", "Europe/Berlin",
    "US/Eastern", "US/Central", "US/Pacific",
    "Asia/Tokyo", "Asia/Shanghai", "Australia/Sydney",
    "UTC",
]

PHASES = [
    ("discovery_time", "Discovery", "06:00"),
    ("ideation_time", "Ideation", "06:30"),
    ("sourcing_time", "Sourcing", "07:00"),
    ("composing_time", "Composing", "07:30"),
    ("cleanup_time", "Cleanup", "08:00"),
    ("notification_time", "Notification", "08:30"),
]


def _parse_time(t_str: str) -> dt_time:
    parts = t_str.split(":")
    return dt_time(int(parts[0]), int(parts[1]))


def render():
    st.title("Scheduler")

    cfg = load_config()
    sched = cfg.get("scheduler", {})
    if not isinstance(sched, dict):
        sched = {}

    runner = get_runner()

    # ── Master Toggle ───────────────────────────────────────────
    section_header("Automated Pipeline")

    is_scheduler_running = st.session_state.get("scheduler_pid") is not None

    col_toggle, col_status = st.columns([1, 2])
    with col_toggle:
        if is_scheduler_running:
            st.success("Scheduler is running")
            if st.button("Stop Scheduler", type="secondary", width="stretch"):
                _stop_scheduler()
                st.toast("Scheduler stopped")
                st.rerun()
        else:
            st.info("Scheduler is not running")
            if st.button("Start Scheduler", type="primary", width="stretch", disabled=runner.is_running):
                _start_scheduler()
                st.toast("Scheduler started in background!")
                st.rerun()
    with col_status:
        if is_scheduler_running:
            pid = st.session_state.get("scheduler_pid")
            st.caption(f"Process ID: {pid}")

    st.divider()

    # ── Schedule Configuration ──────────────────────────────────
    section_header("Phase Schedule")
    st.caption("Set the time each phase runs daily. The scheduler must be restarted for changes to take effect.")

    phase_cols = st.columns(3)
    for i, (key, label, default) in enumerate(PHASES):
        with phase_cols[i % 3]:
            current = sched.get(key, default)
            val = st.time_input(
                label,
                value=_parse_time(current),
                key=f"sched_{key}",
                step=900,  # 15-min increments
            )
            sched[key] = val.strftime("%H:%M")

    st.write("")

    st.caption("Note: The scheduler runs discovery, ideation, sourcing, composing, and cleanup. **Uploads are never automatic** — select videos in the Uploads tab to avoid algorithm penalty.")

    # ── General Settings ────────────────────────────────────────
    col_tz, col_vpr = st.columns(2)

    with col_tz:
        current_tz = sched.get("timezone", "Europe/London")
        tz_idx = TIMEZONES.index(current_tz) if current_tz in TIMEZONES else 0
        val = st.selectbox("Timezone", TIMEZONES, index=tz_idx, key="sched_tz")
        sched["timezone"] = val

    with col_vpr:
        val = st.slider("Videos Per Run", 1, 20, int(sched.get("videos_per_run", 5)), key="sched_vpr")
        sched["videos_per_run"] = val

    st.divider()

    # ── Schedule Visualization ──────────────────────────────────
    section_header("Timeline")

    _render_timeline(sched)

    st.divider()

    # ── Save ────────────────────────────────────────────────────
    col_save, col_reload = st.columns([1, 3])
    with col_save:
        if st.button("Save Schedule", type="primary", width="stretch"):
            new_cfg = copy.deepcopy(cfg)
            new_cfg["scheduler"] = sched
            save_config(new_cfg)
            reload_config()
            st.toast("Schedule saved! Restart the scheduler for changes to take effect.")
            st.rerun()
    with col_reload:
        if st.button("Reset to Defaults"):
            for key, _, default in PHASES:
                sched[key] = default
            sched["timezone"] = "Europe/London"
            sched["videos_per_run"] = 5
            st.rerun()

    st.divider()

    # ── Run History ─────────────────────────────────────────────
    section_header("Run History")

    log_dir = PROJECT_ROOT / "logs"
    if log_dir.exists():
        pipeline_logs = sorted(
            [f for f in log_dir.glob("pipeline_*.log")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:10]
        if pipeline_logs:
            for lf in pipeline_logs:
                from datetime import datetime
                modified = datetime.fromtimestamp(lf.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                size_kb = lf.stat().st_size / 1024
                with st.expander(f"{lf.name} — {modified} ({size_kb:.0f} KB)"):
                    try:
                        text = lf.read_text(encoding="utf-8", errors="replace")
                        log_viewer(text[-5000:] if len(text) > 5000 else text)
                    except Exception:
                        st.error("Could not read log file")
        else:
            st.info("No pipeline logs yet. Run the pipeline to generate logs.")
    else:
        st.info("No log directory found.")


def _render_timeline(sched: dict):
    """Render a simple timeline visualization of the schedule."""
    phases_data = []
    for key, label, default in PHASES:
        t = sched.get(key, default)
        h, m = t.split(":")
        minutes = int(h) * 60 + int(m)
        phases_data.append((label, minutes, t))

    phases_data.sort(key=lambda x: x[1])

    min_t = max(phases_data[0][1] - 30, 0) if phases_data else 0
    max_t = min(phases_data[-1][1] + 60, 1440) if phases_data else 1440
    span = max(max_t - min_t, 1)

    colors = ["#4a69bd", "#6c5ce7", "#00b894", "#fdcb6e", "#e17055", "#74b9ff"]

    bars_html = ""
    for i, (label, mins, time_str) in enumerate(phases_data):
        pct = ((mins - min_t) / span) * 100
        color = colors[i % len(colors)]
        bars_html += (
            f'<div style="position:absolute;left:{pct:.1f}%;top:0;bottom:0;width:3px;'
            f'background:{color};"></div>'
            f'<div style="position:absolute;left:{pct:.1f}%;top:-22px;transform:translateX(-50%);'
            f'font-size:0.72rem;color:{color};font-weight:600;white-space:nowrap">'
            f'{label}<br>{time_str}</div>'
        )

    st.markdown(
        f'<div style="position:relative;height:30px;margin:35px 10px 10px 10px;'
        f'background:#1e2130;border-radius:6px;border:1px solid #2d3250">'
        f'{bars_html}</div>',
        unsafe_allow_html=True,
    )
    st.write("")


def _start_scheduler():
    """Start the scheduler as a background subprocess."""
    try:
        proc = subprocess.Popen(
            ["python", "main.py", "--schedule"],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        st.session_state.scheduler_pid = proc.pid
    except Exception as e:
        st.error(f"Failed to start scheduler: {e}")


def _stop_scheduler():
    """Stop the running scheduler process."""
    pid = st.session_state.get("scheduler_pid")
    if pid:
        try:
            import os
            import signal
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        st.session_state.scheduler_pid = None

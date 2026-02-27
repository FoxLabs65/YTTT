"""Dashboard page - pipeline overview, stats, quick actions, recent activity."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root on path (Streamlit may run pages in isolation)
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from models.database import get_connection, get_stats
from ui.components import metric_card, section_header, status_badge, static_log_viewer
from ui.runner import get_runner

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _dir_size_mb(d: Path) -> float:
    if not d.exists():
        return 0.0
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / (1024 * 1024)


def render():
    st.title("Dashboard")

    conn = get_connection()
    stats = get_stats(conn)

    # ── Metric cards ────────────────────────────────────────────
    cols = st.columns(6)
    items = [
        ("Trends", stats.get("total_trends", 0), "🔍"),
        ("Scripts", stats.get("total_scripts", 0), "✍️"),
        ("Videos", stats.get("total_videos", 0), "🎬"),
        ("Pending", stats.get("pending_review", 0), "⏳"),
        ("Uploaded", stats.get("uploaded", 0), "📤"),
        ("Disk", f"{_dir_size_mb(PROJECT_ROOT / 'output'):.0f} MB", "💾"),
    ]
    for col, (label, value, icon) in zip(cols, items):
        with col:
            metric_card(label, value, icon)

    st.write("")

    # ── Pipeline Health ─────────────────────────────────────────
    runner = get_runner()
    col_health, col_actions = st.columns([2, 1])

    with col_health:
        section_header("Pipeline Health")
        if runner.is_running:
            st.markdown(
                f'<div class="pipeline-status">'
                f'<span class="phase-dot running"></span> '
                f'<b>{runner.task_name}</b> running for {runner.elapsed}'
                f'</div>',
                unsafe_allow_html=True,
            )
            with st.expander("Live Log", expanded=True):
                static_log_viewer(task_name=None, lines=40)
            if st.button("Stop", type="secondary"):
                runner.stop()
                st.toast("Task stopped")
                st.rerun()
        elif runner.status in ("completed", "failed"):
            dot = "success" if runner.status == "completed" else "failed"
            st.markdown(
                f'<div class="pipeline-status">'
                f'<span class="phase-dot {dot}"></span> '
                f'Last run: <b>{runner.task_name}</b> {runner.status} ({runner.elapsed})'
                f'</div>',
                unsafe_allow_html=True,
            )
            with st.expander("Last Run Log"):
                static_log_viewer(task_name=None, lines=50)
        else:
            st.markdown(
                '<div class="pipeline-status">'
                '<span class="phase-dot idle"></span> No recent pipeline runs'
                '</div>',
                unsafe_allow_html=True,
            )

    with col_actions:
        section_header("Quick Actions")
        if st.button("Run Full Pipeline", type="primary", disabled=runner.is_running, width="stretch"):
            runner.start("full_pipeline")
            st.toast("Pipeline started!")
            st.rerun()
        if st.button("Upload Approved", disabled=runner.is_running, width="stretch"):
            runner.start("upload")
            st.toast("Upload started!")
            st.rerun()
        if st.button("Run Cleanup", disabled=runner.is_running, width="stretch"):
            runner.start("cleanup")
            st.toast("Cleanup started!")
            st.rerun()

    st.divider()

    # ── Recent Activity ─────────────────────────────────────────
    section_header("Recent Activity")

    col_scripts, col_videos = st.columns(2)

    with col_scripts:
        st.markdown("**Recent Scripts**")
        recent_scripts = conn.execute(
            "SELECT id, title, category, status, created_at FROM scripts ORDER BY created_at DESC LIMIT 8"
        ).fetchall()
        if recent_scripts:
            for s in recent_scripts:
                st.markdown(
                    f"#{s['id']} {status_badge(s['status'])} **{s['title'][:50]}** "
                    f"<small style='color:#94a3b8'>{s['category']} · {s['created_at'][:16]}</small>",
                    unsafe_allow_html=True,
                )
        else:
            st.info("No scripts yet. Run the pipeline to generate content.")

    with col_videos:
        st.markdown("**Recent Videos**")
        recent_videos = conn.execute(
            "SELECT v.id, v.yt_title, v.status, v.created_at, v.duration "
            "FROM videos v ORDER BY v.created_at DESC LIMIT 8"
        ).fetchall()
        if recent_videos:
            for v in recent_videos:
                dur = f"{v['duration']:.0f}s" if v['duration'] else "?"
                st.markdown(
                    f"#{v['id']} {status_badge(v['status'])} **{(v['yt_title'] or 'Untitled')[:50]}** "
                    f"<small style='color:#94a3b8'>{dur} · {v['created_at'][:16]}</small>",
                    unsafe_allow_html=True,
                )
        else:
            st.info("No videos yet.")

    st.divider()

    # ── Run History ─────────────────────────────────────────────
    section_header("Run History")
    logs = runner.get_recent_logs(limit=8)
    if logs:
        for log in logs:
            st.markdown(
                f"**{log['file']}** — {log['modified']} — {log['size_kb']:.1f} KB",
            )
    else:
        st.info("No pipeline run logs yet.")

    # ── Breakdown ───────────────────────────────────────────────
    st.divider()
    section_header("Storage Breakdown")
    dirs = {
        "Pending Videos": PROJECT_ROOT / "output" / "pending",
        "Archived Videos": PROJECT_ROOT / "output" / "archive",
        "Assets": PROJECT_ROOT / "assets",
        "Music Library": PROJECT_ROOT / "assets" / "music",
    }
    bcols = st.columns(len(dirs))
    for col, (name, path) in zip(bcols, dirs.items()):
        with col:
            metric_card(name, f"{_dir_size_mb(path):.1f} MB")

    conn.close()

    # Auto-refresh every 5s when any task is running (must be last so page renders first)
    @st.fragment(run_every=5)
    def _dashboard_refresh():
        r = get_runner()
        if r.is_running:
            st.rerun()

    _dashboard_refresh()

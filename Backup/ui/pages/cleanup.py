"""Cleanup page - disk usage, preview what gets cleaned, run cleanup."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root on path (Streamlit may run pages in isolation)
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from models.database import (
    get_connection, get_rejected_videos, get_uploaded_videos, get_stats,
)
from ui.components import section_header, metric_card, log_viewer, confirm_action, live_log_viewer
from ui.runner import get_runner

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _dir_size_mb(d: Path) -> float:
    if not d.exists():
        return 0.0
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / (1024 * 1024)


def _count_files(d: Path) -> int:
    if not d.exists():
        return 0
    return sum(1 for f in d.rglob("*") if f.is_file())


def render():
    st.title("Cleanup")

    conn = get_connection()
    runner = get_runner()

    # ── Disk Usage Overview ─────────────────────────────────────
    section_header("Disk Usage")

    dirs = {
        "Pending Videos": PROJECT_ROOT / "output" / "pending",
        "Archive": PROJECT_ROOT / "output" / "archive",
        "Assets": PROJECT_ROOT / "assets",
        "Music Library": PROJECT_ROOT / "assets" / "music",
        "Thumbnails": PROJECT_ROOT / "output" / "thumbnails",
        "Logs": PROJECT_ROOT / "logs",
    }

    cols = st.columns(len(dirs))
    total_mb = 0.0
    for col, (name, path) in zip(cols, dirs.items()):
        size = _dir_size_mb(path)
        total_mb += size
        with col:
            metric_card(name, f"{size:.1f} MB")

    st.markdown(f"**Total: {total_mb:.1f} MB** across {sum(_count_files(d) for d in dirs.values())} files")

    st.divider()

    # ── What Would Be Cleaned ───────────────────────────────────
    section_header("Cleanup Preview")

    rejected = get_rejected_videos(conn)
    uploaded = get_uploaded_videos(conn)

    orphan_count = _count_orphaned_assets(conn)

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("**Rejected Videos**")
        if rejected:
            st.warning(f"{len(rejected)} rejected videos to delete")
            with st.expander("Details"):
                for v in rejected:
                    fpath = Path(v.get("file_path", ""))
                    exists = fpath.exists()
                    size = f"{fpath.stat().st_size / (1024*1024):.1f} MB" if exists else "file missing"
                    st.markdown(f"- #{v['id']} — {v.get('yt_title', 'Untitled')[:30]} ({size})")
        else:
            st.success("No rejected videos to clean")

    with col2:
        st.markdown("**Uploaded Videos to Archive**")
        if uploaded:
            st.info(f"{len(uploaded)} videos ready to archive")
            with st.expander("Details"):
                for v in uploaded:
                    st.markdown(f"- #{v['id']} — {v.get('yt_title', 'Untitled')[:30]}")
        else:
            st.success("No videos to archive")

    with col3:
        st.markdown("**Orphaned Asset Files**")
        if orphan_count > 0:
            st.warning(f"{orphan_count} orphaned files found")
        else:
            st.success("No orphaned files")

    st.divider()

    # ── Run Cleanup ─────────────────────────────────────────────
    section_header("Run Cleanup")
    st.caption(
        "Cleanup will: delete rejected video files and their assets, "
        "move uploaded videos to the archive folder, and remove orphaned asset files."
    )

    if runner.is_running:
        st.info(f"Task running: {runner.task_name} ({runner.elapsed})")
        with st.expander("Live Log", expanded=True):
            live_log_viewer(task_name="cleanup", lines=40)
    else:
        if confirm_action("Run Cleanup", "cleanup_action"):
            runner.start("cleanup")
            st.toast("Cleanup started!")
            st.rerun()

    st.divider()

    # ── Cleanup History ─────────────────────────────────────────
    section_header("Recent Cleanup Logs")
    log_dir = PROJECT_ROOT / "logs" / "ui_runs"
    if log_dir.exists():
        cleanup_logs = sorted(
            [f for f in log_dir.glob("cleanup_*.log")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:5]
        if cleanup_logs:
            for lf in cleanup_logs:
                from datetime import datetime
                modified = datetime.fromtimestamp(lf.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                with st.expander(f"{lf.name} — {modified}"):
                    try:
                        log_viewer(lf.read_text(encoding="utf-8", errors="replace"))
                    except Exception:
                        st.error("Could not read log file")
        else:
            st.info("No cleanup logs yet.")
    else:
        st.info("No cleanup logs yet.")

    conn.close()


def _count_orphaned_assets(conn) -> int:
    """Count asset files on disk that aren't tracked in the DB or belong to terminal scripts."""
    assets_dir = PROJECT_ROOT / "assets"
    if not assets_dir.exists():
        return 0

    db_paths = set()
    rows = conn.execute("SELECT local_path FROM assets").fetchall()
    for r in rows:
        db_paths.add(r["local_path"])

    orphan_count = 0
    for f in assets_dir.rglob("*"):
        if f.is_file() and str(f) not in db_paths and f.suffix.lower() not in (".gitkeep",):
            orphan_count += 1
    return orphan_count

"""Uploads page - upload management, pending uploads, upload history, archive browser."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure project root on path (Streamlit may run pages in isolation)
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from models.database import get_connection, get_pending_uploads, get_videos_by_status
from ui.components import section_header, status_badge, metric_card, live_log_viewer
from ui.runner import get_runner

PROJECT_ROOT = Path(__file__).parent.parent.parent


def render():
    st.title("Uploads & Archive")

    conn = get_connection()
    runner = get_runner()

    # ── Pending Uploads ─────────────────────────────────────────
    section_header("Pending Uploads")

    pending = get_pending_uploads(conn)

    if pending:
        st.caption(f"{len(pending)} uploads waiting")

        col_action1, col_action2, col_action3 = st.columns(3)
        with col_action1:
            if st.button("Upload All", type="primary", disabled=runner.is_running, width="stretch"):
                runner.start("upload")
                st.toast("Upload started!")
                st.rerun()
        with col_action2:
            if st.button("Upload YouTube Only", disabled=runner.is_running, width="stretch"):
                runner.start("upload", ["--platform", "youtube"])
                st.toast("YouTube upload started!")
                st.rerun()
        with col_action3:
            if st.button("Upload TikTok Only", disabled=runner.is_running, width="stretch"):
                runner.start("upload", ["--platform", "tiktok"])
                st.toast("TikTok upload started!")
                st.rerun()

        for u in pending:
            with st.expander(f"{u.get('platform', '?').title()} — {u.get('yt_title', 'Untitled')[:50]}"):
                col1, col2 = st.columns([1, 2])
                with col1:
                    fpath = Path(u.get("file_path", ""))
                    if fpath.exists():
                        st.video(str(fpath))
                with col2:
                    st.markdown(f"**Platform:** {u.get('platform', '?').title()}")
                    st.markdown(f"**Title:** {u.get('yt_title', 'Untitled')}")
                    if u.get("thumbnail_path"):
                        tp = Path(u["thumbnail_path"])
                        if tp.exists():
                            st.image(str(tp), width=200)
    else:
        st.info("No pending uploads. Approve videos in the Review tab first.")

    # Show upload log when running or when upload just finished (real-time refresh)
    upload_active = runner.task_name == "upload" and (runner.is_running or runner.status in ("completed", "failed"))
    if upload_active:
        with st.expander("Upload Log", expanded=True):
            live_log_viewer(task_name="upload", lines=40)
        if st.button("Refresh", help="Refresh page to see latest status"):
            st.rerun()

    # Auto-refresh when upload is running (updates status, pending list, log)
    # Use 5s interval to reduce "fragment does not exist" warnings during reruns
    @st.fragment(run_every=5)
    def _upload_refresh():
        r = get_runner()
        if r.is_running and r.task_name == "upload":
            st.rerun()

    _upload_refresh()

    st.divider()

    # ── Upload History ──────────────────────────────────────────
    section_header("Upload History")

    uploads = conn.execute(
        """SELECT u.*, v.yt_title, v.file_path, v.thumbnail_path
           FROM uploads u
           JOIN videos v ON u.video_id = v.id
           WHERE u.upload_status != 'pending'
           ORDER BY u.uploaded_at DESC NULLS LAST
           LIMIT 50"""
    ).fetchall()

    if uploads:
        for u in uploads:
            u_dict = dict(u)
            badge = status_badge(u_dict.get("upload_status", "unknown"))
            platform = u_dict.get("platform", "?").title()
            title = u_dict.get("yt_title", "Untitled")[:50]
            url = u_dict.get("platform_url", "")
            uploaded_at = u_dict.get("uploaded_at", "")[:16] if u_dict.get("uploaded_at") else ""

            link = f"[View]({url})" if url else ""
            st.markdown(
                f"{badge} **{platform}** — {title} {link} "
                f"<small style='color:#94a3b8'>{uploaded_at}</small>",
                unsafe_allow_html=True,
            )
            if u_dict.get("error_message"):
                st.error(u_dict["error_message"])
    else:
        st.info("No upload history yet.")

    st.divider()

    # ── Archive Browser ─────────────────────────────────────────
    section_header("Archive")

    archive_dir = PROJECT_ROOT / "output" / "archive"
    archived_videos = get_videos_by_status(conn, "archived")

    if archived_videos:
        cols_per_row = 3
        for i in range(0, len(archived_videos), cols_per_row):
            cols = st.columns(cols_per_row)
            for j, col in enumerate(cols):
                idx = i + j
                if idx >= len(archived_videos):
                    break
                v = archived_videos[idx]
                with col:
                    fpath = Path(v.get("file_path", ""))
                    title = v.get("yt_title", "Untitled")

                    if v.get("thumbnail_path") and Path(v["thumbnail_path"]).exists():
                        st.image(str(v["thumbnail_path"]), width="stretch")
                    st.markdown(f"**{title[:40]}**")

                    dur = f"{v.get('duration', 0):.0f}s" if v.get("duration") else ""
                    size = f"{v.get('file_size_mb', 0):.1f}MB" if v.get("file_size_mb") else ""
                    st.caption(f"{dur} · {size}")

                    if fpath.exists():
                        with st.expander("Play"):
                            st.video(str(fpath))
                        with open(fpath, "rb") as f:
                            st.download_button(
                                "Download",
                                data=f,
                                file_name=fpath.name,
                                mime="video/mp4",
                                key=f"dl_{v['id']}",
                                width="stretch",
                            )
    elif archive_dir.exists() and any(archive_dir.iterdir()):
        st.info("Archive files found on disk but not tracked in database. Run cleanup to reconcile.")
    else:
        st.info("No archived videos. Videos are archived after successful upload.")

    conn.close()

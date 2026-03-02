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
from ui.components import section_header, status_badge, metric_card, static_log_viewer
from ui.runner import get_runner

PROJECT_ROOT = Path(__file__).parent.parent.parent


def render():
    st.title("Uploads & Archive")

    conn = get_connection()
    runner = get_runner()

    # ── Pending Uploads ─────────────────────────────────────────
    section_header("Pending Uploads")
    st.caption("Select which videos to upload. Uploads are spaced to protect channel algorithm ranking.")

    pending = get_pending_uploads(conn)

    if pending:
        default_selected = st.session_state.get("upload_selected_ids", set())

        selected_ids = []
        for u in pending:
            uid = u.get("id")
            platform = u.get("platform", "?")
            title = (u.get("yt_title") or "Untitled")[:50]
            is_selected = st.checkbox(
                f"{platform.title()} — {title}",
                value=uid in default_selected,
                key=f"upload_sel_{uid}",
            )
            if is_selected:
                selected_ids.append(uid)
        st.session_state["upload_selected_ids"] = set(selected_ids)

        if selected_ids:
            st.caption(f"✓ {len(selected_ids)} selected. Uploads spaced per config to avoid algorithm penalty.")

        col_sel1, col_sel2, _ = st.columns([1, 1, 2])
        with col_sel1:
            if st.button("Select all", key="upload_sel_all"):
                st.session_state["upload_selected_ids"] = {u["id"] for u in pending}
                st.rerun()
        with col_sel2:
            if st.button("Deselect all", key="upload_desel_all"):
                st.session_state["upload_selected_ids"] = set()
                st.rerun()

        col_action1, col_action2, col_action3 = st.columns(3)
        with col_action1:
            disabled = runner.is_running or not selected_ids
            if st.button(
                "Upload Selected",
                type="primary",
                disabled=disabled,
                width="stretch",
                help="Upload selected videos only" if selected_ids else "Select at least one video",
            ):
                extra = ["--upload-ids", ",".join(str(i) for i in selected_ids)]
                runner.start("upload", extra)
                st.toast(f"Uploading {len(selected_ids)} selected video(s)…")
                st.rerun()
        with col_action2:
            yt_ids = [u["id"] for u in pending if u.get("platform") == "youtube" and u["id"] in selected_ids]
            if st.button(
                "Upload Selected (YouTube)",
                disabled=runner.is_running or not yt_ids,
                width="stretch",
                help="Upload selected YouTube only" if yt_ids else "Select YouTube video(s)",
            ):
                extra = ["--platform", "youtube", "--upload-ids", ",".join(str(i) for i in yt_ids)]
                runner.start("upload", extra)
                st.toast(f"Uploading {len(yt_ids)} to YouTube…")
                st.rerun()
        with col_action3:
            tt_ids = [u["id"] for u in pending if u.get("platform") == "tiktok" and u["id"] in selected_ids]
            if st.button(
                "Upload Selected (TikTok)",
                disabled=runner.is_running or not tt_ids,
                width="stretch",
                help="Upload selected TikTok only" if tt_ids else "Select TikTok video(s)",
            ):
                extra = ["--platform", "tiktok", "--upload-ids", ",".join(str(i) for i in tt_ids)]
                runner.start("upload", extra)
                st.toast(f"Uploading {len(tt_ids)} to TikTok…")
                st.rerun()

        st.divider()

        for u in pending:
            with st.expander(f"{u.get('platform', '?').title()} — {u.get('yt_title', 'Untitled')[:50]}", expanded=False):
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
            static_log_viewer(task_name="upload", lines=40)
        if st.button("Refresh", key="upload_refresh", help="Update page with latest upload status"):
            st.rerun()

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

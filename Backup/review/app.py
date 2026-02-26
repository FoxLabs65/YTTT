"""
Streamlit Review Dashboard
Human-in-the-loop review of generated videos before upload.

Run with: streamlit run review/app.py
"""

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.database import get_connection, init_db, get_videos_by_status, update_video_status, get_stats, insert_upload

init_db()

st.set_page_config(page_title="Shorts Review Dashboard", layout="wide", page_icon="🎬")


def main():
    st.title("Shorts Review Dashboard")

    tab_review, tab_stats, tab_history = st.tabs(["Review Queue", "Stats", "Upload History"])

    with tab_review:
        render_review_queue()

    with tab_stats:
        render_stats()

    with tab_history:
        render_history()


def render_review_queue():
    conn = get_connection()
    videos = get_videos_by_status(conn, "pending")

    if not videos:
        st.info("No videos pending review. Run the pipeline to generate new content.")
        conn.close()
        return

    st.write(f"**{len(videos)} videos pending review**")

    for video in videos:
        with st.container():
            st.divider()
            col_video, col_details = st.columns([1, 1])

            with col_video:
                video_path = Path(video["file_path"])
                if video_path.exists():
                    st.video(str(video_path))
                else:
                    st.warning(f"Video file not found: {video_path.name}")

                if video.get("thumbnail_path"):
                    thumb = Path(video["thumbnail_path"])
                    if thumb.exists():
                        st.image(str(thumb), caption="Thumbnail", width=200)

            with col_details:
                video_id = video["id"]
                key_prefix = f"v{video_id}"

                st.subheader(f"Video #{video_id}")

                # Editable fields
                new_title = st.text_input(
                    "YouTube Title",
                    value=video.get("yt_title", ""),
                    key=f"{key_prefix}_title",
                )
                new_desc = st.text_area(
                    "YouTube Description",
                    value=video.get("yt_description", ""),
                    height=120,
                    key=f"{key_prefix}_desc",
                )
                new_tags = st.text_input(
                    "Tags (comma-separated)",
                    value=_format_tags(video.get("yt_tags")),
                    key=f"{key_prefix}_tags",
                )
                new_tt_caption = st.text_area(
                    "TikTok Caption",
                    value=video.get("tt_caption", ""),
                    height=80,
                    key=f"{key_prefix}_tt",
                )

                # Metadata display
                st.caption(
                    f"Duration: {video.get('duration', '?'):.1f}s | "
                    f"Size: {video.get('file_size_mb', '?')} MB | "
                    f"Resolution: {video.get('resolution', '?')}"
                )

                # Script info
                script_row = conn.execute(
                    "SELECT category, hook, trend_source_ids FROM scripts WHERE id = ?",
                    (video.get("script_id"),),
                ).fetchone()
                if script_row:
                    st.caption(f"Category: {script_row['category']} | Hook: {script_row['hook'][:80]}")

                # Action buttons
                col_approve, col_reject = st.columns(2)

                with col_approve:
                    if st.button("Approve", key=f"{key_prefix}_approve", type="primary"):
                        # Save edits
                        tag_list = [t.strip() for t in new_tags.split(",") if t.strip()]
                        conn.execute(
                            """UPDATE videos SET yt_title=?, yt_description=?, yt_tags=?,
                               tt_caption=?, status='approved', reviewed_at=datetime('now')
                               WHERE id=?""",
                            (new_title, new_desc, json.dumps(tag_list), new_tt_caption, video_id),
                        )
                        conn.commit()
                        # Create upload records
                        insert_upload(conn, video_id=video_id, platform="youtube")
                        insert_upload(conn, video_id=video_id, platform="tiktok")
                        st.success(f"Video #{video_id} approved!")
                        st.rerun()

                with col_reject:
                    reason = st.text_input("Rejection reason", key=f"{key_prefix}_reason", placeholder="Optional")
                    if st.button("Reject", key=f"{key_prefix}_reject"):
                        update_video_status(conn, video_id, "rejected", rejection_reason=reason or "")
                        st.warning(f"Video #{video_id} rejected")
                        st.rerun()

    conn.close()


def render_stats():
    conn = get_connection()
    stats = get_stats(conn)
    conn.close()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Trends", stats.get("total_trends", 0))
    col2.metric("Scripts Generated", stats.get("total_scripts", 0))
    col3.metric("Videos Created", stats.get("total_videos", 0))
    col4.metric("Pending Review", stats.get("pending_review", 0))

    col5, col6, col7 = st.columns(3)
    col5.metric("Approved", stats.get("approved", 0))
    col6.metric("Uploaded", stats.get("uploaded", 0))
    col7.metric("Rejected", stats.get("rejected", 0))

    approval_rate = 0
    total = stats.get("approved", 0) + stats.get("rejected", 0)
    if total > 0:
        approval_rate = stats["approved"] / total * 100
    st.progress(approval_rate / 100, text=f"Approval rate: {approval_rate:.0f}%")


def render_history():
    conn = get_connection()

    st.subheader("Approved Videos")
    approved = get_videos_by_status(conn, "approved")
    if approved:
        for v in approved:
            st.write(f"**#{v['id']}** - {v.get('yt_title', 'Untitled')} ({v.get('duration', 0):.1f}s)")
    else:
        st.info("No approved videos yet")

    st.subheader("Uploaded Videos")
    uploaded = get_videos_by_status(conn, "uploaded")
    if uploaded:
        for v in uploaded:
            uploads = conn.execute(
                "SELECT * FROM uploads WHERE video_id = ?", (v["id"],)
            ).fetchall()
            links = []
            for u in uploads:
                if u["platform_url"]:
                    links.append(f"[{u['platform'].title()}]({u['platform_url']})")
            link_str = " | ".join(links) if links else "Uploaded"
            st.write(f"**#{v['id']}** - {v.get('yt_title', 'Untitled')} - {link_str}")
    else:
        st.info("No uploaded videos yet")

    st.subheader("Rejected Videos")
    rejected = get_videos_by_status(conn, "rejected")
    if rejected:
        for v in rejected:
            reason = v.get("rejection_reason") or "No reason given"
            st.write(f"~~#{v['id']} - {v.get('yt_title', 'Untitled')}~~ - {reason}")
    else:
        st.info("No rejected videos")

    conn.close()


def _format_tags(tags_json: str | None) -> str:
    if not tags_json:
        return ""
    try:
        tags = json.loads(tags_json)
        if isinstance(tags, list):
            return ", ".join(tags)
    except (json.JSONDecodeError, TypeError):
        pass
    return str(tags_json)


if __name__ == "__main__":
    main()

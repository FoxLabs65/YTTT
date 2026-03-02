"""Review page - enhanced video review with player, bulk actions, inline editing."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Ensure project root on path (Streamlit may run pages in isolation)
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from models.database import (
    get_connection, get_videos_by_status, update_video_status, insert_upload,
)
from agents.cleanup import cleanup_single_rejected_video
from ui.runner import get_runner
from agents.composer import generate_thumbnail
from agents.sourcing import search_pexels_images, download_pexels_image
from agents.music_scraper import scan_local_library
from ui.components import section_header, status_badge

PROJECT_ROOT = Path(__file__).parent.parent.parent
IMAGES_DIR = PROJECT_ROOT / "assets" / "images"


REJECTION_PRESETS = [
    "Audio/video desync",
    "Poor visual quality",
    "Caption timing off",
    "Wrong background music",
    "Script not engaging",
    "Inappropriate content",
    "Too short / too long",
    "Other",
]


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


def render():
    st.title("Review Queue")

    conn = get_connection()

    # ── Filters ─────────────────────────────────────────────────
    col_f1, col_f2, col_f3 = st.columns([1, 1, 2])
    with col_f1:
        view_status = st.selectbox("Status", ["pending", "approved", "rejected", "composed"], key="rev_status")
    with col_f2:
        cat_filter = st.selectbox("Category", ["All", "motivational", "funny", "meme", "news", "storytime", "reaction"], key="rev_cat")
    with col_f3:
        st.write("")

    videos = get_videos_by_status(conn, view_status)

    if cat_filter != "All":
        filtered = []
        for v in videos:
            row = conn.execute("SELECT category FROM scripts WHERE id = ?", (v.get("script_id"),)).fetchone()
            if row and row["category"] == cat_filter:
                filtered.append(v)
        videos = filtered

    if not videos:
        st.info(f"No {view_status} videos found.")
        conn.close()
        return

    st.caption(f"{len(videos)} videos")

    # ── Bulk Actions (pending only) ─────────────────────────────
    if view_status == "pending" and len(videos) > 1:
        section_header("Bulk Actions")
        col_b1, col_b2, col_b3 = st.columns(3)
        with col_b1:
            if st.button("Approve All", type="primary"):
                for v in videos:
                    _approve_video(conn, v["id"], v)
                st.toast(f"{len(videos)} videos approved!")
                st.rerun()
        with col_b2:
            bulk_reason = st.selectbox("Rejection reason", REJECTION_PRESETS, key="rev_bulk_reason")
        with col_b3:
            if st.button("Reject All"):
                pipeline_running = get_runner().is_running
                for v in videos:
                    update_video_status(conn, v["id"], "rejected", rejection_reason=bulk_reason)
                    cleanup_single_rejected_video(v["id"], defer_asset_cleanup=pipeline_running)
                st.toast(f"{len(videos)} videos rejected and cleaned up" + (" (asset cleanup deferred)" if pipeline_running else ""))
                st.rerun()
        st.divider()

    # ── Video Review Cards ──────────────────────────────────────
    for video in videos:
        vid = video["id"]
        key = f"rev_{vid}"
        editable = view_status in ("pending", "composed")

        with st.container():
            st.divider()
            col_player, col_meta = st.columns([1, 1])

            with col_player:
                video_path = Path(video["file_path"])
                if video_path.exists():
                    st.video(str(video_path))
                else:
                    st.warning(f"File not found: {video_path.name}")

                if video.get("thumbnail_path"):
                    thumb = Path(video["thumbnail_path"])
                    if thumb.exists():
                        st.image(str(thumb), caption="Thumbnail", width=250)

                # Thumbnail options
                if editable:
                    with st.expander("Change Thumbnail"):
                        tcol1, tcol2 = st.columns(2)
                        with tcol1:
                            if st.button("Extract from video start", key=f"{key}_thumb_video"):
                                vp = Path(video["file_path"])
                                if vp.exists():
                                    new_thumb = generate_thumbnail(vp, video.get("yt_title", ""), frame_time=0)
                                    if new_thumb:
                                        conn.execute(
                                            "UPDATE videos SET thumbnail_path = ? WHERE id = ?",
                                            (str(new_thumb), vid),
                                        )
                                        conn.commit()
                                        st.toast("Thumbnail updated from video start")
                                        st.rerun()
                                else:
                                    st.error("Video file not found")
                        with tcol2:
                            thumb_search = st.text_input("Search for image", key=f"{key}_thumb_search", placeholder="e.g. sunset motivation")
                            if thumb_search and st.button("Search & pick", key=f"{key}_thumb_search_btn"):
                                photos = search_pexels_images(thumb_search, count=6)
                                if photos:
                                    st.session_state[f"_thumb_photos_{vid}"] = photos
                                    st.rerun()
                                else:
                                    st.warning("No images found")
                        if f"_thumb_photos_{vid}" in st.session_state:
                            st.caption("Select an image to use as thumbnail")
                            photos = st.session_state[f"_thumb_photos_{vid}"][:6]
                            for row in range(0, len(photos), 3):
                                cols = st.columns(3)
                                for c, photo in enumerate(photos[row : row + 3]):
                                    with cols[c]:
                                        src = photo.get("src", {}).get("medium") or photo.get("src", {}).get("original")
                                        if src:
                                            st.image(src, width=120)
                                            idx = row + c
                                            if st.button("Use", key=f"{key}_thumb_use_{idx}"):
                                                IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                                                dl = download_pexels_image(photo, IMAGES_DIR)
                                                if dl:
                                                    new_thumb = generate_thumbnail(
                                                        Path(video["file_path"]),
                                                        video.get("yt_title", ""),
                                                        source_assets=[{"asset_type": "image", "local_path": str(dl)}],
                                                    )
                                                    if new_thumb:
                                                        conn.execute(
                                                            "UPDATE videos SET thumbnail_path = ? WHERE id = ?",
                                                            (str(new_thumb), vid),
                                                        )
                                                        conn.commit()
                                                        st.session_state.pop(f"_thumb_photos_{vid}", None)
                                                        st.toast("Thumbnail updated from search")
                                                        st.rerun()

            with col_meta:
                badge = status_badge(video.get("status", "unknown"))
                st.markdown(f"**Video #{vid}** {badge}", unsafe_allow_html=True)

                # Script info
                script_row = conn.execute(
                    "SELECT category, hook, script_body FROM scripts WHERE id = ?",
                    (video.get("script_id"),),
                ).fetchone()
                if script_row:
                    st.caption(f"Category: {script_row['category']}")
                    with st.expander("Script"):
                        st.markdown(f"**Hook:** {script_row['hook']}")
                        st.text(script_row["script_body"][:500])

                # Metadata
                dur = f"{video.get('duration', 0):.1f}s" if video.get("duration") else "?"
                size = f"{video.get('file_size_mb', 0):.1f} MB" if video.get("file_size_mb") else "?"
                st.caption(f"Duration: {dur} | Size: {size} | Resolution: {video.get('resolution', '?')}")

                # Editable fields
                new_title = st.text_input("YouTube Title", value=video.get("yt_title", ""), key=f"{key}_title", disabled=not editable)
                new_desc = st.text_area("YouTube Description", value=video.get("yt_description", ""), height=100, key=f"{key}_desc", disabled=not editable)
                new_tags = st.text_input("Tags (comma-separated)", value=_format_tags(video.get("yt_tags")), key=f"{key}_tags", disabled=not editable)
                new_tt = st.text_area("TikTok Caption", value=video.get("tt_caption", ""), height=60, key=f"{key}_tt", disabled=not editable)

                # Action buttons
                if editable:
                    col_a, col_r = st.columns(2)
                    with col_a:
                        if st.button("Approve", key=f"{key}_approve", type="primary", width="stretch"):
                            _approve_video(conn, vid, video, new_title, new_desc, new_tags, new_tt)
                            st.toast(f"Video #{vid} approved!")
                            st.rerun()
                    with col_r:
                        reason_idx = st.selectbox("Reason", REJECTION_PRESETS, key=f"{key}_reason_sel")
                        custom_reason = st.text_input("Custom reason", key=f"{key}_reason_txt", placeholder="Optional details...")
                        final_reason = custom_reason if custom_reason else reason_idx
                        if st.button("Reject", key=f"{key}_reject", width="stretch"):
                            update_video_status(conn, vid, "rejected", rejection_reason=final_reason)
                            pipeline_running = get_runner().is_running
                            cleanup_single_rejected_video(vid, defer_asset_cleanup=pipeline_running)
                            st.toast(f"Video #{vid} rejected and cleaned up" + (" (asset cleanup deferred)" if pipeline_running else ""))
                            st.rerun()

                # Change music, voice, regenerate
                if view_status in ("pending", "rejected", "composed"):
                    st.markdown("**Regenerate with changes**")
                    script_row = conn.execute(
                        "SELECT voice_override, music_override_path, force_ai_audio_override FROM scripts WHERE id = ?",
                        (video.get("script_id"),),
                    ).fetchone()
                    current_voice = script_row["voice_override"] if script_row else None
                    current_music = script_row["music_override_path"] if script_row else None

                    voices = _get_voice_list()
                    voice_idx = voices.index(current_voice) if current_voice in voices else 0
                    new_voice = st.selectbox(
                        "Voice", voices, index=voice_idx, key=f"{key}_voice",
                        help="Change voice for regeneration",
                    )

                    library = scan_local_library()
                    track_options = ["(use default)"]
                    track_paths = [None]
                    for mood, paths in sorted(library.items()):
                        for p in paths:
                            track_options.append(f"{mood} / {p.name}")
                            track_paths.append(str(p))
                    current_idx = 0
                    if current_music:
                        for i, p in enumerate(track_paths[1:], 1):
                            if p == current_music:
                                current_idx = i
                                break
                    new_music_sel = st.selectbox(
                        "Music track", track_options, index=current_idx,
                        key=f"{key}_music", help="Change music for regeneration",
                    )
                    new_music_path = track_paths[track_options.index(new_music_sel)] if new_music_sel != "(use default)" else None
                    use_default_music = new_music_sel == "(use default)"
                    force_ai_checked = st.checkbox(
                        "Use AI-generated music (Suno)",
                        value=bool(script_row.get("force_ai_audio_override") if script_row else False),
                        key=f"{key}_force_ai",
                        disabled=not use_default_music,
                        help="Force Suno AI music when using default. Ignored when a specific track is selected.",
                    )
                    force_ai_audio = use_default_music and force_ai_checked

                    if st.button("Regenerate Video", key=f"{key}_regen"):
                        script_id = video.get("script_id")
                        voice_val = new_voice if new_voice else None
                        conn.execute(
                            "UPDATE scripts SET voice_override = ?, music_override_path = ? WHERE id = ?",
                            (voice_val, new_music_path, script_id),
                        )
                        conn.commit()
                        extra = ["--script-id", str(script_id)]
                        if voice_val:
                            extra.extend(["--voice", voice_val])
                        if force_ai_audio and use_default_music:
                            extra.append("--force-ai-audio")
                        elif new_music_path:
                            extra.extend(["--music-path", new_music_path])
                        runner = get_runner()
                        runner.start("regenerate", extra)
                        st.toast("Regenerating video with new voice/music...")
                        st.rerun()

    # ── Keyboard Shortcuts hint ─────────────────────────────────
    if view_status == "pending":
        st.divider()
        st.caption("Tip: Use the Approve/Reject buttons on each card to process videos.")

    conn.close()


def _approve_video(
    conn, video_id: int, video: dict,
    title: str | None = None,
    desc: str | None = None,
    tags: str | None = None,
    tt: str | None = None,
):
    """Save edits and approve a video, creating upload records."""
    final_title = title or video.get("yt_title", "")
    final_desc = desc or video.get("yt_description", "")
    final_tt = tt or video.get("tt_caption", "")

    if tags is not None:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    else:
        tag_list = json.loads(video.get("yt_tags", "[]") or "[]")

    conn.execute(
        """UPDATE videos SET yt_title=?, yt_description=?, yt_tags=?,
           tt_caption=?, status='approved', reviewed_at=datetime('now')
           WHERE id=?""",
        (final_title, final_desc, json.dumps(tag_list), final_tt, video_id),
    )
    conn.commit()

    insert_upload(conn, video_id=video_id, platform="youtube")
    insert_upload(conn, video_id=video_id, platform="tiktok")


def _get_voice_list() -> list[str]:
    """Return list of edge-tts voices for the voice picker."""
    if "review_voices" not in st.session_state:
        try:
            import edge_tts
            voices = asyncio.run(edge_tts.list_voices())
            en = sorted([v["ShortName"] for v in voices if v["Locale"].startswith("en-")])
            st.session_state.review_voices = en if en else [
                "en-US-AndrewMultilingualNeural",
                "en-US-AvaMultilingualNeural",
                "en-US-BrianMultilingualNeural",
            ]
        except Exception:
            st.session_state.review_voices = [
                "en-US-AndrewMultilingualNeural",
                "en-US-AvaMultilingualNeural",
                "en-US-BrianMultilingualNeural",
            ]
    return st.session_state.review_voices

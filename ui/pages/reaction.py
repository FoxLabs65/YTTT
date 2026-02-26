"""Reaction Shorts - discover and review YouTube clip candidates (human-in-the-loop)."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from ui.components import section_header
from ui.runner import get_runner


def render():
    st.title("Reaction Shorts")
    st.caption("Optional: Discover long-form YouTube clips for reaction shorts. Human review required.")

    section_header("Disclaimer")
    st.warning(
        "Downloading YouTube content may violate YouTube ToS. Reaction/commentary may qualify as fair use. "
        "Use at your own risk. Review candidates before extraction."
    )

    try:
        from agents.reaction_sourcing import (
            discover_candidates,
            get_pending_review,
            get_approved,
            approve_candidates,
            run_extraction,
            CANDIDATES_FILE,
        )
    except ImportError as e:
        st.error(f"Reaction module not available: {e}")
        return

    section_header("1. Discover Candidates")
    st.markdown("Find long-form videos (gaming, podcasts, etc.) and extract candidate moments from chapters.")
    if st.button("Run Discovery", key="reaction_discover"):
        with st.spinner("Discovering candidates..."):
            result = discover_candidates(max_videos=10)
        st.success(f"Discovered {len(result)} candidates. Review below.")
        st.rerun()

    section_header("2. Review & Approve")
    pending = get_pending_review()
    approved_list = get_approved()

    if not pending and not approved_list:
        st.info("No candidates yet. Run Discovery above, or check data/reaction/candidates.json")
    else:
        if pending:
            st.markdown(f"**{len(pending)} pending review** — select to approve:")
            for c in pending:
                with st.expander(f"{c.get('title', '')[:60]}... | {c.get('channel', '')} | {c.get('duration_sec', 0)}s"):
                    st.write(f"**Video:** [{c.get('video_url', '')}]({c.get('video_url', '#')})")
                    st.write(f"**Clip:** {c.get('start_sec', 0)}s - {c.get('end_sec', 0)}s | {c.get('chapter_title', '')}")
                    if st.button("Approve", key=f"approve_{c.get('id', '')}"):
                        approve_candidates([c["id"]])
                        st.rerun()
        if approved_list:
            st.markdown(f"**{len(approved_list)} approved** — ready for extraction")

    section_header("3. Extract Clips")
    st.markdown("Download approved clips using yt-dlp. Requires yt-dlp and ffmpeg.")
    if approved_list and st.button("Extract Approved Clips", key="reaction_extract"):
        with st.spinner("Extracting clips..."):
            paths = run_extraction()
        st.success(f"Extracted {len(paths)} clips to assets/reaction_clips/")
        st.rerun()

    section_header("Data")
    if CANDIDATES_FILE.exists():
        st.code(str(CANDIDATES_FILE), language=None)
        st.caption("Edit this file to approve (set status to 'approved') or use the Approve buttons above.")
    else:
        st.caption("Candidates file will be created after first discovery run.")

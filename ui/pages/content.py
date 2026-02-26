"""Content Studio page - script browser, generate scripts, inline editing."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root on path (Streamlit may run pages in isolation)
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from models.config import load_config
from models.database import (
    get_connection, get_scripts_by_status, get_top_trends, get_top_tags,
    delete_script,
)
from ui.components import section_header, status_badge, static_log_viewer
from ui.runner import get_runner


ALL_STATUSES = [
    "pending_assets", "assets_ready", "composing", "composed",
    "approved", "rejected", "uploaded", "archived",
]


def render():
    st.title("Content Studio")

    conn = get_connection()
    runner = get_runner()
    cfg = load_config()

    # ── Generate Scripts ────────────────────────────────────────
    section_header("Generate Scripts")

    col_gen1, col_gen2, col_gen3, col_gen4, col_gen5 = st.columns([2, 1, 1, 1, 1])

    categories = cfg.get("ideation", {}).get("categories", ["motivational", "funny", "meme", "news", "storytime", "howto", "pov"])

    with col_gen1:
        category = st.selectbox("Category", ["auto (from trends)"] + categories, key="cs_category")
    with col_gen2:
        platform = st.selectbox("Scrape from", ["both", "youtube", "tiktok"], key="cs_platform", help="Which platforms to scrape for trends")
    with col_gen3:
        count = st.slider("Scripts to generate", 1, 10, int(cfg.get("ideation", {}).get("scripts_per_batch", 5)), key="cs_count")
    with col_gen4:
        if st.button("Generate", type="primary", disabled=runner.is_running, width="stretch"):
            extra = ["--count", str(count)]
            if category != "auto (from trends)":
                extra.extend(["--category", category])
            if platform != "both":
                extra.extend(["--platform", platform])
            runner.start("full_pipeline", extra)
            st.toast("Pipeline started for script generation!")
            st.rerun()
    with col_gen5:
        if runner.is_running and runner.task_name == "full_pipeline":
            if "_cs_stop_confirm" in st.session_state:
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Confirm Stop", type="primary", key="cs_stop_yes"):
                        runner.stop()
                        st.session_state.pop("_cs_stop_confirm", None)
                        st.toast("Pipeline stopped.")
                        st.rerun()
                with c2:
                    if st.button("Cancel", key="cs_stop_cancel"):
                        st.session_state.pop("_cs_stop_confirm", None)
                        st.rerun()
            elif st.button("Stop", type="secondary", width="stretch", help="Stop the running pipeline"):
                st.session_state["_cs_stop_confirm"] = True
                st.rerun()

    if runner.is_running and runner.task_name == "full_pipeline":
        with st.expander("Log (live in sidebar)", expanded=True):
            static_log_viewer(task_name="full_pipeline", lines=40)

    st.divider()

    # ── Style / Trend Selector ──────────────────────────────────
    section_header("Trending Inspiration")

    col_trends, col_tags = st.columns([2, 1])

    with col_trends:
        lookback = cfg.get("discovery", {}).get("trend_lookback_hours", 48)
        trends = get_top_trends(conn, limit=10, hours=lookback)
        if trends:
            for t in trends[:8]:
                score = f"{t.get('trend_score', 0):.1f}" if t.get('trend_score') else "?"
                st.markdown(
                    f"**{t.get('title', 'Untitled')[:60]}** "
                    f"<small style='color:#94a3b8'>— {t.get('platform')} · score {score} · "
                    f"{t.get('category', '?')}</small>",
                    unsafe_allow_html=True,
                )
        else:
            st.info("No trends. Run Discovery first.")

    with col_tags:
        st.markdown("**Tag Cloud**")
        tags = get_top_tags(conn, limit=20)
        if tags:
            tag_html = " ".join(
                f'<span style="display:inline-block;background:#1e3a5f;color:#7dd3fc;'
                f'padding:3px 8px;border-radius:10px;margin:2px;font-size:0.8rem">'
                f'{t["tag"]}</span>'
                for t in tags
            )
            st.markdown(tag_html, unsafe_allow_html=True)
        else:
            st.caption("No tags yet.")

    st.divider()

    # ── Script Browser ──────────────────────────────────────────
    section_header("Script Browser")

    col_f1, col_f2 = st.columns([1, 2])
    with col_f1:
        status_filter = st.selectbox("Filter by status", ["all"] + ALL_STATUSES, key="cs_status")
    with col_f2:
        search_text = st.text_input("Search scripts", key="cs_search", placeholder="Search title or body...")

    if status_filter == "all":
        scripts = []
        for s in ALL_STATUSES:
            scripts.extend(get_scripts_by_status(conn, s))
        scripts.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    else:
        scripts = get_scripts_by_status(conn, status_filter)

    if search_text:
        q = search_text.lower()
        scripts = [
            s for s in scripts
            if q in (s.get("title", "").lower()) or q in (s.get("script_body", "").lower())
        ]

    st.caption(f"{len(scripts)} scripts found")

    for script in scripts[:50]:
        sid = script["id"]
        key_prefix = f"cs_{sid}"

        with st.expander(
            f"#{sid} — {script.get('title', 'Untitled')[:60]} [{script.get('status')}]",
        ):
            badge_html = status_badge(script.get("status", "unknown"))
            st.markdown(
                f"{badge_html} <small style='color:#94a3b8'>"
                f"{script.get('category', '?')} · {script.get('created_at', '')[:16]}</small>",
                unsafe_allow_html=True,
            )

            # Editable fields (all scripts can be edited)
            new_title = st.text_input("Title", value=script.get("title", ""), key=f"{key_prefix}_title")
            new_hook = st.text_input("Hook", value=script.get("hook", ""), key=f"{key_prefix}_hook")
            new_body = st.text_area("Script Body", value=script.get("script_body", ""), height=150, key=f"{key_prefix}_body")
            new_cta = st.text_input("CTA", value=script.get("cta", ""), key=f"{key_prefix}_cta")

            tags_raw = script.get("suggested_tags", "[]")
            try:
                tags_list = json.loads(tags_raw) if isinstance(tags_raw, str) else (tags_raw or [])
            except (json.JSONDecodeError, TypeError):
                tags_list = []
            new_tags = st.text_input(
                "Tags (comma-separated)",
                value=", ".join(tags_list),
                key=f"{key_prefix}_tags",
            )

            # Visual cues
            cues_raw = script.get("visual_cues", "[]")
            try:
                cues = json.loads(cues_raw) if isinstance(cues_raw, str) else (cues_raw or [])
            except (json.JSONDecodeError, TypeError):
                cues = []
            if cues:
                st.caption(f"Visual cues: {', '.join(str(c) for c in cues[:8])}")

            # Action buttons: Save, Regenerate video, Delete
            col_save, col_regen, col_del = st.columns(3)
            with col_save:
                if st.button("Save Changes", key=f"{key_prefix}_save"):
                    tag_list = [t.strip() for t in new_tags.split(",") if t.strip()]
                    conn.execute(
                        """UPDATE scripts SET title=?, hook=?, script_body=?, cta=?,
                           suggested_tags=?, updated_at=? WHERE id=?""",
                        (new_title, new_hook, new_body, new_cta, json.dumps(tag_list),
                         datetime.utcnow().isoformat(), sid),
                    )
                    conn.commit()
                    st.toast(f"Script #{sid} updated!")
                    st.rerun()
            with col_regen:
                if st.button("Regenerate video", key=f"{key_prefix}_regen", disabled=runner.is_running,
                             help="Re-source assets and re-compose video with new audio and images"):
                    tag_list = [t.strip() for t in new_tags.split(",") if t.strip()]
                    conn.execute(
                        """UPDATE scripts SET title=?, hook=?, script_body=?, cta=?,
                           suggested_tags=?, updated_at=? WHERE id=?""",
                        (new_title, new_hook, new_body, new_cta, json.dumps(tag_list),
                         datetime.utcnow().isoformat(), sid),
                    )
                    conn.commit()
                    runner.start("regenerate", ["--script-id", str(sid)])
                    st.toast("Regenerating video with new assets...")
                    st.rerun()
            with col_del:
                if f"_cs_del_confirm_{sid}" in st.session_state:
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("Yes, delete", key=f"{key_prefix}_del_yes", type="primary"):
                            if delete_script(conn, sid):
                                st.session_state.pop(f"_cs_del_confirm_{sid}", None)
                                st.toast(f"Script #{sid} deleted.")
                                st.rerun()
                            else:
                                st.error("Failed to delete")
                    with c2:
                        if st.button("Cancel", key=f"{key_prefix}_del_no"):
                            st.session_state.pop(f"_cs_del_confirm_{sid}", None)
                            st.rerun()
                else:
                    if st.button("Delete", key=f"{key_prefix}_del", type="secondary"):
                        st.session_state[f"_cs_del_confirm_{sid}"] = True
                        st.rerun()

    conn.close()

    # Auto-refresh every 2s when pipeline or regenerate is running (must be last so page renders first)
    @st.fragment(run_every=2)
    def _content_refresh():
        r = get_runner()
        if r.is_running and r.task_name in ("full_pipeline", "regenerate"):
            st.rerun()

    _content_refresh()

"""Content Studio page - search criteria, scraping, script browser, trend browser."""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root on path (Streamlit may run pages in isolation)
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import streamlit as st

from models.config import load_config, save_config, reload_config
from models.database import (
    get_connection, get_scripts_by_status, get_top_trends, get_top_tags,
    get_trend_categories, purge_discovery_data, delete_script,
)
from ui.components import section_header, status_badge, static_log_viewer
from ui.runner import get_runner


ALL_STATUSES = [
    "pending_assets", "assets_ready", "composing", "composed",
    "approved", "rejected", "uploaded", "archived",
]

SCRIPT_CATS = ["motivational", "funny", "meme", "news", "storytime", "howto", "pov", "reaction"]


def _save_discovery_criteria(cfg: dict, yt_queries: list, tt_hashtags: list, categories: list | None = None):
    """Save discovery queries/hashtags and optionally ideation categories to config."""
    new_cfg = copy.deepcopy(cfg)
    if "discovery" not in new_cfg:
        new_cfg["discovery"] = {}
    new_cfg["discovery"]["youtube_queries"] = yt_queries
    new_cfg["discovery"]["tiktok_hashtags"] = tt_hashtags
    if categories is not None:
        if "ideation" not in new_cfg:
            new_cfg["ideation"] = {}
        new_cfg["ideation"]["categories"] = categories
    save_config(new_cfg)
    reload_config()


def render():
    st.title("Content Studio")

    conn = get_connection()
    runner = get_runner()
    cfg = load_config()
    disc = cfg.get("discovery", {})
    lookback = disc.get("trend_lookback_hours", 48)

    # ── Search Criteria (expanded) ─────────────────────────────────
    with st.expander("Search Criteria", expanded=True):
        st.caption("Choose which tags to use for each search. Add/delete below to manage your tag library.")
        col_yt, col_tt = st.columns(2)

        with col_yt:
            st.markdown("**YouTube Queries**")
            yt_queries = list(disc.get("youtube_queries", []))
            yt_selected = st.multiselect(
                "Select queries for this search",
                options=yt_queries,
                default=yt_queries,
                key="cs_yt_select",
                label_visibility="visible",
                help="Multi-select. Used when you click Scrape or Generate.",
            )
            if yt_queries:
                st.caption(f"✓ {len(yt_selected)} of {len(yt_queries)} selected")
            _yt_ok = st.button("✓ Apply selection", key="cs_yt_ok", help="Confirm your YouTube query selection")
            if _yt_ok:
                st.toast(f"YouTube: {len(yt_selected)} queries selected")
                st.rerun()

            st.caption("_Manage library_")
            new_q = st.text_input("Add YouTube query", key="cs_add_yt", placeholder="e.g. life hack shorts", label_visibility="collapsed")
            if new_q and st.button("Add query", key="cs_add_yt_btn"):
                new_yt = list(yt_queries) + [new_q.strip()]
                _save_discovery_criteria(cfg, new_yt, disc.get("tiktok_hashtags", []))
                st.rerun()

            with st.expander("Delete YouTube queries"):
                to_del_yt = st.multiselect("Select to delete", options=yt_queries, key="cs_yt_del_select")
                if to_del_yt and st.button("OK", key="cs_yt_del_ok"):
                    st.session_state["_cs_yt_confirm"] = to_del_yt
                    st.rerun()
                if "_cs_yt_confirm" in st.session_state:
                    pending = st.session_state["_cs_yt_confirm"]
                    st.warning(f"Delete {len(pending)} query(ies): {', '.join(pending)}?")
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("Confirm delete", key="cs_yt_confirm_del", type="primary"):
                            updated = [q for q in yt_queries if q not in pending]
                            _save_discovery_criteria(cfg, updated, disc.get("tiktok_hashtags", []))
                            st.session_state.pop("_cs_yt_confirm", None)
                            st.rerun()
                    with c2:
                        if st.button("Cancel", key="cs_yt_cancel_del"):
                            st.session_state.pop("_cs_yt_confirm", None)
                            st.rerun()

        with col_tt:
            st.markdown("**TikTok Hashtags**")
            tt_tags = [t if isinstance(t, str) else str(t) for t in disc.get("tiktok_hashtags", [])]
            tt_selected = st.multiselect(
                "Select hashtags for this search",
                options=tt_tags,
                default=tt_tags,
                key="cs_tt_select",
                label_visibility="visible",
                help="Multi-select. Used when you click Scrape or Generate.",
            )
            if tt_tags:
                st.caption(f"✓ {len(tt_selected)} of {len(tt_tags)} selected")
            _tt_ok = st.button("✓ Apply selection", key="cs_tt_ok", help="Confirm your TikTok hashtag selection")
            if _tt_ok:
                st.toast(f"TikTok: {len(tt_selected)} hashtags selected")
                st.rerun()

            st.caption("_Manage library_")
            new_tag = st.text_input("Add TikTok hashtag", key="cs_add_tt", placeholder="e.g. #trending", label_visibility="collapsed")
            if new_tag and st.button("Add hashtag", key="cs_add_tt_btn"):
                ht = new_tag.strip() if new_tag.strip().startswith("#") else f"#{new_tag.strip()}"
                new_tt = list(tt_tags) + [ht]
                _save_discovery_criteria(cfg, disc.get("youtube_queries", []), new_tt)
                st.rerun()

            with st.expander("Delete TikTok hashtags"):
                to_del_tt = st.multiselect("Select to delete", options=tt_tags, key="cs_tt_del_select")
                if to_del_tt and st.button("OK", key="cs_tt_del_ok"):
                    st.session_state["_cs_tt_confirm"] = to_del_tt
                    st.rerun()
                if "_cs_tt_confirm" in st.session_state:
                    pending = st.session_state["_cs_tt_confirm"]
                    st.warning(f"Delete {len(pending)} hashtag(s): {', '.join(pending)}?")
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("Confirm delete", key="cs_tt_confirm_del", type="primary"):
                            updated = [t for t in tt_tags if t not in pending]
                            _save_discovery_criteria(cfg, disc.get("youtube_queries", []), updated)
                            st.session_state.pop("_cs_tt_confirm", None)
                            st.rerun()
                    with c2:
                        if st.button("Cancel", key="cs_tt_cancel_del"):
                            st.session_state.pop("_cs_tt_confirm", None)
                            st.rerun()

        st.markdown("**Script Categories**")
        st.caption("Script styles the pipeline can generate. Discovery topics define what gets scraped.")
        ideation_cfg = cfg.get("ideation", {})
        current_cats = ideation_cfg.get("categories", SCRIPT_CATS)
        if not isinstance(current_cats, list):
            current_cats = SCRIPT_CATS
        current_cats = [c for c in current_cats if c in SCRIPT_CATS]
        selected_cats = []
        for cat in SCRIPT_CATS:
            if st.checkbox(cat.title(), value=cat in current_cats, key=f"cs_cat_{cat}"):
                selected_cats.append(cat)

        if st.button("Save Search Criteria", type="primary"):
            # Save full tag library (yt_queries, tt_tags), not selection — selection is per-run only
            _save_discovery_criteria(cfg, yt_queries, tt_tags, selected_cats)
            st.toast("Search criteria saved!")
            st.rerun()

    st.divider()

    # ── Actions: Scrape, Generate, Stop ─────────────────────────────
    section_header("Create")

    categories = cfg.get("ideation", {}).get("categories", SCRIPT_CATS)
    col_platform, col_scrape, col_gen1, col_gen2, col_gen3, col_stop = st.columns([1, 1, 2, 1, 1, 1])

    with col_platform:
        scrape_platform = st.selectbox("Scrape from", ["both", "youtube", "tiktok"], key="cs_scrape_platform", help="Platforms to scrape")
    with col_scrape:
        if st.button("Scrape Now", type="primary", disabled=runner.is_running, help="Scrape trends only; run Generate to create scripts"):
            _save_discovery_criteria(cfg, yt_queries, tt_tags, selected_cats)
            extra = ["--platform", scrape_platform] if scrape_platform != "both" else []
            # Pass selected tags (empty = use none for this run; omit = use full config)
            extra.extend(["--youtube-queries", ",".join(yt_selected)])
            extra.extend(["--tiktok-hashtags", ",".join(tt_selected)])
            runner.start("discovery", extra)
            st.toast("Discovery started!")
            st.rerun()
    with col_gen1:
        category = st.selectbox("Category", ["auto (from trends)"] + categories, key="cs_category")
    with col_gen2:
        count = st.slider("Count", 1, 10, int(cfg.get("ideation", {}).get("scripts_per_batch", 5)), key="cs_count")
    with col_gen3:
        if st.button("Generate", type="primary", disabled=runner.is_running, help="Scrape + ideate + source + compose"):
            _save_discovery_criteria(cfg, yt_queries, tt_tags, selected_cats)
            extra = ["--count", str(count)]
            if category != "auto (from trends)":
                extra.extend(["--category", category])
            if scrape_platform != "both":
                extra.extend(["--platform", scrape_platform])
            extra.extend(["--youtube-queries", ",".join(yt_selected)])
            extra.extend(["--tiktok-hashtags", ",".join(tt_selected)])
            runner.start("full_pipeline", extra)
            st.toast("Pipeline started!")
            st.rerun()
    with col_stop:
        if runner.is_running:
            if runner.task_name == "discovery":
                if "_cs_stop_confirm" in st.session_state:
                    if st.button("Confirm Stop & Purge", type="primary", key="cs_stop_yes"):
                        runner.stop()
                        conn_purge = get_connection()
                        trends_del, tags_del = purge_discovery_data(conn_purge)
                        conn_purge.close()
                        st.session_state.pop("_cs_stop_confirm", None)
                        st.toast(f"Stopped. Purged {trends_del} trends, {tags_del} tags.")
                        st.rerun()
                    if st.button("Cancel", key="cs_stop_cancel"):
                        st.session_state.pop("_cs_stop_confirm", None)
                        st.rerun()
                elif st.button("Stop & Purge", type="secondary", help="Stop discovery and delete trends/tags"):
                    st.session_state["_cs_stop_confirm"] = True
                    st.rerun()
            else:
                if "_cs_stop_confirm" in st.session_state:
                    if st.button("Confirm Stop", type="primary", key="cs_stop_yes"):
                        runner.stop()
                        st.session_state.pop("_cs_stop_confirm", None)
                        st.toast("Pipeline stopped.")
                        st.rerun()
                    if st.button("Cancel", key="cs_stop_cancel"):
                        st.session_state.pop("_cs_stop_confirm", None)
                        st.rerun()
                elif st.button("Stop", type="secondary", help="Stop the pipeline"):
                    st.session_state["_cs_stop_confirm"] = True
                    st.rerun()

    with st.expander("Live Log", expanded=runner.is_running):
        if runner.is_running:
            st.caption(f"Running... {runner.elapsed} — click **Refresh** for latest")
        elif runner.status in ("completed", "failed") and runner.log_path and runner.log_path.exists():
            st.caption(f"Finished: {runner.status} ({runner.elapsed})")
        static_log_viewer(task_name=None, lines=40)
        if st.button("Refresh", key="cs_log_refresh", help="Update log and page status"):
            st.rerun()

    st.divider()

    # ── Trending Inspiration (expanded) ─────────────────────────────
    with st.expander("Trending Inspiration", expanded=True):
        col_trends, col_tags = st.columns([2, 1])
        with col_trends:
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
                st.info("No trends. Scrape or Generate first.")
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

    # ── Trend Browser (collapsed) ──────────────────────────────────
    with st.expander("Browse all trends", expanded=False):
        trend_cats = get_trend_categories(conn, hours=lookback)
        cat_options = ["All"] + sorted(set(trend_cats) | set(SCRIPT_CATS)) if trend_cats else ["All"] + SCRIPT_CATS

        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            platform_filter = st.selectbox("Platform", ["All", "youtube", "tiktok"], key="cs_platform_filter")
        with col_f2:
            cat_filter = st.selectbox("Category", cat_options, key="cs_cat_filter")
        with col_f3:
            score_min = st.number_input("Min Trend Score", value=0.0, step=0.5, key="cs_score_min")

        all_trends = get_top_trends(conn, limit=100, hours=lookback)
        if platform_filter != "All":
            all_trends = [t for t in all_trends if t["platform"] == platform_filter]
        if cat_filter != "All":
            all_trends = [t for t in all_trends if (t.get("category") or "other") == cat_filter]
        if score_min > 0:
            all_trends = [t for t in all_trends if (t.get("trend_score") or 0) >= score_min]

        if all_trends:
            df = pd.DataFrame(all_trends)
            if "category" in df.columns:
                df = df.copy()
                df["category"] = df["category"].fillna("other")
            else:
                df["category"] = "other"
            display_cols = ["id", "platform", "title", "category", "trend_score", "view_count", "scraped_at"]
            available = [c for c in display_cols if c in df.columns]
            df_display = df[available].copy()
            if "trend_score" in df_display.columns:
                df_display["trend_score"] = df_display["trend_score"].round(2)
            st.dataframe(df_display, width="stretch", hide_index=True)

            selected_id = st.selectbox("Expand trend details", [None] + [t["id"] for t in all_trends], key="cs_trend_expand")
            if selected_id:
                t = next((x for x in all_trends if x["id"] == selected_id), None)
                if t:
                    st.json({
                        "title": t.get("title"),
                        "description": (t.get("description") or "")[:300],
                        "tags": json.loads(t["tags"]) if t.get("tags") else [],
                        "channel": t.get("channel_name"),
                        "views": t.get("view_count"),
                        "likes": t.get("like_count"),
                        "trend_score": t.get("trend_score"),
                        "platform": t.get("platform"),
                        "published": t.get("publish_date"),
                    })
        else:
            st.info("No trends. Scrape or Generate first.")

        st.markdown("**Trending Tags**")
        tags_full = get_top_tags(conn, limit=30)
        if tags_full:
            tag_html = " ".join(
                f'<span style="display:inline-block;background:#1e3a5f;color:#7dd3fc;'
                f'padding:4px 10px;border-radius:12px;margin:3px;font-size:0.85rem">'
                f'{t["tag"]} ({t["frequency"]})</span>'
                for t in tags_full
            )
            st.markdown(tag_html, unsafe_allow_html=True)
        else:
            st.caption("No tags yet.")

    st.divider()

    # ── Script Browser (expanded) ──────────────────────────────────
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

            cues_raw = script.get("visual_cues", "[]")
            try:
                cues = json.loads(cues_raw) if isinstance(cues_raw, str) else (cues_raw or [])
            except (json.JSONDecodeError, TypeError):
                cues = []
            if cues:
                st.caption(f"Visual cues: {', '.join(str(c) for c in cues[:8])}")

            # Select assets override (for pending_assets or before first sourcing)
            with st.expander("Select assets (override)", expanded=False):
                _ROOT = Path(__file__).resolve().parent.parent.parent
                _USER_VIDEO = _ROOT / "assets" / "stock_footage" / "user"
                _USER_IMAGE = _ROOT / "assets" / "images" / "user"
                user_videos = []
                if _USER_VIDEO.exists():
                    user_videos = [f for f in _USER_VIDEO.iterdir() if f.is_file() and f.suffix.lower() in (".mp4", ".mov", ".webm")]
                user_images = []
                if _USER_IMAGE.exists():
                    user_images = [f for f in _USER_IMAGE.iterdir() if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
                sel_raw = script.get("user_selected_asset_paths")
                try:
                    sel_list = json.loads(sel_raw) if isinstance(sel_raw, str) else (sel_raw or [])
                except (json.JSONDecodeError, TypeError):
                    sel_list = []
                sel_videos = [x["path"] for x in sel_list if x.get("type") == "video"]
                sel_images = [x["path"] for x in sel_list if x.get("type") == "image"]
                video_opts = {str(p): p.name for p in user_videos}
                image_opts = {str(p): p.name for p in user_images}
                sel_vid_names = {Path(p).name for p in sel_videos}
                sel_img_names = {Path(p).name for p in sel_images}
                default_videos = [k for k in video_opts.keys() if Path(k).name in sel_vid_names]
                default_images = [k for k in image_opts.keys() if Path(k).name in sel_img_names]
                if not user_videos and not user_images:
                    st.caption("Add videos to assets/stock_footage/user/, images to assets/images/user/, and music to assets/music/user/ to select or auto-score.")
                chosen_videos = st.multiselect(
                    "Videos (order = segment order)",
                    options=list(video_opts.keys()),
                    default=default_videos[:10],
                    format_func=lambda x: video_opts.get(x, Path(x).name),
                    key=f"{key_prefix}_sel_videos",
                )
                chosen_images = st.multiselect(
                    "Images (order = segment order)",
                    options=list(image_opts.keys()),
                    default=default_images[:5],
                    format_func=lambda x: image_opts.get(x, Path(x).name),
                    key=f"{key_prefix}_sel_images",
                )
                col_sel_save, col_sel_clear = st.columns(2)
                with col_sel_save:
                    if st.button("Save asset selection", key=f"{key_prefix}_sel_save"):
                        items = [{"type": "video", "path": p} for p in chosen_videos]
                        items += [{"type": "image", "path": p} for p in chosen_images]
                        rel_items = []
                        for it in items:
                            path = Path(it["path"])
                            try:
                                rel = path.relative_to(_ROOT)
                            except ValueError:
                                rel = path
                            rel_items.append({"type": it["type"], "path": rel.as_posix()})
                        conn.execute(
                            "UPDATE scripts SET user_selected_asset_paths=?, updated_at=? WHERE id=?",
                            (json.dumps(rel_items), datetime.utcnow().isoformat(), sid),
                        )
                        conn.commit()
                        st.toast("Asset selection saved. Run pipeline to use.")
                        st.rerun()
                with col_sel_clear:
                    if st.button("Clear selection", key=f"{key_prefix}_sel_clear"):
                        conn.execute(
                            "UPDATE scripts SET user_selected_asset_paths=NULL, updated_at=? WHERE id=?",
                            (datetime.utcnow().isoformat(), sid),
                        )
                        conn.commit()
                        st.toast("Asset selection cleared.")
                        st.rerun()

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

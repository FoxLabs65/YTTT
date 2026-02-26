"""Discovery page - dynamic search criteria, run discovery, trend browser."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure project root on path (Streamlit may run pages in isolation)
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import streamlit as st

from models.config import load_config, save_config, reload_config
from models.database import get_connection, get_top_trends, get_top_tags, get_trend_categories, purge_discovery_data
from ui.components import section_header, metric_card, log_viewer, static_log_viewer
from ui.runner import get_runner


def _save_discovery_criteria(cfg: dict, yt_queries: list, tt_hashtags: list, categories: list | None = None):
    """Save discovery queries/hashtags and optionally ideation categories to config."""
    import copy
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
    st.title("Discovery")

    conn = get_connection()
    runner = get_runner()

    # ── Search Criteria Manager ─────────────────────────────────
    section_header("Search Criteria")

    cfg = load_config()
    disc = cfg.get("discovery", {})

    col_yt, col_tt = st.columns(2)

    with col_yt:
        st.markdown("**YouTube Queries**")
        st.caption("Select which search terms to use when scraping YouTube.")
        yt_queries = list(disc.get("youtube_queries", []))
        yt_selected = st.multiselect(
            "Queries to use (checked = included in scrape)",
            options=yt_queries,
            default=yt_queries,
            key="disc_yt_select",
            label_visibility="collapsed",
        )
        new_yt = [q for q in yt_queries if q in yt_selected]

        new_q = st.text_input("Add YouTube query", key="disc_add_yt", placeholder="e.g. life hack shorts")
        if new_q and st.button("Add query", key="disc_add_yt_btn"):
            new_yt = list(yt_queries) + [new_q.strip()]
            _save_discovery_criteria(cfg, new_yt, disc.get("tiktok_hashtags", []))
            st.rerun()

        with st.expander("Delete YouTube queries"):
            to_del_yt = st.multiselect("Select queries to delete", options=yt_queries, key="disc_yt_del_select")
            if to_del_yt and st.button("OK", key="disc_yt_del_ok"):
                st.session_state["_disc_yt_confirm"] = to_del_yt
                st.rerun()
            if "_disc_yt_confirm" in st.session_state:
                pending = st.session_state["_disc_yt_confirm"]
                st.warning(f"Delete {len(pending)} query(ies): {', '.join(pending)}?")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Confirm delete", key="disc_yt_confirm_del", type="primary"):
                        updated = [q for q in yt_queries if q not in pending]
                        _save_discovery_criteria(cfg, updated, disc.get("tiktok_hashtags", []))
                        st.session_state.pop("_disc_yt_confirm", None)
                        st.rerun()
                with c2:
                    if st.button("Cancel", key="disc_yt_cancel_del"):
                        st.session_state.pop("_disc_yt_confirm", None)
                        st.rerun()

    with col_tt:
        st.markdown("**TikTok Hashtags**")
        st.caption("Select which hashtags to use when scraping TikTok.")
        tt_tags = [t if isinstance(t, str) else str(t) for t in disc.get("tiktok_hashtags", [])]
        tt_selected = st.multiselect(
            "Hashtags to use (checked = included in scrape)",
            options=tt_tags,
            default=tt_tags,
            key="disc_tt_select",
            label_visibility="collapsed",
        )
        new_tt = [t for t in tt_tags if t in tt_selected]

        new_tag = st.text_input("Add TikTok hashtag", key="disc_add_tt", placeholder="e.g. #trending")
        if new_tag and st.button("Add hashtag", key="disc_add_tt_btn"):
            ht = new_tag.strip() if new_tag.strip().startswith("#") else f"#{new_tag.strip()}"
            new_tt = list(tt_tags) + [ht]
            _save_discovery_criteria(cfg, disc.get("youtube_queries", []), new_tt)
            st.rerun()

        with st.expander("Delete TikTok hashtags"):
            to_del_tt = st.multiselect("Select hashtags to delete", options=tt_tags, key="disc_tt_del_select")
            if to_del_tt and st.button("OK", key="disc_tt_del_ok"):
                st.session_state["_disc_tt_confirm"] = to_del_tt
                st.rerun()
            if "_disc_tt_confirm" in st.session_state:
                pending = st.session_state["_disc_tt_confirm"]
                st.warning(f"Delete {len(pending)} hashtag(s): {', '.join(pending)}?")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Confirm delete", key="disc_tt_confirm_del", type="primary"):
                        updated = [t for t in tt_tags if t not in pending]
                        _save_discovery_criteria(cfg, disc.get("youtube_queries", []), updated)
                        st.session_state.pop("_disc_tt_confirm", None)
                        st.rerun()
                with c2:
                    if st.button("Cancel", key="disc_tt_cancel_del"):
                        st.session_state.pop("_disc_tt_confirm", None)
                        st.rerun()

    # Script Categories (for script generation, not scraping)
    st.markdown("**Script Categories**")
    st.caption(
        "Select which *script styles* the pipeline can generate. "
        "Discovery topics (YouTube queries / TikTok hashtags above) define what gets scraped. "
        "Script categories define the style of scripts written from those trends."
    )
    script_cats = ["motivational", "funny", "meme", "news", "storytime", "howto", "pov"]
    ideation_cfg = cfg.get("ideation", {})
    current_cats = ideation_cfg.get("categories", script_cats)
    if not isinstance(current_cats, list):
        current_cats = script_cats
    # Filter to only allowed script categories (ignore legacy custom entries like gaming)
    current_cats = [c for c in current_cats if c in script_cats]
    selected_cats = []
    for cat in script_cats:
        if st.checkbox(cat.title(), value=cat in current_cats, key=f"disc_cat_{cat}"):
            selected_cats.append(cat)

    # Save criteria
    if st.button("Save Search Criteria", type="primary"):
        _save_discovery_criteria(cfg, new_yt, new_tt, selected_cats)
        st.toast("Search criteria saved!")
        st.rerun()

    st.divider()

    # ── Run Discovery ───────────────────────────────────────────
    section_header("Run Discovery")
    col_platform, col_run, col_stop, col_status = st.columns([1, 1, 1, 2])
    with col_platform:
        scrape_platform = st.selectbox("Scrape from", ["both", "youtube", "tiktok"], key="disc_scrape_platform", help="Which platforms to scrape")
    with col_run:
        if st.button("Scrape Now", type="primary", disabled=runner.is_running, width="stretch"):
            # Auto-save current selection so backend uses it (config is read fresh by subprocess)
            _save_discovery_criteria(cfg, new_yt, new_tt, selected_cats)
            extra = ["--platform", scrape_platform] if scrape_platform != "both" else []
            runner.start("discovery", extra)
            st.toast("Search criteria saved and discovery started!")
            st.rerun()
    with col_stop:
        if runner.is_running and runner.task_name == "discovery":
            if "_disc_stop_confirm" in st.session_state:
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Confirm", type="primary", key="disc_stop_yes"):
                        runner.stop()
                        conn_purge = get_connection()
                        trends_del, tags_del = purge_discovery_data(conn_purge)
                        conn_purge.close()
                        st.session_state.pop("_disc_stop_confirm", None)
                        st.toast(f"Stopped. Purged {trends_del} trends and {tags_del} tags.")
                        st.rerun()
                with c2:
                    if st.button("Cancel", key="disc_stop_cancel"):
                        st.session_state.pop("_disc_stop_confirm", None)
                        st.rerun()
            elif st.button("Stop & Purge", type="secondary", width="stretch", help="Stop discovery and delete all trends/tags"):
                st.session_state["_disc_stop_confirm"] = True
                st.rerun()

    # Live log with elapsed time (auto-refreshes every 2s when running)
    if runner.is_running and runner.task_name == "discovery":
        with st.expander("Live Log", expanded=True):
            static_log_viewer(task_name="discovery", lines=40)

    st.divider()

    # ── Trend Browser ───────────────────────────────────────────
    section_header("Trend Browser")

    lookback = cfg.get("discovery", {}).get("trend_lookback_hours", 48)

    # Category filter: use actual categories from trends (includes "other" for uncategorized)
    trend_cats = get_trend_categories(conn, hours=lookback)
    cat_options = ["All"] + sorted(set(trend_cats) | set(script_cats)) if trend_cats else ["All"] + script_cats

    col_filter1, col_filter2, col_filter3 = st.columns(3)
    with col_filter1:
        platform_filter = st.selectbox("Platform", ["All", "youtube", "tiktok"], key="disc_platform")
    with col_filter2:
        cat_filter = st.selectbox("Category", cat_options, key="disc_cat_filter")
    with col_filter3:
        score_min = st.number_input("Min Trend Score", value=0.0, step=0.5, key="disc_score_min")

    trends = get_top_trends(conn, limit=100, hours=lookback)

    if platform_filter != "All":
        trends = [t for t in trends if t["platform"] == platform_filter]
    if cat_filter != "All":
        trends = [t for t in trends if (t.get("category") or "other") == cat_filter]
    if score_min > 0:
        trends = [t for t in trends if (t.get("trend_score") or 0) >= score_min]

    if trends:
        df = pd.DataFrame(trends)
        # Ensure category column exists (fill nulls with "other")
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

        # Expandable detail for selected trend
        selected_id = st.selectbox("Expand trend details", [None] + [t["id"] for t in trends], key="disc_expand")
        if selected_id:
            t = next((x for x in trends if x["id"] == selected_id), None)
            if t:
                with st.expander(f"Trend #{t['id']}: {t.get('title', '')[:60]}", expanded=True):
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
        st.info("No trends found. Run discovery to scrape trending content.")

    st.divider()

    # ── Trending Tags ───────────────────────────────────────────
    section_header("Trending Tags")
    tags = get_top_tags(conn, limit=30)
    if tags:
        tag_html = " ".join(
            f'<span style="display:inline-block;background:#1e3a5f;color:#7dd3fc;'
            f'padding:4px 10px;border-radius:12px;margin:3px;font-size:0.85rem">'
            f'{t["tag"]} ({t["frequency"]})</span>'
            for t in tags
        )
        st.markdown(tag_html, unsafe_allow_html=True)
    else:
        st.info("No trending tags yet.")

    conn.close()

    # Auto-refresh every 2s when discovery is running (must be last so page renders first)
    @st.fragment(run_every=2)
    def _discovery_refresh():
        r = get_runner()
        if r.is_running and r.task_name == "discovery":
            st.rerun()

    _discovery_refresh()

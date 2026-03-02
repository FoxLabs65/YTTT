"""Setup page - guided configuration editor with categorised forms."""

from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path

# Ensure project root on path (Streamlit may run pages in isolation)
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from models.config import load_config, save_config, reload_config, config_exists, _CONFIG_PATH, _EXAMPLE_PATH

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _get_editable_config() -> dict:
    """Load a deep copy of the config for editing."""
    if "edit_config" not in st.session_state:
        try:
            st.session_state.edit_config = copy.deepcopy(load_config())
        except FileNotFoundError:
            import yaml
            with open(_EXAMPLE_PATH, "r", encoding="utf-8") as f:
                st.session_state.edit_config = yaml.safe_load(f) or {}
    return st.session_state.edit_config


def _save():
    cfg = st.session_state.edit_config
    save_config(cfg)
    reload_config()
    st.toast("Configuration saved!")


def _set(cfg: dict, dotted_key: str, value):
    keys = dotted_key.split(".")
    node = cfg
    for k in keys[:-1]:
        if k not in node or not isinstance(node[k], dict):
            node[k] = {}
        node = node[k]
    node[keys[-1]] = value


def render():
    st.title("Setup")

    if not config_exists():
        st.warning(
            "No `settings.yaml` found. A new one will be created from the example template "
            "when you save. Fill in your API keys below to get started."
        )

    cfg = _get_editable_config()

    # ── API Keys ────────────────────────────────────────────────
    with st.expander("API Keys", expanded=True):
        st.caption("Keys are stored locally in config/settings.yaml and never transmitted.")

        col1, col2 = st.columns(2)
        with col1:
            val = st.text_input(
                "Pexels API Key", value=cfg.get("pexels_api_key", ""),
                type="password", key="cfg_pexels",
            )
            _set(cfg, "pexels_api_key", val)

            val = st.text_input(
                "Anthropic API Key", value=cfg.get("anthropic_api_key", ""),
                type="password", key="cfg_anthropic",
            )
            _set(cfg, "anthropic_api_key", val)

            val = st.text_input(
                "Coverr API Key",
                value=cfg.get("coverr_api_key", ""),
                type="password", key="cfg_coverr",
                help="Optional. Free stock videos. See instructions below.",
            )
            _set(cfg, "coverr_api_key", val)

            val = st.text_input(
                "Unsplash Access Key",
                value=cfg.get("unsplash_access_key", ""),
                type="password", key="cfg_unsplash",
                help="Optional. Free stock photos. See instructions below.",
            )
            _set(cfg, "unsplash_access_key", val)

            val = st.text_input(
                "Freesound API Key",
                value=cfg.get("freesound_api_key", ""),
                type="password", key="cfg_freesound",
                help="Optional. Get a free key at https://freesound.org/apiv2/apply/",
            )
            _set(cfg, "freesound_api_key", val)

        with col2:
            val = st.text_input(
                "Pixabay API Key", value=cfg.get("pixabay_api_key", ""),
                type="password", key="cfg_pixabay",
            )
            _set(cfg, "pixabay_api_key", val)

            val = st.text_input(
                "Gemini API Key (fallback LLM)",
                value=cfg.get("gemini_api_key", ""),
                type="password", key="cfg_gemini",
                help="Free at https://aistudio.google.com/apikey",
            )
            _set(cfg, "gemini_api_key", val)

        st.caption("**Openverse** — No API key required. Uses anonymous access for images and music.")

        # API connection tests
        st.markdown("**Test Connections**")
        tcols = st.columns(7)
        with tcols[0]:
            if st.button("Test Pexels", key="test_pexels"):
                _test_pexels(cfg.get("pexels_api_key", ""))
        with tcols[1]:
            if st.button("Test Pixabay", key="test_pixabay"):
                _test_pixabay(cfg.get("pixabay_api_key", ""))
        with tcols[2]:
            if st.button("Test Coverr", key="test_coverr"):
                _test_coverr(cfg.get("coverr_api_key", ""))
        with tcols[3]:
            if st.button("Test Unsplash", key="test_unsplash"):
                _test_unsplash(cfg.get("unsplash_access_key", ""))
        with tcols[4]:
            if st.button("Test Anthropic", key="test_anthropic"):
                _test_anthropic(cfg.get("anthropic_api_key", ""))
        with tcols[5]:
            if st.button("Test Gemini", key="test_gemini"):
                _test_gemini(cfg.get("gemini_api_key", ""))
        with tcols[6]:
            if st.button("Test Freesound", key="test_freesound"):
                _test_freesound(cfg.get("freesound_api_key", ""))

        with st.expander("How to get API keys (step-by-step)"):
            _render_api_key_instructions()

    # ── Category Checklist ───────────────────────────────────────
    with st.expander("Category Checklist", expanded=False):
        st.caption(
            "Ensures each ideation category is configured across discovery, music, and voiceover. "
            "Missing entries cause fallbacks (e.g. trends → 'other', music → storytime)."
        )
        try:
            from config.validation import validate_category_config

            cat_warnings = validate_category_config()
            if cat_warnings:
                for w in cat_warnings:
                    st.warning(w)
                st.info(
                    "Add missing categories to: "
                    "`agents/discovery.py` (_CATEGORY_KEYWORDS), "
                    "`agents/music_scraper.py` (CATEGORY_MUSIC_MAP), "
                    "and `config/settings.yaml` (music_moods, voiceover_voices)."
                )
            else:
                st.success("All ideation categories are fully configured.")
        except Exception as e:
            st.caption(f"Check skipped: {e}")

    # ── YouTube OAuth ───────────────────────────────────────────
    with st.expander("YouTube OAuth"):
        secret_path = Path(cfg.get("youtube_client_secret", "config/client_secret.json"))
        token_path = Path(cfg.get("youtube_token", "config/token.json"))

        col1, col2 = st.columns(2)
        with col1:
            if secret_path.exists():
                st.success(f"client_secret.json found at `{secret_path}`")
            else:
                st.warning("client_secret.json not found. Upload it below.")
                uploaded = st.file_uploader("Upload client_secret.json", type="json", key="yt_secret_upload")
                if uploaded:
                    target = PROJECT_ROOT / "config" / "client_secret.json"
                    target.write_bytes(uploaded.read())
                    st.success("Saved!")
                    st.rerun()
        with col2:
            if token_path.exists():
                st.success("OAuth token exists (authenticated)")
            else:
                st.info("No token yet. Run `python main.py --setup` to complete OAuth flow.")

    # ── TikTok ──────────────────────────────────────────────────
    with st.expander("TikTok"):
        tiktok = cfg.get("tiktok", {})
        if not isinstance(tiktok, dict):
            tiktok = {}
            cfg["tiktok"] = tiktok

        enabled = st.toggle("Enable TikTok uploads", value=tiktok.get("enabled", False), key="cfg_tt_enabled")
        tiktok["enabled"] = enabled

        col1, col2 = st.columns(2)
        with col1:
            val = st.text_input("Client Key", value=tiktok.get("client_key", ""), type="password", key="cfg_tt_key")
            tiktok["client_key"] = val
        with col2:
            val = st.text_input("Client Secret", value=tiktok.get("client_secret", ""), type="password", key="cfg_tt_secret")
            tiktok["client_secret"] = val

        redirect_uri = tiktok.get("redirect_uri", "http://localhost:8765/callback")
        with st.expander("Advanced (redirect URI)", expanded=False):
            val = st.text_input("Redirect URI", value=redirect_uri, key="cfg_tt_redirect", help="Must match exactly what you registered in TikTok Developer Portal")
            tiktok["redirect_uri"] = val

        # OAuth: Content Posting API requires user access token, not client_key
        from agents.tiktok_auth import has_tiktok_token, run_oauth_flow
        if has_tiktok_token():
            st.success("TikTok OAuth: connected")
        else:
            st.info("Content Posting API requires OAuth. Add redirect_uri `http://localhost:8765/callback` in TikTok Developer Portal, then click Connect.")
            if st.button("Connect TikTok", key="cfg_tt_oauth"):
                with st.spinner("Opening browser... Complete authorization there, then return here. (Times out after 90s if you close the tab.)"):
                    if run_oauth_flow():
                        st.success("TikTok connected! You can enable uploads above.")
                        st.rerun()
                    else:
                        st.error("OAuth failed or timed out. Check Client Key/Secret and redirect_uri in Developer Portal. Try again if you closed the browser.")

    # ── Voiceover ───────────────────────────────────────────────
    with st.expander("Voiceover"):
        sourcing = cfg.get("sourcing", {})
        if not isinstance(sourcing, dict):
            sourcing = {}
            cfg["sourcing"] = sourcing

        voices_map = sourcing.get("voiceover_voices", {})
        if not isinstance(voices_map, dict):
            voices_map = {}
            sourcing["voiceover_voices"] = voices_map

        available_voices = _get_voice_list()
        st.caption("Select a voice for each content category.")

        # Always show all supported categories (not just ideation.categories) so users can
        # configure voices for every category. Merge: ideation categories + existing
        # voiceover_voices keys + canonical list.
        ideation_cats = set(cfg.get("ideation", {}).get("categories") or [])
        existing_voices = set((cfg.get("sourcing", {}).get("voiceover_voices") or {}).keys())
        canonical = ["motivational", "funny", "meme", "news", "storytime", "howto", "pov", "reaction"]
        voice_cats = list(dict.fromkeys(canonical + [c for c in ideation_cats | existing_voices if c not in canonical]))
        for cat in voice_cats:
            current = voices_map.get(cat, "en-US-AndrewMultilingualNeural")
            idx = available_voices.index(current) if current in available_voices else 0
            val = st.selectbox(f"{cat.title()} voice", available_voices, index=idx, key=f"cfg_voice_{cat}")
            voices_map[cat] = val

        st.markdown("**Voice variety**")
        pool = sourcing.get("voiceover_voice_pool") or []
        if not isinstance(pool, list):
            pool = []
        pool_default = [v for v in pool if v in available_voices]
        pool_selected = st.multiselect(
            "Voice pool (rotate randomly across scripts)",
            options=available_voices,
            default=pool_default,
            key="cfg_voice_pool",
            help="When set, each script gets a random voice from this list instead of per-category. Leave empty to use per-category voices above.",
        )
        sourcing["voiceover_voice_pool"] = pool_selected if pool_selected else []

        with st.expander("Natural voices & alternatives"):
            st.markdown("""
**Edge-TTS (built-in)** — Free Microsoft neural voices. Recommended natural-sounding options:
- `en-US-AvaMultilingualNeural` — warm, conversational
- `en-US-AndrewMultilingualNeural` — clear, neutral
- `en-US-EmmaMultilingualNeural` — expressive
- `en-US-BrianMultilingualNeural` — friendly, casual
- `en-GB-SoniaNeural` — British, polished
- `en-GB-RyanNeural` — British, authoritative

Use **Voice pool** above to rotate through multiple voices for variety within a pipeline run.

**Alternative TTS providers** (not built-in) — For more natural/human-like voices, consider:
- **ElevenLabs** — High-quality, many voices; paid API
- **Play.ht** — Natural voices; paid
- **Google Cloud TTS** — Neural2 voices; pay-per-use

These would require custom integration. Edge-TTS is free and works out of the box.
""")

        rate = sourcing.get("voiceover_rate", "-5%")
        val = st.slider(
            "Speech rate (%)", min_value=-50, max_value=50,
            value=int(rate.replace("%", "").replace("+", "")),
            key="cfg_vo_rate",
            help="Negative = slower, positive = faster",
        )
        sourcing["voiceover_rate"] = f"{val:+d}%"

    # ── Suno AI Music ───────────────────────────────────────────
    with st.expander("Suno AI Music"):
        st.caption("Generate background music with Suno AI. Uses script metadata when custom prompt is empty.")
        music = cfg.get("sourcing", {}).get("music") or {}
        if not isinstance(music, dict):
            music = {}
        if "sourcing" not in cfg:
            cfg["sourcing"] = {}
        if "music" not in cfg["sourcing"]:
            cfg["sourcing"]["music"] = {}

        suno_key = st.text_input(
            "Suno API Key",
            value=music.get("suno_api_key", ""),
            type="password",
            key="cfg_suno_key",
            help="Get key at https://sunoapi.org",
        )
        _set(cfg, "sourcing.music.suno_api_key", suno_key)

        val = st.checkbox("Enable Suno", value=music.get("suno_enabled") is True, key="cfg_suno_enabled")
        _set(cfg, "sourcing.music.suno_enabled", val)

        col1, col2 = st.columns(2)
        with col1:
            model_opts = ["V4", "V4_5", "V4_5PLUS", "V4_5ALL", "V5"]
            current = music.get("suno_model", "V4_5ALL")
            idx = model_opts.index(current) if current in model_opts else 3
            val = st.selectbox("Model", model_opts, index=idx, key="cfg_suno_model")
            _set(cfg, "sourcing.music.suno_model", val)
            val = st.checkbox("Instrumental only", value=music.get("suno_instrumental", True), key="cfg_suno_instrumental")
            _set(cfg, "sourcing.music.suno_instrumental", val)
            val = st.checkbox("Custom mode", value=music.get("suno_custom_mode") is True, key="cfg_suno_custom_mode")
            _set(cfg, "sourcing.music.suno_custom_mode", val)
        with col2:
            w_val = int(music.get("suno_weirdness", 50))
            val = st.slider("Weirdness (0-100)", 0, 100, w_val, key="cfg_suno_weirdness")
            _set(cfg, "sourcing.music.suno_weirdness", val)
            sw_val = int(music.get("suno_style_weight", 65))
            val = st.slider("Style weight (0-100)", 0, 100, sw_val, key="cfg_suno_style_weight")
            _set(cfg, "sourcing.music.suno_style_weight", val)

        val = st.text_area(
            "Custom prompt",
            value=music.get("suno_prompt_override", ""),
            height=80,
            key="cfg_suno_prompt",
            help="Override auto prompt; leave empty to use script-derived prompt from mood, keywords, tags.",
        )
        _set(cfg, "sourcing.music.suno_prompt_override", val)

        val = st.text_input(
            "Style prompt",
            value=music.get("suno_style_override", ""),
            key="cfg_suno_style",
            help="Genre/style (e.g. Cinematic orchestral); required in custom mode.",
        )
        _set(cfg, "sourcing.music.suno_style_override", val)

        val = st.text_input(
            "Title",
            value=music.get("suno_title_override", ""),
            key="cfg_suno_title",
            help="Track title for custom mode; auto ShortsBg_{mood} if empty.",
        )
        _set(cfg, "sourcing.music.suno_title_override", val)

        val = st.text_input(
            "Exclusion prompt",
            value=music.get("suno_exclusion_override", ""),
            key="cfg_suno_exclusion",
            help="Styles to exclude (e.g. Heavy Metal, Upbeat Drums).",
        )
        _set(cfg, "sourcing.music.suno_exclusion_override", val)

        val = st.checkbox(
            "Force AI audio (always use Suno, ignore stock)",
            value=music.get("force_ai_audio") is True,
            key="cfg_force_ai_audio",
            help="When enabled, always try Suno first and skip stock scoring. Use to prefer AI-generated music.",
        )
        _set(cfg, "sourcing.music.force_ai_audio", val)

        thresh_val = float(music.get("stock_score_threshold", 0.5))
        val = st.number_input(
            "Stock score threshold (0.0–1.0)",
            min_value=0.0,
            max_value=1.0,
            value=thresh_val,
            step=0.1,
            key="cfg_stock_threshold",
            help="If best stock score >= this, use stock and skip Suno. Lower = more likely to use Suno.",
        )
        _set(cfg, "sourcing.music.stock_score_threshold", val)

        if st.button("Test Suno", key="test_suno"):
            _test_suno(suno_key)

    # ── Video Composition ───────────────────────────────────────
    with st.expander("Video Composition"):
        composer = cfg.get("composer", {})
        if not isinstance(composer, dict):
            composer = {}
            cfg["composer"] = composer

        font_options = ["segoe-ui-bold", "impact", "arial-bold", "calibri-bold"]
        current_font = composer.get("font", "segoe-ui-bold")
        if current_font not in font_options:
            font_options.append(current_font)
        val = st.selectbox("Caption Font", font_options, index=font_options.index(current_font), key="cfg_font")
        composer["font"] = val

        col1, col2 = st.columns(2)
        with col1:
            val = st.slider("Music Volume", 0.0, 1.0, float(composer.get("music_volume", 0.05)), 0.05, key="cfg_music_vol")
            composer["music_volume"] = val
            val = st.number_input("Caption Font Size", 20, 100, int(composer.get("caption_font_size", 48)), key="cfg_cap_size")
            composer["caption_font_size"] = val
        with col2:
            val = st.number_input("Hook Font Size", 30, 120, int(composer.get("hook_font_size", 72)), key="cfg_hook_size")
            composer["hook_font_size"] = val
            val = st.number_input("FPS", 15, 60, int(composer.get("fps", 30)), key="cfg_fps")
            composer["fps"] = val

        val = st.text_input("Watermark Text", value=composer.get("watermark_text", ""), key="cfg_watermark")
        composer["watermark_text"] = val

    # ── Upload Defaults ─────────────────────────────────────────
    with st.expander("Upload Defaults"):
        upload = cfg.get("upload", {})
        if not isinstance(upload, dict):
            upload = {}
            cfg["upload"] = upload

        st.caption("Delay between uploads prevents YouTube/TikTok algorithm penalty. Scheduler never auto-uploads.")
        val = st.number_input(
            "Minutes between uploads",
            0, 120,
            int(upload.get("delay_minutes_between", 15)),
            key="cfg_upload_delay",
            help="Wait this long between each upload. Recommended 15–30.",
        )
        upload["delay_minutes_between"] = val

        yt = upload.get("youtube", {})
        if not isinstance(yt, dict):
            yt = {}
            upload["youtube"] = yt

        val = st.selectbox(
            "YouTube Category",
            ["22 (People & Blogs)", "24 (Entertainment)", "23 (Comedy)", "25 (News & Politics)", "26 (Howto & Style)", "27 (Education)"],
            index=0, key="cfg_yt_cat",
        )
        yt["category_id"] = val.split(" ")[0]

        val = st.selectbox("Privacy Status", ["public", "unlisted", "private"], index=["public", "unlisted", "private"].index(yt.get("privacy_status", "public")), key="cfg_yt_priv")
        yt["privacy_status"] = val

        tags_str = ", ".join(yt.get("default_tags", ["shorts", "viral", "trending"]))
        val = st.text_input("Default Tags (comma-separated)", value=tags_str, key="cfg_yt_tags")
        yt["default_tags"] = [t.strip() for t in val.split(",") if t.strip()]

        val = st.text_area("Description Footer", value=yt.get("description_footer", ""), height=80, key="cfg_yt_footer")
        yt["description_footer"] = val

    # ── Pipeline Settings ───────────────────────────────────────
    with st.expander("Pipeline Settings"):
        pipeline = cfg.get("pipeline", {})
        if not isinstance(pipeline, dict):
            pipeline = {}
            cfg["pipeline"] = pipeline

        val = st.slider("Max Retries Per Phase", 1, 10, int(pipeline.get("max_retries", 3)), key="cfg_retries")
        pipeline["max_retries"] = val

        delays = pipeline.get("retry_delays", [30, 60, 120])
        val = st.text_input("Retry Delays (seconds, comma-separated)", value=", ".join(str(d) for d in delays), key="cfg_delays")
        try:
            pipeline["retry_delays"] = [int(d.strip()) for d in val.split(",") if d.strip()]
        except ValueError:
            st.error("Delays must be comma-separated integers")

        val = st.toggle("Ideation Bonus Retry", value=pipeline.get("ideation_bonus_retry", True), key="cfg_bonus")
        pipeline["ideation_bonus_retry"] = val

    # ── Rejection Insights ─────────────────────────────────────
    with st.expander("Rejection Insights"):
        st.caption("Script-related rejection counts by category. Consider updating templates or enabling rejection-guided ideation.")
        try:
            from models.database import get_connection, get_rejection_insights_by_category

            conn = get_connection()
            insights = get_rejection_insights_by_category(conn)
            conn.close()
            if insights:
                for cat, items in sorted(insights.items()):
                    parts = [f"{r} ({c})" for r, c in items]
                    st.text(f"{cat}: {', '.join(parts)}")
            else:
                st.info("No script-related rejections recorded yet.")
        except Exception as e:
            st.caption(f"Could not load insights: {e}")

    # ── Script Templates ────────────────────────────────────────
    with st.expander("Script Templates"):
        st.caption("Restore default templates if you've edited them and want to revert.")
        try:
            from config.validation import TEMPLATE_DIR, TEMPLATE_DEFAULTS_DIR, revert_template_to_default

            templates = sorted(p.stem for p in TEMPLATE_DIR.glob("*.txt"))
            for cat in templates:
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.text(f"{cat}.txt")
                with col2:
                    if st.button("Restore default", key=f"revert_{cat}"):
                        if revert_template_to_default(cat):
                            st.toast(f"Restored {cat}.txt from default")
                            st.rerun()
                        else:
                            st.error(f"No default found for {cat}")
            if not templates:
                st.info("No templates found in config/templates/scripts/")
        except Exception as e:
            st.caption(f"Could not load templates: {e}")

    # ── Save Button ─────────────────────────────────────────────
    st.divider()
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("Save Configuration", type="primary", width="stretch"):
            _save()
            st.rerun()
    with col2:
        if st.button("Reload from Disk"):
            st.session_state.pop("edit_config", None)
            reload_config()
            st.toast("Config reloaded from disk")
            st.rerun()


# ── Helpers ─────────────────────────────────────────────────────

def _get_voice_list() -> list[str]:
    """Return cached list of edge-tts voices."""
    if "edge_tts_voices" not in st.session_state:
        try:
            import edge_tts
            voices = asyncio.run(edge_tts.list_voices())
            en_voices = sorted(
                [v["ShortName"] for v in voices if v["Locale"].startswith("en-")],
            )
            st.session_state.edge_tts_voices = en_voices if en_voices else _fallback_voices()
        except Exception:
            st.session_state.edge_tts_voices = _fallback_voices()
    return st.session_state.edge_tts_voices


def _fallback_voices() -> list[str]:
    return [
        "en-US-AndrewMultilingualNeural",
        "en-US-AvaMultilingualNeural",
        "en-US-BrianMultilingualNeural",
        "en-US-EmmaMultilingualNeural",
        "en-GB-SoniaNeural",
        "en-GB-RyanNeural",
    ]


def _test_pexels(key: str):
    if not key or key.startswith("YOUR_"):
        st.error("No key configured")
        return
    try:
        import requests
        r = requests.get("https://api.pexels.com/v1/search?query=test&per_page=1", headers={"Authorization": key}, timeout=10)
        if r.status_code == 200:
            st.success("Pexels: connected")
        else:
            st.error(f"Pexels: HTTP {r.status_code}")
    except Exception as e:
        st.error(f"Pexels: {e}")


def _test_pixabay(key: str):
    if not key or key.startswith("YOUR_"):
        st.error("No key configured")
        return
    try:
        import requests
        r = requests.get(f"https://pixabay.com/api/?key={key}&q=test&per_page=3", timeout=10)
        if r.status_code == 200:
            st.success("Pixabay: connected")
        else:
            st.error(f"Pixabay: HTTP {r.status_code}")
    except Exception as e:
        st.error(f"Pixabay: {e}")


def _test_anthropic(key: str):
    if not key or key.startswith("YOUR_"):
        st.error("No key configured")
        return
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        client.messages.create(model="claude-sonnet-4-6", max_tokens=10, messages=[{"role": "user", "content": "Hi"}])
        st.success("Anthropic: connected")
    except Exception as e:
        st.error(f"Anthropic: {e}")


def _test_gemini(key: str):
    if not key:
        st.error("No key configured")
        return
    try:
        import requests
        r = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
            timeout=10,
        )
        if r.status_code == 200:
            st.success("Gemini: connected")
        else:
            st.error(f"Gemini: HTTP {r.status_code}")
    except Exception as e:
        st.error(f"Gemini: {e}")


def _test_freesound(key: str):
    if not key:
        st.error("No key configured")
        return
    try:
        import requests
        r = requests.get(
            f"https://freesound.org/apiv2/search/text/?query=test&token={key}&page_size=1",
            timeout=10,
        )
        if r.status_code == 200:
            st.success("Freesound: connected")
        else:
            st.error(f"Freesound: HTTP {r.status_code}")
    except Exception as e:
        st.error(f"Freesound: {e}")


def _test_coverr(key: str):
    if not key or key.startswith("YOUR_"):
        st.error("No key configured")
        return
    try:
        import requests
        r = requests.get(
            "https://api.coverr.co/videos",
            params={"query": "test", "page_size": 1, "api_key": key},
            timeout=10,
        )
        if r.status_code == 200:
            st.success("Coverr: connected")
        else:
            st.error(f"Coverr: HTTP {r.status_code}")
    except Exception as e:
        st.error(f"Coverr: {e}")


def _test_unsplash(key: str):
    if not key or key.startswith("YOUR_"):
        st.error("No key configured")
        return
    try:
        import requests
        r = requests.get(
            "https://api.unsplash.com/search/photos",
            headers={"Authorization": f"Client-ID {key}"},
            params={"query": "test", "per_page": 1},
            timeout=10,
        )
        if r.status_code == 200:
            st.success("Unsplash: connected")
        else:
            st.error(f"Unsplash: HTTP {r.status_code}")
    except Exception as e:
        st.error(f"Unsplash: {e}")


def _test_suno(key: str):
    if not key or key.startswith("YOUR_"):
        st.error("No key configured")
        return
    try:
        import requests
        r = requests.get(
            "https://api.sunoapi.org/api/v1/generate/record-info",
            params={"taskId": "test_invalid_task"},
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if r.status_code == 401:
            st.error("Suno: invalid API key")
        elif r.status_code in (200, 400, 404):
            st.success("Suno: connected")
        else:
            st.error(f"Suno: HTTP {r.status_code}")
    except Exception as e:
        st.error(f"Suno: {e}")


def _render_api_key_instructions():
    """Render step-by-step instructions for obtaining API keys for new providers."""
    st.markdown("""
### Coverr (stock videos)

1. Go to [Coverr API docs](https://api.coverr.co/docs/start/).
2. Contact **developers@coverr.co** by email.
3. Describe your use case (e.g. "short-form video content creation for social media").
4. Wait for a reply (typically within a week). They will send you an API key.
5. Paste the key into the **Coverr API Key** field above and click **Save Configuration**.

**Limits:** Free tier allows up to 1,000 requests/month for personal/staging use. Contact them for production limits.

---

### Unsplash (stock photos)

1. Go to [Unsplash Developers](https://unsplash.com/developers).
2. Sign in or create a free Unsplash account.
3. Click **Your apps** → **New Application**.
4. Accept the API use and guidelines.
5. Fill in the form:
   - **Application name:** e.g. "My Shorts Engine"
   - **Description:** Brief description of your project
6. After creating the app, you'll see your **Access Key** and **Secret Key**.
7. Copy the **Access Key** (not the Secret) into the **Unsplash Access Key** field above.
8. Click **Save Configuration**.

**Note:** Unsplash requires attribution. The app handles this when using images.

---

### Openverse (images + music)

**No API key required.** Openverse allows anonymous access for images and music. The app uses it automatically when enabled in provider settings.

For higher rate limits, you can optionally register at [api.openverse.org](https://api.openverse.org) and use OAuth, but it's not needed for typical use.
""")

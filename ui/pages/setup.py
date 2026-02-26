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

        # API connection tests
        st.markdown("**Test Connections**")
        tcols = st.columns(5)
        with tcols[0]:
            if st.button("Test Pexels", width="stretch"):
                _test_pexels(cfg.get("pexels_api_key", ""))
        with tcols[1]:
            if st.button("Test Pixabay", width="stretch"):
                _test_pixabay(cfg.get("pixabay_api_key", ""))
        with tcols[2]:
            if st.button("Test Anthropic", width="stretch"):
                _test_anthropic(cfg.get("anthropic_api_key", ""))
        with tcols[3]:
            if st.button("Test Gemini", width="stretch"):
                _test_gemini(cfg.get("gemini_api_key", ""))
        with tcols[4]:
            if st.button("Test Freesound", width="stretch"):
                _test_freesound(cfg.get("freesound_api_key", ""))

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

        for cat in ["motivational", "funny", "meme", "news", "storytime"]:
            current = voices_map.get(cat, "en-US-AndrewMultilingualNeural")
            idx = available_voices.index(current) if current in available_voices else 0
            val = st.selectbox(f"{cat.title()} voice", available_voices, index=idx, key=f"cfg_voice_{cat}")
            voices_map[cat] = val

        rate = sourcing.get("voiceover_rate", "-5%")
        val = st.slider(
            "Speech rate (%)", min_value=-50, max_value=50,
            value=int(rate.replace("%", "").replace("+", "")),
            key="cfg_vo_rate",
            help="Negative = slower, positive = faster",
        )
        sourcing["voiceover_rate"] = f"{val:+d}%"

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

# Changelog

All notable changes to the Shorts Engine are documented here.

## [1.7.0] - 2026-02-24

### Added
- **Rejection feedback learning:** Rejected videos now improve future asset selection
  - **Phase 1:** Captures asset filenames (music, video, image) before cleanup when a video is rejected
  - **Phase 2:** Negative scoring — rejected assets receive a penalty (not excluded) so they rank lower when sourcing again
  - User/local assets: match by filename + size + mtime to avoid false penalties when files are replaced
  - Scraped assets: match by filename or (source, source_id)
- **Rejection insights:** Setup page shows script-related rejection counts by category ("Script not engaging", "Inappropriate content")
- **Rejection-guided ideation:** Optional runtime prompt injection — when generating scripts, prepends guidance from past rejections (config: `ideation.rejection_guidance`, default true)
- **Template validation:** Ideation checks for required placeholders (`{trending_topics}`, `{n}`, `{category}`); uses fallback if missing
- **Script template revert:** Setup page "Script Templates" expander with "Restore default" per category — copies from `config/templates/scripts_defaults/`

### Changed
- Database: New tables `rejection_feedback` and `rejection_assets`; new functions `insert_rejection_feedback`, `insert_rejection_assets`, `get_rejection_penalty`, `get_rejection_insights_by_category`, `get_rejection_guidance_for_category` in `models/database.py`
- **Documentation:** README and `settings.example.yaml` updated for Setup page as primary configuration method; dashboard launch path updated from `review/app.py` to `ui/app.py`; project structure and Quick Reference updated

---

## [1.6.0] - 2026-02-26

### Added
- **TikTok OAuth:** Content Posting API now uses user OAuth access token (fixes 401 Unauthorized)
  - Setup "Connect TikTok" button and `python main.py --tiktok-oauth` for authorization flow
  - Token storage in `config/tiktok_token.json` with auto-refresh when expired
  - Retry on 401 with refreshed token during upload
- **Script browser:** Edit and delete scripts for any status
  - Edit title, hook, body, CTA, tags on all scripts
  - "Regenerate video" re-sources assets and re-composes with new audio/images
  - Delete with confirmation (removes script, assets, videos, uploads)
- **Platform selector:** Choose YouTube, TikTok, or both for scraping and generation
  - Content Studio Generate: "Scrape from" dropdown
  - Discovery Scrape Now: platform selector
  - CLI: `--platform youtube|tiktok|both` for `--run` and `--discovery-only`

### Fixed
- **Pipeline navigation:** Fragment orphan error when switching pages — live log moved to sidebar only; pages use static log viewer
- **Connect TikTok:** 90-second timeout when browser tab closed without completing OAuth; spinner no longer hangs indefinitely

### Changed
- TikTok uploader uses `get_tiktok_access_token()` instead of client_key for API calls
- Discovery `run_discovery(platforms=...)` supports youtube, tiktok, or both

---

## [1.5.0] - 2026-02-26

### Added
- **howto script category:** Educational/how-to content ("3 quick tips", "did you know", "you're doing it wrong")
  - Template: `config/templates/scripts/howto.txt`
  - Music: chill, instructional
  - Discovery keywords: howto, tutorial, tips, life hack, guide, learn
- **pov script category:** POV-style immersive narratives ("POV: you just...", "The moment you realize...")
  - Template: `config/templates/scripts/pov.txt`
  - Music: quirky, relatable
  - Discovery keywords: pov, relatable, the moment you, when you

---

## [1.4.0] - 2026-02-26

### Added
- **Gaming & Roblox categories:** Full pipeline support for gaming and roblox content
  - Discovery: `_CATEGORY_KEYWORDS` for trend classification (gaming, roblox)
  - Music: `CATEGORY_MUSIC_MAP` entries with gaming/roblox-appropriate tracks
  - Config: `music_moods` and `voiceover_voices` for gaming and roblox
- **Category checklist:** Startup validation ensures ideation categories are configured across discovery, music, and config
  - `python main.py --setup` runs category consistency check
  - Setup page: "Category Checklist" expander with validation results
  - Sidebar: Warning when category config issues detected at app startup

---

## [1.3.0] - 2026-02-26

### Added
- **Voice variety:** `voiceover_voice_pool` config option to rotate voices across scripts within a pipeline run
- **Setup UI:** Voice pool multiselect and "Natural voices & alternatives" expander (Edge-TTS recommendations, ElevenLabs/Play.ht notes)

### Fixed
- **Dashboard:** Live log and timer now auto-refresh every 2 seconds when any task is running
- **Discovery:** Blank page when clicking "Scrape Now" — fragment moved to end of render so page displays before refresh
- **Content Studio:** Fragment moved to end for consistent behavior
- **Review:** Rejecting videos during pipeline run no longer affects in-progress composition — asset cleanup deferred until pipeline idle

### Changed
- **Evergreen content:** Removed day/date/year from all ideation prompts and templates — scripts use evergreen phrasing ("recently", "now", "these days") for longer video longevity
- **Per-category voices:** Support list of voices per category for random variety
- **Rejection flow:** `cleanup_single_rejected_video(defer_asset_cleanup=True)` when pipeline running; full cleanup runs on next `--cleanup`

---

## [1.2.0] - 2026-02-26

### Added
- **New media providers:** Coverr (stock videos), Unsplash (stock photos), Openverse (images + music)
- **Setup UI:** API key fields for Coverr and Unsplash with test connection buttons
- **Setup UI:** Step-by-step instructions for obtaining API keys (Coverr, Unsplash, Openverse)
- **Content Studio:** Live log auto-refresh every 2 seconds when pipeline is running
- **Content Studio:** Stop button to halt the pipeline mid-run
- **Rejection history:** Trends from rejected videos are excluded from future ideation (no duplicate suggestions)
- **Reaction Shorts:** New workflow for YouTube reaction clip discovery and extraction (`--reaction-discovery`, `--reaction-extract`)
- **Versioning:** Central `version.py`; `--version` flag for CLI; version shown in dashboard sidebar

### Fixed
- **Discovery:** Trend Browser `UnboundLocalError` (lookback variable used before definition)

### Changed
- Ideation now filters out trends that led to rejected content
- `get_top_trends()` accepts optional `exclude_trend_ids` parameter

---

## [1.0.0] - Initial release

- Discovery, Ideation, Sourcing, Composer, Upload pipeline
- Dashboard, Discovery, Content Studio, Review, Uploads, Setup, Cleanup, Scheduler pages
- Pexels, Pixabay, Anthropic, Gemini, Freesound integrations
- YouTube OAuth and TikTok upload support

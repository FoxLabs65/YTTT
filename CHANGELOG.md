# Changelog

All notable changes to the Shorts Engine are documented here.

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

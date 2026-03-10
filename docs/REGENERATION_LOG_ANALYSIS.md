# Regeneration Log Analysis (2026-03-09)

## Summary of Issues Found

Analysis of `regenerate_20260309_180236.log` and related logs identified two distinct issues.

---

## Issue 1: Regenerate Composed Multiple Scripts (FIXED)

**Observation:** User requested regeneration of script #98 only, but the composer processed scripts #98, #97, #91, and #81.

**Root cause:** `run_composer()` processes all scripts with status `assets_ready`. When regenerating script #98:
- Sourcing correctly processes only #98 (sets it to `assets_ready`)
- Composer fetches all `assets_ready` scripts — including #97, #91, #81 from prior runs
- All four were composed (and all four failed with the same render error)

**Fix implemented:** `run_composer(script_id: int | None = None)` now accepts an optional `script_id`. When provided (from `cmd_regenerate`), only that script is composed. The full pipeline continues to pass no `script_id` and composes all assets_ready scripts.

**Files changed:** `agents/composer.py`, `main.py`

---

## Issue 2: Composer Render Error (PRE-EXISTING)

**Error:** `index 0 is out of bounds for axis 0 with size 0 (videos=5, images=3, voiceover=yes, music=yes)`

**Observations:**
- Occurs during `final_video.write_videofile()` (MoviePy/FFmpeg)
- Assets exist: 5 videos, 3 images, voiceover, music
- Affects scripts #81, #91, #97, #98 consistently since ~2026-03-03
- **Not caused by asset sourcing changes** (sourcing correctly provides assets)

**Likely cause (per PIPELINE_ANALYSIS_FINDINGS.md):** Empty segment list, empty caption array, zero-length clip, or short/corrupt music track causing MoviePy audio buffer index error. The "size 0" suggests an empty array somewhere in the render pipeline.

**Recommended next steps:**
1. Add more defensive checks before render: validate all clips have non-zero duration, audio tracks are non-empty
2. Ensure music fallback enforces minimum duration (e.g. 5s) and rejects short sound-effect-style assets
3. Log the exact exception traceback (not just message) to pinpoint which MoviePy/FFmpeg call fails
4. Consider excluding YouTube tracks whose titles suggest "sound effect" rather than background music

---

## Issue 3: User Assets Deleted by Cleanup (FIXED)

**Observation:** After regeneration errors, user-added images and videos in `assets/stock_footage/user/` and `assets/images/user/` were removed.

**Root cause:** The cleanup agent (`_cleanup_script_assets`, `cleanup_orphaned_assets`) deletes asset files when videos are rejected or archived. It treated user assets the same as downloaded stock — deleting the actual files. User assets are the user's library for future videos and must never be deleted.

**Fix implemented:** Added `_is_user_asset_path()` and skip deletion for any file under:
- `assets/stock_footage/user/`
- `assets/images/user/`
- `assets/music/user/`

Only DB references are removed; user library files remain for re-scraping.

---

## Relation to Asset Sourcing Changes

The asset sourcing flow alignment (user pool first, clear user_selected on regenerate, two-phase selection) is **not** the cause of the render or multi-script issues:
- Sourcing correctly processed only script #98
- The composer render error predates these changes and occurs with valid asset counts

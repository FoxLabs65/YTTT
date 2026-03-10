# Asset Sourcing Flow Mapping

## Process Map: Content Studio and Regenerate

### Content Studio Actions

| Action | Runner Task | Command | Result |
|--------|-------------|---------|--------|
| **Scrape Now** | discovery | `main.py --run` (discovery-only) | Scrapes YouTube/TikTok trends; no scripts, no assets |
| **Generate** | full_pipeline | `main.py --run --count N --category X` | Discovery + Ideation + Sourcing + Composer + Cleanup |
| **Regenerate** (script card) | regenerate | `main.py --regenerate --script-id N` | Re-sources + re-composes one script (clears user_selected; voice/music/force_ai from Content) |

### Regenerate from Review Page

| Action | Runner Task | Command | Result |
|--------|-------------|---------|--------|
| **Regenerate Video** | regenerate | `main.py --regenerate --script-id N [--voice X] [--music-path P] [--force-ai-audio] [--force-ai-image] [--force-ai-video]` | Same sourcing as pipeline; overrides applied |

### Backend Flow (Both Full Pipeline and Regenerate)

Both call `run_sourcing()` -> `source_assets_for_script(script)` for each `pending_assets` script. Regenerate deletes assets first, sets status to `pending_assets`, **clears `user_selected_asset_paths`**, then runs the same `run_sourcing()`. User folders are always scraped on both generation and regeneration so new files added for a specific video are considered.

---

## Asset Sourcing Order by Type

### Video (Two-Phase Selection)

| Step | Source | Scored? | Metadata used |
|------|--------|---------|---------------|
| 1 | user_selected_asset_paths | No (override; cleared on regenerate) | None |
| 2 | Reaction trend (if category=reaction) | No | N/A |
| 3a | **User pool:** user_videos | Yes | .meta.json (keywords, mood) or stem |
| 3b | **External pool:** stock + ai_reuse + scraped | Yes | .meta.json for stock/ai; search_query for scraped |
| 4 | AI fallback (Segmind) | N/A | Generated from prompt |

**Selection:** Phase 1 fills from user pool (scored); Phase 2 fills remaining slots from external pool (scored). User assets always get first chance. On regenerate, `user_selected_asset_paths` is cleared so the full pool (user + external) is re-evaluated, including new files in user folders.

### Image (Two-Phase Selection)

| Step | Source | Scored? | Metadata used |
|------|--------|---------|---------------|
| 1 | user_selected_asset_paths | No (override; cleared on regenerate) | None |
| 2 | **User pool:** user_images | Yes | .meta.json for user |
| 3 | **External pool:** stock + ai + scraped | Yes | .meta.json for stock/ai |
| 4 | AI fallback (Flux/Segmind) | N/A | Generated from prompt |

Same two-phase logic as video: user pool first, then external for remaining slots.

### Music (Audio)

| Step | Source | Scored? | Metadata used |
|------|--------|---------|---------------|
| 1 | music_override_path | No (override) | Path only |
| 2 | Pool: user_audio + local + YouTube + Freesound + Openverse | Yes | .meta.json (keywords, mood) for user/local; title/tags for online |
| 3 | AI fallback (Suno) | N/A | Generated |

**Music already follows best practice:** User assets (`_scan_user_audio`) are in the pool and scored with external. No bypass for user folder files.

---

## Scoring Logic

### Video/Image (`_score_visual_candidate`)

- `searchable_text` vs `search_queries`: +0.5 per query match
- Category fallbacks: +0.3 per fallback match
- Rejection penalty: minus penalty for previously rejected files
- User assets: `searchable_text = stem + keywords + mood` from .meta.json (or stem only if no meta)

### Music (`_score_music_candidate`)

- Mood match: +1.0
- Script keyword match: +0.5 per keyword
- Category freesound tags: +0.2 per tag
- Rejection penalty applied
- User/local: `searchable_text` from stem + keywords + mood from .meta.json

---

## Flow (Implemented)

```
User folder assets (scraped, scored) -> fill slots first
  -> if slots remain, external assets (scraped, scored) -> fill remaining
  -> if still not full, AI fallback
```

- **User folders:** Always scraped on both normal generation and regeneration.
- **Regeneration:** Clears `user_selected_asset_paths` so new files in user folders are considered.
- **Two-phase selection:** Video and image use user pool first, then external pool for remaining slots.

---

## User Assets Protection

**User asset folders are never deleted by cleanup:**
- `assets/stock_footage/user/` — user videos
- `assets/images/user/` — user images  
- `assets/music/user/` — user music

When a video is rejected, archived, or cleaned as orphan, the cleanup agent removes intermediate assets (downloaded stock, voiceovers, etc.) but **skips any file in these user folders**. The user's library is preserved for future videos and re-scraping.

---

## Overrides

| Override | When set | Effect |
|----------|----------|--------|
| user_selected_asset_paths | Content page "Select assets" | Bypasses scoring; uses explicit picks. **Cleared on regenerate** so pool is re-evaluated. |
| music_override_path | Review page music pick | Skips music sourcing; uses specified track |
| voice_override | Review page / regenerate | Uses specified voice |
| force_ai_audio | Setup / Review | Uses Suno instead of stock scoring |
| force_ai_image | Review regenerate | Skips stock images; uses Flux AI |
| force_ai_video | Review regenerate | Skips stock videos; uses Segmind AI |

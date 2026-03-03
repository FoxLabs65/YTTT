# Pipeline Analysis Findings

Analysis date: 2026-03-03 (from last 5 log files and codebase review)

---

## 1. Sidebar Timer – Not Auto-Updating

**Issue:** The running indicator in the left pane shows `{task} running ({elapsed})` but the elapsed time does not update automatically.

**Root cause:** `running_indicator()` in `ui/components.py` renders HTML once per full page run. There is no `run_every` fragment, so the elapsed time only updates when the user triggers a rerun (e.g. navigation, button click).

**Resolution:** Use a dynamic `@st.fragment(run_every=5)` for the sidebar running indicator while a task is running. The fragment must not call `st.rerun()` to avoid fragment orphan errors.

---

## 2. Roblox Keeps Being Generated

**Issue:** Roblox scripts and videos appear even when the user did not add Roblox to the search criteria for a given run.

**Finding:** Roblox is coming from **stored config and trend data**, not from hardcoded defaults in the pipeline.

### 2.1 Command-line and config

The pipeline log shows:

```
--tiktok-hashtags #meme,#viral,#funny,#gaming,#roblox,#motivation,...
```

`#gaming` and `#roblox` are present in the passed TikTok hashtags. They come from the saved `config/settings.yaml` (or the UI selection). So they are part of the search criteria when config is used.

### 2.2 Where Roblox scripts come from

1. **Ideation** uses `get_top_trends(conn, limit=20)` to fetch trends from the last 48 hours.
2. There is **no filter** by the current run’s discovery queries.
3. Trends can come from **past scrapes** (e.g. runs that used `#gaming` / `#roblox`).
4. The LLM prompt includes those top 20 trends (titles and tags), so it sees Roblox content if it ranks in the top 20.
5. `discovery_queries` is added as a hint (“prefer ideas that align with these search topics”), but the model still receives all trends, including Roblox.

### 2.3 Code locations

- `agents/ideation.py` lines 191–193: `get_top_trends()` with no query-based filtering.
- `models/database.py` lines 209–245: `get_top_trends()` selects by `trend_score` and `category_filter` only.
- `agents/sourcing.py` line 678: `TOPIC_VISUAL_MODIFIERS = {}` (empty) – no hardcoded gaming/Roblox modifiers here.
- `agents/music_scraper.py`: `TOPIC_MUSIC_MODIFIERS` exists but is only used when a script’s `trend_topic` is gaming/roblox (derived from trend data).

### 2.4 Platform-dependent discovery queries (implemented)

**Root cause:** In `main.py` line 228, `disc_queries` was built as `(yt_q or []) + (tt_h or [])`, always including both YouTube queries and TikTok hashtags regardless of platform. When scraping **YouTube only**, TikTok hashtags (`#gaming`, `#roblox`) were still passed to ideation, biasing scripts.

**Fix:** Build `disc_queries` based on selected platform: YouTube only → `yt_q`; TikTok only → `tt_h`; both → both.

### 2.5 Other recommended changes

1. **Config:** Remove `#gaming` and `#roblox` from `tiktok_hashtags` in `settings.yaml` if the user does not want them.
2. **Ideation:** Restrict ideation to trends from the current discovery run only. Options:
   - A) `run_discovery` returns the IDs of newly scraped trends; ideation uses only those.
   - B) Store the search query per trend; `get_top_trends()` accepts a `query_filter` to limit by current run’s queries.
3. **Stronger discovery alignment:** Make the discovery hint mandatory, e.g. “ONLY create scripts based on these search topics: …”, so the model is constrained, not merely nudged.

---

## 3. Suno File Reuse and Metadata

**Issue:** Suno-generated tracks may not be reused well, leading to redundant files and wasted credits.

### 3.1 Current behavior

1. Suno creates `assets/music/{mood}/suno_{uuid}.mp3`.
2. No metadata file is written.
3. `scan_local_library()` picks up these files as local candidates.
4. `_local_path_to_candidate()` uses the path stem and `.meta.json` (if present) for `searchable_text`.
5. Suno files only contribute `suno_159a1f0af2c4`-style stems – no semantic information.
6. `_score_music_candidate()` scores by mood and script keyword matches in `searchable_text`.
7. Suno tracks therefore score low for keyword relevance and are rarely chosen over better-matched stock.

### 3.2 Existing metadata support

- `_load_music_meta(path)` already reads `{stem}.meta.json`.
- If `keywords` and `mood` exist, they are added to `searchable_text` and affect scoring.

### 3.3 Recommended changes

1. **Persist metadata when saving Suno tracks:**
   - Save `{track_stem}.meta.json` next to each Suno file with:
     - `keywords`: from script keywords and prompt
     - `mood`: category mood
     - `category`: script category
     - `prompt`: Suno prompt (for reference)
2. **Suno files become reusable:** With metadata, future scripts with similar keywords/mood will score Suno tracks higher, increasing reuse and reducing unnecessary generation.

---

## 4. Keyword Extraction – Search Query Length

**Issue:** Extracted keywords are long and may hurt stock search relevance (e.g. Pexels, Pixabay).

### 4.1 Examples from logs

| Extracted | Possible shorter form |
|----------|------------------------|
| "soccer stadium crowd" | "soccer crowd", "stadium" |
| "slow motion ball curving" | "ball curving", "slow motion soccer" |
| "technical diagram of ball trajectory" | "ball trajectory", "diagram" |
| "close-up of player's foot striking ball" | "football kick", "player foot" |
| "slow motion close-up analysis" | "slow motion" |
| "target zone highlight" | "target zone" |
| "roaring crowd" | "crowd" |
| "goalkeeper frozen" | "goalkeeper" |

Long phrases are often less well matched by stock APIs.

### 4.2 Current logic

- `_extract_script_keywords()` in `agents/sourcing.py` (lines 562–657):
  - Uses bracket cues, title, script text.
  - Produces multi-word phrases (adjective+noun, verb+object, etc.) with no length limit.
- These keywords are passed directly to `_expand_visual_queries()` and then to Pexels/Pixabay.

### 4.3 Recommended changes

1. **Add a shortening step** (e.g. `_shorten_keywords_for_search(keywords, max_words=4)`):
   - Drop redundant adjectives.
   - Use core noun phrases (e.g. “ball trajectory” instead of “technical diagram of ball trajectory”).
   - Prefer 2–4 word phrases for stock APIs.
2. **Optionally add simplified variants:**
   - “soccer stadium crowd” → also add “soccer crowd”, “football”
   - “close-up of player's foot striking ball” → also add “football kick”, “soccer”
3. **Ordering:** Use shortened phrases first for search, keep full phrases for scoring if needed.

---

## 5. Pipeline Warnings – Last Run

From `full_pipeline_20260303_103526.log`:

### 5.1 Discovery – No results for several queries

```
WARNING: No results for YouTube query: Relatable
WARNING: No results for YouTube query: funny story time
WARNING: No results for YouTube query: did you know
...
```

**Cause:** YouTube search returns no results for those queries (API limits, query format, or no matching Shorts).

**Resolution:** Treat as expected; consider query variations or fallback queries when common searches return empty.

### 5.2 Template missing placeholder

```
WARNING: Template D:\...\motivational.txt missing placeholder {category} — using fallback
```

**Cause:** `config/templates/scripts/motivational.txt` hardcodes `"category": "motivational"` instead of using `{category}`.

**Resolution:** Add `{category}` as a placeholder (e.g. in the JSON instruction) so the template works for any category and the validator is satisfied.

### 5.3 yt-dlp music search failures

```
WARNING: yt-dlp music search failed: ERROR: [youtube] iwKS4b9aUeI: This video is not available
```

**Cause:** Some YouTube URLs are unavailable (private, deleted, geo-blocked).

**Resolution:** Already handled by continuing to the next candidate; no further change required unless a retry strategy is desired.

### 5.4 Suno no `audioUrl`

```
WARNING: Suno: no audioUrl in response
WARNING: Suno music generation failed, falling back to stock
```

**Cause:** Suno API sometimes returns success without an audio URL.

**Resolution:** Already handled by falling back to stock; consider optional retries for transient Suno failures.

### 5.5 MoviePy audio buffer warning

```
UserWarning: Error in file .../yt_o3B3Pw-_Poo_Trending_Sound_Effect_For_Video_Editing_soundeffec.mp3
At time t=45.09-0.04 seconds, indices wanted: 0-1988909, but len(buffer)=200000
index 1988478 is out of bounds for axis 0 with size 200000
```

**Cause:** The chosen track is a short sound effect, not a loopable music track. When stretched to match video length, buffer indices go out of range.

**Resolution:**
- Prefer full music tracks over sound effects.
- Add simple heuristics (e.g. duration, filename containing “sound effect”) to deprioritize or exclude sound-effect-style assets for background music.

### 5.6 Render failure – script #81

```
ERROR: Render failed for script #81: index 0 is out of bounds for axis 0 with size 0
```

**Cause:** Likely empty segment list, empty caption array, or zero-length clip causing an index error in MoviePy.

**Resolution:** Add defensive checks in the composer (e.g. for empty segments or clips) before indexing, and log which script and step failed for easier debugging.

---

## Summary of Recommended Implementations

| Item | Priority | Effort |
|------|----------|--------|
| Sidebar timer auto-update | High | Low |
| Ideation: trends from current discovery only | High | Medium |
| Suno metadata (.meta.json) for reuse | High | Low |
| Keyword shortening for search | Medium | Medium |
| Motivational template {category} | Low | Low |
| Exclude sound-effect assets from music pool | Medium | Low |
| Composer: guard against empty segments/clips | Medium | Low |

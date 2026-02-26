# Analysis: Discovery Topics & Script Words Influencing Asset Selection

## Scope

1. **Option A for music** — Topic augmentation (already analyzed)
2. **Option A for videos and images** — Same topic augmentation for stock footage/images
3. **Script-word extraction** — Extract specific words from the script to add appropriate pictures that match the content

---

## Current Flow

| Stage | Input | Music | Videos/Images |
|-------|-------|-------|---------------|
| Discovery | `youtube_queries`, `tiktok_hashtags` (e.g. Gaming, Roblox, dogs) | N/A | N/A |
| Ideation | Trends + script category (funny, meme, etc.) | N/A | N/A |
| Sourcing | Script: `category`, `suggested_tags`, `trend_source_ids`, `visual_cues`, `script_body` | `find_best_music(category, tags)` | `_expand_visual_queries(visual_cues, title, category, script_keywords)` |

**Today:**
- **Music:** Chosen solely by script category. Discovery topics do not influence.
- **Videos/Images:** Chosen by script_keywords (from `_extract_script_keywords`), visual_cues (LLM), title, and `CATEGORY_VISUAL_FALLBACKS`. Discovery topics do not influence.

---

## Data Available at Music Selection Time

When `source_music_for_script()` runs, we have:

1. **Script category** — e.g. "funny" (from ideation)
2. **Script tags** — LLM-generated hashtags (e.g. #gaming #roblox #funny)
3. **trend_source_ids** — JSON array of trend IDs that inspired this script

**Trends table** stores per-trend:
- `category` — inferred from tags/title via `_CATEGORY_KEYWORDS` (gaming, roblox, funny, meme, other, etc.)
- `title`, `tags`, `platform`, etc.

So we can derive **trend topic** from `trend_source_ids` → lookup trends → dominant `category` among them.

---

## Feasibility: Yes

### 1. Topic derivation

- Add `get_trends_by_ids(conn, ids)` in `models/database.py`
- For a script, get its `trend_source_ids` → fetch those trends → take the most common `category` (excluding "other")
- Result: `dominant_topic` = "gaming" | "roblox" | None (when all "other" or no sources)

### 2. Topic → music influence

**Option A: Query augmentation (recommended)**

- Define `TOPIC_MUSIC_MODIFIERS`: `{"gaming": ["gaming", "game", "esports"], "roblox": ["playful", "kids", "cartoon"]}`
- When sourcing music: base queries from `CATEGORY_MUSIC_MAP[category]`
- If `dominant_topic` is gaming/roblox: prepend 1–2 topic-augmented queries, e.g. `"no copyright funny gaming music"`
- Fallback: if no topic or "other", use category-only queries (current behavior)

**Option B: Topic-specific mood subfolder**

- e.g. `assets/music/funny_gaming/` for funny + gaming
- Would require more music pre-download and a larger `CATEGORY_MUSIC_MAP`-style structure
- Higher complexity, more storage

**Option C: Tag-based only**

- Use `script_tags` (e.g. #gaming) to bias music search
- Simpler, but tags are LLM-generated and may not match discovery topics
- Less reliable than using actual trend categories

---

## Recommended Approach: Option A (Query Augmentation)

### Logic

```
1. Get script's trend_source_ids
2. Fetch trends by IDs → extract categories
3. dominant_topic = most_common(category) if category in ["gaming", "roblox"] else None
4. base_queries = CATEGORY_MUSIC_MAP[script_category]["queries"]
5. If dominant_topic:
     augmented = [f"{q} {topic}" for q in base_queries[:2] for topic in TOPIC_MODIFIERS[dominant_topic][:1]]
     search_queries = augmented + base_queries  # Try topic-specific first
6. Else:
     search_queries = base_queries
7. find_best_music: prefer tracks from mood dir whose filename contains topic terms
8. If no match, fall back to category-only (current behavior)
```

### Scope of topics

- Start with **gaming** and **roblox** (already in `_CATEGORY_KEYWORDS`)
- "dogs", "chihuahua" etc. map to "other" today, so no topic-specific music unless we add more trend categories

---

## Implementation Outline

### 1. `models/database.py`

```python
def get_trends_by_ids(conn: sqlite3.Connection, trend_ids: list[int]) -> list[dict]:
    """Fetch trends by ID list. Returns [] if ids empty or not found."""
    if not trend_ids:
        return []
    placeholders = ",".join("?" * len(trend_ids))
    rows = conn.execute(
        f"SELECT * FROM trends WHERE id IN ({placeholders})",
        trend_ids,
    ).fetchall()
    return [dict(r) for r in rows]
```

### 2. `agents/music_scraper.py`

- Add `TOPIC_MUSIC_MODIFIERS = {"gaming": ["gaming", "game"], "roblox": ["playful", "roblox"]}`
- Add `get_dominant_trend_topic(conn, trend_source_ids) -> str | None`
- Update `find_best_music(category, script_tags=None, trend_topic=None)` to prefer topic-matched tracks when `trend_topic` is set
- Update `search_and_download_music(category, count=3, trend_topic=None)` to prepend topic-augmented queries
- Update `source_music_for_script(script_id, category, tags=None, trend_source_ids=None)` — pass `trend_source_ids`, resolve topic, then call find/search with topic

### 3. `agents/sourcing.py`

- When calling `source_music_for_script()`, pass `trend_source_ids=script.get("trend_source_ids")`

### 4. Config (optional)

- `sourcing.music_topic_influence: true` — allow turning this off if desired

---

## Edge Cases

| Case | Behavior |
|------|----------|
| No trend_source_ids | Use category-only (current) |
| All trends "other" | Use category-only |
| Mixed topics (2 gaming, 1 roblox) | Use dominant (gaming) |
| Script category = funny, topic = gaming | Search "funny gaming music" first, then "funny" |
| No topic-matched tracks in library | Fall back to category-only |

---

## Effort Estimate

- **Low–medium:** ~2–3 hours
- New DB helper, small changes in music_scraper and sourcing
- No schema changes
- Backward compatible: no topic → same behavior as today

---

## Recommendation

Implement Option A (query augmentation) with gaming and roblox as initial topics. It reuses existing trend classification and keeps changes localized. If it works well, more topics (e.g. pets, fitness) can be added to `_CATEGORY_KEYWORDS` and `TOPIC_MUSIC_MODIFIERS` later.

---

# Part 2: Option A for Videos and Images

## Current Video/Image Query Flow

`_expand_visual_queries()` builds search queries in this order:

1. **script_keywords** — from `_extract_script_keywords(script_body, title, tags)`
2. **visual_cues** — simplified and raw (from LLM's `visual_cues` JSON field)
3. **title** — noun phrases
4. **CATEGORY_VISUAL_FALLBACKS[category]** — category-based fallbacks

Videos and images both use the same `search_queries` list. No topic influence today.

## Feasibility: Yes

Same mechanism as music: derive `dominant_topic` from `trend_source_ids` → trends → categories.

### Topic augmentation for videos/images

- Add `TOPIC_VISUAL_MODIFIERS = {"gaming": ["gaming", "gameplay", "esports"], "roblox": ["roblox", "kids gaming", "cartoon game"]}`
- In `_expand_visual_queries()`, accept optional `trend_topic` parameter
- If `trend_topic` is set: prepend topic-augmented queries, e.g. `"gaming reaction"`, `"roblox gameplay"`
- Same `get_trends_by_ids` + `get_dominant_trend_topic` used for music

### Implementation

- `source_assets_for_script()` already has `script` dict with `trend_source_ids`
- Call `get_dominant_trend_topic(conn, script.get("trend_source_ids"))` once at start
- Pass `trend_topic` into `_expand_visual_queries(..., trend_topic=dominant_topic)`
- Prepend `TOPIC_VISUAL_MODIFIERS.get(topic, [])` to queries when topic is set

### Effort

- **Low:** ~30 min — reuse same topic derivation, add modifiers dict and one parameter to `_expand_visual_queries`

---

# Part 3: Script-Word Extraction for Better Picture Matching

## Current Script Keyword Extraction

`_extract_script_keywords(script_body, title, tags)` currently:

1. **Title** — noun phrases (first 4 significant words)
2. **Proper nouns** — capitalized words (e.g. "Prince", "Mrs Henderson")
3. **Adjective+noun** — only when adjective is in `concrete_indicators` (cold, dark, bright, etc.)
4. **Visual nouns** — only words in fixed list (~40 terms: room, desk, ocean, mountain, phone, etc.)
5. **Tags** — cleaned hashtags

**Critical gap:** The script body contains `[show X]` inline cues (e.g. `[show confused face stock clip]`, `[show dark hallway]`). The current code **strips** these with `re.sub(r"\[.*?\]", " ", script_body)` — they are discarded and never used for search.

## Feasibility: Yes

### Enhancement 1: Extract `[show X]` cues from script body (high value)

The LLM is instructed to include timing cues like `[show footage]`, `[show confused face]`, `[show news headline graphic]`. These are explicit visual directions.

**Change:** Before stripping, extract: `re.findall(r"\[(.*?)\]", script_body)` → clean each (remove "show", "footage", "stock clip", "effect") → add as **highest priority** keywords.

Example: `"I walked in [show dark hallway] and saw [show surprised face]"` → extract `["dark hallway", "surprised face"]` → use as first search queries.

### Enhancement 2: Expand visual_nouns and concrete_indicators

Current `visual_nouns` has ~40 terms. Missing: `dog`, `cat`, `pet`, `game`, `controller`, `computer`, `laptop`, `office`, `kitchen`, `food`, `coffee`, `book`, `guitar` (has piano), `baby`, `child`, `teacher`, `doctor`, etc.

**Change:** Add 30–50 more concrete nouns that stock libraries commonly have. Low effort, immediate improvement.

### Enhancement 3: Extract more adjective+noun phrases

Current `concrete_indicators` has 15 adjectives. Could add: `surprised`, `confused`, `happy`, `sad`, `angry`, `tired`, `excited`, `scared`, `nervous`, `relaxed`, `busy`, `empty`, `modern`, `old`, `young`, etc.

**Change:** Expand the set. Any `(adjective, noun)` where adjective suggests a visual state and noun is filmable.

### Enhancement 4: N-gram extraction (optional, higher effort)

Extract 2–3 word phrases that appear in the script and match common stock search patterns. Could use simple frequency or a small "visual phrase" dictionary. More complex; defer unless Enhancements 1–3 are insufficient.

## Recommended Implementation Order

| Enhancement | Effort | Impact | Recommendation |
|-------------|--------|--------|-----------------|
| 1. Extract `[show X]` cues | Low (~20 min) | High | **Do first** — recovers explicit LLM visual directions |
| 2. Expand visual_nouns | Low (~15 min) | Medium | **Do** — catches dog, game, computer, etc. |
| 3. Expand concrete_indicators | Low (~10 min) | Medium | **Do** — catches "surprised face", "busy office" |
| 4. N-gram / phrase extraction | Medium | Medium | Defer — evaluate after 1–3 |

## Implementation Outline for Enhancements 1–3

### `_extract_script_keywords()` changes

```python
# NEW: Extract [show X] cues FIRST (highest priority - explicit LLM directions)
bracket_cues = re.findall(r"\[(.*?)\]", script_body)
for cue in bracket_cues:
    clean = re.sub(r"\b(show|footage|clip|stock|effect|animation|visual|graphic)\b", "", cue, flags=re.IGNORECASE)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > 3:
        keywords.append(clean)

# Then strip brackets for rest of processing
body = re.sub(r"\[.*?\]", " ", script_body)
# ... existing logic ...

# EXPAND visual_nouns: add dog, cat, pet, game, controller, computer, laptop, office, kitchen, food, coffee, book, baby, child, doctor, etc.
# EXPAND concrete_indicators: add surprised, confused, happy, sad, excited, scared, nervous, relaxed, busy, modern, etc.
```

### Query priority in `_expand_visual_queries`

After enhancement, effective order:

1. **Bracket cues** (`[show X]`) — explicit, highest priority
2. **Script keywords** — from enhanced `_extract_script_keywords`
3. **Visual cues** — from LLM's `visual_cues` JSON
4. **Title** — noun phrases
5. **Topic modifiers** — if Option A for videos/images
6. **Category fallbacks** — lowest priority

---

# Combined Implementation: All Three Features

## Can All Be Added Together? Yes

| Feature | Depends On | Conflicts |
|---------|------------|-----------|
| Option A (music) | `get_trends_by_ids`, `get_dominant_trend_topic` | None |
| Option A (videos/images) | Same topic derivation | None |
| Script-word extraction | None | None |

All three can be implemented in the same pass. Shared work:

1. **`get_trends_by_ids()`** — used by music and video/image topic logic
2. **`get_dominant_trend_topic()`** — used by both
3. **`_extract_script_keywords()`** — enhanced for script-word extraction
4. **`_expand_visual_queries()`** — add `trend_topic`, integrate bracket cues into keyword flow

## Suggested Implementation Order

1. Add `get_trends_by_ids` and `get_dominant_trend_topic` (database + helper)
2. Enhance `_extract_script_keywords` (bracket cues, expand nouns/indicators)
3. Add topic augmentation to `_expand_visual_queries` for videos/images
4. Add topic augmentation to music (as in Part 1)

## Total Effort Estimate

- **Option A (music):** ~1.5 hours
- **Option A (videos/images):** ~30 min
- **Script-word extraction (Enhancements 1–3):** ~45 min
- **Integration and testing:** ~30 min

**Total: ~3–4 hours** for all three features.

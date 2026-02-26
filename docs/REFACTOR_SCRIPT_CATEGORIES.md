# Refactor: Discovery Topics vs Script Categories

## Overview

**Problem:** Discovery topics (what to scrape) and script categories (style of output) are conflated. Users can add custom categories (e.g. "dogs") that have no templates, causing fallback behavior and confusion.

**Solution:** Decouple the two concepts. Discovery topics are flexible; script categories are a fixed set with full pipeline support (template, music, voice, visuals).

---

## 1. Canonical Script Categories (Fixed Set)

| Category | Template | Music Mood | Use Case |
|----------|----------|------------|----------|
| motivational | motivational.txt | uplifting | Inspiring quotes, mindset, goals |
| funny | funny.txt | funny | Comedy, relatable humor, funny stories |
| meme | meme.txt | quirky | POV, viral, quick punchy content |
| news | news.txt | dramatic | News reactions, hot takes, commentary |
| storytime | storytime.txt | chill | Personal anecdotes, dramatic twists |

**Optional additions** (research-backed, high-performing formats):

| Category | Template | Music Mood | Use Case |
|----------|----------|------------|----------|
| howto | howto.txt (new) | chill | "3 quick tips", "did you know", "you're doing it wrong" — educational micro-content |
| pov | pov.txt (new) | quirky | "POV: you just...", "The moment you realize..." — immersive short narratives |

---

## 2. Config Changes

### `config/settings.yaml` and `config/settings.example.yaml`

```yaml
# BEFORE
ideation:
  categories:
    - funny
    - meme
    - gaming   # REMOVE — belongs in discovery only
    - roblox   # REMOVE

# AFTER
ideation:
  script_categories:   # RENAME from categories
    - motivational
    - funny
    - meme
    - news
    - storytime
    # Optional: - howto, - pov
```

**Backward compatibility:** Support both `ideation.categories` and `ideation.script_categories` during migration; prefer `script_categories` if both exist.

### Discovery (unchanged — topics stay flexible)

```yaml
discovery:
  youtube_queries:
    - funny
    - Gaming      # topic — scrapes gaming content
    - Roblox      # topic — scrapes roblox content
    - dogs        # topic — scrapes dog content
    - chihuahua
  tiktok_hashtags:
    - '#gaming'
    - '#roblox'
    - '#dogs'
```

### Sourcing (remove gaming/roblox from music_moods and voiceover_voices)

```yaml
sourcing:
  music_moods:
    motivational: uplifting
    funny: funny
    meme: quirky
    news: dramatic
    storytime: chill
    # howto: chill    # if adding howto
    # pov: quirky     # if adding pov
  voiceover_voices:
    motivational: en-US-AndrewMultilingualNeural
    funny: en-US-BrianMultilingualNeural
    meme: en-US-BrianMultilingualNeural
    news: en-US-AndrewMultilingualNeural
    storytime: en-US-AvaMultilingualNeural
```

---

## 3. Code Changes by File

### 3.1 `config/validation.py`

- **Source of truth:** Define `ALLOWED_SCRIPT_CATEGORIES = ["motivational", "funny", "meme", "news", "storytime"]` (add howto, pov if implemented).
- **Validation logic:** Only validate categories that exist in config against templates/music/voice. Reject or warn if config contains categories not in `ALLOWED_SCRIPT_CATEGORIES`.
- **Remove:** Validation of discovery topics (they are free-form).
- **Config key:** Read from `ideation.script_categories` (fallback to `ideation.categories` for backward compat).

### 3.2 `ui/pages/discovery.py`

- **Rename:** "Content Categories" → "Script Categories".
- **Caption:** "Script categories define the *style* of scripts generated (funny, meme, storytime, etc.). Discovery topics (YouTube queries / TikTok hashtags above) define what content gets scraped."
- **Remove:** "Add custom category" text input and "Add category" button.
- **Remove:** Delete-button per category (no custom = no delete needed for non-presets).
- **UI:** Show checkboxes for **only** the allowed script categories. User can enable/disable which script styles to use. No free-text add.
- **Save:** Only save subset of `ALLOWED_SCRIPT_CATEGORIES` that are checked. Never save user-typed custom categories.

### 3.3 `ui/pages/content.py`

- **Source:** `categories = cfg.get("ideation", {}).get("script_categories") or cfg.get("ideation", {}).get("categories") or ALLOWED_SCRIPT_CATEGORIES`.
- **Filter:** Only show categories that are in `ALLOWED_SCRIPT_CATEGORIES`. If config has invalid entries, filter them out.
- **Selectbox:** `["auto (from trends)"] + [c for c in categories if c in ALLOWED_SCRIPT_CATEGORIES]`.

### 3.4 `ui/pages/setup.py`

- **Voiceover section:** Loop over `ALLOWED_SCRIPT_CATEGORIES` instead of hardcoded `["motivational", "funny", "meme", "news", "storytime"]`.
- **Category Checklist:** Update to reference `script_categories` and `ALLOWED_SCRIPT_CATEGORIES`.

### 3.5 `agents/ideation.py`

- **Config key:** `categories = cfg("ideation.script_categories") or cfg("ideation.categories") or ALLOWED_SCRIPT_CATEGORIES`.
- **Validation:** Before generating, validate `category in ALLOWED_SCRIPT_CATEGORIES`. If not, log warning and fall back to `random.choice(ALLOWED_SCRIPT_CATEGORIES)` or "funny".
- **Template load:** `_load_template(category)` — if category not allowed, use fallback template (e.g. funny).

### 3.6 `agents/discovery.py`

- **No change to `_CATEGORY_KEYWORDS`** — this classifies *trends* for reporting/filtering (e.g. "this trend is gaming-related"). Keep gaming, roblox, etc. for trend classification. This is separate from script categories.
- **Trend categories** can include: motivational, funny, meme, news, storytime, gaming, roblox, other. These describe the scraped content, not the script style.

### 3.7 `agents/music_scraper.py`

- **CATEGORY_MUSIC_MAP:** Remove `gaming` and `roblox`. Keep only script categories: motivational, funny, meme, news, storytime. Add howto, pov if implementing.
- **Fallback:** `CATEGORY_MUSIC_MAP.get(category, CATEGORY_MUSIC_MAP["storytime"])` — unknown categories (e.g. from old DB rows) fall back to storytime.

### 3.8 `agents/sourcing.py`

- **CATEGORY_VISUAL_FALLBACKS:** Remove wellness, wellbeing, viral (or keep as fallbacks for legacy scripts). Ensure all `ALLOWED_SCRIPT_CATEGORIES` have entries.
- **Voiceover:** `voices.get(category, "en-US-AndrewMultilingualNeural")` — already has fallback.
- **Validation:** When sourcing, if `script["category"]` not in allowed set, treat as "storytime" for music/voice/visuals.

### 3.9 `main.py`

- **`--category` help:** "Script category for ideation (motivational, funny, meme, news, storytime)".
- **Validation:** If `args.category` not in `ALLOWED_SCRIPT_CATEGORIES`, log warning and use "auto" or "funny".

### 3.10 `models/database.py`

- **No schema change.** `scripts.category` remains TEXT. Old rows may have "gaming", "roblox", "dogs" — sourcing will fall back. New scripts will only get allowed categories.

### 3.11 `README.md`

- Update "Valid categories" to list only script categories.
- Clarify: "Discovery topics (youtube_queries) define what to scrape. Script categories define the style of scripts generated."

---

## 4. New Template Files (Optional)

### `config/templates/scripts/howto.txt`

```
You are a viral short-form content writer specializing in educational and how-to content.

Given these trending topics on YouTube/TikTok:
{trending_topics}

CRITICAL: NEVER include any specific day, date, month, or year in titles, hooks, or script body.

Generate {n} original how-to / educational short video scripts (15-45 seconds each). Each script must:
- Ride on a trending topic but be ORIGINAL (not copying)
- Be a "3 quick tips", "did you know", or "you're doing it wrong" style
- Have a hook in the first 2 seconds (e.g. "Nobody tells you this about...")
- Deliver actionable, memorable takeaways
- Include timing cues for visuals in square brackets
- End with engagement bait ("Save this", "Which tip will you try?")

Return ONLY a JSON array. Each element: title, category: "howto", hook, script_body, cta, suggested_tags, visual_cues, estimated_duration_seconds
```

### `config/templates/scripts/pov.txt`

```
You are a viral short-form content writer specializing in POV and immersive short narratives.

Given these trending topics on YouTube/TikTok:
{trending_topics}

CRITICAL: NEVER include any specific day, date, month, or year.

Generate {n} original POV-style short video scripts (15-45 seconds each). Each script must:
- Ride on a trending topic but be ORIGINAL
- Be "POV: you just...", "The moment you realize...", or immersive second-person narrative
- Have a hook in the first 2 seconds that puts viewer in the scenario
- Be relatable, slightly dramatic, or absurd
- Include timing cues for visuals in square brackets
- End with engagement bait ("Tag someone who...", "Part 2?")

Return ONLY a JSON array. Each element: title, category: "pov", hook, script_body, cta, suggested_tags, visual_cues, estimated_duration_seconds
```

---

## 5. Central Constant

Create `config/script_categories.py` (or add to `models/config.py`):

```python
"""Canonical list of script categories. All must have template + music + voice config."""

ALLOWED_SCRIPT_CATEGORIES = [
    "motivational",
    "funny",
    "meme",
    "news",
    "storytime",
]
# Optional: "howto", "pov"
```

Import this wherever script category validation is needed.

---

## 6. Migration for Existing Configs

- If `ideation.categories` contains "gaming", "roblox", "dogs", etc.: strip them when loading. Only keep entries that are in `ALLOWED_SCRIPT_CATEGORIES`.
- On first save after refactor, write `ideation.script_categories` with the filtered list.
- Log: "Migrated ideation.categories → script_categories. Removed unsupported: gaming, roblox, dogs."

---

## 7. Summary Checklist

| Task | File(s) |
|------|---------|
| Define `ALLOWED_SCRIPT_CATEGORIES` | New: `config/script_categories.py` |
| Rename config key `categories` → `script_categories` | settings.yaml, settings.example.yaml |
| Remove gaming/roblox from ideation, music, voiceover | settings, music_scraper, sourcing |
| Remove "Add custom category" UI | discovery.py |
| Restrict Discovery script section to checkboxes only | discovery.py |
| Validate category in ideation before generate | ideation.py |
| Validate category in main.py --category | main.py |
| Filter categories in content.py selectbox | content.py |
| Update Setup voiceover loop | setup.py |
| Update validation.py to use allowed list | validation.py |
| Add howto.txt, pov.txt (optional) | config/templates/scripts/ |
| Update README | README.md |

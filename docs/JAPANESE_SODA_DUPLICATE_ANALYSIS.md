# Analysis: Japanese Soda Videos Selected Twice in Continuous Searches

## Log Correlation

### Run 1 — `full_pipeline_20260301_173805.log` (17:38:05)
- **Discovery**: YouTube (Relatable, motivational short, did you know, daily motivation) + TikTok (full tag set)
- **Scraped**: 1 YouTube trend, 45 TikTok trends
- **Ideation**: Generated 5 scripts including **Script #56: "I Ranked Every Japanese Soda So You Don't Waste Your Money"**
- **Pipeline**: **Exited with code 1** (failed) before sourcing/composing
- Scripts #52–#56 saved to DB with status `pending_assets`

### Run 2 — `full_pipeline_20260301_175104.log` (17:51:04)
- **Discovery**: YouTube only (Relatable, motivational short, Wellness, daily motivation, Mindfulness, Mental Health, Meditation)
- **User selection**: Wellness/mindfulness tags
- **Scraped**: **0 new trends** (many queries returned "No results")
- **Top trends**: 20 — same pool from Run 1 (48h lookback, no new data)
- **Ideation**: Same `get_top_trends(limit=20)` → same/similar trends as Run 1
- **Scripts**: Generated 5 new scripts including **Script #61: "I Ranked Every Japanese Soda You Can Actually Buy Right Now"**
- **Sourcing**: Processed scripts #52–#61 (5 pending from Run 1 + 5 new from Run 2)

## Root Causes

### 1. Ideation Uses Stale Trends When Discovery Returns 0
- When discovery returns 0 new trends (e.g. wellness queries with no results), the trend DB is unchanged.
- Ideation always uses `get_top_trends(limit=20)` from the DB.
- Run 2 therefore received the same top trends as Run 1 (different tag set).
- User’s selected tags (Wellness, Mindfulness, Meditation) had no effect on ideation.

### 2. No Topic Deduplication
- Ideation does not consider recently generated scripts.
- `get_rejected_trend_ids` only excludes trends that led to rejected content.
- No logic prevents generating similar topics (e.g. Japanese soda) across runs.
- Same trend and tag set → LLM tends to produce similar ideas (e.g. "Japanese soda" twice).

### 3. Discovery Queries Not Passed to Ideation
- Discovery uses user-selected YouTube queries and TikTok hashtags.
- Ideation only receives trends and trending tags from the DB.
- Even with new trends, ideation is not instructed to prefer topics matching the user’s tags.
- No `discovery_queries` or `tag_hint` parameter in `run_ideation` or `generate_scripts`.

### 4. Trend Pool Not Tied to Discovery Source
- Trends table has no column for which discovery query produced a trend.
- Impossible to filter trends by “from this run’s search” without schema changes.
- All trends within the lookback period are treated equally by `get_top_trends`.

## Improvement Summary

| Issue | Fix |
|-------|-----|
| Topic duplication | Add topic deduplication: exclude titles/topics from scripts created in last 72h from ideation prompt |
| Stale trends when 0 new | Pass discovery queries to ideation; add hint to prefer topics aligning with user’s search when using old data |
| Tag/topic mismatch | Include discovery query context in ideation prompt when available |

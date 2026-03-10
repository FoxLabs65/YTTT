"""
Content Ideation Agent
Uses Claude Pro to generate original short-form video scripts
based on trending topics discovered by the Discovery Agent.
Falls back to Google Gemini when Claude is overloaded (529).
"""

import json
import logging
import random
from datetime import datetime
from pathlib import Path

import anthropic

from models.config import get as cfg
from models.database import (
    get_connection,
    get_top_trends,
    get_top_tags,
    get_rejected_trend_ids,
    get_rejection_guidance_for_category,
    get_recent_script_titles,
    insert_script,
)

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent.parent / "config" / "templates" / "scripts"


REQUIRED_PLACEHOLDERS = ("{trending_topics}", "{n}", "{category}")


def _fallback_generic_prompt() -> str:
    """Return the built-in fallback prompt when template is missing or invalid."""
    return """You are a viral short-form content writer.

Given these trending topics on YouTube/TikTok:
{trending_topics}

CRITICAL: NEVER include any specific day, date, month, or year in titles, hooks, or script body. Dates make videos look stale and lose longevity. Use evergreen phrasing instead (e.g. "recently", "now", "these days", "right now").

Generate {n} original short video scripts (15-45 seconds each). Each script must:
- Ride on a currently trending topic/tag but be ORIGINAL content (not copying)
- Have a hook in the first 2 seconds that stops scrolling
- Have a clickworthy title (curiosity gap, emotion, or shock value)
- Include timing cues for visuals in square brackets like [show footage]
- End with engagement bait (question, "follow for part 2", etc.)

IMPORTANT structure rules:
- "hook" is a SHORT text-only overlay shown on screen (NOT narrated separately)
- "script_body" is the COMPLETE voiceover narration from start to finish. It must start with the hook idea woven in naturally. Do NOT repeat the hook as a separate sentence.
- Use varied punctuation for natural pacing: commas, dashes, rhetorical questions, ellipses (...), and [[pause]] or [[long pause]] for deliberate beats. Avoid flat run-on sentences.
- "cta" is a SHORT text-only overlay shown at the end (NOT narrated separately)

Return ONLY a JSON array. Each element must have these exact keys:
- "title": clickworthy video title
- "category": "{category}"
- "hook": short on-screen text overlay for the first 2 seconds (visual only, max 10 words)
- "script_body": the COMPLETE voiceover narration with [visual cues] inline
- "cta": short on-screen call-to-action text overlay (visual only)
- "suggested_tags": array of 5-8 hashtags
- "visual_cues": array of search terms for stock footage
- "estimated_duration_seconds": integer between 15 and 45
"""


def _load_template(category: str) -> str:
    path = TEMPLATE_DIR / f"{category}.txt"
    if path.exists():
        content = path.read_text(encoding="utf-8")
        for ph in REQUIRED_PLACEHOLDERS:
            if ph not in content:
                logger.warning("Template %s missing placeholder %s — using fallback", path, ph)
                return _fallback_generic_prompt()
        return content
    return _fallback_generic_prompt()


def _format_trends_for_prompt(trends: list[dict], tags: list[dict]) -> str:
    lines = []

    if trends:
        lines.append("TOP TRENDING VIDEOS:")
        for i, t in enumerate(trends[:15], 1):
            score = f"(score: {t.get('trend_score', 0):.0f})"
            platform = t.get("platform", "unknown").upper()
            lines.append(f"  {i}. [{platform}] {t.get('title', 'N/A')} {score}")
            if t.get("tags"):
                tag_list = t["tags"] if isinstance(t["tags"], list) else json.loads(t["tags"])
                lines.append(f"     Tags: {', '.join(tag_list[:8])}")

    if tags:
        lines.append("\nTOP TRENDING TAGS/HASHTAGS:")
        tag_strs = [f"#{t['tag']} (freq: {t['frequency']})" for t in tags[:20]]
        lines.append("  " + ", ".join(tag_strs))

    return "\n".join(lines)


# --- LLM Backends ---

def _call_claude(prompt: str, max_tokens: int = 4096) -> str | None:
    """Call Claude API. Returns response text or None on overload/failure."""
    api_key = cfg("anthropic_api_key")
    if not api_key or api_key.startswith("YOUR_"):
        return None

    model = cfg("ideation.claude_model") or "claude-sonnet-4-6"
    client = anthropic.Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        logger.info("Claude responded successfully (%s)", model)
        return response.content[0].text
    except anthropic.APIStatusError as e:
        if e.status_code == 529:
            logger.warning("Claude overloaded (529), will try fallback...")
            return None
        logger.error("Claude API error (%d): %s", e.status_code, e.message)
        return None
    except anthropic.APIError as e:
        logger.error("Claude API error: %s", e)
        return None


def _call_gemini(prompt: str) -> str | None:
    """Call Google Gemini API as fallback. Returns response text or None."""
    api_key = cfg("gemini_api_key")
    if not api_key or api_key.startswith("YOUR_"):
        logger.warning("Gemini API key not configured -- cannot fall back. Set gemini_api_key in settings.yaml")
        return None

    model = cfg("ideation.gemini_model") or "gemini-2.0-flash"

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
        logger.info("Gemini responded successfully (%s)", model)
        return response.text
    except Exception as e:
        logger.error("Gemini API error: %s", e)
        return None


def _call_llm(prompt: str, max_tokens: int = 4096) -> str | None:
    """Try Claude first, fall back to Gemini if Claude is overloaded or fails."""
    content = _call_claude(prompt, max_tokens)
    if content:
        return content

    logger.info("Falling back to Gemini...")
    content = _call_gemini(prompt)
    if content:
        return content

    logger.error("All LLM backends failed")
    return None


# --- Script Generation ---

def generate_scripts(
    category: str | None = None,
    count: int | None = None,
    trends: list[dict] | None = None,
    tags: list[dict] | None = None,
    discovery_queries: list[str] | None = None,
) -> list[dict]:
    """Generate video scripts using Claude Pro (with Gemini fallback)."""
    count = count or cfg("ideation.scripts_per_batch") or 5
    categories = cfg("ideation.categories") or ["motivational", "funny", "meme", "news", "storytime", "howto", "pov"]

    if not category:
        category = random.choice(categories)

    conn = get_connection()
    if not trends:
        excluded = get_rejected_trend_ids(conn)
        exclude_list = list(excluded) if excluded else None
        # When category specified (not random), prefer trends matching that category
        cat_filter = category if category and category != "random" else None
        trends = get_top_trends(
            conn, limit=20, exclude_trend_ids=exclude_list, category_filter=cat_filter
        )
    if not tags:
        tags = get_top_tags(conn, limit=30)
    recent_titles = get_recent_script_titles(conn, hours=72)
    conn.close()

    if not trends and not tags:
        logger.warning("No trend data available. Run discovery first.")
        return []

    trending_text = _format_trends_for_prompt(trends, tags)
    template = _load_template(category)
    prompt = (
        template.replace("{trending_topics}", trending_text)
        .replace("{n}", str(count))
        .replace("{category}", category)
    )
    # Topic deduplication: avoid generating near-duplicate topics recently created
    if recent_titles:
        avoid_block = (
            "AVOID generating scripts on the same or very similar topics as these recently generated titles. "
            "Create distinctly different content:\n"
            + "\n".join(f"  - {t}" for t in recent_titles[:15])
        )
        prompt = f"{avoid_block}\n\n{prompt}"
    # Discovery alignment: when user selected specific search tags, bias toward them
    if discovery_queries:
        hint = (
            "The user searched for these topics: "
            + ", ".join(repr(q) for q in discovery_queries[:15])
            + ". When relevant, prefer script ideas that align with these search topics."
        )
        prompt = f"{hint}\n\n{prompt}"
    # Prepend rejection guidance if enabled
    if cfg("ideation.rejection_guidance", True):
        conn = get_connection()
        guidance = get_rejection_guidance_for_category(conn, category)
        conn.close()
        if guidance:
            prompt = f"REJECTION FEEDBACK: {guidance}\n\n{prompt}"

    logger.info("Generating %d %s scripts...", count, category)
    content = _call_llm(prompt)

    if not content:
        raise RuntimeError("All LLM backends failed — no script content generated")

    scripts = _parse_scripts_response(content)
    if not scripts:
        logger.warning("Failed to parse scripts from LLM response")
        return []

    conn = get_connection()
    saved = []
    trend_ids = [t.get("id") for t in trends[:10] if t.get("id")]
    for script in scripts:
        script_id = insert_script(
            conn,
            title=script.get("title", "Untitled"),
            category=script.get("category", category),
            hook=script.get("hook", ""),
            script_body=script.get("script_body", ""),
            cta=script.get("cta", ""),
            suggested_tags=script.get("suggested_tags", []),
            visual_cues=script.get("visual_cues", []),
            estimated_duration=script.get("estimated_duration_seconds", 30),
            trend_source_ids=trend_ids,
            status="pending_assets",
        )
        script["id"] = script_id
        saved.append(script)
        title = script.get("title", "")
        # Sanitize for logging: Windows cp1252 can't encode emojis; avoid UnicodeEncodeError
        title_safe = title.encode("ascii", "replace").decode("ascii") if title else ""
        logger.info("Saved script #%d: %s", script_id, title_safe)

    conn.close()
    return saved


def generate_title_variants(script: dict) -> list[str]:
    """Generate 3 title variants for A/B testing."""
    prompt = f"""Given this short-form video script:
Title: {script.get('title', '')}
Category: {script.get('category', '')}
Hook: {script.get('hook', '')}
Script: {script.get('script_body', '')[:300]}

Generate 3 alternative clickworthy titles. Each should use a different strategy:
1. Curiosity gap (makes viewer NEED to know)
2. Emotional trigger (strong feeling)
3. Trend-aligned (uses trending keywords)

Return ONLY a JSON array of 3 title strings."""

    content = _call_llm(prompt, max_tokens=500)
    if content:
        return _parse_json_array(content) or [script.get("title", "")]
    return [script.get("title", "")]


def _parse_scripts_response(text: str) -> list[dict]:
    """Extract JSON array from LLM response, handling markdown fences."""
    text = text.strip()
    if "```" in text:
        start = text.find("```")
        first_newline = text.find("\n", start)
        end = text.find("```", first_newline)
        if end > first_newline:
            text = text[first_newline:end].strip()

    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    bracket_start = text.find("[")
    bracket_end = text.rfind("]")
    if bracket_start >= 0 and bracket_end > bracket_start:
        try:
            result = json.loads(text[bracket_start : bracket_end + 1])
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse JSON from response: %s...", text[:200])
    return []


def _parse_json_array(text: str) -> list[str] | None:
    text = text.strip()
    if "```" in text:
        start = text.find("```")
        first_newline = text.find("\n", start)
        end = text.find("```", first_newline)
        if end > first_newline:
            text = text[first_newline:end].strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        bracket_start = text.find("[")
        bracket_end = text.rfind("]")
        if bracket_start >= 0 and bracket_end > bracket_start:
            try:
                return json.loads(text[bracket_start : bracket_end + 1])
            except json.JSONDecodeError:
                pass
    return None


def run_ideation(
    category: str | None = None,
    count: int | None = None,
    discovery_queries: list[str] | None = None,
) -> dict:
    """Run ideation pipeline. Returns summary dict.
    Raises RuntimeError on LLM failures so the pipeline retry wrapper can retry.
    discovery_queries: optional list of YouTube queries / TikTok hashtags the user searched;
        used to bias script topics toward the user's selected tags.
    """
    logger.info("Starting content ideation...")

    # generate_scripts raises RuntimeError if all LLMs fail;
    # let it propagate so main.py _run_phase retries the whole phase.
    scripts = generate_scripts(
        category=category,
        count=count,
        discovery_queries=discovery_queries,
    )

    conn = get_connection()
    for script in scripts:
        try:
            variants = generate_title_variants(script)
            if variants and script.get("id"):
                conn.execute(
                    "UPDATE scripts SET title_variants = ? WHERE id = ?",
                    (json.dumps(variants), script["id"]),
                )
        except Exception as e:
            logger.warning("Title variant generation failed for script %s: %s", script.get("id"), e)
    conn.commit()
    conn.close()

    summary = {
        "scripts_generated": len(scripts),
        "category": category or "random",
    }
    logger.info("Ideation complete: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(Path(__file__).parent.parent / "logs" / "ideation.log"),
        ],
    )
    from models.database import init_db
    init_db()
    result = run_ideation()
    print(json.dumps(result, indent=2))

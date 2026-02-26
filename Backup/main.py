"""
Trending Content Shorts Engine - Main Orchestrator

Usage:
    python main.py --setup          Initialize database and verify config
    python main.py --run            Run full pipeline (discover -> ideate -> source -> compose -> cleanup)
    python main.py --upload         Upload all approved videos
    python main.py --cleanup        Run cleanup (delete rejected, archive uploaded, remove orphan assets)
    python main.py --music          Pre-populate royalty-free music library
    python main.py --schedule       Start the background scheduler daemon
    python main.py --status         Show current pipeline status
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"),
    ],
)
logger = logging.getLogger("main")


def cmd_setup():
    """Initialize database and verify configuration."""
    from models.database import init_db
    from models.config import load_config

    logger.info("Initializing database...")
    init_db()
    logger.info("Database initialized at data/yttt.db")

    logger.info("Checking configuration...")
    try:
        config = load_config()
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)

    def _key_valid(val: str | None) -> bool:
        return bool(val) and not val.startswith("YOUR_")

    tiktok_cfg = config.get("tiktok", {})
    tiktok_keys_present = _key_valid(tiktok_cfg.get("client_key")) and _key_valid(tiktok_cfg.get("client_secret"))
    tiktok_enabled = tiktok_cfg.get("enabled", False)

    checks = {
        "Pexels API": _key_valid(config.get("pexels_api_key")),
        "Pixabay API": _key_valid(config.get("pixabay_api_key")),
        "Anthropic API": _key_valid(config.get("anthropic_api_key")),
        "YouTube OAuth": Path(config.get("youtube_client_secret", "config/client_secret.json")).exists(),
    }

    for name, ok in checks.items():
        status = "OK" if ok else "NOT CONFIGURED"
        logger.info("  %s: %s", name, status)

    gemini_key = config.get("gemini_api_key", "")
    if _key_valid(gemini_key):
        logger.info("  Gemini (fallback): OK")
    else:
        logger.info("  Gemini (fallback): NOT SET — optional, get free key at https://aistudio.google.com/apikey")

    if tiktok_keys_present and tiktok_enabled:
        logger.info("  TikTok: OK")
    elif tiktok_keys_present and not tiktok_enabled:
        logger.info("  TikTok: KEYS SET but 'enabled' is false — set tiktok.enabled to true in settings.yaml to activate")
    else:
        logger.info("  TikTok: NOT CONFIGURED — add client_key and client_secret under tiktok in settings.yaml")

    # Verify FFmpeg
    import subprocess
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        logger.info("  FFmpeg: OK")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("  FFmpeg: NOT FOUND - install via 'winget install FFmpeg'")

    # Verify yt-dlp
    try:
        result = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=10)
        logger.info("  yt-dlp: OK (v%s)", result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("  yt-dlp: NOT FOUND - install via 'pip install yt-dlp'")

    logger.info("Setup complete. Configure any missing API keys in config/settings.yaml")


DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAYS = [30, 60, 120]


def _get_retry_config():
    """Load retry settings from config, falling back to defaults."""
    try:
        from models.config import get as cfg
        max_r = cfg("pipeline.max_retries") or DEFAULT_MAX_RETRIES
        delays = cfg("pipeline.retry_delays") or DEFAULT_RETRY_DELAYS
        bonus = cfg("pipeline.ideation_bonus_retry")
        if bonus is None:
            bonus = True
        return int(max_r), [int(d) for d in delays], bool(bonus)
    except Exception:
        return DEFAULT_MAX_RETRIES, DEFAULT_RETRY_DELAYS, True


def _run_phase(name: str, fn, *args, retries: int | None = None, **kwargs) -> dict:
    """Run a pipeline phase with automatic retries on failure.
    Returns the result dict from the phase function, or an error dict.
    """
    max_retries, retry_delays, _ = _get_retry_config()
    if retries is None:
        retries = max_retries

    last_error = None

    for attempt in range(1, retries + 1):
        try:
            result = fn(*args, **kwargs)
            if isinstance(result, dict) and result.get("error"):
                raise RuntimeError(result["error"])
            return result
        except Exception as e:
            last_error = e
            if attempt < retries:
                delay = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                logger.warning(
                    "%s failed (attempt %d/%d): %s — retrying in %ds...",
                    name, attempt, retries, e, delay,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "%s failed after %d attempts: %s", name, retries, e,
                )

    return {"error": str(last_error), "attempts": retries}


def cmd_run(category: str | None = None, count: int | None = None, max_retries: int | None = None):
    """Run the full content pipeline with retry logic and phase gating.
    Each phase retries up to max_retries times on failure (default from config).
    If a critical phase (discovery/ideation) produces no output after all
    retries, later phases still run -- they will pick up any work left
    from previous pipeline runs (e.g. scripts pending assets).
    """
    from models.database import init_db
    init_db()

    retries_arg = max_retries  # CLI override, or None to use config/defaults

    logger.info("=" * 60)
    logger.info("PIPELINE START: %s", datetime.now().isoformat())
    logger.info("=" * 60)

    results = {}

    # Phase 1: Discovery (non-blocking -- later phases can work with existing DB data)
    logger.info("--- Phase 1: Trend Discovery ---")
    from agents.discovery import run_discovery
    results["discovery"] = _run_phase("Discovery", run_discovery, retries=retries_arg)
    logger.info("Discovery: %s", results["discovery"])

    # Phase 2: Ideation (critical -- scripts drive the rest of the pipeline)
    logger.info("--- Phase 2: Content Ideation ---")
    from agents.ideation import run_ideation
    results["ideation"] = _run_phase(
        "Ideation", run_ideation, category=category, count=count,
        retries=retries_arg,
    )
    logger.info("Ideation: %s", results["ideation"])

    scripts_generated = results["ideation"].get("scripts_generated", 0) if isinstance(results["ideation"], dict) else 0
    _, retry_delays, bonus_retry = _get_retry_config()
    if scripts_generated == 0 and bonus_retry:
        wait = retry_delays[-1] if retry_delays else 60
        logger.warning("Ideation produced 0 scripts — waiting %ds then retrying once more...", wait)
        time.sleep(wait)
        results["ideation"] = _run_phase(
            "Ideation (bonus retry)", run_ideation, category=category, count=count,
            retries=retries_arg or 2,
        )
        logger.info("Ideation (retry): %s", results["ideation"])
        scripts_generated = results["ideation"].get("scripts_generated", 0) if isinstance(results["ideation"], dict) else 0

    # Phase 3: Asset Sourcing (processes any scripts with status 'pending_assets')
    logger.info("--- Phase 3: Asset Sourcing ---")
    from agents.sourcing import run_sourcing
    results["sourcing"] = _run_phase("Sourcing", run_sourcing, retries=retries_arg)
    logger.info("Sourcing: %s", results["sourcing"])

    # Phase 4: Video Composition (processes any scripts with status 'assets_ready')
    logger.info("--- Phase 4: Video Composition ---")
    from agents.composer import run_composer
    results["composer"] = _run_phase("Composer", run_composer, retries=retries_arg)
    logger.info("Composer: %s", results["composer"])

    # Phase 5: Cleanup (archive uploaded, delete rejected, remove orphan assets)
    logger.info("--- Phase 5: Cleanup ---")
    from agents.cleanup import run_cleanup
    results["cleanup"] = _run_phase("Cleanup", run_cleanup, retries=1)
    logger.info("Cleanup: %s", results["cleanup"])

    # Summary
    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE")
    for phase, result in results.items():
        logger.info("  %-12s %s", phase.title() + ":", result)
    logger.info("=" * 60)

    # Check overall health
    videos_made = results["composer"].get("success", 0) if isinstance(results["composer"], dict) else 0
    if videos_made > 0:
        _send_notification(f"Pipeline complete! {videos_made} videos ready for review.")
    elif scripts_generated > 0:
        _send_notification("Pipeline complete with warnings — scripts generated but video rendering may have issues. Check logs.")
    else:
        _send_notification("Pipeline finished but produced no new videos. Check logs for errors.")


def cmd_upload():
    """Upload all approved videos."""
    from models.database import init_db
    init_db()
    from agents.uploader import run_uploads
    result = run_uploads()
    logger.info("Upload result: %s", result)


def cmd_regenerate(script_id: int, voice: str | None = None, music_path: str | None = None):
    """Regenerate a single video with optional voice and music overrides."""
    from models.database import init_db, get_connection
    from agents.sourcing import run_sourcing
    from agents.composer import run_composer

    init_db()
    conn = get_connection()

    row = conn.execute("SELECT id, category FROM scripts WHERE id = ?", (script_id,)).fetchone()
    if not row:
        logger.error("Script #%d not found", script_id)
        conn.close()
        sys.exit(1)

    video_ids = [r[0] for r in conn.execute("SELECT id FROM videos WHERE script_id = ?", (script_id,)).fetchall()]
    for vid in video_ids:
        conn.execute("DELETE FROM uploads WHERE video_id = ?", (vid,))
    conn.execute("DELETE FROM videos WHERE script_id = ?", (script_id,))
    conn.execute("DELETE FROM assets WHERE script_id = ?", (script_id,))
    conn.execute("UPDATE scripts SET voice_override = ?, music_override_path = ?, status = ? WHERE id = ?",
                 (voice or None, music_path or None, "pending_assets", script_id))
    conn.commit()
    conn.close()

    logger.info("Regenerating script #%d (voice=%s, music=%s)", script_id, voice or "default", music_path or "default")
    _run_phase("Sourcing", run_sourcing, retries=2)
    _run_phase("Composer", run_composer, retries=2)
    logger.info("Regenerate complete for script #%d", script_id)


def cmd_cleanup():
    """Run cleanup: delete rejected files, archive uploaded, remove orphan assets."""
    from models.database import init_db
    init_db()
    from agents.cleanup import run_cleanup
    result = run_cleanup()

    print("\n=== Cleanup Summary ===\n")
    print(f"  Rejected videos cleaned:    {result.get('rejected_cleaned', 0)}")
    print(f"  Videos archived:            {result.get('archived', 0)}")
    print(f"  Asset files cleaned:        {result.get('assets_cleaned', 0) + result.get('files_deleted', 0)}")
    print(f"  Orphaned files deleted:     {result.get('orphaned_files_deleted', 0)}")
    print()


def cmd_music(categories: list[str] | None = None):
    """Pre-populate the royalty-free music library."""
    from models.database import init_db
    init_db()
    from agents.music_scraper import run_music_scraper, scan_local_library

    result = run_music_scraper(categories)
    library = scan_local_library()

    print("\n=== Music Library ===\n")
    total = 0
    for mood, tracks in sorted(library.items()):
        print(f"  {mood}/: {len(tracks)} tracks")
        for t in tracks[:3]:
            print(f"    - {t.name}")
        if len(tracks) > 3:
            print(f"    ... and {len(tracks) - 3} more")
        total += len(tracks)
    print(f"\n  Total: {total} tracks\n")


def cmd_status():
    """Show current pipeline status."""
    from models.database import init_db, get_connection, get_stats
    init_db()
    conn = get_connection()
    stats = get_stats(conn)

    # Count pending uploads
    pending_yt = conn.execute(
        "SELECT COUNT(*) FROM uploads WHERE platform='youtube' AND upload_status='pending'"
    ).fetchone()[0]
    pending_tt = conn.execute(
        "SELECT COUNT(*) FROM uploads WHERE platform='tiktok' AND upload_status='pending'"
    ).fetchone()[0]
    conn.close()

    print("\n=== Trending Content Shorts Engine - Status ===\n")
    print(f"  Trends discovered:    {stats.get('total_trends', 0)}")
    print(f"  Scripts generated:    {stats.get('total_scripts', 0)}")
    print(f"  Videos created:       {stats.get('total_videos', 0)}")
    print(f"  Pending review:       {stats.get('pending_review', 0)}")
    print(f"  Approved:             {stats.get('approved', 0)}")
    print(f"  Rejected:             {stats.get('rejected', 0)}")
    print(f"  Uploaded:             {stats.get('uploaded', 0)}")
    print(f"  Archived:             {stats.get('archived', 0)}")
    print(f"  Pending YT uploads:   {pending_yt}")
    print(f"  Pending TT uploads:   {pending_tt}")

    # Disk usage
    output_dir = PROJECT_ROOT / "output"
    archive_dir = output_dir / "archive"
    pending_dir = output_dir / "pending"
    assets_dir = PROJECT_ROOT / "assets"

    def _dir_size(d: Path) -> float:
        if not d.exists():
            return 0.0
        return sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / (1024 * 1024)

    print(f"  Pending dir size:     {_dir_size(pending_dir):.1f} MB")
    print(f"  Archive dir size:     {_dir_size(archive_dir):.1f} MB")
    print(f"  Assets dir size:      {_dir_size(assets_dir):.1f} MB")

    # Music library stats
    from agents.music_scraper import scan_local_library
    library = scan_local_library()
    total_tracks = sum(len(t) for t in library.values())
    moods = ", ".join(f"{m}({len(t)})" for m, t in sorted(library.items()) if t)
    print(f"  Music library:        {total_tracks} tracks [{moods or 'empty'}]")
    print()


def cmd_schedule():
    """Start the APScheduler background daemon."""
    from models.database import init_db
    init_db()

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from models.config import get as cfg_get

    tz = cfg_get("scheduler.timezone") or "Europe/London"

    def parse_time(key: str, default: str) -> tuple[int, int]:
        t = cfg_get(f"scheduler.{key}") or default
        parts = t.split(":")
        return int(parts[0]), int(parts[1])

    scheduler = BlockingScheduler(timezone=tz)

    # Discovery job
    h, m = parse_time("discovery_time", "06:00")
    scheduler.add_job(
        _job_discovery, CronTrigger(hour=h, minute=m, timezone=tz),
        id="discovery", name="Trend Discovery", replace_existing=True,
    )

    # Ideation job
    h, m = parse_time("ideation_time", "06:30")
    scheduler.add_job(
        _job_ideation, CronTrigger(hour=h, minute=m, timezone=tz),
        id="ideation", name="Content Ideation", replace_existing=True,
    )

    # Sourcing job
    h, m = parse_time("sourcing_time", "07:00")
    scheduler.add_job(
        _job_sourcing, CronTrigger(hour=h, minute=m, timezone=tz),
        id="sourcing", name="Asset Sourcing", replace_existing=True,
    )

    # Composing job
    h, m = parse_time("composing_time", "07:30")
    scheduler.add_job(
        _job_composing, CronTrigger(hour=h, minute=m, timezone=tz),
        id="composing", name="Video Composing", replace_existing=True,
    )

    # Cleanup job (runs 30 min after composing)
    h, m = parse_time("cleanup_time", "08:00")
    scheduler.add_job(
        _job_cleanup, CronTrigger(hour=h, minute=m, timezone=tz),
        id="cleanup", name="Cleanup", replace_existing=True,
    )

    # Notification job
    h, m = parse_time("notification_time", "08:30")
    scheduler.add_job(
        lambda: _send_notification("Videos are ready for review!"),
        CronTrigger(hour=h, minute=m, timezone=tz),
        id="notification", name="Review Notification", replace_existing=True,
    )

    logger.info("Scheduler started with timezone: %s", tz)
    logger.info("Jobs:")
    for job in scheduler.get_jobs():
        logger.info("  %s: %s", job.name, job.trigger)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


def _job_discovery():
    from agents.discovery import run_discovery
    result = _run_phase("Scheduled Discovery", run_discovery)
    logger.info("Scheduled discovery result: %s", result)


def _job_ideation():
    from agents.ideation import run_ideation
    result = _run_phase("Scheduled Ideation", run_ideation)
    logger.info("Scheduled ideation result: %s", result)


def _job_sourcing():
    from agents.sourcing import run_sourcing
    result = _run_phase("Scheduled Sourcing", run_sourcing)
    logger.info("Scheduled sourcing result: %s", result)


def _job_composing():
    from agents.composer import run_composer
    result = _run_phase("Scheduled Composing", run_composer)
    logger.info("Scheduled composing result: %s", result)


def _job_cleanup():
    from agents.cleanup import run_cleanup
    result = _run_phase("Scheduled Cleanup", run_cleanup, retries=1)
    logger.info("Scheduled cleanup result: %s", result)


def _send_notification(message: str):
    """Send a desktop notification (Windows toast)."""
    try:
        import subprocess
        subprocess.run(
            [
                "powershell",
                "-Command",
                f"[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; "
                f"[System.Windows.Forms.MessageBox]::Show('{message}', 'Shorts Engine')",
            ],
            timeout=10,
            capture_output=True,
        )
    except Exception:
        logger.info("NOTIFICATION: %s", message)


def main():
    parser = argparse.ArgumentParser(description="Trending Content Shorts Engine")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--setup", action="store_true", help="Initialize database and verify config")
    group.add_argument("--run", action="store_true", help="Run full pipeline")
    group.add_argument("--discovery-only", action="store_true", help="Run only discovery phase")
    group.add_argument("--upload", action="store_true", help="Upload approved videos")
    group.add_argument("--cleanup", action="store_true", help="Delete rejected files, archive uploaded, clean orphan assets")
    group.add_argument("--music", action="store_true", help="Pre-populate music library")
    group.add_argument("--schedule", action="store_true", help="Start scheduler daemon")
    group.add_argument("--status", action="store_true", help="Show pipeline status")
    group.add_argument("--regenerate", action="store_true", help="Regenerate a single video with overrides")

    parser.add_argument("--script-id", type=int, help="Script ID for regenerate")
    parser.add_argument("--voice", type=str, help="Voice override for regenerate (e.g. en-US-AndrewMultilingualNeural)")
    parser.add_argument("--music-path", type=str, help="Music file path override for regenerate")
    parser.add_argument("--category", type=str, help="Content category for ideation (motivational/funny/meme/news/storytime)")
    parser.add_argument("--count", type=int, help="Number of scripts to generate")
    parser.add_argument("--retries", type=int, help="Override max retries per phase (default: from config or 3)")

    args = parser.parse_args()

    if args.setup:
        cmd_setup()
    elif args.discovery_only:
        from models.database import init_db
        init_db()
        logger.info("--- Discovery Only ---")
        from agents.discovery import run_discovery
        result = run_discovery()
        logger.info("Discovery: %s", result)
    elif args.run:
        cmd_run(category=args.category, count=args.count, max_retries=args.retries)
    elif args.upload:
        cmd_upload()
    elif args.cleanup:
        cmd_cleanup()
    elif args.music:
        cmd_music()
    elif args.schedule:
        cmd_schedule()
    elif args.status:
        cmd_status()
    elif args.regenerate:
        if not args.script_id:
            logger.error("--regenerate requires --script-id")
            sys.exit(1)
        cmd_regenerate(args.script_id, voice=args.voice, music_path=args.music_path)


if __name__ == "__main__":
    main()

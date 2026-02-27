"""
Cleanup Agent
Handles post-pipeline housekeeping:
  1. Delete rejected video files and their assets
  2. Archive uploaded videos (rename to match YouTube title)
  3. Remove intermediate assets (B-roll, voiceover, music) once no longer needed
"""

import logging
import re
import shutil
from pathlib import Path

from models.database import (
    get_connection,
    get_rejected_videos,
    get_uploaded_videos,
    get_script_asset_paths,
    get_assets_for_script,
    is_asset_shared,
    delete_assets_for_script,
    mark_video_archived,
    delete_rejected_video_record,
    record_rejected_trend_sources,
    insert_rejection_feedback,
    insert_rejection_assets,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
ARCHIVE_DIR = PROJECT_ROOT / "output" / "archive"
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def _safe_filename(title: str) -> str:
    """Convert a video title to a safe filesystem name."""
    clean = re.sub(r'[<>:"/\\|?*]', '', title)
    clean = re.sub(r'\s+', ' ', clean).strip()
    clean = clean[:120]
    if not clean:
        clean = "untitled"
    return clean


def _delete_file(path: str | Path) -> bool:
    p = Path(path)
    if p.exists():
        try:
            p.unlink()
            logger.debug("Deleted: %s", p)
            return True
        except OSError as e:
            logger.warning("Failed to delete %s: %s", p, e)
    return False


def _cleanup_script_assets(script_id: int, conn) -> int:
    """Delete intermediate asset files for a script.
    Skips shared files (referenced by other scripts).
    Returns count of files deleted.
    """
    assets = get_script_asset_paths(conn, script_id)
    deleted = 0

    for asset in assets:
        path = asset["local_path"]
        if not Path(path).exists():
            continue

        # Don't delete music files that live in the shared music library
        if asset["asset_type"] == "music" and "music" in str(Path(path).parent).lower():
            continue

        if is_asset_shared(conn, path, exclude_script_id=script_id):
            logger.debug("Skipping shared asset: %s", path)
            continue

        if _delete_file(path):
            deleted += 1

    delete_assets_for_script(conn, script_id)
    return deleted


def cleanup_single_rejected_video(video_id: int, defer_asset_cleanup: bool = False) -> dict:
    """Immediately delete files and DB entries for a single rejected video.
    Call this when user rejects a video in the review UI.
    When defer_asset_cleanup=True (e.g. pipeline is running), skip deleting script assets
    to avoid affecting in-progress composition. Asset cleanup will run on next --cleanup.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT v.*, s.id as sid FROM videos v JOIN scripts s ON v.script_id = s.id WHERE v.id = ? AND v.status = 'rejected'",
        (video_id,),
    ).fetchone()
    conn.close()
    if not row:
        return {"cleaned": 0, "error": "Video not found or not rejected"}

    video = dict(row)
    script_id = video.get("script_id") or video.get("sid")
    reason = video.get("rejection_reason") or ""

    # Record trend sources so they are excluded from future ideation
    if script_id:
        conn2 = get_connection()
        record_rejected_trend_sources(conn2, script_id, reason=reason)
        conn2.close()

    conn = get_connection()
    files_deleted = 0

    if _delete_file(video.get("file_path", "")):
        files_deleted += 1
    if video.get("thumbnail_path") and _delete_file(video["thumbnail_path"]):
        files_deleted += 1

    if script_id and not defer_asset_cleanup:
        # Capture rejection assets before deletion (for negative scoring)
        script_row = conn.execute("SELECT category FROM scripts WHERE id = ?", (script_id,)).fetchone()
        category = script_row["category"] if script_row else "storytime"
        assets = get_assets_for_script(conn, script_id)
        rejection_id = insert_rejection_feedback(conn, video_id, script_id, category, reason)
        for a in assets:
            atype = a["asset_type"]
            if atype not in ("music", "video", "image"):
                continue
            path_str = a.get("local_path", "")
            fname = Path(path_str).name if path_str else ""
            src = a.get("source", "") or ""
            sid = str(a.get("source_id", "")) if a.get("source_id") is not None else ""
            if not fname and src and sid:
                fname = f"{src}_{sid}.mp4" if atype == "video" else f"{src}_{sid}.jpg" if atype == "image" else f"{src}_{sid}.mp3"
            # For online music, extract raw id for lookup (yt_xxx_title -> xxx, fs_123_name -> 123)
            if atype == "music" and fname and src in ("youtube", "freesound", "openverse"):
                stem = Path(fname).stem
                if stem.startswith("yt_") and "_" in stem[3:]:
                    sid = stem.split("_", 2)[1]  # video_id
                elif stem.startswith("fs_") and "_" in stem[3:]:
                    sid = stem.split("_", 2)[1]
                elif stem.startswith("ov_") and "_" in stem[3:]:
                    sid = stem.split("_", 2)[1]
            if fname:
                size_bytes, mtime_real = None, None
                if src in ("user", "user_selected", "local") and path_str:
                    try:
                        st = Path(path_str).stat()
                        size_bytes, mtime_real = st.st_size, st.st_mtime
                    except OSError:
                        pass
                insert_rejection_assets(conn, rejection_id, atype, fname, src or None, sid or None, size_bytes, mtime_real)
        files_deleted += _cleanup_script_assets(script_id, conn)

    if not defer_asset_cleanup:
        delete_rejected_video_record(conn, video_id)
    # When deferring: keep video record so cleanup_rejected can run later (e.g. after pipeline finishes)
    conn.close()
    logger.info("Immediately cleaned rejected video #%d (defer_assets=%s)", video_id, defer_asset_cleanup)
    return {"cleaned": 1, "files_deleted": files_deleted}


def cleanup_rejected() -> dict:
    """Delete all files for rejected videos and their associated assets."""
    conn = get_connection()
    rejected = get_rejected_videos(conn)

    if not rejected:
        logger.info("No rejected videos to clean up")
        conn.close()
        return {"rejected_cleaned": 0}

    cleaned = 0
    files_deleted = 0

    for video in rejected:
        video_path = video.get("file_path", "")
        thumb_path = video.get("thumbnail_path", "")
        script_id = video.get("script_id") or video.get("sid")
        reason = video.get("rejection_reason") or ""

        # Record trend sources so they are excluded from future ideation
        if script_id:
            record_rejected_trend_sources(conn, script_id, reason=reason)

        if _delete_file(video_path):
            files_deleted += 1
        if thumb_path and _delete_file(thumb_path):
            files_deleted += 1

        if script_id:
            # Capture rejection assets before deletion (for negative scoring)
            script_row = conn.execute("SELECT category FROM scripts WHERE id = ?", (script_id,)).fetchone()
            category = script_row["category"] if script_row else "storytime"
            assets = get_assets_for_script(conn, script_id)
            rejection_id = insert_rejection_feedback(conn, video["id"], script_id, category, reason)
            for a in assets:
                atype = a["asset_type"]
                if atype not in ("music", "video", "image"):
                    continue
                path_str = a.get("local_path", "")
                fname = Path(path_str).name if path_str else ""
                src = a.get("source", "") or ""
                sid = str(a.get("source_id", "")) if a.get("source_id") is not None else ""
                if not fname and src and sid:
                    fname = f"{src}_{sid}.mp4" if atype == "video" else f"{src}_{sid}.jpg" if atype == "image" else f"{src}_{sid}.mp3"
                if atype == "music" and fname and src in ("youtube", "freesound", "openverse"):
                    stem = Path(fname).stem
                    if stem.startswith("yt_") and "_" in stem[3:]:
                        sid = stem.split("_", 2)[1]
                    elif stem.startswith("fs_") and "_" in stem[3:]:
                        sid = stem.split("_", 2)[1]
                    elif stem.startswith("ov_") and "_" in stem[3:]:
                        sid = stem.split("_", 2)[1]
                if fname:
                    size_bytes, mtime_real = None, None
                    if src in ("user", "user_selected", "local") and path_str:
                        try:
                            st = Path(path_str).stat()
                            size_bytes, mtime_real = st.st_size, st.st_mtime
                        except OSError:
                            pass
                    insert_rejection_assets(conn, rejection_id, atype, fname, src or None, sid or None, size_bytes, mtime_real)
            files_deleted += _cleanup_script_assets(script_id, conn)

        delete_rejected_video_record(conn, video["id"])
        cleaned += 1
        logger.info("Cleaned rejected video #%d (script #%s)", video["id"], script_id)

    conn.close()
    return {"rejected_cleaned": cleaned, "files_deleted": files_deleted}


def archive_uploaded() -> dict:
    """Archive uploaded videos: rename to match YouTube title and move to archive folder."""
    conn = get_connection()
    uploaded = get_uploaded_videos(conn)

    if not uploaded:
        logger.info("No uploaded videos to archive")
        conn.close()
        return {"archived": 0}

    archived = 0
    files_cleaned = 0
    seen_video_ids = set()

    for record in uploaded:
        vid = record["id"]
        if vid in seen_video_ids:
            continue
        seen_video_ids.add(vid)

        video_path = Path(record.get("file_path", ""))
        thumb_path = record.get("thumbnail_path", "")
        yt_title = record.get("yt_title", "")
        script_id = record.get("script_id") or record.get("sid")

        if not video_path.exists():
            # Already moved or missing — just mark archived
            mark_video_archived(conn, vid, str(video_path))
            archived += 1
            continue

        safe_title = _safe_filename(yt_title) if yt_title else video_path.stem
        archive_name = f"{safe_title}{video_path.suffix}"
        archive_path = ARCHIVE_DIR / archive_name

        # Handle name collisions
        counter = 1
        while archive_path.exists():
            archive_name = f"{safe_title}_{counter}{video_path.suffix}"
            archive_path = ARCHIVE_DIR / archive_name
            counter += 1

        try:
            shutil.move(str(video_path), str(archive_path))
            logger.info("Archived: %s -> archive/%s", video_path.name, archive_name)
        except OSError as e:
            logger.error("Failed to archive %s: %s", video_path, e)
            continue

        # Archive thumbnail alongside video
        if thumb_path and Path(thumb_path).exists():
            thumb_archive = archive_path.with_suffix(".jpg")
            try:
                shutil.move(str(thumb_path), str(thumb_archive))
            except OSError:
                pass

        mark_video_archived(conn, vid, str(archive_path))

        # Clean up intermediate assets for this script
        if script_id:
            files_cleaned += _cleanup_script_assets(script_id, conn)

        archived += 1

    conn.close()
    return {"archived": archived, "assets_cleaned": files_cleaned}


def cleanup_orphaned_assets() -> dict:
    """Remove asset files on disk that have no DB reference, or belong to
    scripts that are fully done (archived/rejected).
    """
    conn = get_connection()
    deleted = 0

    terminal_statuses = ("archived", "rejected", "uploaded")
    rows = conn.execute(
        f"""SELECT DISTINCT s.id FROM scripts s
            WHERE s.status IN ({','.join('?' for _ in terminal_statuses)})""",
        terminal_statuses,
    ).fetchall()

    for row in rows:
        script_id = row[0]
        assets = get_script_asset_paths(conn, script_id)
        for asset in assets:
            path = asset["local_path"]
            if not Path(path).exists():
                continue
            if asset["asset_type"] == "music" and "music" in str(Path(path).parent).lower():
                continue
            if is_asset_shared(conn, path, exclude_script_id=script_id):
                continue
            if _delete_file(path):
                deleted += 1
        delete_assets_for_script(conn, script_id)

    # Remove empty subdirectories in assets/
    assets_root = PROJECT_ROOT / "assets"
    if assets_root.exists():
        for subdir in assets_root.rglob("*"):
            if subdir.is_dir() and not any(subdir.iterdir()):
                try:
                    subdir.rmdir()
                except OSError:
                    pass

    conn.close()
    return {"orphaned_files_deleted": deleted}


def run_cleanup() -> dict:
    """Run the full cleanup pipeline: rejected -> archive -> orphans."""
    logger.info("Starting cleanup...")

    rejected_result = cleanup_rejected()
    logger.info("Rejected cleanup: %s", rejected_result)

    archive_result = archive_uploaded()
    logger.info("Archive: %s", archive_result)

    orphan_result = cleanup_orphaned_assets()
    logger.info("Orphan cleanup: %s", orphan_result)

    summary = {**rejected_result, **archive_result, **orphan_result}
    logger.info("Cleanup complete: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(PROJECT_ROOT / "logs" / "cleanup.log"),
        ],
    )
    from models.database import init_db
    init_db()
    result = run_cleanup()
    import json
    print(json.dumps(result, indent=2))

import sqlite3
import json
import os
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "yttt.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,          -- 'youtube' or 'tiktok'
            video_id TEXT,
            title TEXT,
            description TEXT,
            tags TEXT,                       -- JSON array
            category TEXT,                   -- motivational, funny, meme, news, storytime, other
            view_count INTEGER DEFAULT 0,
            like_count INTEGER DEFAULT 0,
            comment_count INTEGER DEFAULT 0,
            share_count INTEGER DEFAULT 0,
            channel_name TEXT,
            publish_date TEXT,
            trend_score REAL DEFAULT 0.0,    -- views / hours_since_publish
            sound_name TEXT,                 -- TikTok trending sound
            scraped_at TEXT NOT NULL,
            UNIQUE(platform, video_id)
        );

        CREATE TABLE IF NOT EXISTS trending_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag TEXT NOT NULL,
            platform TEXT NOT NULL,
            frequency INTEGER DEFAULT 1,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            UNIQUE(tag, platform)
        );

        CREATE TABLE IF NOT EXISTS scripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            hook TEXT NOT NULL,
            script_body TEXT NOT NULL,
            cta TEXT NOT NULL,
            suggested_tags TEXT,             -- JSON array
            visual_cues TEXT,                -- JSON array of search terms
            estimated_duration INTEGER,
            title_variants TEXT,             -- JSON array of alternative titles
            trend_source_ids TEXT,           -- JSON array of trend IDs that inspired this
            status TEXT DEFAULT 'pending_assets',  -- pending_assets, assets_ready, composing, composed, approved, rejected, uploaded
            created_at TEXT NOT NULL,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            script_id INTEGER,
            asset_type TEXT NOT NULL,        -- 'video', 'image', 'music', 'voiceover'
            source TEXT NOT NULL,            -- 'pexels', 'pixabay', 'edge-tts', 'local'
            source_id TEXT,                  -- API ID from source
            source_url TEXT,
            local_path TEXT NOT NULL,
            search_query TEXT,
            duration REAL,                   -- seconds, for video/audio
            width INTEGER,
            height INTEGER,
            mood TEXT,                       -- for music: uplifting, dramatic, etc.
            downloaded_at TEXT NOT NULL,
            FOREIGN KEY (script_id) REFERENCES scripts(id)
        );

        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            script_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            thumbnail_path TEXT,
            duration REAL,
            resolution TEXT,                 -- '1080x1920'
            file_size_mb REAL,
            yt_title TEXT,
            yt_description TEXT,
            yt_tags TEXT,                    -- JSON array
            tt_caption TEXT,
            status TEXT DEFAULT 'pending',   -- pending, approved, rejected, uploaded
            rejection_reason TEXT,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            FOREIGN KEY (script_id) REFERENCES scripts(id)
        );

        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER NOT NULL,
            platform TEXT NOT NULL,          -- 'youtube' or 'tiktok'
            platform_video_id TEXT,          -- YouTube/TikTok video ID after upload
            platform_url TEXT,
            upload_status TEXT DEFAULT 'pending',  -- pending, uploading, success, failed
            error_message TEXT,
            scheduled_time TEXT,
            uploaded_at TEXT,
            FOREIGN KEY (video_id) REFERENCES videos(id)
        );

        CREATE INDEX IF NOT EXISTS idx_trends_platform ON trends(platform);
        CREATE INDEX IF NOT EXISTS idx_trends_category ON trends(category);
        CREATE INDEX IF NOT EXISTS idx_trends_score ON trends(trend_score DESC);
        CREATE INDEX IF NOT EXISTS idx_scripts_status ON scripts(status);
        CREATE INDEX IF NOT EXISTS idx_assets_script ON assets(script_id);
        CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
        CREATE INDEX IF NOT EXISTS idx_uploads_status ON uploads(upload_status);
    """)
    _migrate_schema(conn)
    conn.commit()
    conn.close()


def _migrate_schema(conn: sqlite3.Connection):
    """Add new columns to existing databases."""
    try:
        conn.execute("ALTER TABLE scripts ADD COLUMN voice_override TEXT")
    except sqlite3.OperationalError:
        pass  # Column exists
    try:
        conn.execute("ALTER TABLE scripts ADD COLUMN music_override_path TEXT")
    except sqlite3.OperationalError:
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Trends ---

def insert_trend(conn: sqlite3.Connection, **kwargs) -> int | None:
    kwargs.setdefault("scraped_at", _now())
    if "tags" in kwargs and isinstance(kwargs["tags"], list):
        kwargs["tags"] = json.dumps(kwargs["tags"])

    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    try:
        cur = conn.execute(
            f"INSERT OR IGNORE INTO trends ({cols}) VALUES ({placeholders})",
            list(kwargs.values()),
        )
        conn.commit()
        return cur.lastrowid if cur.lastrowid else None
    except sqlite3.Error:
        return None


def get_top_trends(conn: sqlite3.Connection, limit: int = 50, hours: int = 48) -> list[dict]:
    cutoff = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        """SELECT * FROM trends
           WHERE scraped_at >= datetime(?, '-' || ? || ' hours')
           ORDER BY trend_score DESC LIMIT ?""",
        (cutoff, hours, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def update_trending_tag(conn: sqlite3.Connection, tag: str, platform: str):
    now = _now()
    conn.execute(
        """INSERT INTO trending_tags (tag, platform, frequency, first_seen, last_seen)
           VALUES (?, ?, 1, ?, ?)
           ON CONFLICT(tag, platform) DO UPDATE SET
             frequency = frequency + 1,
             last_seen = ?""",
        (tag, platform, now, now, now),
    )
    conn.commit()


def get_top_tags(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM trending_tags ORDER BY frequency DESC, last_seen DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# --- Scripts ---

def insert_script(conn: sqlite3.Connection, **kwargs) -> int:
    kwargs.setdefault("created_at", _now())
    for key in ("suggested_tags", "visual_cues", "title_variants", "trend_source_ids"):
        if key in kwargs and isinstance(kwargs[key], list):
            kwargs[key] = json.dumps(kwargs[key])

    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    cur = conn.execute(
        f"INSERT INTO scripts ({cols}) VALUES ({placeholders})",
        list(kwargs.values()),
    )
    conn.commit()
    return cur.lastrowid


def get_scripts_by_status(conn: sqlite3.Connection, status: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM scripts WHERE status = ? ORDER BY created_at DESC", (status,)
    ).fetchall()
    return [dict(r) for r in rows]


def update_script_status(conn: sqlite3.Connection, script_id: int, status: str):
    conn.execute(
        "UPDATE scripts SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now(), script_id),
    )
    conn.commit()


# --- Assets ---

def insert_asset(conn: sqlite3.Connection, **kwargs) -> int:
    kwargs.setdefault("downloaded_at", _now())
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    cur = conn.execute(
        f"INSERT INTO assets ({cols}) VALUES ({placeholders})",
        list(kwargs.values()),
    )
    conn.commit()
    return cur.lastrowid


def get_assets_for_script(conn: sqlite3.Connection, script_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM assets WHERE script_id = ? ORDER BY asset_type, id", (script_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# --- Videos ---

def insert_video(conn: sqlite3.Connection, **kwargs) -> int:
    kwargs.setdefault("created_at", _now())
    for key in ("yt_tags",):
        if key in kwargs and isinstance(kwargs[key], list):
            kwargs[key] = json.dumps(kwargs[key])

    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    cur = conn.execute(
        f"INSERT INTO videos ({cols}) VALUES ({placeholders})",
        list(kwargs.values()),
    )
    conn.commit()
    return cur.lastrowid


def get_videos_by_status(conn: sqlite3.Connection, status: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM videos WHERE status = ? ORDER BY created_at DESC", (status,)
    ).fetchall()
    return [dict(r) for r in rows]


def update_video_status(conn: sqlite3.Connection, video_id: int, status: str, **extras):
    extras["status"] = status
    extras["reviewed_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in extras.keys())
    conn.execute(
        f"UPDATE videos SET {sets} WHERE id = ?",
        list(extras.values()) + [video_id],
    )
    conn.commit()


# --- Uploads ---

def insert_upload(conn: sqlite3.Connection, **kwargs) -> int:
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    cur = conn.execute(
        f"INSERT INTO uploads ({cols}) VALUES ({placeholders})",
        list(kwargs.values()),
    )
    conn.commit()
    return cur.lastrowid


def update_upload(conn: sqlite3.Connection, upload_id: int, **kwargs):
    sets = ", ".join(f"{k} = ?" for k in kwargs.keys())
    conn.execute(
        f"UPDATE uploads SET {sets} WHERE id = ?",
        list(kwargs.values()) + [upload_id],
    )
    conn.commit()


def get_pending_uploads(conn: sqlite3.Connection, platform: str | None = None) -> list[dict]:
    query = "SELECT u.*, v.file_path, v.yt_title, v.yt_description, v.yt_tags, v.tt_caption, v.thumbnail_path FROM uploads u JOIN videos v ON u.video_id = v.id WHERE u.upload_status = 'pending'"
    params = []
    if platform:
        query += " AND u.platform = ?"
        params.append(platform)
    query += " ORDER BY u.scheduled_time ASC NULLS LAST"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# --- Stuck Job Recovery ---

def get_stuck_scripts(conn: sqlite3.Connection, status: str, stuck_minutes: int = 30) -> list[dict]:
    """Find scripts stuck in a transient status for longer than stuck_minutes."""
    rows = conn.execute(
        """SELECT * FROM scripts
           WHERE status = ?
             AND updated_at < datetime('now', '-' || ? || ' minutes')
           ORDER BY updated_at ASC""",
        (status, stuck_minutes),
    ).fetchall()
    return [dict(r) for r in rows]


def reset_stuck_scripts(conn: sqlite3.Connection, from_status: str, to_status: str, stuck_minutes: int = 30) -> int:
    """Reset scripts stuck in from_status back to to_status. Returns count reset."""
    now = _now()
    cur = conn.execute(
        """UPDATE scripts SET status = ?, updated_at = ?
           WHERE status = ?
             AND updated_at < datetime('now', '-' || ? || ' minutes')""",
        (to_status, now, from_status, stuck_minutes),
    )
    conn.commit()
    return cur.rowcount


# --- Cleanup Queries ---

def get_rejected_videos(conn: sqlite3.Connection) -> list[dict]:
    """Get all rejected videos that still have files on disk."""
    rows = conn.execute(
        "SELECT v.*, s.id as sid FROM videos v JOIN scripts s ON v.script_id = s.id WHERE v.status = 'rejected'"
    ).fetchall()
    return [dict(r) for r in rows]


def get_uploaded_videos(conn: sqlite3.Connection) -> list[dict]:
    """Get uploaded videos that haven't been archived yet (file still in pending dir)."""
    rows = conn.execute(
        """SELECT v.*, s.id as sid,
                  u.platform_video_id, u.platform_url, u.platform
           FROM videos v
           JOIN scripts s ON v.script_id = s.id
           JOIN uploads u ON u.video_id = v.id
           WHERE v.status = 'uploaded'
             AND u.upload_status = 'success'
           ORDER BY v.id"""
    ).fetchall()
    return [dict(r) for r in rows]


def get_script_asset_paths(conn: sqlite3.Connection, script_id: int) -> list[dict]:
    """Get all asset file paths for a script."""
    rows = conn.execute(
        "SELECT id, local_path, asset_type FROM assets WHERE script_id = ?", (script_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def is_asset_shared(conn: sqlite3.Connection, local_path: str, exclude_script_id: int) -> bool:
    """Check if an asset file is referenced by another script (shared B-roll)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM assets WHERE local_path = ? AND script_id != ?",
        (local_path, exclude_script_id),
    ).fetchone()
    return row[0] > 0


def delete_assets_for_script(conn: sqlite3.Connection, script_id: int):
    """Remove asset DB records for a script."""
    conn.execute("DELETE FROM assets WHERE script_id = ?", (script_id,))
    conn.commit()


def mark_video_archived(conn: sqlite3.Connection, video_id: int, archive_path: str):
    """Update the video record with the archived file path."""
    conn.execute(
        "UPDATE videos SET file_path = ?, status = 'archived', reviewed_at = ? WHERE id = ?",
        (archive_path, _now(), video_id),
    )
    conn.commit()


def delete_rejected_video_record(conn: sqlite3.Connection, video_id: int):
    """Remove video and upload records for a rejected video (call after files are deleted)."""
    conn.execute("DELETE FROM uploads WHERE video_id = ?", (video_id,))
    conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    conn.commit()


# --- Stats ---

def get_stats(conn: sqlite3.Connection) -> dict:
    stats = {}
    stats["total_trends"] = conn.execute("SELECT COUNT(*) FROM trends").fetchone()[0]
    stats["total_scripts"] = conn.execute("SELECT COUNT(*) FROM scripts").fetchone()[0]
    stats["total_videos"] = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    stats["pending_review"] = conn.execute("SELECT COUNT(*) FROM videos WHERE status = 'pending'").fetchone()[0]
    stats["approved"] = conn.execute("SELECT COUNT(*) FROM videos WHERE status = 'approved'").fetchone()[0]
    stats["uploaded"] = conn.execute("SELECT COUNT(*) FROM videos WHERE status = 'uploaded'").fetchone()[0]
    stats["rejected"] = conn.execute("SELECT COUNT(*) FROM videos WHERE status = 'rejected'").fetchone()[0]
    stats["archived"] = conn.execute("SELECT COUNT(*) FROM videos WHERE status = 'archived'").fetchone()[0]
    return stats

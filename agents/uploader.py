"""
Upload Agent
Uploads approved videos to YouTube Shorts and TikTok.
"""

import json
import logging
import os
import shutil
import time
from pathlib import Path

from models.config import get as cfg
from models.database import (
    get_connection,
    get_pending_uploads,
    update_upload,
    update_video_status,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
PUBLISHED_DIR = PROJECT_ROOT / "output" / "published"
PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)


# --- YouTube Upload ---

def _get_youtube_service():
    """Build an authenticated YouTube API service using OAuth2."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    client_secret_path = Path(cfg("youtube_client_secret") or "config/client_secret.json")
    token_path = Path(cfg("youtube_token") or "config/token.json")
    scopes = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not client_secret_path.exists():
                logger.error(
                    "YouTube client_secret.json not found at %s. "
                    "Download from Google Cloud Console.", client_secret_path
                )
                return None
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), scopes)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def upload_to_youtube(upload_record: dict) -> bool:
    """Upload a single video to YouTube Shorts."""
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    video_path = upload_record.get("file_path")
    if not video_path or not Path(video_path).exists():
        logger.error("Video file not found: %s", video_path)
        update_upload(get_connection(), upload_record["id"], upload_status="failed", error_message="File not found")
        return False

    service = _get_youtube_service()
    if not service:
        update_upload(get_connection(), upload_record["id"], upload_status="failed", error_message="YouTube auth failed")
        return False

    title = upload_record.get("yt_title", "Untitled")[:100]
    description = upload_record.get("yt_description", "")
    tags_json = upload_record.get("yt_tags", "[]")
    try:
        tags = json.loads(tags_json) if isinstance(tags_json, str) else tags_json
    except json.JSONDecodeError:
        tags = []

    category_id = cfg("upload.youtube.category_id") or "22"

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags[:30],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": cfg("upload.youtube.privacy_status") or "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=1024 * 1024)

    conn = get_connection()
    update_upload(conn, upload_record["id"], upload_status="uploading")

    try:
        request = service.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info("YouTube upload progress: %d%%", int(status.progress() * 100))

        yt_video_id = response.get("id")
        yt_url = f"https://youtube.com/shorts/{yt_video_id}"
        logger.info("YouTube upload success: %s", yt_url)

        update_upload(
            conn,
            upload_record["id"],
            upload_status="success",
            platform_video_id=yt_video_id,
            platform_url=yt_url,
            uploaded_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        )

        # Upload thumbnail if available
        if upload_record.get("thumbnail_path") and Path(upload_record["thumbnail_path"]).exists():
            try:
                thumb_media = MediaFileUpload(upload_record["thumbnail_path"], mimetype="image/jpeg")
                service.thumbnails().set(videoId=yt_video_id, media_body=thumb_media).execute()
                logger.info("Thumbnail uploaded for %s", yt_video_id)
            except HttpError as e:
                logger.warning("Thumbnail upload failed (may need channel verification): %s", e)

        conn.close()
        return True

    except HttpError as e:
        error_msg = str(e)[:500]
        logger.error("YouTube upload failed: %s", error_msg)
        update_upload(conn, upload_record["id"], upload_status="failed", error_message=error_msg)
        conn.close()
        return False


# --- TikTok Upload ---

def upload_to_tiktok(upload_record: dict) -> bool:
    """Upload video to TikTok via Content Posting API.

    Note: TikTok's Content Posting API requires approved developer access.
    If not available, videos are placed in output/approved/tiktok/ for manual upload.
    """
    tiktok_enabled = cfg("tiktok.enabled")
    if not tiktok_enabled:
        logger.info("TikTok upload disabled. Video ready for manual upload.")
        conn = get_connection()
        update_upload(
            conn,
            upload_record["id"],
            upload_status="failed",
            error_message="TikTok API not enabled. Upload manually from output/approved/",
        )
        conn.close()
        return False

    from agents.tiktok_auth import get_tiktok_access_token

    access_token = get_tiktok_access_token()
    if not access_token:
        logger.warning("TikTok OAuth token not configured. Run OAuth flow (Setup > TikTok > Connect)")
        conn = get_connection()
        update_upload(
            conn,
            upload_record["id"],
            upload_status="failed",
            error_message="TikTok OAuth required. Go to Setup > TikTok and click Connect.",
        )
        conn.close()
        return False

    # TikTok Content Posting API flow:
    # 1. Initialize upload to get upload_url
    # 2. Upload video binary to upload_url
    # 3. Publish with caption/hashtags
    # This requires an approved TikTok developer account with Content Posting API access.

    import requests

    video_path = upload_record.get("file_path")
    if not video_path or not Path(video_path).exists():
        logger.error("Video file not found: %s", video_path)
        return False

    caption = upload_record.get("tt_caption", "")[:2200]
    file_size = Path(video_path).stat().st_size

    conn = get_connection()
    update_upload(conn, upload_record["id"], upload_status="uploading")

    def _do_init(token: str):
        init_url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
        hdrs = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        init_body = {
            "post_info": {
                "title": caption,
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": file_size,
                "total_chunk_count": 1,
            },
        }
        return requests.post(init_url, json=init_body, headers=hdrs, timeout=30)

    try:
        # Step 1: Init upload (retry once with refreshed token on 401)
        resp = _do_init(access_token)
        if resp.status_code == 401:
            from agents.tiktok_auth import refresh_and_get_token
            fresh = refresh_and_get_token()
            if fresh:
                resp = _do_init(fresh)
        resp.raise_for_status()
        init_data = resp.json().get("data", {})
        upload_url = init_data.get("upload_url")
        publish_id = init_data.get("publish_id")

        if not upload_url:
            raise ValueError(f"No upload_url in TikTok response: {resp.text[:200]}")

        # Step 2: Upload video
        with open(video_path, "rb") as f:
            upload_resp = requests.put(
                upload_url,
                data=f,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
                },
                timeout=300,
            )
            upload_resp.raise_for_status()

        logger.info("TikTok upload success (publish_id: %s)", publish_id)
        update_upload(
            conn,
            upload_record["id"],
            upload_status="success",
            platform_video_id=publish_id,
            uploaded_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        )
        conn.close()
        return True

    except Exception as e:
        error_msg = str(e)[:500]
        logger.error("TikTok upload failed: %s", error_msg)
        update_upload(conn, upload_record["id"], upload_status="failed", error_message=error_msg)
        conn.close()
        return False


# --- Main upload pipeline ---

def _archive_video(video_path: str):
    """Move uploaded video to published directory (legacy — cleanup agent handles rename)."""
    src = Path(video_path)
    if src.exists():
        dest = PUBLISHED_DIR / src.name
        shutil.move(str(src), str(dest))
        logger.info("Archived: %s -> published/", src.name)


def run_uploads(
    platform: str | None = None,
    upload_ids: list[int] | None = None,
) -> dict:
    """Upload selected pending videos. Requires upload_ids to avoid mass-upload algorithm penalty.
    If upload_ids is empty/None, uploads nothing (safety: no automatic mass upload).
    Delay between uploads is configurable (upload.delay_minutes_between)."""
    if not upload_ids:
        logger.warning(
            "No upload IDs specified. Select videos in the Uploads page to upload. "
            "Mass upload is disabled to protect channel algorithm ranking."
        )
        return {
            "youtube_uploaded": 0,
            "youtube_total": 0,
            "tiktok_uploaded": 0,
            "tiktok_total": 0,
        }
    logger.info("Starting uploads (selected %d)%s...", len(upload_ids), f" ({platform})" if platform else "")
    conn = get_connection()

    yt_uploads = (
        get_pending_uploads(conn, platform="youtube", upload_ids=upload_ids)
        if platform in (None, "youtube")
        else []
    )
    tt_uploads = (
        get_pending_uploads(conn, platform="tiktok", upload_ids=upload_ids)
        if platform in (None, "tiktok")
        else []
    )
    conn.close()

    delay_min = max(0, int(cfg("upload.delay_minutes_between") or 15))
    delay_sec = delay_min * 60

    yt_success = 0
    tt_success = 0
    total_uploaded = 0

    for upload in yt_uploads:
        if upload_to_youtube(upload):
            yt_success += 1
            total_uploaded += 1
            if total_uploaded < len(yt_uploads) + len(tt_uploads) and delay_sec > 0:
                logger.info("Waiting %d min before next upload (algorithm-safe spacing)...", delay_min)
                time.sleep(delay_sec)

    for upload in tt_uploads:
        if upload_to_tiktok(upload):
            tt_success += 1
            total_uploaded += 1
            if total_uploaded < len(yt_uploads) + len(tt_uploads) and delay_sec > 0:
                logger.info("Waiting %d min before next upload (algorithm-safe spacing)...", delay_min)
                time.sleep(delay_sec)

    # Mark fully uploaded videos and archive
    conn = get_connection()
    approved_videos = conn.execute(
        "SELECT DISTINCT v.id, v.file_path FROM videos v "
        "JOIN uploads u ON u.video_id = v.id "
        "WHERE v.status = 'approved'"
    ).fetchall()

    for video in approved_videos:
        all_uploads = conn.execute(
            "SELECT upload_status FROM uploads WHERE video_id = ?", (video["id"],)
        ).fetchall()
        if all(u["upload_status"] == "success" for u in all_uploads):
            update_video_status(conn, video["id"], "uploaded")
            _archive_video(video["file_path"])

    conn.close()

    summary = {
        "youtube_uploaded": yt_success,
        "youtube_total": len(yt_uploads),
        "tiktok_uploaded": tt_success,
        "tiktok_total": len(tt_uploads),
    }
    logger.info("Upload complete: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(PROJECT_ROOT / "logs" / "uploader.log"),
        ],
    )
    from models.database import init_db
    init_db()
    result = run_uploads()
    print(json.dumps(result, indent=2))

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

    client_key = cfg("tiktok.client_key")
    client_secret = cfg("tiktok.client_secret")
    if not client_key or client_key.startswith("YOUR_"):
        logger.warning("TikTok credentials not configured")
        conn = get_connection()
        update_upload(conn, upload_record["id"], upload_status="failed", error_message="TikTok not configured")
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

    try:
        # Step 1: Init upload
        init_url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
        headers = {
            "Authorization": f"Bearer {client_key}",
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

        resp = requests.post(init_url, json=init_body, headers=headers, timeout=30)
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


def run_uploads(platform: str | None = None) -> dict:
    """Upload all pending approved videos. If platform is 'youtube' or 'tiktok', upload only to that platform."""
    logger.info("Starting uploads%s...", f" ({platform})" if platform else "")
    conn = get_connection()

    yt_uploads = get_pending_uploads(conn, platform="youtube") if platform in (None, "youtube") else []
    tt_uploads = get_pending_uploads(conn, platform="tiktok") if platform in (None, "tiktok") else []
    conn.close()

    yt_success = 0
    tt_success = 0

    for upload in yt_uploads:
        if upload_to_youtube(upload):
            yt_success += 1
            time.sleep(2)

    for upload in tt_uploads:
        if upload_to_tiktok(upload):
            tt_success += 1
            time.sleep(2)

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

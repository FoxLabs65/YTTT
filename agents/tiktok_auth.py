"""
TikTok OAuth token management for Content Posting API.

The Content Posting API requires a user OAuth access token (not client_key).
This module handles token storage, refresh, and OAuth flow.
"""

from __future__ import annotations

import json
import logging
import secrets
import socket
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Timeout when user closes browser without completing OAuth (no callback received)
OAUTH_WAIT_TIMEOUT_SECONDS = 90
OAUTH_POLL_INTERVAL = 2

from models.config import get as cfg

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_TOKEN_PATH = PROJECT_ROOT / "config" / "tiktok_token.json"

# Buffer before expiry to refresh (5 minutes)
REFRESH_BUFFER_SECONDS = 300


def _token_path() -> Path:
    path = cfg("tiktok.token_path") or "config/tiktok_token.json"
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p


def _load_tokens() -> dict | None:
    path = _token_path()
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load TikTok token: %s", e)
        return None


def _save_tokens(data: dict) -> None:
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.info("TikTok tokens saved to %s", path)


def get_tiktok_access_token() -> str | None:
    """
    Return a valid access token for TikTok API calls.
    Refreshes the token if expired. Returns None if no token or refresh fails.
    """
    client_key = cfg("tiktok.client_key")
    client_secret = cfg("tiktok.client_secret")
    if not client_key or not client_secret or client_key.startswith("YOUR_"):
        return None

    data = _load_tokens()
    if not data or not data.get("access_token"):
        return None

    expires_at = data.get("expires_at")
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if (exp_dt - datetime.now(timezone.utc)).total_seconds() < REFRESH_BUFFER_SECONDS:
                refreshed = _refresh_access_token(data)
                if refreshed:
                    data = _load_tokens()
                else:
                    return None
        except (ValueError, TypeError):
            pass

    return data.get("access_token")


def _refresh_access_token(current: dict) -> bool:
    """Refresh the access token using refresh_token. Returns True on success."""
    import requests

    client_key = cfg("tiktok.client_key")
    client_secret = cfg("tiktok.client_secret")
    refresh_token = current.get("refresh_token")
    if not refresh_token:
        logger.warning("No refresh_token in TikTok token file")
        return False

    url = "https://open.tiktokapis.com/v2/oauth/token/"
    payload = {
        "client_key": client_key,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        body = resp.json()

        if "access_token" not in body:
            logger.error("TikTok refresh response missing access_token: %s", body)
            return False

        expires_in = body.get("expires_in", 86400)
        expires_at = datetime.now(timezone.utc)
        from datetime import timedelta

        expires_at = (expires_at + timedelta(seconds=expires_in)).isoformat()

        _save_tokens({
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token", refresh_token),
            "expires_at": expires_at,
            "open_id": body.get("open_id", current.get("open_id", "")),
        })
        logger.info("TikTok access token refreshed")
        return True
    except Exception as e:
        logger.error("TikTok token refresh failed: %s", e)
        return False


def run_oauth_flow() -> bool:
    """
    Run the TikTok OAuth flow: open browser, start local server, exchange code for tokens.
    Returns True if tokens were obtained and saved.
    """
    import requests

    client_key = cfg("tiktok.client_key")
    client_secret = cfg("tiktok.client_secret")
    if not client_key or not client_secret or client_key.startswith("YOUR_"):
        logger.error("TikTok client_key and client_secret must be set in settings.yaml")
        return False

    redirect_uri = cfg("tiktok.redirect_uri") or "http://localhost:8765/callback"
    scope = "user.info.basic,video.publish"
    state = secrets.token_urlsafe(16)

    # Parse port from redirect_uri (e.g. http://localhost:8765/callback -> 8765)
    try:
        parsed = urlparse(redirect_uri)
        port = parsed.port if parsed.port else 8765
    except Exception:
        port = 8765

    auth_url = (
        "https://www.tiktok.com/v2/auth/authorize/"
        f"?client_key={client_key}"
        f"&scope={scope}"
        "&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
    )

    code_received = []
    code_error = []

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path.endswith("/callback") or "/callback" in parsed.path:
                params = parse_qs(parsed.query)
                if "code" in params:
                    code_received.append(params["code"][0])
                if "error" in params:
                    code_error.append(params.get("error_description", params.get("error", ["Unknown"])[0]))
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                if code_received:
                    html = "<h2>Success!</h2><p>You can close this window and return to the app.</p>"
                else:
                    html = f"<h2>Error</h2><p>{code_error[0] if code_error else 'No code received'}</p>"
                self.wfile.write(html.encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    server.socket.settimeout(OAUTH_POLL_INTERVAL)

    webbrowser.open(auth_url)
    logger.info("Opened browser for TikTok authorization. Waiting for callback (timeout %ds)...", OAUTH_WAIT_TIMEOUT_SECONDS)

    deadline = time.time() + OAUTH_WAIT_TIMEOUT_SECONDS
    while time.time() < deadline and not code_received and not code_error:
        try:
            server.handle_request()
            break
        except socket.timeout:
            continue
        except OSError as e:
            logger.debug("OAuth server: %s", e)
            break

    server.server_close()

    if not code_received:
        err = code_error[0] if code_error else "No authorization code received (timed out or browser closed)"
        logger.error("TikTok OAuth failed: %s", err)
        return False

    code = code_received[0]

    url = "https://open.tiktokapis.com/v2/oauth/token/"
    payload = {
        "client_key": client_key,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        body = resp.json()

        if "access_token" not in body:
            logger.error("TikTok token response missing access_token: %s", body)
            return False

        expires_in = body.get("expires_in", 86400)
        from datetime import timedelta

        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

        _save_tokens({
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token", ""),
            "expires_at": expires_at,
            "open_id": body.get("open_id", ""),
        })
        logger.info("TikTok OAuth complete. Tokens saved.")
        return True
    except Exception as e:
        logger.error("TikTok token exchange failed: %s", e)
        return False


def has_tiktok_token() -> bool:
    """Return True if a valid token file exists."""
    data = _load_tokens()
    return bool(data and data.get("access_token"))


def refresh_and_get_token() -> str | None:
    """Force refresh the token and return new access_token, or None on failure."""
    data = _load_tokens()
    if not data or not data.get("refresh_token"):
        return None
    if _refresh_access_token(data):
        return get_tiktok_access_token()
    return None

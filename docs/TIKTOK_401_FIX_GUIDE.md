# TikTok 401 Unauthorized – Resolution Guide

## Problem

**Error:** `401 Client Error: Unauthorized for url: https://open.tiktokapis.com/v2/post/publish/video/init/`

## Root Cause

The app currently uses **`client_key`** (your app ID) as the Bearer token in the `Authorization` header. TikTok's Content Posting API requires a **user OAuth access token**, not the client key.

| What we use now | What TikTok expects |
|-----------------|---------------------|
| `client_key` (app identifier) | `access_token` (user authorization) |

The `client_key` and `client_secret` are only used to **obtain** the access token via OAuth. They must not be sent as the Bearer token for API calls.

---

## What Needs to Be Done

1. **Implement TikTok OAuth flow** – User logs in to TikTok and authorizes the app with `video.publish` scope.
2. **Store access and refresh tokens** – Persist them (e.g. `config/tiktok_token.json`).
3. **Use access token in API calls** – Send `Authorization: Bearer {access_token}` instead of `client_key`.
4. **Refresh token when expired** – Access tokens expire in 24 hours; use `refresh_token` to get a new one.

---

## Step-by-Step Resolution Guide

### Step 1: Verify TikTok Developer Portal Setup

1. Go to [TikTok for Developers](https://developers.tiktok.com/).
2. Open your app → **Manage apps** → select your app.
3. **Products** – Ensure **Content Posting API** is added and approved.
4. **Scopes** – Ensure `video.publish` is requested and approved.
5. **Redirect URI** – Add a redirect URI for OAuth (e.g. `http://localhost:8080/callback` for local testing).
   - TikTok requires HTTPS for production; for local dev you may need `http://localhost:...` if supported.
   - Check [TikTok redirect URI rules](https://developers.tiktok.com/doc/login-kit-web/).

### Step 2: Complete OAuth Flow (One-Time)

You must authorize your TikTok account with the app to get an access token:

1. Build the authorization URL:
   ```
   https://www.tiktok.com/v2/auth/authorize/
     ?client_key=YOUR_CLIENT_KEY
     &scope=user.info.basic,video.publish
     &response_type=code
     &redirect_uri=YOUR_REGISTERED_REDIRECT_URI
     &state=random_string
   ```

2. Open that URL in a browser and log in with the TikTok account that will receive uploads.

3. After authorizing, TikTok redirects to your `redirect_uri` with `?code=...&state=...`.

4. Exchange the `code` for tokens:
   ```
   POST https://open.tiktokapis.com/v2/oauth/token/
   Content-Type: application/x-www-form-urlencoded

   client_key=YOUR_CLIENT_KEY
   client_secret=YOUR_CLIENT_SECRET
   code=THE_CODE_FROM_REDIRECT
   grant_type=authorization_code
   redirect_uri=SAME_AS_ABOVE
   ```

5. Response contains:
   - `access_token` – use as `Bearer {access_token}` in API calls
   - `refresh_token` – use to get new access tokens (valid 365 days)
   - `expires_in` – access token lifetime (typically 86400 seconds = 24 hours)

### Step 3: Store Tokens Securely

Save the tokens to a file (e.g. `config/tiktok_token.json`) and add it to `.gitignore`:

```json
{
  "access_token": "act.xxx...",
  "refresh_token": "rft.xxx...",
  "expires_at": "2026-02-27T12:00:00Z",
  "open_id": "user-open-id"
}
```

### Step 4: Refresh Token Before Expiry

Before each upload (or on 401), check if the access token is expired. If so:

```
POST https://open.tiktokapis.com/v2/oauth/token/
Content-Type: application/x-www-form-urlencoded

client_key=YOUR_CLIENT_KEY
client_secret=YOUR_CLIENT_SECRET
grant_type=refresh_token
refresh_token=YOUR_REFRESH_TOKEN
```

Use the new `access_token` and `refresh_token` from the response for future calls.

### Step 5: Use Access Token in Upload Requests

Replace the current header:

```python
# WRONG (current)
"Authorization": f"Bearer {client_key}"

# CORRECT
"Authorization": f"Bearer {access_token}"
```

---

## Checklist for User

- [ ] Content Posting API is added and approved in TikTok Developer Portal
- [ ] `video.publish` scope is approved
- [ ] Redirect URI is registered and matches exactly (including trailing slash if used)
- [ ] OAuth flow completed – access_token and refresh_token obtained
- [ ] Tokens stored in `config/tiktok_token.json` (or equivalent)
- [ ] Code updated to use access_token and refresh logic

---

## Implementation in This Project

The app now includes:

1. **Setup > TikTok > Connect TikTok** – Runs the OAuth flow from the UI
2. **`python main.py --tiktok-oauth`** – Runs the OAuth flow from the command line
3. **Token storage** – `config/tiktok_token.json` (gitignored)
4. **Auto-refresh** – Access tokens are refreshed automatically when expired
5. **Retry on 401** – Upload retries once with a refreshed token if the first request returns 401

### Quick Start

1. Add `http://localhost:8765/callback` to your app's Redirect URIs in [TikTok Developer Portal](https://developers.tiktok.com/apps/) → your app → Login Kit → Redirect URI
2. Set `client_key` and `client_secret` in `config/settings.yaml` under `tiktok`
3. Go to **Setup** → **TikTok** → click **Connect TikTok**
4. Authorize in the browser, then return to the app
5. Enable TikTok uploads and save

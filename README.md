# Trending Content Shorts Engine

Automated pipeline that scrapes YouTube and TikTok for trending topics, generates original short-form content (motivational, funny, memes, news reactions) with free B-roll, voiceovers, and background music, then queues for human review before uploading to YouTube Shorts and TikTok.

---

## Table of Contents

1. [How It Works](#how-it-works)
2. [Pre-Requisites](#pre-requisites)
3. [Account Setup (One-Time)](#account-setup-one-time)
4. [Installation (One-Time)](#installation-one-time)
5. [Configuration](#configuration)
6. [Running the Pipeline](#running-the-pipeline)
7. [Reviewing and Approving Videos](#reviewing-and-approving-videos)
8. [Uploading to YouTube and TikTok](#uploading-to-youtube-and-tiktok)
9. [Automated Scheduling](#automated-scheduling)
10. [Running Individual Agents](#running-individual-agents)
11. [Music Library](#music-library)
12. [Project Structure](#project-structure)
13. [Troubleshooting](#troubleshooting)

---

## How It Works

The engine runs five agents in sequence:

```
1. DISCOVERY   - Scrapes YouTube/TikTok for trending Shorts, extracts tags and scores
2. IDEATION    - Sends trends to Claude Pro, generates original scripts with clickworthy titles
3. SOURCING    - Downloads B-roll video from Pexels/Pixabay, background music via yt-dlp,
                 and generates voiceover with edge-tts
4. COMPOSER    - Assembles everything into a vertical 1080x1920 MP4 with text overlays
5. UPLOADER    - Pushes approved videos to YouTube Shorts and TikTok
```

Between steps 4 and 5 there is a **human review step** -- you approve or reject each video via a local web dashboard before anything is published.

---

## Pre-Requisites

You need the following installed on your machine **before** starting:

| Requirement | Version | How to check | How to install |
|-------------|---------|-------------|----------------|
| **Windows 10/11** | Any recent | -- | Already running |
| **Python** | 3.11 or newer | `python --version` | https://python.org/downloads/ (tick "Add to PATH" during install) |
| **pip** | Any | `pip --version` | Included with Python |
| **Git** | Any (optional) | `git --version` | https://git-scm.com/downloads |

> **Note:** FFmpeg is bundled automatically by the `moviepy` package (via `imageio-ffmpeg`), so you do **not** need to install it separately. yt-dlp is installed as a Python package in the next step.

---

## Account Setup (One-Time)

You need API keys from several free services. Create each account below, then keep the keys/files ready for the Configuration step.

### 1. Pexels API Key (free B-roll video footage)

1. Open https://www.pexels.com/api/
2. Click **Get Started** and register a free account (email + password)
3. On the dashboard, describe your project (e.g. "short-form video creation tool")
4. Copy the **API Key** shown on the dashboard
5. Keep it ready -- you will paste it into the config file later

### 2. Pixabay API Key (backup footage and images)

1. Open https://pixabay.com/accounts/register/ and create a free account
2. Once logged in, go to https://pixabay.com/api/docs/
3. Your **API Key** is displayed at the top of the page
4. Copy it and keep it ready

### 3. Anthropic API Key (Claude Pro for script generation)

1. Open https://console.anthropic.com/
2. Log in with your Claude Pro account (or create one)
3. Go to **API Keys** in the left sidebar, then click **Create Key**
4. Copy the key (it starts with `sk-ant-...`)
5. Keep it ready

> Claude Pro includes $5/month in API credits. Script generation uses very few tokens -- this is more than enough for daily use.

### 4. YouTube Channel + Google Cloud API Credentials

**Create the channel:**

1. Go to https://www.youtube.com/
2. Click your profile icon (top right) > **Create a channel**
3. Set a channel name and complete setup

**Create API credentials:**

1. Go to https://console.cloud.google.com/
2. Click **Select a project** (top bar) > **New Project** > name it (e.g. "TrendingShorts") > **Create**
3. Make sure the new project is selected in the top bar
4. Go to **APIs & Services** > **Library**
5. Search for **YouTube Data API v3** > click it > click **Enable**
6. Go to **APIs & Services** > **Credentials**
7. Click **+ Create Credentials** > **OAuth client ID**
8. If prompted, configure the OAuth consent screen first:
   - Choose **External** > **Create**
   - Fill in app name, user support email, developer email > **Save and Continue** through all steps
9. Back in Credentials > **+ Create Credentials** > **OAuth client ID**
10. Application type: **Desktop app** > name it anything > **Create**
11. Click **Download JSON** on the popup
12. Rename the downloaded file to `client_secret.json`
13. Keep the file ready -- you will place it in the `config/` folder later

### 5. TikTok Developer Account (optional -- can be set up later)

1. Go to https://developers.tiktok.com/
2. Sign up with your TikTok account
3. Click **Manage apps** > **Create app**
4. Fill in app details and request access to the **Content Posting API**
5. Once approved (1-5 business days), note the **Client Key** and **Client Secret**

> The pipeline works in YouTube-only mode until TikTok is approved. Videos will still be saved locally for manual TikTok upload.

---

## Installation (One-Time)

Open **PowerShell** (or Terminal) and run each step below.

### Step 1: Navigate to the project folder

```
cd D:\Data\Claude\Proj\yttt
```

### Step 2: Install Python dependencies

```
pip install -r requirements.txt
```

This installs: yt-dlp, moviepy, edge-tts, Pillow, google-api-python-client, anthropic, streamlit, apscheduler, and all sub-dependencies. Expect it to take 1-2 minutes.

### Step 3: Verify the install

```
python -c "import moviepy, anthropic, edge_tts, streamlit, yt_dlp; print('All imports OK')"
```

You should see `All imports OK`. If any import fails, re-run `pip install -r requirements.txt`.

### Step 4: Add yt-dlp to your PATH (if needed)

If `yt-dlp --version` gives a "not recognized" error, Python's Scripts folder is not on your PATH. Run:

```
$env:PATH += ";$env:APPDATA\Python\Python314\Scripts"
```

To make this permanent, add that Scripts folder to your system PATH via **Settings > System > Environment Variables**.

> Replace `Python314` with your actual Python version folder (e.g. `Python311`, `Python312`).

---

## Configuration

### Step 1: Create your settings file

```
copy config\settings.example.yaml config\settings.yaml
```

### Step 2: Edit `config\settings.yaml`

Open `config\settings.yaml` in any text editor and replace the placeholder values with your actual keys:

```yaml
# Paste your real keys here (keep the quotes):
pexels_api_key: "abc123..."
pixabay_api_key: "xyz789..."
anthropic_api_key: "sk-ant-..."
```

### Step 3: Place your YouTube OAuth file

Copy the `client_secret.json` file you downloaded from Google Cloud Console into:

```
config\client_secret.json
```

### Step 4: Configure TikTok (if approved)

If you have TikTok developer access, update these fields in `settings.yaml`:

```yaml
tiktok:
  client_key: "your_actual_key"
  client_secret: "your_actual_secret"
  enabled: true
```

Otherwise leave `enabled: false` -- the pipeline will skip TikTok uploads.

### Step 5: (Optional) Set your channel watermark

In `settings.yaml`, set your channel name to show a small watermark on videos:

```yaml
composer:
  watermark_text: "YourChannelName"
```

Leave it empty (`""`) for no watermark.

### Step 6: Run setup to verify everything

```
python main.py --setup
```

Expected output:

```
Initializing database...
Database initialized at data/yttt.db
Checking configuration...
  Pexels API: OK
  Pixabay API: OK
  Anthropic API: OK
  YouTube OAuth: OK
  TikTok: NOT CONFIGURED    <-- fine if not set up yet
  FFmpeg: OK
  yt-dlp: OK (v2026.x.x)
Setup complete.
```

If any service shows `NOT CONFIGURED`, go back and check that key in `settings.yaml`.

---

## Running the Pipeline

### Full pipeline (recommended for daily use)

This runs all four agents in sequence: discovery, ideation, sourcing, composing.

```
python main.py --run
```

**What happens:**
1. Scrapes YouTube/TikTok for current trending topics (~10 seconds)
2. Sends trends to Claude, generates scripts (~10 seconds)
3. Downloads B-roll footage, images, background music, generates voiceover (~30 seconds)
4. Renders the videos as 1080x1920 MP4 files (~5-7 minutes per video)

**Output:** Videos appear in `output\pending\` ready for review.

### Check current status at any time

```
python main.py --status
```

Shows counts of trends, scripts, videos pending review, approved, uploaded, and music library size.

### Generate for a specific category

```
python main.py --run --category motivational --count 3
```

Valid categories: `motivational`, `funny`, `meme`, `news`, `storytime`.

---

## Reviewing and Approving Videos

Before any video is uploaded, you review it in a local web dashboard.

### Step 1: Start the dashboard

```
streamlit run review/app.py
```

### Step 2: Open in your browser

Go to http://localhost:8501

### Step 3: Review each video

For each pending video you will see:
- **Video player** -- watch the full Short
- **Title, description, tags** -- all editable before approval
- **TikTok caption** -- separate editable field optimized for TikTok
- **Approve** button -- moves the video to the upload queue
- **Reject** button -- archives the video with an optional reason

### Step 4: Stop the dashboard

Press `Ctrl+C` in the terminal when done reviewing.

---

## Uploading to YouTube and TikTok

After approving videos in the review dashboard:

```
python main.py --upload
```

**What happens:**
- Uploads each approved video to YouTube Shorts (title, description, tags, thumbnail)
- Uploads to TikTok if enabled (caption, hashtags)
- Moves uploaded videos from `output\pending\` to `output\published\`
- Records platform URLs in the database

**First YouTube upload:** The first time you upload, a browser window will open asking you to authorize the app with your Google account. Sign in with the account that owns the YouTube channel. This creates a `config\token.json` file that persists -- you will not need to authorize again.

---

## Automated Scheduling

Instead of running manually each day, you can start a background scheduler:

```
python main.py --schedule
```

**Default schedule** (configurable in `settings.yaml`):

| Time | Agent | What it does |
|------|-------|-------------|
| 06:00 | Discovery | Scrapes latest trending content |
| 06:30 | Ideation | Generates scripts from trends |
| 07:00 | Sourcing | Downloads footage, music, generates voiceover |
| 07:30 | Composer | Renders videos |
| 08:00 | Notification | Desktop popup: "Videos ready for review" |

The scheduler runs until you close the terminal or press `Ctrl+C`. Uploads are **not** automated -- you still review and approve manually, then run `python main.py --upload`.

To change the schedule, edit these values in `config\settings.yaml`:

```yaml
scheduler:
  discovery_time: "06:00"
  ideation_time: "06:30"
  sourcing_time: "07:00"
  composing_time: "07:30"
  notification_time: "08:00"
  timezone: "Europe/London"
```

---

## Running Individual Agents

Each agent can be run standalone for debugging or partial runs.

| Command | What it does |
|---------|-------------|
| `python -m agents.discovery` | Scrape YouTube/TikTok for trending content only |
| `python -m agents.ideation` | Generate scripts from existing trend data only |
| `python -m agents.sourcing` | Download B-roll, music, voiceover for pending scripts |
| `python -m agents.music_scraper` | Pre-populate the royalty-free music library |
| `python -m agents.composer` | Render videos from scripts that have all assets ready |
| `python -m agents.uploader` | Upload approved videos to YouTube/TikTok |
| `python main.py --music` | Download music for all content categories |

---

## Music Library

Background music is automatically sourced from two free services:

1. **YouTube no-copyright channels** (NoCopyrightSounds, Audio Library, etc.) via yt-dlp -- no account needed
2. **Freesound** (Creative Commons Zero licensed tracks) -- optional free API key for extra variety

Each mood has 8+ diverse search queries that are **randomized per run** so you get different tracks across videos, not the same background music every time.

### How it works

| Category | Mood folder | Example sources |
|----------|-------------|----------------|
| Motivational | `assets/music/uplifting/` | Inspirational piano, orchestral, acoustic guitar |
| Funny | `assets/music/funny/` | Ukulele, silly cartoon, upbeat jazz |
| Meme | `assets/music/quirky/` | Lo-fi beats, retro chiptune, trap |
| News | `assets/music/dramatic/` | Corporate news, documentary, technology |
| Storytime | `assets/music/chill/` | Ambient, gentle piano, indie folk |

Tracks are downloaded once and cached permanently. The sourcing agent randomly selects a mood-matched track when assembling each video.

### Pre-populate the library

To download music for all categories in advance (recommended before your first full run):

```
python main.py --music
```

### Add Freesound for extra variety (optional)

1. Go to https://freesound.org/apiv2/apply/ and request a free API key
2. Add it to `config\settings.yaml`:

```yaml
freesound_api_key: "your_freesound_key"
```

This gives the scraper access to thousands of additional CC0-licensed tracks. Without this key the scraper still works -- it just uses YouTube only.

### Add your own music manually

Place any `.mp3` files in `assets\music\` (root) or in a mood subfolder:
- `assets\music\uplifting\my_track.mp3`
- `assets\music\funny\comedy_theme.mp3`
- `assets\music\my_custom_track.mp3` (root = picked up as "general")

Manually placed files are discovered automatically and used alongside auto-downloaded tracks.

---

## Project Structure

```
yttt/
  main.py                           Entry point / orchestrator / scheduler
  requirements.txt                  Python dependencies
  README.md                         This file
  .gitignore                        Excludes secrets, assets, output, DB, logs
  config/
    settings.example.yaml           Config template (committed to repo)
    settings.yaml                   Your actual config with API keys (gitignored)
    client_secret.json              YouTube OAuth credentials (gitignored)
    token.json                      Auto-created after first YouTube auth (gitignored)
    templates/scripts/              Claude prompt templates per category
  agents/
    discovery.py                    Trend Discovery Agent (yt-dlp scraper)
    ideation.py                     Content Ideation Agent (Claude Pro)
    sourcing.py                     Asset Sourcing Agent (Pexels, Pixabay, edge-tts)
    music_scraper.py                Royalty-Free Music Scraper (yt-dlp)
    composer.py                     Video Composer Agent (MoviePy / FFmpeg)
    uploader.py                     Upload Agent (YouTube API, TikTok API)
  models/
    database.py                     SQLite schema and CRUD operations
    config.py                       YAML config loader
  review/
    app.py                          Streamlit review dashboard
  assets/                           (gitignored)
    stock_footage/                  Downloaded B-roll video clips
    images/                         Downloaded background images
    music/                          Royalty-free music (auto + manual)
      uplifting/                    Motivational tracks
      funny/                        Comedy tracks
      quirky/                       Meme tracks
      dramatic/                     News tracks
      chill/                        Storytime tracks
    voiceovers/                     Generated TTS audio files
  output/                           (gitignored)
    pending/                        Videos awaiting human review
    approved/                       Videos approved, ready for upload
    published/                      Uploaded and archived
    rejected/                       Rejected with reason
  data/                             (gitignored)
    yttt.db                         SQLite database
  logs/                             (gitignored)
    pipeline_YYYYMMDD.log           Daily pipeline logs
    discovery.log                   Discovery agent log
    ideation.log                    Ideation agent log
    sourcing.log                    Sourcing agent log
    composer.log                    Composer agent log
    uploader.log                    Uploader agent log
  scripts/
    oracle_setup.sh                 Oracle Cloud free-tier setup script
```

---

## Troubleshooting

### "yt-dlp is not recognized"

Python's Scripts directory is not on your system PATH. Run this in the same terminal session:

```
$env:PATH += ";$env:APPDATA\Python\Python314\Scripts"
```

Or add it permanently via Windows Settings > System > Environment Variables. Replace `Python314` with your version.

### "YouTube upload quota exceeded"

The YouTube Data API allows ~5-6 uploads per day (10,000 quota units). The quota resets at **midnight Pacific Time**. Wait and try again, or reduce `videos_per_run` in settings.

### "App has not completed the Google verification process" / "Only developer-approved testers"

Your OAuth app is in **Testing** mode. Add your Google account as a test user:

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → your project
2. **APIs & Services** → **OAuth consent screen**
3. Under **Test users**, click **+ ADD USERS**
4. Add the Gmail address of the YouTube channel you want to upload to
5. Save and try the upload again

Until your app is verified by Google, only listed test users can sign in. For personal use, adding yourself as a test user is sufficient.

### "Doesn't have permissions to upload and set custom video thumbnails" (403)

The video uploads successfully, but the custom thumbnail fails. YouTube requires **channel verification** before custom thumbnails can be set via the API.

1. Go to [YouTube Studio](https://studio.youtube.com) → **Settings** → **Channel** → **Feature eligibility**
2. Complete **phone verification** if prompted
3. Wait a few minutes and try uploading again

Until verified, videos will upload but YouTube will use an auto-generated thumbnail.

### "TikTok scraping returns empty" / "No working app info"

TikTok actively blocks scrapers. The discovery agent automatically falls back to searching YouTube for TikTok-originated trending content (e.g. "tiktok viral" queries) when direct TikTok scraping fails. This means trending topics from TikTok still get picked up through YouTube cross-posts. YouTube is the primary and most reliable trend source.

If you want to try direct TikTok scraping, make sure yt-dlp is up to date: `pip install --upgrade yt-dlp`

### Video render fails

Verify MoviePy's bundled FFmpeg is working:

```
python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
```

This should print a path to an FFmpeg executable. If it does not, reinstall moviepy:

```
pip install --force-reinstall moviepy imageio-ffmpeg
```

### Claude API returns an error

1. Check your key in `config\settings.yaml` is correct (starts with `sk-ant-`)
2. Verify you have credits at https://console.anthropic.com/
3. Check `logs/ideation.log` for the full error message

### "No trends found" / Discovery returns 0

This can happen if the search queries return only long-form videos (over 2 minutes). The agent filters to videos under 120 seconds. Try adding more specific search queries in `settings.yaml` under `discovery.youtube_queries`.

### Dashboard won't start

Make sure streamlit is installed and on your PATH:

```
pip install streamlit
streamlit run review/app.py
```

If port 8501 is busy, streamlit will try 8502, 8503, etc. Check the terminal output for the actual URL.

### Logs

All components use centralized logging for end-to-end traceability:

| Log file | Purpose |
|----------|---------|
| `logs/errors.log` | **Start here for troubleshooting** — all ERROR and CRITICAL messages |
| `logs/pipeline_YYYYMMDD.log` | Full pipeline and agent logs (INFO and above) |
| `logs/ui.log` | UI/frontend events (when dashboard is running) |
| `logs/ui_runs/*.log` | Per-task logs (discovery, upload, etc.) from the UI runner |

Agent-specific logs (when run standalone): `discovery.log`, `ideation.log`, `sourcing.log`, `composer.log`, `uploader.log`, `cleanup.log`, `music_scraper.log`

---

## Quick Reference Card

```
FIRST TIME SETUP:
  pip install -r requirements.txt
  copy config\settings.example.yaml config\settings.yaml
  (edit settings.yaml with your API keys)
  (place client_secret.json in config\)
  python main.py --setup

DAILY WORKFLOW:
  python main.py --run              Generate new videos
  streamlit run review/app.py       Review and approve
  python main.py --upload           Upload approved videos

STATUS / DIAGNOSTICS:
  python main.py --status           Show pipeline stats
  python main.py --music            Pre-download music library

AUTOMATED:
  python main.py --schedule         Start daily scheduler daemon

INDIVIDUAL AGENTS:
  python -m agents.discovery        Scrape trends only
  python -m agents.ideation         Generate scripts only
  python -m agents.sourcing         Download assets only
  python -m agents.music_scraper    Download music only
  python -m agents.composer         Render videos only
  python -m agents.uploader         Upload only
```

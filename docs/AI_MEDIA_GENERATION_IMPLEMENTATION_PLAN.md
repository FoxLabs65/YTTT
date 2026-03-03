# AI Media Generation Upgrade — Implementation Plan (Phases 2–5)

This document provides the implementation plan for Phases 2–5 of the AI Media Generation Upgrade Plan, including metadata for reuse/scoring and user configuration steps.

**Phase 1 (Suno)** is already implemented with metadata support via `_write_suno_meta()` and `.meta.json`.

---

## Metadata Strategy for All AI-Generated Assets

All AI-generated files (video, image, music) must store metadata for **reuse** and **scoring** in future pipeline runs.

### Metadata Format (`.meta.json` alongside each file)

| Field | Description | Used For |
|-------|-------------|----------|
| `keywords` | List of search terms (script keywords, visual cues) | Scoring match with new scripts |
| `category` | Script category (motivational, funny, etc.) | Category fallback matching |
| `prompt` | Generation prompt used | Reference; optional for scoring |
| `provider` | Source (flux, segmind, suno) | Attribution |
| `mood` | Optional (for images: aesthetic; for video: cinematic) | Mood-based fallback |
| `created_at` | ISO timestamp | Audit |

### Reuse Integration

| Asset Type | Storage Location | Scan Function | Candidate Pool |
|------------|------------------|---------------|-----------------|
| **Music (Suno)** | `assets/music/{mood}/suno_*.mp3` | `scan_local_library()` + `_local_path_to_candidate()` | `_collect_all_music_candidates()` |
| **Images (AI)** | `assets/images/ai/` | `_scan_ai_images()` (new) | `all_image_candidates` in sourcing |
| **Video (AI)** | `assets/stock_footage/ai/` | `_scan_ai_videos()` (new) | `all_video_candidates` in sourcing |

### Scoring Logic

- **User assets:** Already use `_load_meta_json()` for `keywords` and `mood` in `searchable_text`.
- **AI assets:** Same pattern — `_scan_ai_*()` loads `.meta.json`, builds `searchable_text`, adds to candidates.
- **Scoring:** `_score_visual_candidate()` matches `search_queries` against `searchable_text` — works for AI assets when keywords are in meta.

---

## Phase 2: AI Image Generation (Replicate Flux)

**Effort:** 1–2 days

### 2.1 Create `agents/ai_image_providers.py`

```python
# High-level API
def generate_ai_image(
    prompt: str,
    aspect_ratio: str = "9:16",
    dest_dir: Path | None = None,
) -> Path | None:
    """Try configured AI image providers in order. Returns local path or None."""
    providers = cfg("sourcing.ai_image_providers") or []
    for name in providers:
        fn = AI_IMAGE_PROVIDERS.get(name)
        if fn:
            path = fn(prompt, aspect_ratio, dest_dir)
            if path:
                return path
    return None

# Replicate Flux implementation
def _generate_flux(prompt: str, aspect_ratio: str, dest_dir: Path) -> Path | None:
    import replicate
    output = replicate.run("black-forest-labs/flux-schnell", input={
        "prompt": prompt,
        "aspect_ratio": aspect_ratio.replace(":", "x"),  # 9:16 -> 9x16 if needed
    })
    # Download first image URL to dest_dir
    ...
```

### 2.2 Config (config/settings.example.yaml)

```yaml
sourcing:
  ai_image_providers: []           # e.g. [flux] or [flux, dalle]
  ai_image_fallback_only: true    # Only use AI when stock fails
  ai_image_aspect_ratio: "9:16"    # Vertical for Shorts
```

### 2.3 Integration in `agents/sourcing.py`

**Location:** After collecting and scoring `all_image_candidates`, before the loop that picks images.

**Logic:**
1. If `ai_image_fallback_only` and no candidate with score above threshold (e.g. 0.5), OR `ai_image_first` (optional future flag):
2. Build prompt: `"Cinematic vertical image, {first 2-3 search_queries}, 9:16, high quality"`
3. Call `generate_ai_image(prompt, dest_dir=IMAGES_DIR / "ai")`
4. On success: write `_write_ai_image_meta(path, keywords=search_queries[:5], category=category, prompt=prompt, provider="flux")`; `insert_asset(source="flux", ...)`; increment `images_saved`

### 2.4 Add `_scan_ai_images()` and `_write_ai_image_meta()`

- `_scan_ai_images()`: Scan `assets/images/ai/`, load `.meta.json`, return candidates like `_scan_user_images()`.
- Merge `_scan_ai_images()` results into `all_image_candidates` before scoring.

### 2.5 Files to Create/Modify

| File | Action |
|------|--------|
| `agents/ai_image_providers.py` | Create — `generate_ai_image()`, Replicate Flux |
| `agents/sourcing.py` | Modify — AI image fallback, `_scan_ai_images`, `_write_ai_image_meta` |
| `config/settings.example.yaml` | Add `ai_image_providers`, `ai_image_fallback_only`, `ai_image_aspect_ratio` |
| `ui/pages/setup.py` | Add "AI Image" controls (Phase 5 consolidation) |
| `requirements.txt` | Add `replicate` |

---

## Phase 3: AI Video Generation (Segmind Veo3)

**Effort:** 2–3 days

### 3.1 Create `agents/ai_video_providers.py`

```python
def generate_ai_video(
    prompt: str,
    duration: int = 8,
    aspect_ratio: str = "9:16",
    dest_dir: Path | None = None,
) -> Path | None:
    """Try configured AI video providers in order. Returns local path or None."""
    providers = cfg("sourcing.ai_video_providers") or []
    for name in providers:
        fn = AI_VIDEO_PROVIDERS.get(name)
        if fn:
            path = fn(prompt, duration, aspect_ratio, dest_dir)
            if path:
                return path
    return None

# Segmind Veo3 implementation (REST API)
def _generate_segmind_video(prompt: str, duration: int, aspect_ratio: str, dest_dir: Path) -> Path | None:
    api_key = cfg("segmind_api_key")
    # POST to Segmind video endpoint, poll for completion, download MP4
    ...
```

### 3.2 Config

```yaml
sourcing:
  ai_video_providers: []           # e.g. [segmind]
  ai_video_fallback_only: true     # Only use AI when stock fails
  ai_video_max_duration: 8         # Seconds
```

### 3.3 Integration in `agents/sourcing.py`

**Location:** After scoring `all_video_candidates`, when `assets_saved < 3` (or threshold).

**Logic:**
1. If `ai_video_fallback_only` and (no candidates OR best score below threshold):
2. Build prompt from `search_queries[:3]` joined
3. Call `generate_ai_video(prompt, duration=min(8, estimated_duration), dest_dir=STOCK_DIR / "ai")`
4. On success: write `_write_ai_video_meta(...)`; `insert_asset(source="segmind", ...)`; increment `assets_saved`

### 3.4 Add `_scan_ai_videos()` and `_write_ai_video_meta()`

- `_scan_ai_videos()`: Scan `assets/stock_footage/ai/`, load `.meta.json`, return candidates like `_scan_user_videos()`.
- Merge into `all_video_candidates`.

### 3.5 Files to Create/Modify

| File | Action |
|------|--------|
| `agents/ai_video_providers.py` | Create — `generate_ai_video()`, Segmind Veo3 |
| `agents/sourcing.py` | Modify — AI video fallback, `_scan_ai_videos`, `_write_ai_video_meta` |
| `config/settings.example.yaml` | Add `ai_video_*`, `segmind_api_key` |
| `ui/pages/setup.py` | Add Segmind key, provider order (Phase 5) |
| `requirements.txt` | Optional: `segmindapi` or use `requests` |

---

## Phase 4: Additional AI Video Providers

**Effort:** 3–5 days (can be done incrementally)

| Provider | API | Key Config | Notes |
|----------|-----|------------|-------|
| **Sora (OpenAI)** | `client.videos.generate` | `openai_api_key` | Tier 4+; job poll |
| **Veo (Vertex AI)** | `predictLongRunning` | GCP project + auth | Most complex |
| **Runway** | RunwayML SDK | `runway_api_key` | Credit-based |
| **Nano Banana** | REST | `nanobanana_api_key` | Credit-based |

**Implementation:** Add provider functions to `ai_video_providers.py`; register in `AI_VIDEO_PROVIDERS` dict; add config keys and Setup UI fields.

**Recommendation:** Implement Segmind first (Phase 3), then Sora if user has OpenAI Tier 4+, then Veo for GCP users.

---

## Phase 5: Setup UI Consolidation

**Effort:** 1 day

### 5.1 New Expander: "AI Media Generation"

Consolidate Suno, AI Image, and AI Video into one section in `ui/pages/setup.py`:

**Layout:**
```
┌─ AI Media Generation ─────────────────────────────────┐
│  Suno AI Music        [existing controls]              │
│  AI Image (Flux)      [Replicate token, providers]     │
│  AI Video            [Segmind key, provider order]     │
│  [Test Suno] [Test Flux] [Test Segmind]                 │
└────────────────────────────────────────────────────────┘
```

### 5.2 Controls to Add

| Control | Config Key | Phase |
|---------|------------|-------|
| Replicate API Token | `replicate_api_token` | 2 |
| AI Image providers | `sourcing.ai_image_providers` | 2 |
| AI Image fallback only | `sourcing.ai_image_fallback_only` | 2 |
| Test Flux | — | 2 |
| Segmind API Key | `segmind_api_key` | 3 |
| AI Video providers | `sourcing.ai_video_providers` | 3 |
| AI Video fallback only | `sourcing.ai_video_fallback_only` | 3 |
| Test Segmind | — | 3 |

---

## Implementation Order

1. **Phase 2 (AI Image)** — Replicate Flux, metadata, scan, integration.
2. **Phase 3 (AI Video)** — Segmind, metadata, scan, integration.
3. **Phase 5 (UI)** — Consolidate Setup; add AI Image/Video controls.
4. **Phase 4 (Optional)** — Sora, Veo, etc. as needed.

---

## User Configuration Plan

### Prerequisites

- Python 3.10+
- Existing Shorts Engine setup (API keys for Pexels, Anthropic, etc.)

### Step 1: Install Dependencies

After implementation, run:

```bash
pip install replicate
# Optional for Segmind: pip install segmindapi  (or use requests)
```

### Step 2: Replicate (AI Image)

1. Sign up at [replicate.com](https://replicate.com).
2. Go to **Account** → **API tokens** → **Create token**.
3. Copy token.
4. In **Setup** → **AI Media Generation** → **AI Image**:
   - Paste token in "Replicate API Token".
   - Set "AI Image providers" to `flux` (or add via UI).
   - Enable "AI Image fallback only" (recommended).
5. Click **Test Flux** to verify.
6. Save configuration.

### Step 3: Segmind (AI Video)

1. Sign up at [cloud.segmind.com](https://cloud.segmind.com).
2. Go to **Keys** → Create API key.
3. In **Setup** → **AI Media Generation** → **AI Video**:
   - Paste key in "Segmind API Key".
   - Set "AI Video providers" to `segmind`.
   - Enable "AI Video fallback only" (recommended).
5. Click **Test Segmind** to verify.
6. Save configuration.

### Step 4: Run Setup Verification

```bash
python main.py --setup
```

Expected output includes:
- `Replicate: OK` (if token set)
- `Segmind: OK` (if key set)
- `Suno AI Music: OK` (if enabled)

### Step 5: Pipeline Run

Run the pipeline as usual. AI generation triggers only when:
- **AI Image:** Stock image search returns low scores or no results.
- **AI Video:** Stock video search returns low scores or fewer than 3 assets.
- **Suno:** Stock music score below threshold or forced.

### Optional: Cost and Rate Limits

| Provider | Est. Cost | Notes |
|----------|-----------|-------|
| Replicate Flux | ~$0.003/image | Pay per run |
| Segmind | Per-gen pricing | Check cloud.segmind.com |
| Suno | Per track | Check sunoapi.org |

Enable only the providers you need. Use `*_fallback_only: true` to minimise cost.

---

## Implementation Status (Completed)

- [x] `replicate` added to requirements.txt
- [x] `assets/images/ai/` and `assets/stock_footage/ai/` created on first use
- [x] Metadata helpers for AI and stock assets follow same schema pattern
- [x] `--setup` checks for Replicate/Segmind when providers configured
- [x] Stock video/image downloads now write `.meta.json` for reuse

---

## Questions for Clarification (If Needed)

1. **Segmind model:** Plan mentions "Segmind Veo3" — confirm endpoint (e.g. `/v1/veo-2` or `/v1/veo-3`) from Segmind docs.
2. **Stock metadata:** Should we also write `.meta.json` for Pexels/Pixabay downloads to enable future "local stock library" reuse? (Out of scope for this plan; can be a follow-up.)
3. **Aspect ratio:** Replicate Flux may use `"9:16"` or `"9x16"` — confirm from API docs before implementation.

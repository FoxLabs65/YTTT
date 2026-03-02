# Root Cause Analysis: "Fragment does not exist anymore" Errors

## Problem
Constant CLI messages appear:
```
The fragment with id XXXXX does not exist anymore - it might have been removed during a preceding full-app rerun.
```

## Root Cause

### 1. Pipeline Log (full_pipeline_*.log) – **Not related**
The pipeline log the user analyzed runs in a **subprocess** (`python main.py --run`). It records discovery, ideation, sourcing, composition, and cleanup. It does **not** contain the fragment error.

The fragment error is emitted by the **Streamlit server** (where `streamlit run ui/app.py` runs), not by the pipeline subprocess.

### 2. Actual Cause: Fragment + run_every + st.rerun() Race
The app uses `@st.fragment(run_every=5)` in several places. Some fragments call `st.rerun()` when conditions change. This causes a race:

1. Fragment A runs every 5 seconds (via `run_every=5`).
2. Fragment A detects “task finished” or “task running” and calls `st.rerun()`.
3. `st.rerun()` triggers a full app rebuild; the previous fragment’s DOM element is removed.
4. The **scheduler** still has a pending run for the old fragment.
5. When it fires, the old fragment’s DOM element no longer exists → **“fragment does not exist anymore”**.
6. The scheduler continues to tick, so the error repeats every 5 seconds.

### 3. Affected Code
| Location | Fragment behavior | Triggers error? |
|----------|-------------------|------------------|
| `app.py` (sidebar) | `_sidebar_status` runs every 5s, no `st.rerun()` | Possibly on navigation |
| `content.py` | `_live_log` runs every 5s; calls `st.rerun()` when task finishes | **Yes** |
| `dashboard.py` | `_dashboard_refresh` runs every 5s; calls `st.rerun()` when task running | **Yes** |
| `uploads.py` | `_upload_refresh` runs every 5s; calls `st.rerun()` when upload running | **Yes** |

### 4. Rejected Videos – **Not the cause**
The pipeline log shows `No rejected videos to clean up` and `rejected_cleaned: 0`. The fragment error is unrelated to rejected videos or asset cleanup. Cleanup logic does not trigger it.

### 5. Closure Mechanism
The user asked about a “closure mechanism” for when videos/assets are deleted. The fragment error comes from Streamlit’s `run_every` scheduler, not from lifecycle or deletion logic. No extra closure is needed for rejected videos or asset deletion.

## Solution
Remove `run_every` from fragments that call `st.rerun()`, and from the sidebar to avoid orphaned fragments on navigation. Replace auto-refresh with explicit **Refresh** buttons so users can update the page without causing the race.

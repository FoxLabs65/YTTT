"""
Background task executor for the Shorts Engine UI.

Runs pipeline phases as subprocesses so the Streamlit UI stays responsive.
Streams stdout/stderr to a log file that pages can poll via st.session_state.
"""

import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
logger = logging.getLogger("ui.runner")
LOG_DIR = PROJECT_ROOT / "logs" / "ui_runs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

COMMANDS = {
    "full_pipeline": ["python", "main.py", "--run"],
    "discovery": ["python", "main.py", "--discovery-only"],
    "ideation": ["python", "main.py", "--run"],
    "sourcing": ["python", "main.py", "--run"],
    "composing": ["python", "main.py", "--run"],
    "upload": ["python", "main.py", "--upload"],
    "cleanup": ["python", "main.py", "--cleanup"],
    "music": ["python", "main.py", "--music"],
    "setup": ["python", "main.py", "--setup"],
    "scheduler": ["python", "main.py", "--schedule"],
    "regenerate": ["python", "main.py", "--regenerate"],
}


class TaskRunner:
    """Manages a single background pipeline task at a time."""

    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.task_name: str = ""
        self.status: str = "idle"  # idle, running, completed, failed
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.log_path: Path | None = None
        self.exit_code: int | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    @property
    def elapsed(self) -> str:
        if not self.started_at:
            return ""
        end = self.finished_at or datetime.now()
        delta = end - self.started_at
        mins, secs = divmod(int(delta.total_seconds()), 60)
        return f"{mins}m {secs}s"

    def start(self, task_name: str, extra_args: list[str] | None = None) -> bool:
        """Start a background task. Returns False if one is already running."""
        if self.is_running:
            return False

        cmd = list(COMMANDS.get(task_name, ["python", "main.py", "--run"]))
        if extra_args:
            cmd.extend(extra_args)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = LOG_DIR / f"{task_name}_{stamp}.log"
        self.task_name = task_name
        self.status = "running"
        self.started_at = datetime.now()
        self.finished_at = None
        self.exit_code = None

        logger.info("Starting task: %s (log: %s)", task_name, self.log_path)
        self._thread = threading.Thread(target=self._run, args=(cmd,), daemon=True)
        self._thread.start()
        return True

    def _run(self, cmd: list[str]):
        try:
            with open(self.log_path, "w", encoding="utf-8") as log_f:
                log_f.write(f"=== {self.task_name} started at {self.started_at.isoformat()} ===\n")
                log_f.write(f"Command: {' '.join(cmd)}\n\n")
                log_f.flush()

                env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
                self.process = subprocess.Popen(
                    cmd,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    cwd=str(PROJECT_ROOT),
                    text=True,
                    bufsize=1,
                    env=env,
                )
                self.process.wait()
                self.exit_code = self.process.returncode

                log_f.write(f"\n=== Finished with exit code {self.exit_code} ===\n")

            self.status = "completed" if self.exit_code == 0 else "failed"
            if self.exit_code != 0:
                logger.error(
                    "Task %s failed with exit code %s (log: %s)",
                    self.task_name,
                    self.exit_code,
                    self.log_path,
                )
        except Exception as e:
            self.status = "failed"
            self.exit_code = -1
            logger.exception("Task %s raised exception: %s", self.task_name, e)
            if self.log_path and self.log_path.exists():
                with open(self.log_path, "a", encoding="utf-8") as lf:
                    lf.write(f"\n=== EXCEPTION: {e} ===\n")
        finally:
            self.finished_at = datetime.now()
            self.process = None
            logger.info(
                "Task %s finished: status=%s exit_code=%s elapsed=%s",
                self.task_name,
                self.status,
                self.exit_code,
                self.elapsed,
            )

    def stop(self):
        """Kill the running process."""
        if self.process:
            self.process.terminate()
            time.sleep(1)
            if self.process and self.process.poll() is None:
                self.process.kill()
            self.status = "failed"
            self.finished_at = datetime.now()

    def get_log_tail(self, lines: int = 50) -> str:
        """Read the last N lines of the current log."""
        if not self.log_path or not self.log_path.exists():
            return ""
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
            all_lines = text.splitlines()
            return "\n".join(all_lines[-lines:])
        except Exception:
            return ""

    def get_full_log(self) -> str:
        if not self.log_path or not self.log_path.exists():
            return ""
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    def get_recent_logs(self, limit: int = 10) -> list[dict]:
        """Return metadata for recent run logs."""
        logs = []
        for f in sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            first_line = ""
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    first_line = fh.readline().strip()
            except Exception:
                pass
            logs.append({
                "file": f.name,
                "path": str(f),
                "size_kb": f.stat().st_size / 1024,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "header": first_line,
            })
        return logs


def get_runner() -> TaskRunner:
    """Get or create the singleton TaskRunner stored in Streamlit session state."""
    import streamlit as st
    if "task_runner" not in st.session_state:
        st.session_state.task_runner = TaskRunner()
    return st.session_state.task_runner

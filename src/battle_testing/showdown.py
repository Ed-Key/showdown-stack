"""Showdown server management: install, start, stop, and health check."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from subprocess import PIPE

import httpx

SHOWDOWN_DIR = Path.home() / "Projects" / "showdown-servers" / "pokemon-showdown"
SHOWDOWN_PORT = 8000
SHOWDOWN_URL = f"http://localhost:{SHOWDOWN_PORT}"


def ensure_showdown_installed() -> Path:
    """Clone and install pokemon-showdown if not already present.

    Returns the path to the showdown directory.
    """
    if not SHOWDOWN_DIR.exists():
        print(f"[Showdown] Cloning pokemon-showdown to {SHOWDOWN_DIR}...")
        subprocess.run(
            [
                "git",
                "clone",
                "https://github.com/smogon/pokemon-showdown.git",
                str(SHOWDOWN_DIR),
            ],
            check=True,
        )
        print("[Showdown] Running npm install...")
        subprocess.run(["npm", "install"], cwd=SHOWDOWN_DIR, check=True)

    # Ensure config exists (copy from example if needed)
    config_path = SHOWDOWN_DIR / "config" / "config.js"
    config_example = SHOWDOWN_DIR / "config" / "config-example.js"
    if not config_path.exists() and config_example.exists():
        print("[Showdown] Copying config-example.js to config.js...")
        shutil.copy(config_example, config_path)

    return SHOWDOWN_DIR


def start_showdown(timeout: float = 30.0) -> subprocess.Popen:
    """Start a local Showdown server and wait for it to be ready.

    Args:
        timeout: Maximum seconds to wait for the server to become ready.

    Returns:
        The Popen process handle for the running server.

    Raises:
        TimeoutError: If the server doesn't respond within the timeout.
    """
    showdown_dir = ensure_showdown_installed()

    print(f"[Showdown] Starting server on port {SHOWDOWN_PORT}...")
    showdown_log = open("/tmp/showdown.log", "w")
    print(f"[Showdown] Logging stdout/stderr to /tmp/showdown.log")
    proc = subprocess.Popen(
        ["node", "pokemon-showdown", "start", "--no-security"],
        cwd=showdown_dir,
        stdout=showdown_log,
        stderr=subprocess.STDOUT,
    )

    # Poll until server is ready
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout:
        if proc.poll() is not None:
            # Process exited unexpectedly
            try:
                with open("/tmp/showdown.log") as f:
                    log = f.read()
            except OSError:
                log = "(could not read /tmp/showdown.log)"
            raise RuntimeError(
                f"Showdown process exited with code {proc.returncode}.\n"
                f"--- /tmp/showdown.log ---\n{log}"
            )
        if is_showdown_running():
            print("[Showdown] Server is ready.")
            return proc
        time.sleep(0.5)

    # Timed out — kill the process and raise
    proc.terminate()
    raise TimeoutError(
        f"Showdown server did not become ready within {timeout}s"
    )


def stop_showdown(proc: subprocess.Popen) -> None:
    """Terminate the Showdown server process."""
    print("[Showdown] Stopping server...")
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    print("[Showdown] Server stopped.")


def is_showdown_running() -> bool:
    """Check if the local Showdown server is responding on the expected port."""
    try:
        resp = httpx.get(SHOWDOWN_URL, timeout=2.0)
        return resp.status_code < 500
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        return False

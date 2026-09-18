"""
Application Updater Mechanism for CAN & CANopen Studio.
Supports checking GitHub Releases, downloading installers, and running git pull updates.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
"""

import os
import re
import sys
import json
import subprocess
import urllib.request
from typing import Tuple, Optional, Dict, Any, Callable

CURRENT_VERSION = "0.2.0"
GITHUB_REPO = "s-celles/canopen-studio"


def parse_version_tuple(version_str: str) -> Tuple[int, ...]:
    """
    Parse a version string (e.g. '0.2.0' or 'v1.4.12') into a tuple of integers.
    """
    cleaned = version_str.strip().lstrip("vV")
    parts = re.findall(r"\d+", cleaned)
    if not parts:
        return (0, 0, 0)
    return tuple(int(p) for p in parts)


def compare_versions(current: str, candidate: str) -> bool:
    """
    Returns True if candidate is strictly newer than current.
    """
    cur_tuple = parse_version_tuple(current)
    cand_tuple = parse_version_tuple(candidate)

    # Pad with zeros if different lengths
    max_len = max(len(cur_tuple), len(cand_tuple))
    cur_padded = cur_tuple + (0,) * (max_len - len(cur_tuple))
    cand_padded = cand_tuple + (0,) * (max_len - len(cand_tuple))

    return cand_padded > cur_padded


def check_for_updates(
    current_version: str = CURRENT_VERSION,
    repo: str = GITHUB_REPO,
    timeout: float = 6.0,
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Queries GitHub Releases API to check if a newer version is available.

    :param current_version: Current running version string.
    :param repo: GitHub repository in 'owner/repo' format.
    :param timeout: Network request timeout in seconds.
    :return: (is_update_available, release_info_dict)
    """
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"CANopen-Studio-Updater/{current_version}",
            "Accept": "application/vnd.github.v3+json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))

        tag = payload.get("tag_name", "")
        has_update = compare_versions(current_version, tag)

        # Locate Windows installer setup asset if present
        setup_asset = None
        portable_asset = None
        for asset in payload.get("assets", []):
            name = asset.get("name", "")
            if name.endswith("-Setup.exe") or name.endswith("Setup.exe"):
                setup_asset = asset
            elif name.endswith("-Portable.zip") or name.endswith(".zip"):
                portable_asset = asset

        info = {
            "tag_name": tag,
            "name": payload.get("name", tag),
            "html_url": payload.get("html_url", f"https://github.com/{repo}/releases"),
            "body": payload.get("body", ""),
            "published_at": payload.get("published_at", ""),
            "setup_asset": setup_asset,
            "portable_asset": portable_asset,
        }

        return has_update, info

    except Exception:
        return False, None


def is_git_repo(path: Optional[str] = None) -> bool:
    """Check if the given directory (or project root) is a git repository."""
    if path is None:
        path = os.path.dirname(os.path.abspath(__file__))
    git_dir = os.path.join(path, ".git")
    return os.path.exists(git_dir) and os.path.isdir(git_dir)


def perform_git_update(repo_dir: Optional[str] = None) -> Tuple[bool, str]:
    """
    Runs git pull and uv sync to update local source installation.
    """
    if repo_dir is None:
        repo_dir = os.path.dirname(os.path.abspath(__file__))

    try:
        # 1. git pull
        pull_res = subprocess.run(
            ["git", "pull"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if pull_res.returncode != 0:
            return False, f"git pull failed:\n{pull_res.stderr or pull_res.stdout}"

        # 2. uv sync
        sync_res = subprocess.run(
            ["uv", "sync"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if sync_res.returncode != 0:
            return True, f"Code updated, but uv sync reported warnings:\n{sync_res.stderr or sync_res.stdout}"

        return True, "Update completed successfully via git and uv!"

    except Exception as e:
        return False, f"Update error: {str(e)}"


def download_file(
    url: str,
    dest_path: str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> bool:
    """
    Downloads a remote file with progress reporting.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "CANopen-Studio-Downloader"})
    try:
        with urllib.request.urlopen(req) as resp:
            total_size = int(resp.headers.get("content-length", 0))
            downloaded = 0
            block_size = 65536

            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(block_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size > 0:
                        progress_callback(downloaded, total_size)
        return True
    except Exception:
        return False


def launch_installer_and_exit(installer_path: str) -> None:
    """
    Launches the downloaded Windows installer and terminates the current Python process.
    """
    if sys.platform == "win32":
        os.startfile(installer_path)
    else:
        subprocess.Popen([installer_path])
    sys.exit(0)

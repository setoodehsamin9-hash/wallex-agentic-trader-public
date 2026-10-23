"""Code/dependency provenance for the RunManifest (git commit + dirty flag, uv.lock hash)."""

import hashlib
import subprocess
from pathlib import Path

from wallex_agent.contracts import CodeVersion

REPO = Path(__file__).resolve().parents[3]


def current_code_version(repo: Path = REPO) -> CodeVersion:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo, capture_output=True,
                                    text=True, check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = "0" * 40, True
    return CodeVersion(git_commit=commit, dirty=dirty)


def live_release_problems(repo: Path = REPO) -> list[str]:
    """LIVE trades only committed, tagged code: tracked changes, untracked source/config files or no `live-*` tag on HEAD
    are problems. Any git failure is a problem (fail closed)."""
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

    try:
        problems = ["uncommitted tracked changes"] if git("status", "--porcelain", "--untracked-files=no") else []
        untracked = git("ls-files", "--others", "--exclude-standard", "--", "src", "config")
        if untracked:
            problems.append("untracked source/config files: " + ", ".join(untracked.splitlines()[:5]))
        if not any(t.startswith("live-") for t in git("tag", "--points-at", "HEAD").splitlines()):
            problems.append("HEAD has no live-YYYYMMDD-N tag")
    except (OSError, subprocess.CalledProcessError):
        problems = ["git state unreadable"]
    return problems


def lockfile_sha256(repo: Path = REPO) -> str:
    lock = repo / "uv.lock"
    return hashlib.sha256(lock.read_bytes()).hexdigest() if lock.exists() else "0" * 64

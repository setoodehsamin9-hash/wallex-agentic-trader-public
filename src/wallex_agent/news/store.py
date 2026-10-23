"""Immutable content-addressed local captures. Replay reads never fetch missing evidence."""

import re
from itertools import islice
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol

from wallex_agent.contracts.hashing import canonical_json, sha256_hex

from .contracts import NewsCapture


class NewsIntegrityError(ValueError):
    pass


class NewsReader(Protocol):
    def captures(self) -> tuple[NewsCapture, ...]: ...


class NewsArchive:
    def __init__(self, directory: Path, *, max_snapshots: int = 256) -> None:
        if max_snapshots <= 0:
            raise ValueError("positive archive bound required")
        self.directory = directory.absolute()
        self.max_snapshots = max_snapshots

    @staticmethod
    def _deny_symlinks(path: Path) -> None:
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise NewsIntegrityError("archive symlinks denied, including ancestor directories")

    @classmethod
    def _write_once(cls, path: Path, data: bytes) -> None:
        cls._deny_symlinks(path)
        if path.exists():
            if path.read_bytes() != data:
                raise NewsIntegrityError("immutable archive content conflict")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        cls._deny_symlinks(path)
        with NamedTemporaryFile(dir=path.parent, prefix=".news-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(data)
                stream.flush()
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            cls._deny_symlinks(path)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def record(self, capture: NewsCapture, raw: bytes | None) -> str:
        if capture.payload_sha256 != (sha256_hex(raw) if raw is not None else None):
            raise NewsIntegrityError("capture raw hash mismatch")
        for name in ("raw", "snapshots"):
            self._deny_symlinks(self.directory / name)
        if raw is not None:
            self._write_once(self.directory / "raw" / (sha256_hex(raw) + ".bin"), raw)
        identity = capture.snapshot_id
        self._write_once(self.directory / "snapshots" / (identity + ".json"), canonical_json(capture))
        return identity

    def captures(self) -> tuple[NewsCapture, ...]:
        snapshots = self.directory / "snapshots"
        self._deny_symlinks(snapshots)
        self._deny_symlinks(self.directory / "raw")
        if not snapshots.is_dir():
            raise NewsIntegrityError("missing or uninitialized news archive")
        paths = sorted(islice(snapshots.glob("*.json"), self.max_snapshots + 1))
        if len(paths) > self.max_snapshots:
            raise NewsIntegrityError("archive bound exceeded; use a bounded snapshot export")
        captures: list[NewsCapture] = []
        for path in paths:
            self._deny_symlinks(path)
            if not re.fullmatch(r"[a-f0-9]{64}", path.stem):
                raise NewsIntegrityError("invalid archive snapshot path")
            if path.stat().st_size > 2097152:
                raise NewsIntegrityError("oversized news snapshot")
            raw = path.read_bytes()
            if sha256_hex(raw) != path.stem:
                raise NewsIntegrityError("news snapshot hash mismatch")
            try:
                capture = NewsCapture.model_validate_json(raw)
            except ValueError as exc:
                raise NewsIntegrityError("invalid news snapshot contract") from exc
            if capture.payload_sha256 is not None:
                payload = self.directory / "raw" / (capture.payload_sha256 + ".bin")
                self._deny_symlinks(payload)
                if (not payload.exists() or payload.stat().st_size > 1048576
                        or sha256_hex(payload.read_bytes()) != capture.payload_sha256):
                    raise NewsIntegrityError("missing or changed raw news evidence")
            captures.append(capture)
        return tuple(captures)

    def export(self, destination: Path) -> None:
        captures = self.captures()
        target = NewsArchive(destination, max_snapshots=self.max_snapshots)
        self._deny_symlinks(target.directory)
        target.directory.mkdir(parents=True, exist_ok=False)
        (target.directory / "snapshots").mkdir()
        for capture in captures:
            raw = ((self.directory / "raw" / (capture.payload_sha256 + ".bin")).read_bytes()
                   if capture.payload_sha256 is not None else None)
            target.record(capture, raw)

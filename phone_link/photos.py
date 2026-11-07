"""Import camera photos from the phone over ADB.

The phone's MediaStore is asked for rows under ``DCIM/Camera``; each photo that
has not been seen before is read with ``adb exec-out cat`` and written to a
local folder.  Automatic import is opt-in, works only while ADB can see the
phone, and copies the original files unchanged.  Nothing is uploaded anywhere.

State lives in ``$XDG_DATA_HOME/phone-link/photos.json`` (``~/.local/share``
by default) and records the newest imported date plus the MediaStore rows
already handled, so a restart does not copy the same photo twice.  Existing
files with the same name and size are also treated as already imported.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

CAMERA_PATH = "DCIM/Camera/"
MEDIA_URI = "content://media/external/images/media"
STATE_FILE = "photos.json"
MAX_SEEN = 2_000
MAX_PHOTO_BYTES = 100 * 1024 * 1024
QUERY_TIMEOUT = 30.0
PULL_TIMEOUT = 120.0

Runner = Callable[..., subprocess.CompletedProcess]

_ROW_RE = re.compile(
    r"^Row:\s*\d+\s+_id=(?P<id>\d+),\s*_display_name=(?P<name>.*?),"
    r"\s*relative_path=(?P<path>.*?),\s*date_added=(?P<date>-?\d+),"
    r"\s*_size=(?P<size>\d+)\s*$"
)
_QUERY_ERROR_MARKERS = (
    "Error while accessing provider",
    "java.lang.",
    "java.lang.IllegalArgumentException",
    "Invalid column",
    "SecurityException",
    "Exception",
)


class PhotoImportError(RuntimeError):
    """The phone could not be asked for its photos."""


@dataclass(frozen=True)
class PhonePhoto:
    remote_id: str
    name: str
    relative_path: str
    date_added: int
    size: int

    @property
    def key(self) -> str:
        return f"{self.remote_id}:{self.date_added}:{self.size}"

    @property
    def remote_path(self) -> str:
        folder = self.relative_path.strip("/")
        if folder:
            return f"/sdcard/{folder}/{self.name}"
        return f"/sdcard/{self.name}"


@dataclass
class PhotoImportState:
    since: int = 0
    seen: list[str] = field(default_factory=list)

    def remember(self, photo: PhonePhoto) -> None:
        if photo.key in self.seen:
            return
        self.seen.append(photo.key)
        if len(self.seen) > MAX_SEEN:
            del self.seen[:-MAX_SEEN]


@dataclass(frozen=True)
class ImportResult:
    imported: int = 0
    duplicates: int = 0
    failed: int = 0
    errors: tuple[str, ...] = ()


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value or "")


def _first_line(value: str) -> str:
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:300]
    return ""


def _query_error(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if any(marker in stripped for marker in _QUERY_ERROR_MARKERS):
            return stripped[:300]
    return ""


def parse_photos(output: str | bytes) -> tuple[PhonePhoto, ...]:
    """Turn ``content query`` rows into photos, skipping malformed rows."""

    photos: list[PhonePhoto] = []
    for line in _text(output).splitlines():
        match = _ROW_RE.match(line.strip())
        if match is None:
            continue
        name = match.group("name").strip()
        relative_path = match.group("path").strip()
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            continue
        folder = relative_path.strip("/")
        parts = folder.split("/")
        if (
            not folder.startswith(CAMERA_PATH.rstrip("/") + "/")
            and folder != CAMERA_PATH.rstrip("/")
        ):
            continue
        if any(part in ("", ".", "..") for part in parts):
            continue
        try:
            date_added = int(match.group("date"))
            size = int(match.group("size"))
        except ValueError:
            continue
        if date_added < 0 or size < 0:
            continue
        photos.append(
            PhonePhoto(
                remote_id=match.group("id"),
                name=name,
                relative_path=folder + "/",
                date_added=date_added,
                size=size,
            )
        )
    photos.sort(key=lambda photo: (photo.date_added, photo.remote_id))
    return tuple(photos)


def camera_query_command(adb_path: str, serial: str, since: int = 0) -> list[str]:
    """The adb command that lists camera photos in MediaStore order."""

    where = f"relative_path='{CAMERA_PATH}'"
    since = max(0, int(since))
    if since:
        where += f" AND date_added >= {since}"
    shell = (
        f"content query --uri {MEDIA_URI} "
        "--projection _id:_display_name:relative_path:date_added:_size "
        f'--where "{where}" --sort "date_added ASC"'
    )
    return [adb_path, "-s", serial, "shell", shell]


def safe_filename(name: str, fallback: str) -> str:
    """A single path component safe to create under the photo folder."""

    cleaned = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    cleaned = "".join(
        character for character in cleaned if character >= " " and character not in "/\\"
    )
    if cleaned in ("", ".", "..") or cleaned.startswith("."):
        cleaned = fallback
    return cleaned[:200]


def choose_destination(
    folder: Path, name: str, size: int
) -> tuple[Path, bool]:
    """Pick a free local path, or report an existing identical file.

    Returns ``(path, already_present)``.  A same-size file counts as already
    imported, which keeps a lost state file from creating duplicates.  A
    different file with the same name gets a numeric suffix.
    """

    candidate = folder / name
    if not candidate.exists():
        return candidate, False
    if size > 0 and candidate.is_file():
        try:
            if candidate.stat().st_size == size:
                return candidate, True
        except OSError:
            pass
    stem, suffix = os.path.splitext(name)
    for index in range(1, 1_000):
        candidate = folder / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate, False
        if size > 0 and candidate.is_file():
            try:
                if candidate.stat().st_size == size:
                    return candidate, True
            except OSError:
                pass
    raise OSError(f"no free filename for {name}")


def _write_atomically(destination: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".phone-link-", suffix=".part", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _set_photo_time(path: Path, date_added: int) -> None:
    if date_added <= 0:
        return
    try:
        os.utime(path, (date_added, date_added))
    except OSError:
        pass


def default_photo_folder() -> Path:
    return Path.home() / "Pictures" / "Phone Link"


def default_state_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "phone-link" / STATE_FILE


def load_state(path: Path | None = None) -> PhotoImportState:
    config = path or default_state_path()
    try:
        raw = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return PhotoImportState()
    if not isinstance(raw, dict):
        return PhotoImportState()
    since = raw.get("since", 0)
    if isinstance(since, bool) or not isinstance(since, int) or since < 0:
        since = 0
    seen = raw.get("seen", [])
    if not isinstance(seen, list):
        seen = []
    keys = [item for item in seen if isinstance(item, str) and item]
    return PhotoImportState(since=since, seen=keys[-MAX_SEEN:])


def save_state(state: PhotoImportState, path: Path | None = None) -> Path:
    config = path or default_state_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "since": max(0, int(state.since)),
        "seen": state.seen[-MAX_SEEN:],
    }
    temporary = config.with_name(config.name + ".tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, config)
    try:
        os.chmod(config, 0o600)
    except OSError:
        pass
    return config


def _run(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, timeout=timeout, check=False)


class PhotoImporter(QObject):
    """Copy new camera photos in a background thread.

    Set ``serial``, ``folder`` and ``state`` before calling :meth:`start`.
    ``run`` is synchronous and exists for tests and command-line use.
    """

    finished = Signal(int, int, int)  # imported, duplicates, failed
    failed = Signal(str)

    def __init__(
        self,
        adb_path: str | None,
        parent: QObject | None = None,
        *,
        runner: Runner | None = None,
    ) -> None:
        super().__init__(parent)
        self.adb_path = adb_path
        self.serial = ""
        self.folder = default_photo_folder()
        self.state = PhotoImportState()
        self.last_errors: tuple[str, ...] = ()
        self._runner = runner or _run
        self._busy = False
        self._stopped = False
        self._lock = threading.Lock()

    @property
    def is_busy(self) -> bool:
        return self._busy

    def stop(self) -> None:
        self._stopped = True

    def start(self, *, since: int | None = None) -> bool:
        """Begin one import; returns False when one is already running."""

        if self._stopped:
            return False
        with self._lock:
            if self._busy:
                return False
            self._busy = True
        try:
            threading.Thread(
                target=self._worker,
                args=(since,),
                name="photo-import",
                daemon=True,
            ).start()
        except Exception:
            self._busy = False
            raise
        return True

    def _worker(self, since: int | None) -> None:
        try:
            result = self.run(since=since)
        except PhotoImportError as exc:
            self._emit(self.failed, str(exc))
        except Exception as exc:  # a background thread must not kill the app
            self._emit(self.failed, f"Photo import failed: {exc}")
        else:
            self._emit(
                self.finished, result.imported, result.duplicates, result.failed
            )
        finally:
            with self._lock:
                self._busy = False

    def _emit(self, signal, *args) -> None:
        if self._stopped:
            return
        try:
            signal.emit(*args)
        except RuntimeError:
            pass

    def run(self, *, since: int | None = None) -> ImportResult:
        """One synchronous import pass."""

        if not self.adb_path:
            raise PhotoImportError("adb is not installed, so photos cannot be imported.")
        if not self.serial:
            raise PhotoImportError("No phone is connected over ADB.")

        since = self.state.since if since is None else max(0, int(since))
        command = camera_query_command(self.adb_path, self.serial, since)
        try:
            query = self._runner(command, timeout=QUERY_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PhotoImportError(f"Could not ask the phone for photos: {exc}") from exc

        output = _text(query.stdout) + _text(query.stderr)
        if query.returncode != 0:
            detail = _first_line(output) or f"adb exited with code {query.returncode}"
            raise PhotoImportError(f"Could not list camera photos: {detail}")
        photos = parse_photos(output)
        error_line = _query_error(output)
        if not photos and error_line:
            raise PhotoImportError(f"Could not list camera photos: {error_line}")

        folder = Path(self.folder)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PhotoImportError(f"Could not create {folder}: {exc}") from exc

        imported = 0
        duplicates = 0
        failed = 0
        successful_dates: list[int] = []
        errors: list[str] = []

        for photo in photos:
            if photo.key in self.state.seen:
                continue
            if photo.size > MAX_PHOTO_BYTES:
                failed += 1
                errors.append(f"{photo.name} is larger than 100 MB")
                continue
            try:
                pulled = self._runner(
                    [
                        self.adb_path,
                        "-s",
                        self.serial,
                        "exec-out",
                        "cat",
                        shlex.quote(photo.remote_path),
                    ],
                    timeout=PULL_TIMEOUT,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                failed += 1
                errors.append(f"{photo.name}: {exc}")
                continue
            data = pulled.stdout
            if isinstance(data, str):
                data = data.encode("utf-8", "replace")
            if pulled.returncode != 0 or not data:
                detail = _first_line(_text(pulled.stderr) or _text(pulled.stdout))
                failed += 1
                errors.append(f"{photo.name}: {detail or 'the file was empty'}")
                continue

            name = safe_filename(photo.name, f"photo-{photo.remote_id}.jpg")
            try:
                destination, already_present = choose_destination(
                    folder, name, len(data)
                )
            except OSError as exc:
                failed += 1
                errors.append(f"{name}: {exc}")
                continue
            if already_present:
                self.state.remember(photo)
                successful_dates.append(photo.date_added)
                duplicates += 1
                continue
            try:
                _write_atomically(destination, data)
                _set_photo_time(destination, photo.date_added)
            except OSError as exc:
                failed += 1
                errors.append(f"{name}: {exc}")
                continue
            self.state.remember(photo)
            successful_dates.append(photo.date_added)
            imported += 1

        self.last_errors = tuple(errors[:5])
        # Do not move the watermark past a failure: the next scan can retry it.
        if not failed and successful_dates:
            self.state.since = max(self.state.since, max(successful_dates))
        return ImportResult(
            imported=imported,
            duplicates=duplicates,
            failed=failed,
            errors=self.last_errors,
        )

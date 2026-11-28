"""Short-lived owner/session-bound restore files; never retain blobs in RAM."""
import os
import secrets
import tempfile
import time
from pathlib import Path
from .config import settings

_uploads = {}
_receiving = set()
_receiving_sids = set()


def _remove(token):
    entry = _uploads.pop(token, None)
    if entry:
        Path(entry["path"]).unlink(missing_ok=True)


def clear_uploads():
    for token in list(_uploads):
        _remove(token)


def cleanup():
    for token, entry in list(_uploads.items()):
        if entry["expires"] <= time.monotonic():
            _remove(token)


async def store(file, sid):
    cleanup()
    # Reserve before awaiting the first read: concurrent uploads count too.
    if sid in _receiving_sids:
        await file.close()
        raise ValueError("UPLOAD_IN_PROGRESS")
    # At most one pending archive per session, four across this process.
    for token, entry in list(_uploads.items()):
        if entry["sid"] == sid:
            _remove(token)
    if len(_uploads) + len(_receiving) >= 4:
        await file.close()
        raise ValueError("TOO_MANY_UPLOADS")
    fd, path = tempfile.mkstemp(prefix="hamchat-restore-")
    os.chmod(path, 0o600)
    size = 0
    token = secrets.token_urlsafe(32)
    _receiving.add(token)
    _receiving_sids.add(sid)
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.restore_max_bytes:
                    raise ValueError("TOO_LARGE")
                out.write(chunk)
        _uploads[token] = {"sid": sid, "path": path, "expires": time.monotonic() + 600}
        return token, Path(path).read_bytes()
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise
    finally:
        _receiving.discard(token)
        _receiving_sids.discard(sid)
        await file.close()


def consume(token, sid):
    cleanup()
    entry = _uploads.get(token)
    if not entry or entry["sid"] != sid:
        raise ValueError("UPLOAD_EXPIRED")
    try:
        return Path(entry["path"]).read_bytes()
    finally:
        _remove(token)

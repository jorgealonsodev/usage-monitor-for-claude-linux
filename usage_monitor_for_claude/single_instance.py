"""
Single-Instance Guard
======================

Prevents multiple instances from running simultaneously using an
advisory ``flock`` on a lock file in ``$XDG_RUNTIME_DIR`` (fallback
``/tmp``).  The holder's PID and version are stored as JSON in the
lock file so a new instance can identify and terminate it regardless
of executable name.
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import time
from pathlib import Path

from . import __version__, dialogs
from .i18n import T
from .instance_id import config_dir_suffix

__all__ = ['ensure_single_instance', 'release_instance_lock']

_LOCK_BASE_NAME = 'usage-monitor-for-claude'

# Seconds to wait for the old instance to exit (and its flock to be
# released by the kernel) after asking it to terminate.
_REPLACE_TIMEOUT = 5.0

# Open file object holding the flock, kept alive for the process
# lifetime; released on exit or explicitly via release_instance_lock().
_lock_file = None


def _lock_path() -> Path:
    """Return the per-instance lock file path.

    The name carries a config-dir suffix so one monitor instance per
    Claude account can run concurrently, each a singleton for its own
    config directory.
    """
    runtime_dir = os.environ.get('XDG_RUNTIME_DIR')
    base = Path(runtime_dir) if runtime_dir else Path('/tmp')
    return base / f'{_LOCK_BASE_NAME}{config_dir_suffix()}.lock'


def _try_acquire() -> bool:
    """Try to take the lock; on success store our PID and version.

    Returns
    -------
    bool
        True when the lock was acquired, False when another instance
        holds it.

    Raises
    ------
    OSError
        The lock file could not be opened (permissions, missing dir).
    """
    global _lock_file

    # 'a+' creates without truncating: truncating a lock file another
    # instance still holds would destroy its holder record.
    f = open(_lock_path(), 'a+', encoding='utf-8')
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False

    f.seek(0)
    f.truncate()
    json.dump({'pid': os.getpid(), 'version': __version__}, f)
    f.flush()
    _lock_file = f
    return True


def _read_holder_info() -> tuple[int | None, str | None]:
    """Read PID and version of the lock-holding instance from the lock file.

    Returns
    -------
    tuple[int | None, str | None]
        ``(pid, version)`` of the holder, or ``(None, None)`` if the
        lock file is missing or unreadable.
    """
    try:
        data = json.loads(_lock_path().read_text(encoding='utf-8'))
        pid = data.get('pid')
        version = data.get('version')
    except (OSError, ValueError, AttributeError):
        return None, None

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        pid = None
    if not isinstance(version, str) or not version:
        version = None
    return pid, version


def _terminate_pid(pid: int) -> None:
    """Terminate a process by PID and wait until it is fully dead.

    Sends SIGTERM first so the old instance can clean up, escalates to
    SIGKILL when it has not exited within the timeout.
    """
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return

    deadline = time.monotonic() + _REPLACE_TIMEOUT
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)


def ensure_single_instance() -> bool:
    """Ensure only one instance of the application is running.

    If another instance holds the lock, shows a dialog asking the user
    whether to replace it.  The dialog title includes the running
    instance's version when available.

    Returns
    -------
    bool
        True if this instance may proceed, False if it should exit.
    """
    try:
        if _try_acquire():
            return True
    except OSError as exc:
        # An unexpected failure (unwritable runtime dir) fails closed
        # rather than running a second, unguarded instance.
        dialogs.show_error(
            T['popup_title'],
            f'Failed to create the single-instance lock file:\n{_lock_path()}\n\n{exc}',
        )
        return False

    # Another instance is running - ask the user.
    holder_pid, running_version = _read_holder_info()

    title = T['popup_title']
    if running_version:
        title += f' v{running_version}'

    message = T['already_running'].format(
        running_version=running_version or '?',
    )

    if not dialogs.ask_yes_no(title, message):
        return False

    # Re-read the holder info after the dialog: it can stay open for a long
    # time, the old instance may have exited meanwhile, and the kernel
    # recycles PIDs - terminating the snapshotted PID could kill an
    # unrelated process.  A matching re-read PID is a liveness signal for
    # the snapshot (the holder rewrites the record whenever it takes over).
    current_holder_pid, _ = _read_holder_info()
    if holder_pid and current_holder_pid == holder_pid:
        _terminate_pid(holder_pid)

    # Re-acquiring the flock is the ground truth for whether the old
    # instance is really gone: the kernel releases it only when every
    # file descriptor of the holder is closed.  _terminate_pid is best
    # effort - it cannot signal a process of another user, and the old
    # instance may need a moment to die.
    deadline = time.monotonic() + _REPLACE_TIMEOUT
    while True:
        try:
            if _try_acquire():
                return True
        except OSError:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.1)

    dialogs.show_error(title, T['replace_failed'])
    return False


def release_instance_lock() -> None:
    """Release the lock file so a new instance can start."""
    global _lock_file

    if _lock_file is None:
        return

    try:
        fcntl.flock(_lock_file.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        _lock_file.close()
    except OSError:
        pass
    _lock_file = None

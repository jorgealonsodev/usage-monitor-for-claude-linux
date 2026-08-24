"""Entry point for ``python -m usage_monitor_for_claude``."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path

from usage_monitor_for_claude.dialogs import show_error
from usage_monitor_for_claude.instance_id import parse_config_dir

_verbose = '--verbose' in sys.argv

# --config-dir selects which Claude account to monitor. It must be
# resolved into CLAUDE_CONFIG_DIR before any other package import:
# api, settings, verbose and i18n all read the variable at import or
# first-use time. Keep every other package import below this block.
_config_dir = parse_config_dir(sys.argv)
if _config_dir is not None:
    _config_path = Path(_config_dir)
    if not _config_path.is_dir():
        show_error(
            'Usage Monitor for Claude - Error',
            f'--config-dir directory does not exist:\n{_config_dir}',
        )
        sys.exit(1)
    os.environ['CLAUDE_CONFIG_DIR'] = str(_config_path.resolve())

# In frozen builds stdout/stderr may go nowhere; --verbose attaches a
# console so diagnostics are visible.  Running from source already has one.
if _verbose and getattr(sys, 'frozen', False):
    from usage_monitor_for_claude.verbose import setup_console
    setup_console()

if _verbose:
    from usage_monitor_for_claude.verbose import print_startup_diagnostics
    print_startup_diagnostics()

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk

from usage_monitor_for_claude import notifications
from usage_monitor_for_claude.app import UsageMonitorForClaude, crash_log
from usage_monitor_for_claude.single_instance import ensure_single_instance, release_instance_lock

if _verbose:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)-5s %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )


def _verbose_step(label: str) -> None:
    """Print a startup progress step in verbose mode."""
    if _verbose:
        print(f'  [startup] {label}', flush=True)


def _run_app(app: UsageMonitorForClaude) -> None:
    """Run the poll loop in a background thread (GTK owns the main thread)."""
    try:
        if _verbose:
            from usage_monitor_for_claude.verbose import print_runtime_diagnostics
            print_runtime_diagnostics()

        _verbose_step('app.run...')
        app.run()
        _verbose_step('app.run returned')
    except Exception:
        _verbose_step(f'CRASH: {traceback.format_exc()}')
        crash_log(traceback.format_exc())
    finally:
        # Whatever ended the poll loop (quit, restart, crash), the GTK
        # main loop on the main thread must return.
        GLib.idle_add(Gtk.main_quit)


try:
    _verbose_step('ensure_single_instance...')
    if not ensure_single_instance():
        _verbose_step('another instance is running, exiting')
        sys.exit(0)
    _verbose_step('ensure_single_instance... OK')

    # Give notifications a fixed identity and logo instead of the live
    # tray icon.  Failure is non-fatal (notify-send/stderr fallbacks).
    _verbose_step('notifications.init...')
    notifications.init('Usage Monitor for Claude')

    _verbose_step('UsageMonitorForClaude()...')
    app = UsageMonitorForClaude()
    _verbose_step('UsageMonitorForClaude()... OK')

    # GTK owns the main thread; polling runs on a daemon thread and
    # marshals every UI mutation back through GLib.idle_add.
    threading.Thread(target=_run_app, args=(app,), daemon=True).start()

    _verbose_step('Gtk.main...')
    Gtk.main()
    _verbose_step('Gtk.main returned')

    if app.restart_requested:
        release_instance_lock()

        passthrough_args = []
        if _config_dir is not None:
            passthrough_args.append(f'--config-dir={os.environ["CLAUDE_CONFIG_DIR"]}')
        if _verbose:
            passthrough_args.append('--verbose')

        if getattr(sys, 'frozen', False):
            restart_command = [sys.executable, *passthrough_args]
        else:
            restart_command = [sys.executable, '-m', 'usage_monitor_for_claude', *passthrough_args]

        # start_new_session detaches the replacement from this dying
        # process so it survives the parent's exit and any signals to it.
        subprocess.Popen(restart_command, start_new_session=True)
except Exception:
    crash_log(traceback.format_exc())

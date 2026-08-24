"""
Dialogs
========

Minimal modal dialogs for startup-time errors and questions.

Uses a GTK ``MessageDialog`` when the GTK bindings import and a display
is available; degrades gracefully otherwise: ``show_error`` falls back
to stderr and ``ask_yes_no`` answers ``False`` (fail closed).

Kept free of package imports so every other module can use it without
import cycles.
"""
from __future__ import annotations

import sys

__all__ = ['ask_yes_no', 'show_error']

# Cached result of the GTK probe: unset (None), or a (Gtk,) tuple where the
# single element is the usable Gtk module or None when GTK is unavailable.
# The probe initializes a display connection, so it must not rerun per call.
_gtk_probe: tuple | None = None


def _get_gtk():
    """Return the Gtk module, or ``None`` when GTK or the display is unavailable."""
    global _gtk_probe
    if _gtk_probe is not None:
        return _gtk_probe[0]

    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk

        # init_check (unlike init) reports a missing display instead of
        # aborting the process.  PyGObject returns either a bool or a
        # (success, argv) tuple depending on the version.
        result = Gtk.init_check()
        success = result[0] if isinstance(result, tuple) else bool(result)
        gtk = Gtk if success else None
    except Exception:
        gtk = None

    _gtk_probe = (gtk,)
    return gtk


def show_error(title: str, message: str) -> None:
    """Show a modal error dialog, or print to stderr without a display.

    Parameters
    ----------
    title : str
        Dialog window title.
    message : str
        Error text to display.
    """
    gtk = _get_gtk()
    if gtk is None:
        print(f'{title}: {message}', file=sys.stderr)
        return

    try:
        dialog = gtk.MessageDialog(
            message_type=gtk.MessageType.ERROR,
            buttons=gtk.ButtonsType.OK,
            text=message,
        )
        dialog.set_title(title)
        dialog.set_keep_above(True)
        dialog.run()
        dialog.destroy()
    except Exception:
        print(f'{title}: {message}', file=sys.stderr)


def ask_yes_no(title: str, message: str) -> bool:
    """Ask a yes/no question via a modal dialog.

    Returns
    -------
    bool
        ``True`` only when the user clicked Yes.  Without a display (or
        on any GTK failure) the answer is ``False`` - the safe default
        for destructive questions like replacing a running instance.
    """
    gtk = _get_gtk()
    if gtk is None:
        return False

    try:
        dialog = gtk.MessageDialog(
            message_type=gtk.MessageType.QUESTION,
            buttons=gtk.ButtonsType.YES_NO,
            text=message,
        )
        dialog.set_title(title)
        dialog.set_keep_above(True)
        response = dialog.run()
        dialog.destroy()
        return response == gtk.ResponseType.YES
    except Exception:
        return False

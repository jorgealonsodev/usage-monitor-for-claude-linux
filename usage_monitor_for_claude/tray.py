"""
Tray
=====

System tray adapter on AyatanaAppIndicator3, exposing the minimal
surface the application needs: a settable PIL icon, a title, a static
menu built from plain data, and notification routing.

AppIndicator caches icons by file path, so every icon update is written
to a new monotonically-numbered PNG file inside a per-instance runtime
directory - reusing one filename would leave the panel showing the first
frame forever.  All UI mutations are marshalled to the GLib main loop,
so any thread may set ``icon``/``title``/``visible`` or call ``stop()``.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
from typing import Any, Callable

from . import notifications, tray_icon

__all__ = ['SEPARATOR', 'MenuItem', 'TrayIcon']

try:
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('AyatanaAppIndicator3', '0.1')
    from gi.repository import AyatanaAppIndicator3, GLib, Gtk
except Exception:  # bindings or display stack unavailable (headless import)
    AyatanaAppIndicator3 = None
    GLib = None
    Gtk = None

# Marker object for a menu separator row.
SEPARATOR = object()


class MenuItem:
    """One tray menu entry, described as plain data.

    Parameters
    ----------
    label : str
        Menu entry text.
    callback : callable or None
        Called without arguments on activation.  None for submenu roots.
    submenu : list or None
        Child ``MenuItem``/``SEPARATOR`` entries rendered as a submenu.
    checked : callable or None
        Zero-argument predicate rendering the entry as a check item whose
        state is re-evaluated every time the menu is shown.
    enabled : bool
        Whether the entry is sensitive.
    visible : bool
        Whether the entry is shown at all.
    default : bool
        Marks the menu's default action; the tray's middle-click
        (secondary activate) triggers this entry.
    """

    def __init__(
        self, label: str, callback: Callable[[], Any] | None = None, *,
        submenu: list | None = None, checked: Callable[[], bool] | None = None,
        enabled: bool = True, visible: bool = True, default: bool = False,
    ) -> None:
        self.label = label
        self.callback = callback
        self.submenu = submenu
        self.checked = checked
        self.enabled = enabled
        self.visible = visible
        self.default = default


class TrayIcon:
    """AppIndicator tray icon with a data-driven menu.

    Parameters
    ----------
    name : str
        Indicator id (also used for the icon file directory name).
    icon : PIL.Image.Image or None
        Initial icon image.
    title : str
        Initial title/tooltip text.
    menu : list or None
        ``MenuItem``/``SEPARATOR`` entries for the context menu.
    """

    def __init__(self, name: str, icon: Any = None, title: str = '', menu: list | None = None) -> None:
        self._name = name
        self._title = title
        self._icon_image = None
        self._icon_serial = 0
        self._icon_path: str | None = None
        self._icon_dir = self._make_icon_dir(name)
        self._icon_lock = threading.Lock()
        self._indicator = None
        self._gtk_menu = None
        self._check_syncers: list[Callable[[], None]] = []

        if AyatanaAppIndicator3 is not None:
            self._indicator = AyatanaAppIndicator3.Indicator.new(
                name, '', AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
            )
            self._indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
            self._build_menu(menu or [])
            if title:
                self._indicator.set_title(title)

        if icon is not None:
            self.icon = icon

    # Public surface used by the application

    @property
    def icon(self) -> Any:
        return self._icon_image

    @icon.setter
    def icon(self, image: Any) -> None:
        """Set the tray icon from a PIL image (any thread)."""
        self._icon_image = image
        if image is None or self._indicator is None:
            return

        # Write under the lock so two rapid renders cannot interleave their
        # serial numbers; only the GLib call is deferred to the main loop.
        with self._icon_lock:
            self._icon_serial += 1
            path = os.path.join(self._icon_dir, f'icon-{self._icon_serial}.png')
            # The margin is presentation: only what the panel renders is
            # padded, so callers keep the image they assigned.
            tray_icon.add_icon_margin(image).save(path, format='PNG')
            previous, self._icon_path = self._icon_path, path
        self._on_main(self._apply_icon, path, previous)

    @property
    def title(self) -> str:
        return self._title

    @title.setter
    def title(self, text: str) -> None:
        """Set the indicator title/tooltip (any thread)."""
        self._title = text
        if self._indicator is not None:
            self._on_main(self._indicator.set_title, text)

    @property
    def visible(self) -> bool:
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self._visible = bool(value)
        if self._indicator is not None:
            status = (
                AyatanaAppIndicator3.IndicatorStatus.ACTIVE if value
                else AyatanaAppIndicator3.IndicatorStatus.PASSIVE
            )
            self._on_main(self._indicator.set_status, status)

    _visible = True

    def notify(self, message: str, title: str = '') -> None:
        """Show a desktop notification (argument order kept from pystray)."""
        notifications.notify(title, message)

    def run(self, setup: Callable[[TrayIcon], None] | None = None) -> None:
        """Run the tray lifecycle: the indicator lives on the GLib main loop.

        The main loop itself is owned by the entry point (``Gtk.main()``),
        so this only invokes *setup* - typically the poll loop - on the
        calling thread and returns when it does.
        """
        if setup is not None:
            setup(self)

    def stop(self) -> None:
        """Hide the indicator and quit the GTK main loop (any thread)."""
        if self._indicator is not None:
            self._on_main(self._indicator.set_status, AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        if Gtk is not None:
            self._on_main(Gtk.main_quit)
        self._cleanup_icon_dir()

    # Internals

    @staticmethod
    def _make_icon_dir(name: str) -> str:
        """Create a private per-instance directory for the icon PNG files."""
        runtime_dir = os.environ.get('XDG_RUNTIME_DIR')
        if not runtime_dir or not os.path.isdir(runtime_dir):
            runtime_dir = os.path.join(os.path.expanduser('~'), '.cache')
            os.makedirs(runtime_dir, exist_ok=True)
        return tempfile.mkdtemp(prefix=f'{name}-', dir=runtime_dir)

    def _cleanup_icon_dir(self) -> None:
        shutil.rmtree(self._icon_dir, ignore_errors=True)

    def _on_main(self, func: Callable, *args: Any) -> None:
        """Run *func* on the GLib main loop; directly when GLib is unavailable."""
        if GLib is not None:
            GLib.idle_add(self._call_once, func, args)
        else:
            func(*args)

    @staticmethod
    def _call_once(func: Callable, args: tuple) -> bool:
        try:
            func(*args)
        except Exception:
            # A failed UI update (e.g. indicator disposed during shutdown)
            # must not leave a repeating idle handler behind.
            pass
        return False

    def _apply_icon(self, path: str, previous: str | None) -> None:
        """Point the indicator at the new icon file and drop the old one.

        AppIndicator resolves and caches by path, so the fresh filename is
        what forces the panel to actually reload the pixels.
        """
        self._indicator.set_icon_theme_path(self._icon_dir)
        self._indicator.set_icon_full(path, self._title or self._name)
        if previous:
            try:
                os.unlink(previous)
            except OSError:
                pass

    def _build_menu(self, items: list) -> None:
        """Map the ``MenuItem`` structure onto a ``Gtk.Menu``."""
        self._check_syncers = []
        menu = self._materialize_menu(items, top_level=True)
        menu.connect('show', self._on_menu_show)
        menu.show_all()
        # Re-apply per-item visibility: show_all() reveals hidden items too.
        self._apply_visibility(menu)
        self._gtk_menu = menu
        self._indicator.set_menu(menu)

    def _materialize_menu(self, items: list, top_level: bool = False) -> Any:
        menu = Gtk.Menu()
        for item in items:
            if item is SEPARATOR:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            menu.append(self._materialize_item(item, top_level))
        return menu

    def _materialize_item(self, item: MenuItem, top_level: bool) -> Any:
        if item.checked is not None:
            gtk_item = Gtk.CheckMenuItem(label=item.label)
            gtk_item.set_active(bool(item.checked()))
        else:
            gtk_item = Gtk.MenuItem(label=item.label)

        handler_id = None
        if item.submenu is not None:
            gtk_item.set_submenu(self._materialize_menu(item.submenu))
        elif item.callback is not None:
            handler_id = gtk_item.connect('activate', self._make_activate_handler(item.callback))

        if item.checked is not None:
            self._check_syncers.append(
                lambda gi_item=gtk_item, probe=item.checked, hid=handler_id: self._sync_check(gi_item, probe, hid),
            )

        gtk_item.set_sensitive(bool(item.enabled))
        if not item.visible:
            gtk_item.set_no_show_all(True)
            gtk_item.hide()
        gtk_item._tray_visible = bool(item.visible)  # noqa: SLF001 - own marker for _apply_visibility

        if top_level and item.default and self._indicator is not None:
            # Middle-click on the indicator triggers the default action.
            self._indicator.set_secondary_activate_target(gtk_item)

        return gtk_item

    @staticmethod
    def _make_activate_handler(callback: Callable[[], Any]) -> Callable:
        def _on_activate(_gtk_item: Any) -> None:
            callback()
        return _on_activate

    @staticmethod
    def _apply_visibility(menu: Any) -> None:
        for child in menu.get_children():
            if getattr(child, '_tray_visible', True) is False:
                child.hide()
            submenu = child.get_submenu() if hasattr(child, 'get_submenu') else None
            if submenu is not None:
                TrayIcon._apply_visibility(submenu)

    def _on_menu_show(self, _menu: Any) -> None:
        """Refresh check-item states each time the menu opens."""
        for sync in self._check_syncers:
            sync()

    @staticmethod
    def _sync_check(gtk_item: Any, probe: Callable[[], bool], handler_id: Any) -> None:
        try:
            state = bool(probe())
        except Exception:
            return
        if gtk_item.get_active() == state:
            return

        # A programmatic set_active emits 'activate' just like a user click,
        # so the callback handler is blocked while the state is synced.
        if handler_id is not None:
            gtk_item.handler_block(handler_id)
        try:
            gtk_item.set_active(state)
        finally:
            if handler_id is not None:
                gtk_item.handler_unblock(handler_id)

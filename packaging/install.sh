#!/bin/sh
# Usage Monitor for Claude - user-level installer (no sudo required).
# Installs into ~/.local (override with PREFIX=/some/prefix ./install.sh).
set -eu

APP=usage-monitor-for-claude
HERE=$(cd "$(dirname "$0")" && pwd -P)
PREFIX=${PREFIX:-"$HOME/.local"}

APT_LINE="sudo apt install python3 python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1 gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7 python3-requests python3-pil libxss1 fonts-dejavu-core"

# ---------------------------------------------------------------------------
# Runtime dependency check (system packages, not pip).
# ---------------------------------------------------------------------------
PY=
for candidate in /usr/bin/python3 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY=$(command -v "$candidate")
        break
    fi
done

MISSING=""
if [ -z "$PY" ]; then
    MISSING="python3 (interpreter not found)"
else
    "$PY" -c 'import gi' >/dev/null 2>&1 || MISSING="$MISSING python3-gi"
    if [ -z "$MISSING" ]; then
        "$PY" -c 'import gi; gi.require_version("Gtk", "3.0")' >/dev/null 2>&1 \
            || MISSING="$MISSING gir1.2-gtk-3.0"
        "$PY" -c 'import gi; gi.require_version("WebKit2", "4.1")' >/dev/null 2>&1 \
            || MISSING="$MISSING gir1.2-webkit2-4.1"
        "$PY" -c 'import gi; gi.require_version("AyatanaAppIndicator3", "0.1")' >/dev/null 2>&1 \
            || MISSING="$MISSING gir1.2-ayatanaappindicator3-0.1"
        "$PY" -c 'import gi; gi.require_version("Notify", "0.7")' >/dev/null 2>&1 \
            || MISSING="$MISSING gir1.2-notify-0.7"
    fi
    "$PY" -c 'import requests' >/dev/null 2>&1 || MISSING="$MISSING python3-requests"
    "$PY" -c 'import PIL' >/dev/null 2>&1 || MISSING="$MISSING python3-pil"
fi

if [ -n "$MISSING" ]; then
    echo "WARNING: missing runtime dependencies detected:$MISSING" >&2
    echo "The application will not start until they are installed. Run:" >&2
    echo "  $APT_LINE" >&2
    echo "Continuing with the file installation anyway..." >&2
    echo "" >&2
fi

# ---------------------------------------------------------------------------
# Install files.
# ---------------------------------------------------------------------------
# Application code (remove any previous install to avoid stale files).
rm -rf "$PREFIX/lib/$APP"
mkdir -p "$PREFIX/lib"
cp -R "$HERE/lib/$APP" "$PREFIX/lib/$APP"

# Launcher.
mkdir -p "$PREFIX/bin"
cp "$HERE/bin/$APP" "$PREFIX/bin/$APP"
chmod 755 "$PREFIX/bin/$APP"

# Desktop entry, icons, docs.
(cd "$HERE/share" && find . -type f) | while IFS= read -r f; do
    f=${f#./}
    mkdir -p "$PREFIX/share/$(dirname "$f")"
    cp "$HERE/share/$f" "$PREFIX/share/$f"
    chmod 644 "$PREFIX/share/$f"
done

# ---------------------------------------------------------------------------
# Refresh desktop caches (best effort).
# ---------------------------------------------------------------------------
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q "$PREFIX/share/applications" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q "$PREFIX/share/icons/hicolor" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Summary.
# ---------------------------------------------------------------------------
echo "Installed:"
echo "  Application: $PREFIX/lib/$APP"
echo "  Launcher:    $PREFIX/bin/$APP"
echo "  Desktop:     $PREFIX/share/applications/$APP.desktop"
echo "  Icons:       $PREFIX/share/icons/hicolor/*/apps/$APP.png"
echo "  Man page:    $PREFIX/share/man/man1/$APP.1.gz"
echo ""
echo "Run it with: $APP"
case ":${PATH}:" in
    *:"$PREFIX/bin":*) ;;
    *)
        echo "NOTE: $PREFIX/bin is not on your PATH. Add it, e.g.:"
        echo "  export PATH=\"$PREFIX/bin:\$PATH\""
        ;;
esac
echo "Uninstall with: ./uninstall.sh"

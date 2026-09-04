#!/usr/bin/env bash
# Build Linux distribution artifacts for Usage Monitor for Claude:
#   dist/usage-monitor-for-claude_<version>_all.deb
#   dist/usage-monitor-for-claude-<version>.tar.gz
#
# The version is read at build time from usage_monitor_for_claude/__init__.py
# (single source of truth) and is never hardcoded in packaging files.
# All staging happens in a temporary directory; only dist/ is written to.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
APP=usage-monitor-for-claude

# ---------------------------------------------------------------------------
# Version (single source of truth: __version__ in the package).
# ---------------------------------------------------------------------------
VERSION="$(sed -n "s/^__version__ = '\([^']*\)'.*/\1/p" \
    "$ROOT/usage_monitor_for_claude/__init__.py")"
if [ -z "$VERSION" ]; then
    echo "ERROR: could not read __version__ from usage_monitor_for_claude/__init__.py" >&2
    exit 1
fi
echo "Building $APP $VERSION"

MAINTAINER="J. Alonso <desarrollos@enfoquestic.com>"
# RFC 5322 build date; honours SOURCE_DATE_EPOCH so a reproducible build
# gets a stable changelog entry.
BUILD_DATE="$(date -R ${SOURCE_DATE_EPOCH:+-d "@$SOURCE_DATE_EPOCH"})"

DIST="$ROOT/dist"
mkdir -p "$DIST"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

DEPENDS="python3 (>= 3.10), python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1, gir1.2-ayatanaappindicator3-0.1, gir1.2-notify-0.7, python3-requests, python3-pil, libxss1, fonts-dejavu-core"

# ---------------------------------------------------------------------------
# Common payload tree (maps to /usr in the .deb and ~/.local in the tarball).
# ---------------------------------------------------------------------------
PAYLOAD="$STAGE/payload"
LIBDIR="$PAYLOAD/lib/$APP"
mkdir -p "$LIBDIR"

# Application package + locale/ as SIBLING dirs (i18n resolves
# Path(__file__).parent.parent / 'locale', so this layout is mandatory).
# Exclude __pycache__, *.pyc, tests and Windows .ico leftovers.
(cd "$ROOT" && find usage_monitor_for_claude locale -type f \
    ! -path '*/__pycache__/*' ! -name '*.pyc' ! -name '*.ico' -print) |
while IFS= read -r f; do
    install -Dm644 "$ROOT/$f" "$LIBDIR/$f"
done

# Launcher.
install -Dm755 "$ROOT/packaging/$APP" "$PAYLOAD/bin/$APP"

# Desktop entry.
install -Dm644 "$ROOT/packaging/$APP.desktop" \
    "$PAYLOAD/share/applications/$APP.desktop"
if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$PAYLOAD/share/applications/$APP.desktop"
fi

# Icons (hicolor theme, all provided sizes).
for size in 16 24 32 48 64 128 256; do
    install -Dm644 "$ROOT/packaging/icons/$APP-$size.png" \
        "$PAYLOAD/share/icons/hicolor/${size}x${size}/apps/$APP.png"
done

# Man page.
gzip -9nc "$ROOT/packaging/$APP.1" > "$STAGE/$APP.1.gz"
install -Dm644 "$STAGE/$APP.1.gz" "$PAYLOAD/share/man/man1/$APP.1.gz"

# Docs: upstream MIT LICENSE, the project changelog, and a Debian
# machine-readable copyright file generated from the license at build time.
install -Dm644 "$ROOT/LICENSE" "$PAYLOAD/share/doc/$APP/LICENSE"
gzip -9nc "$ROOT/CHANGELOG.md" > "$STAGE/changelog.gz"
install -Dm644 "$STAGE/changelog.gz" "$PAYLOAD/share/doc/$APP/changelog.gz"
{
    echo "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/"
    echo "Upstream-Name: Usage Monitor for Claude"
    echo "Upstream-Contact: Jens Duttke"
    echo "Source: https://github.com/jorgealonsodev/usage-monitor-for-claude-linux"
    echo ""
    echo "Files: *"
    echo "Copyright: 2026 Jens Duttke"
    echo "           2026 $MAINTAINER (Linux port)"
    echo "License: MIT"
    sed -e 's/^$/./' -e 's/^/ /' "$ROOT/LICENSE"
} > "$PAYLOAD/share/doc/$APP/copyright"
chmod 644 "$PAYLOAD/share/doc/$APP/copyright"

# ---------------------------------------------------------------------------
# .deb
# ---------------------------------------------------------------------------
DEBROOT="$STAGE/deb"
mkdir -p "$DEBROOT/DEBIAN" "$DEBROOT/usr"
cp -R "$PAYLOAD/." "$DEBROOT/usr/"

# Debian changelog - required for a non-native package, and Debian-only,
# so it is written here rather than into the shared payload.
{
    echo "$APP ($VERSION) unstable; urgency=medium"
    echo ""
    echo "  * Release $VERSION.  See changelog.gz in this directory for the"
    echo "    full list of changes."
    echo ""
    echo " -- $MAINTAINER  $BUILD_DATE"
} | gzip -9nc > "$DEBROOT/usr/share/doc/$APP/changelog.Debian.gz"
chmod 644 "$DEBROOT/usr/share/doc/$APP/changelog.Debian.gz"

# Normalize directory permissions (mktemp roots are group-writable).
find "$STAGE" -type d -exec chmod 755 {} +

INSTALLED_SIZE="$(du -sk "$DEBROOT/usr" | cut -f1)"

cat > "$DEBROOT/DEBIAN/control" <<EOF
Package: $APP
Version: $VERSION
Architecture: all
Maintainer: $MAINTAINER
Installed-Size: $INSTALLED_SIZE
Depends: $DEPENDS
Section: utils
Priority: optional
Homepage: https://github.com/jorgealonsodev/usage-monitor-for-claude-linux
Description: system tray monitor for Claude usage and quota limits
 Usage Monitor for Claude sits in the system tray and shows your current
 Claude usage against session and weekly quota limits, with a detailed
 popup, desktop notifications when thresholds are reached, and support
 for multiple languages.
 .
 This is the Linux port of the Windows application by Jens Duttke. It is
 a pure-Python GTK application; all runtime dependencies are provided by
 system packages (no bundled interpreter).
EOF

cat > "$DEBROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q /usr/share/icons/hicolor || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q || true
fi
exit 0
EOF

cat > "$DEBROOT/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q /usr/share/icons/hicolor || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q || true
fi
exit 0
EOF

chmod 755 "$DEBROOT/DEBIAN/postinst" "$DEBROOT/DEBIAN/postrm"

DEB_OUT="$DIST/${APP}_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$DEBROOT" "$DEB_OUT"
echo "Built: $DEB_OUT"

if command -v lintian >/dev/null 2>&1; then
    echo "--- lintian report (informational, does not fail the build) ---"
    lintian "$DEB_OUT" || true
    echo "--- end lintian report ---"
else
    echo "lintian not installed; skipping lint."
fi

# ---------------------------------------------------------------------------
# tar.gz (user-level install)
# ---------------------------------------------------------------------------
TARTOP="$APP-$VERSION"
TARDIR="$STAGE/tar/$TARTOP"
mkdir -p "$TARDIR"
cp -R "$PAYLOAD/." "$TARDIR/"
install -m755 "$ROOT/packaging/install.sh" "$TARDIR/install.sh"
install -m755 "$ROOT/packaging/uninstall.sh" "$TARDIR/uninstall.sh"

TAR_OUT="$DIST/$APP-$VERSION.tar.gz"
tar -czf "$TAR_OUT" -C "$STAGE/tar" "$TARTOP"
echo "Built: $TAR_OUT"

echo "Done."

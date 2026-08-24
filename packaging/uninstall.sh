#!/bin/sh
# Usage Monitor for Claude - user-level uninstaller.
# Removes a user install from ~/.local (override with PREFIX=/some/prefix).
set -eu

APP=usage-monitor-for-claude
PREFIX=${PREFIX:-"$HOME/.local"}

rm -rf "$PREFIX/lib/$APP"
rm -f "$PREFIX/bin/$APP"
rm -f "$PREFIX/share/applications/$APP.desktop"
for size in 16 24 32 48 64 128 256; do
    rm -f "$PREFIX/share/icons/hicolor/${size}x${size}/apps/$APP.png"
done
rm -rf "$PREFIX/share/doc/$APP"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q "$PREFIX/share/applications" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q "$PREFIX/share/icons/hicolor" 2>/dev/null || true
fi

echo "Uninstalled $APP from $PREFIX."
echo "User configuration (if any) under ~/.config was not removed."

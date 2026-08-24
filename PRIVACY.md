# Privacy Policy

**Usage Monitor for Claude** is a local desktop application that monitors your Claude API usage.

## Data Collection

This application does **not** collect, store, or transmit any personal data.

## Network Communication

The application communicates exclusively with `api.anthropic.com` to retrieve your current API usage
data. No other network connections are made.

## Credentials

The application reads your existing Claude OAuth token from the local Claude CLI configuration file
(`~/.claude/.credentials.json`, or the equivalent file in the directory set via `CLAUDE_CONFIG_DIR`
or `--config-dir`). This token is:

- Used solely in HTTP Authorization headers to authenticate with the Anthropic API
- Never logged, stored elsewhere, copied, or transmitted to any third party

The credentials file is only ever read - the application never writes to it.

## Local Storage

All usage data is kept in memory only and discarded when the application closes. An optional
settings file (`usage-monitor-settings.json`) is read-only - the application never creates or
modifies it.

The application writes only the following files, all in standard per-user locations:

- **Single-instance lock file** - `usage-monitor-for-claude*.lock` in `$XDG_RUNTIME_DIR`
  (fallback `/tmp`). Contains only the process ID and app version of the running instance, so a
  second launch can detect and optionally replace it.
- **Transient tray icon images** - the rendered tray icon PNGs, in a private temporary directory
  under `$XDG_RUNTIME_DIR` (fallback `~/.cache`). Each icon file is deleted as soon as it is
  replaced, and the directory is removed on exit.
- **Autostart entry** - `~/.config/autostart/usage-monitor-for-claude*.desktop` (respects
  `$XDG_CONFIG_HOME`). Written only when you enable autostart, removed when you disable it again.
- **UI state file** - `~/.config/usage-monitor-for-claude/state*.json` (respects
  `$XDG_CONFIG_HOME`). Contains only the screen coordinates the popup was last dragged to, so it
  can reopen in the same place. Written only when you drag the pinned popup.

Nothing else on disk is created or modified.

## Claude Code Installation

When the OAuth token has expired, the application runs `claude update` so that the Claude Code CLI
renews the token in its own credentials file. As a side effect of that command, a newer Claude Code
version may be installed. No other software on your system is modified.

## Third-Party Services

The application does not integrate with any analytics, tracking, advertising, or telemetry services.

## Contact

For questions about this privacy policy, please open an issue at
https://github.com/jorgealonsodev/usage-monitor-for-claude-linux/issues

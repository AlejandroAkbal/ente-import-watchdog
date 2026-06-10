# Ente Import Watchdog

`ente-import-watchdog` is a tiny macOS helper for long-running [Ente Photos](https://ente.io/) imports.

When Ente's desktop renderer crashes during a large import, the app can usually resume from its existing upload queue after it is reopened. This watchdog keeps the Mac awake, watches Ente's log for renderer crashes, and restarts Ente automatically so the import can keep grinding forward.

It does **not** modify your Ente account, upload queue, files, albums, metadata, or Google Takeout data.

## Why This Exists

Large Ente Photos imports, especially Google Takeout migrations, can hit renderer crashes in the desktop app. In practice, Ente often uploads some files, crashes, and then resumes on the next launch.

This tool embraces that recovery path:

1. You start the import in Ente.
2. The watchdog waits in the background.
3. If Ente logs `render-process-gone: crashed` or `render-process-gone: killed`, the watchdog force-clears stale Ente processes.
4. It relaunches Ente.
5. Ente resumes using its own pending upload state.

## Features

- Watches the standard Ente desktop log at `~/Library/Logs/ente/ente.log`
- Detects renderer crash markers:
  - `render-process-gone: crashed`
  - `render-process-gone: killed`
- Restarts Ente with a cooldown to avoid rapid restart loops
- Keeps macOS awake with `caffeinate`
- Handles full process exits as well as renderer crashes
- Writes an audit log to `~/Library/Logs/ente-import-watchdog.log`
- Uses only Python standard library modules

## Requirements

- macOS
- Python 3.10+
- Ente Photos desktop app installed as `Ente`

This script uses macOS commands available by default:

- `open`
- `pgrep`
- `pkill`
- `caffeinate`

## Quick Start

Clone the repository:

```bash
git clone https://github.com/AlejandroAkbal/ente-import-watchdog.git
cd ente-import-watchdog
```

Start Ente and begin your import manually. Then run:

```bash
python3 ente_import_watchdog.py
```

Leave the terminal open. If Ente crashes, the watchdog will restart it.

## Recommended Workflow

1. Open Ente Photos.
2. Start your import using Ente's normal UI.
3. Start the watchdog:

   ```bash
   python3 ente_import_watchdog.py
   ```

4. Let it run until the import finishes.
5. Stop the watchdog with `Ctrl-C`.

## What It Will Not Do

The watchdog intentionally does not:

- Upload files itself
- Click around the Ente UI
- Edit `upload-status.json`
- Reset or delete Ente state
- Move, delete, or reorganize your photos
- Patch the Ente app
- Interact with your Ente account or credentials

It only restarts the desktop app when it crashes or exits.

## Options

```bash
python3 ente_import_watchdog.py --help
```

Common options:

```bash
# Show what would happen without killing or launching Ente
python3 ente_import_watchdog.py --dry-run

# Do not keep the Mac awake
python3 ente_import_watchdog.py --no-caffeinate

# Use SIGTERM instead of SIGKILL when clearing stale Ente processes
python3 ente_import_watchdog.py --no-force-kill

# Write watchdog logs somewhere else
python3 ente_import_watchdog.py --watchdog-log ./watchdog.log

# Watch a non-standard Ente log path
python3 ente_import_watchdog.py --ente-log /path/to/ente.log
```

## Logs

By default, watchdog activity is written to:

```text
~/Library/Logs/ente-import-watchdog.log
```

Example:

```text
[2026-06-09T23:07:42] Watchdog starting
[2026-06-09T23:07:42] Watching Ente log: /Users/example/Library/Logs/ente/ente.log
[2026-06-09T23:14:40] Restarting Ente: renderer crash detected in log
[2026-06-09T23:14:45] Starting Ente
```

## Safety Notes

This tool is intentionally small, but it still kills and restarts a desktop app.

- Run it only while you are intentionally doing an Ente import.
- Keep Ente's own upload queue intact; this tool relies on Ente's normal resume behavior.
- If the app gets into a bad state, stop the watchdog with `Ctrl-C`, quit Ente, and restart manually.
- The watchdog uses `pkill -9` by default because Ente can leave behind black or unresponsive renderer windows after a crash. Use `--no-force-kill` if you prefer a gentler restart.

## Troubleshooting

### The watchdog starts but does not restart Ente

Make sure Ente is writing crash lines to:

```text
~/Library/Logs/ente/ente.log
```

You can check recent crashes with:

```bash
grep 'render-process-gone' ~/Library/Logs/ente/ente.log | tail
```

### Ente restarts too often

Increase the cooldown:

```bash
python3 ente_import_watchdog.py --restart-cooldown 60
```

### Ente is installed under a different app name

Pass a custom app name:

```bash
python3 ente_import_watchdog.py --app-name "Ente Photos"
```

### The Mac sleeps during the import

Do not pass `--no-caffeinate`. The default behavior starts:

```bash
caffeinate -dimsu -w <watchdog-pid>
```

## Development

Run a syntax check:

```bash
python3 -m py_compile ente_import_watchdog.py
```

Run in dry-run mode:

```bash
python3 ente_import_watchdog.py --dry-run
```

## License

MIT

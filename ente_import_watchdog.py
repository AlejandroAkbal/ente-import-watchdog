#!/usr/bin/env python3
"""Restart Ente Photos when its renderer crashes during long imports.

The watchdog is deliberately conservative: it only observes Ente's log and
process state, then restarts the app when it detects a renderer crash or a full
process exit. It does not modify Ente's upload queue, account data, or files.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


DEFAULT_APP_NAME = "Ente"
DEFAULT_APP_PROCESS_PATTERN = "/Applications/ente.app"
DEFAULT_LOG_PATH = Path.home() / "Library/Logs/ente/ente.log"
DEFAULT_WATCHDOG_LOG_PATH = Path.home() / "Library/Logs/ente-import-watchdog.log"

CRASH_MARKERS = (
    "render-process-gone: crashed",
    "render-process-gone: killed",
)


@dataclass(frozen=True)
class WatchdogConfig:
    app_name: str
    app_process_pattern: str
    app_helper_pattern: str
    ente_log: Path
    watchdog_log: Path
    poll_interval: float
    restart_cooldown: float
    restart_delay: float
    post_start_grace: float
    missing_process_grace: float
    force_kill: bool
    keep_awake: bool
    launch_on_start: bool
    dry_run: bool


class Watchdog:
    def __init__(self, config: WatchdogConfig) -> None:
        self.config = config
        self.known_inode = 0
        self.offset = 0
        self.last_restart_at = 0.0
        self.missing_since: float | None = None
        self.caffeinate: subprocess.Popen[bytes] | None = None
        self.should_stop = False

    def log(self, message: str) -> None:
        line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
        print(line, flush=True)
        self.config.watchdog_log.parent.mkdir(parents=True, exist_ok=True)
        with self.config.watchdog_log.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def start(self) -> int:
        self.log("Watchdog starting")
        self.log(f"Watching Ente log: {self.config.ente_log}")
        self.log(f"Writing watchdog log: {self.config.watchdog_log}")
        self.log("Upload queue will not be modified")

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        if self.config.keep_awake:
            self._start_caffeinate()

        self.known_inode, self.offset = self._follow_log_from_end()

        if self.config.launch_on_start and not self._ente_pids():
            self._start_ente()

        try:
            self._loop()
        finally:
            self._stop_caffeinate()
            self.log("Watchdog stopped")

        return 0

    def _handle_signal(self, _signum: int, _frame: object) -> None:
        self.should_stop = True

    def _loop(self) -> None:
        while not self.should_stop:
            now = time.time()
            data = self._read_new_log()

            if any(marker in data for marker in CRASH_MARKERS):
                if now - self.last_restart_at >= self.config.restart_cooldown:
                    self.last_restart_at = now
                    self._restart_ente("renderer crash detected in log")
                    self.missing_since = None
                else:
                    self.log("Crash detected, restart suppressed by cooldown")

            if self._ente_pids():
                self.missing_since = None
            else:
                if self.missing_since is None:
                    self.missing_since = now
                    self.log("Ente process is not running")
                elif (
                    now - self.missing_since >= self.config.missing_process_grace
                    and now - self.last_restart_at >= self.config.restart_cooldown
                ):
                    self.last_restart_at = now
                    self._restart_ente("process exited")
                    self.missing_since = None

            time.sleep(self.config.poll_interval)

    def _ente_pids(self) -> list[str]:
        result = subprocess.run(
            ["pgrep", "-f", self.config.app_process_pattern],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def _start_ente(self) -> None:
        self.log(f"Starting {self.config.app_name}")
        if self.config.dry_run:
            return
        subprocess.run(["open", "-a", self.config.app_name], check=False)

    def _stop_stale_ente(self) -> None:
        signal_arg = "-9" if self.config.force_kill else "-TERM"
        patterns = (
            self.config.app_process_pattern,
            self.config.app_helper_pattern,
        )
        for pattern in patterns:
            if self.config.dry_run:
                self.log(f"Would pkill {signal_arg} -f {pattern}")
            else:
                subprocess.run(["pkill", signal_arg, "-f", pattern], check=False)

        if self.config.dry_run:
            self.log("Would pkill app executable named ente")
        else:
            subprocess.run(["pkill", signal_arg, "-x", "ente"], check=False)

    def _restart_ente(self, reason: str) -> None:
        self.log(f"Restarting {self.config.app_name}: {reason}")
        self._stop_stale_ente()
        time.sleep(self.config.restart_delay)
        self._start_ente()
        time.sleep(self.config.post_start_grace)

    def _start_caffeinate(self) -> None:
        try:
            self.caffeinate = subprocess.Popen(
                ["caffeinate", "-dimsu", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.log("Keeping system awake with caffeinate")
        except OSError as exc:
            self.log(f"Could not start caffeinate: {exc}")

    def _stop_caffeinate(self) -> None:
        if self.caffeinate:
            self.caffeinate.terminate()
            self.caffeinate = None

    def _follow_log_from_end(self) -> tuple[int, int]:
        if not self.config.ente_log.exists():
            return 0, 0
        stat = self.config.ente_log.stat()
        return stat.st_ino, stat.st_size

    def _read_new_log(self) -> str:
        if not self.config.ente_log.exists():
            return ""

        stat = self.config.ente_log.stat()
        if stat.st_ino != self.known_inode or stat.st_size < self.offset:
            self.known_inode = stat.st_ino
            self.offset = 0

        with self.config.ente_log.open("r", encoding="utf-8", errors="ignore") as handle:
            handle.seek(self.offset)
            data = handle.read()
            self.offset = handle.tell()
        return data


def parse_args(argv: list[str]) -> WatchdogConfig:
    parser = argparse.ArgumentParser(
        description="Restart Ente Photos when it crashes during long imports.",
    )
    parser.add_argument("--app-name", default=DEFAULT_APP_NAME)
    parser.add_argument("--app-process-pattern", default=DEFAULT_APP_PROCESS_PATTERN)
    parser.add_argument("--app-helper-pattern", default="ente Helper")
    parser.add_argument("--ente-log", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--watchdog-log", type=Path, default=DEFAULT_WATCHDOG_LOG_PATH)
    parser.add_argument("--poll-interval", type=float, default=3.0)
    parser.add_argument("--restart-cooldown", type=float, default=20.0)
    parser.add_argument("--restart-delay", type=float, default=5.0)
    parser.add_argument("--post-start-grace", type=float, default=15.0)
    parser.add_argument("--missing-process-grace", type=float, default=15.0)
    parser.add_argument(
        "--no-force-kill",
        action="store_true",
        help="Use SIGTERM instead of SIGKILL when clearing stale Ente processes.",
    )
    parser.add_argument(
        "--no-caffeinate",
        action="store_true",
        help="Do not keep the Mac awake while the watchdog is running.",
    )
    parser.add_argument(
        "--no-launch-on-start",
        action="store_true",
        help="Do not launch Ente if it is not running when the watchdog starts.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log the actions that would be taken without killing or launching Ente.",
    )

    args = parser.parse_args(argv)
    return WatchdogConfig(
        app_name=args.app_name,
        app_process_pattern=args.app_process_pattern,
        app_helper_pattern=args.app_helper_pattern,
        ente_log=args.ente_log,
        watchdog_log=args.watchdog_log,
        poll_interval=args.poll_interval,
        restart_cooldown=args.restart_cooldown,
        restart_delay=args.restart_delay,
        post_start_grace=args.post_start_grace,
        missing_process_grace=args.missing_process_grace,
        force_kill=not args.no_force_kill,
        keep_awake=not args.no_caffeinate,
        launch_on_start=not args.no_launch_on_start,
        dry_run=args.dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv if argv is not None else sys.argv[1:])
    return Watchdog(config).start()


if __name__ == "__main__":
    raise SystemExit(main())

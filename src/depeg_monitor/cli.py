"""Command line entry. ``--loop`` is for a machine you control; Actions runs once."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from depeg_monitor.config import ConfigError, load_config
from depeg_monitor.notify import build_notifiers
from depeg_monitor.runner import run_once
from depeg_monitor.state import load_state

log = logging.getLogger("depeg_monitor")

_BANNER = (
    "depeg-monitor: alerts only. No keys, no orders, not financial advice.\n"
    "只监控、只告警。不持有密钥，不下单，不构成投资建议。"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alert-only stablecoin depeg monitor.")
    parser.add_argument(
        "--config",
        default=os.environ.get("CONFIG_PATH", "config.yaml"),
        help="YAML config path (default: config.yaml or CONFIG_PATH)",
    )
    parser.add_argument(
        "--state",
        default=os.environ.get("STATE_PATH", ".state/monitor_state.json"),
        help="JSON state path for dedup and recovery",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print alerts instead of sending them"
    )
    parser.add_argument("--loop", action="store_true", help="Repeat until interrupted")
    parser.add_argument("--interval", type=float, default=30.0, help="Seconds between loop cycles")
    parser.add_argument("--no-save-state", action="store_true", help="Do not write the state file")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.loop and args.interval <= 0:
        print("--interval must be positive", file=sys.stderr)
        return 2
    try:
        config = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2
    state_path = Path(args.state)
    state = load_state(state_path)
    notifiers = [] if args.dry_run else build_notifiers()
    print(_BANNER)
    while True:
        started = time.time()
        try:
            code = run_once(
                config,
                state,
                dry_run=args.dry_run,
                save=not args.no_save_state,
                state_path=state_path,
                notifiers=notifiers,
                now=started,
            )
        except KeyboardInterrupt:
            print("stopped")
            return 0
        except Exception:
            log.exception("cycle failed")
            if not args.loop:
                return 1
            code = 1
        if code == 2 or not args.loop:
            return code
        remaining = args.interval - (time.time() - started)
        if remaining > 0:
            try:
                time.sleep(remaining)
            except KeyboardInterrupt:
                print("stopped")
                return 0

"""Command-line entry point: `draftdesk <command>`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .config import Config, ConfigError, load_config, load_knowledge, parse_config
from .pipeline import Pipeline
from .registry import available, build_providers
from .store import Store

log = logging.getLogger("draftdesk")


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader so secrets can stay out of the shell history."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def build_pipeline(config: Config, base_dir: Path | None = None, store: Store | None = None) -> Pipeline:
    email, commerce, llm = build_providers(config)
    knowledge = load_knowledge(config.knowledge, base_dir)
    return Pipeline(config, email, commerce, llm, store or Store(config.storage_path), knowledge)


def demo_config(db_path: str = "data/demo.db") -> Config:
    from .demo_data import path as demo_path

    return parse_config(
        {
            "store": {
                "name": "Maple Lane Goods",
                "signature": "Maple Lane Goods Support",
                "form_senders": ["noreply@shop-platform.example"],
                "ignore_senders": ["orders@shop-platform.example"],
            },
            "email": {"provider": "mock"},
            "commerce": {"provider": "mock"},
            "llm": {"provider": "mock"},
            "knowledge": {"paths": [str(demo_path("knowledge.md"))]},
            "storage_path": db_path,
        }
    )


def _serve(pipeline: Pipeline, host: str, port: int) -> None:
    import uvicorn

    from .web import create_app

    password = os.environ.get("DRAFTDESK_DASHBOARD_PASSWORD")
    if host not in ("127.0.0.1", "localhost") and not password:
        log.warning(
            "dashboard is listening on %s without DRAFTDESK_DASHBOARD_PASSWORD set; "
            "anyone who can reach it can send email as your store", host,
        )
    uvicorn.run(create_app(pipeline, password=password), host=host, port=port, log_level="warning")


def _poll_forever(pipeline: Pipeline, interval: int) -> None:
    while True:
        try:
            pipeline.run_once()
        except Exception:  # noqa: BLE001 - keep polling through transient provider failures
            log.exception("inbox check failed; will retry in %ds", interval)
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="draftdesk", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def with_config(p: argparse.ArgumentParser) -> None:
        p.add_argument("-c", "--config", default="config/config.yaml", help="path to the YAML config")

    def with_server(p: argparse.ArgumentParser) -> None:
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=8000)

    p = sub.add_parser("demo", help="run the full workflow offline with sample data")
    with_server(p)
    p = sub.add_parser("run-once", help="check the inbox once, draft replies, then exit")
    with_config(p)
    p = sub.add_parser("poll", help="check the inbox on a timer (no dashboard)")
    with_config(p)
    p = sub.add_parser("serve", help="run the review dashboard and poll the inbox in the background")
    with_config(p)
    with_server(p)
    p.add_argument("--no-poll", action="store_true", help="dashboard only; do not poll the inbox")
    sub.add_parser("providers", help="list the available adapters")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _load_dotenv()

    try:
        if args.command == "providers":
            for kind in ("email", "commerce", "llm"):
                print(f"{kind:9} {', '.join(available(kind))}")
            return 0

        if args.command == "demo":
            db = Path("data/demo.db")
            db.unlink(missing_ok=True)  # the demo always starts from a clean queue
            pipeline = build_pipeline(demo_config(str(db)))
            summary = pipeline.run_once()
            print(
                f"Demo inbox processed: {summary.drafted} drafts, {summary.skipped} skipped.\n"
                f"Open http://{args.host}:{args.port} to review them. Ctrl+C to stop."
            )
            _serve(pipeline, args.host, args.port)
            return 0

        config_path = Path(args.config)
        config = load_config(config_path)
        # Relative knowledge paths resolve against the directory you run from.
        pipeline = build_pipeline(config, Path.cwd())

        if args.command == "run-once":
            summary = pipeline.run_once()
            print(
                f"fetched={summary.fetched} drafted={summary.drafted} skipped={summary.skipped} "
                f"errors={summary.errors} duplicates={summary.duplicates}"
            )
            return 1 if summary.errors else 0
        if args.command == "poll":
            _poll_forever(pipeline, config.poll_interval_seconds)
        if args.command == "serve":
            if not args.no_poll:
                threading.Thread(
                    target=_poll_forever, args=(pipeline, config.poll_interval_seconds), daemon=True
                ).start()
            _serve(pipeline, args.host, args.port)
        return 0
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

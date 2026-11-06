"""Run the server: python -m slowcast (or the `slowcast` console script)."""

from __future__ import annotations

import argparse
import os

import uvicorn

from .config import Config, load_config


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="slowcast", description="Self-hosted article-to-podcast server."
    )
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--db", default=None, help="path to the sqlite database")
    args = parser.parse_args()

    cfg: Config = load_config()
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    if args.db:
        cfg.db_path = os.path.Path(args.db)  # type: ignore[attr-defined]

    import slowcast

    print(f"🐢 Slowcast {slowcast.__version__}")
    print(f"   UI       {cfg.public_url}/")
    print(f"   feed     {cfg.public_url}/feed/<token>.xml (token in the UI)")
    print(f"   db       {cfg.db_path}")

    from .web import create_app

    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()

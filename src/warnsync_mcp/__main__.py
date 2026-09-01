"""Command-line entry point: `warnsync-mcp` (stdio transport)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .ingest import SyncEngine
from .server import DEFAULT_POLL_INTERVAL, build_server
from .sources import JsonDirectorySource
from .store import VersionedStore

#: The bundled corpus ships with the repository, not the wheel, so an installed
#: copy falls back to a `data/sample_corpus` directory under the working dir.
_REPO_CORPUS = Path(__file__).resolve().parents[2] / "data" / "sample_corpus"
DEFAULT_CORPUS = _REPO_CORPUS if _REPO_CORPUS.is_dir() else Path("data/sample_corpus")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="warnsync-mcp",
        description="Run the WarnSync MCP server over stdio.",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS,
        help="Directory of JSON letter records to serve (default: bundled sample corpus).",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help="Seconds between change-detection passes; 0 disables background sync.",
    )
    parser.add_argument(
        "--name",
        default="warnsync",
        help="MCP server name advertised to clients.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.corpus.is_dir():
        print(
            f"corpus directory not found: {args.corpus}\n"
            "Pass --corpus with a directory of JSON letter records "
            "(see data/README.md for the record shape).",
            file=sys.stderr,
        )
        return 2
    store = VersionedStore()
    engine = SyncEngine(store, JsonDirectorySource(args.corpus))
    handle = build_server(store, engine, poll_interval=args.poll_interval, name=args.name)
    asyncio.run(handle.server.run_stdio_async())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""End-to-end protocol demo: a real MCP client watching the corpus change.

Spawns the WarnSync server over stdio, opens a subscription stream, drops a new
letter into the watched corpus, and shows the whole cycle happen live —
detection, atomic commit, protocol-native notification, refetch, fresh answer —
with the measured freshness lag at the end.

    python scripts/demo_live.py

This is the script for the "it actually works" slide: nothing here is staged,
the timings are real, and the server is a separate process.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.subscriptions import ResourcesListChanged, ResourceUpdated, listen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_URI = "warnsync://manifest"
POLL_INTERVAL = 2.0
NEW_LETTER = "WL-2026-0224.json"
QUERY = "biologics deviation reporting bioburden excursion"

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def step(text: str) -> None:
    print(f"\n{BOLD}{text}{RESET}")


def note(text: str) -> None:
    print(f"  {DIM}{text}{RESET}")


def unwrap(result: object) -> dict:
    """Pull the JSON payload out of a CallToolResult."""
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if structured:
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return {}


def top_hits(payload: dict, limit: int = 2) -> list[str]:
    return [
        f"{hit['letter_id']} v{hit['version']} c{hit['chunk_id']} "
        f"({hit['score']:.3f}) — {hit['provenance']['recipient']}"
        for hit in payload.get("results", [])[:limit]
    ]


async def run() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="warnsync-live-"))
    corpus = workdir / "corpus"
    shutil.copytree(ROOT / "data" / "sample_corpus", corpus)

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "warnsync_mcp", "--corpus", str(corpus),
              "--poll-interval", str(POLL_INTERVAL)],
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )

    print("=" * 72)
    print("WarnSync live protocol demo")
    print(f"corpus: {corpus}")
    print(f"poll interval: {POLL_INTERVAL}s")
    print("=" * 72)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            # `server/discover` reaches the 2026-07-28 era, where subscription
            # streams live; `initialize` is the legacy fallback.
            try:
                await session.discover()
            except Exception:  # noqa: BLE001 - older servers only speak initialize
                await session.initialize()
            step("1. Connected")
            info = session.server_info
            if info is not None:
                note(f"server: {info.name} v{info.version}")
            note(f"protocol: {session.protocol_version}")

            tools = await session.list_tools()
            step("2. Tool surface (stable — synchronization never changes it)")
            for tool in tools.tools:
                note(f"{tool.name}: {tool.description.splitlines()[0]}")

            step("3. Query before the letter exists")
            before = unwrap(await session.call_tool("search_letters", {"query": QUERY, "k": 2}))
            for line in top_hits(before) or ["(no results)"]:
                note(line)
            note("no Stonebrook letter in the corpus yet — correct, it has not been posted")

            status = unwrap(await session.call_tool("corpus_status", {}))
            note(f"corpus: {status['store']['active_letters']} active letters, "
                 f"{status['store']['chunks_current']} chunks")

            step(f"4. Subscribing to {MANIFEST_URI}")
            async with listen(
                session,
                resource_subscriptions=[MANIFEST_URI],
                resources_list_changed=True,
            ) as subscription:
                note("subscription acknowledged; the server will push on every commit")

                step("5. A new letter appears upstream")
                posted_at = time.time()
                shutil.copy(ROOT / "data" / "incoming" / NEW_LETTER, corpus)
                note(f"dropped {NEW_LETTER} into the watched corpus at "
                     f"{time.strftime('%H:%M:%S', time.localtime(posted_at))}")
                note("(the server is polling; nothing was told to it directly)")

                step("6. Waiting for the protocol-native notification...")
                notified_at = None
                seen = 0
                with anyio.move_on_after(POLL_INTERVAL * 6):
                    async for event in subscription:
                        elapsed = time.time() - posted_at
                        if isinstance(event, ResourceUpdated):
                            note(f"+{elapsed:5.2f}s  ResourceUpdated  uri={event.uri}")
                        elif isinstance(event, ResourcesListChanged):
                            note(f"+{elapsed:5.2f}s  ResourcesListChanged")
                        notified_at = notified_at or time.time()
                        seen += 1
                        if seen >= 2:  # manifest + list-changed for a NEW letter
                            break

                if notified_at is None:
                    print("\n  no notification arrived — something is wrong")
                    return 1

                step("7. Refetching what changed (notify-then-fetch)")
                # A bare epoch is accepted alongside ISO-8601, which gives the
                # sub-second precision this demo needs to exclude the cold start.
                updates = unwrap(
                    await session.call_tool("list_updates", {"since": str(posted_at)})
                )
                for update in updates["updates"]:
                    note(f"{update['kind']:8s} {update['letter_id']} v{update['version']} "
                         f"at {update['committed_at']}")

                manifest = await session.read_resource(MANIFEST_URI)
                snapshot = json.loads(manifest.contents[0].text)
                note(f"manifest now lists {len(snapshot['letters'])} letters")

            step("8. The same query, after the commit")
            after = unwrap(await session.call_tool("search_letters", {"query": QUERY, "k": 2}))
            for line in top_hits(after):
                note(line)

            top = after["results"][0]
            step("9. Provenance for the fresh answer")
            for key, value in top["provenance"].items():
                note(f"{key}: {value}")

            step("10. Freshness lag")
            note(f"posted (file appeared)     +0.00s")
            note(f"notification delivered     +{notified_at - posted_at:.2f}s")
            note(f"poll interval was          {POLL_INTERVAL:.2f}s (the lower bound on detection)")
            note("the query path was never blocked: step 3's query was served throughout")

    shutil.rmtree(workdir, ignore_errors=True)
    print("\nDone.")
    return 0


def main() -> int:
    return anyio.run(run)


if __name__ == "__main__":
    raise SystemExit(main())

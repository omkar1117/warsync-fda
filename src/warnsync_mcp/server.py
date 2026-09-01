"""The MCP serving plane: a stable tool surface over a corpus that keeps moving.

Two properties are the point of this module.

*The tool surface never changes.* Synchronization alters the data behind the
tools, never the tools themselves, so a connected agent is never asked to
rediscover its capabilities mid-session.

*Freshness is signalled in-protocol.* When a commit lands, the server publishes
a `ResourceUpdated` event for the corpus manifest (and a `ResourcesListChanged`
when letters are added or withdrawn). Events carry no payload: a client learns
only that something moved and refetches what it actually depends on, so a
duplicated or dropped event costs a refetch and nothing else.

On the 2026-07-28 protocol revision clients open that stream with
`subscriptions/listen` (SEP-2575); earlier revisions spelled the same
notify-then-fetch pattern `resources/subscribe` plus
`notifications/resources/updated`. The design is unchanged either way.
"""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

import anyio
from mcp.server import MCPServer
from mcp.server.subscriptions import InMemorySubscriptionBus
from mcp.shared.subscriptions import ResourcesListChanged, ResourceUpdated

from .ingest import SyncEngine
from .models import ChangeEvent, iso
from .store import VersionedStore

MANIFEST_URI = "warnsync://manifest"
LETTER_URI = "warnsync://letter/{letter_id}"

DEFAULT_POLL_INTERVAL = 30.0

INSTRUCTIONS = """\
WarnSync serves a versioned corpus of regulatory enforcement letters.

Every retrieval result carries provenance: letter id, version, chunk id and
ingestion timestamp. Cite them — an answer without them is not auditable, and
the corpus changes underneath you.

The corpus is synchronized in the background. Subscribe to warnsync://manifest
to be told when it changes, then call list_updates to see what moved. Nothing
you have already retrieved is invalidated: earlier versions remain readable
through get_letter(letter_id, version).
"""


@dataclass
class ServerHandle:
    """What `build_server` hands back: the server plus the machinery behind it."""

    server: MCPServer
    store: VersionedStore
    engine: SyncEngine
    bus: InMemorySubscriptionBus


def build_server(
    store: VersionedStore,
    engine: SyncEngine,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    sync_on_start: bool = True,
    name: str = "warnsync",
) -> ServerHandle:
    """Wire the store and sync engine into an MCP server."""

    bus = InMemorySubscriptionBus()

    async def publish(events: list[ChangeEvent]) -> None:
        """Commit-and-notify: one manifest event per change, advisory delivery."""
        for event in events:
            await bus.publish(ResourceUpdated(uri=MANIFEST_URI))
            await bus.publish(
                ResourceUpdated(uri=LETTER_URI.format(letter_id=event.letter_id))
            )
            if event.kind in ("NEW", "REMOVED"):
                await bus.publish(ResourcesListChanged())

    async def sync_pass() -> list[ChangeEvent]:
        """Run one ingestion pass off the event loop, then notify subscribers."""
        events = await anyio.to_thread.run_sync(engine.sync_once)
        await publish(events)
        return events

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[dict[str, Any]]:
        async with anyio.create_task_group() as task_group:
            if sync_on_start:
                # Serve a warm corpus rather than an empty one on first query.
                await sync_pass()
            if poll_interval > 0:
                task_group.start_soon(_poll_forever, sync_pass, poll_interval)
            try:
                yield {"store": store, "engine": engine}
            finally:
                task_group.cancel_scope.cancel()

    server = MCPServer(
        name=name,
        title="WarnSync",
        version="0.1.0",
        instructions=INSTRUCTIONS,
        subscriptions=bus,
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------ tools

    @server.tool(
        name="search_letters",
        title="Search enforcement letters",
        description=(
            "Hybrid search over the current corpus. Returns the top-k matching "
            "chunks, each with the provenance needed to cite it."
        ),
    )
    def search_letters(
        query: str,
        k: int = 5,
        recipient: str | None = None,
        office: str | None = None,
        cfr: str | None = None,
        posted_after: str | None = None,
        posted_before: str | None = None,
        include_withdrawn: bool = False,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        """Search the corpus.

        Args:
            query: Natural-language or keyword query.
            k: Number of chunks to return.
            recipient: Substring filter on the letter recipient.
            office: Substring filter on the issuing office or center.
            cfr: Prefix filter on cited provisions, e.g. "21 CFR 211".
            posted_after: ISO date (YYYY-MM-DD) lower bound on posting date.
            posted_before: ISO date (YYYY-MM-DD) upper bound on posting date.
            include_withdrawn: Include letters withdrawn upstream.
            as_of: ISO-8601 timestamp; search the corpus as it stood then.
        """
        hits = store.search(
            query,
            k=k,
            recipient=recipient,
            office=office,
            cfr=cfr,
            posted_after=posted_after,
            posted_before=posted_before,
            include_withdrawn=include_withdrawn,
            as_of=_parse_time(as_of),
        )
        return {
            "query": query,
            "returned": len(hits),
            "as_of": as_of or iso(time.time()),
            "results": [hit.to_dict() for hit in hits],
        }

    @server.tool(
        name="get_letter",
        title="Get a letter",
        description="Fetch one letter's full text and metadata at a specific version.",
    )
    def get_letter(letter_id: str, version: int | None = None) -> dict[str, Any]:
        """Read one letter.

        Args:
            letter_id: The letter identifier.
            version: Version to read; omit for the current version.
        """
        letter = store.get(letter_id, version)
        if letter is None:
            return {
                "error": "not_found",
                "letter_id": letter_id,
                "version": version,
                "known_versions": [v.version for v in store.versions_of(letter_id)],
            }
        row = letter.manifest_row()
        row["text"] = letter.text
        row["source_url"] = letter.source_url
        row["is_current_version"] = (
            store.current(letter_id) is not None
            and store.current(letter_id).version == letter.version
        )
        row["all_versions"] = [
            {"version": v.version, "ingested_at": iso(v.ingested_at), "status": v.status}
            for v in store.versions_of(letter_id)
        ]
        return row

    @server.tool(
        name="violation_trends",
        title="Violation trends",
        description=(
            "Count cited provisions across the corpus in a date window, with the "
            "most-cited recipients per provision."
        ),
    )
    def violation_trends(
        provision: str | None = None,
        window_start: str | None = None,
        window_end: str | None = None,
        top_recipients: int = 5,
    ) -> dict[str, Any]:
        """Aggregate cited provisions.

        Args:
            provision: Prefix filter, e.g. "21 CFR 211" or "21 CFR 820.75".
            window_start: ISO date lower bound on issuance date.
            window_end: ISO date upper bound on issuance date.
            top_recipients: How many recipients to list per provision.
        """
        return store.trends(
            provision,
            window_start=window_start,
            window_end=window_end,
            top_recipients=top_recipients,
        )

    @server.tool(
        name="list_updates",
        title="List corpus updates",
        description=(
            "Enumerate committed corpus changes since a timestamp — the refetch "
            "half of the notify-then-fetch pattern."
        ),
    )
    def list_updates(since: str | None = None, limit: int = 100) -> dict[str, Any]:
        """List changes.

        Args:
            since: ISO-8601 timestamp; omit for the full update log.
            limit: Maximum number of change records to return (most recent last).
        """
        cutoff = _parse_time(since) or 0.0
        events = store.updates_since(cutoff)[-limit:]
        return {
            "since": since,
            "now": iso(time.time()),
            "count": len(events),
            "updates": [event.to_dict() for event in events],
        }

    @server.tool(
        name="corpus_status",
        title="Corpus status",
        description=(
            "Freshness and synchronization state: corpus size, retained versions, "
            "last commit, and last-pass ingestion cost."
        ),
    )
    def corpus_status() -> dict[str, Any]:
        """Report corpus and sync state."""
        return {
            "store": store.stats(),
            "sync": engine.stats.to_dict(),
            "poll_interval_seconds": poll_interval,
            "manifest_resource": MANIFEST_URI,
        }

    # -------------------------------------------------------------- resources

    @server.resource(
        MANIFEST_URI,
        name="corpus_manifest",
        title="WarnSync corpus manifest",
        description=(
            "One row per letter: current version, fingerprint, status and "
            "ingestion time. Subscribe here to be told when the corpus changes."
        ),
        mime_type="application/json",
    )
    def corpus_manifest() -> str:
        return json.dumps(
            {
                "generated_at": iso(time.time()),
                "stats": store.stats(),
                "letters": [entry.to_dict() for entry in store.manifest().values()],
            },
            indent=2,
        )

    @server.resource(
        LETTER_URI,
        name="letter",
        title="Letter (current version)",
        description="Full text and metadata of one letter at its current version.",
        mime_type="application/json",
    )
    def letter_resource(letter_id: str) -> str:
        letter = store.current(letter_id)
        if letter is None:
            return json.dumps({"error": "not_found", "letter_id": letter_id})
        row = letter.manifest_row()
        row["text"] = letter.text
        return json.dumps(row, indent=2)

    return ServerHandle(server=server, store=store, engine=engine, bus=bus)


async def _poll_forever(sync_pass: Any, interval: float) -> None:
    """Poll on a fixed interval; a failing pass only lengthens the current lag."""
    while True:
        await anyio.sleep(interval)
        try:
            await sync_pass()
        except Exception:  # noqa: BLE001 - a bad pass must not kill the server
            continue


def _parse_time(value: str | None) -> float | None:
    """Accept ISO-8601 (with or without trailing Z) or a bare epoch string."""
    if not value:
        return None
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    normalized = text.replace("Z", "+00:00")
    from datetime import datetime, timezone

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()

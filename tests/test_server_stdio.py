"""End-to-end test over a real stdio connection to a spawned server process.

Slower than the unit tests but it exercises the thing that actually matters: a
client sees the corpus change through the protocol, without the tool surface
changing underneath it.
"""

import json
import shutil
import sys
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.subscriptions import ResourceUpdated, listen

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample_corpus"
INCOMING = ROOT / "data" / "incoming"
MANIFEST_URI = "warnsync://manifest"
POLL_INTERVAL = 0.5
TIMEOUT = 20.0

EXPECTED_TOOLS = {
    "search_letters",
    "get_letter",
    "violation_trends",
    "list_updates",
    "corpus_status",
}


def payload(result) -> dict:
    structured = getattr(result, "structured_content", None)
    if structured:
        return structured
    return json.loads(result.content[0].text)


def server_params(corpus: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "warnsync_mcp", "--corpus", str(corpus),
              "--poll-interval", str(POLL_INTERVAL)],
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )


async def connect(session: ClientSession) -> None:
    try:
        await session.discover()
    except Exception:  # noqa: BLE001 - fall back on pre-2026-07-28 servers
        await session.initialize()


@pytest.fixture
def corpus(tmp_path):
    target = tmp_path / "corpus"
    shutil.copytree(SAMPLE, target)
    return target


async def test_tool_surface_and_queries(corpus):
    async with stdio_client(server_params(corpus)) as (read, write):
        async with ClientSession(read, write) as session:
            await connect(session)

            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS

            found = payload(await session.call_tool(
                "search_letters", {"query": "audit trail chromatography", "k": 3}
            ))
            assert found["results"], "expected at least one hit"
            top = found["results"][0]
            assert top["letter_id"] == "WL-2025-0518"
            assert top["provenance"]["cite_as"].startswith("WL-2025-0518 v1")

            letter = payload(await session.call_tool(
                "get_letter", {"letter_id": "WL-2025-0518"}
            ))
            assert letter["version"] == 1
            assert letter["is_current_version"] is True
            assert "21 CFR 211.68(b)" in letter["cfr_citations"]

            missing = payload(await session.call_tool(
                "get_letter", {"letter_id": "WL-9999-0000"}
            ))
            assert missing["error"] == "not_found"

            trends = payload(await session.call_tool(
                "violation_trends", {"provision": "21 CFR 211.192"}
            ))
            assert trends["provisions"][0]["letters_citing"] >= 2

            status = payload(await session.call_tool("corpus_status", {}))
            assert status["store"]["active_letters"] == len(list(corpus.glob("*.json")))


async def test_manifest_resource_is_readable(corpus):
    async with stdio_client(server_params(corpus)) as (read, write):
        async with ClientSession(read, write) as session:
            await connect(session)
            resources = await session.list_resources()
            assert MANIFEST_URI in {str(resource.uri) for resource in resources.resources}

            manifest = json.loads((await session.read_resource(MANIFEST_URI)).contents[0].text)
            assert len(manifest["letters"]) == len(list(corpus.glob("*.json")))
            assert all(entry["status"] == "active" for entry in manifest["letters"])


async def test_a_new_letter_notifies_subscribers_and_becomes_queryable(corpus):
    query = "bioburden excursion deviation reporting"
    async with stdio_client(server_params(corpus)) as (read, write):
        async with ClientSession(read, write) as session:
            await connect(session)

            before = payload(await session.call_tool(
                "search_letters", {"query": query, "k": 5}
            ))
            assert all(hit["letter_id"] != "WL-2026-0224" for hit in before["results"])

            async with listen(
                session,
                resource_subscriptions=[MANIFEST_URI],
                resources_list_changed=True,
            ) as subscription:
                shutil.copy(INCOMING / "WL-2026-0224.json", corpus)

                updated = None
                with anyio.move_on_after(TIMEOUT):
                    async for event in subscription:
                        if isinstance(event, ResourceUpdated) and event.uri == MANIFEST_URI:
                            updated = event
                            break
                assert updated is not None, "no manifest notification arrived"

            after = payload(await session.call_tool(
                "search_letters", {"query": query, "k": 5}
            ))
            assert after["results"][0]["letter_id"] == "WL-2026-0224"

            updates = payload(await session.call_tool("list_updates", {}))
            assert ("WL-2026-0224", "NEW") in {
                (u["letter_id"], u["kind"]) for u in updates["updates"]
            }


async def test_the_tool_surface_survives_a_corpus_change(corpus):
    """R4's point: the data moves, the interface does not."""
    async with stdio_client(server_params(corpus)) as (read, write):
        async with ClientSession(read, write) as session:
            await connect(session)
            before = sorted(tool.name for tool in (await session.list_tools()).tools)

            shutil.copy(INCOMING / "WL-2026-0224.json", corpus)
            (corpus / "WL-2025-0721.json").unlink()
            await anyio.sleep(POLL_INTERVAL * 4)

            after = sorted(tool.name for tool in (await session.list_tools()).tools)
            assert before == after

            status = payload(await session.call_tool("corpus_status", {}))
            assert status["store"]["withdrawn_letters"] == 1

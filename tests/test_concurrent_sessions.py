import asyncio
import base64
import io
import json
import multiprocessing
import os
import sys
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

from study_errorbook.models import Region
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


def _initialize_worker(root, barrier, results):
    barrier.wait(8)
    try:
        ws = Workspace(Path(root), None, TextIn("", ""))
        try:
            results.put({"documents": ws.list_documents()["total"]})
        finally:
            ws.close()
    except Exception as error:
        results.put({"error": f"{type(error).__name__}: {error}"})


def test_simultaneous_first_start_can_initialize_one_database(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    barrier, results = ctx.Barrier(2), ctx.Queue()
    children = [
        ctx.Process(target=_initialize_worker, args=(str(tmp_path / "new"), barrier, results))
        for _ in range(2)
    ]
    for child in children:
        child.start()
    try:
        actual = [results.get(timeout=8) for _ in children]
        assert actual == [{"documents": 0}, {"documents": 0}]
        for child in children:
            child.join(8)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)
        results.close()


def _held_remote_worker(root, inputs, identifier, kind, entered, release):
    """Real process, simulated HTTP held open so peers can inspect its live ownership."""

    async def run():
        async def provider(request):
            entered.set()
            assert await asyncio.to_thread(release.wait, 10)
            if kind == "job":
                return httpx.Response(200, json={"code": 200, "data": {"job_id": "remote-one"}})
            output = io.BytesIO()
            Image.new("RGB", (10, 10), "white").save(output, format="PNG")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "result": {"image": base64.b64encode(output.getvalue()).decode()},
                },
            )

        ws = Workspace(
            Path(root), Path(inputs), TextIn("id", "secret", httpx.MockTransport(provider))
        )
        try:
            if kind == "job":
                await ws.parse_document(identifier, 1)
            else:
                await ws.make_clean_copy(identifier)
        finally:
            ws.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["job", "erase"])
@pytest.mark.parametrize("crash", [False, True])
async def test_live_owner_survives_peer_start_and_crashed_owner_is_not_resubmitted(
    tmp_path, kind, crash
):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "sample.png"
    Image.new("RGB", (100, 100), "white").save(source)
    root = tmp_path / "state"
    initial = Workspace(root, inputs, TextIn("", ""))
    doc = initial.import_document(str(source))
    evidence, _ = initial.read_evidence(doc["id"], Region(page=1, box=[0, 0, 1, 1]))
    initial.close()
    identifier = doc["id"] if kind == "job" else evidence["id"]
    ctx = multiprocessing.get_context("spawn")
    entered, release = ctx.Event(), ctx.Event()
    worker = ctx.Process(
        target=_held_remote_worker,
        args=(str(root), str(inputs), identifier, kind, entered, release),
    )
    worker.start()
    peer = None
    try:
        assert await asyncio.to_thread(entered.wait, 8), "child did not begin its request"
        peer = Workspace(
            root,
            inputs,
            TextIn(
                "id",
                "secret",
                httpx.MockTransport(
                    lambda _: pytest.fail("peer must not make a duplicate remote request")
                ),
            ),
        )
        active = peer._all(kind)[0]
        assert active["status"] == "submitting"  # Starting a peer must not mark it interrupted.
        duplicate = await (
            peer.parse_document(identifier, 1)
            if kind == "job"
            else peer.make_clean_copy(identifier)
        )
        assert duplicate["status"] == "submitting" and duplicate["cached"]
        if kind == "job":
            assert (await peer.get_job(active["id"]))["status"] == "submitting"
        assert peer.list_documents()["total"] == 1  # Reads remain available while remote I/O runs.
        if crash:
            worker.terminate()  # Only the disposable test child, never an application process.
        else:
            release.set()
        await asyncio.to_thread(worker.join, 8)
        assert not worker.is_alive()
        if not crash:
            assert worker.exitcode == 0
        final = await (
            peer.parse_document(identifier, 1)
            if kind == "job"
            else peer.make_clean_copy(identifier)
        )
        assert final["status"] == (
            "submission_unknown" if crash else ("pending" if kind == "job" else "completed")
        )
        assert final["cached"]
    finally:
        if worker.is_alive():
            release.set()
            worker.terminate()
            worker.join(5)
        if peer:
            peer.close()


async def test_two_real_stdio_sessions_share_records_without_lost_updates(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "sample.png"
    Image.new("RGB", (100, 100), "white").save(source)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "study_errorbook.server"],
        env={
            **os.environ,
            "STUDY_DATA_DIR": str(tmp_path / "state"),
            "STUDY_INPUT_ROOT": str(inputs),
            "TEXTIN_APP_ID": "",
            "TEXTIN_SECRET_CODE": "",
        },
    )
    async with stdio_client(params) as (r1, w1):
        async with ClientSession(r1, w1) as first:
            await first.initialize()
            async with stdio_client(params) as (r2, w2):
                async with ClientSession(r2, w2) as second:
                    await second.initialize()
                    doc = (
                        await first.call_tool("import_document", {"path": str(source)})
                    ).structuredContent
                    listing = await second.call_tool("list_documents", {})
                    assert listing.structuredContent["total"] == 1
                    evidence = await first.call_tool(
                        "read_evidence",
                        {"document_id": doc["id"], "region": {"page": 1, "box": [0, 0, 1, 1]}},
                    )
                    ev = json.loads(evidence.content[0].text)
                    saved = await first.call_tool(
                        "save_study_record",
                        {
                            "record": {
                                "document_id": doc["id"],
                                "question_label": "synthetic 1",
                                "stem": "synthetic question",
                                "evidence_ids": [ev["id"]],
                            }
                        },
                    )
                    assert not saved.isError
                    record = saved.structuredContent
                    decisions = await asyncio.gather(
                        *[
                            session.call_tool(
                                "review_study_record",
                                {
                                    "record_id": record["id"],
                                    "expected_revision": 1,
                                    "decision": decision,
                                    "user_note": "Explicit synthetic test decision",
                                },
                            )
                            for session, decision in [(first, "include"), (second, "exclude")]
                        ]
                    )
                    assert sum(bool(result.isError) for result in decisions) == 1
                    assert (
                        "revision_conflict"
                        in next(result for result in decisions if result.isError).content[0].text
                    )
                    for session in (first, second):
                        latest = await session.call_tool(
                            "read_study_record", {"record_id": record["id"]}
                        )
                        assert latest.structuredContent["revision"] == 2
                        # A failed transaction must release its lock and allow another valid write.
                    accepted = await second.call_tool(
                        "review_study_record",
                        {
                            "record_id": record["id"],
                            "expected_revision": 2,
                            "decision": "include",
                            "user_note": "Later synthetic decision",
                        },
                    )
                    assert not accepted.isError and accepted.structuredContent["revision"] == 3

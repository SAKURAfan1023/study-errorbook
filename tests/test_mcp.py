import json
import os
import sys
from importlib.metadata import version
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

from study_errorbook.service import Workspace
from study_errorbook.textin import PARSE_CONFIG, TextIn


async def test_real_stdio_discovery_image_errors_and_persistence(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    sample = inputs / "sample.png"
    Image.new("RGB", (120, 120), "white").save(sample)
    env = {
        **os.environ,
        "STUDY_DATA_DIR": str(tmp_path / "data"),
        "STUDY_INPUT_ROOT": str(inputs),
        "TEXTIN_APP_ID": "",
        "TEXTIN_SECRET_CODE": "",
    }
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "study_errorbook.server"], env=env
    )
    # Seed a labelled cached OCR fixture; protocol calls below still use a real subprocess.
    seeded = Workspace(tmp_path / "data", inputs, TextIn("", ""))
    seeded_document = seeded.import_document(str(sample))
    fixture = seeded.root / seeded_document["id"] / "synthetic-ocr.json"
    fixture.write_text(
        json.dumps(
            {
                "elements": [
                    {
                        "text": "合成文字",
                        "page_number": 1,
                        "coordinates": [0.1, 0.1, 0.9, 0.1, 0.9, 0.9, 0.1, 0.9],
                        "char_details": [{"text": "字", "recognition": {"confidence": 0.7}}],
                    }
                ],
                "pages": [{"page_number": 1, "angle": 0, "status": "Success"}],
            }
        )
    )
    seeded._put(
        "job",
        "synthetic-ocr",
        {
            "id": "synthetic-ocr",
            "document_id": seeded_document["id"],
            "config": PARSE_CONFIG,
            "status": "completed",
            "result_path": str(fixture),
        },
    )
    seeded._put(
        "job", "paused-fixture",
        {"id": "paused-fixture", "document_id": seeded_document["id"],
         "status": "pending", "provider_job_id": "original-fixture",
         "poll_error": {"code": "textin_40306", "message": "fixture"}},
    )
    seeded.close()
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            assert {"parse_document", "read_evidence", "save_study_record", "export_errorbook"} <= {
                t.name for t in listed.tools
            }
            assert "read_element" in {t.name for t in listed.tools}
            assert "retry_parse_document" in {t.name for t in listed.tools}
            assert "resume_job_observation" in {t.name for t in listed.tools}
            documents = await session.call_tool('list_documents', {})
            jobs = await session.call_tool('list_parse_jobs', {'document_id': documents.structuredContent['items'][0]['id']})
            assert not jobs.isError and jobs.structuredContent['total'] == 2
            cached = next(item for item in jobs.structuredContent['items'] if item['status'] == 'completed')
            recovered = await session.call_tool('read_document', {'job_id': cached['id']})
            assert not recovered.isError and recovered.structuredContent['elements'][0]['text'] == '合成文字'
            paused = await session.call_tool("get_job", {"job_id": "paused-fixture"})
            assert paused.structuredContent["attempt_number"] == 1
            assert paused.structuredContent["observation_revision"] == 0
            refused_resume = await session.call_tool(
                "resume_job_observation",
                {"job_id": "paused-fixture", "expected_attempt": 1,
                 "expected_observation_revision": 0, "user_note": "测试缺凭据状态保持"},
            )
            assert refused_resume.isError and "credentials_missing" in refused_resume.content[0].text
            observed = await session.call_tool(
                "get_job", {"job_id": "synthetic-ocr", "include_attempt_history": True}
            )
            assert observed.structuredContent["attempt_history"]["total"] == 0
            rejected = await session.call_tool(
                "retry_parse_document",
                {"job_id": "synthetic-ocr", "expected_attempt": 1, "max_paid_pages": 1,
                 "acknowledge_resubmission": True, "user_note": "测试拒绝已完成任务"},
            )
            assert rejected.isError and "retry_state" in rejected.content[0].text
            cropped = await session.call_tool(
                "read_element_evidence", {"job_id": "synthetic-ocr", "element_index": 0}
            )
            assert not cropped.isError
            assert {part.type for part in cropped.content} == {"text", "image"}
            assert json.loads(cropped.content[0].text)["ocr_reference"]["element_index"] == 0
            details = await session.call_tool(
                "read_element",
                {"job_id": "synthetic-ocr", "element_index": 0, "section": "char_details"},
            )
            assert not details.isError and details.structuredContent["complete"]
            assert (
                json.loads(details.structuredContent["content"])[0]["recognition"]["confidence"]
                == 0.7
            )
            result = await session.call_tool("workspace_status", {})
            assert not result.isError and not result.structuredContent["textin_configured"]
            assert result.structuredContent["runtime_version"] == version("study-errorbook")
            imported = await session.call_tool("import_document", {"path": str(sample)})
            assert not imported.isError
            doc = imported.structuredContent
            image = await session.call_tool(
                "read_evidence",
                {"document_id": doc["id"], "region": {"page": 1, "box": [0, 0, 1, 1]}},
            )
            assert not image.isError
            assert {part.type for part in image.content} == {"text", "image"}
            evidence = json.loads(image.content[0].text)
            saved = await session.call_tool(
                "save_study_record",
                {
                    "record": {
                        "document_id": doc["id"],
                        "question_label": "synthetic",
                        "stem": "2(x+3)",
                        "evidence_ids": [evidence["id"]],
                        "exercises": [{"prompt": "合成说明题", "answer": "合成参考说明",
                                       "targets_error": "仅测试协议与复核字段"}],
                    }
                },
            )
            assert not saved.isError
            first_review = await session.call_tool(
                "review_study_record",
                {"record_id": saved.structuredContent["id"], "expected_revision": 1,
                 "decision": "include", "user_note": "协议夹具：只纳入原题"},
            )
            assert first_review.structuredContent["revision"] == 2
            blocked = await session.call_tool(
                "export_errorbook", {"record_ids": [saved.structuredContent["id"]]},
            )
            assert blocked.isError and "exercise_not_ready" in blocked.content[0].text
            inspected = await session.call_tool(
                "read_study_record", {"record_id": saved.structuredContent["id"]},
            )
            assert inspected.structuredContent["exercise_validation"][0]["status"] == "needs_review"
            confirmed = await session.call_tool(
                "review_study_record",
                {"record_id": saved.structuredContent["id"], "expected_revision": 2,
                 "decision": "include", "user_note": "协议夹具：已核对练习0题干与答案",
                 "approved_exercise_indices": [0]},
            )
            assert confirmed.structuredContent["revision"] == 3
            released = await session.call_tool(
                "export_errorbook", {"record_ids": [saved.structuredContent["id"]]},
            )
            assert not released.isError and not released.structuredContent["contains_drafts"]
            failed = await session.call_tool(
                "parse_document", {"document_id": doc["id"], "max_paid_pages": 1}
            )
            assert failed.isError
            assert "credentials_missing" in failed.content[0].text
    # A fresh process must be able to retrieve the previous conversation's record.
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("query_errorbook", {"search": "synthetic"})
            assert result.structuredContent["total"] == 1
            exported = await session.call_tool(
                "export_errorbook",
                {"record_ids": [saved.structuredContent["id"]], "include_drafts": True},
            )
            assert not exported.isError
            assert all(Path(path).is_file() for path in exported.structuredContent["files"])

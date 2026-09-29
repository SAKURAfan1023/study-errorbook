"""Exercise an installed MCP runtime with temporary synthetic data; never call OCR."""

import asyncio
import json
import os
import sys
import tempfile
import zipfile
from importlib.metadata import version
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image


async def main(config_path):
    plugin_root = Path(config_path).resolve().parent
    if not (plugin_root / "skills").is_dir():
        plugin_root = plugin_root / "plugin"
    skill_root = plugin_root / "skills/study-errorbook"
    entry = (skill_root / "SKILL.md").read_text()
    for name in ("records.md", "intake.md", "practice.md", "deletion.md", "exports.md"):
        assert f"references/{name}" in entry
        assert (skill_root / "references" / name).read_text().strip()
    config = json.loads(Path(config_path).read_text())["mcpServers"]["study-errorbook"]
    with tempfile.TemporaryDirectory(prefix="study-release-") as temporary:
        root = Path(temporary)
        sample = root / "synthetic.png"
        Image.new("RGB", (120, 120), "white").save(sample)
        config["env"] = {**config.get("env", {}), "STUDY_INPUT_ROOT": str(root),
                         "STUDY_DATA_DIR": str(root / "data"),
                         "STUDY_CONFIG_FILE": str(root / "absent.env"),
                         "TEXTIN_APP_ID": "", "TEXTIN_SECRET_CODE": ""}
        params = StdioServerParameters(**config, cwd=str(root))
        async with asyncio.timeout(30):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    assert len(listed.tools) == 32
                    schemas = {tool.name: tool.inputSchema for tool in listed.tools}
                    check_output = next(tool.outputSchema for tool in listed.tools if tool.name == 'check_exercise')
                    assert set(check_output['properties']['status']['enum']) == {'equivalent', 'not_equivalent', 'needs_review'}
                    assert set(check_output['properties']['kind']['enum']) == {'polynomial_identity', 'linear_equation'}
                    assert 'reason' not in check_output['required']
                    assert check_output['properties']['status']['description']
                    assert {"review_status", "subject", "document_id", "knowledge_point", "agent_verdict"} <= schemas["query_errorbook"]["properties"].keys()
                    assert "revision" in schemas["read_study_record"]["properties"]
                    assert "approved_clean_copies" in schemas["review_study_record"]["properties"]
                    assert "clean_evidence_ids" in json.dumps(schemas["save_study_record"])
                    assert "withdraw_user_opinion" in json.dumps(schemas["review_practice_attempt"])
                    check_mode = schemas['check_exercise']['properties']['kind']
                    assert check_mode['default'] == 'polynomial_identity'
                    assert set(check_mode['enum']) == {'polynomial_identity', 'linear_equation'}
                    for equation, answer, outcome in (
                        ('2*(x+3)=10', '2', 'equivalent'),
                        ('2*(x+3)=10', '7/2', 'not_equivalent'),
                        ('3*x+1=0', '-1/3', 'equivalent'),
                        ('x=x', '3', 'needs_review'),
                        ('x^2=4', '2', 'needs_review'),
                        ('(x-1)/(x-1)=1', '2', 'needs_review'),
                    ):
                        solved = await session.call_tool('check_exercise', {
                            'expression': equation, 'proposed_answer': answer, 'kind': 'linear_equation'})
                        assert not solved.isError and solved.structuredContent['status'] == outcome
                        assert solved.structuredContent['kind'] == 'linear_equation'
                        assert json.loads(solved.content[0].text) == solved.structuredContent
                        assert ('reason' in solved.structuredContent) == (outcome == 'needs_review')
                    invalid_mode = await session.call_tool('check_exercise', {
                        'expression': 'x=2', 'proposed_answer': '2', 'kind': 'guess'})
                    assert invalid_mode.isError
                    status = await session.call_tool("workspace_status", {})
                    assert not status.isError and not status.structuredContent["textin_configured"]
                    runtime_version = status.structuredContent.get("runtime_version")
                    expected_version = version("study-errorbook")
                    assert runtime_version == expected_version, f"MCP runtime {runtime_version!r} differs from verifier installation {expected_version!r}"
                    fifo = root / 'not-a-worksheet.png'
                    os.mkfifo(fifo)
                    candidates = await session.call_tool('list_input_files', {'limit': 1})
                    assert not candidates.isError
                    assert candidates.structuredContent['total'] == 1
                    assert candidates.structuredContent['items'][0]['path'] == str(sample.resolve())
                    assert candidates.structuredContent['items'][0]['size_within_limit']
                    assert candidates.structuredContent['next_offset'] is None
                    invalid_listing = await session.call_tool('list_input_files', {'offset': True})
                    assert invalid_listing.isError
                    rejected_import = await session.call_tool('import_document', {'path': str(fifo)})
                    assert rejected_import.isError and 'file_unavailable' in rejected_import.content[0].text
                    empty_library = await session.call_tool('list_documents', {})
                    assert not empty_library.isError and empty_library.structuredContent['total'] == 0
                    for expression, answer, outcome in (
                        ('0.10000000000000001', '0.1', 'needs_review'),
                        ('1e-400', '0', 'needs_review'),
                        ('0.1+0.2', '0.3', 'equivalent'),
                        ('('*25+'1000000'+')^2'*25, '0', 'needs_review'),
                    ):
                        numeric_check = await session.call_tool('check_exercise', {'expression': expression, 'proposed_answer': answer})
                        assert not numeric_check.isError and numeric_check.structuredContent['status'] == outcome
                    assert '4096' in numeric_check.structuredContent['reason']
                    document = await session.call_tool("import_document", {"path": candidates.structuredContent['items'][0]['path']})
                    assert not document.isError
                    saved_jobs = await session.call_tool('list_parse_jobs', {'document_id': document.structuredContent['id']})
                    assert not saved_jobs.isError and saved_jobs.structuredContent['total'] == 0
                    assert saved_jobs.structuredContent['next_offset'] is None
                    pdf_sample = root / 'synthetic.pdf'
                    Image.new('RGB', (120, 120), 'white').save(pdf_sample, format='PDF')
                    pdf_document = await session.call_tool('import_document', {'path': str(pdf_sample)})
                    assert not pdf_document.isError
                    for imported, input_path in ((document, sample), (pdf_document, pdf_sample)):
                        source_path = Path(imported.structuredContent['source'])
                        original_bytes = source_path.read_bytes()
                        source_path.unlink()
                        source_path.symlink_to(input_path)
                        for name, arguments in (
                            ('read_evidence', {'document_id': imported.structuredContent['id'],
                                               'region': {'page': 1, 'box': [0, 0, 1, 1]}}),
                            ('redact_document', {'document_id': imported.structuredContent['id'],
                                                 'regions': [{'page': 1, 'box': [0, 0, .5, .5]}]}),
                        ):
                            blocked = await session.call_tool(name, arguments)
                            assert blocked.isError and 'document_source_unavailable' in blocked.content[0].text
                            assert all(part.type != 'image' for part in blocked.content)
                        source_path.unlink()
                        source_path.write_bytes(original_bytes)
                        restored = await session.call_tool('read_evidence', {
                            'document_id': imported.structuredContent['id'], 'region': {'page': 1, 'box': [0, 0, 1, 1]}})
                        assert not restored.isError and any(part.type == 'image' for part in restored.content)
                    redacted = await session.call_tool("redact_document", {
                        "document_id": document.structuredContent["id"],
                        "regions": [{"page": 1, "box": [0, 0, .5, .5]}],
                    })
                    assert not redacted.isError
                    doc_id = redacted.structuredContent["id"]
                    assert doc_id != document.structuredContent["id"]
                    with Image.open(redacted.structuredContent["source"]) as preview:
                        assert preview.getpixel((10, 10)) == (0, 0, 0)
                        assert preview.getpixel((100, 100)) == (255, 255, 255)
                    redacted_path = Path(redacted.structuredContent['source'])
                    redacted_bytes = redacted_path.read_bytes()
                    Image.new('RGB', (120, 120), 'red').save(redacted_path)
                    changed_redaction = await session.call_tool('read_evidence', {
                        'document_id': doc_id, 'region': {'page': 1, 'box': [0, 0, 1, 1]}})
                    assert changed_redaction.isError and 'redacted_copy_changed' in changed_redaction.content[0].text
                    redacted_path.write_bytes(redacted_bytes)
                    crop_args = {"document_id": doc_id, "region": {"page": 1, "box": [.25, .25, .75, .75]}}
                    crop = await session.call_tool("crop_document", crop_args)
                    assert not crop.isError
                    crop_doc = crop.structuredContent
                    assert crop_doc["crop"]["source_document_id"] == doc_id
                    assert crop_doc["crop"]["pixel_box"] == [30, 30, 90, 90]
                    assert crop_doc["crop"]["output_size"] == [60, 60]
                    with Image.open(crop_doc["source"]) as preview:
                        assert preview.getpixel((0, 0)) == (0, 0, 0)
                        assert preview.getpixel((59, 59)) == (255, 255, 255)
                    crop_image = await session.call_tool("read_evidence", {
                        "document_id": crop_doc["id"], "region": {"page": 1, "box": [0, 0, 1, 1]}})
                    assert not crop_image.isError and any(p.type == "image" for p in crop_image.content)
                    repeated_crop = await session.call_tool("crop_document", crop_args)
                    assert not repeated_crop.isError and repeated_crop.structuredContent["cached"]
                    image = await session.call_tool("read_evidence", {
                        "document_id": crop_doc["id"], "region": {"page": 1, "box": [0, 0, 1, 1]},
                    })
                    assert not image.isError and {p.type for p in image.content} == {"text", "image"}
                    evidence = json.loads(next(p.text for p in image.content if p.type == "text"))
                    saved = await session.call_tool("save_study_record", {"record": {
                        "document_id": crop_doc["id"], "question_label": "synthetic-SECRET-LABEL-answer",
                        "subject": "SECRET-SUBJECT-hint",
                        "stem": r"Synthetic fixture: \(2(x+3)\)", "evidence_ids": [evidence["id"]],
                    }})
                    assert not saved.isError
                    record_id = saved.structuredContent["id"]
                    evidence_path = Path(evidence['path'])
                    evidence_bytes = evidence_path.read_bytes()
                    for replacement in ('link', 'fifo'):
                        evidence_path.unlink()
                        if replacement == 'link':
                            evidence_path.symlink_to(sample)
                        else:
                            os.mkfifo(evidence_path)
                        invalid_evidence = await session.call_tool('export_errorbook', {
                            'record_ids': [record_id], 'include_drafts': True})
                        assert invalid_evidence.isError and 'evidence_unavailable' in invalid_evidence.content[0].text
                        evidence_path.unlink()
                        evidence_path.write_bytes(evidence_bytes)
                    filtered = await session.call_tool("query_errorbook", {"search": "SYNTHETIC",
                        "document_id": crop_doc["id"], "review_status": "needs_review", "limit": 1})
                    assert not filtered.isError and filtered.structuredContent["total"] == 1
                    assert filtered.structuredContent["items"][0]["id"] == record_id
                    assert filtered.structuredContent["next_offset"] is None
                    uncertain = await session.call_tool("query_errorbook", {
                        "agent_verdict": "uncertain", "review_status": "needs_review", "document_id": crop_doc["id"]})
                    assert not uncertain.isError and uncertain.structuredContent["total"] == 1
                    assert uncertain.structuredContent["items"][0]["id"] == record_id
                    assert uncertain.structuredContent["filters"]["agent_verdict"] == "uncertain"
                    for filters in ({"agent_verdict": "wrong"}, {"agent_verdict": "uncertain", "review_status": "uncertain"}):
                        empty = await session.call_tool("query_errorbook", filters)
                        assert not empty.isError and empty.structuredContent["total"] == 0
                    invalid_judgment = await session.call_tool("query_errorbook", {"agent_verdict": "include"})
                    assert invalid_judgment.isError
                    false_match = await session.call_tool("query_errorbook", {"search": "document_id"})
                    assert not false_match.isError and false_match.structuredContent["total"] == 0
                    excluded = await session.call_tool("query_errorbook", {"review_status": "exclude"})
                    assert not excluded.isError and excluded.structuredContent["total"] == 0
                    attempt_args = {"record_id": record_id, "expected_revision": 1,
                                    "practice_kind": "original", "response": "synthetic response",
                                    "assistance": "unknown", "attempt_key": "release-verification"}
                    attempt = await session.call_tool("record_practice", attempt_args)
                    assert not attempt.isError
                    retry = await session.call_tool("record_practice", attempt_args)
                    assert not retry.isError and retry.structuredContent["cached"]
                    assert attempt.structuredContent["id"] == retry.structuredContent["id"]
                    assessed = await session.call_tool("review_practice_attempt", {
                        "attempt_id": attempt.structuredContent["id"], "expected_feedback_revision": 0,
                        "feedback": {"agent_verdict": "uncertain", "feedback": "Synthetic protocol fixture, not learner assessment.",
                                     "next_step": "Check a real authorized response before giving learning feedback."},
                    })
                    assert not assessed.isError and assessed.structuredContent["user_verdict"] == "unreported"
                    base_feedback = {"agent_verdict": "uncertain", "feedback": "Synthetic protocol fixture.",
                                     "next_step": "Check the authorized original response."}
                    for revision, opinion in enumerate([
                        {"user_verdict": "correct", "user_note": "Synthetic user disagreement."},
                        {},
                        {"withdraw_user_opinion": True, "user_note": "Synthetic explicit withdrawal."},
                        {},
                    ], start=1):
                        revised = await session.call_tool("review_practice_attempt", {
                            "attempt_id": attempt.structuredContent["id"], "expected_feedback_revision": revision,
                            "feedback": {**base_feedback, **opinion},
                        })
                        assert not revised.isError
                        if revision == 2:
                            assert revised.structuredContent["user_verdict"] == "correct"
                            assert revised.structuredContent["user_opinion_source_revision"] == 2
                            assert not revised.structuredContent["user_opinion_updated"]
                    exported = await session.call_tool("export_errorbook", {
                        "record_ids": [record_id], "include_drafts": True,
                    })
                    assert not exported.isError
                    files = [Path(p) for p in exported.structuredContent["files"]]
                    assert len(files) == 3 and all(p.is_file() for p in files)
                    material_label = '资料编号：' + exported.structuredContent['export_id']
                    assert all(material_label in p.read_text() for p in files)
                    practice_bundle = Path(exported.structuredContent['practice_bundle_path'])
                    with zipfile.ZipFile(practice_bundle) as archive:
                        assert archive.testzip() is None
                        assert {'practice.html', 'README.txt', 'assets/export.js'} <= set(archive.namelist())
                        assert not {'errorbook.html', 'answers.html', 'manifest.json', evidence['id'] + '.png'} & set(archive.namelist())
                        assert len(archive.namelist()) == 69
                        assert material_label in archive.read('README.txt').decode('utf-8')
                        assert material_label in archive.read('practice.html').decode('utf-8')
                    manifest = json.loads(Path(exported.structuredContent["manifest_path"]).read_text())
                    lineage = manifest["records"][0]["document_provenance"]
                    assert lineage["status"] == "complete_saved_chain"
                    assert [entry["kind"] for entry in lineage["chain"]] == ["crop", "redaction", "import"]
                    assert manifest["records"][0]["evidence"][0]["document_id"] == crop_doc["id"]
                    assert "裁图页 1" in files[0].read_text()
                    assets = files[0].parent / "assets"
                    assert len([p for p in assets.rglob("*") if p.is_file()]) == 67
                    # Failure recovery through the installed MCP transport, using only
                    # this verifier's temporary synthetic evidence.
                    export_root = files[0].parent.parent
                    previous_exports = set(export_root.iterdir())
                    evidence_path = Path(evidence["path"])
                    evidence_bytes = evidence_path.read_bytes()
                    evidence_path.unlink()
                    try:
                        failed_export = await session.call_tool("export_errorbook", {
                            "record_ids": [record_id], "include_drafts": True,
                        })
                        assert failed_export.isError
                    finally:
                        evidence_path.write_bytes(evidence_bytes)
                    assert set(export_root.iterdir()) == previous_exports
                    preview_after_failure = await session.call_tool("preview_document_deletion", {"document_id": doc_id})
                    assert not preview_after_failure.isError
                    assert preview_after_failure.structuredContent["inventory_complete"]
                    recovered_export = await session.call_tool("export_errorbook", {
                        "record_ids": [record_id], "include_drafts": True,
                    })
                    assert not recovered_export.isError
                    assert Path(recovered_export.structuredContent["manifest_path"]).is_file()
                    included = await session.call_tool("review_study_record", {
                        "record_id": record_id, "expected_revision": 1, "decision": "include",
                        "user_note": "Synthetic installer fixture, no real user opinion.",
                    })
                    assert not included.isError
                    historical = await session.call_tool("read_study_record", {"record_id": record_id, "revision": 1})
                    assert not historical.isError
                    old = historical.structuredContent
                    assert old["revision"] == 1 and old["current_revision"] == 2
                    assert old["revision_view"] == "saved_snapshot" and old["review_status"] == "needs_review"
                    assert not {"clean_copy_validation", "recent_practice_attempts", "review_plan"} & old.keys()
                    missing_revision = await session.call_tool("read_study_record", {"record_id": record_id, "revision": 99})
                    assert missing_revision.isError and "revision_not_found" in missing_revision.content[0].text
                    invalid_revision = await session.call_tool("read_study_record", {"record_id": record_id, "revision": True})
                    assert invalid_revision.isError
                    prompt_result = await session.call_tool("read_practice_prompt", {
                        "record_id": record_id, "expected_revision": 2, "practice_kind": "original", "presentation": "text",
                    })
                    assert not prompt_result.isError
                    prompt = json.loads(prompt_result.content[0].text)
                    assert prompt["prompt"] == r"Synthetic fixture: \(2(x+3)\)"
                    assert prompt["source_revision"] == 2 and prompt["images"] == []
                    assert "student_answer" not in prompt and "analysis" not in prompt
                    assert 'question_label' not in prompt and 'subject' not in prompt
                    assert 'SECRET' not in json.dumps(prompt) and prompt['display_label'] == '原题重练'
                    before_plan = await session.call_tool("record_practice", {
                        **attempt_args, "expected_revision": 2, "attempt_key": "before-plan",
                    })
                    assert not before_plan.isError
                    scheduled = await session.call_tool("update_review_plan", {
                        "record_id": record_id, "expected_record_revision": 2, "expected_plan_revision": 0,
                        "change": {"action": "schedule", "due_at": "2030-01-02T09:00:00+08:00",
                                   "user_note": "Synthetic requested date."},
                    })
                    assert not scheduled.isError
                    premature = await session.call_tool("update_review_plan", {
                        "record_id": record_id, "expected_record_revision": 2, "expected_plan_revision": 1,
                        "change": {"action": "complete", "attempt_id": before_plan.structuredContent["id"],
                                   "user_note": "Synthetic invalid old-attempt completion."},
                    })
                    assert premature.isError and "attempt_plan_mismatch" in premature.content[0].text
                    practiced = await session.call_tool("record_practice", {
                        **attempt_args, "expected_revision": 2, "attempt_key": "planned-review",
                    })
                    assert not practiced.isError
                    completed = await session.call_tool("update_review_plan", {
                        "record_id": record_id, "expected_record_revision": 2, "expected_plan_revision": 1,
                        "change": {"action": "complete", "attempt_id": practiced.structuredContent["id"],
                                   "user_note": "Synthetic review completion."},
                    })
                    assert not completed.isError and completed.structuredContent["status"] == "completed"
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    recovered = await session.call_tool("read_study_record", {"record_id": record_id})
                    assert not recovered.isError
                    history = await session.call_tool("read_practice_history", {"record_id": record_id, "include_details": True})
                    assert not history.isError and history.structuredContent["total"] == 3
                    assert history.structuredContent["items"][0]["source_snapshot"]["prompt"] == r"Synthetic fixture: \(2(x+3)\)"
                    feedback = await session.call_tool("read_practice_feedback", {"attempt_id": attempt.structuredContent["id"]})
                    assert not feedback.isError and feedback.structuredContent["current_feedback_revision"] == 5
                    assert feedback.structuredContent["items"][0]["agent_verdict"] == "uncertain"
                    versions = feedback.structuredContent["items"]
                    assert [item["user_opinion_source_revision"] for item in versions] == [4, 4, 2, 2, None]
                    assert versions[0]["user_verdict"] == "unreported"
                    assert versions[0]["user_note"] == "Synthetic explicit withdrawal."
                    assert not versions[0]["user_opinion_updated"] and versions[1]["withdraw_user_opinion"]
                    plan = await session.call_tool("read_review_plan", {"record_id": record_id})
                    assert not plan.isError and plan.structuredContent["current_plan_revision"] == 2
                    assert [item["status"] for item in plan.structuredContent["items"]] == ["completed", "scheduled"]
                    queue = await session.call_tool("read_review_queue", {"status": "completed"})
                    assert not queue.isError and queue.structuredContent["total"] == 1
                    assert 'SECRET' not in json.dumps(queue.structuredContent)
                    assert queue.structuredContent['items'][0]['display_label'] == '原题重练'
                    labelled_queue = await session.call_tool("read_review_queue", {"status": "completed", "include_labels": True})
                    assert not labelled_queue.isError
                    assert labelled_queue.structuredContent['items'][0]['question_label'] == 'synthetic-SECRET-LABEL-answer'
                    assert labelled_queue.structuredContent['items'][0]['subject'] == 'SECRET-SUBJECT-hint'
                    preview = await session.call_tool("preview_document_deletion", {"document_id": doc_id})
                    assert not preview.isError
                    inventory = preview.structuredContent
                    assert inventory["inventory_complete"] and not inventory["deleted"]
                    assert inventory["counts"]["document"] == 2
                    assert inventory["counts"]["record"] == 1 and inventory["counts"]["review_plan"] == 2
                    assert inventory["counts"]["practice_feedback"] == 5 and inventory["files_total"] > 67
                    preserved = await session.call_tool("read_study_record", {"record_id": record_id})
                    assert not preserved.isError
                    removed = await session.call_tool("delete_document", {"document_id": doc_id,
                        "inventory_digest": inventory["inventory_digest"], "acknowledge_delete": True})
                    assert not removed.isError and removed.structuredContent["deleted"]
                    assert not Path(crop_doc["source"]).exists()
                    missing = await session.call_tool("read_study_record", {"record_id": record_id})
                    assert missing.isError
                    repeated = await session.call_tool("delete_document", {"document_id": doc_id,
                        "inventory_digest": inventory["inventory_digest"], "acknowledge_delete": True})
                    assert not repeated.isError and repeated.structuredContent["cached"]
                    equation_check = await session.call_tool('check_exercise', {
                        'expression': '2*(x+3)=10', 'proposed_answer': '2', 'kind': 'linear_equation'})
                    assert not equation_check.isError
                    equation_image = await session.call_tool('read_evidence', {
                        'document_id': document.structuredContent['id'], 'region': {'page': 1, 'box': [0, 0, 1, 1]}})
                    assert not equation_image.isError
                    equation_evidence = json.loads(next(p.text for p in equation_image.content if p.type == 'text'))
                    equation_record = await session.call_tool('save_study_record', {'record': {
                        'document_id': document.structuredContent['id'], 'question_label': 'Synthetic equation fixture',
                        'stem': 'Synthetic source for equation practice',
                        'evidence_ids': [equation_evidence['id']], 'exercises': [{
                            'prompt': 'Solve 2*(x+3)=10', 'answer': '2', 'targets_error': 'Synthetic distribution fixture',
                            'check_id': equation_check.structuredContent['id']}],
                    }})
                    assert not equation_record.isError, equation_record.content
                    equation_id = equation_record.structuredContent['id']
                    equation_review = await session.call_tool('review_study_record', {
                        'record_id': equation_id, 'expected_revision': 1, 'decision': 'include',
                        'user_note': 'Synthetic review fixture; not a real user decision.'})
                    assert not equation_review.isError
                    equation_current = await session.call_tool('read_study_record', {'record_id': equation_id})
                    assert not equation_current.isError
                    assert equation_current.structuredContent['exercise_validation'][0]['status'] == 'equation_verified'
                    equation_prompt = await session.call_tool('read_practice_prompt', {
                        'record_id': equation_id, 'expected_revision': 2, 'exercise_index': 0})
                    assert not equation_prompt.isError
                    prompt_data = json.loads(next(p.text for p in equation_prompt.content if p.type == 'text'))
                    assert prompt_data['prompt'] == 'Solve 2*(x+3)=10' and 'answer' not in prompt_data
                    equation_export = await session.call_tool('export_errorbook', {'record_ids': [equation_id]})
                    assert not equation_export.isError
                    answer_page = (Path(equation_export.structuredContent['folder']) / 'answers.html').read_text()
                    assert '一元一次方程的唯一解验算通过' in answer_page
        print(json.dumps({"tools": 32, "linear_equation_checks": True, "parse_job_discovery": True, "input_discovery_without_import": True, "runtime_version": runtime_version, "export_failure_recovery": True, "saved_record_revision_reads": True, "synthetic_import": True, "filtered_content_search": True, "practice_prompt_without_answer_fields": True, "image_content": True,
                          "deletion_inventory_without_mutation": True, "local_cascade_deletion_and_receipt": True,
                          "local_redacted_copy": True,
                          "bounded_original_png_pdf_rendering_and_restore": True,
                          "changed_redaction_evidence_refused": True,
                          "local_crop_pixels_lineage_cache_and_cascade": True,
                          "exported_crop_lineage": True,
                          "export_files": 3, "export_assets": 67, "restart_persistence": True,
                          "original_practice_history": True, "attempt_retry_deduplication": True,
                          "practice_feedback_persistence": True,
                          "user_opinion_preservation_and_withdrawal": True,
                          "review_plan_completion_and_restart": True, "old_attempt_cannot_complete_new_plan": True,
                          "ocr_calls": 0}))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))

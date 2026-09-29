import io
import json

import pytest
from PIL import Image, ImageDraw

from study_errorbook.models import DomainError, Region
from study_errorbook.service import Workspace
from study_errorbook.textin import PARSE_CONFIG, TextIn


@pytest.fixture
def evidence_case(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    ws = Workspace(tmp_path / "state", inputs, TextIn("", ""))
    try:
        yield ws, inputs
    finally:
        ws.close()


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_original_coordinates_and_upright_view_agree_for_each_rotation(evidence_case, angle):
    ws, inputs = evidence_case
    upright = Image.new("RGB", (200, 100), "white")
    draw = ImageDraw.Draw(upright)
    draw.rectangle((40, 20, 79, 59), fill="red")
    draw.rectangle((80, 20, 119, 59), fill="blue")
    original = upright.rotate(-angle, expand=True)
    path = inputs / "rotated.png"
    original.save(path)
    document = ws.import_document(str(path))
    fixture = ws.root / document["id"] / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "elements": [
                    {
                        "page_number": 1,
                        "text": "colored fixture",
                        "coordinates": [0.2, 0.2, 0.6, 0.2, 0.6, 0.6, 0.2, 0.6],
                    }
                ],
                "pages": [{"page_number": 1, "angle": angle, "status": "Success"}],
            }
        )
    )
    ws._put(
        "job",
        "fixture",
        {
            "id": "fixture",
            "document_id": document["id"],
            "status": "completed",
            "result_path": str(fixture),
            "config": PARSE_CONFIG,
        },
    )
    evidence, binary = ws.read_element_evidence("fixture", 0, padding=0)
    with Image.open(io.BytesIO(binary)) as shown:
        assert shown.getpixel((shown.width // 4, shown.height // 2)) == (255, 0, 0)
        assert shown.getpixel((3 * shown.width // 4, shown.height // 2)) == (0, 0, 255)
        assert shown.width > shown.height
    assert evidence["view_rotation_ccw"] == angle
    assert ws._get("evidence", evidence["id"])["region"] == evidence["region"]
    original_evidence, _ = ws.read_evidence(
        document["id"], Region.model_validate(evidence["region"])
    )
    assert (evidence["id"] == original_evidence["id"]) == (angle == 0)
    assert (ws.root / document["id"] / "original.bin").read_bytes() == path.read_bytes()


@pytest.mark.parametrize(
    "defect", ["missing_angle", "skew", "coordinates", "empty", "dewarp", "merged", "missing_page"]
)
def test_uncertain_mapping_requires_manual_original_view(evidence_case, defect):
    ws, inputs = evidence_case
    path = inputs / "source.png"
    Image.new("RGB", (100, 100), "white").save(path)
    doc = ws.import_document(str(path))
    data = {
        "elements": [{"page_number": 1, "coordinates": [0.1, 0.1, 0.9, 0.1, 0.9, 0.9, 0.1, 0.9]}],
        "pages": [{"page_number": 1, "angle": 0, "status": "Success"}],
    }
    config = json.loads(json.dumps(PARSE_CONFIG))
    if defect == "missing_angle":
        del data["pages"][0]["angle"]
    elif defect == "skew":
        data["pages"][0]["angle"] = 12.5
    elif defect == "coordinates":
        data["elements"][0]["coordinates"][0] = float("nan")
    elif defect == "empty":
        data["elements"][0]["coordinates"] = [0.5] * 8
    elif defect == "dewarp":
        config["capabilities"]["crop_dewarp"] = True
    elif defect == "merged":
        data["elements"][0]["metadata"] = {
            "source_regions": [{"page_number": 1}, {"page_number": 2}]
        }
    else:
        data["pages"] = []
    fixture = ws.root / doc["id"] / "fixture.json"
    fixture.write_text(json.dumps(data))
    ws._put(
        "job",
        "fixture",
        {
            "id": "fixture",
            "document_id": doc["id"],
            "status": "completed",
            "result_path": str(fixture),
            "config": config,
        },
    )
    with pytest.raises(DomainError, match="coordinate_mapping_unavailable"):
        ws.read_element_evidence("fixture", 0)
    assert not ws._all("evidence")

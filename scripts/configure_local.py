"""Generate only this project's local MCP command; no global config or secrets."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
python = root / ".venv/bin/python"
if not python.exists():
    raise SystemExit("Run uv sync --python 3.12 first.")
config = {
    "mcpServers": {
        "study-errorbook": {
            "command": str(python),
            "args": ["-m", "study_errorbook.server"],
            "env": {},
        }
    }
}
(root / ".mcp.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
print("Wrote project-local .mcp.json (machine-specific; regenerate after moving the project).")

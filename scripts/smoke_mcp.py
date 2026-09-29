"""Start a trusted local MCP configuration and verify discovery/status in an isolated empty library."""

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main(config_path: Path):
    config = json.loads(config_path.read_text())["mcpServers"]["study-errorbook"]
    with tempfile.TemporaryDirectory(prefix="study-smoke-") as directory:
        root = Path(directory).resolve()
        config["env"] = {
            **config.get("env", {}),
            "STUDY_INPUT_ROOT": str(root),
            "STUDY_DATA_DIR": str(root / "data"),
            "STUDY_CONFIG_FILE": str(root / "absent.env"),
            "TEXTIN_APP_ID": "",
            "TEXTIN_SECRET_CODE": "",
        }
        params = StdioServerParameters(**config)
        async with asyncio.timeout(30):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    result = await session.call_tool("workspace_status", {})
                    if result.isError or not result.structuredContent:
                        raise RuntimeError("workspace_status did not return a structured success")
                    assert result.structuredContent["data_dir"] == str(root / "data")
                    assert not result.structuredContent["textin_configured"]
                    print(
                        json.dumps(
                            {
                                "config": str(config_path),
                                "tool_count": len(listing.tools),
                                "tools": [tool.name for tool in listing.tools],
                                "status": result.structuredContent,
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, nargs="?", default=Path(".mcp.json"))
    asyncio.run(main(parser.parse_args().config.resolve()))

"""Install a verified bundle into a NEW durable directory, without editing Agent settings."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "posix":
        raise SystemExit("This release requires macOS/Linux (POSIX file locks).")
    bundle = Path(__file__).resolve().parent
    checksums = json.loads((bundle / "SHA256SUMS.json").read_text())
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()}
    if actual != set(checksums) | {"SHA256SUMS.json"}:
        raise SystemExit("Bundle file inventory mismatch; extract a fresh trusted release.")
    for relative, expected in checksums.items():
        path = bundle / relative
        if not path.resolve().is_relative_to(bundle) or path.is_symlink():
            raise SystemExit("Unsafe bundle path.")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SystemExit(f"Bundle checksum mismatch: {relative}")
    wheels = list(bundle.glob("study_errorbook-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Expected exactly one application wheel.")
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("Install uv first, then run this installer again.")
    target = args.target.expanduser().resolve()
    # Refuse replacement, including partial installs: no active runtime or data gets overwritten.
    target.mkdir(parents=True, exist_ok=False)
    python = target / "runtime/bin/python"
    subprocess.run([uv, "venv", "--python", "3.12", str(target / "runtime")], check=True)
    subprocess.run(
        [uv, "pip", "install", "--python", str(python), "--require-hashes",
         "--only-binary", ":all:", "--link-mode", "copy", "-r", str(bundle / "requirements.txt")],
        check=True,
    )
    subprocess.run(
        [uv, "pip", "install", "--python", str(python), "--no-deps", "--link-mode", "copy",
         str(wheels[0])], check=True,
    )
    shutil.copytree(bundle / "plugin", target / "plugin")
    config = {"mcpServers": {"study-errorbook": {
        "command": str(python), "args": ["-I", "-m", "study_errorbook.server"], "env": {},
    }}}
    encoded = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    (target / "mcp.json").write_text(encoded)
    (target / "plugin/.mcp.json").write_text(encoded)
    shutil.copy2(bundle / "verify_release.py", target / "verify_release.py")
    subprocess.run([str(python), "-I", str(target / "verify_release.py"), str(target / "mcp.json")], check=True)
    (target / "installation.json").write_text(json.dumps({
        "wheel": wheels[0].name, "sha256": checksums[wheels[0].name],
        "runtime_version": wheels[0].name.split("-")[1],
        "verified": "isolated local MCP workflow; no OCR request",
    }, indent=2) + "\n")
    print(f"Installed and verified. MCP config: {target / 'mcp.json'}")
    print(f"Plugin source: {target / 'plugin'}")


if __name__ == "__main__":
    main()

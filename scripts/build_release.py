"""Build an allowlisted source-independent installer bundle; never include user data."""

import hashlib
import json
import shutil
import subprocess
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / ".codex-plugin/plugin.json").read_text())
    project_text = (root / "pyproject.toml").read_text()
    base_version = tomllib.loads(project_text)["project"]["version"]
    if base_version != manifest["version"] or "+" in base_version:
        raise ValueError("Project and plugin must use the same public base version.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    runtime_version = f"{base_version}+build.{stamp.lower()}"
    bundle = root / "dist" / f"study-errorbook-{manifest['version']}-{stamp}"
    bundle.mkdir(parents=True)
    # Build a private copy so editable installs and concurrent development keep their metadata.
    with tempfile.TemporaryDirectory(prefix="study-build-") as temporary:
        staging = Path(temporary)
        version_line = f'version = "{base_version}"'
        if project_text.count(version_line) != 1:
            raise ValueError("Expected exactly one static project version declaration.")
        (staging / "pyproject.toml").write_text(project_text.replace(version_line, f'version = "{runtime_version}"'))
        shutil.copytree(root / "src", staging / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(root / "LICENSE", staging / "LICENSE")
        subprocess.run(["uv", "build", "--wheel", "--out-dir", str(bundle)], cwd=staging, check=True)
    subprocess.run(
        ["uv", "export", "--locked", "--no-dev", "--no-emit-project", "--no-header",
         "--output-file", str(bundle / "requirements.txt")],
        cwd=root, check=True, stdout=subprocess.DEVNULL,
    )
    for name in ("install_release.py", "verify_release.py"):
        shutil.copy2(root / "scripts" / name, bundle / name)
    shutil.copy2(root / "README.md", bundle / "README.md")
    (bundle / "docs").mkdir()
    for name in ('distribution.md', 'manual-review-guide.md', 'ocr-conflict-user-guide.md', 'recover-existing-recognition-2026-09-26.md', 'public-validation.md'):
        shutil.copy2(root / "docs" / name, bundle / "docs" / name)
    for name in ("LICENSE", "USE_POLICY.md", "THIRD_PARTY_NOTICES.md", "SECURITY.md", "CONTRIBUTING.md"):
        shutil.copy2(root / name, bundle / name)
    (bundle / "plugin/.codex-plugin").mkdir(parents=True)
    shutil.copy2(root / ".codex-plugin/plugin.json", bundle / "plugin/.codex-plugin/plugin.json")
    # Fixed paths, rather than recursively copying a working directory.
    for relative in (
        "SKILL.md", "references/records.md", "references/intake.md",
        "references/practice.md", "references/deletion.md", "references/exports.md",
    ):
        destination = bundle / "plugin/skills/study-errorbook" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / "skills/study-errorbook" / relative, destination)
    files = {
        path.relative_to(bundle).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(bundle.rglob("*")) if path.is_file()
    }
    (bundle / "SHA256SUMS.json").write_text(json.dumps(files, indent=2) + "\n")
    archive = shutil.make_archive(str(bundle), "zip", bundle.parent, bundle.name)
    print(archive)


if __name__ == "__main__":
    main()

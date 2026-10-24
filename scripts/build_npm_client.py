"""Prepare an allowlisted npm client payload; user data and credentials never enter it."""
import hashlib
import json
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    client = root / 'npm-client'
    metadata = json.loads((client / 'package.json').read_text())
    source_files = sorted(p for p in (root / 'src/study_errorbook').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    fingerprint = hashlib.sha256(b''.join(p.relative_to(root / 'src').as_posix().encode() + p.read_bytes() for p in source_files)).hexdigest()[:12]
    project = (root / 'pyproject.toml').read_text()
    base = tomllib.loads(project)['project']['version']
    runtime_version = f'{base}+client.{metadata["version"]}.f{fingerprint}'
    payload = client / 'runtime'
    if payload.exists():
        shutil.rmtree(payload)
    payload.mkdir()
    with tempfile.TemporaryDirectory(prefix='study-npm-build-') as directory:
        stage = Path(directory)
        (stage / 'pyproject.toml').write_text(project.replace(f'version = "{base}"', f'version = "{runtime_version}"', 1))
        shutil.copytree(root / 'src', stage / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copy2(root / 'LICENSE', stage / 'LICENSE')
        subprocess.run(['uv', 'build', '--wheel', '--out-dir', str(payload)], cwd=stage, check=True)
    (payload / '.gitignore').unlink(missing_ok=True)
    subprocess.run(['uv', 'export', '--locked', '--no-dev', '--no-emit-project', '--no-header', '--output-file', str(payload / 'requirements.txt')], cwd=root, check=True, stdout=subprocess.DEVNULL)
    shutil.copy2(root / 'scripts/verify_release.py', payload / 'verify_release.py')
    for name in ('skills', '.codex-plugin'):
        destination = client / name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(root / name, destination)
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(root / name, client / name)
    pins = json.loads((client / 'uv-assets.json').read_text())
    wheel = next(payload.glob('*.whl'))
    include = [client / 'package.json', client / 'plugin.json', client / 'mcp.json', client / 'bin/client.mjs', *payload.iterdir()]
    include += [p for name in ('skills', '.codex-plugin') for p in (client / name).rglob('*') if p.is_file()]
    files = {p.relative_to(client).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(include)}
    manifest = {'schema': 1, 'runtime_version': runtime_version, 'wheel': wheel.name, 'python_version': '3.12.14', 'uv': pins, 'files': files}
    (payload / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'runtime_version': runtime_version, 'files': len(files), 'private': metadata.get('private'), 'npm_name': metadata['name']}))


if __name__ == '__main__':
    main()

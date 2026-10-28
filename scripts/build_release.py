#!/usr/bin/env python3
"""Build a metadata-free release bundle and matching update manifest."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile

root = Path(__file__).resolve().parents[1]
version = re.search(r'^VERSION=([0-9]+\.[0-9]+\.[0-9]+)$', (root/'mihomo-lite').read_text(), re.M)[1]
manifest = {
    'version': version,
    'sha256': hashlib.sha256((root/'mihomo-lite').read_bytes()).hexdigest(),
    'repository': 'wangmancang520/mihomo-lite',
}
(root/'release.json').write_text(json.dumps(manifest, indent=2)+'\n')
dist = root/'dist'
dist.mkdir(exist_ok=True)
excluded = {'.git','dist','work','__pycache__','.DS_Store'}
paths = [p for p in sorted(root.rglob('*')) if not any(part in excluded or part.startswith('._') for part in p.relative_to(root).parts)]
archive = dist/f'mihomo-lite-v{version}.tar.gz'
with tarfile.open(archive, 'w:gz', format=tarfile.USTAR_FORMAT) as tar:
    for path in [root]+paths:
        if path.is_symlink(): raise SystemExit(f'Refusing symlink: {path}')
        info = tar.gettarinfo(str(path), arcname=str(Path('mihomo-lite')/path.relative_to(root)))
        info.uid = info.gid = 0
        info.uname = info.gname = 'root'
        info.pax_headers = {}
        info.mode = 0o755 if path.is_dir() or path.name == 'mihomo-lite' or path.suffix == '.sh' else 0o644
        if path.is_file():
            with path.open('rb') as file: tar.addfile(info,file)
        else: tar.addfile(info)
for name in ('mihomo-lite','install.sh','release.json'):
    shutil.copyfile(root/name,dist/name)
assets = [dist/name for name in ('mihomo-lite','install.sh','release.json',archive.name)]
(dist/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in assets))
with tarfile.open(archive) as tar:
    for member in tar.getmembers():
        assert not member.pax_headers and not Path(member.name).name.startswith('._')
        if member.isfile():
            original = root/Path(member.name).relative_to('mihomo-lite')
            assert tar.extractfile(member).read() == original.read_bytes()
print(f'Built {archive.name}; manager SHA256={manifest["sha256"]}')

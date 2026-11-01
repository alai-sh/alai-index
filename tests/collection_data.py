# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Collection tarballs for tests, laid out as ``ansible-galaxy collection build`` writes them."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _entry(name: str, data: bytes | None) -> dict[str, object]:
    """A FILES.json entry, a directory without ``data``."""
    if data is None:
        return {"name": name, "ftype": "dir", "chksum_type": None, "chksum_sha256": None}
    return {"name": name, "ftype": "file", "chksum_type": "sha256", "chksum_sha256": _sha256(data)}


def module(short_description: str) -> bytes:
    return f'DOCUMENTATION = """\nmodule: x\nshort_description: {short_description}\n"""\n'.encode()


def build_collection(
    path: Path,
    name: str = "ns.coll",
    version: str = "1.0.0",
    *,
    dependencies: dict[str, object] | None = None,
    requires_ansible: str | None = None,
    runtime: bytes | None = None,
    modules: dict[str, bytes] | None = None,
) -> Path:
    """Write a collection's tarball to ``path`` and return it. ``runtime`` is ``meta/runtime.yml`` as written, else
    one is made from ``requires_ansible`` if given. ``modules`` maps a module's name to its source. The same arguments
    give the same bytes."""
    namespace, short = name.split(".")
    files: dict[str, bytes] = {f"plugins/modules/{n}.py": src for n, src in (modules or {}).items()}
    if runtime is None and requires_ansible is not None:
        runtime = f"requires_ansible: '{requires_ansible}'\n".encode()
    if runtime is not None:
        files["meta/runtime.yml"] = runtime

    dirs = sorted({str(Path(f).parents[i].as_posix()) for f in files for i in range(len(Path(f).parents) - 1)})
    entries: list[dict[str, object]] = [_entry(d, None) for d in [".", *dirs]]
    entries.extend(_entry(f, data) for f, data in sorted(files.items()))
    files_json = json.dumps({"files": entries, "format": 1}).encode()
    info = {"namespace": namespace, "name": short, "version": version, "dependencies": dependencies or {}}
    manifest = {
        "collection_info": info,
        "file_manifest_file": {
            "name": "FILES.json",
            "ftype": "file",
            "chksum_type": "sha256",
            "chksum_sha256": _sha256(files_json),
            "format": 1,
        },
        "format": 1,
    }

    buffer = io.BytesIO()
    # mtime=0, as w:gz stamps the time into the gzip header and the bytes would differ a second later.
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        members = {"MANIFEST.json": json.dumps(manifest).encode(), "FILES.json": files_json}
        for d in dirs:
            info_d = tarfile.TarInfo(d)
            info_d.type = tarfile.DIRTYPE
            info_d.mode = 0o755
            tar.addfile(info_d)
        for member, data in [*members.items(), *sorted(files.items())]:
            info_f = tarfile.TarInfo(member)
            info_f.size = len(data)
            info_f.mode = 0o644
            tar.addfile(info_f, io.BytesIO(data))
    path.write_bytes(buffer.getvalue())
    return path

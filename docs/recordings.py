# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""The fixture for the docs' terminal recordings in ``docs/recordings.toml``, loaded by ``python -m cli_recorder``.

The recordings start with collection tarballs built as the tests build them, and mirror a local Galaxy server, so the
docs build never reaches the network.
"""

from __future__ import annotations

import collections.abc as _c
import contextlib
import pathlib
import sys

sys.path.insert(0, "tests")

from collection_data import build_collection, module
from galaxy_data import MockGalaxy


@contextlib.contextmanager
def environment(root: pathlib.Path, scenarios: dict[str, object]) -> _c.Generator[dict[str, str]]:
    """Serve each ``[[galaxy]]`` version from a local Galaxy server, at ``$GALAXY_SERVER`` in the recordings."""
    galaxy = MockGalaxy()
    try:
        for spec in _specs(scenarios.get("galaxy")):
            name, version = str(spec["name"]), str(spec["version"])
            galaxy.add(name, version, _build(root, spec), str(spec.get("created_at", "2026-01-01T00:00:00Z")))
        yield {"GALAXY_SERVER": galaxy.url}
    finally:
        galaxy.close()


def prepare(recording: dict[str, object], directory: pathlib.Path) -> None:
    """Build each of a recording's ``tarballs`` into its directory, named as ansible-galaxy names them."""
    for spec in _specs(recording.get("tarballs")):
        _build(directory, spec)


def _build(directory: pathlib.Path, spec: dict[str, object]) -> pathlib.Path:
    """A collection's tarball from its spec, a ``name`` and ``version``, with optional ``dependencies``,
    ``requires_ansible`` and ``modules``, each module's name and its short description."""
    name, version = str(spec["name"]), str(spec["version"])
    modules = spec.get("modules")
    dependencies = spec.get("dependencies")
    return build_collection(
        directory / f"{name.replace('.', '-')}-{version}.tar.gz",
        name,
        version,
        dependencies=dict(dependencies) if isinstance(dependencies, dict) else None,
        requires_ansible=str(spec.get("requires_ansible", ">=2.16")),
        modules={str(n): module(str(d)) for n, d in modules.items()} if isinstance(modules, dict) else None,  # pyright: ignore[reportUnknownVariableType]
    )


def _specs(value: object) -> list[dict[str, object]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []  # pyright: ignore[reportUnknownVariableType]

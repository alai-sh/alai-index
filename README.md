# alai-index

[![PyPI](https://img.shields.io/pypi/v/alai-index)](https://pypi.org/project/alai-index/)
[![CI](https://github.com/alai-sh/alai-index/actions/workflows/ci.yml/badge.svg)](https://github.com/alai-sh/alai-index/actions/workflows/ci.yml)

> **Preview:** The commands and their options can still change in a release
> before 1.0. The index alai-index writes doesn't, as the
> [index format](https://alai.sh/alai-index/index-format/) is versioned on its
> own.

alai-index builds and operates an [alai](https://github.com/alai-sh/alai)
index as a tree of files on disk. The tree can then be published to a web
server, a bucket or anywhere else.

```console
$ alai-index config --root my-index
$ alai-index add --root my-index ns-coll-1.0.0.tar.gz ns-dep-2.1.0.tar.gz
added ns.coll 1.0.0
added ns.dep 2.1.0
$ python -m http.server -d my-index
```

## Installing

alai-index is published on [PyPI](https://pypi.org/project/alai-index/).
When it's used often, [uv](https://docs.astral.sh/uv/) can install it as a
tool so `alai-index` is on the `PATH`.

```console
$ uv tool install alai-index
```

When it's only needed once, such as in a CI job, `uvx` runs it without
installing it.

```console
$ uvx alai-index config --root my-index
```

The [documentation](https://alai.sh/alai-index/) covers everything from
[the tree](https://alai.sh/alai-index/the-tree/) an index is made of to
[every command](https://alai.sh/alai-index/cli/) and its options.

## Developing

Working on alai-index needs [uv](https://docs.astral.sh/uv/), which
installs everything else.

```console
$ uv sync
$ uv run tox run -f sanity
$ uv run tox run -f py314
```

The docs at [alai.sh/alai-index](https://alai.sh/alai-index/) are built
from `docs/` with `uv run tox run -e docs`. The build also writes the CLI
reference and records the terminal recordings with
[cli-recorder](https://github.com/alai-sh/cli-recorder), so it needs Linux
or macOS.

## License

MIT, see [LICENSE](https://github.com/alai-sh/alai-index/blob/main/LICENSE).

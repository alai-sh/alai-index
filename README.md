# alai-index

> **Warning:** alai-index is a proof of concept. Nothing is released, and
> anything can change.

Builds and operates an [alai](https://github.com/alai-sh/alai-cli) index as a
tree of files on disk. Publishing the tree, to a web server, a bucket or
anywhere else, is up to you.

```console
$ alai-index config --root my-index
$ alai-index add --root my-index ns-coll-1.0.0.tar.gz ns-dep-2.1.0.tar.gz
added ns.coll 1.0.0
added ns.dep 2.1.0
$ python -m http.server -d my-index
```

## Commands

| Command | |
|---|---|
| `config [--index TEMPLATE] [--dl TEMPLATE] [--[no-]listing] [--[no-]plugins]` | Write `config.json`. An option left out keeps what it says now, or takes the default, which is index files by name (`index/{namespace}/{name}.jsonl`), artifacts in the tree (`dl/{sha256-prefix}/{sha256-checksum}.tar.gz`), a `collections.json` listing and a `plugins.jsonl` plugin index. It can't change once the index has collections |
| `add TARBALL... [--published TIME]` | Add collection artifacts. Each version is read from its own `MANIFEST.json` and `meta/runtime.yml`. A version already in the index with the same artifact is skipped, and one with another artifact is refused. Every dependency must be met by a version in the index, or among the tarballs, that isn't yanked |
| `set NS.NAME[:VERSION]... [--[no-]yanked] [--[no-]deprecated]` | Change whether versions are yanked or deprecated, the only things about a version that can change. `NS.NAME` alone is every version of it. A yanked version stays installable when a lock file pins it, and is skipped otherwise |
| `remove NS.NAME[:VERSION]...` | Take versions down, deleting their lines, artifacts and plugin entries. Lock files pinning them stop working. It's refused if a version that stays, and isn't yanked, would be left without a version it needs. Yank those first, or remove them too |
| `generate-shell-completion SHELL` | Print the script that enables Tab completion. See [Shell completion](#shell-completion) |

`config` and `add` take `--root DIR`, the index's directory, the current
one by default.

## Shell completion

`alai-index generate-shell-completion SHELL` prints the script that enables
Tab completion, for `bash`, `zsh`, `fish`, `powershell` or `tcsh`.

| Shell | Add to | Line |
|---|---|---|
| bash | `~/.bashrc` | `eval "$(alai-index generate-shell-completion bash)"` |
| zsh | `~/.zshrc`, after `compinit` | `eval "$(alai-index generate-shell-completion zsh)"` |
| fish | | Run `alai-index generate-shell-completion fish > ~/.config/fish/completions/alai-index.fish` once |
| PowerShell | `$PROFILE` | `alai-index generate-shell-completion powershell \| Out-String \| Invoke-Expression` |
| tcsh | `~/.tcshrc` | ``eval "`alai-index generate-shell-completion tcsh`"`` |

It's built on [argcomplete](https://github.com/kislyuk/argcomplete), so its
[global completion](https://github.com/kislyuk/argcomplete#global-completion)
completes alai-index with no setup.

## The tree

With the default `config.json`, after adding versions 1.0.0 and 1.1.0 of
`ns.name`:

```text
my-index/
├── config.json
├── collections.json
├── plugins.jsonl
├── index/
│   └── ns/
│       └── name.jsonl            one line per version
├── dl/
│   ├── 15/
│   │   └── 1531a523….tar.gz      artifacts, by sha256
│   └── ab/
│       └── ab401fdd….tar.gz
└── private/                      never published, but kept for later adds
    └── plugins/
        └── ns/
            └── name.jsonl        each version's extraction
```

- `config.json`, `collections.json`, `plugins.jsonl` and the index files, at
  the paths `config.json` gives.
- The artifacts, where the `dl` template puts them. One on another host, such
  as `https://dl.example/sha256/…`, is kept under `dl/` at the URL's path.
- `private/`, alai-index's own state. **It isn't part of the index format,
  isn't published, and its files can change in any release. But it must be
  kept.** With a plugin index, `private/plugins/<namespace>/<name>.jsonl`
  records what was extracted from each version's artifact, which is what a
  collection's `plugins.jsonl` entries are worked out from. A later `add` to
  that collection needs its file, and refuses to run without it rather than
  drop the plugins of the versions already there. Keep `private/` with the
  rest of the tree, or wherever you keep state, and fetch it back before
  adding to an index from another machine.

To publish a changed tree, leave out `private/`, and upload everything. When
updating a live index you should publish it in the following order:

1. **dl/**
2. **index/**
3. **plugins.jsonl**
4. **collections.json**

Publishing in this order ensures an index never names a file that is not there
yet.

When index files are configured with the sha256 template format, an index file
a change replaces stays in the tree. Only clients still holding a stale
`collections.json` with the older entry can still see and request that file.
The file can be pruned once the cache lifetime on the index host or client's
is hit so it won't use an older `collections.json` entry.

## Developing

Building it needs `cargo` as well as [uv](https://docs.astral.sh/uv/), as
alai-core is built from source until it's on PyPI.

```console
$ uv sync
$ uv run tox run -f sanity
$ uv run tox run -f py314
```

alai-core is pinned to a commit in `[project] dependencies`. To work on both
at once, point it at a local checkout in `[tool.uv.sources]`.

```toml
alai-core = { path = "../alai-core", editable = true }
```

## License

MIT, see [LICENSE](LICENSE).

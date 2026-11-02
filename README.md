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
| `config [--index TEMPLATE] [--dl-template TEMPLATE] [--[no-]listing] [--[no-]plugins]` | Write `config.json`. An option left out keeps what it says now, or takes the default, which is index files by name (`index/{namespace}/{name}.jsonl`), artifacts in the `--dl` directory as a path in the index (`dl/{sha256-prefix}/{sha256-checksum}.tar.gz`), a `collections.json` listing and a `plugins.jsonl` plugin index. It can't change once the index has collections. See [Artifacts](#artifacts) |
| `add TARBALL... [--published TIME] [--skip-checks CHECK]... [--no-commit]` | Add collection artifacts. Each version is read from its own `MANIFEST.json` and `meta/runtime.yml`. A version already in the index with the same artifact is skipped, and one with another artifact is refused. Every dependency must be met by a version in the index, or among the tarballs, that isn't yanked, and every requirement must be one ansible-core can read. `--skip-checks dependencies` and `--skip-checks requirements` make those warnings instead. Avoid them, as a client may then fail to use the collection. They're for mirroring another source one to one, such as Galaxy. The artifact, its `MANIFEST.json`, and its name and version are always checked |
| `set NS.NAME[:VERSION]... [--[no-]yanked] [--[no-]deprecated] [--no-commit]` | Change whether versions are yanked or deprecated, the only things about a version that can change. `NS.NAME` alone is every version of it. A yanked version stays installable when a lock file pins it, and is skipped otherwise |
| `remove NS.NAME[:VERSION]... [--no-commit]` | Take versions down, deleting their lines, artifacts and plugin extractions. Lock files pinning them stop working. It's refused if a version that stays, and isn't yanked, would be left without a version it needs. Yank those first, or remove them too |
| `rebuild [NS.NAME[:VERSION]... \| --all] [--files index\|plugins]... [--from-artifacts] [--allow-changes]` | Make public files again, from what the index knows or from the artifacts. See [Rebuilding](#rebuilding) |
| `generate-shell-completion SHELL` | Print the script that enables Tab completion. See [Shell completion](#shell-completion) |

Every command but `generate-shell-completion` takes:

- `--root DIR`, the index's directory, the current directory by default.
- `--private DIR`, the directory of the index's own state, `private/` in
  the root by default. A path given is relative to the current directory,
  like `--root`. Point it outside the root to keep the index tree to just
  the files that need to be publicly published.
- `--dl DIR`, the directory of the artifacts, `dl/` in the root by default,
  also relative to the current directory. See [Artifacts](#artifacts).

If the collections don't fit on the command line for your platform, put them
in a file, one a line, and pass `@FILE`, such as
`alai-index rebuild @changed-collections.txt`. Each line is read as one
argument, as it's written.

## Running it from a program

`add`, `set`, `remove` and `rebuild` take `--output-format json`, which
prints one JSON object to stdout:

```json
{
  "versions": [{"name": "ns.coll", "version": "1.0.0", "sha256": "…", "result": "added"}],
  "collections": ["ns.coll"],
  "written": {
    "dl": [],
    "index": ["dl/ab/ab40….tar.gz", "index/ns/coll.jsonl", "plugins.jsonl", "collections.json"],
    "private": ["cache/plugins/ns/coll.jsonl", "index/ns/coll.jsonl", "collections.json"]
  },
  "deleted": {"dl": [], "index": [], "private": []},
  "stale": []
}
```

| Key | |
|---|---|
| `versions` | Each version the command acted on, as an object with its collection's `name`, its `version`, and `result`, what the command did with it, from the table below. `add` gives the artifact's `sha256` too, and `rebuild` an `error` when extracting failed |
| `collections` | Each collection with a file written or deleted. A command that changed nothing gives none |
| `written`, `deleted` | Each file written or deleted, in three groups, each relative to its own directory. `dl` is artifacts published on another host, relative to `--dl`. `index` is what's published with the index, relative to the root, in the order to publish it. `private` is the private state, relative to `--private` |
| `stale` | Each index file found by sha256 that the listing no longer names. Delete it from the host once copies of the old `collections.json` have expired |

Each command gives these values of `result`:

| Command | `result` |
|---|---|
| `add` | `added`, or `exists` if it was already in the index |
| `set` | `changed`, or `unchanged` if it already was as asked |
| `remove` | `removed` |
| `rebuild` | `extracted`, for each version whose plugins `--from-artifacts` extracted again. Otherwise it gives no versions |

Publish `dl` first, then `index` in its order, and delete what `deleted`
lists after. Warnings always go to stderr, so stdout is only the JSON. Errors
go to stderr too, with exit code 1. The JSON only ever gains keys, and a key
never changes meaning, so it has no version.

### Batching changes

Each `add`, `set` or `remove` rewrites `plugins.jsonl` and
`collections.json`, which is most of the time a change takes in a large
index. `--no-commit` leaves those two as they are, and a `rebuild` with the
collections changed writes them once at the end:

```console
$ alai-index add --no-commit --output-format json a.tar.gz > a.json
$ alai-index add --no-commit --output-format json b.tar.gz > b.json
$ jq -r '.collections[]' a.json b.json | sort -u > changed.txt
$ alai-index rebuild @changed.txt
```

Until then, the published `plugins.jsonl` and `collections.json` don't show
the changes, but every other file is written, the private state included.

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
├── collections.json              public, made from the private state
├── plugins.jsonl                 public, made from the private state
├── index/
│   └── ns/
│       └── name.jsonl            public, one line per version
├── dl/
│   ├── 15/
│   │   └── 1531a523….tar.gz      artifacts, by sha256
│   └── ab/
│       └── ab401fdd….tar.gz
└── private/                      never published, must be kept
    ├── collections.json          state: every collection and its current index file
    ├── index/
    │   └── ns/
    │       └── name.jsonl        state: each version's sha256, published time, yanked and deprecated
    └── cache/                    made from the artifacts, kept so changes needn't unpack them
        └── plugins/
            └── ns/
                └── name.jsonl    each version's plugin extraction
```

### What's the source of truth

`config.json`, the index's state in `private/`, and the artifacts in `dl/`
are the source of truth. **Everything clients read is derived from them**, so
it can always be made again:

| Public file | Made from |
|---|---|
| An index file's lines | Each version's private state (`sha256`, `published`, `yanked`, `deprecated`), with `deps`, `requires_ansible` and `size` from its artifact's `MANIFEST.json` and `meta/runtime.yml` |
| `collections.json` | A copy of `private/collections.json`. An index without a public listing does not have this file |
| `plugins.jsonl` | Each version's plugin extraction, from its artifact, cached in `private/cache/plugins/` |

Commands read what's derived from the artifacts back from where it's kept,
rather than unpacking the artifacts each time, which would be far too slow.
`deps`, `requires_ansible` and `size` come from the current index file, and
the plugin extractions from `private/cache/`. A collection's plugin lines
need every version's extraction, so without the cache, adding one version
would mean unpacking all the earlier ones. A command fails if an index file
doesn't match the private state, or the cache is missing a version. Lose any public file, or all of them,
and [`rebuild`](#rebuilding) makes them again.

> [!WARNING]
> `private/` is alai-index's internal state, **not an interface**. It isn't
> part of the index format, it's never published, and its layout and file
> formats can change in any alai-index release without notice. What's
> described here is only so you know what to keep. Don't read it or write it
> from other tools. Read the public files instead, which follow the index
> format.

It is important to keep the `private/` directory. Without it, the index can't
be changed and the index needs to be re-created again. Keep it with the rest
of the tree, or wherever you keep state, and fetch it back before changing an
index from another machine.

### Artifacts

`config.json`'s `dl` template is the URL clients download artifacts from. It
must contain `{sha256-checksum}`, so an artifact's URL never changes, and a
cache can never serve one version's old bytes for a version that's taken
down and published again. `--dl DIR`, given to every command, is where the
artifacts are on disk. Neither depends on the other being the default:

| `dl` template | Artifacts on disk |
|---|---|
| A path in the index, the default, such as `dl/{sha256-prefix}/{sha256-checksum}.tar.gz` | At that path in the index, as clients resolve it against the index root. They're published with the index. `--dl` must be the directory that path is in, and without `--dl-template`, `config` makes the template from it |
| A URL on another host, such as `https://dl.example/sha256/{sha256-prefix}/{sha256-checksum}.tar.gz` | In the `--dl` directory, which stands for that host's root, at the URL's path, such as `DIR/sha256/ab/ab40….tar.gz`. Publish the directory to the host as it is |

A `--dl` directory outside the index needs `--dl-template`, since clients
can only find artifacts by a path in the index when they're in it.

### Rebuilding

`rebuild` makes public files again. Two choices decide what it does.

**Which files.** `--files index` is the index files and `collections.json`,
and `--files plugins` is `plugins.jsonl`. Both are made by default, and
`--files` can be given twice. The targets are named collections, or one
version as `NS.NAME:VERSION`, or `--all` for every collection, but not both.
Without targets or `--all`, only `collections.json` and `plugins.jsonl` are
made again.

**Where the data comes from:**

| | From what the index knows (the default) | `--from-artifacts` |
|---|---|---|
| `index` | Each version's private state, with `deps`, `requires_ansible` and `size` from its current index file. `collections.json` from the private listing | `deps`, `requires_ansible` and `size` are read again from each targeted version's artifact |
| `plugins` | The plugin extractions in the private state | Each targeted version's plugins are extracted again from its artifact, then `plugins.jsonl` is made from them |

What the index knows is enough unless the data itself has to change. Reading
the artifacts means unpacking every targeted version, so
`--from-artifacts` needs targets or `--all`, and the artifacts must be on
disk.

| When | Run |
|---|---|
| `collections.json` or `plugins.jsonl` is lost or wrong | `rebuild` |
| The line format changed, such as a field dropped, or an index file is wrong in what the private state holds (`sha256`, `published`, `yanked`, `deprecated`) | `rebuild NS.NAME --files index`, or `--all` |
| An index file is wrong in `deps`, `requires_ansible` or `size` | `rebuild NS.NAME --files index --from-artifacts --allow-changes` |
| An index file can't be read | `rebuild NS.NAME --files index --from-artifacts` |
| How `plugins.jsonl` is worked out changed, such as the `since` and `until` rules | `rebuild --files plugins` |
| An index file is lost, or a field is read from artifacts that wasn't before, or is read differently | `rebuild NS.NAME --files index --from-artifacts`, or `--all` |
| Extraction changed, a collection's extractions are lost, or a failed extraction should be tried again | `rebuild NS.NAME --files plugins --from-artifacts`, or `--all` |

`deps`, `requires_ansible` and `size` are meant to stay as they were
published, so if an artifact gives other values than the index file has,
`--from-artifacts` names them and writes nothing. `--allow-changes` writes
them anyway.

### Publishing

To publish a changed tree, leave out `private/`, and upload the rest. When
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

## Backing up and restoring

While the whole index could be backed up, the following three items can be
used to rebuild the entire public index if needed:

- `config.json`, the index's settings, which nothing else holds.
- The private directory, `private/` or wherever `--private` puts it.
  `private/cache/` can be left out, as the restore makes it again.
- The artifacts, `dl/` or wherever `--dl` puts them. They're most of the
  size. If they're already published to a host you can fetch them back from,
  that host's copy can serve as their backup.

Take the backup while no command is running, so the private state and the
artifacts match.

To restore, put the three back where they were, then make everything else
again from the artifacts:

```console
$ alai-index rebuild --root my-index --all --from-artifacts
```

It reads every artifact once, to make the index files, `collections.json`,
the plugin cache and `plugins.jsonl`. Nothing is taken from a cache, so
nothing stale from the backup survives. Then publish as usual.

Only the newest version of a collection's index file will be recreated. If any
stale index files, as found by the sha256, were present they will not be
recreated. This should not matter unless a client is using a cached version of
`collections.json` but as the index can control what the client gets when that
file is requested this should not be an issue.

The private directory, outside of `private/cache`, cannot be rebuilt from the
published files as it contains state the index controls. For example whether a
collection version is yanked or deprecated. Without it the index can be
manually recreated by adding each artifact but it will loose this extra
information that was stored in the private state.

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

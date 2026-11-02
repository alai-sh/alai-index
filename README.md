# alai-index

> **Warning:** alai-index is a proof of concept. Nothing is released, and
> anything can change.

alai-index builds and operates an [alai](https://github.com/alai-sh/alai-cli)
index as a tree of files on disk. The tree can then be published to a web
server, a bucket or anywhere else.

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
| `config [--index TEMPLATE] [--dl-template TEMPLATE] [--[no-]listing] [--[no-]plugins]` | Write `config.json`. An option left out keeps its current value, or takes its default when there isn't one. By default, index files are by name as `index/{namespace}/{name}.jsonl` and artifacts are in the `--dl` directory as `dl/{sha256-prefix}/{sha256-checksum}.tar.gz`. The default config also has a `collections.json` listing and a `plugins.jsonl` plugin index. `config.json` can't change once the index has collections. See [Artifacts](#artifacts) |
| `add TARBALL... [--published TIME] [--skip-checks CHECK]... [--no-commit]` | Add collection artifacts. Each version is read from its own `MANIFEST.json` and `meta/runtime.yml`. A version already in the index with the same artifact is skipped, and one with another artifact is refused. Every dependency must be met by a version that isn't yanked, either in the index already or among the tarballs. Every requirement must be one ansible-core can read. When mirroring another source one to one, such as Galaxy, `--skip-checks dependencies` or `--skip-checks requirements` can make those checks warnings instead. A client may fail to use a collection added with a check skipped. The artifact, its `MANIFEST.json` and its name and version are always checked, even with `--skip-checks` |
| `set NS.NAME[:VERSION]... [--[no-]yanked] [--[no-]deprecated] [--no-commit]` | Change whether versions are yanked or deprecated, the only things about a version that can change. `NS.NAME` alone is every version of it. A yanked version stays installable when a lock file pins it, and is skipped otherwise |
| `remove NS.NAME[:VERSION]... [--no-commit]` | Take versions down, deleting their lines, artifacts and plugin extractions. Lock files pinning them stop working. When a remaining version that isn't yanked would be left without a version it needs, `remove` refuses, so yank that version first or remove it too |
| `rebuild [NS.NAME[:VERSION]... \| --all] [--files index\|plugins]... [--from-artifacts] [--allow-changes]` | Make public files again, from what the index knows or from the artifacts. See [Rebuilding](#rebuilding) |
| `galaxy-mirror SERVER [NS.NAME... \| --all] [--limit N] [--session FILE] [--revalidate] [--no-commit] [--strict]` | Bring collections in line with a Galaxy server. See [Mirroring a Galaxy server](#mirroring-a-galaxy-server) |
| `generate-shell-completion SHELL` | Print the script that enables Tab completion. See [Shell completion](#shell-completion) |

Every command but `generate-shell-completion` takes these options.

- `--root DIR` is the index's directory, and is the current directory by
  default.
- `--private DIR` is the directory of the index's own state, and is
  `private/` in the root by default. Like `--root`, the path is relative to
  the current directory. When the index tree should hold only the files that
  are published, `--private` can point outside the root.
- `--dl DIR` is the directory of the artifacts, and is `dl/` in the root by
  default. Like `--root`, the path is relative to the current directory. See
  [Artifacts](#artifacts).
- `-q` or `--quiet` shows only errors on stderr, without progress or
  warnings.
- `-v` or `--verbose` also shows each request to a server and how long it
  took.

By default, a command shows what it's doing on stderr as it does it and
prints its warnings at the end. stdout is only ever the command's output.

When there are too many collections to fit on the command line, they can be
put in a file with one on each line and passed as `@FILE`, such as
`alai-index rebuild @changed-collections.txt`. Each line is read as one
argument, exactly as it's written.

## Mirroring a Galaxy server

> [!WARNING]
> `galaxy-mirror` is for a private Galaxy server used within an
> organization, and shouldn't be used to mirror galaxy.ansible.com.
> galaxy.ansible.com is already mirrored at
> [index.alai.sh](https://index.alai.sh/), which is the index alai uses by
> default. Mirroring all of galaxy.ansible.com would also take over a hundred
> thousand requests. When galaxy.ansible.com must be mirrored, only a subset
> of its collections should be targeted.

`galaxy-mirror` brings collections in line with a Galaxy server, through the
v3 endpoints ansible-galaxy itself uses. `SERVER` is the URL ansible-galaxy
would be given.

```console
$ alai-index galaxy-mirror https://galaxy.example.com corp.tools
$ alai-index galaxy-mirror https://galaxy.example.com --all --limit 100
```

`galaxy-mirror` does the following for each collection.

- **Versions on the server and not in the index** are downloaded and added
  from the oldest upload to the newest. Each added version's `published`
  time is the server's `created_at`. Each artifact must match the sha256
  checksum the server gives, and its `MANIFEST.json` must match the
  collection's name and version. Galaxy doesn't enforce dependencies or
  requirements, so a dependency the index doesn't have or a requirement
  ansible-core can't read is only a warning.
- **A version is yanked only when its own page is a `404`**, so a listing
  that's cut short can't yank anything. When the collection's own page at
  `/api/v3/collections/<ns>/<name>/` is a `404`, every version of the
  collection is yanked.
- **Versions already in the index are checked** against the server's sha256
  checksum when they're yanked, when the server's `created_at` differs from the
  index's `published`, or with `--revalidate`. A yanked version the server
  lists again is unyanked. If the server now serves a different artifact, a
  `conflict` is reported with the artifact's details for further analysis.
  `galaxy-mirror` never replaces a version's artifact, as versions are
  immutable.
- **The collection's deprecation** is set on every version.

A version that fails is reported as a warning and the mirror carries on with
the rest. `galaxy-mirror` exits with `0` unless `--strict` is given. When
syncing against a server with lots of collections and versions, `--limit N`
can be used to perform the sync in smaller batches. Any versions not
downloaded are reported as `deferred` and are picked up by the next run.

Each run asks the server for a collection's versions again, using one request
for every 100 versions. When running `galaxy-mirror` repeatedly with
`--limit N`, `--session FILE` can be used to persist data between runs so the
same work isn't repeated each time. Versions already in the index aren't
checked again until the collection is listed afresh after its downloads are
done, and `--revalidate` always lists it again. As nothing in a session file
is checked for being out of date, start a new one for each mirror and only
use it for runs close together against the same server.

With `--output-format json`, each version's result is one of those in
[Results](#results).

## Running it from a program

When running alai-index from another program, `add`, `set`, `remove`,
`rebuild` and `galaxy-mirror` can be given `--output-format json` to print
one JSON object to stdout. The object looks like the following.

```json
{
  "versions": [{"name": "ns.coll", "version": "1.0.0", "result": "added", "details": {"sha256": "…"}}],
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
| `versions` | Each version the command acted on, as an object. The object holds the collection's `name` and the `version`, with the `result` and its `details`. `version` is `null` when the entry is for the whole collection. See [Results](#results) |
| `collections` | Each collection with a file written or deleted. A command that changed nothing gives none |
| `written`, `deleted` | Each file written or deleted, split into three groups. `dl` holds the artifacts published to another host, relative to `--dl`. `index` holds the files published with the index, relative to the root and in the order to publish them. `private` holds the private state, relative to `--private` |
| `stale` | Each index file that the listing no longer names, when index files are found by sha256. Once cached copies of the old `collections.json` have expired, the file can be deleted from the host |

Publish `dl` first and then `index` in its order, and delete what `deleted`
lists after that. Warnings and errors always go to stderr, so stdout is only
the JSON. A command that fails exits with code 1. The JSON only ever gains
keys, and a key never changes meaning, so it has no version.

### Results

Every command uses the same `versions` key, with a `result` and its
`details` for each collection and version it processed. The table below
lists each result, the commands that give it and what its `details` hold.

| `result` | Given by | Means | `details` |
|---|---|---|---|
| `added` | `add`, `galaxy-mirror` | The version was added | `sha256` is the artifact's sha256 checksum |
| `changed` | `set`, `galaxy-mirror` | The version's `yanked` or `deprecated` changed | The fields that changed and their new values, such as `{"yanked": true}` |
| `conflict` | `galaxy-mirror` | The server serves another artifact for a version the index has. The index's artifact is kept | `sha256` is the sha256 checksum of the artifact the server serves now, and `url` is where to download it |
| `deferred` | `galaxy-mirror` | The version still needs mirroring, but `--limit` was reached before it, so it's left for a later run. Versions mirrored before the limit have the result `added`. When `version` is `null`, the whole collection was left for a later run without being looked at. Which of its versions are missing isn't known yet | None |
| `exists` | `add` | The version was already in the index, with the same artifact | `sha256` is the artifact's sha256 checksum |
| `extracted` | `rebuild --from-artifacts` | The version's plugins were extracted again | When extracting failed, `error` says why and the plugin index has none of the version's plugins |
| `failed` | `galaxy-mirror` | The version couldn't be mirrored, or the whole collection couldn't when `version` is `null`. The mirror carried on with the rest | `kind` is the kind of failure, from the table below, and `error` is the error message |
| `removed` | `remove` | The version, its artifact and its plugin extraction were deleted | `sha256` is the removed artifact's sha256 checksum |
| `unchanged` | `set` | The version was already as asked | None |

A `failed` result's `kind` is one of these.

| `kind` | Means |
|---|---|
| `bad_artifact` | The artifact can't be read, or isn't a valid collection |
| `bad_detail` | The version's page has no download URL or sha256 |
| `galaxy_http_<code>` | An HTTP error status that retrying won't fix, such as `galaxy_http_404`. A `429` or `5xx` is `galaxy_unavailable` |
| `galaxy_unavailable` | A timeout, a `429` or a `5xx` that retrying didn't get past. Trying again later may work |
| `manifest_mismatch` | The artifact's `MANIFEST.json` is for another collection or version |
| `sha256_mismatch` | The download doesn't match the sha256 the server gives |

### Batching changes

When making many changes to a large index, rewriting `plugins.jsonl` and
`collections.json` after each `add`, `set` or `remove` takes most of the
time. `--no-commit` can be used to leave the two files as they are, and a
`rebuild` of the changed collections then writes them once at the end.

```console
$ alai-index add --no-commit --output-format json a.tar.gz > a.json
$ alai-index add --no-commit --output-format json b.tar.gz > b.json
$ jq -r '.collections[]' a.json b.json | sort -u > changed.txt
$ alai-index rebuild @changed.txt
```

Every other file is written straight away, including the private state, but
`plugins.jsonl` and `collections.json` don't show the changes until the
`rebuild`.

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

alai-index's completion is built on
[argcomplete](https://github.com/kislyuk/argcomplete). When argcomplete's
[global completion](https://github.com/kislyuk/argcomplete#global-completion)
is set up, alai-index is completed without any further setup.

## The tree

With the default `config.json`, adding versions 1.0.0 and 1.1.0 of `ns.name`
gives the following tree.

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
it can always be made again.

| Public file | Made from |
|---|---|
| An index file's lines | Each version's private state (`sha256`, `published`, `yanked`, `deprecated`), with `deps`, `requires_ansible` and `size` from its artifact's `MANIFEST.json` and `meta/runtime.yml` |
| `collections.json` | A copy of `private/collections.json`. An index configured without a public listing has no `collections.json` |
| `plugins.jsonl` | Each version's plugin extraction, from its artifact, cached in `private/cache/plugins/` |

Unpacking every artifact on every command would be far too slow, so commands
read the values derived from the artifacts from where they're already kept.
`deps`, `requires_ansible` and `size` come from the current index file, and
the plugin extractions come from `private/cache/`. The cache is what lets a
new version be added without unpacking every earlier one, as a collection's
plugin lines need every version's extraction. A command fails if an index
file doesn't match the private state or the cache is missing a version, and
[`rebuild`](#rebuilding) can make any lost public file again.

> [!WARNING]
> `private/` is alai-index's internal state, **not an interface**. The
> private directory isn't part of the index format and is never published.
> Its layout and file formats can change in any alai-index release without
> notice. What's described here is only so you know what to keep. Other
> tools should never read or write the private directory, and should read the
> public files that follow the index format instead.

The `private/` directory must be kept, as the index can't be changed without
it and would have to be created again. The private directory can be kept with
the rest of the tree or wherever you keep state, and must be fetched back
before changing the index from another machine.

### Artifacts

The `dl` template in `config.json` is the URL clients download artifacts
from. The template must contain `{sha256-checksum}` so an artifact's URL never
changes, which stops a cache serving old bytes for a version that's taken down
and published again. `--dl DIR` is where the artifacts are kept on disk, and
every command takes it. The template and the directory can each be changed
without changing the other.

| `dl` template | Artifacts on disk |
|---|---|
| A path in the index such as `dl/{sha256-prefix}/{sha256-checksum}.tar.gz`, which is the default | Clients resolve the path against the index root, so the artifacts are kept at that path and published with the index. `--dl` must be the directory that path is in. When `--dl-template` isn't given, `config` makes the template from `--dl` |
| A URL on another host, such as `https://dl.example/sha256/{sha256-prefix}/{sha256-checksum}.tar.gz` | At the URL's path in the `--dl` directory, such as `DIR/sha256/ab/ab40….tar.gz`. The directory stands for that host's root, so publish it to the host as it is |

When `--dl` is outside the index, `--dl-template` must be given, as clients
can only find artifacts by a path in the index when the artifacts are inside
it.

### Rebuilding

`rebuild` makes public files again, and two choices decide what it does.

**Which files.** `--files index` is the index files and `collections.json`,
and `--files plugins` is `plugins.jsonl`. `rebuild` makes both by default,
and `--files` can be given twice to name both. The targets are collections or
single versions as `NS.NAME:VERSION`, and `--all` targets every collection
instead. Without targets or `--all`, only `collections.json` and `plugins.jsonl` are
made again.

**Where the data comes from.**

| | From what the index knows (the default) | `--from-artifacts` |
|---|---|---|
| `index` | Each version's private state, with `deps`, `requires_ansible` and `size` from its current index file. `collections.json` from the private listing | `deps`, `requires_ansible` and `size` are read again from each targeted version's artifact |
| `plugins` | The plugin extractions in the private state | Each targeted version's plugins are extracted again from its artifact, then `plugins.jsonl` is made from them |

What the index knows is enough unless the data itself has to change. Reading
the artifacts means unpacking every targeted version, so `--from-artifacts`
needs targets or `--all`. The artifacts must also be on disk.

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
published. When an artifact gives values for them that differ from the index
file, `--from-artifacts` writes nothing and names each version that differs.
When the artifact's values are the right ones, `--allow-changes` writes them
anyway.

### Publishing

When publishing a changed tree, upload everything but `private/`. When
updating a live index, publish the files in the following order.

1. **dl/**
2. **index/**
3. **plugins.jsonl**
4. **collections.json**

Publishing in this order ensures an index never names a file that is not there
yet.

When index files are found by sha256, an index file that a change replaces
stays in the tree and is reported in `stale`. Only clients still holding an
old `collections.json` can see and request the replaced file. Once cached
copies of the old `collections.json` have expired on the host and on clients,
the replaced file can be deleted.

## Backing up and restoring

While the whole index could be backed up, the following three items can be
used to rebuild the entire public index if needed.

- `config.json`, as nothing else holds the index's settings.
- The private directory, which is `private/` unless `--private` puts it
  elsewhere. `private/cache/` can be left out, as the restore makes it again.
- The artifacts, which are in `dl/` unless `--dl` puts them elsewhere. The
  artifacts are most of a backup's size, so a host they're published to can
  serve as their backup when they can be fetched back from it.

Take the backup while no command is running, so the private state and the
artifacts match.

To restore, put the three back where they were, then make everything else
again from the artifacts.

```console
$ alai-index rebuild --root my-index --all --from-artifacts
```

The rebuild reads every artifact once and makes the index files, the plugin
cache, `collections.json` and `plugins.jsonl` from them. Nothing is taken
from a cache, so nothing stale from the backup survives, and the tree can then
be published as usual.

When index files are found by sha256, the restore only makes each
collection's current index file again and any stale index files aren't made
again. A missing stale index file only matters to a client still using a
cached copy of an old `collections.json`. The index host controls how long
`collections.json` is cached, so a missing stale index file shouldn't be an
issue.

Apart from `private/cache/`, the private directory can't be made again from
the published files, as it holds state only the index has. Whether a version
is yanked or deprecated is one example. Without the private directory, the
index can be created again by adding each artifact, but the state the private
directory held is lost.

## Developing

Building it needs `cargo` as well as [uv](https://docs.astral.sh/uv/), as
alai-core is built from source until it's on PyPI.

```console
$ uv sync
$ uv run tox run -f sanity
$ uv run tox run -f py314
```

alai-core is pinned to a commit in `[project] dependencies`. When working on
alai-core and alai-index at once, `[tool.uv.sources]` can point alai-core at
a local checkout instead.

```toml
alai-core = { path = "../alai-core", editable = true }
```

## License

MIT, see [LICENSE](LICENSE).

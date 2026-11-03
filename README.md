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
| `config [--index TEMPLATE] [--dl-template TEMPLATE] [--[no-]listing] [--[no-]plugins]` | Write `config.json`. An option left out keeps its current value, or takes its default when there isn't one. By default, index files are by name as `index/{namespace}/{name}.jsonl` and artifacts are in the `--dl` directory as `dl/{sha256-prefix}/{sha256-checksum}.tar.gz`. The default config also has a `collections.json` listing and a `plugins.jsonl` plugin index. Once the index has collections, the templates can't change, as files would be left where the old ones put them. Whether the index has a listing or a plugin index can still change. See [Artifacts](#artifacts) |
| `add TARBALL... [--published TIME] [--skip-checks CHECK]...` | Add collection artifacts. Each version is read from its own `MANIFEST.json` and `meta/runtime.yml`. A version already in the index with the same artifact is skipped, and one with another artifact is refused. Every dependency must be met by a version that isn't yanked, either in the index already or among the tarballs. Every requirement must be one ansible-core can read. When mirroring another source one to one, such as Galaxy, `--skip-checks dependencies` or `--skip-checks requirements` can make those checks warnings instead. A client may fail to use a collection added with a check skipped. The artifact, its `MANIFEST.json` and its name and version are always checked, even with `--skip-checks` |
| `set NS.NAME[:VERSION]... [--[no-]yanked] [--[no-]deprecated]` | Change whether versions are yanked or deprecated. `NS.NAME` alone is every version of it. A yanked version stays installable when a lock file pins it, and is skipped otherwise |
| `remove NS.NAME[:VERSION]...` | Take versions down, deleting their lines, artifacts and plugin extractions. Lock files pinning them stop working. When a remaining version that isn't yanked would be left without a version it needs, `remove` refuses, so yank that version first or remove it too |
| `generate-indexes [NS.NAME[:VERSION]...]` | Make index lines again from the artifacts. See [Generating the index files](#generating-the-index-files) |
| `generate-plugins [NS.NAME[:VERSION]...] [--from-plugin-cache]` | Extract plugins again and work `plugins.jsonl` out again. See [Generating the plugin index](#generating-the-plugin-index) |
| `galaxy-mirror SERVER [NS.NAME... \| --all] [--limit N] [--session FILE] [--checked FILE] [--revalidate] [--strict]` | Bring collections in line with a Galaxy server. See [Mirroring a Galaxy server](#mirroring-a-galaxy-server) |
| `generate-shell-completion SHELL` | Print the script that enables Tab completion. See [Shell completion](#shell-completion) |

Every command but `generate-shell-completion` takes these options.

- `--root DIR` is the index's directory, and is the current directory by
  default.
- `--plugin-cache DIR` is the directory of the plugin cache, and is
  `plugin-cache/` in the root by default. Like `--root`, the path is relative
  to the current directory. When the index tree should hold only the files
  that are published, `--plugin-cache` can point outside the root. See
  [The plugin cache](#the-plugin-cache).
- `--dl DIR` is the directory of the artifacts, and is `dl/` in the root by
  default. Like `--root`, the path is relative to the current directory. See
  [Artifacts](#artifacts).
- `-q` or `--quiet` shows only errors on stderr, without progress or
  warnings.
- `-v` or `--verbose` also shows each request to a server and how long it
  took.

Every command that changes the index also takes `--output-format text|json`.
That's `add`, `set`, `remove`, `generate-indexes`, `generate-plugins` and
`galaxy-mirror`.

- `--output-format text` prints what changed as a line for each version, and
  is the default.
- `--output-format json` prints one JSON object instead, for a program
  publishing the index. See [Running it from a program](#running-it-from-a-program).

By default, a command shows what it's doing on stderr as it does it and
prints its warnings at the end. stdout is only ever the command's output.

When there are too many collections to fit on the command line, they can be
put in a file with one on each line and passed as `@FILE`, such as
`alai-index generate-plugins @changed-collections.txt`. Each line is read as one
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
  lists again is unyanked. A version the server still serves with the same
  artifact is reported as `unchanged`. If the server now serves a different
  artifact, a `conflict` is reported with the artifact's details for further
  analysis. `galaxy-mirror` never replaces a version's artifact, as versions
  are immutable.
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

A version the server serves with another artifact keeps a `created_at` that
differs from the index's `published`, so every later run checks it again.
When that costs too many requests, `--checked FILE` can be used to skip
versions already checked. The file is kept by the caller, and holds the
server's `created_at` already checked for each version.

```json
{"corp.tools": {"1.0.0": "2026-05-01T00:00:00Z"}}
```

A version is then checked again only when the server's `created_at` differs
from the one in the file. The `published` in each `conflict` and `unchanged`
result is the time to keep in the file. An `unchanged` result means the server
serves the index's artifact again, so its entry can be dropped.

With `--output-format json`, each version's result is one of those in
[Results](#results).

## Running it from a program

When running alai-index from another program, `add`, `set`, `remove`,
`generate-indexes`, `generate-plugins` and `galaxy-mirror` can be given
`--output-format json` to print one JSON object to stdout. The object looks
like the following.

```json
{
  "versions": [{"name": "ns.coll", "version": "1.0.0", "result": "added", "details": {"sha256": "…"}}],
  "written": {
    "dl": [],
    "index": ["dl/ab/ab40….tar.gz", "index/ns/coll.jsonl", "plugins.jsonl", "collections.json"],
    "plugin-cache": ["ns/coll.jsonl"]
  },
  "deleted": {"dl": [], "index": [], "plugin-cache": []},
  "stale": [],
  "outdated": []
}
```

| Key | |
|---|---|
| `versions` | Each version the command acted on, as an object. The object holds the collection's `name` and the `version`, with the `result` and its `details`. `version` is `null` when the entry is for the whole collection. See [Results](#results) |
| `written`, `deleted` | Each file written or deleted, split into three groups. `dl` holds the artifacts published to another host, relative to `--dl`. `index` holds the files published with the index, relative to the root and in the order to publish them. `plugin-cache` holds the plugin cache's files, relative to `--plugin-cache` |
| `stale` | Each index file that the listing no longer names, when index files are found by sha256. Once cached copies of the old `collections.json` have expired, the file can be deleted from the host |
| `outdated` | Each version whose plugin extraction is missing or out of date, and whose artifact wasn't on disk to extract it again. Each entry has the `name` and `version`, and the artifact's path in `dl` or `index`, as `written` gives paths. See [The plugin cache](#the-plugin-cache) |

Publish `dl` first and then `index` in its order, and delete what `deleted`
lists after that. `plugin-cache` is never published, but a program that keeps
the plugin cache somewhere else stores those files there. Warnings and errors
always go to stderr, so stdout is only the JSON. A command that fails exits
with code 1. The JSON only ever gains keys, and a key never changes meaning,
so it has no version.

### Results

Every command uses the same `versions` key, with a `result` and its
`details` for each collection and version it processed. The table below
lists each result, the commands that give it and what its `details` hold.

| `result` | Given by | Means | `details` |
|---|---|---|---|
| `added` | `add`, `galaxy-mirror` | The version was added | `sha256` is the artifact's sha256 checksum. When extracting its plugins failed, `error` says why and the plugin index has none of the version's plugins |
| `changed` | `set`, `generate-indexes`, `galaxy-mirror` | The version's line changed | The fields that changed and their new values, such as `{"yanked": true}`. A field the line no longer has is `null` |
| `conflict` | `galaxy-mirror` | The server serves another artifact for a version the index has. The index's artifact is kept | `sha256` is the sha256 checksum of the artifact the server serves now, and `url` is where to download it. `published` is when the server says that artifact was published, or `null` when it doesn't say |
| `deferred` | `galaxy-mirror` | The version still needs mirroring, but `--limit` was reached before it, so it's left for a later run. Versions mirrored before the limit have the result `added`. When `version` is `null`, the whole collection was left for a later run without being looked at. Which of its versions are missing isn't known yet | None |
| `extracted` | `add`, `remove`, `generate-plugins`, `galaxy-mirror` | The version's plugins were extracted again from its artifact, as `generate-plugins` does, or as its extraction in the plugin cache was missing or out of date | When extracting failed, `error` says why and the plugin index has none of the version's plugins |
| `failed` | `galaxy-mirror` | The version couldn't be mirrored, or the whole collection couldn't when `version` is `null`. The mirror carried on with the rest | `kind` is the kind of failure, from the table below, and `error` is the error message |
| `removed` | `remove` | The version, its artifact and its plugin extraction were deleted | `sha256` is the removed artifact's sha256 checksum |
| `unchanged` | `add`, `set`, `galaxy-mirror` | The version was looked at and nothing about it changed. From `add`, it was already in the index with the same artifact. From `set`, it was already as asked. From `galaxy-mirror`, it was checked against the server, which serves the index's artifact | From `add`, `sha256` is the artifact's sha256 checksum. From `galaxy-mirror`, `sha256` is the artifact's sha256 checksum. `published` is when the server says it was published, or `null` when it doesn't say. None from `set` |

A `failed` result's `kind` is one of these.

| `kind` | Means |
|---|---|
| `bad_artifact` | The artifact can't be read, or isn't a valid collection |
| `bad_detail` | The version's page has no download URL or sha256 |
| `galaxy_http_<code>` | An HTTP error status that retrying won't fix, such as `galaxy_http_404`. A `429` or `5xx` is `galaxy_unavailable` |
| `galaxy_unavailable` | A timeout, a `429` or a `5xx` that retrying didn't get past. Trying again later may work |
| `manifest_mismatch` | The artifact's `MANIFEST.json` is for another collection or version |
| `sha256_mismatch` | The download doesn't match the sha256 the server gives |

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
├── collections.json              every collection and its current index file
├── plugins.jsonl                 made from the plugin cache
├── index/
│   └── ns/
│       └── name.jsonl            one line per version
├── dl/
│   ├── 15/
│   │   └── 1531a523….tar.gz      artifacts, by sha256
│   └── ab/
│       └── ab401fdd….tar.gz
└── plugin-cache/                 never published, made from the artifacts
    └── ns/
        └── name.jsonl            each version's plugin extraction
```

### What's the source of truth

`config.json`, `collections.json`, the index files and the artifacts are the
source of truth. An index file's lines hold everything about each version.
Some fields are set by the index, such as `published`, `yanked` and
`deprecated`. Others are read from the artifact when it's added, such as
`deps`, `requires_ansible` and `size`. The fields read from the artifact are
meant to stay as they were published, so they're kept in the line rather than
read again.
`collections.json` says which index file is each collection's current one.

`plugins.jsonl` is the only file made from something else. It's made from the
plugin cache, which can always be made again from the artifacts. It only ever
describes collections the index has, so whenever it's written, a line for any
other collection is dropped.

A command refuses when an index file is missing, or isn't the one
`collections.json` names. An index file that's lost can't be made again from
the artifacts, as the artifacts don't hold what the index set. Back up the
published index, as [Backing up and restoring](#backing-up-and-restoring)
describes.

When `config.json` says the index has no listing, `collections.json` is still
kept in the tree, as alai-index needs it. Whether it's published is up to
whoever hosts the index, so leave it out of the upload or have the host
refuse it.

### The plugin cache

A collection's lines in `plugins.jsonl` are worked out from every version's
plugins. Unpacking every artifact on every change would be far too slow, so
each version's plugin extraction is kept in the plugin cache. The cache has a
file for each collection at `<namespace>/<name>.jsonl`, and is never
published.

The path of each collection's file is fixed, so a program can fetch it before
running a command. What's in the file is alai-index's own and can change in
any release. Each extraction is numbered, so a newer alai-index knows which
ones were made by an older extractor.

- **A missing or out of date extraction** is made again from the version's
  artifact when a command works on the collection and the artifact is on
  disk. The version is reported as `extracted`.
- **When the artifact isn't on disk**, the version is listed in `outdated`.
  An out of date extraction is still used until it's made again. A
  collection missing an extraction keeps the lines it has in
  `plugins.jsonl`.
- **An extraction made by a newer alai-index** makes the command refuse, as
  this alai-index can't use it. Use the newer alai-index, or delete the
  collection's file to extract its plugins again.

Losing the plugin cache only costs extracting the plugins again, as
[`generate-plugins`](#generating-the-plugin-index) does from the artifacts.

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

### Generating the index files

`generate-indexes` makes index lines again from the artifacts, as `add` makes
them. Each version's `published`, `yanked` and `deprecated` are kept, as only
the index knows them. Everything else is read again from the artifact's
`MANIFEST.json` and `meta/runtime.yml`. When a new alai-index reads artifacts
differently, such as a field added or a fix, `generate-indexes` brings the
existing lines up to date.

Without collections, `generate-indexes` covers every collection. A collection
or a single version as `NS.NAME:VERSION` limits it to a scoped fix. Each
version whose line changes is reported as `changed` with the new values, so
the output shows exactly what changed. The artifacts must be on disk.

### Generating the plugin index

`generate-plugins` extracts each version's plugins again from its artifact,
then works `plugins.jsonl` out again. Without collections, it covers every
collection, and a lost `plugins.jsonl` is made again. With collections, only
their lines are worked out again and merged in. The artifacts must be on
disk.

When the artifacts aren't on disk, or extracting them all again would take
too long, `--from-plugin-cache` works `plugins.jsonl` out from the plugin
cache instead. Only an extraction that's missing or out of date is made again
from its artifact, and one whose artifact isn't on disk is listed in
`outdated`.

| When | Run |
|---|---|
| `plugins.jsonl` is lost or wrong | `generate-plugins --from-plugin-cache` |
| How `plugins.jsonl` is worked out changed, such as the `since` and `until` rules | `generate-plugins --from-plugin-cache` |
| Extraction changed with a new alai-index | `generate-plugins --from-plugin-cache`, which extracts again only what's out of date |
| The plugin cache is lost | `generate-plugins`, which extracts every version again |
| A failed extraction should be tried again, or the cache is suspect | `generate-plugins NS.NAME`, or `generate-plugins` for every collection |
| A plugin index is added to an index that has collections | `config --plugins`, then `generate-plugins` |

When both the index files and the plugin index have to be made again, run
`generate-indexes` and then `generate-plugins`.

### Publishing

When publishing a changed tree, upload everything but the plugin cache. When
updating a live index, publish the files in the following order.

1. **dl/**
2. **index/**
3. **plugins.jsonl**, when `config.json` has a plugin index
4. **collections.json**, when `config.json` has a listing

Publishing in this order ensures an index never names a file that is not there
yet. `collections.json` is always in the tree, as alai-index needs it. When
`config.json` has no listing, leave it out of the upload or have the host
refuse it.

When index files are found by sha256, an index file that a change replaces
stays in the tree and is reported in `stale`. Only clients still holding an
old `collections.json` can see and request the replaced file. Once cached
copies of the old `collections.json` have expired on the host and on clients,
the replaced file can be deleted.

## Backing up and restoring

The published index holds everything that can't be made again. Back up
`config.json`, `collections.json`, the index files and the artifacts. The
artifacts are most of a backup's size, so a host they're published to can
serve as their backup when they can be fetched back from it. Take the backup
while no command is running, so `collections.json` names the index files
that are in it.

When index files are found by sha256, only each collection's current index
file needs backing up. A stale index file only matters to a client still
using a cached copy of an old `collections.json`.

To restore, put the files back where they were, then make `plugins.jsonl`
and the plugin cache again from the artifacts.

```console
$ alai-index generate-plugins --root my-index
```

`generate-plugins` extracts every version's plugins from its artifact, and
makes the plugin cache and `plugins.jsonl` from them. The tree can then be
published as usual.

## Developing

Working on alai-index needs [uv](https://docs.astral.sh/uv/), which
installs everything else.

```console
$ uv sync
$ uv run tox run -f sanity
$ uv run tox run -f py314
```

## License

MIT, see [LICENSE](LICENSE).

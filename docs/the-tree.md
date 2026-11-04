---
description: The files an alai index is made of, which are the source of truth, and where artifacts and the plugin cache live.
---

# The tree

An index is a tree of files on disk. `config` writes its `config.json`, and
every other command reads it to find where the rest of the files go.

[Starting an index](casts/config.cast){ .cli-cast }

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

## What's the source of truth

`config.json`, `collections.json`, the index files and the artifacts are the
source of truth of an index. Each collection's index file holds everything
that is known about each version. While some fields are derived from the
published collection artifacts, others like `published`, `yanked`, and
`deprecated` are only stored in the index line. The `collections.json` lists
the known collections and the current index file for that collection.

`plugins.jsonl` is the only file that is derived from others as it describes
the collections contained in the index. When a new collection is added the
`plugin-cache/` for that artifact is updated and `plugins.jsonl` is also
updated if any plugin information has changed.

A command will fail when an index file is missing, or isn't the one
`collections.json` names. An index file that's lost can't be made again from
the artifacts, as the artifacts don't hold the complete data stored in an
index line. It is recommended to back up the published index, as
[Backing up and restoring](publishing.md#backing-up-and-restoring) describes.

While `config.json` can indicate that the index has no public listing, the
actual `collections.json` file is still required for `alai-index` to manage an
index. Whether it is published publicly is up to whoever hosts the index.

## The plugin cache

A collection's lines in `plugins.jsonl` are worked out from every version's
plugins. Unpacking every artifact on every change would be far too slow, so
each version's plugin extraction is kept in the plugin cache. The cache has a
file for each collection at `<namespace>/<name>.jsonl`, and is not meant to be
published. The cache is `plugin-cache/` in the root unless `--plugin-cache`
gives another directory. When the index tree should hold only the files that
are published, `--plugin-cache` can point outside the root.

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
[`generate-plugins`](generating.md#generating-the-plugin-index) does from the
artifacts.

## Artifacts

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

Once the index has collections, the templates in `config.json` can't change,
as files would be left where the old ones put them. Whether the index has a
listing or a plugin index can still change.

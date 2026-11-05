---
description: The JSON output of alai-index's commands, for a program publishing the index, and every result a version can have.
---

# Running it from a program

When running alai-index from another program, `add`, `set`, `remove`,
`generate-indexes`, `generate-plugins`, and `galaxy-mirror` can be given
`--output-format json` to print one JSON object to stdout. The object looks
like the following.

[Output for a program](casts/json.cast){ .cli-cast }

The output of adding `corp.base` 1.2.0 to an index is shown below, with a
description of each key.

```json
{
  "versions": [ // (1)!
    {
      "name": "corp.base",
      "version": "1.2.0", // (2)!
      "result": "added", // (3)!
      "details": { // (4)!
        "sha256": "8f1d1a0742d00e21690598744960a5789f6b70f065cc1da5f8a104679e64ab05"
      }
    }
  ],
  "written": {
    "dl": [], // (5)!
    "index": [ // (6)!
      "dl/8f/8f1d1a0742d00e21690598744960a5789f6b70f065cc1da5f8a104679e64ab05",
      "index/corp/base.jsonl",
      "plugins.jsonl",
      "collections.json"
    ],
    "plugin-cache": [ // (7)!
      "corp/base.jsonl"
    ]
  },
  "deleted": { // (8)!
    "dl": [],
    "index": [],
    "plugin-cache": []
  },
  "stale": [], // (9)!
  "outdated": [] // (10)!
}
```

1.  Each version the command acted on, with its collection's `name` and the
    `version`.
2.  `null` when the entry is for the whole collection.
3.  What happened to the version. See [Results](#results).
4.  What the result holds, which depends on the `result`.
5.  Artifacts published to another host, relative to `--dl`. Empty here, as
    the artifacts are in the index.
6.  Files published with the index, relative to the root and in the order to
    publish them.
7.  The plugin cache's files, relative to `--plugin-cache`. These are never
    published.
8.  Each file deleted, in the same three groups as `written`.
9.  Index files the listing no longer names, when index files are found by
    sha256. Once cached copies of the old `collections.json` have expired, a
    stale file can be deleted from the host, as
    [Stale index files](publishing.md#stale-index-files) describes.
10. Versions whose plugin extraction is missing or out of date, and whose
    artifact wasn't on disk to extract it again. Each entry has the `name`
    and `version`, and the artifact's path in `dl` or `index` as `written`
    gives paths. See [The plugin cache](the-tree.md#the-plugin-cache).

Publish `dl` first and then `index` in its order, and delete what `deleted`
lists after that. `plugin-cache` is never published, but a program that keeps
the plugin cache somewhere else stores those files there. Warnings and errors
always go to stderr, so stdout is only the JSON. A command that fails exits
with code 1. The JSON only ever gains keys, and a key never changes meaning,
so it has no version.

## Results

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

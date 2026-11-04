---
description: Making the index files and the plugin index again from the artifacts after alai-index changes or a file is lost.
---

# Generating

The index lines and the plugin index are made from the artifacts when a
version is added. When a newer alai-index reads artifacts differently, or a
file made from them is lost, they can be recreated with the newer logic.

## Generating the index files

!!! warning "Use with caution"

    `generate-indexes` can change lines that clients have already read, as a
    newer alai-index may write a line's fields differently than the one that
    added the version. The command is meant for fixing invalid values and
    mistakes in how artifacts were read, so check what it reports as
    `changed` before publishing. Limiting it to the collections or versions
    that need the fix keeps the rest of the index as it was published.

`generate-indexes` makes index lines again from the artifacts, as `add` makes
them. Each version's `published`, `yanked` and `deprecated` are kept, as only
the index file is the source of truth for that data. Everything else is read
again from the artifact's data. When a new alai-index reads artifacts
differently, such as a field added or a fix, `generate-indexes` brings the
existing lines up to date.

Without collections, `generate-indexes` covers every collection. A collection
or a single version as `NS.NAME:VERSION` limits it to a scoped fix. Each
version whose line changes is reported as `changed` with the new values, so
the output shows exactly what changed. The artifacts must be on disk.

## Generating the plugin index

`generate-plugins` extracts each version's plugins again from its artifact,
then rebuilds `plugins.jsonl` from the new data. If no collections are
specified, it will rebuild the plugin cache and `plugins.jsonl` for every
collection in the index. When a specific collection is set, only that
collection's lines are re-generated and merged in. The artifacts for these
collection must be on disk.

When the artifacts aren't on disk, or extracting them all again would take
too long, `--from-plugin-cache` rebuilds `plugins.jsonl` out from the plugin
cache instead. Only an extraction that's missing or out of date is made again
from its artifact, and one whose artifact isn't on disk is listed in
`outdated`.

[Making plugins.jsonl again from the plugin cache](casts/generate-plugins.cast){ .cli-cast }

Use the following table to figure out what to run based on your scenario:

| When | Run |
|---|---|
| `plugins.jsonl` is lost or wrong | `generate-plugins --from-plugin-cache` |
| How `plugins.jsonl` is worked out changed, such as the `since` and `until` rules | `generate-plugins --from-plugin-cache` |
| A new alai-index marks older extractions as out of date | `generate-plugins --from-plugin-cache`, which extracts again only what's out of date |
| A new alai-core extracts plugins differently | `generate-plugins`, which extracts every version again, as the plugin cache can't tell its extractions are out of date |
| The plugin cache is lost | `generate-plugins`, which extracts every version again |
| A failed extraction should be tried again, or the cache is suspect | `generate-plugins NS.NAME`, or `generate-plugins` for every collection |
| A plugin index is added to an index that has collections | `config --plugins`, then `generate-plugins` |

When both the index files and the plugin index have to be made again, run
`generate-indexes` and then `generate-plugins`.

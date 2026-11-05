---
description: Uploading a changed alai index in the right order, and backing it up and restoring it.
---

# Publishing

When publishing a changed tree, upload everything but the plugin cache.
Upload the files in the following order.

1. **dl/**
2. **index/**
3. **plugins.jsonl**, when `config.json` has a plugin index
4. **collections.json**, when `config.json` has a listing

Publishing in this order ensures an index never names a file that is not there
yet.

alai-index always keeps `collections.json` in the tree, as it needs the file
itself. When `config.json` has no listing, leave `collections.json` out of the
upload. The following `config.json` has no `collections` key in `formats.1`,
so the index has no listing.

```json
{
  "formats": {
    "1": {
      "index": "index/{namespace}/{name}.jsonl"
    }
  },
  "dl": "https://index.host/sha256/{sha256-prefix}/{sha256-checksum}",
  "moved-to": null
}
```

## Stale index files

When the index template has `{sha256-checksum}` in it, an index file that a
change replaces stays in the tree. The replaced file is listed in `stale` in
the [JSON output](scripting.md). Only clients still holding an old
`collections.json` can see and request a stale file. Each stale file must
be kept for as long as any cache can still serve an old `collections.json`
that names it, as the
[index format](index-format.md#index-templates-by-sha-256) requires. After
that, the host can delete it. For example, when `collections.json` is
cached for an hour, keep each stale file for at least an hour after it's
replaced.

## Backing up and restoring

The published index holds everything that can't be made again. Back up
`config.json` and `collections.json` along with the index files and the
artifacts. Take the backup while no command is running, so `collections.json`
names the index files that are in it.

When the index template has `{sha256-checksum}` in it, only each collection's
current index file needs backing up. A stale index file only matters to a
client still using a cached copy of an old `collections.json`.

To restore, put the files back where they were, then make `plugins.jsonl`
and the plugin cache again from the artifacts.

```console
$ alai-index generate-plugins --root my-index
```

`generate-plugins` extracts every version's plugins from its artifact, and
makes the plugin cache and `plugins.jsonl` from them. The tree can then be
published as usual.

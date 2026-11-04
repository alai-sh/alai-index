# Agent notes

- Builds and operates an alai index as a tree of files on disk, with
  alai-core's index models. The command line and the tree are the only
  interface. Callers run it as a command and never import it, so modules
  in `src/alai_index/` are private (`_name.py`), and anything a caller
  needs to know is in the command's output.
- Building and managing the index uses no network and no storage
  backends. The caller fetches what an operation reads into the tree, and
  publishes what it writes. An index file or listing the tree should have
  and doesn't is an error, never taken as empty, which would publish an
  index missing versions. A missing plugin cache file is made again
  instead.
- The network is only for reading an upstream server, as `galaxy-mirror`
  reads a Galaxy server's v3 API through `_galaxy`. One request at a time,
  retrying with backoff, and only the endpoints ansible-galaxy itself uses.
  A version is yanked only on a 404 from its own page or its collection's.
  There's no authentication yet. When there is, credentials go only to the
  server they're for.
- `config.json`, `collections.json`, the index files and the artifacts are
  the source of truth. An index line holds everything about a version, the
  fields the index sets and the ones read from the artifact when it was
  added, which are meant to stay as published. `collections.json` is the
  record of each collection's current index file, so it's written by every
  command that changes one. There's no other copy of
  any of it, so nothing has to be migrated but the public format.
- The plugin cache (`plugin-cache/` in the tree unless `--plugin-cache` says
  otherwise) is the only thing kept besides the index. It's never published,
  and exists because a collection's plugin lines need every version's
  extraction. Each collection's file path is fixed, so callers can fetch it,
  but its contents are alai-index's own. Each extraction is numbered
  (`_plugins.CURRENT`). A missing or older one is made again when the
  artifact is on disk, and listed in `outdated` when it isn't. A newer one
  refuses the command. Losing the cache only costs extracting again.
- An index file that's missing, or isn't the one `collections.json` names,
  is an error, never taken as empty or rewritten.
- An index line comes from the artifact itself, `MANIFEST.json` and
  `meta/runtime.yml`, never from an upstream's API.
- Write order: artifacts and the plugin cache, then index files, then
  `plugins.jsonl`, then `collections.json`, so the index never names a
  file that isn't written yet. Every file is written whole.
- Every check runs before anything is written, so a refused command leaves
  the tree as it was.
- A failed plugin extraction never stops a version being added.
- It must work on Windows, and CI tests it there.
- stdout is only the command's output, text or JSON. Warnings and errors
  go to stderr.
- The JSON output only ever gains keys, and a key never changes meaning,
  so it has no version field. If a key ever has to be removed or change
  meaning, add a top-level `v` first, with a missing `v` read as 1, so
  callers can tell the two apart.
- What users read is US English, the CLI's help and output, `README.md`
  and `docs/`, without `;` or `:` in the text.
- The docs are published at alai.sh/alai-index. `docs/cli.md` is written
  from the parser by `python -m cli_recorder reference`, so edit the help
  instead. The recordings are in `docs/recordings.toml`.
- How what users read is written:
  - Start with the situation the reader is in, then what happens or what to
    do. Describe an option by the problem it solves, such as "When syncing
    against a server with lots of collections and versions, `--limit N` can
    be used to perform the sync in smaller batches", not as a bare
    instruction.
  - Give each sentence one main idea. Join a second point to it with "and",
    "so", "as", "but", "when" or "which" only when it completes or explains
    the first, and start a new sentence for the next idea.
  - Keep a sentence to two commas or fewer. One that needs more is carrying
    more than one idea, so split it where the ideas change.
  - Don't write runs of very short sentences that only make sense together.
    When avoiding `;` or `:`, join the two halves with one of the words
    above rather than splitting them into separate sentences.
  - Don't push a phrase into the middle of a clause, between a subject and
    its verb or between "which" and what it says. A condition goes at the
    start or the end of a sentence.
  - Each sentence should make sense on its own, so name the thing rather
    than opening with "this", "that" or "it" pointing at the sentence before.
  - Don't drop words to shorten a phrase, such as "`sha256`, its artifact's"
    for "the artifact's sha256 checksum".
- Comments are sparse, in short plain sentences, without `;` or `:`.
- Every `.py` file starts with the two-line copyright and MIT header.
- Nothing is released, so no compatibility shims. Rename and replace
  outright.
- Checks are `uv run tox run -f sanity` and `uv run tox run -f py314` (any
  of `py310` to `py315`, `py314t`, `py315t`). The docs build is
  `uv run tox run -e docs`.

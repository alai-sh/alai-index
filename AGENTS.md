# Agent notes

- Builds and operates an alai index as a tree of files on disk, with
  alai-core's index models. The command line and the tree are the only
  interface. Callers run it as a command and never import it, so modules
  in `src/alai_index/` are private (`_name.py`), and anything a caller
  needs to know is in the command's output.
- Building and managing the index uses no network and no storage
  backends. The caller fetches what an operation reads into the tree, and
  publishes what it writes. A file the tree should have and doesn't is an
  error, never taken as empty, which would publish an index missing
  versions.
- The network is only for reading an upstream server, as `galaxy-mirror`
  reads a Galaxy server's v3 API through `_galaxy`. One request at a time,
  retrying with backoff, and only the endpoints ansible-galaxy itself uses.
  A version is yanked only on a 404 from its own page or its collection's.
  There's no authentication yet. When there is, credentials go only to the
  server they're for.
- The private directory (`private/` in the tree unless `--private` says
  otherwise) holds the index's state, which with `config.json` and the
  artifacts is the source of truth. Only what can't be derived goes in the
  state. Every public file, and the private `cache/`, is derived and can
  always be rebuilt. The cache exists because rebuilding it means
  unpacking every artifact. The private directory isn't part of the index
  format or the public interface, clients never read it, and its files can
  change shape without a format version bump.
- Private files are written before public ones, and a public index file
  that doesn't match the private state is an error, never silently
  trusted or rewritten.
- An index line comes from the artifact itself, `MANIFEST.json` and
  `meta/runtime.yml`, never from an upstream's API.
- Write order: artifacts and private files, then index files, then
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
- What users read is US English, the CLI's help and output and
  `README.md`, without `;` or `:` in the text.
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
  of `py310` to `py315`, `py314t`, `py315t`).

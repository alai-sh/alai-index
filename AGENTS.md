# Agent notes

- Builds and operates an alai index as a tree of files on disk, with
  alai-core's index models. The command line and the tree are the only
  interface. Callers run it as a command and never import it, so modules
  in `src/alai_index/` are private (`_name.py`), and anything a caller
  needs to know is in the command's output.
- No network and no storage backends. The caller fetches what an
  operation reads into the tree, and publishes what it writes. A file the
  tree should have and doesn't is an error, never taken as empty, which
  would publish an index missing versions.
- Everything under `private/` in the tree is alai-index's own state. It
  isn't part of the index format or the public interface, clients never
  read it, and its files can change shape without a format version bump.
- An index line comes from the artifact itself, `MANIFEST.json` and
  `meta/runtime.yml`, never from an upstream's API.
- Write order: artifacts and private files, then index files, then
  `plugins.jsonl`, then `collections.json`, so the index never names a
  file that isn't written yet. Every file is written whole.
- Every check runs before anything is written, so a refused command leaves
  the tree as it was.
- A failed plugin extraction never stops a version being added.
- It must work on Windows, and CI tests it there.
- What users read is US English, the CLI's help and output and
  `README.md`, without `;` or `:` in the text.
- Comments are sparse, in short plain sentences, without `;` or `:`.
- Every `.py` file starts with the two-line copyright and MIT header.
- Nothing is released, so no compatibility shims. Rename and replace
  outright.
- Checks are `uv run tox run -f sanity` and `uv run tox run -f py314` (any
  of `py310` to `py315`, `py314t`, `py315t`).

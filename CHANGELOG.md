# Changelog

All notable changes to alai-index are listed here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and alai-index
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-05

### Added

- `config`, `add`, `set`, `remove`, `generate-indexes` and
  `generate-plugins`, which build and operate an alai index as a tree of
  files on disk, leaving how the tree is published to the caller.
- A plugin index, `plugins.jsonl`, worked out from a plugin cache that holds
  each version's extraction and can always be made again from the artifacts.
- `galaxy-mirror`, which brings collections in line with a Galaxy server
  through the v3 API ansible-galaxy uses, with `--limit`, `--session` and
  `--checked` for mirroring a large server in batches.
- `--output-format json`, which gives a program publishing the index every
  version changed and every file written, deleted or left stale.
- `generate-shell-completion`, which prints the script that enables Tab
  completion in bash, zsh, fish, PowerShell and tcsh.

[Unreleased]: https://github.com/alai-sh/alai-index/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/alai-sh/alai-index/releases/tag/v0.1.0

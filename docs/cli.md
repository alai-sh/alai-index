---
description: Every alai-index command and the options it takes.
---

<!-- Written by python -m cli_recorder reference from alai-index's argument parser. Edit the help there, not here. -->

# CLI reference

Every alai-index command, with what it does and the options it takes, as
`alai-index <command> --help` shows them. The recordings were made when
these docs were built, so their output is alai-index's own, though the
collections are only examples.

## alai-index

Build and operate an alai index on disk. An argument @FILE is read as arguments from FILE, one a line.

```console
$ alai-index [OPTIONS] <COMMAND>
```

| Command | Description |
|---|---|
| [`config`](#alai-index-config) | Write config.json |
| [`add`](#alai-index-add) | Add collection artifacts |
| [`set`](#alai-index-set) | Yank or deprecate versions |
| [`remove`](#alai-index-remove) | Take versions down |
| [`generate-indexes`](#alai-index-generate-indexes) | Make index lines again from the artifacts |
| [`generate-plugins`](#alai-index-generate-plugins) | Extract plugins again and work plugins.jsonl out again |
| [`galaxy-mirror`](#alai-index-galaxy-mirror) | Bring collections in line with a Galaxy server |
| [`generate-shell-completion`](#alai-index-generate-shell-completion) | Print the script that enables Tab completion in a shell |

## alai-index config

Write config.json. An option left out keeps what it says now, or takes the default. It can't change once the index has collections.

[Starting an index](casts/config.cast){ .cli-cast }

```console
$ alai-index config [OPTIONS]
```

### Options

| Option | Description |
|---|---|
| `--root DIR` | The index's directory, the current one by default |
| `-q`, `--quiet` | Only show errors, not progress or warnings, on stderr |
| `-v`, `--verbose` | Say more on stderr, such as every request to a server |
| `--plugin-cache DIR` | The directory of the plugin cache, which is never published and can always be made again from the artifacts, ROOT/plugin-cache by default |
| `--dl DIR` | The directory of the artifacts, ROOT/dl by default. For a dl template on another host, the host's root |
| `--index TEMPLATE` | Where each collection's index file is, index/{namespace}/{name}.jsonl by default |
| `--dl-template TEMPLATE` | The URL clients download each artifact from, with {sha256-checksum} in it, by default the artifacts' directory as a path in the index, dl/{sha256-prefix}/{sha256-checksum}.tar.gz |
| `--listing`, `--no-listing` | Whether the index has collections.json, which it does by default |
| `--plugins`, `--no-plugins` | Whether the index has plugins.jsonl, which it does by default |

## alai-index add

Add collection artifacts. Each version is read from its MANIFEST.json and meta/runtime.yml.

[Adding collections](casts/add.cast){ .cli-cast }

```console
$ alai-index add [OPTIONS] TARBALL [TARBALL ...]
```

### Arguments

| Argument | Description |
|---|---|
| `TARBALL` | A collection artifact |

### Options

| Option | Description |
|---|---|
| `--root DIR` | The index's directory, the current one by default |
| `-q`, `--quiet` | Only show errors, not progress or warnings, on stderr |
| `-v`, `--verbose` | Say more on stderr, such as every request to a server |
| `--plugin-cache DIR` | The directory of the plugin cache, which is never published and can always be made again from the artifacts, ROOT/plugin-cache by default |
| `--dl DIR` | The directory of the artifacts, ROOT/dl by default. For a dl template on another host, the host's root |
| `--output-format {text,json}` | How to print what changed, text by default. json gives the versions changed and every file written, deleted or left stale, for a program publishing the index. Warnings go to stderr either way |
| `--published TIME` | When they were first published, RFC 3339 in UTC, now by default |
| `--skip-checks CHECK` | Warn rather than refuse when a version fails this check. Avoid it, as a client may then fail to use the collection. It's for mirroring another source one to one. dependencies is a dependency no version in the index meets, and requirements one ansible-core can't read. Give it again for both. The artifact, its MANIFEST.json and its name and version are always checked |

## alai-index set

Change whether versions are yanked or deprecated, the only things about a version that can change.

[Yanking a version](casts/set.cast){ .cli-cast }

```console
$ alai-index set [OPTIONS] NS.NAME[:VERSION] [NS.NAME[:VERSION] ...]
```

### Arguments

| Argument | Description |
|---|---|
| `NS.NAME[:VERSION]` | A collection, for every version of it, or one version as NS.NAME:VERSION |

### Options

| Option | Description |
|---|---|
| `--root DIR` | The index's directory, the current one by default |
| `-q`, `--quiet` | Only show errors, not progress or warnings, on stderr |
| `-v`, `--verbose` | Say more on stderr, such as every request to a server |
| `--plugin-cache DIR` | The directory of the plugin cache, which is never published and can always be made again from the artifacts, ROOT/plugin-cache by default |
| `--dl DIR` | The directory of the artifacts, ROOT/dl by default. For a dl template on another host, the host's root |
| `--output-format {text,json}` | How to print what changed, text by default. json gives the versions changed and every file written, deleted or left stale, for a program publishing the index. Warnings go to stderr either way |
| `--yanked`, `--no-yanked` | Whether they're yanked, so only used when locked |
| `--deprecated`, `--no-deprecated` | Whether they're deprecated |

## alai-index remove

Take versions down, deleting their lines and artifacts. It's refused if a version that stays, and isn't yanked, would be left without a version it needs. Yank those first, or remove them too.

[Removing versions](casts/remove.cast){ .cli-cast }

A version that isn't yanked can't be left without a version it needs, so removing corp.base alone is refused. Removing both together works.
{: .cli-cast-description }

```console
$ alai-index remove [OPTIONS] NS.NAME[:VERSION] [NS.NAME[:VERSION] ...]
```

### Arguments

| Argument | Description |
|---|---|
| `NS.NAME[:VERSION]` | A collection, for every version of it, or one version as NS.NAME:VERSION |

### Options

| Option | Description |
|---|---|
| `--root DIR` | The index's directory, the current one by default |
| `-q`, `--quiet` | Only show errors, not progress or warnings, on stderr |
| `-v`, `--verbose` | Say more on stderr, such as every request to a server |
| `--plugin-cache DIR` | The directory of the plugin cache, which is never published and can always be made again from the artifacts, ROOT/plugin-cache by default |
| `--dl DIR` | The directory of the artifacts, ROOT/dl by default. For a dl template on another host, the host's root |
| `--output-format {text,json}` | How to print what changed, text by default. json gives the versions changed and every file written, deleted or left stale, for a program publishing the index. Warnings go to stderr either way |

## alai-index generate-indexes

Make index lines again from the artifacts, as add makes them, keeping each version's published, yanked and deprecated. It's for when alai-index reads artifacts differently than it did when a version was added. The artifacts must be on disk. Without collections, it's every collection.

```console
$ alai-index generate-indexes [OPTIONS] [NS.NAME[:VERSION] ...]
```

### Arguments

| Argument | Description |
|---|---|
| `NS.NAME[:VERSION]` | A collection, for every version of it, or one version as NS.NAME:VERSION |

### Options

| Option | Description |
|---|---|
| `--root DIR` | The index's directory, the current one by default |
| `-q`, `--quiet` | Only show errors, not progress or warnings, on stderr |
| `-v`, `--verbose` | Say more on stderr, such as every request to a server |
| `--plugin-cache DIR` | The directory of the plugin cache, which is never published and can always be made again from the artifacts, ROOT/plugin-cache by default |
| `--dl DIR` | The directory of the artifacts, ROOT/dl by default. For a dl template on another host, the host's root |
| `--output-format {text,json}` | How to print what changed, text by default. json gives the versions changed and every file written, deleted or left stale, for a program publishing the index. Warnings go to stderr either way |

## alai-index generate-plugins

Extract plugins again from the artifacts, and work plugins.jsonl out again. The artifacts must be on disk. Without collections, it's every collection, and plugins.jsonl is made from nothing.

[Making plugins.jsonl again from the plugin cache](casts/generate-plugins.cast){ .cli-cast }

```console
$ alai-index generate-plugins [OPTIONS] [NS.NAME[:VERSION] ...]
```

### Arguments

| Argument | Description |
|---|---|
| `NS.NAME[:VERSION]` | A collection, for every version of it, or one version as NS.NAME:VERSION |

### Options

| Option | Description |
|---|---|
| `--root DIR` | The index's directory, the current one by default |
| `-q`, `--quiet` | Only show errors, not progress or warnings, on stderr |
| `-v`, `--verbose` | Say more on stderr, such as every request to a server |
| `--plugin-cache DIR` | The directory of the plugin cache, which is never published and can always be made again from the artifacts, ROOT/plugin-cache by default |
| `--dl DIR` | The directory of the artifacts, ROOT/dl by default. For a dl template on another host, the host's root |
| `--output-format {text,json}` | How to print what changed, text by default. json gives the versions changed and every file written, deleted or left stale, for a program publishing the index. Warnings go to stderr either way |
| `--from-plugin-cache` | Work plugins.jsonl out from the plugin cache, so only an extraction that's missing or out of date needs its artifact. One whose artifact isn't on disk is listed as outdated |

## alai-index galaxy-mirror

Bring collections in line with a Galaxy server of your own, through the v3 API ansible-galaxy uses, as Private Automation Hub, galaxy_ng and Artifactory serve it. Don't use it for galaxy.ansible.com, which index.alai.sh already mirrors, and which is too large and slow to mirror this way. Versions the server has that the index doesn't are added as published, a version is yanked only once its own page is gone, and a version the server serves with another artifact is reported, never written. A version that fails is reported, and the rest go on.

[Mirroring a Galaxy server](casts/galaxy-mirror.cast){ .cli-cast }

GALAXY_SERVER holds the URL of a local Galaxy server. The second run finds nothing new to add.
{: .cli-cast-description }

```console
$ alai-index galaxy-mirror [OPTIONS] SERVER [NS.NAME ...]
```

### Arguments

| Argument | Description |
|---|---|
| `SERVER` | The server, as ansible-galaxy is given it |
| `NS.NAME` | A collection |

### Options

| Option | Description |
|---|---|
| `--root DIR` | The index's directory, the current one by default |
| `-q`, `--quiet` | Only show errors, not progress or warnings, on stderr |
| `-v`, `--verbose` | Say more on stderr, such as every request to a server |
| `--plugin-cache DIR` | The directory of the plugin cache, which is never published and can always be made again from the artifacts, ROOT/plugin-cache by default |
| `--dl DIR` | The directory of the artifacts, ROOT/dl by default. For a dl template on another host, the host's root |
| `--output-format {text,json}` | How to print what changed, text by default. json gives the versions changed and every file written, deleted or left stale, for a program publishing the index. Warnings go to stderr either way |
| `--all` | Every collection the server lists or the index has, in place of naming them |
| `--limit N` | Download at most N versions, leaving the rest for a later run, so a long mirror is written as it goes |
| `--revalidate` | Check every version the index has against the server's sha256, not only those that look changed |
| `--session FILE` | Carry on from FILE if it's there, and write to it what's needed to carry on a mirror `--limit` leaves part done, so the next run only downloads. Give the same FILE to each run. Its contents are internal |
| `--checked FILE` | A JSON file of the server's created_at already checked for versions whose created_at differs from published, so a version the server serves with another artifact isn't checked again on every run. It's kept by the caller, as {"ns.name": {"version": "created_at"}} |
| `--strict` | Exit 1 if any version failed |

## alai-index generate-shell-completion

Print the script that enables Tab completion for alai-index in SHELL.

```console
$ alai-index generate-shell-completion [OPTIONS] {bash,zsh,fish,powershell,tcsh}
```

### Arguments

| Argument | Description |
|---|---|
| `{bash,zsh,fish,powershell,tcsh}` | The shell to print the script for |

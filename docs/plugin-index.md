---
description: The alai plugin index, plugins.jsonl, which maps plugin, role and playbook names to the collections that have them.
---

# Plugin index

!!! info "Status"

    Part of [index format](index-format.md) `1`, and still being worked out.

The plugin index maps plugin, role and playbook names to the collections that
provide them. `alai find` uses it to answer questions such as which collection
has a `to_seconds` filter, or where `community.general.docker_container` went.

The plugin index is one file, which clients download, cache with an `ETag`
and search locally, much like `apt-file`. Substring and fuzzy search then need
no server. The [conventions](index-format.md#conventions) of the index format
apply, and the key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be
interpreted as described in [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

## Location

The plugin index is at the optional `plugins` path in the format's entry in
`config.json`.

```json
{
  "formats": {
    "1": {
      "index": "index/{namespace}/{name}.jsonl",
      "collections": "collections.json",
      "plugins": "plugins.jsonl"
    }
  },
  "dl": "https://dl.alai.sh/sha256/{sha256-prefix}/{sha256-checksum}.tar.gz",
  "moved-to": null
}
```

When `plugins` is absent, the index has no plugin index. A client that
doesn't search for plugins ignores it, as it would any unknown field. As the
plugin index is a search aid, a client MAY treat one that `config.json`
names but that isn't found as absent.

The plugin index describes only collections the index has. A writer MUST
drop the lines of a collection that's removed from the index.

## Lines

Each line is one JSON object, and the order of lines is unspecified. The two
lines below are shown across several lines each to make them easier to read.
In the plugin index, each object is on a single line.

A filter the collection has had since 0.1.0 is described by the following
line.

```json
{
  "v": 1,
  "type": "filter",
  "name": "to_seconds",
  "collection": "alai_example.demo",
  "since": "0.1.0",
  "short_description": "Convert a duration string to seconds"
}
```

A module that was removed in 0.2.0, and redirects to another, is described by
the following line.

```json
{
  "v": 1,
  "type": "module",
  "name": "old_thing",
  "collection": "alai_example.demo",
  "since": "0.1.0",
  "until": "0.2.0",
  "routing": {"redirect": "alai_example.demo.new_thing"}
}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `v` | integer | yes | The version of this line's schema, `1`. Clients MUST skip a line with a `v` higher than they support |
| `type` | string | yes | The kind of content, as [Types](#types) lists |
| `name` | string | yes | The short name, such as `user`. The full name is `<collection>.<name>` |
| `collection` | string | yes | The collection that has or had it, such as `community.general` |
| `since` | string | no | The lowest version of the collection that has it, as [`since` and `until`](#since-and-until) describes |
| `until` | string | no | The version it was removed in. Absent when the latest version still has it |
| `short_description` | string | no | The one line summary from its documentation |
| `routing` | object | no | Its entry in the collection's `plugin_routing`, copied verbatim, such as `redirect`, `deprecation` or `tombstone`. The entry comes from `meta/runtime.yml`, or from `extensions/eda/eda_runtime.yml` for `event_source` and `event_filter` |

The combination of `type`, `collection` and `name` is unique in the file. A
reader SHOULD skip a line it can't read rather than fail, as the plugin index
is a search aid.

### Types

`type` uses the names `ansible-doc -t` accepts, plus `role`, `playbook` and
the Event-Driven Ansible types that ansible-rulebook loads from a collection.

| `type` | Where it is in a collection |
|---|---|
| `module` | `plugins/modules/<name>.py` or `.ps1`, and `plugins/action/<name>.py` when there's no module of that name, for a plugin with only an action |
| `become`, `cache`, `callback`, `cliconf`, `connection`, `httpapi`, `inventory`, `lookup`, `netconf`, `shell`, `strategy`, `terminal`, `vars` | `plugins/<type>/<name>.py` |
| `filter`, `test` | The names defined in `plugins/filter/*.py` or `plugins/test/*.py`. One file can define several, so file names alone aren't enough, as [Extraction](#extraction) describes |
| `role` | `roles/<name>/`. `short_description` comes from `argument_specs.main.short_description` in `meta/argument_specs.yml` when it's there |
| `playbook` | `playbooks/<name>.yml` or `.yaml` |
| `event_source`, `event_filter` | `extensions/eda/plugins/<type>/<name>.py`, `extensions/eda/plugins/<type>s/<name>.py` or the older `plugins/<type>/<name>.py`. When a name is in more than one, the first in that order, as ansible-rulebook searches them |
| `rulebook` | `extensions/eda/rulebooks/<name>.yml`, or `rulebooks/<name>.yml`, the first one found. Only `.yml`, as ansible-rulebook doesn't load `.yaml` |

`module_utils`, `doc_fragments`, `plugin_utils` and action plugins that back
a module of the same name aren't included, as playbooks don't name them.

New types MAY be added without a new format. Clients MUST show a line with a
`type` they don't know as a plain name rather than hide it.

## `since` and `until`

Both are worked out from every version of the collection the index has.
Pre-releases are ignored unless only pre-releases have the plugin.

- `since` is the lowest version, by SemVer precedence, that has it.
- `until` is the lowest version, by precedence, that's higher than every
  version that has it. It's absent when the highest version has it.

Working by precedence rather than by publication date keeps maintenance
branches from confusing things. A plugin removed in `13.0.0` but still in
`12.6.6`, published later, has `until` of `13.0.0`.

A plugin that was removed and later added back is described by one range,
from its first `since` to its last `until`, which simplifies that rare case.

`short_description` comes from the highest version that has one. `routing`
comes from the highest version that isn't a pre-release, or the highest
pre-release when there are only pre-releases.

## Routing and history

One file covers both what exists now and what used to exist.

- **A plugin in the latest version** has `since` and no `until`.
- **A plugin that was removed** keeps its line, with `until`, and with
  `routing` when the collection's latest routing mentions it.
- **A plugin known only from routing**, which no version of the collection
  has as a file but its routing redirects, has a line with `routing` and no
  `since`.

So a search for `docker_container` can show community.general, where it was
removed in 5.0.0 with routing, and community.docker, which has it.

`routing` is copied verbatim, so it has ansible-core's `meta/runtime.yml`
structure. A plugin that redirects to another has a `routing` such as the
following.

```json
{
  "redirect": "community.general.say"
}
```

A plugin that was removed has a `tombstone`.

```json
{
  "tombstone": {
    "removal_version": "2.0.0",
    "warning_text": "Use the 'default' callback plugin with 'display_skipped_hosts = no' and 'display_ok_hosts = no' options."
  }
}
```

A plugin that will be removed has a `deprecation`.

```json
{
  "deprecation": {
    "removal_version": "14.0.0",
    "warning_text": "Please migrate to the 'community.general.loganalytics_ingestion' plugin."
  }
}
```

## `ansible.builtin` and `eda.builtin`

ansible-core's and ansible-rulebook's own plugins, with `collection` set to
`ansible.builtin` or `eda.builtin`, aren't in a collection an index has. An
index SHOULD NOT have lines for them, and a client SHOULD ignore any it has.
A client that searches for them reads them from ansible-core and
ansible-rulebook themselves, as alai does from their releases on PyPI, so it
finds the ones in the version a project uses.

## Extraction

A writer reads collections that anyone can publish, and can write to the
index, so it MUST NOT run a collection's code. That rules out
`ansible-doc --metadata-dump` and importing plugins.

- **Names** come from file and directory names, except for filters and
  tests.
- **Filters and tests** come from the file's Python syntax tree, as the keys
  of the dict literal that `FilterModule.filters()` or `TestModule.tests()`
  returns. A file whose names can't be read without running it is left out.
  Their documentation can come from a sidecar such as
  `plugins/filter/<name>.yml`.
- **YAML** is read with a safe loader that keeps timestamps as strings, so a
  value such as `removal_date: 2024-01-01` in `routing` stays a JSON string.
- **`short_description`** comes from the `DOCUMENTATION` string constant in
  the Python syntax tree, a `.yml` sidecar, or for a `.ps1` module its
  `.py` or `.yml` documentation file.

As nothing is run, anything a collection works out at runtime can be missed.
That includes a `DOCUMENTATION` that isn't a string constant, a doc fragment
built at runtime, filter or test names built at runtime, and a file that
can't be parsed as Python 3, such as one written for Python 2. A plugin named
by its file still has a line, but can be without a `short_description`. A
filter or test file whose names can't be read gives no lines.

A version whose extraction fails stays in the index, and has no lines of its
own in the plugin index.

## Full documentation

The plugin index holds only `short_description`, as full documentation is
far too large to download up front. `formats.<n>.docs` in `config.json` is
reserved for a file of each artifact's full documentation, fetched only when
a user looks at a plugin.

---
description: The alai index format, the static files an alai client reads to find, resolve and download Ansible collections.
---

# Index format

!!! info "Status"

    Format `1`. The format is still being worked out and may change before
    alai's first release. A change that an existing client would misread
    gets a new version, as [Versioning](#versioning) describes.

An alai index is a set of static files that an alai client reads to resolve
and download Ansible collections. There's no server-side logic, so any
static host can serve one, such as a web server, an object store or a local
directory. Copying the files is enough to make a private or offline mirror.

This page specifies the files and how clients read them, for anyone writing
an index or a client. alai-index writes indexes in this format, and alai is
a client. The plugin index used by `alai find` is specified in
[Plugin index](plugin-index.md).

The design follows Cargo's sparse registry index
([RFC 2789](https://rust-lang.github.io/rfcs/2789-sparse-index.html) and
[the registry index docs](https://doc.rust-lang.org/cargo/reference/registry-index.html)),
with a `config.json` at the root and one file per collection holding one JSON
line per version. Field names follow Cargo's where the meaning is the same.
[Differences from Cargo](#differences-from-cargo) lists where they differ.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be
interpreted as described in [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

## Overview

An index has an index root and the location of its artifacts, which MAY be
on another host.

```text
<index root>                         such as https://index.alai.sh/
  config.json                        how to read this index
  collections.json                   every collection, optional
  plugins.jsonl                      the plugin index, optional
  index/<namespace>/<name>.jsonl     one line per version, found by name
    or index/<aa>/<sha256>.jsonl     or by SHA-256, as config.json says

<artifacts>                          such as https://dl.alai.sh/
  sha256/<aa>/<sha256>               collection artifacts, as config.json says
```

To resolve a collection, a client does the following.

1. Fetch `config.json` and pick the highest format it supports.
2. Decide which index the collection comes from, which is up to the
   client. When an index has a `collections.json`, the client can look the
   collection up in it to see whether the index has it. Without one, the
   client has to request the collection's index file, where a `404` means
   the index doesn't have it.
3. Fetch the collection's [index file](#index-files) from the
   [`index` template](#the-index-template), and the index file of each
   dependency from the index it comes from. Requests SHOULD run in parallel.
4. Resolve using the lines, with no further requests.
5. Download each chosen artifact from the [`dl` template](#artifacts) and
   check its SHA-256 against the line's `sha256`.

## Conventions

- **Encoding.** Every file is UTF-8 JSON. A `.jsonl` file holds one JSON
  object per line, each ended by `\n`. Readers MUST ignore blank lines.
- **Unknown fields.** Readers MUST ignore fields they don't recognize, in
  every file. A reader that rejects unknown keys isn't conforming, as this
  is how the format grows without a new version.
- **Key names.** `config.json` keys are kebab-case, such as `moved-to`, as
  Cargo's are. Keys in every other file are snake_case, such as
  `requires_ansible`.
- **Times**, the `time` type in the tables below, are JSON strings of
  exactly 20 characters in the form `YYYY-MM-DDTHH:MM:SSZ`, such as
  `2026-10-05T17:30:02Z`. That's an
  [RFC 3339](https://www.rfc-editor.org/rfc/rfc3339#section-5.6) date and
  time in UTC, to the second, with an uppercase `T` and `Z`. Each field has
  the number of digits shown, and is a valid date and time, with a second of
  `60` allowed for a leap second. A time in any other form is invalid, such
  as one with fractional seconds, an offset such as `+00:00`, a lowercase
  `t` or `z`, or a space in place of `T`. Writers MUST NOT write one.
- **SHA-256 values**, the `sha256` type in the tables below, are JSON
  strings of exactly 64 lowercase hex characters (`0-9` and `a-f`). A value
  with an uppercase letter, a prefix such as `sha256:`, or any other length
  is invalid, and writers MUST NOT write one. The case matters, as the
  `{sha256-checksum}` placeholders put the value into URLs, which a host
  can treat as case-sensitive.
- **Collection names** are `<namespace>.<name>`. Each part MUST be a valid
  Python identifier that isn't a Python keyword, as ansible-core's
  `AnsibleCollectionRef.is_valid_collection_name` requires, and MUST be
  lowercase (`[a-z0-9_]`, not starting with a digit). Galaxy already
  enforces lowercase, and it gives case-sensitive hosts one path per
  collection.
- **Versions** are [SemVer 2.0.0](https://semver.org/spec/v2.0.0.html)
  strings, stored exactly as published, with any pre-release (`-rc.1`) or
  build metadata (`+git940b822`). Clients order versions by SemVer
  precedence. When two versions differ only in build metadata, clients
  order them by comparing the strings, so the order is stable.
- **Dependency requirements** (`deps[].req`) use ansible-galaxy's syntax,
  copied verbatim from the collection's `MANIFEST.json`, which isn't Cargo's.
  See [Requirement syntax](#requirement-syntax).
- **`requires_ansible`** is a
  [PEP 440 version specifier](https://peps.python.org/pep-0440/#version-specifiers),
  copied verbatim from the collection's `meta/runtime.yml`, as ansible-core
  versions are PEP 440 rather than SemVer. See
  [`requires_ansible` syntax](#requires_ansible-syntax).

## `config.json`

`config.json` is at the index root. Its location is the only one that's
fixed, so its schema only ever gains fields.

```json
{
  "formats": {
    "1": {
      "index": "index/{namespace}/{name}.jsonl",
      "collections": "collections.json",
      "plugins": "plugins.jsonl"
    }
  },
  "dl": "https://dl.alai.sh/sha256/{sha256-prefix}/{sha256-checksum}",
  "moved-to": null
}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `formats` | object | yes | Each format the index serves, by its number as a string, with the paths for that format. An index MAY serve several formats at once during a transition. Clients MUST pick the highest format they support and ignore the rest. A key that isn't a number is ignored. A client that supports none of them MUST fail with an error saying a newer client is needed |
| `formats.<n>.index` | string | yes | The URL template for each collection's [index file](#index-files), as [The `index` template](#the-index-template) describes |
| `formats.<n>.collections` | string | no | The path of [`collections.json`](#collectionsjson). Absent when the index has no listing, as [No listing](#no-listing) describes. MUST be present when the `index` template is [by SHA-256](#index-templates-by-sha-256) |
| `formats.<n>.plugins` | string | no | The path of the [plugin index](plugin-index.md). Absent when the index has none |
| `dl` | string | yes | The URL template for artifacts, as [Artifacts](#artifacts) describes |
| `moved-to` | string or null | no | When set, the index has moved to this index root. See [Moved indexes](#moved-indexes) |

An empty string for `collections`, `plugins` or `moved-to` means the same as
leaving it out, and so does `null`.

The paths in `formats`, and the `index` and `dl` templates once filled in,
are URL references, resolved against the index root as
[RFC 3986](https://www.rfc-editor.org/rfc/rfc3986#section-5) describes. They
can be relative, or absolute and on another host. The index root ends in
`/`, so a path without a leading `/` stays under it. Clients MUST refuse a
reference that leads from an `https` index to anything but `https`, and one
that leads from an index that isn't on the client's own disk to a `file:`
URL.

The names `api` and `auth-required`, from Cargo, are reserved in
`config.json`. An index without `api` is read-only.

### The `index` template

`formats.<n>.index` says where each collection's index file is. A client
fills in the placeholders for the collection it wants, then resolves the
result against the index root.

| Placeholder | Replaced with |
|---|---|
| `{namespace}` | The collection's namespace |
| `{name}` | The collection's name, without the namespace |
| `{sha256-checksum}` | The collection's `sha256` in [`collections.json`](#collectionsjson), the SHA-256 of its index file rather than of an artifact |
| `{sha256-prefix}` | The first two characters of that `sha256` |

The template MUST name each file on its own, so it has both `{namespace}`
and `{name}`, or has `{sha256-checksum}`. `{sha256-prefix}` is for spreading
files across directories, and MUST NOT be used without `{sha256-checksum}`.
Any other text is kept as it is. A client MUST treat a template that breaks
either rule as invalid. Whether the template has `{sha256-checksum}` decides
how the index works.

- **By name**, such as `index/{namespace}/{name}.jsonl`, which gives
  `index/community/general.jsonl`. A collection's file keeps its URL and is
  replaced when it changes, so it's served with a short cache lifetime, as
  [HTTP behavior](#http-behavior) describes. A client can build the URL
  without `collections.json`, so an index can generate files on request or
  have no listing at all. `sha256` in `collections.json` is optional.
- **By SHA-256**, such as `index/{sha256-prefix}/{sha256-checksum}.jsonl`,
  which gives `index/a4/a4472ff7….jsonl`. A file's URL changes with its
  content, so a file never changes and can be cached as long as an artifact.
  Only `collections.json` says where a collection's file is, so it MUST be
  present and give `sha256` for every collection, as
  [Index templates by SHA-256](#index-templates-by-sha-256) describes.

A template can use both, such as
`index/{namespace}/{name}-{sha256-checksum}.jsonl`. Any template with
`{sha256-checksum}` is by SHA-256.

### Moved indexes

When `moved-to` is set, the index has moved to that index root, which MUST
be an absolute URL. Clients SHOULD read the index from there and tell the
user to update their configuration, and MAY remember the move. Clients MUST
NOT follow a `moved-to` from `https` to anything but `https`. A client SHOULD
stop after a few moves, and MUST fail when the moves go round in a loop.

## `collections.json`

`collections.json` lists every collection in the index, by namespace, with
when its index file last changed and the index file's SHA-256. An index MAY
leave it out, as [No listing](#no-listing) describes.

```json
{
  "v": 1,
  "namespaces": {
    "community": {
      "general": {
        "updated": "2026-10-05T17:30:02Z",
        "sha256": "a4472ff7659bc3d80663664d2abe233c48b8cc40af100c5d43b4efb4dc1a29cf"
      },
      "library_inventory_filtering_v1": {
        "updated": "2025-10-25T11:31:55Z",
        "sha256": "6cdcc7c93ebabc19be364ff579267dce86961c1c0f4fd24ee7b530597b2c007b"
      }
    }
  }
}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `v` | integer | yes | The version of this file's schema, `1`. A client MUST fail on a `v` higher than it supports, rather than guess at the file |
| `namespaces` | object | yes | Each namespace, with its collections |
| `namespaces.<ns>.<name>` | object | yes | One entry for each collection that has an index file |
| `namespaces.<ns>.<name>.updated` | time | yes | When the collection's index file last changed for any reason, such as a new version, a yank or a deprecation |
| `namespaces.<ns>.<name>.sha256` | sha256 | no | The SHA-256 of the collection's index file, over its bytes exactly as stored, before any transfer compression. MUST be in every entry when the `index` template is [by SHA-256](#index-templates-by-sha-256), and SHOULD be otherwise |

A missing `updated`, or an `updated` or `sha256` that isn't a valid
[`time` or `sha256`](#conventions), is an error in the index. A reader MUST
NOT read another form as a time or a hash. How a reader handles the error
depends on whether it uses the field.

- **A reader that uses `updated`**, such as a mirror following the change
  feed, MUST treat an entry with an invalid one as invalid.
- **A reader that doesn't use `updated`**, such as a client resolving
  collections, MAY ignore it, as it would any field it doesn't need.
- **An invalid `sha256` MUST NOT be used.** For an index by name, the client
  then revalidates the index file as though the listing gave no `sha256`.
  For an index [by SHA-256](#index-templates-by-sha-256), the entry is an
  error for that collection, as it would be with no `sha256`.

`collections.json` serves several purposes.

- **Finding which indexes have a collection.** A client can check whether
  an index has each collection it needs from this one file, rather than
  requesting each collection's index file to find out.
- **Listing an index**, for name completion, "did you mean" suggestions, or
  copying collections into another index.
- **A change feed for mirrors.** A downstream mirror polls this one file,
  compares `updated` with what it last saw, and fetches only the index files
  that changed. A collection that's gone from the listing was removed.
- **Skipping unchanged index files.** A client compares each `sha256` with
  its cached copies, and requests only the files that changed. A resolve
  where nothing changed then costs only `config.json` and this file, as
  [Cached index files](#cached-index-files) describes.

Descriptions, tags and other search data are left out, as they're larger
and needed less often.

### Index templates by SHA-256

When the [`index` template](#the-index-template) uses `{sha256-checksum}`,
`collections.json` is how a client finds index files, not only a shortcut.

- **Every entry MUST have `sha256`.** A client treats an entry without one
  as an error for that collection, as nothing else says where its file is.
- **A client MUST read `collections.json` before any index file.** A
  collection that isn't listed isn't in the index, and the client MUST NOT
  request a file for it.
- **The listing is the only file that changes**, apart from `config.json`,
  so its freshness is the whole index's freshness to a client. A stale copy
  names only older index files, which the index still keeps, so a client
  sees an older index but never a mix of old and new.
- **The index MUST keep each replaced index file** for as long as any cache
  can still serve a `collections.json` that names it, counted from when the
  last such `collections.json` was replaced. The index MAY delete the file
  after that. How long that is depends on the index's caching rules, and
  index.alai.sh keeps replaced files for 7 days to be safe.
- **A changed collection gets a new `sha256` and a new URL.** A downstream
  mirror can reuse the `sha256` rather than hash the file again when it
  publishes the same bytes.

### No listing

An index MAY have no `collections.json`, by leaving `formats.<n>.collections`
out of `config.json`. That suits two kinds of host.

- **A host that serves only index files.** A directory of index files and a
  `config.json` is then a whole index, with no listing to rebuild on every
  change. A host that generates files on request needn't list them.
- **A host that shows each user only the collections they may see**, behind
  authentication. One listing shared by every user can't do that.

The following rules apply to an index with no listing.

- **Its `index` template MUST be [by name](#the-index-template).** A client
  MUST treat a `config.json` with a template by SHA-256 and no `collections`
  as invalid.
- **The index has a collection when its index file exists.** A `404`, `410`
  or `451` for the file means the index doesn't have the collection, and the
  client goes on to the next index. Any other status, `401` and `403`
  included, is a failure, so the client MUST NOT go on to another index for
  that collection. An index that hides a collection from a user MUST answer
  `404` for it, as it would for a collection that doesn't exist.
- **It's asked before any later index.** A client SHOULD NOT request a
  collection from a later index until an index with no listing has answered
  for it. Requesting the collection's file from every index at once would
  save a round trip, but each request names the collection. A later index,
  such as a public one, would then see the names of collections that only an
  earlier, private index has. An index with a listing needs no request, as
  the client finds the collection in the listing it already has.
- **Each index file is revalidated on every resolve**, with
  `If-None-Match`, as there's no `sha256` to show a cached copy is current.
  A client SHOULD NOT remember a `404` from such an index past one resolve,
  as a collection a user was just given access to would otherwise stay
  missing.
- **A cached `collections.json` MUST NOT be used** once `config.json` no
  longer names one, as `config.json` decides whether there's a listing.
- **A listing `config.json` names MUST be there.** When `collections` is
  present and its file isn't found, the client MUST fail rather than treat
  the index as having no listing. Falling back would hide a broken or half
  published index, and would leave an index by SHA-256 with no way to find
  its files.

An index with no listing can't be listed, so there's no name completion or
change feed from it. A mirror of it has to learn the names it copies some
other way.

## Index files

Each collection's index file is where the
[`index` template](#the-index-template) says, such as
`index/community/general.jsonl` by name or `index/a4/a4472ff7….jsonl` by
SHA-256.

Each line describes one version of the collection. Lines are in the **order
the versions were published**, so clients MUST NOT assume they're sorted by
version. A version MUST NOT have more than one line.

The line below is shown across several lines to make it easier to read. In
an index file, each version's JSON object is on a single line.

```json
{
  "v": 1,
  "name": "community.general",
  "vers": "13.5.0",
  "deps": [
    {"name": "community.library_inventory_filtering_v1", "req": ">=1.0.0"}
  ],
  "sha256": "54598287e0ea0504aee2d32c884208ddf26f3ac41e0b4e773ff514af210787c4",
  "size": 2913455,
  "requires_ansible": ">=2.18.0",
  "yanked": false,
  "published": "2026-10-05T17:30:02Z"
}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `v` | integer | yes | The version of this line's schema, `1`. See [Versioning](#versioning) |
| `name` | string | yes | The collection's full name, which MUST be the collection the file is for |
| `vers` | string | yes | The version |
| `deps` | array | yes | The collection's dependencies, possibly empty, as [`deps` entries](#deps-entries) describes |
| `sha256` | sha256 | yes | The SHA-256 of the artifact |
| `size` | integer | yes | The artifact's size in bytes |
| `requires_ansible` | string | no | The ansible-core versions the collection supports, from `meta/runtime.yml`, as a [PEP 440 specifier](#requires_ansible-syntax). Absent means any |
| `yanked` | boolean | yes | Whether the version is yanked, as [Yanking and removal](#yanking-and-removal) describes |
| `deprecated` | boolean | no | Whether the version is deprecated. Absent means `false` |
| `published` | time | yes | When the version was first published at its origin |

A reader SHOULD skip a line it can't read with a warning that names the line,
and read the rest of the file. A line can't be read when it isn't a JSON
object, has a `v` other than one the reader supports, names another
collection, repeats a version an earlier line has, or has a required field
missing or of the wrong type. A line with a `v` higher than the reader
supports is skipped as [Versioning](#versioning) describes.

### `deps` entries

```json
{"name": "community.general", "req": ">=13.0.0,<14.0.0"}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `name` | string | yes | The dependency's collection name |
| `req` | string | yes | The versions of it that are needed, in [ansible-galaxy's syntax](#requirement-syntax) |

### Requirement syntax

ansible-galaxy defines the syntax in
`ansible.galaxy.dependency_resolution.versioning.meets_requirements`, and
clients MUST read it the same way.

- A requirement is a comma separated list of clauses, which all have to hold.
  There's no whitespace.
- A clause is an operator (`==`, `=`, `!=`, `>`, `>=`, `<`, `<=`) followed by
  a version, a bare version meaning `==`, or `*` meaning any version.
- The version in a clause is read loosely and cut to three parts, so `>=1.0`
  means `>=1.0.0` and `1.2.3.4` means `1.2.3`.
- Clauses compare by SemVer precedence, so build metadata is ignored.

Examples are `*`, `>=1.0.0`, `>=13.0.0,<14.0.0`, `!=2.1.0` and `1.2.3`.

Writers SHOULD only write requirements in this syntax. An index can still
have lines with requirements in other forms, as a mirror copies what authors
published. For example, Galaxy accepts npm-style requirements such as
`^3.0.0`, so a mirror of Galaxy has lines with them. Readers have to handle
such lines, as [Reading an index](#reading-an-index) describes.

### `requires_ansible` syntax

`requires_ansible` is a PEP 440 version specifier set, as ansible-core checks
it in `ansible.plugins.loader._does_collection_support_ansible_version` with
`packaging.specifiers.SpecifierSet`. ansible-core compares its own version
with any pre-release, post-release or dev part dropped, so 2.19.0rc1 meets
`>=2.19.0`. Clients MUST compare the same way.

Examples are `>=2.17.0`, `>=2.15.0,<2.19.0` and `>=2.19.0b1`.

### Line mutability

Once a line is published, its `name`, `vers`, `deps`, `sha256`, `size`,
`requires_ansible` and `published` MUST NOT change. Only `yanked` and
`deprecated` can change afterwards. A version's `sha256` never changes for
any reason.

## Yanking and removal

- **A yanked version** (`"yanked": true`) stays in the index and its
  artifact stays available. Clients SHOULD NOT choose it in a new
  resolution unless the user asks for that version, and MUST still install
  it when a lock file pins it.
- **A removed version** has its line and artifact deleted, for takedowns
  such as malware, leaked secrets or legal requests. Lock files that pin it
  fail to install, which is intended.

## Index completeness

Every index MUST be self-contained, so it can meet the dependencies of every
collection it has. A client then finds every dependency of a collection in
the collection's own index, even when that index is the only one
configured.

- **When adding a version**, a writer MUST refuse it unless each of its
  `deps[].req` is met by a version in the same index that isn't yanked.
  Each dependency was checked when it was added, so checking direct
  dependencies covers the whole graph.
- **When removing a version**, a writer MUST NOT leave a requirement of a
  remaining version that isn't yanked unmet. It either refuses the removal,
  or yanks the versions that need it too.
- **Yanking** is allowed even when it leaves a dependency met only by yanked
  versions. Locked installs keep working, and new resolutions fail with a
  normal resolver error.
- **Mirrors** copy their upstream as it is, so they're only as complete as
  the upstream. A Galaxy collection can have a dependency Galaxy doesn't
  have, and a mirror of Galaxy has the collection anyway. Clients report the
  missing dependency like any other resolver error.

Completeness means each requirement can be met on its own, not that every
combination resolves, as two collections can still need versions of the
same dependency that don't overlap. A private index usually meets it by
copying the public collections it depends on. The artifacts are copied byte
for byte, so their `sha256` values are the same and client caches share
them.

## Artifacts

The `dl` template in `config.json` gives each artifact's URL. It MUST have at
least one placeholder, as unlike Cargo, nothing is added to a template
without one.

| Placeholder | Replaced with |
|---|---|
| `{sha256-checksum}` | The line's `sha256` |
| `{sha256-prefix}` | The first two characters of `sha256` |
| `{namespace}` | The collection's namespace |
| `{name}` | The collection's name, without the namespace |
| `{version}` | `vers`, percent-encoded so only `A-Z`, `a-z`, `0-9`, `-`, `.`, `_` and `~` stay as they are, so `+` becomes `%2B` |

A template with `{sha256-checksum}` gives each artifact a URL that never
changes, which stops a cache serving old bytes for a version that's removed
and published again. Writers SHOULD use it.

Artifacts are the tar archives exactly as they were published, and are never
repackaged. An artifact can be compressed or not, and the index doesn't say
how. Clients MUST tell an artifact's compression from its bytes rather than
its URL, and MUST refuse an artifact whose compression they can't read. The
URL is only a name, so the index chooses whether it ends in an extension
such as `.tar.gz`, and clients don't read anything from it.

Clients MUST check the SHA-256 of every artifact against the line's
`sha256`, wherever it came from, and MUST refuse one that doesn't match.
Clients SHOULD also check the artifact's `size`, and stop a download that
goes past it.

### Trust

As with crates.io, trust comes from HTTPS and `sha256`. Clients trust the
index, and the hash proves the artifact is the one the index lists. For a
version mirrored from Galaxy, `sha256` is also Galaxy's own, so anyone can
check the mirror against Galaxy.

Signatures aren't carried yet. No version on galaxy.ansible.com was signed
as of 2026-10-16, and the wider ecosystem is moving from GPG towards
Sigstore attestations, such as PyPI's PEP 740 and npm provenance. They can
be added later as an optional field without a new version.

## Writing an index

Whatever writes an index MUST write each file before any file that names
it, so a client never finds a name for a file that isn't there yet.

1. Artifacts, before the index lines that name them.
2. Index files, before the `collections.json` that describes them.
3. The plugin index, before the `collections.json` that lists its
   collections.
4. `collections.json` last.

## HTTP behavior

- **Caching.** `config.json`, `collections.json` and index files found by
  name SHOULD be served with a short `Cache-Control`, such as `max-age=60`,
  and an `ETag`. Index files found by SHA-256 and artifacts never change,
  and SHOULD be served with `Cache-Control: public, max-age=31536000,
  immutable`. Clients SHOULD revalidate with `If-None-Match` on every
  resolve unless offline, except for an index file whose `sha256` shows the
  cached copy is current, as [Cached index files](#cached-index-files)
  describes. These lifetimes are advice, and clients don't rely on any of
  them.
- **Query strings.** A client MAY add a query string to a request for
  `collections.json` or an index file found by name, as
  [Cached index files](#cached-index-files) describes. An index host MUST
  ignore the query string and serve these files the same whatever it is.
  How caches in front of the host treat the query string is up to the host.
- **Compression.** Clients SHOULD send `Accept-Encoding`, such as gzip and
  zstd. Servers MAY compress.
- **Content type.** Clients MUST NOT depend on `Content-Type`.
- **Not found.** A `404`, `410` or `451` for an index file found by name
  means the collection isn't in this index. Clients MAY remember that
  briefly, except for an index with [no listing](#no-listing). Any other
  error is a failure. When `collections.json` lists a collection but its
  file isn't found, the client MUST fail rather than go on to another
  index. For an index file found by SHA-256, see
  [Cached index files](#cached-index-files).
- **Redirects.** Clients MUST follow redirects, but MUST NOT follow one from
  `https` to `http`.
- **Authentication** is the host's concern rather than the format's. A
  private index can put any authentication in front of the same files.
  Clients send credentials only to the indexes they're configured for, and
  treat `401` as needing credentials. An index that shows each user only
  what they may see has no `collections.json` and answers `404` for what's
  hidden, as [No listing](#no-listing) describes.

## Versioning

The format changes in three ways, each for a different kind of change.

1. **Adding an optional field needs no new version.** Readers ignore fields
   they don't recognize, so old clients don't see the new data. Most
   changes are of this kind.
2. **A line's `v` goes up when an old client would misread the line**, such
   as a new kind of dependency that a resolver mustn't drop. Clients MUST
   skip a line with a `v` higher than they support, so an old client doesn't
   see that version at all rather than resolve it wrongly. Clients SHOULD
   tell the user when lines were skipped, such as "3 newer versions need a
   newer alai". This works as Cargo's `v` does.
3. **A new format number in `config.json` is for a new layout**, such as new
   paths, files split differently or another encoding. The new layout is
   written alongside the old one, and both are listed in `formats` during
   the transition.

`collections.json`, and any future file other than `config.json`, has its
own top-level `v`, so each file can change on its own. Changes to the data,
such as new versions and yanks, aren't versioned.

## Reading an index

How a client chooses between indexes and versions, such as which index a
collection comes from or when a pre-release is chosen, is up to the client.
alai's choices are in [its docs](https://docs.alai.sh/). A client needs the
following rules to read an index correctly.

- **Whether an index has a collection.** An index with a listing has a
  collection when its `collections.json` lists it. An index with
  [no listing](#no-listing) has a collection when its index file exists.
- **A version whose metadata can't be read is still a valid line.** A line
  can have a `deps[].req` the [requirement syntax](#requirement-syntax)
  can't read, a dependency name that isn't valid, or a `requires_ansible`
  that isn't a valid PEP 440 specifier, as mirrors copy what authors
  published. A reader MUST NOT fail the file because of such a line, and
  SHOULD treat the version as one that can't be used. ansible-galaxy fails a
  whole install on such a version, which would leave anything that resolves
  to it uninstallable.

### Cached index files

`collections.json` is revalidated on every resolve, like any other file.
Each index file the resolve needs is then handled by comparing the listing's
`sha256` with the SHA-256 the client works out over its own cached copy.

1. **When they match**, the cached copy is current, and the client MAY use
   it without a request.
2. **When they differ, there's no cached copy, or the listing has no
   `sha256`**, the client fetches the file as usual, revalidating any
   cached copy with `If-None-Match`.
3. **When the fetched file's SHA-256 still differs from the listing's**, for
   a file found by name, a cache between the client and the index served an
   old copy, or the listing is the older of the two. The client SHOULD
   fetch once more with a query string nobody could predict, such as
   `?r=<16 random hex characters>`, which no cache holds, and then use what
   it gets. For a file found by SHA-256, the file isn't the one its URL
   names, and the client MUST treat it as an error.

For an index by SHA-256, a `404` for a listed file means the client's
`collections.json` is older than the index keeps replaced files. The client
SHOULD fetch `collections.json` once more past any cache, as above, and try
the file it names now, and fail if that's missing too.

The client compares the listing with the hash of its own cached bytes,
never with a hash it stored from the listing, and MUST NOT treat a cached
file as current because of `updated` alone. Anyone can make a CDN cache old content under a URL they
choose, including one with a future `updated` in its query string, but
nobody can make old content hash to the new value. A client following these
rules can be served stale data for one resolve, within the `max-age`, but
never gets stuck on it. Files found by SHA-256 can't be stale at all, as
only `collections.json` can be.

## Reserved names

These names are reserved for planned additions, and MUST NOT be used for
anything else.

- `api` and `auth-required` in `config.json`, a publishing endpoint and
  whether the index needs authentication, as Cargo has them.
- `formats.<n>.docs` in `config.json`, for each artifact's full plugin
  documentation, as [Plugin index](plugin-index.md#full-documentation)
  describes.
- `requirements` on an index line, for dependencies that aren't
  collections, such as Python and system packages from
  `meta/execution-environment.yml`.
- `deps[].registry`, an index root a dependency must come from, as Cargo
  has it. Dependencies always resolve from the same index for now, and the
  name is reserved in case dependencies across indexes are ever needed.

## Differences from Cargo

| | Cargo | alai |
|---|---|---|
| Index file path | Sharded by the name's prefix, such as `se/rd/serde` | A template, such as `index/{namespace}/{name}.jsonl` or `index/{sha256-prefix}/{sha256-checksum}.jsonl`. Ansible names already have two levels, and object storage doesn't need sharding |
| File extension | None | `.jsonl` |
| `config.json` | `dl`, `api` and `auth-required` | `dl`, `formats` for several layouts at once, and `moved-to`. `api` and `auth-required` are reserved, and authentication is left to the host |
| `dl` placeholders | `{crate}`, `{version}`, `{prefix}`, `{lowerprefix}` and `{sha256-checksum}`. Without any, Cargo adds `/{crate}/{version}/download` | `{namespace}`, `{name}`, `{version}`, `{sha256-prefix}` and `{sha256-checksum}`. At least one is required |
| `deps` entries | `name`, `req`, `features`, `optional`, `default_features`, `target`, `kind`, `registry` and `package` | `name` and `req`. `registry` is reserved, and the rest have no Ansible equivalent |
| Requirement syntax | Cargo's, where `1.2` means `^1.2` | ansible-galaxy's, where `1.2` means `==1.2.0` |
| Dependencies across indexes | Allowed by `deps[].registry`, though crates.io forbids them | Not allowed, as every index is self-contained |
| Artifact hash field | `cksum` | `sha256`, named for what it holds |
| Other line fields | `features`, `features2`, `links` and `rust_version` | `size`, `requires_ansible`, `deprecated` and `published` |
| Collection listing | None, as crates.io lists crates through its web API | `collections.json`, optional for an index by name |

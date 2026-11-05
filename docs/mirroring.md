---
description: Keeping an alai index in line with a private Galaxy server through the v3 API ansible-galaxy uses.
---

# Mirroring a Galaxy server

!!! warning "Not for galaxy.ansible.com"

    `galaxy-mirror` is for a private Galaxy server used within an
    organization, and shouldn't be used to mirror galaxy.ansible.com.
    galaxy.ansible.com is already mirrored at
    [index.alai.sh](https://index.alai.sh/), which is the index alai uses by
    default. Mirroring all of galaxy.ansible.com would also take over a hundred
    thousand requests taking hours to complete and would add extra load on the
    public infrastructure hosting it. If you must mirror something from
    galaxy.ansible.com and cannot use `index.alai.sh`, specify only a subset
    of collections as the mirror target.

`galaxy-mirror` brings collections in line with a Galaxy server, through the
v3 endpoints ansible-galaxy itself uses. `SERVER` is the URL ansible-galaxy
would be given.

```console
$ alai-index galaxy-mirror https://galaxy.example.com corp.tools
$ alai-index galaxy-mirror https://galaxy.example.com --all --limit 100
```

[Mirroring a Galaxy server](casts/galaxy-mirror.cast){ .cli-cast }

`GALAXY_SERVER` holds the URL of a local Galaxy server in the recording.
The second run finds nothing new to add.
{: .cli-cast-description }

`galaxy-mirror` does the following for each collection.

- **Versions on the server and not in the index** are downloaded and added
  from the oldest upload to the newest. Each added version's `published`
  time is the server's `created_at`. Each artifact must match the sha256
  checksum the server gives, and its `MANIFEST.json` must match the
  collection's name and version. Galaxy doesn't enforce dependencies or
  requirements, so a dependency the index doesn't have or a requirement
  ansible-core can't read is only a warning.
- **A version is yanked only when its own page is a `404`**, so a listing
  that's cut short can't yank anything. When the collection's own page at
  `/api/v3/collections/<ns>/<name>/` is a `404`, every version of the
  collection is yanked.
- **Versions already in the index are checked** against the server's sha256
  checksum when they're yanked, when the server's `created_at` differs from the
  index's `published`, or with `--revalidate`. A yanked version the server
  lists again with the same artifact is unyanked. A version the server still
  serves with the same artifact is reported as `unchanged`. If the server now
  serves a different artifact, a `conflict` is reported with the artifact's
  details for further analysis. `galaxy-mirror` never replaces a version's
  artifact, as versions are immutable.
- **The collection's deprecation** is set on every version.

A version that fails is reported as a warning and the mirror carries on with
the rest. `galaxy-mirror` exits with `0` unless `--strict` is given. When
syncing against a server with lots of collections and versions, `--limit N`
can be used to perform the sync in smaller batches. Any versions not
downloaded are reported as `deferred` and are picked up by the next run.

Each run asks the server for a collection's versions again, where one request
can retrieve up to 100 versions at a time. When running `galaxy-mirror`
repeatedly with `--limit N`, `--session FILE` can be used to persist data
between runs so the same work isn't repeated each time. Versions already in
the index aren't checked again until the collection is listed afresh after its
downloads are done, and `--revalidate` always lists it again. The `--session`
file is only designed for related runs, do not reuse this file across different
servers or a run performed during a different time.

A version the server serves with another artifact keeps a `created_at` that
differs from the index's `published`, so every later run checks it again.
When that costs too many requests, `--checked FILE` can be used to skip
versions already checked. The file is kept by the caller, and holds the
server's `created_at` already checked for each version.

```json
{"corp.tools": {"1.0.0": "2026-05-01T00:00:00Z"}}
```

A version is then checked again only when the server's `created_at` differs
from the one in the file. The `published` in each `conflict` and `unchanged`
result is the time to keep in the file. An `unchanged` result means the server
serves the index's artifact again, so its entry can be dropped.

With `--output-format json`, each version's result is one of those in
[Results](scripting.md#results).

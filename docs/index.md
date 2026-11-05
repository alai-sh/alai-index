# alai-index

!!! warning "Preview"

    The commands and their options can still change in a release before 1.0.
    The index alai-index writes doesn't, as the [index format](index-format.md)
    is versioned on its own.

alai-index builds and operates an [alai](https://docs.alai.sh/) index as a
tree of files on disk. The tree can then be published to a web server, a
bucket or anywhere else.

```console
$ alai-index config --root my-index
$ alai-index add --root my-index ns-coll-1.0.0.tar.gz ns-dep-2.1.0.tar.gz
added ns.coll 1.0.0
added ns.dep 2.1.0
$ python -m http.server -d my-index
```

Building and managing the index never touches the network, so the caller
fetches what a command reads into the tree and publishes what it writes. The
one exception is [`galaxy-mirror`](mirroring.md), which downloads new
versions from a Galaxy server to bring the index in line with it.

## Installing

alai-index is published on [PyPI](https://pypi.org/project/alai-index/).
When it's used often, [uv](https://docs.astral.sh/uv/) can install it as a
tool so `alai-index` is on the `PATH`.

```console
$ uv tool install alai-index
```

When it's only needed once, such as in a CI job, `uvx` runs it without
installing it.

```console
$ uvx alai-index config --root my-index
```

## Running a command

Every command works on the index in the current directory unless `--root`
gives another. The [CLI reference](cli.md) lists every command and the
options it takes.

By default, a command shows what it's doing on stderr as it does it and
prints its warnings at the end. stdout is only ever the command's output.
When the output is read by a program, `--output-format json` prints one JSON
object instead, as [Running it from a program](scripting.md) describes.

When there are too many collections to fit on the command line, they can be
put in a file with one on each line and passed as `@FILE`, such as
`alai-index generate-plugins @changed-collections.txt`. Each line is read as
one argument, exactly as it's written.

## Where to go next

- [The tree](the-tree.md) describes the files an index is made of, and which
  of them are the source of truth.
- [Generating](generating.md) covers making the index files and the plugin
  index again from the artifacts.
- [Mirroring a Galaxy server](mirroring.md) keeps an index in line with a
  private Galaxy server.
- [Publishing](publishing.md) covers uploading a changed tree, and backing it
  up and restoring it.

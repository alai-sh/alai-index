# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)
# PYTHON_ARGCOMPLETE_OK: lets argcomplete's global completion run alai-index for completions.

"""``alai-index COMMAND``: the command line."""

from __future__ import annotations

import argparse
import collections.abc as _c
import json
import os
import sys
import typing as _t

if _t.TYPE_CHECKING:
    from ._index import Change, Index

EXIT_ERROR = 1
"""An error was reported. argparse exits 2 for a wrong command line."""


SHELLS = ("bash", "zsh", "fish", "powershell", "tcsh")


def main(argv: _c.Sequence[str] | None = None) -> int:
    parser = _parser()
    if "_ARGCOMPLETE" in os.environ:
        # The shell runs alai-index on every Tab to ask for completions. This prints them and exits.
        _complete(parser)
    args = parser.parse_args(argv)

    from . import _index as index

    try:
        return args.func(args)
    except index.Error as e:
        _report("error", RED, str(e))
        return EXIT_ERROR


def _config(args: argparse.Namespace) -> int:
    from . import _index as index

    index.configure(
        args.root,
        private=args.private,
        dl=args.dl,
        index=args.index,
        dl_template=args.dl_template,
        listing=args.listing,
        plugins=args.plugins,
    )
    return 0


def _open(args: argparse.Namespace) -> Index:
    from . import _index as index

    return index.Index(args.root, private=args.private, dl=args.dl)


TEXT = {
    "added": "added {name} {version}",
    "exists": "{name} {version} is already in the index",
    "changed": "updated {name} {version}",
    "unchanged": "{name} {version} is unchanged",
    "removed": "removed {name} {version}",
    "extracted": "re-extracted {name} {version}",
}
"""Each ``Change.result``, as the text output says it."""

BOLD, RED, YELLOW = "1", "31", "33"


def _color() -> bool:
    """Whether to colour stderr, as alai does. Not with ``NO_COLOR``, always with ``FORCE_COLOR``, and otherwise on a
    terminal that isn't ``TERM=dumb``."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stderr.isatty() and os.environ.get("TERM") != "dumb"


def _styled(text: str, *codes: str) -> str:
    return f"\x1b[{';'.join(codes)}m{text}\x1b[0m" if _color() else text


def _report(kind: str, color: str, message: str) -> None:
    print(f"{_styled(kind, BOLD, color)}{_styled(':', BOLD)} {_styled(message, BOLD)}", file=sys.stderr)


def _finish(args: argparse.Namespace, tree: Index, results: list[Change], more: _c.Sequence[str] = ()) -> int:
    """Print the warnings to stderr, and what changed to stdout, as text, with ``more`` lines after, or as JSON.

    The JSON only ever gains keys, and a key never changes meaning, so it has no version.
    """
    for warning in tree.warnings:
        _report("warning", YELLOW, warning)
    if args.output_format == "json":
        versions = [{k: v for k, v in c._asdict().items() if v is not None} for c in results]
        print(json.dumps({"versions": versions, **tree.files}))
    else:
        for line in [TEXT[c.result].format(name=c.name, version=c.version) for c in results] + list(more):
            print(line)
    return 0


def _add(args: argparse.Namespace) -> int:
    tree = _open(args)
    skip = args.skip_checks or ()
    return _finish(args, tree, tree.add(args.tarballs, published=args.published, skip=skip, commit=not args.no_commit))


def _set(args: argparse.Namespace) -> int:
    tree = _open(args)
    results = tree.set(args.targets, yanked=args.yanked, deprecated=args.deprecated, commit=not args.no_commit)
    return _finish(args, tree, results)


def _remove(args: argparse.Namespace) -> int:
    tree = _open(args)
    return _finish(args, tree, tree.remove(args.targets, commit=not args.no_commit))


def _rebuild(args: argparse.Namespace) -> int:
    tree = _open(args)
    results = tree.rebuild(
        args.targets,
        everything=args.all,
        files=args.files,
        from_artifacts=args.from_artifacts,
        allow_changes=args.allow_changes,
    )
    written = [f"rebuilt {path}" for path in tree.files["written"]["index"]]
    return _finish(args, tree, results, written if results or written else ["nothing changed"])


def _generate_shell_completion(args: argparse.Namespace) -> int:
    from argcomplete.shell_integration import shellcode

    print(shellcode(["alai-index"], shell=args.shell), end="")
    return 0


def _complete(parser: argparse.ArgumentParser) -> None:
    """Print the completions the shell asked for through argcomplete's environment variables, then exit."""
    from argcomplete import autocomplete
    from argcomplete.completers import DirectoriesCompleter

    def set_completers(parser: argparse.ArgumentParser) -> None:
        for action in parser._actions:
            if action.metavar == "DIR":
                action.completer = DirectoriesCompleter()  # pyright: ignore[reportAttributeAccessIssue]
            elif isinstance(action, argparse._SubParsersAction):
                for subparser in action.choices.values():  # pyright: ignore[reportUnknownVariableType]
                    set_completers(subparser)

    set_completers(parser)
    # sys.exit rather than argcomplete's default os._exit, so exit handlers such as coverage's still run.
    autocomplete(parser, exit_method=sys.exit)


SKIPPABLE_CHECKS = ("dependencies", "requirements")
"""``_index.SKIPPABLE_CHECKS``, which is imported only to run a command."""


def _parser() -> argparse.ArgumentParser:
    # @FILE reads arguments from FILE, a line each, for more than a command line holds, as Windows' 32,767 characters.
    parser = argparse.ArgumentParser(
        prog="alai-index",
        description="Build and operate an alai index on disk. An argument @FILE is read as arguments from FILE, one "
        + "a line.",
        fromfile_prefix_chars="@",
    )
    commands = parser.add_subparsers(title="commands", metavar="COMMAND", required=True)

    # Options every command that works on an index takes.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".", metavar="DIR", help="the index's directory, the current one by default")
    common.add_argument(
        "--private",
        metavar="DIR",
        help="the directory of the index's own state, which is never published but must be kept, ROOT/private by "
        + "default",
    )
    common.add_argument(
        "--dl",
        metavar="DIR",
        help="the directory of the artifacts, ROOT/dl by default. For a dl template on another host, the host's root",
    )

    # Options every command that changes collections takes.
    changes = argparse.ArgumentParser(add_help=False, parents=[common])
    changes.add_argument(
        "--output-format",
        choices=("text", "json"),
        default="text",
        help="how to print what changed, text by default. json gives the versions changed and every file written, "
        + "deleted or left stale, for a program publishing the index. Warnings go to stderr either way",
    )
    no_commit = argparse.ArgumentParser(add_help=False)
    no_commit.add_argument(
        "--no-commit",
        action="store_true",
        help="leave plugins.jsonl and collections.json as they are, for 'alai-index rebuild' with the collections "
        + "changed to write once after a batch of changes",
    )

    config = commands.add_parser(
        "config",
        parents=[common],
        help="write config.json",
        description=(
            "Write config.json. An option left out keeps what it says now, or takes the default. "
            + "It can't change once the index has collections."
        ),
    )
    config.add_argument(
        "--index",
        metavar="TEMPLATE",
        help="where each collection's index file is, index/{namespace}/{name}.jsonl by default",
    )
    config.add_argument(
        "--dl-template",
        metavar="TEMPLATE",
        help="the URL clients download each artifact from, with {sha256-checksum} in it, by default the artifacts' "
        + "directory as a path in the index, dl/{sha256-prefix}/{sha256-checksum}.tar.gz",
    )
    config.add_argument(
        "--listing",
        action=argparse.BooleanOptionalAction,
        help="whether the index has collections.json, which it does by default",
    )
    config.add_argument(
        "--plugins",
        action=argparse.BooleanOptionalAction,
        help="whether the index has plugins.jsonl, which it does by default",
    )
    config.set_defaults(func=_config)

    add = commands.add_parser(
        "add",
        parents=[changes, no_commit],
        help="add collection artifacts",
        description="Add collection artifacts. Each version is read from its MANIFEST.json and meta/runtime.yml.",
    )
    add.add_argument("tarballs", nargs="+", metavar="TARBALL", help="a collection artifact")
    add.add_argument(
        "--published", metavar="TIME", help="when they were first published, RFC 3339 in UTC, now by default"
    )
    add.add_argument(
        "--skip-checks",
        action="append",
        choices=SKIPPABLE_CHECKS,
        metavar="CHECK",
        help="warn rather than refuse when a version fails this check. Avoid it, as a client may then fail to use the "
        + "collection. It's for mirroring another source one to one. dependencies is a dependency no version in the "
        + "index meets, and requirements one ansible-core can't read. Give it again for both. The artifact, its "
        + "MANIFEST.json and its name and version are always checked",
    )
    add.set_defaults(func=_add)

    target_help = "a collection, for every version of it, or one version as NS.NAME:VERSION"
    set_ = commands.add_parser(
        "set",
        parents=[changes, no_commit],
        help="yank or deprecate versions",
        description="Change whether versions are yanked or deprecated, the only things about a version that can change.",
    )
    set_.add_argument("targets", nargs="+", metavar="NS.NAME[:VERSION]", help=target_help)
    set_.add_argument(
        "--yanked",
        action=argparse.BooleanOptionalAction,
        help="whether they're yanked, so only used when locked",
    )
    set_.add_argument("--deprecated", action=argparse.BooleanOptionalAction, help="whether they're deprecated")
    set_.set_defaults(func=_set)

    remove = commands.add_parser(
        "remove",
        parents=[changes, no_commit],
        help="take versions down",
        description="Take versions down, deleting their lines and artifacts. It's refused if a version that stays, and "
        + "isn't yanked, would be left without a version it needs. Yank those first, or remove them too.",
    )
    remove.add_argument("targets", nargs="+", metavar="NS.NAME[:VERSION]", help=target_help)
    remove.set_defaults(func=_remove)

    rebuild = commands.add_parser(
        "rebuild",
        parents=[changes],
        help="make the public files again",
        description="Make public files again, from what the index knows, or with --from-artifacts, from the "
        + "artifacts. Without collections or --all, only collections.json and plugins.jsonl are made again.",
    )
    rebuild.add_argument(
        "targets",
        nargs="*",
        metavar="NS.NAME[:VERSION]",
        help="a collection, for every version of it, or one version as NS.NAME:VERSION",
    )
    rebuild.add_argument("--all", action="store_true", help="every collection, in place of naming them")
    rebuild.add_argument(
        "--files",
        action="append",
        choices=("index", "plugins"),
        help="what to make again, index files and collections.json, or plugins.jsonl, both by default. Give it "
        + "twice for both",
    )
    rebuild.add_argument(
        "--from-artifacts",
        action="store_true",
        help="read each version's deps, requires_ansible and size, and extract its plugins, from its artifact, "
        + "rather than from what the index knows",
    )
    rebuild.add_argument(
        "--allow-changes",
        action="store_true",
        help="with --from-artifacts, write deps, requires_ansible or size that differ from the index file's, which "
        + "are meant to stay as they were published",
    )
    rebuild.set_defaults(func=_rebuild)

    completion = commands.add_parser(
        "generate-shell-completion",
        help="print the script that enables Tab completion in a shell",
        description="Print the script that enables Tab completion for alai-index in SHELL.",
    )
    completion.add_argument("shell", choices=SHELLS, help="the shell to print the script for")
    completion.set_defaults(func=_generate_shell_completion)
    return parser

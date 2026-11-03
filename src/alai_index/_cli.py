# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)
# PYTHON_ARGCOMPLETE_OK: lets argcomplete's global completion run alai-index for completions.

"""``alai-index COMMAND``: the command line."""

from __future__ import annotations

import argparse
import collections.abc as _c
import json
import logging
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
    if getattr(args, "quiet", False) and getattr(args, "verbose", 0):
        parser.error("-q/--quiet and -v/--verbose can't be used together")
    _logging(-1 if getattr(args, "quiet", False) else getattr(args, "verbose", 0))

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
        plugin_cache=args.plugin_cache,
        dl=args.dl,
        index=args.index,
        dl_template=args.dl_template,
        listing=args.listing,
        plugins=args.plugins,
    )
    return 0


def _open(args: argparse.Namespace) -> Index:
    from . import _index as index

    return index.Index(args.root, plugin_cache=args.plugin_cache, dl=args.dl)


TEXT = {
    "added": "added {what}",
    "changed": "updated {what}",
    "unchanged": "{what} is unchanged",
    "removed": "removed {what}",
    "extracted": "re-extracted {what}",
    "conflict": "{what} is served with another artifact, which isn't written",
    "failed": "{what} failed",
    "deferred": "{what} is left for a later run",
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


class _Progress(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        return f"debug: {message}" if record.levelno <= logging.DEBUG else message


def _logging(verbosity: int) -> None:
    """Progress to stderr as it happens, unless quiet, and each request and decision too with ``-v``."""
    logger = logging.getLogger("alai_index")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Progress())
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.DEBUG if verbosity > 0 else logging.INFO if verbosity == 0 else logging.ERROR)


def _text(change: Change) -> str:
    what = change.name if change.version is None else f"{change.name} {change.version}"
    if change.result == "changed" and change.details:
        words = {("yanked", True): "yanked", ("yanked", False): "unyanked"}
        words |= {("deprecated", True): "deprecated", ("deprecated", False): "undeprecated"}
        if all(k in ("yanked", "deprecated") for k in change.details):
            return " and ".join(words[k, bool(v)] for k, v in change.details.items()) + f" {what}"
        # A line made again from its artifact, whose fields can be any.
        return f"updated {', '.join(change.details)} of {what}"
    return TEXT[change.result].format(what=what)


def _finish(args: argparse.Namespace, tree: Index, results: list[Change], more: _c.Sequence[str] = ()) -> int:
    """Print the warnings to stderr, and what changed to stdout, as text, with ``more`` lines after, or as JSON.

    The JSON only ever gains keys, and a key never changes meaning, so it has no version.
    """
    if not args.quiet:
        for warning in tree.warnings:
            _report("warning", YELLOW, warning)
    if args.output_format == "json":
        versions = [
            {"name": c.name, "version": c.version, "result": c.result, "details": c.details or {}} for c in results
        ]
        print(json.dumps({"versions": versions, **tree.files}))
    else:
        outdated = [
            f"{o['name']} {o['version']}'s plugins are out of date, as its artifact isn't on disk to extract them again"
            for o in tree.files["outdated"]
        ]
        for line in [_text(c) for c in results] + outdated + list(more):
            print(line)
    return 0


def _add(args: argparse.Namespace) -> int:
    tree = _open(args)
    skip = args.skip_checks or ()
    return _finish(args, tree, tree.add(args.tarballs, published=args.published, skip=skip))


def _set(args: argparse.Namespace) -> int:
    tree = _open(args)
    results = tree.set(args.targets, yanked=args.yanked, deprecated=args.deprecated)
    return _finish(args, tree, results)


def _remove(args: argparse.Namespace) -> int:
    tree = _open(args)
    return _finish(args, tree, tree.remove(args.targets))


def _generate_indexes(args: argparse.Namespace) -> int:
    tree = _open(args)
    results = tree.generate_indexes(args.targets)
    written = [f"wrote {path}" for path in tree.files["written"]["index"]]
    return _finish(args, tree, results, written if results or written else ["nothing changed"])


def _generate_plugins(args: argparse.Namespace) -> int:
    tree = _open(args)
    results = tree.generate_plugins(args.targets, from_plugin_cache=args.from_plugin_cache)
    written = [f"wrote {path}" for path in tree.files["written"]["index"] if path != "collections.json"]
    return _finish(args, tree, results, written if results or written else ["nothing changed"])


def _galaxy_mirror(args: argparse.Namespace) -> int:
    from . import __version__
    from ._galaxy import GalaxyClient

    tree = _open(args)
    client = GalaxyClient(args.server, user_agent=f"alai-index/{__version__} (+https://github.com/alai-sh/alai-index)")
    results = tree.galaxy_mirror(
        client,
        args.targets,
        everything=args.all,
        limit=args.limit,
        revalidate=args.revalidate,
        session=args.session,
        checked=args.checked,
    )
    _finish(args, tree, results)
    return EXIT_ERROR if args.strict and any(c.result == "failed" for c in results) else 0


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


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"{value} isn't a positive number")
    return number


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
        "-q", "--quiet", action="store_true", help="only show errors, not progress or warnings, on stderr"
    )
    common.add_argument(
        "-v", "--verbose", action="count", default=0, help="say more on stderr, such as every request to a server"
    )
    common.add_argument(
        "--plugin-cache",
        metavar="DIR",
        help="the directory of the plugin cache, which is never published and can always be made again from the "
        + "artifacts, ROOT/plugin-cache by default",
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
        parents=[changes],
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
        parents=[changes],
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
        parents=[changes],
        help="take versions down",
        description="Take versions down, deleting their lines and artifacts. It's refused if a version that stays, and "
        + "isn't yanked, would be left without a version it needs. Yank those first, or remove them too.",
    )
    remove.add_argument("targets", nargs="+", metavar="NS.NAME[:VERSION]", help=target_help)
    remove.set_defaults(func=_remove)

    indexes = commands.add_parser(
        "generate-indexes",
        parents=[changes],
        help="make index lines again from the artifacts",
        description="Make index lines again from the artifacts, as add makes them, keeping each version's published, "
        + "yanked and deprecated. It's for when alai-index reads artifacts differently than it did when a version was "
        + "added. The artifacts must be on disk. Without collections, it's every collection.",
    )
    indexes.add_argument("targets", nargs="*", metavar="NS.NAME[:VERSION]", help=target_help)
    indexes.set_defaults(func=_generate_indexes)

    generate = commands.add_parser(
        "generate-plugins",
        parents=[changes],
        help="extract plugins again and work plugins.jsonl out again",
        description="Extract plugins again from the artifacts, and work plugins.jsonl out again. The artifacts must "
        + "be on disk. Without collections, it's every collection, and plugins.jsonl is made from nothing.",
    )
    generate.add_argument("targets", nargs="*", metavar="NS.NAME[:VERSION]", help=target_help)
    generate.add_argument(
        "--from-plugin-cache",
        action="store_true",
        help="work plugins.jsonl out from the plugin cache, so only an extraction that's missing or out of date needs "
        + "its artifact. One whose artifact isn't on disk is listed as outdated",
    )
    generate.set_defaults(func=_generate_plugins)

    mirror = commands.add_parser(
        "galaxy-mirror",
        parents=[changes],
        help="bring collections in line with a Galaxy server",
        description="Bring collections in line with a Galaxy server of your own, through the v3 API ansible-galaxy "
        + "uses, as Private Automation Hub, galaxy_ng and Artifactory serve it. Don't use it for galaxy.ansible.com, "
        + "which index.alai.sh already mirrors, and which is too large and slow to mirror this way. Versions the "
        + "server has that the index doesn't are added as published, a version is yanked only once its own page is "
        + "gone, and a version the server serves with another artifact is reported, never written. A version that "
        + "fails is reported, and the rest go on.",
    )
    mirror.add_argument("server", metavar="SERVER", help="the server, as ansible-galaxy is given it")
    mirror.add_argument("targets", nargs="*", metavar="NS.NAME", help="a collection")
    mirror.add_argument(
        "--all", action="store_true", help="every collection the server lists or the index has, in place of naming them"
    )
    mirror.add_argument(
        "--limit",
        type=_positive,
        metavar="N",
        help="download at most N versions, leaving the rest for a later run, so a long mirror is written as it goes",
    )
    mirror.add_argument(
        "--revalidate",
        action="store_true",
        help="check every version the index has against the server's sha256, not only those that look changed",
    )
    mirror.add_argument(
        "--session",
        metavar="FILE",
        help="carry on from FILE if it's there, and write to it what's needed to carry on a mirror --limit leaves part "
        + "done, so the next run only downloads. Give the same FILE to each run. Its contents are internal",
    )
    mirror.add_argument(
        "--checked",
        metavar="FILE",
        help="a JSON file of the server's created_at already checked for versions whose created_at differs from "
        + "published, so a version the server serves with another artifact isn't checked again on every run. It's "
        + 'kept by the caller, as {"ns.name": {"version": "created_at"}}',
    )
    mirror.add_argument("--strict", action="store_true", help="exit 1 if any version failed")
    mirror.set_defaults(func=_galaxy_mirror)

    completion = commands.add_parser(
        "generate-shell-completion",
        help="print the script that enables Tab completion in a shell",
        description="Print the script that enables Tab completion for alai-index in SHELL.",
    )
    completion.add_argument("shell", choices=SHELLS, help="the shell to print the script for")
    completion.set_defaults(func=_generate_shell_completion)
    return parser

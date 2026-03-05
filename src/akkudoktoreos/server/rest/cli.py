import argparse

from akkudoktoreos.utils.stringutil import str2bool


def cli_argument_parser() -> argparse.ArgumentParser:
    """Build argument parser for EOS cli."""
    parser = argparse.ArgumentParser(description="Start EOS server.")

    parser.add_argument(
        "--host",
        type=str,
        help="Host for the EOS server (default: value from config)",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Port for the EOS server (default: value from config)",
    )
    parser.add_argument(
        "--log_level",
        type=str,
        default="none",
        help='Log level for the server console. Options: "critical", "error", "warning", "info", "debug", "trace" (default: "none")',
    )
    parser.add_argument(
        "--reload",
        type=str2bool,
        default=False,
        help="Enable or disable auto-reload. Useful for development. Options: True or False (default: False)",
    )
    parser.add_argument(
        "--startup_eosdash",
        type=str2bool,
        default=None,
        help="Enable or disable automatic EOSdash startup. Options: True or False (default: value from config)",
    )
    parser.add_argument(
        "--run_as_user",
        type=str,
        help="The unprivileged user account the EOS server shall switch to after performing root-level startup tasks.",
    )
    return parser


def cli_parse_args(
    argv: list[str] | None = None,
) -> tuple[argparse.Namespace, list[str]]:
    """Parse command-line arguments for the EOS CLI.

    This function parses known EOS-specific command-line arguments and
    returns any remaining unknown arguments unmodified. Unknown arguments
    can be forwarded to other subsystems (e.g. Uvicorn).

    If ``argv`` is ``None``, arguments are read from ``sys.argv[1:]``.
    If ``argv`` is provided, it is used instead.

    Args:
        argv: Optional list of command-line arguments to parse. If omitted,
            the arguments are taken from ``sys.argv[1:]``.

    Returns:
        A tuple containing:
        - A namespace with parsed EOS CLI arguments.
        - A list of unparsed (unknown) command-line arguments.
    """
    args, args_unknown = cli_argument_parser().parse_known_args(argv)
    return args, args_unknown

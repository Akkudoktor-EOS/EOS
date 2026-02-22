import argparse

from loguru import logger

from akkudoktoreos.core.coreabc import get_config
from akkudoktoreos.core.logabc import LOGGING_LEVELS
from akkudoktoreos.server.server import get_default_host
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


def cli_apply_args_to_config(args: argparse.Namespace) -> None:
    """Apply parsed CLI arguments to the EOS configuration.

    This function updates the EOS configuration with values provided via
    the command line. For each parameter, the precedence is:

        CLI argument > existing config value > default value

    Currently handled arguments:

        - log_level: Updates "logging/console_level" in config.
        - host: Updates "server/host" in config.
        - port: Updates "server/port" in config.
        - startup_eosdash: Updates "server/startup_eosdash" in config.
        - eosdash_host/port: Initialized if EOSdash is enabled and not already set.

    Args:
        args: Parsed command-line arguments from argparse.
    """
    config_eos = get_config()

    # Setup parameters from args, config_eos and default
    # Remember parameters in config

    # Setup EOS logging level - first to have the other logging messages logged
    if args.log_level is not None:
        log_level = args.log_level.upper()
        # Ensure log_level from command line is in config settings
        if log_level in LOGGING_LEVELS:
            # Setup console logging level using nested value
            # - triggers logging configuration by logging_track_config
            config_eos.set_nested_value("logging/console_level", log_level)
            logger.debug(f"logging/console_level configuration set by argument to {log_level}")

    # Setup EOS server host
    if args.host:
        host = args.host
        logger.debug(f"server/host configuration set by argument to {host}")
    elif config_eos.server.host:
        host = config_eos.server.host
    else:
        host = get_default_host()
    # Ensure host from command line is in config settings
    config_eos.set_nested_value("server/host", host)

    # Setup EOS server port
    if args.port:
        port = args.port
        logger.debug(f"server/port configuration set by argument to {port}")
    elif config_eos.server.port:
        port = config_eos.server.port
    else:
        port = 8503
    # Ensure port from command line is in config settings
    config_eos.set_nested_value("server/port", port)

    # Setup EOSdash startup
    if args.startup_eosdash is not None:
        # Ensure startup_eosdash from command line is in config settings
        config_eos.set_nested_value("server/startup_eosdash", args.startup_eosdash)
        logger.debug(
            f"server/startup_eosdash configuration set by argument to {args.startup_eosdash}"
        )

    if config_eos.server.startup_eosdash:
        # Ensure EOSdash host and port config settings are at least set to default values

        # Setup EOS server host
        if config_eos.server.eosdash_host is None:
            config_eos.set_nested_value("server/eosdash_host", host)

        # Setup EOS server host
        if config_eos.server.eosdash_port is None:
            config_eos.set_nested_value("server/eosdash_port", port + 1)

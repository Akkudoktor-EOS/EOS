import sys

MIN_VERSION = (3, 11)

if sys.version_info < MIN_VERSION:
    min_version = ".".join(map(str, MIN_VERSION))
    raise RuntimeError(
        f"EOS requires Python {min_version} or newer. "
        f"Current version: {sys.version.split()[0]}. "
        f"Please upgrade your Python installation or Home Assistant."
    )

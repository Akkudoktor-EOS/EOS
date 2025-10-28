from typing import Optional, Union

import requests
from loguru import logger
from monsterui.daisy import Loading, LoadingT
from monsterui.franken import A, ButtonT, DivFullySpaced, P
from requests.exceptions import RequestException

import akkudoktoreos.server.dash.eosstatus as eosstatus
from akkudoktoreos.config.config import get_config

config_eos = get_config()


def get_alive(eos_host: str, eos_port: Union[str, int]) -> str:
    """Fetch alive information from the specified EOS server.

    Args:
        eos_host (str): The hostname of the server.
        eos_port (Union[str, int]): The port of the server.

    Returns:
        str: Alive data.
    """
    global eos_health
    result = requests.Response()
    try:
        result = requests.get(f"http://{eos_host}:{eos_port}/v1/health", timeout=10)
        if result.status_code == 200:
            eosstatus.eos_health = result.json()
            alive = eosstatus.eos_health["status"]
        else:
            eosstatus.eos_health = None
            alive = f"Server responded with status code: {result.status_code}"
    except RequestException as e:
        warning_msg = f"{e}"
        logger.warning(warning_msg)
        alive = warning_msg

    return alive


def Footer(eos_host: Optional[str], eos_port: Optional[Union[str, int]]) -> str:
    if eos_host is None:
        eos_host = config_eos.server.host
    if eos_port is None:
        eos_port = config_eos.server.port
    alive_icon = None
    if eos_host is None or eos_port is None:
        alive = "EOS server not given: {eos_host}:{eos_port}"
    else:
        alive = get_alive(eos_host, eos_port)
        if alive == "alive":
            alive_icon = Loading(
                cls=(
                    LoadingT.ring,
                    LoadingT.sm,
                ),
            )
            alive = f"EOS {eos_host}:{eos_port}"
    if alive_icon:
        alive_cls = f"{ButtonT.primary} uk-link rounded-md"
    else:
        alive_cls = f"{ButtonT.secondary} uk-link rounded-md"
    return DivFullySpaced(
        P(
            alive_icon,
            A(alive, href=f"http://{eos_host}:{eos_port}/docs", target="_blank", cls=alive_cls),
        ),
        P(
            A(
                "Documentation",
                href="https://akkudoktor-eos.readthedocs.io/en/latest/",
                target="_blank",
                cls="uk-link",
            ),
        ),
        P(
            A(
                "Issues",
                href="https://github.com/Akkudoktor-EOS/EOS/issues",
                target="_blank",
                cls="uk-link",
            ),
        ),
        P(
            A(
                "GitHub",
                href="https://github.com/Akkudoktor-EOS/EOS/",
                target="_blank",
                cls="uk-link",
            ),
        ),
        cls="uk-padding-remove-top uk-padding-remove-botton",
    )

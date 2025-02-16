from typing import Any

from fasthtml.common import Div

from akkudoktoreos.server.dash.markdown import Markdown

hello_md = """![Logo](/eosdash/assets/logo.png)

# Akkudoktor EOSdash

The dashboard for Akkudoktor EOS.

EOS provides a comprehensive solution for simulating and optimizing an energy system based
on renewable energy sources. With a focus on photovoltaic (PV) systems, battery storage (batteries),
load management (consumer requirements), heat pumps, electric vehicles, and consideration of
electricity price data, this system enables forecasting and optimization of energy flow and costs
over a specified period.

Documentation can be found at [Akkudoktor-EOS](https://akkudoktor-eos.readthedocs.io/en/latest/).
"""


def Hello(**kwargs: Any) -> Div:
    return Markdown(hello_md, **kwargs)

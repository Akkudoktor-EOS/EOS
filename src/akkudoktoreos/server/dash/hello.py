from fasthtml.common import Div

from akkudoktoreos.server.dash.components import Markdown, ScrollArea

hello_md = """# Akkudoktor EOSdash

The dashboard for Akkudoktor EOS.

EOS provides a comprehensive solution for simulating and optimizing an energy system based
on renewable energy sources. With a focus on photovoltaic (PV) systems, battery storage (batteries),
load management (consumer requirements), heat pumps, electric vehicles, and consideration of
electricity price data, this system enables forecasting and optimization of energy flow and costs
over a specified period.

Documentation can be found at [Akkudoktor-EOS](https://akkudoktor-eos.readthedocs.io/en/latest/).
"""


def Hello() -> Div:
    return ScrollArea(
        Markdown(hello_md),
        cls="h-[75vh] w-full rounded-md",
    )

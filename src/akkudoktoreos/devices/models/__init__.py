"""Device models of the optimizer kernel.

One module per device type. Each holds a lean, immutable parameter structure that is
flattened into numpy arrays for the compiled step functions of the same module:

- ``storage``: house battery and electric vehicle (same type)
- ``inverter``: hybrid inverter with efficiency curves and limits
- ``appliance``: household appliance with a load profile from its start slot
"""

% SPDX-License-Identifier: Apache-2.0

# Configuration

The configuration controls all aspects of EOS: optimization, prediction, measurement, and energy
management.

## Storing Configuration

EOS stores configuration data in a `nested structure`. Note that configuration changes inside EOS
are updated in memory, meaning all changes will be lost upon restarting the EOS REST server if not
saved to the `EOS configuration file`.

Some `configuration keys` are read-only and cannot be altered. These keys are either set up by other
means, such as environment variables, or determined from other information.

Several endpoints of the EOS REST server allow for the management and retrieval of configuration
data.

### Save Configuration File

Use endpoint `PUT /v1/config/file` to save the current configuration to the
`EOS configuration file`.

### Load Configuration File

Use endpoint `POST /v1/config/reset` to reset the configuration to the values in the
`EOS configuration file`.

## Configuration Sources and Priorities

The configuration sources and their priorities are as follows:

1. **Runtime Config Updates**: Provided during runtime by the REST interface
2. **Environment Variables**: Defined at startup of the REST server and during runtime
3. **EOS Configuration File**: Read at startup of the REST server and on request
4. **Default Values**

### Runtime Config Updates

The EOS configuration can be updated at runtime. Note that those updates are not persistet
automatically. However it is possible to save the configuration to the `EOS configuration file`.

Use the following endpoints to change the current runtime configuration:

- `PUT /v1/config`: Update the entire or parts of the configuration.

### Environment Variables

All `configuration keys` can be set by environment variables prefixed with `EOS_` and separated by
`__` for nested structures. Environment variables are case insensitive.

EOS recognizes the following special environment variables (case sensitive):

- `EOS_CONFIG_DIR`: The directory to search for an EOS configuration file.
- `EOS_DIR`: The directory used by EOS for data, which will also be searched for an EOS
             configuration file.

### EOS Configuration File

The EOS configuration file provides persistent storage for configuration data. It can be modified
directly or through the REST interface.

If you do not have a configuration file, it will be automatically created on the first startup of
the REST server in a system-dependent location.

To determine the location of the configuration file used by EOS, ask the REST server. The endpoint
`GET /v1/config` provides the `general.config_file_path` configuration key.

EOS searches for the configuration file in the following order:

1. The directory specified by the `EOS_CONFIG_DIR` environment variable
2. The directory specified by the `EOS_DIR` environment variable
3. A platform-specific default directory for EOS
4. The current working directory

The first configuration file available in these directories is loaded. If no configuration file is
found, a default configuration file is created, and the default settings are written to it. The
location of the created configuration file follows the same order in which EOS searches for
configuration files, and it depends on whether the relevant environment variables are set.

Use the following endpoints to interact with the configuration file:

- `PUT /v1/config/file`: Save the current configuration to the configuration file.
- `PUT /v1/config/reset`: Reload the configuration file, all unsaved runtime configuration is reset.

### Default Values

Some of the `configuration keys` have default values by definition. For most of the
`configuration keys` the default value is just `None`, which means no default value.

```{include} /_generated/config.md
:heading-offset: 1
:relative-docs: ..
:relative-images:
```

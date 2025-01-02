% SPDX-License-Identifier: Apache-2.0

# Configuration

The configuration controls all aspects of EOS: optimization, prediction, measurement, and energy
management.

## Storing Configuration

EOS stores configuration data in a **key-value store**, where a `configuration key` refers to the
unique identifier used to store and retrieve specific configuration data. Note that the key-value
store is memory-based, meaning all stored data will be lost upon restarting the EOS REST server if
not saved to the `EOS configuration file`.

Some `configuration keys` are read-only and cannot be altered. These keys are either set up by other
means, such as environment variables, or determined from other information.

Several endpoints of the EOS REST server allow for the management and retrieval of configuration
data.

### Save Configuration File

Use endpoint `PUT /v1/config/file` to save the current configuration to the
`EOS configuration file`.

### Load Configuration File

Use endpoint `POST /v1/config/update` to update the configuration from the `EOS configuration file`.

## Configuration Sources and Priorities

The configuration sources and their priorities are as follows:

1. **Settings**: Provided during runtime by the REST interface
2. **Environment Variables**: Defined at startup of the REST server and during runtime
3. **EOS Configuration File**: Read at startup of the REST server and on request
4. **Default Values**

### Settings

Settings are sets of configuration data that take precedence over all other configuration data from
different sources. Note that settings are not persistent. To make the current configuration with the
current settings persistent, save the configuration to the `EOS configuration file`.

Use the following endpoints to change the current configuration settings:

- `PUT /v1/config`: Replaces the entire configuration settings.
- `PUT /v1/config/value`: Sets a specific configuration option.

### Environment Variables

All `configuration keys` can be set by environment variables with the same name. EOS recognizes the
following special environment variables:

- `EOS_CONFIG_DIR`: The directory to search for an EOS configuration file.
- `EOS_DIR`: The directory used by EOS for data, which will also be searched for an EOS
             configuration file.
- `EOS_LOGGING_LEVEL`: The logging level to use in EOS.

### EOS Configuration File

The EOS configuration file provides persistent storage for configuration data. It can be modified
directly or through the REST interface.

If you do not have a configuration file, it will be automatically created on the first startup of
the REST server in a system-dependent location.

To determine the location of the configuration file used by EOS, ask the REST server. The endpoint
`GET /v1/config` provides the `config_file_path` configuration key.

EOS searches for the configuration file in the following order:

1. The directory specified by the `EOS_CONFIG_DIR` environment variable
2. The directory specified by the `EOS_DIR` environment variable
3. A platform-specific default directory for EOS
4. The current working directory

The first available configuration file found in these directories is loaded. If no configuration
file is found, a default configuration file is created in the platform-specific default directory,
and default settings are loaded into it.

### Default Values

Some of the `configuration keys` have default values by definition. For most of the
`configuration keys` the default value is just `None`, which means no default value.

```{eval-sh}
./scripts/generate_config_md.py | ./scripts/extract_markdown.py --input-stdin --heading-level 1
```

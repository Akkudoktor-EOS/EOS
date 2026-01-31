# Changelog

All notable changes to the akkudoktoreos project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.3.0 (2025-12-??)

Adapters for Home Assistant and NodeRed integration are added. These adapters
provide a simplified interface to these HEMS besides the standard REST interface.
Akkudoktor-EOS can now be run as Home Assistant add-on and standalone.
As Home Assistant add-on EOS uses ingress to fully integrate the EOSdash dashboard
in Home Assistant.

In addition, bugs were fixed and new features were added.

### Feat

- add adapters for integrations

  Adapters for Home Assistant and NodeRED integration are added.
  Akkudoktor-EOS can now be run as Home Assistant add-on and standalone.

  As Home Assistant add-on EOS uses ingress to fully integrate the EOSdash dashboard
  in Home Assistant.

- allow eos to be started with root permissions and drop priviledges

  Home assistant starts all add-ons with root permissions. Eos now drops
  root permissions if an applicable user is defined by paramter --run_as_user.
  The docker image defines the user eos to be used.

- make eos supervise and monitor EOSdash

  Eos now not only starts EOSdash but also monitors EOSdash during runtime
  and restarts EOSdash on fault. EOSdash logging is captured by EOS
  and forwarded to the EOS log to provide better visibility.

- add duration to string conversion

  Make to_duration to also return the duration as string on request.

### Fixed

- development version scheme

  The development versioning scheme is adaptet to fit to docker and
  home assistant expectations. The new scheme is x.y.z and x.y.z.dev<hash>.
  Hash is only digits as expected by home assistant. Development version
  is appended by .dev as expected by docker.

- use mean value in interval on resampling for array

  When downsampling data use the mean value of all values within the new
  sampling interval.

- default battery ev soc and appliance wh

  Make the genetic simulation return default values for the
  battery SoC, electric vehicle SoC and appliance load if these
  assets are not used.

- import json string

  Strip outer quotes from JSON strings on import to be compliant to json.loads()
  expectation.

- default interval definition for import data

  Default interval must be defined in lowercase human definition to
  be accepted by pendulum.

- clearoutside schema change

### Chore

- Use info logging to report missing optimization parameters

  In parameter preparation for automatic optimization an error was logged for missing paramters.
  Log is now down using the info level.

- make EOSdash use the EOS data directory for file import/ export

  EOSdash use the EOS data directory for file import/ export by default.
  This allows to use the configuration import/ export function also
  within docker images.

- improve EOSdash config tab display

  Improve display of JSON code and add more forms for config value update.

- make docker image file system layout similar to home assistant

  Only use /data directory for persistent data. This is handled as a
  docker volume. The /data volume is mapped to ~/.local/share/net.akkudoktor.eos
  if using docker compose.

- add home assistant add-on development environment

  Add VSCode devcontainer and task definition for home assistant add-on
  development.

- improve documentation

## 0.2.0 (2025-11-09)

The most important new feature is **automatic optimization**.
EOS can now independently perform optimization at regular intervals.
This is based on the configured system parameters and forecasts, and also uses supplied
measurement data, such as the current battery SoC.
The result is an energy-management plan as well as the optimization output.
The existing optimization interface using `POST /optimize` remains available and can still
be used as before.

In addition, bugs were fixed and new features were added:

- Automatic optimization creates a **default configuration** if none is provided.
  This is intended to make it easier to create a custom configuration by adapting the default.
- The parameters of the genetic optimization algorithm (number of generations, etc.) are now
  configurable.
- For home appliances, start windows can now be specified (experimental).
- Configuration files from previous versions are converted to the current format on first launch.
- There are now measurement keys that are permanently assigned to a specific device simulation.
  This simplifies providing measurement values for device simulations (e.g. battery SoC).
- The infrastructure and first applications for **feed-in tariff forecasting**
  (currently only fixed tariffs) are now integrated.
- EOSdash has been expanded with new tabs for displaying the **energy-management plan**
  and **predictions**.
- The documentation has been updated and expanded in many places.

### Feat

- Energy-management plan generation based on S2 standard instructions
- Feed-in-tariff prediction support (incl. tests & docs)
- `LoadAkkudoktorAdjusted` load prediction variant
- Standardized measurement keys for battery/EV SoC
- Measurement keys configurable via EOS configuration
- Setup default device configuration for automatic optimization
- Health endpoints show version + last optimization timestamps
- Configuration of genetic algorithm parameters
- Configuration options for home-appliance time windows
- Mitigation of legacy configuration
- Config backup enhancements:

  - Timestamp-based backup IDs
  - API to list backups
  - API to revert to a specific backup
  - EOSdash Admin tab integration

- Pendulum date types via `pydantic_extra_types.pendulum_dt`
- `Time`, `TimeWindow`, `TimeWindowSequence`, and `to_time` helpers in `datetimeutil`
- Extended `DataRecord` with configurable field-like semantics
- EOSdash: Solution view now displays genetic optimization results and aggregated totals
- EOSdash UI:

  - Plan tab
  - Predictions tab
  - Cache management in Admin tab
  - About tab

- Pydantic merge model tests
- Developer profiling entry in Makefile
- Changelog & docs updated for commitizen release flow
- Developer documentation updated
- Improved install & development documentation

### Changed

- Battery simulation

  - Performance improvements
  - Charge + start times now reflect realistic simulation

- Appliance simulation:

  - Time windows may roll over to next day

- Revised load prediction by splitting original `LoadAkkudoktor` into:

  - `LoadAkkudoktor`
  - `LoadAkkudoktorAdjusted`

### Fixed

- Correct URL/path for Akkudoktor forum in README
- Automatic optimization:

  - Reuses previous start solution
  - Interval execution + locking + new endpoints
  - Properly loads required data
  - EV charge-rate migration for proper availability

- Genetic common settings consistently available
- Config markdown generation
- Recognize environment variables on EOS server startup
- Remove `0.0.0.0 → localhost` translation on Windows
- Allow hostnames as well as IPs
- Access Pydantic model fields via class instead of instance
- Down-sampling in `key_to_array`
- `/v1/admin/cache/clear` clears all cache files; added `/clear-expired`
- Use `tzfpy` instead of timezonefinder for more accurate EU timezones
- Explicit provider settings in config instead of union
- ClearOutside weather prediction irradiance calculation
- Test config file priority without `config_eos` fixture
- Complete optimization sample-request documentation
- Replace gitlint with commitizen
- Synchronize pre-commit config with real dependencies
- Add missing `babel` to requirements
- Fix documentation, tests, and implementation around optimization + predictions

### Chore

- Use memory cache for inverter interpolation
- Refactor genetic modules (split config, remove device singleton)
- Rename memory cache to `CacheEnergyManagementStore`
- Use class properties for config/EMS/prediction mixins
- Skip matplotlib debug logs
- Auto-sync Bokeh JS CDN version
- Rename `hello.py` → `about.py` in EOSdash
- Remove EOSdash demo page
- Split server test from system test
- Move doc utils to `generate_config_md.py`
- Improve documentation for pydantic merge models
- Remove pendulum warning from README
- Drop GitHub Discussions from contributing docs
- Rename or reorganize files / classes during refactors

### BREAKING CHANGES

EOS configuration + v1 API have changed:

- `available_charge_rates_percent` removed → replaced by `charge_rate`
- Optimization parameter `hours` → renamed to `horizon_hours`
- Device config must explicitly list devices + properties
- Prediction providers now explicit (instead of union)
- Measurement keys provided as lists
- Feed-in-tariff providers must be explicitly configured
- `/v1/measurement/loadxxx` endpoints removed → use generic measurement endpoints
- `/v1/admin/cache/clear` now clears **all*- cache files;
  `/v1/admin/cache/clear-expired` only clears expired entries

## v0.1.0 (2025-09-30)

### Feat

- added Changelog for 0.0.0 and 0.1.0

## v0.0.0 (2025-09-30)

This version represents one year of development of EOS (Energy Optimization System). From this point forward, release management will be introduced.

### Feat

#### Core Features
- energy Management System (EMS) with battery optimization
- PV (Photovoltaic) forecast integration with multiple providers
- load prediction and forecasting capabilities
- electricity price integration
- VRM API integration for load and PV forecasting
- battery State of Charge (SoC) prediction and optimization
- inverter class with AC/DC charging logic
- electric vehicle (EV) charging optimization with configurable currents
- home appliance scheduling optimization
- horizon validation for shading calculations

#### API & Server
- migration from Flask to FastAPI
- RESTful API with comprehensive endpoints
- EOSdash web interface for configuration and visualization
- Docker support with multi-architecture builds
- web-based visualization with interactive charts
- OpenAPI/Swagger documentation
- configurable server settings (port, host)

#### Configuration & Data Management
- JSON-based configuration system with nested support
- configuration validation with Pydantic
- device registry for managing multiple devices
- persistent caching for predictions and prices
- manual prediction updates
- timezone support with automatic detection
- configurable VAT rates for electricity prices

#### Optimization
- DEAP-based genetic algorithm optimization
- multi-objective optimization (cost, battery usage, self-consumption)
- 48-hour prediction and optimization window
- AC/DC charging decision optimization
- discharge hour optimization
- start solution enforcement
- fitness visualization with violin plots
- self-consumption probability interpolator

#### Testing & Quality
- comprehensive test suite with pytest
- unit tests for major components (EMS, battery, inverter, load, optimization)
- integration tests for server endpoints
- pre-commit hooks for code quality
- type checking with mypy
- code formatting with ruff and isort
- markdown linting

#### Documentation
- conceptual documentation
- API documentation with Sphinx
- ReadTheDocs integration
- Docker setup instructions
- contributing guidelines
- English README translation

#### Providers & Integrations
- PVForecast.Akkudoktor provider
- BrightSky weather provider
- ClearOutside weather provider
- electricity price provider

### Refactor

- optimized Inverter class for improved SCR calculation performance
- improved caching mechanisms for better performance
- enhanced visualization with proper timestamp handling
- updated dependency management with automatic Dependabot updates
- restructured code into logical submodules
- package directory structure reorganization
- improved error handling and logging
- Windows compatibility improvements

### Fix

- cross-site scripting (XSS) vulnerabilities
- ReDoS vulnerability in duration parsing
- timezone and daylight saving time handling
- BrightSky provider with None humidity data
- negative values in load mean adjusted calculations
- SoC calculation bugs
- AC charge efficiency in price calculations
- optimization timing bugs
- Docker BuildKit compatibility
- float value handling in user horizon configuration
- circular runtime import issues
- load simulation data return issues
- multiple optimization-related bugs

### Build

- Python version requirement updated to 3.10+
- added Bandit security checks
- improved credential management with environment variables

#### Dependencies
Major dependencies included in this release:
- FastAPI 0.115.14
- Pydantic 2.11.9
- NumPy 2.3.3
- Pandas 2.3.2
- Scikit-learn 1.7.2
- Uvicorn 0.36.0
- Bokeh 3.8.0
- Matplotlib 3.10.6
- PVLib 0.13.1
- Python-FastHTML 0.12.29

### Notes

#### Development Notes
This version encompasses all development from the initial commit (February 16, 2024) through September 29, 2025. The project evolved from a basic energy optimization concept to a comprehensive energy management system with:
- 698+ commits
- multiple contributor involvement
- continuous integration/deployment setup
- automated dependency updates
- comprehensive testing infrastructure

#### Migration Notes
As this is the initial versioned release, no migration is required. Future releases will include migration guides as needed.

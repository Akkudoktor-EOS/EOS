# Changelog

All notable changes to the akkudoktoreos project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.0+dev (2025-10-26)

### feat
- setup default device configuration for automatic optimization
- allow configuration of genetic algorithm parameters
- allow configuration of home appliance time windows
- mitigate old config
- standardize measurement keys for battery/EV SoC measurements
- feed-in tariff prediction support (incl. tests and docs)
- energy management plan generation based on S2 standard instructions
- make measurement keys configurable through EOS configuration
- use pendulum types with pydantic via pydantic_extra_types.pendulum_dt
- add Time, TimeWindow, TimeWindowSequence and to_time to datetimeutil
- extend DataRecord with configurable field-like data
- enrich health endpoints with version and optimization timestamps
- add pydantic merge model tests
- add plan tab to EOSdash
- add predictions tab to EOSdash
- add cache management to EOSdash admin tab
- add about tab to EOSdash
- adapt changelog & documentation for commitizen release flow
- improve install and development documentation

### fix
- automatic optimization (interval execution, locking, new endpoints)
- recognize environment variables on EOS server startup
- remove 0.0.0.0 → localhost translation on Windows
- allow hostnames as well as IPs
- access pydantic model fields via class instead of instance
- downsampling in key_to_array
- /v1/admin/cache/clear now clears all cache files; new /clear-expired endpoint
- replace timezonefinder with tzfpy for accurate European timezones
- explicit provider settings in config versus union
- ClearOutside weather prediction irradiance calculation
- test config file priority without config_eos fixture
- complete optimization sample request documentation
- replace gitlint with commitizen
- synchronize pre-commit config with real dependencies
- add missing babel to requirements
- fix documentation, tests, and implementation around optimization and predictions

### chore
- use memory cache for inverter interpolation
- refactor genetic algorithm modules (split config, remove device singleton)
- rename memory cache to CacheEnergyManagementStore
- use class properties for config/ems/prediction mixins
- skip matplotlib debug logs
- auto-sync Bokeh JS CDN version
- rename hello.py to about.py in EOSdash
- remove demo page from EOSdash
- split server test for system test
- move doc utils to generate_config_md.py
- improve documentation for pydantic merge models
- remove pendulum warning from README
- drop GitHub Discussions from contributing docs
- bump version to 0.1.0+dev
- rename or reorganize files/classes during refactoring

### build
- bump fastapi[standard] 0.115.14 → 0.117.1 and fix pytest-cov version
- bump uvicorn 0.36.0 → 0.37.0

### BREAKING CHANGE
EOS configuration and v1 API were changed:

- available_charge_rates_percent removed; replaced by new charge_rate config
- optimization param hours renamed to horizon_hours
- device config must now list devices and their properties explicitly
- specific prediction provider configuration versus union
- measurement keys provided as lists
- new feed-in tariff providers must be configured
- /v1/measurement/loadxxx endpoints removed (use generic measurement endpoints)
- /v1/admin/cache/clear clears all cache files; use /v1/admin/cache/clear-expired for expired-only clearing

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

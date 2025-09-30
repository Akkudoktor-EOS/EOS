# Changelog

All notable changes to the akkudoktoreos project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-09-30

### Added

- Added Changelog for 0.0.0 amd 0.1.0

## [0.0.0] - 2025-09-30

This version represents one year of development of EOS (Energy Optimization System). From this point forward, release management will be introduced.

### Added

#### Core Features
- Energy Management System (EMS) with battery optimization
- PV (Photovoltaic) forecast integration with multiple providers
- Load prediction and forecasting capabilities
- Electricity price integration
- VRM API integration for load and PV forecasting
- Battery State of Charge (SoC) prediction and optimization
- Inverter class with AC/DC charging logic
- Electric vehicle (EV) charging optimization with configurable currents
- Home appliance scheduling optimization
- Horizon validation for shading calculations

#### API & Server
- Migration from Flask to FastAPI
- RESTful API with comprehensive endpoints
- EOSdash web interface for configuration and visualization
- Docker support with multi-architecture builds
- Web-based visualization with interactive charts
- OpenAPI/Swagger documentation
- Configurable server settings (port, host)

#### Configuration & Data Management
- JSON-based configuration system with nested support
- Configuration validation with Pydantic
- Device registry for managing multiple devices
- Persistent caching for predictions and prices
- Manual prediction updates
- Timezone support with automatic detection
- Configurable VAT rates for electricity prices

#### Optimization
- DEAP-based genetic algorithm optimization
- Multi-objective optimization (cost, battery usage, self-consumption)
- 48-hour prediction and optimization window
- AC/DC charging decision optimization
- Discharge hour optimization
- Start solution enforcement
- Fitness visualization with violin plots
- Self-consumption probability interpolator

#### Testing & Quality
- Comprehensive test suite with pytest
- Unit tests for major components (EMS, battery, inverter, load, optimization)
- Integration tests for server endpoints
- Pre-commit hooks for code quality
- Type checking with mypy
- Code formatting with ruff and isort
- Markdown linting

#### Documentation
- Conceptual documentation
- API documentation with Sphinx
- ReadTheDocs integration
- Docker setup instructions
- Contributing guidelines
- English README translation

#### Providers & Integrations
- PVForecast.Akkudoktor provider
- BrightSky weather provider
- ClearOutside weather provider
- Electricity price provider

### Changed
- Python version requirement updated to 3.10+
- Optimized Inverter class for improved SCR calculation performance
- Improved caching mechanisms for better performance
- Enhanced visualization with proper timestamp handling
- Updated dependency management with automatic Dependabot updates
- Restructured code into logical submodules
- Package directory structure reorganization
- Improved error handling and logging
- Windows compatibility improvements

### Fixed
- Cross-site scripting (XSS) vulnerabilities
- ReDoS vulnerability in duration parsing
- Timezone and daylight saving time handling
- BrightSky provider with None humidity data
- Negative values in load mean adjusted calculations
- SoC calculation bugs
- AC charge efficiency in price calculations
- Optimization timing bugs
- Docker BuildKit compatibility
- Float value handling in user horizon configuration
- Circular runtime import issues
- Load simulation data return issues
- Multiple optimization-related bugs

### Security
- Added Bandit security checks
- Fixed XSS vulnerabilities
- Mitigated ReDoS attacks with input length validation
- Improved credential management with environment variables

### Dependencies
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

### Development Notes
This version encompasses all development from the initial commit (February 16, 2024) through September 29, 2025. The project evolved from a basic energy optimization concept to a comprehensive energy management system with:
- 698+ commits
- Multiple contributor involvement
- Continuous integration/deployment setup
- Automated dependency updates
- Comprehensive testing infrastructure

### Migration Notes
As this is the initial versioned release, no migration is required. Future releases will include migration guides as needed.

---

**Full Changelog**: Initial development phase (v0.0.0)


## v0.1.0-a0 (2025-09-30)

### BREAKING CHANGE

- This is a BREAKING CHANGE as the configuration structure changed
once again and the server API was also enhanced and streamlined. The server API
that is used by Andreas and Jörg in their videos has not changed
- This is a BREAKING CHANGE as the configuration structure changed
once again and the server API was also enhanced and streamlined. The server API
that is used by Andreas and Jörg in their videos has not changed.
- EOS configuration changed. V1 API changed.
- Default IP address for EOS and EOSdash changed to 127.0.0.1
- Azimuth configurations that followed the PVForecastAkkudoktor convention
(north=+-180, east=-90, south=0, west=90) must be converted to the general azimuth definition:
north=0, east=90, south=180, west=270.

### Feat

- **VRM forecast**: add load and pv forecast by VRM API (#611)
- run pytest for PRs
- be helpful, provide a list of valid routes when visiting /
- add documentation, enable makefile driven usage
- Detailliertere README
- andere ports/bind ips erlauben

### Fix

- dependencies and optimization solution beginning
- typos in bokeh.py
- automatic optimization
- handle float values in userhorizon configuration (#657)
- **docker**: make EOSDash accessible in Docker containers (#656)
- **ElecPriceEnergyCharts**: get history series, update docs (#606)
- logging, prediction update, multiple bugs (#584)
- add required fields to example optimization request (#574)
- pvforecast fails when there is only a single plane (#569)
- delete empty inverter from testdata optimize_input_2.json (#568)
- azimuth setting of pvforecastakkudoktor provider (#567)
- BrightSky with None humidity data (#555)
- Catch optimize error and return error message. (#534)
- Circular runtime import Closes #533 (#535)
- **docker**: enable BuildKit to support --mount (closes #493)
- mitigate ReDoS in to_duration via max input length check (closes #494) (#523)
- relax stale issue/pr handling
- remove verbose comment
- make port configurable via env

### Refactor

- remove `README-DE.md`

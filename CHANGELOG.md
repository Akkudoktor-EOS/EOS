# Changelog

All notable changes to the akkudoktoreos project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

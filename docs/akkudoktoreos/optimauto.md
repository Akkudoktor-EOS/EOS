% SPDX-License-Identifier: Apache-2.0

# Automatic Optimization

## Introduction

The `automatic optimization` optimizes your energy management system on configured intervalls. It
automatically retrieves inputs including electricity prices, battery storage capacity, PV forecast,
and temperature data based on the configuration you provided.

The `automatic optimization` is an alternative to the "classical" **POST** `/optimize` optimization
interface developed by Andreas at the start of the project. It is currently **not** used and
described in his videos.

## Configuration

TBD

## Providing your own prediction data

If EOS does not have a suitable prediction provider you can provide your own data for a prediction.
Configure the respective import provider (ElecPriceImport, LoadImport, PVForecastImport,
WeatherImport) and use one of the following endpoints to provide your own data:

- **PUT** `/v1/prediction/import/ElecPriceImport`
- **PUT** `/v1/prediction/import/LoadImport`
- **PUT** `/v1/prediction/import/PVForecastImport`
- **PUT** `/v1/prediction/import/WeatherImport`

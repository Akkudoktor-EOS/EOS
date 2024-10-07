from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
import pandas as pd
import os

from akkudoktoreos.class_load import LoadForecast
from akkudoktoreos.class_load_container import Gesamtlast
from akkudoktoreos.class_load_corrector import LoadPredictionAdjuster
from akkudoktoreos.class_optimize import isfloat, optimization_problem
from akkudoktoreos.class_pv_forecast import PVForecast
from akkudoktoreos.class_strompreis import HourlyElectricityPriceForecast
from akkudoktoreos.config import get_start_enddate, optimization_hours, prediction_hours

# Initialize FastAPI application
app = FastAPI(title="Energy Management System API", description="API for EMS operations", version="1.0")

# Set up CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

opt_class = optimization_problem(
    prediction_hours=prediction_hours, strafe=10, optimization_hours=optimization_hours
)

class LoadData(BaseModel):
    year_energy: float
    hours: int = 48
    measured_data: list

class OptimizationParams(BaseModel):
    preis_euro_pro_wh_akku: float
    strompreis_euro_pro_wh: float
    gesamtlast: list
    pv_akku_cap: float
    einspeiseverguetung_euro_pro_wh: float
    pv_forecast: list
    temperature_forecast: list
    eauto_min_soc: float
    eauto_cap: float
    eauto_charge_efficiency: float
    eauto_charge_power: float
    eauto_soc: float
    pv_soc: float
    start_solution: list
    haushaltsgeraet_dauer: float
    haushaltsgeraet_wh: float
    min_soc_prozent: float = None

@app.get("/strompreis")
async def get_strompreis():
    date_now, date = get_start_enddate(prediction_hours, startdate=datetime.now().date())
    price_forecast = HourlyElectricityPriceForecast(
        source=f"https://api.akkudoktor.net/prices?start={date_now}&end={date}",
        prediction_hours=prediction_hours,
    )
    specific_date_prices = price_forecast.get_price_for_daterange(date_now, date)
    return specific_date_prices.tolist()


@app.post("/gesamtlast")
async def calculate_gesamtlast(data: LoadData):
    measured_data = pd.DataFrame(data.measured_data)
    measured_data["time"] = pd.to_datetime(measured_data["time"]).dt.tz_localize("Europe/Berlin").dt.tz_localize(None)

    file_path = os.path.join("data", "load_profiles.npz")
    lf = LoadForecast(filepath=file_path, year_energy=data.year_energy)
    forecast_list = []

    for single_date in pd.date_range(measured_data["time"].min().date(), measured_data["time"].max().date()):
        daily_forecast = lf.get_daily_stats(single_date.strftime("%Y-%m-%d"))
        hours = [single_date + pd.Timedelta(hours=i) for i in range(24)]
        daily_forecast_df = pd.DataFrame({"time": hours, "Last Pred": daily_forecast[0]})
        forecast_list.append(daily_forecast_df)

    predicted_data = pd.concat(forecast_list, ignore_index=True)
    adjuster = LoadPredictionAdjuster(measured_data, predicted_data, lf)
    adjuster.calculate_weighted_mean()
    adjuster.adjust_predictions()
    future_predictions = adjuster.predict_next_hours(data.hours)
    gesamtlast = Gesamtlast(prediction_hours=data.hours)
    gesamtlast.hinzufuegen("Haushalt", future_predictions["Adjusted Pred"].values)

    last = gesamtlast.gesamtlast_berechnen()
    return last.tolist()


@app.get("/gesamtlast_simple")
async def get_gesamtlast_simple(year_energy: float):
    date_now, date = get_start_enddate(prediction_hours, startdate=datetime.now().date())
    file_path = os.path.join("data", "load_profiles.npz")
    lf = LoadForecast(filepath=file_path, year_energy=year_energy)
    leistung_haushalt = lf.get_stats_for_date_range(date_now, date)[0]
    gesamtlast = Gesamtlast(prediction_hours=prediction_hours)
    gesamtlast.hinzufuegen("Haushalt", leistung_haushalt)
    last = gesamtlast.gesamtlast_berechnen()
    return last.tolist()


@app.get("/pvforecast")
async def get_pvforecast(url: str, ac_power_measurement: float = None):
    date_now, date = get_start_enddate(prediction_hours, startdate=datetime.now().date())
    PVforecast = PVForecast(prediction_hours=prediction_hours, url=url)
    if isfloat(ac_power_measurement):
        PVforecast.update_ac_power_measurement(datetime.now(), float(ac_power_measurement))

    pv_forecast = PVforecast.get_pv_forecast_for_date_range(date_now, date)
    temperature_forecast = PVforecast.get_temperature_for_date_range(date_now, date)
    return {"temperature": temperature_forecast.tolist(), "pvpower": pv_forecast.tolist()}


@app.post("/optimize")
async def optimize(parameter: OptimizationParams):
    missing_params = [p for p in OptimizationParams.__annotations__.keys() if p not in parameter.dict()]
    if missing_params:
        raise HTTPException(status_code=400, detail=f"Missing parameter: {', '.join(missing_params)}")

    result = opt_class.optimierung_ems(parameter=parameter.dict(), start_hour=datetime.now().hour)
    return result


@app.get("/visualisierungsergebnisse.pdf")
async def get_pdf():
    if os.path.exists("visualisierungsergebnisse.pdf"):
        return RedirectResponse("visualisierungsergebnisse.pdf")
    else:
        raise HTTPException(status_code=404, detail="PDF file not found")


@app.get("/site-map")
async def site_map(request: Request):
    routes = [{"path": route.path, "name": route.name} for route in app.routes]
    return JSONResponse(content=routes)


@app.get("/")
async def root():
    return RedirectResponse(url="/site-map")

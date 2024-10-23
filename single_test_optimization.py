#!/usr/bin/env python3

import time

import numpy as np

from akkudoktoreos.class_numpy_encoder import NumpyEncoder

# Import necessary modules from the project
from akkudoktoreos.class_optimize import optimization_problem
from akkudoktoreos.visualize import visualisiere_ergebnisse

start_hour = 0

# PV Forecast (in W)
pv_forecast = np.zeros(48)
pv_forecast[12] = 5000
# [
#     0,
#     0,
#     0,
#     0,
#     0,
#     0,
#     0,
#     8.05,
#     352.91,
#     728.51,
#     930.28,
#     1043.25,
#     1106.74,
#     1161.69,
#     1018.82,
#     1519.07,
#     1969.88,
#     1017.96,
#     1043.07,
#     1007.17,
#     319.67,
#     7.88,
#     0,
#     0,
#     0,
#     0,
#     0,
#     0,
#     0,
#     0,
#     0,
#     5.04,
#     335.59,
#     705.32,
#     1121.12,
#     1604.79,
#     2157.38,
#     1433.25,
#     5718.49,
#     4553.96,
#     3027.55,
#     2574.46,
#     1720.4,
#     963.4,
#     383.3,
#     0,
#     0,
#     0,
# ]

# Temperature Forecast (in degree C)
temperature_forecast = [
    18.3,
    17.8,
    16.9,
    16.2,
    15.6,
    15.1,
    14.6,
    14.2,
    14.3,
    14.8,
    15.7,
    16.7,
    17.4,
    18.0,
    18.6,
    19.2,
    19.1,
    18.7,
    18.5,
    17.7,
    16.2,
    14.6,
    13.6,
    13.0,
    12.6,
    12.2,
    11.7,
    11.6,
    11.3,
    11.0,
    10.7,
    10.2,
    11.4,
    14.4,
    16.4,
    18.3,
    19.5,
    20.7,
    21.9,
    22.7,
    23.1,
    23.1,
    22.8,
    21.8,
    20.2,
    19.1,
    18.0,
    17.4,
]

# Electricity Price (in Euro per Wh)
strompreis_euro_pro_wh = np.full(48, 0.001)
strompreis_euro_pro_wh[0:10] = 0.00001
strompreis_euro_pro_wh[11:15] = 0.00005
strompreis_euro_pro_wh[20] = 0.00001
# [
#     0.0000384,
#     0.0000318,
#     0.0000284,
#     0.0008283,
#     0.0008289,
#     0.0008334,
#     0.0008290,
#     0.0003302,
#     0.0003042,
#     0.0002430,
#     0.0002280,
#     0.0002212,
#     0.0002093,
#     0.0001879,
#     0.0001838,
#     0.0002004,
#     0.0002198,
#     0.0002270,
#     0.0002997,
#     0.0003195,
#     0.0003081,
#     0.0002969,
#     0.0002921,
#     0.0002780,
#     0.0003384,
#     0.0003318,
#     0.0003284,
#     0.0003283,
#     0.0003289,
#     0.0003334,
#     0.0003290,
#     0.0003302,
#     0.0003042,
#     0.0002430,
#     0.0002280,
#     0.0002212,
#     0.0002093,
#     0.0001879,
#     0.0001838,
#     0.0002004,
#     0.0002198,
#     0.0002270,
#     0.0002997,
#     0.0003195,
#     0.0003081,
#     0.0002969,
#     0.0002921,
#     0.0002780,
# ]

# Overall System Load (in W)
gesamtlast = [
    676.71,
    876.19,
    527.13,
    468.88,
    531.38,
    517.95,
    483.15,
    472.28,
    1011.68,
    995.00,
    1053.07,
    1063.91,
    1320.56,
    1132.03,
    1163.67,
    1176.82,
    1216.22,
    1103.78,
    1129.12,
    1178.71,
    1050.98,
    988.56,
    912.38,
    704.61,
    516.37,
    868.05,
    694.34,
    608.79,
    556.31,
    488.89,
    506.91,
    804.89,
    1141.98,
    1056.97,
    992.46,
    1155.99,
    827.01,
    1257.98,
    1232.67,
    871.26,
    860.88,
    1158.03,
    1222.72,
    1221.04,
    949.99,
    987.01,
    733.99,
    592.97,
]

# Start Solution (binary)
start_solution = None

# Define parameters for the optimization problem
parameter = {
    # Value of energy in battery (per Wh)
    "preis_euro_pro_wh_akku": 0e-05,
    # Initial state of charge (SOC) of PV battery (%)
    "pv_soc": 15,
    # Battery capacity (in Wh)
    "pv_akku_cap": 26400,
    # Yearly energy consumption (in Wh)
    "year_energy": 4100000,
    # Feed-in tariff for exporting electricity (per Wh)
    "einspeiseverguetung_euro_pro_wh": 7e-05,
    # Maximum heating power (in W)
    "max_heizleistung": 1000,
    # Overall load on the system
    "gesamtlast": gesamtlast,
    # PV generation forecast (48 hours)
    "pv_forecast": pv_forecast,
    # Temperature forecast (48 hours)
    "temperature_forecast": temperature_forecast,
    # Electricity price forecast (48 hours)
    "strompreis_euro_pro_wh": strompreis_euro_pro_wh,
    # Minimum SOC for electric car
    "eauto_min_soc": 50,
    # Electric car battery capacity (Wh)
    "eauto_cap": 60000,
    # Charging efficiency of the electric car
    "eauto_charge_efficiency": 0.95,
    # Charging power of the electric car (W)
    "eauto_charge_power": 11040,
    # Current SOC of the electric car (%)
    "eauto_soc": 15,
    # Current PV power generation (W)
    "pvpowernow": 211.137503624,
    # Initial solution for the optimization
    "start_solution": start_solution,
    # Household appliance consumption (Wh)
    "haushaltsgeraet_wh": 5000,
    # Duration of appliance usage (hours)
    "haushaltsgeraet_dauer": 0,
    # Minimum Soc PV Battery
    "min_soc_prozent": 15,
}

# Startzeit nehmen
start_time = time.time()

# Initialize the optimization problem
opt_class = optimization_problem(
    prediction_hours=48, strafe=10, optimization_hours=24, verbose=True, fixed_seed=42
)

# Perform the optimisation based on the provided parameters and start hour
ergebnis = opt_class.optimierung_ems(parameter=parameter, start_hour=start_hour)

# Endzeit nehmen
end_time = time.time()

# Berechnete Zeit ausgeben
elapsed_time = end_time - start_time
print(f"Elapsed time: {elapsed_time:.4f} seconds")


ac_charge, dc_charge, discharge = (
    ergebnis["ac_charge"],
    ergebnis["dc_charge"],
    ergebnis["discharge_allowed"],
)

visualisiere_ergebnisse(
    gesamtlast,
    pv_forecast,
    strompreis_euro_pro_wh,
    ergebnis["result"],
    ac_charge,
    dc_charge,
    discharge,
    temperature_forecast,
    start_hour,
    48,
    np.full(48, parameter["einspeiseverguetung_euro_pro_wh"]),
    filename="visualization_results.pdf",
    extra_data=None,
)


json_data = NumpyEncoder.dumps(ergebnis)
print(json_data)

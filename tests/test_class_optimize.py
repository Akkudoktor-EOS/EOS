from pathlib import Path

import numpy as np
import pytest

from modules.class_optimize import optimization_problem
from modules.config import load_config

# Sample known result (replace with the actual expected output)
EXPECTED_RESULT = {
    "discharge_hours_bin": [
        1,
        1,
        1,
        0,
        1,
        1,
        0,
        1,
        1,
        1,
        0,
        1,
        1,
        1,
        1,
        1,
        0,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
    ],
    "eautocharge_hours_float": [
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0.0,
        0.0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0.0,
        0.0,
    ],
    "result": {
        "Last_Wh_pro_Stunde": [
            None,
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
        ],
        "Netzeinspeisung_Wh_pro_Stunde": [
            None,
            0.0,
            0.0,
            0.0,
            0.0,
            2924.2707438016537,
            2753.66,
            1914.18,
            813.95,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1311.3858057851144,
            497.68000000000006,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ],
        "Netzbezug_Wh_pro_Stunde": [
            None,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ],
        "Kosten_Euro_pro_Stunde": [
            None,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ],
        "akku_soc_pro_stunde": [
            None,
            79.91107093663912,
            78.99070247933885,
            79.08956914600552,
            95.27340247933884,
            100.0,
            100.0,
            100.0,
            100.0,
            99.26162190082644,
            96.11376549586775,
            91.89251893939392,
            87.96526342975206,
            84.93233471074379,
            82.70966769972449,
            78.97322658402202,
            75.98450413223138,
            73.36402376033054,
            70.96943870523413,
            68.86505681818178,
            66.68310950413219,
            63.24022899449031,
            59.76919765840215,
            58.25555268595038,
            58.684419352617034,
            60.18041935261703,
            64.6149860192837,
            65.19921935261704,
            80.15195268595036,
            92.42761935261704,
            99.64985268595038,
            100.0,
            100.0,
            98.89101239669421,
            96.45174758953168,
            92.20325413223141,
            89.04386191460057,
            86.4914772727273,
        ],
        "Einnahmen_Euro_pro_Stunde": [
            None,
            0.0,
            0.0,
            0.0,
            0.0,
            0.20469895206611574,
            0.19275619999999996,
            0.1339926,
            0.0569765,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.091797006404958,
            0.0348376,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ],
        "Gesamtbilanz_Euro": np.float64(-0.7150588584710738),
        "E-Auto_SoC_pro_Stunde": [
            None,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
        ],
        "Gesamteinnahmen_Euro": np.float64(0.7150588584710738),
        "Gesamtkosten_Euro": np.float64(0.0),
        "Verluste_Pro_Stunde": [
            None,
            2.817272727272737,
            29.157272727272726,
            3.5592000000000112,
            582.6179999999995,
            170.1575107438016,
            0.0,
            0.0,
            0.0,
            23.391818181818195,
            99.72409090909093,
            133.72909090909081,
            124.41545454545451,
            96.08318181818186,
            70.41409090909087,
            118.37045454545455,
            94.68272727272722,
            83.01681818181817,
            75.86045454545456,
            66.66681818181814,
            69.12409090909085,
            109.0704545454546,
            109.96227272727276,
            47.952272727272714,
            15.439199999999985,
            53.855999999999995,
            159.6443999999999,
            21.032399999999996,
            538.2984000000001,
            441.924,
            260.0003999999999,
            12.605303305786279,
            0.0,
            35.132727272727266,
            77.27590909090907,
            134.59227272727276,
            100.08954545454549,
            80.85954545454547,
        ],
        "Gesamt_Verluste": np.float64(4041.523450413223),
        "Haushaltsgeraet_wh_pro_stunde": [
            None,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ],
    },
    "eauto_obj": {
        "kapazitaet_wh": 60000,
        "start_soc_prozent": 54,
        "soc_wh": 32400.000000000004,
        "hours": 48,
        "discharge_array": [
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
        ],
        "charge_array": [
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ],
        "lade_effizienz": 0.95,
        "entlade_effizienz": 1.0,
        "max_ladeleistung_w": 11040,
    },
    "start_solution": [
        1,
        1,
        1,
        0,
        1,
        1,
        0,
        1,
        1,
        1,
        0,
        1,
        1,
        1,
        1,
        1,
        0,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0.0,
        0.0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0.0,
        0.0,
    ],
    "spuelstart": None,
    "simulation_data": {
        "Last_Wh_pro_Stunde": [
            None,
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
        ],
        "Netzeinspeisung_Wh_pro_Stunde": [
            None,
            0.0,
            0.0,
            0.0,
            0.0,
            2924.2707438016537,
            2753.66,
            1914.18,
            813.95,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1311.3858057851144,
            497.68000000000006,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ],
        "Netzbezug_Wh_pro_Stunde": [
            None,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ],
        "Kosten_Euro_pro_Stunde": [
            None,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ],
        "akku_soc_pro_stunde": [
            None,
            79.91107093663912,
            78.99070247933885,
            79.08956914600552,
            95.27340247933884,
            100.0,
            100.0,
            100.0,
            100.0,
            99.26162190082644,
            96.11376549586775,
            91.89251893939392,
            87.96526342975206,
            84.93233471074379,
            82.70966769972449,
            78.97322658402202,
            75.98450413223138,
            73.36402376033054,
            70.96943870523413,
            68.86505681818178,
            66.68310950413219,
            63.24022899449031,
            59.76919765840215,
            58.25555268595038,
            58.684419352617034,
            60.18041935261703,
            64.6149860192837,
            65.19921935261704,
            80.15195268595036,
            92.42761935261704,
            99.64985268595038,
            100.0,
            100.0,
            98.89101239669421,
            96.45174758953168,
            92.20325413223141,
            89.04386191460057,
            86.4914772727273,
        ],
        "Einnahmen_Euro_pro_Stunde": [
            None,
            0.0,
            0.0,
            0.0,
            0.0,
            0.20469895206611574,
            0.19275619999999996,
            0.1339926,
            0.0569765,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.091797006404958,
            0.0348376,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ],
        "Gesamtbilanz_Euro": np.float64(-0.7150588584710738),
        "E-Auto_SoC_pro_Stunde": [
            None,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
            54.0,
        ],
        "Gesamteinnahmen_Euro": np.float64(0.7150588584710738),
        "Gesamtkosten_Euro": np.float64(0.0),
        "Verluste_Pro_Stunde": [
            None,
            2.817272727272737,
            29.157272727272726,
            3.5592000000000112,
            582.6179999999995,
            170.1575107438016,
            0.0,
            0.0,
            0.0,
            23.391818181818195,
            99.72409090909093,
            133.72909090909081,
            124.41545454545451,
            96.08318181818186,
            70.41409090909087,
            118.37045454545455,
            94.68272727272722,
            83.01681818181817,
            75.86045454545456,
            66.66681818181814,
            69.12409090909085,
            109.0704545454546,
            109.96227272727276,
            47.952272727272714,
            15.439199999999985,
            53.855999999999995,
            159.6443999999999,
            21.032399999999996,
            538.2984000000001,
            441.924,
            260.0003999999999,
            12.605303305786279,
            0.0,
            35.132727272727266,
            77.27590909090907,
            134.59227272727276,
            100.08954545454549,
            80.85954545454547,
        ],
        "Gesamt_Verluste": np.float64(4041.523450413223),
        "Haushaltsgeraet_wh_pro_stunde": [
            None,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ],
    },
}


@pytest.fixture
def setup_opt_class():
    # Initialize the optimization_problem class with parameters
    start_hour = 10

    # PV Forecast (in W)
    pv_forecast = [
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        8.05,
        352.91,
        728.51,
        930.28,
        1043.25,
        1106.74,
        1161.69,
        6018.82,
        5519.07,
        3969.88,
        3017.96,
        1943.07,
        1007.17,
        319.67,
        7.88,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        5.04,
        335.59,
        705.32,
        1121.12,
        1604.79,
        2157.38,
        1433.25,
        5718.49,
        4553.96,
        3027.55,
        2574.46,
        1720.4,
        963.4,
        383.3,
        0,
        0,
        0,
    ]

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
    strompreis_euro_pro_wh = [
        0.0003384,
        0.0003318,
        0.0003284,
        0.0003283,
        0.0003289,
        0.0003334,
        0.0003290,
        0.0003302,
        0.0003042,
        0.0002430,
        0.0002280,
        0.0002212,
        0.0002093,
        0.0001879,
        0.0001838,
        0.0002004,
        0.0002198,
        0.0002270,
        0.0002997,
        0.0003195,
        0.0003081,
        0.0002969,
        0.0002921,
        0.0002780,
        0.0003384,
        0.0003318,
        0.0003284,
        0.0003283,
        0.0003289,
        0.0003334,
        0.0003290,
        0.0003302,
        0.0003042,
        0.0002430,
        0.0002280,
        0.0002212,
        0.0002093,
        0.0001879,
        0.0001838,
        0.0002004,
        0.0002198,
        0.0002270,
        0.0002997,
        0.0003195,
        0.0003081,
        0.0002969,
        0.0002921,
        0.0002780,
    ]

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
    start_solution = [
        1,
        1,
        1,
        1,
        0,
        1,
        0,
        0,
        1,
        1,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
    ]

    # Define parameters for the optimization problem
    parameter = {
        "preis_euro_pro_wh_akku": 10e-05,  # Cost of storing energy in battery (per Wh)
        "pv_soc": 80,  # Initial state of charge (SOC) of PV battery (%)
        "pv_akku_cap": 26400,  # Battery capacity (in Wh)
        "year_energy": 4100000,  # Yearly energy consumption (in Wh)
        "einspeiseverguetung_euro_pro_wh": 7e-05,  # Feed-in tariff for exporting electricity (per Wh)
        "max_heizleistung": 1000,  # Maximum heating power (in W)
        "gesamtlast": gesamtlast,  # Overall load on the system
        "pv_forecast": pv_forecast,  # PV generation forecast (48 hours)
        "temperature_forecast": temperature_forecast,  # Temperature forecast (48 hours)
        "strompreis_euro_pro_wh": strompreis_euro_pro_wh,  # Electricity price forecast (48 hours)
        "eauto_min_soc": 0,  # Minimum SOC for electric car
        "eauto_cap": 60000,  # Electric car battery capacity (Wh)
        "eauto_charge_efficiency": 0.95,  # Charging efficiency of the electric car
        "eauto_charge_power": 11040,  # Charging power of the electric car (W)
        "eauto_soc": 54,  # Current SOC of the electric car (%)
        "pvpowernow": 211.137503624,  # Current PV power generation (W)
        "start_solution": start_solution,  # Initial solution for the optimization
        "haushaltsgeraet_wh": 937,  # Household appliance consumption (Wh)
        "haushaltsgeraet_dauer": 0,  # Duration of appliance usage (hours)
    }

    # Create an instance of the optimization problem class
    config_path = Path(__file__).parent.parent.joinpath("config", "example.config.json")
    config = load_config(config_path)
    opt_class = optimization_problem(config, fixed_seed=42)
    yield (
        opt_class,
        parameter,
        start_hour,
    )  # Yield the class and parameters for use in tests


def test_optimierung_ems(setup_opt_class):
    opt_class, parameter, start_hour = setup_opt_class

    # Call the optimization function
    ergebnis = opt_class.optimierung_ems(parameter=parameter, start_hour=start_hour)

    # Compare the result with the known expected result
    assert (
        ergebnis == EXPECTED_RESULT
    )  # Use appropriate comparison based on the structure of ergebnis

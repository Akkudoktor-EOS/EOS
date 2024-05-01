import numpy as np
import pandas as pd
import joblib
from sklearn.preprocessing import StandardScaler
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel, WhiteKernel
from sklearn.model_selection import train_test_split

import numpy as np
import matplotlib.pyplot as plt

class BatterySocPredictor:
    def __init__(self):
        # Initialisierung von Scaler und Gaußschem Prozessmodell
        self.scaler = StandardScaler()
        kernel = WhiteKernel(1.0, (1e-7, 1e3)) + RBF(length_scale=(0.1,0.1), length_scale_bounds=((1e-7, 1e3),(1e-7, 1e3)))
        self.gp = GaussianProcessRegressor(kernel=kernel, n_restarts_optimizer=10, alpha=1e-2, normalize_y=True)

    def fit(self, X, y):
        # Transformiere die Zielvariable
        y_transformed = np.log(y / (101 - y))
        # Skaliere die Features
        X_scaled = self.scaler.fit_transform(X)
        # Trainiere das Modell
        self.gp.fit(X_scaled, y_transformed)

    def predict(self, X):
        # Skaliere die Features
        X_scaled = self.scaler.transform(X)
        # Vorhersagen und Unsicherheiten
        y_pred_transformed, sigma_transformed = self.gp.predict(X_scaled, return_std=True)
        # Rücktransformieren der Vorhersagen
        y_pred = 101 / (1 + np.exp(-y_pred_transformed))
        # Rücktransformieren der Unsicherheiten
        sigmoid_y_pred = 1 / (1 + np.exp(-y_pred_transformed))
        sigma = sigma_transformed * 101 * sigmoid_y_pred * (1 - sigmoid_y_pred)
        return float(y_pred), float(sigma)

    def save_model(self, file_path):
        # Speichere das gesamte Modell-Objekt
        joblib.dump(self, file_path)

    @staticmethod
    def load_model(file_path):
        # Lade das Modell-Objekt
        return joblib.load(file_path)





# # Daten laden und Modell verwenden
# data_path = 'train.csv'
# data = pd.read_csv(data_path)
# X = data[['battery_voltage', 'battery_current']]
# y = data['battery_soc']

# # Aufteilen der Daten in Trainings- und Testdatensätze
# X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.1, random_state=42)

# # Modell instanziieren und trainieren
# battery_model = BatteryModel()
# battery_model.fit(X_train, y_train)

# # Modell speichern
# battery_model.save_model('battery_model.pkl')

# # Modell für Vorhersagen laden
# loaded_model = BatteryModel.load_model('battery_model.pkl')

# # Vorhersagen machen
# current_values = np.linspace(-100, 100, 5)
# voltage_range = np.linspace(48, 58, 50)
# for current in current_values:
    # X_pred = np.column_stack([voltage_range, np.full(voltage_range.shape, current)])
    # y_pred, sigma = loaded_model.predict(X_pred)
    # # Plotten der mittleren Vorhersage und des Konfidenzintervalls
    # plt.plot(voltage_range, y_pred, label=f'Strom = {current:.1f} A')
    # plt.fill_between(voltage_range, y_pred - 1.96 * sigma, y_pred + 1.96 * sigma, alpha=0.2)

# # Hinzufügen von Titel und Legende zum Plot
# plt.title('Vorhergesagter SoC als Funktion der Batteriespannung bei verschiedenen Strömen')
# plt.xlabel('Batteriespannung (V)')
# plt.ylabel('State of Charge (SoC %)')
# plt.legend()
# plt.show()



# sys.exit()





# # Daten laden
# data_path = 'train.csv'
# data = pd.read_csv(data_path)

# # Spalten auswählen
# X = data[['battery_voltage', 'battery_current']]  # Merkmale
# y = data['battery_soc']  # Zielvariable

# # Aufteilung der Daten in Trainings- und Testset

# # Transformiere die Zielvariable
# y_transformed =  np.log(y / (101 - y))


# # X Normalisierung
# scaler = StandardScaler()
# X_scaled = scaler.fit_transform(X)


# # Aufteilen der Daten in Trainings- und Testdatensätze
# X_train, X_test, y_train, y_test = train_test_split(X_scaled, y_transformed, test_size=0.5, random_state=42)

# # Kernel und Modell definieren
# kernel = WhiteKernel(1.0, (1e-7, 1e3))  + RBF(length_scale=(0.1,0.1), length_scale_bounds=((1e-7, 1e3),(1e-7, 1e3))) #* ConstantKernel(constant_value=1.0, constant_value_bounds=(1e-05, 100000.0))

# gp = GaussianProcessRegressor(kernel=kernel, n_restarts_optimizer=10, alpha=1e-2, normalize_y=True)

# # Modell trainieren
# gp.fit(X_train, y_train)

# # Vorhersagen und Rücktransformieren
# #y_pred_transformed, sigma = gp.predict(X_test, return_std=True)
# #y_pred = 100 / (1 + np.exp(-y_pred_transformed))


# # Ergebnisse anzeigen
# #print("Vorhersagen:", y_pred)
# #print("Unsicherheit der Vorhersagen:", sigma)


# # Angenommen, 'gp' ist dein trainiertes Gaußsches Prozessmodell
# joblib.dump(gp, 'gaussian_process_model.pkl')



# # Festlegen der Ströme und Spannungsrange für die Vorhersagen
# current_values = np.linspace(-100, 100, 5)
# voltage_range = np.linspace(48, 58, 50)

# # Erstellen einer Figur für die Plots
# plt.figure(figsize=(12, 8))

# # Für jeden Stromwert einen Plot erstellen
# for current in current_values:
    # # Erstellen von Vorhersagedatenpunkten
    # X_pred = np.column_stack([voltage_range, np.full(voltage_range.shape, current)])
    # X_pred_scaled = scaler.transform(X_pred)  # Standardisierung
    
    # # Vorhersage des SoC und der Unsicherheit
    # y_pred_transformed, sigma_transformed = gp.predict(X_pred_scaled, return_std=True)
    # y_pred = 101 / (1 + np.exp(-y_pred_transformed))  # Rücktransformation
    
    # sigmoid_y_pred = 1 / (1 + np.exp(-y_pred_transformed))
    # sigma = sigma_transformed * 101 * sigmoid_y_pred * (1 - sigmoid_y_pred)
    
    
    # # Plotten der mittleren Vorhersage und des Konfidenzintervalls
    # plt.plot(voltage_range, y_pred, label=f'Strom = {current:.1f} A')
    # plt.fill_between(voltage_range, y_pred - 1.96 * sigma, y_pred + 1.96 * sigma, alpha=0.2)

# # Hinzufügen von Titel und Legende zum Plot
# plt.title('Vorhergesagter SoC als Funktion der Batteriespannung bei verschiedenen Strömen')
# plt.xlabel('Batteriespannung (V)')
# plt.ylabel('State of Charge (SoC %)')
# plt.legend()
# plt.show()





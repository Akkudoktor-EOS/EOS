import numpy as np
import pandas as pd
import joblib, json
from sklearn.preprocessing import StandardScaler
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel, WhiteKernel, Matern,DotProduct
from sklearn.model_selection import train_test_split
from tensorflow.keras.models import Sequential, load_model
from tensorflow.keras.layers import LSTM, Dense,Dropout
from sklearn.preprocessing import MinMaxScaler
import matplotlib.pyplot as plt
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.regularizers import l1, l2, l1_l2
from scipy.signal import savgol_filter
import numpy as np
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout, RepeatVector, TimeDistributed
from sklearn.preprocessing import MinMaxScaler
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.regularizers import l2
import matplotlib.pyplot as plt
from sklearn.metrics import mean_absolute_error, mean_squared_error



class BatterySocPredictorGauss:
    def __init__(self):
        # Initialisierung von Scaler und Gaußschem Prozessmodell
        self.scaler = StandardScaler()
        kernel = WhiteKernel(1.0, (1e-7, 1e3)) + Matern(length_scale=(0.1,0.1,0.1), length_scale_bounds=((1e-7, 1e3),(1e-7, 1e3),(1e-7, 1e3))) + DotProduct()
        self.gp = GaussianProcessRegressor(kernel=kernel, n_restarts_optimizer=10, alpha=1e-3, normalize_y=True)

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
        return y_pred

    def save_model(self, file_path):
        # Speichere das gesamte Modell-Objekt
        joblib.dump(self, file_path)

    @staticmethod
    def load_model(file_path):
        # Lade das Modell-Objekt
        return joblib.load(file_path)
        
        
class BatterySoCPredictorLSTM:
    def __init__(self, model_path=None, scaler_path=None, gauss=None):
        self.scaler = MinMaxScaler(feature_range=(0, 1))
        self.target_scaler = MinMaxScaler(feature_range=(0, 1))
        self.seq_length = 5  # Anzahl der Zeitschritte in der Eingabesequenz
        self.n_future_steps = 1  # Anzahl der zukünftigen Schritte, die vorhergesagt werden sollen
        self.gauss_model = BatterySocPredictorGauss.load_model(gauss)
        
        if model_path:
            self.model = load_model(model_path)
        else:
            self.model = self._build_model()
        
        if scaler_path:
            self.load_scalers(scaler_path)

    def _build_model(self):
        regu = 0.00  # Regularisierungsrate
        model = Sequential()
        model.add(LSTM(20, activation='relu', return_sequences=True, input_shape=(self.seq_length, 4), kernel_regularizer=l2(regu)))
        model.add(LSTM(20, activation='relu', return_sequences=False, kernel_regularizer=l2(regu)))
        model.add(RepeatVector(self.n_future_steps))
        model.add(LSTM(20, activation='relu', return_sequences=True, kernel_regularizer=l2(regu)))
        model.add(TimeDistributed(Dense(1, kernel_regularizer=l2(regu))))  # TimeDistributed Layer für Multi-Step Output

        optimizer = Adam(learning_rate=0.0005)
        model.compile(optimizer=optimizer, loss='mae')
        return model


    def fit(self, data_path, epochs=100, batch_size=50, validation_split=0.1):
        data = pd.read_csv(data_path)
        data['Time'] = pd.to_datetime(data['Time'], unit='ms')
        data.set_index('Time', inplace=True)

        data.dropna(inplace=True)
        
        # Gauss
        #data["temperature_mean"] = data[["data","data.1"]].mean(axis=1)
        #data[['battery_voltage', 'battery_current', 'data']]
        data["battery_soc_gauss"] = self.gauss_model.predict(data[['battery_voltage', 'battery_current', 'data']].values)
        # print(data)
        # sys.exit()
        scaled_data = self.scaler.fit_transform(data[['battery_voltage', 'battery_current', 'data', 'battery_soc_gauss']].values)
        data['scaled_soc'] = self.target_scaler.fit_transform(data[['battery_soc']])

        X, y = self._create_sequences(scaled_data, self.seq_length, self.n_future_steps)
        
        print(y.shape)
        
        self.model.fit(X, y, epochs=epochs, batch_size=batch_size, validation_split=validation_split)

    def _create_sequences(self, data, seq_length, n_future_steps):
        xs, ys = [], []
        for i in range(len(data) - seq_length - n_future_steps):
            x = data[i:(i + seq_length)]
            y = data[(i + seq_length):(i + seq_length + n_future_steps), -1]  # Multi-Step Output
            xs.append(x)
            ys.append(y)
        return np.array(xs), np.array(ys)

    # def predict(self, test_data_path):
        # test_data = pd.read_csv(test_data_path)
        # test_data['Time'] = pd.to_datetime(test_data['Time'], unit='ms')
        # test_data.set_index('Time', inplace=True)
        # test_data.replace('undefined', np.nan, inplace=True)
        # test_data.dropna(inplace=True)

        # test_data['battery_voltage'] = pd.to_numeric(test_data['battery_voltage'], errors='coerce')
        # test_data['battery_current'] = pd.to_numeric(test_data['battery_current'], errors='coerce')
        # test_data['battery_soc'] = pd.to_numeric(test_data['battery_soc'], errors='coerce')
        # test_data['data.1'] = pd.to_numeric(test_data['data.1'], errors='coerce')
        # test_data.dropna(inplace=True)

        # scaled_test_data = self.scaler.transform(test_data[['battery_voltage', 'battery_current', 'data.1', 'battery_soc']])
        # test_data['scaled_soc'] = self.target_scaler.transform(test_data[['battery_soc']])
        # test_data.dropna(inplace=True)

        # X_test, _ = self._create_sequences(scaled_test_data, self.seq_length, self.n_future_steps)
        # predictions = self.model.predict(X_test)
        # predictions = self.target_scaler.inverse_transform(predictions.reshape(-1, 1)).reshape(-1, self.n_future_steps)
        # return predictions

    def predict_single(self, voltage_current_temp_soc_sequence):
        
        if len(voltage_current_temp_soc_sequence) != self.seq_length or len(voltage_current_temp_soc_sequence[0]) != 3:
            raise ValueError("Die Eingabesequenz muss die Form (seq_length, 3) haben.")
        

        
        soc_gauss = self.gauss_model.predict(voltage_current_temp_soc_sequence)
        soc_gauss = soc_gauss.reshape(-1,1)
        #print(voltage_current_temp_soc_sequence.shape)
        #print(soc_gauss.shape)
        voltage_current_sequence = np.hstack([voltage_current_temp_soc_sequence, soc_gauss])
        #print(voltage_current_sequence.shape)
        print(voltage_current_sequence)
        scaled_sequence = self.scaler.transform(voltage_current_sequence)
        X = np.array([scaled_sequence])

        prediction = self.model.predict(X)
        prediction = self.target_scaler.inverse_transform(prediction.reshape(-1, 1)).reshape(-1, self.n_future_steps)
        return prediction  # Return the sequence of future SoC predictions

    def save_model(self, model_path=None, scaler_path=None):
        self.model.save(model_path)
        
        scaler_params = {
            'scaler_min_': self.scaler.min_.tolist(),
            'scaler_scale_': self.scaler.scale_.tolist(),
            'target_scaler_min_': self.target_scaler.min_.tolist(),
            'target_scaler_scale_': self.target_scaler.scale_.tolist()
        }
        with open(scaler_path, 'w') as f:
            json.dump(scaler_params, f)

    def load_scalers(self, scaler_path):
        with open(scaler_path, 'r') as f:
            scaler_params = json.load(f)
        self.scaler.min_ = np.array(scaler_params['scaler_min_'])
        self.scaler.scale_ = np.array(scaler_params['scaler_scale_'])
        self.target_scaler.min_ = np.array(scaler_params['target_scaler_min_'])
        self.target_scaler.scale_ = np.array(scaler_params['target_scaler_scale_'])

if __name__ == '__main__':


    train_data_path = 'lstm_train/raw_data_clean.csv'
    test_data_path = 'Test_Data.csv'
    model_path = 'battery_soc_predictor_lstm_model.keras'
    scaler_path = 'battery_soc_predictor_scaler_model'

    ####################
    # GAUSS + K-Means
    ####################
    # Daten laden und vorbereiten
    data_path = 'k_means.csv'
    data = pd.read_csv(data_path, decimal='.')
    data.dropna(inplace=True)  # Entfernen von Zeilen mit NaN-Werten, die durch das Rolling entstehen
    #print(data[["data","data.1"]].mean(axis=1))
    data["temperature_mean"] = data[["data","data.1"]].mean(axis=1)
    # Features und Zielvariable definieren
    X = data[['battery_voltage', 'battery_current',"temperature_mean"]] # 
    y = data['battery_soc']

    # Aufteilen der Daten in Trainings- und Testdatensätze
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.1, random_state=42)

    # # # Modell instanziieren und trainieren
    #battery_model = BatterySocPredictorGauss()
    #battery_model.fit(X_train, y_train)
    #battery_model.save_model('battery_model.pkl')
    
    battery_model = BatterySocPredictorGauss.load_model('battery_model.pkl')
    
    # Vorhersagen auf den Testdaten
    y_pred_test = battery_model.predict(X_test)
    
    print(y_pred_test.shape, " ", y_test.shape)
    # Berechnung des MAE und RMSE
    mae = mean_absolute_error(y_test, y_pred_test)
    rmse = mean_squared_error(y_test, y_pred_test, squared=False)

    print(f'Mean Absolute Error (MAE): {mae}')
    print(f'Root Mean Squared Error (RMSE): {rmse}')

    # Plotten der tatsächlichen Werte vs. Vorhersagen
    # plt.figure(figsize=(12, 6))
    # plt.plot(y_test.values, label='Actual SoC')
    # plt.plot(y_pred_test, label='Predicted SoC')
    # plt.xlabel('Samples')
    # plt.ylabel('State of Charge (SoC)')
    # plt.title('Actual vs Predicted SoC')
    # plt.legend()
    # plt.show()

    
    # # # Modell speichern
    #battery_model.save_model('battery_model.pkl')

    # Modell für Vorhersagen laden
    #loaded_model = BatterySocPredictorGauss.load_model('battery_model.pkl')

    ####################
    # LSTM
    ####################
    

    predictor = BatterySoCPredictorLSTM(gauss='battery_model.pkl')

    # # Training mit rekursiver Vorhersage
    predictor.fit(train_data_path, epochs=50, batch_size=50, validation_split=0.1)

    # # # Speichern des Modells und der Scaler
    predictor.save_model(model_path=model_path, scaler_path=scaler_path)
    
    # # # Laden des Modells und der Scaler
    loaded_predictor = BatterySoCPredictorLSTM(model_path=model_path, scaler_path=scaler_path,gauss='battery_model.pkl')

    test_data = pd.read_csv(test_data_path)
    test_data['Time'] = pd.to_datetime(test_data['Time'], unit='ms')
    test_data.set_index('Time', inplace=True)
    test_data.replace('undefined', np.nan, inplace=True)
    test_data.dropna(inplace=True)
    test_data['battery_voltage'] = pd.to_numeric(test_data['battery_voltage'], errors='coerce')
    test_data['battery_current'] = pd.to_numeric(test_data['battery_current'], errors='coerce')
    test_data['battery_soc'] = pd.to_numeric(test_data['battery_soc'], errors='coerce')
    test_data['data'] = pd.to_numeric(test_data['data.1'], errors='coerce')
    test_data.dropna(inplace=True)

    scaled_test_data = loaded_predictor.scaler.transform(test_data[['battery_voltage', 'battery_current', 'data', 'battery_soc']])
    test_data['scaled_soc'] = loaded_predictor.target_scaler.transform(test_data[['battery_soc']])
    test_data.dropna(inplace=True)

    X_test, y_test = loaded_predictor._create_sequences(scaled_test_data, loaded_predictor.seq_length, loaded_predictor.n_future_steps)
    predictions = loaded_predictor.model.predict(X_test)
    predictions = loaded_predictor.target_scaler.inverse_transform(predictions.reshape(-1, 1)).reshape(-1, loaded_predictor.n_future_steps)



    # print(test_data['battery_soc'].values[5:-5,...].shape)
    # print(predictions[:,0].shape)

    test_data_y = test_data['battery_soc'].values[5:-1,...]
    mae = mean_absolute_error(test_data_y, predictions[:,0])
    rmse = mean_squared_error(test_data_y, predictions[:,0], squared=False)

    print(f'Mean Absolute Error (MAE): {mae}')
    print(f'Root Mean Squared Error (RMSE): {rmse}')

    plt.figure(figsize=(12, 6))
    plt.plot(test_data_y, label='Actual SoC')
    plt.plot(predictions[:,0].flatten(), label='Predicted SoC')
    plt.xlabel('Samples')
    plt.ylabel('State of Charge (SoC)')
    plt.title('Actual vs Predicted SoC using LSTM')
    plt.legend()
    plt.show()

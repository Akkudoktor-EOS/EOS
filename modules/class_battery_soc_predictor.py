import numpy as np
import pandas as pd
import joblib
import json
from sklearn.preprocessing import StandardScaler, MinMaxScaler
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import WhiteKernel, Matern, DotProduct
from sklearn.model_selection import train_test_split
from tensorflow.keras.models import Sequential, load_model
from tensorflow.keras.layers import LSTM, Dense, Dropout, RepeatVector, TimeDistributed
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.regularizers import l1, l2, l1_l2
from sklearn.metrics import mean_absolute_error, mean_squared_error
import matplotlib.pyplot as plt

class BatterySocPredictorGauss:
    def __init__(self):
        # Initialize scaler and Gaussian process model
        self.scaler = StandardScaler()
        kernel = (WhiteKernel(1.0, (1e-7, 1e3)) + 
                  Matern(length_scale=(0.1, 0.1, 0.1), 
                         length_scale_bounds=((1e-7, 1e3), (1e-7, 1e3), (1e-7, 1e3))) + 
                  DotProduct())
        self.gp = GaussianProcessRegressor(kernel=kernel, n_restarts_optimizer=10, alpha=1e-3, normalize_y=True)

    def fit(self, X, y):
        # Transform the target variable
        y_transformed = np.log(y / (101 - y))
        # Scale the features
        X_scaled = self.scaler.fit_transform(X)
        # Train the model
        self.gp.fit(X_scaled, y_transformed)

    def predict(self, X):
        # Scale the features
        X_scaled = self.scaler.transform(X)
        # Predictions and uncertainties
        y_pred_transformed, sigma_transformed = self.gp.predict(X_scaled, return_std=True)
        # Reverse transform the predictions
        y_pred = 101 / (1 + np.exp(-y_pred_transformed))
        # Reverse transform the uncertainties
        sigmoid_y_pred = 1 / (1 + np.exp(-y_pred_transformed))
        sigma = sigma_transformed * 101 * sigmoid_y_pred * (1 - sigmoid_y_pred)
        return y_pred

    def save_model(self, file_path):
        # Save the entire model object
        joblib.dump(self, file_path)

    @staticmethod
    def load_model(file_path):
        # Load the model object
        return joblib.load(file_path)
        
        
class BatterySoCPredictorLSTM:
    def __init__(self, model_path=None, scaler_path=None, gauss=None):
        self.scaler = MinMaxScaler(feature_range=(0, 1))
        self.target_scaler = MinMaxScaler(feature_range=(0, 1))
        self.seq_length = 5  # Number of time steps in input sequence
        self.n_future_steps = 1  # Number of future steps to predict
        self.gauss_model = BatterySocPredictorGauss.load_model(gauss)
        
        if model_path:
            self.model = load_model(model_path)
        else:
            self.model = self._build_model()
        
        if scaler_path:
            self.load_scalers(scaler_path)

    def _build_model(self):
        regu = 0.00  # Regularization rate
        model = Sequential()
        model.add(LSTM(20, activation='relu', return_sequences=True, input_shape=(self.seq_length, 4), kernel_regularizer=l2(regu)))
        model.add(LSTM(20, activation='relu', return_sequences=False, kernel_regularizer=l2(regu)))
        model.add(RepeatVector(self.n_future_steps))
        model.add(LSTM(20, activation='relu', return_sequences=True, kernel_regularizer=l2(regu)))
        model.add(TimeDistributed(Dense(1, kernel_regularizer=l2(regu))))  # TimeDistributed layer for multi-step output

        optimizer = Adam(learning_rate=0.0005)
        model.compile(optimizer=optimizer, loss='mae')
        return model

    def fit(self, data_path, epochs=100, batch_size=50, validation_split=0.1):
        data = pd.read_csv(data_path)
        data['Time'] = pd.to_datetime(data['Time'], unit='ms')
        data.set_index('Time', inplace=True)

        data.dropna(inplace=True)
        
        # Use Gaussian model to predict SoC
        data["battery_soc_gauss"] = self.gauss_model.predict(data[['battery_voltage', 'battery_current', 'data']].values)
        
        scaled_data = self.scaler.fit_transform(data[['battery_voltage', 'battery_current', 'data', 'battery_soc_gauss']].values)
        data['scaled_soc'] = self.target_scaler.fit_transform(data[['battery_soc']])

        X, y = self._create_sequences(scaled_data, self.seq_length, self.n_future_steps)
        
        print(y.shape)
        
        self.model.fit(X, y, epochs=epochs, batch_size=batch_size, validation_split=validation_split)

    def _create_sequences(self, data, seq_length, n_future_steps):
        xs, ys = [], []
        for i in range(len(data) - seq_length - n_future_steps):
            x = data[i:(i + seq_length)]
            y = data[(i + seq_length):(i + seq_length + n_future_steps), -1]  # Multi-step output
            xs.append(x)
            ys.append(y)
        return np.array(xs), np.array(ys)

    def predict_single(self, voltage_current_temp_soc_sequence):
        if len(voltage_current_temp_soc_sequence) != self.seq_length or len(voltage_current_temp_soc_sequence[0]) != 3:
            raise ValueError("Input sequence must have the shape (seq_length, 3).")

        soc_gauss = self.gauss_model.predict(voltage_current_temp_soc_sequence)
        soc_gauss = soc_gauss.reshape(-1, 1)
        voltage_current_sequence = np.hstack([voltage_current_temp_soc_sequence, soc_gauss])
        
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
    # Load and prepare data
    data_path = 'k_means.csv'
    data = pd.read_csv(data_path, decimal='.')
    data.dropna(inplace=True)  # Remove rows with NaN values
    data["temperature_mean"] = data[["data", "data.1"]].mean(axis=1)  # Calculate mean temperature

    # Define features and target variable
    X = data[['battery_voltage', 'battery_current', "temperature_mean"]]
    y = data['battery_soc']

    # Split the data into training and testing sets
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.1, random_state=42)

    battery_model = BatterySocPredictorGauss.load_model('battery_model.pkl')

    # Make predictions on the test data
    y_pred_test = battery_model.predict(X_test)
    
    print(y_pred_test.shape, " ", y_test.shape)
    # Calculate MAE and RMSE
    mae = mean_absolute_error(y_test, y_pred_test)
    rmse = mean_squared_error(y_test, y_pred_test, squared=False)

    print(f'Mean Absolute Error (MAE): {mae}')
    print(f'Root Mean Squared Error (RMSE): {rmse}')

    # Plot actual vs predicted values
    # plt.figure(figsize=(12, 6))
    # plt.plot(y_test.values, label='Actual SoC')
    # plt.plot(y_pred_test, label='Predicted SoC')
    # plt.xlabel('Samples')
    # plt.ylabel('State of Charge (SoC)')
    # plt.title('Actual vs Predicted SoC')
    # plt.legend()
    # plt.show()

    ####################
    # LSTM
    ####################
    predictor = BatterySoCPredictorLSTM(gauss='battery_model.pkl')

    # Training with recursive prediction
    predictor.fit(train_data_path, epochs=50, batch_size=50, validation_split=0.1)

    # Save the model and scalers
    predictor.save_model(model_path=model_path, scaler_path=scaler_path)
    
    # Load the model and scalers
    loaded_predictor = BatterySoCPredictorLSTM(model_path=model_path, scaler_path=scaler_path, gauss='battery_model.pkl')

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

    test_data_y = test_data['battery_soc'].values[5:-1, ...]
    mae = mean_absolute_error(test_data_y, predictions[:, 0])
    rmse = mean_squared_error(test_data_y, predictions[:, 0], squared=False)

    print(f'Mean Absolute Error (MAE): {mae}')
    print(f'Root Mean Squared Error (RMSE): {rmse}')

    plt.figure(figsize=(12, 6))
    plt.plot(test_data_y, label='Actual SoC')
    plt.plot(predictions[:, 0].flatten(), label='Predicted SoC')
    plt.xlabel('Samples')
    plt.ylabel('State of Charge (SoC)')
    plt.title('Actual vs Predicted SoC using LSTM')
    plt.legend()
    plt.show()

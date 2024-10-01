import mariadb
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy.integrate import simpson
from datetime import datetime, timedelta
from config import *


class BatteryDataProcessor:
    def __init__(self, config, voltage_high_threshold, voltage_low_threshold, current_low_threshold, gap, battery_capacity_ah):
        self.config = config
        self.voltage_high_threshold = voltage_high_threshold
        self.voltage_low_threshold = voltage_low_threshold
        self.current_low_threshold = current_low_threshold
        self.gap = gap
        self.battery_capacity_ah = battery_capacity_ah 
        self.conn = None
        self.data = None

    def connect_db(self):
        self.conn = mariadb.connect(**self.config)
        self.cursor = self.conn.cursor()

    def disconnect_db(self):
        if self.conn:
            self.cursor.close()
            self.conn.close()

    def fetch_data(self, start_time):
        query = """
        SELECT timestamp, data, topic
        FROM pip
        WHERE timestamp >= %s AND (topic = 'battery_current' OR topic = 'battery_voltage')
        ORDER BY timestamp
        """
        self.cursor.execute(query, (start_time,))
        rows = self.cursor.fetchall()
        self.data = pd.DataFrame(rows, columns=['timestamp', 'data', 'topic'])
        self.data['timestamp'] = pd.to_datetime(self.data['timestamp'])
        self.data['data'] = self.data['data'].astype(float)

    def process_data(self):
        self.data.drop_duplicates(subset=['timestamp', 'topic'], inplace=True)

        data_pivot = self.data.pivot(index='timestamp', columns='topic', values='data')
        data_pivot = data_pivot.resample('1T').mean().interpolate()
        data_pivot.columns.name = None
        data_pivot.reset_index(inplace=True)
        self.data = data_pivot

    def group_points(self, df):
        df = df.sort_values('timestamp')
        groups = []
        group = []
        last_time = None

        for _, row in df.iterrows():
            if last_time is None or (row['timestamp'] - last_time) <= pd.Timedelta(minutes=self.gap):
                group.append(row)
            else:
                groups.append(group)
                group = [row]
            last_time = row['timestamp']

        if group:
            groups.append(group)
        
        last_points = [group[-1] for group in groups]
        return last_points

    def find_soc_points(self):
        condition_soc_100 = (self.data['battery_voltage'] >= self.voltage_high_threshold) & (self.data['battery_current'].abs() <= self.current_low_threshold)
        condition_soc_0 = (self.data['battery_voltage'] <= self.voltage_low_threshold) & (self.data['battery_current'].abs() <= self.current_low_threshold)

        times_soc_100_all = self.data[condition_soc_100][['timestamp', 'battery_voltage', 'battery_current']]
        times_soc_0_all = self.data[condition_soc_0][['timestamp', 'battery_voltage', 'battery_current']]

        last_points_100 = self.group_points(times_soc_100_all)
        last_points_0 = self.group_points(times_soc_0_all)

        last_points_100_df = pd.DataFrame(last_points_100)
        last_points_0_df = pd.DataFrame(last_points_0)

        return last_points_100_df, last_points_0_df

    def calculate_resetting_soc(self, last_points_100_df, last_points_0_df):
        soc_values = []
        integration_results = []
        reset_points = pd.concat([last_points_100_df, last_points_0_df]).sort_values('timestamp')

        # Initialisieren der SoC-Liste
        self.data['calculated_soc'] = np.nan

        for i in range(len(reset_points)):
            start_point = reset_points.iloc[i]
            if i < len(reset_points) - 1:
                end_point = reset_points.iloc[i + 1]
            else:
                end_point = self.data.iloc[-1]  # Verwenden des letzten Datensatzes als Endpunkt

            if start_point['timestamp'] in last_points_100_df['timestamp'].values:
                initial_soc = 100
            elif start_point['timestamp'] in last_points_0_df['timestamp'].values:
                initial_soc = 0

            cut_data = self.data[(self.data['timestamp'] >= start_point['timestamp']) & (self.data['timestamp'] <= end_point['timestamp'])].copy()
            cut_data['time_diff_hours'] = cut_data['timestamp'].diff().dt.total_seconds() / 3600
            cut_data.dropna(subset=['time_diff_hours'], inplace=True)

            calculated_soc = initial_soc
            calculated_soc_list = [calculated_soc]
            integrated_current = 0

            for j in range(1, len(cut_data)):
                current = cut_data.iloc[j]['battery_current']
                delta_t = cut_data.iloc[j]['time_diff_hours']
                delta_soc = (current * delta_t) / self.battery_capacity_ah * 100  # Convert to percentage
                
                calculated_soc += delta_soc
                calculated_soc = min(max(calculated_soc, 0), 100)  # Clip to 0-100%
                calculated_soc_list.append(calculated_soc)
                
                # Integration des Stroms aufaddieren
                integrated_current += current * delta_t

            cut_data['calculated_soc'] = calculated_soc_list
            soc_values.append(cut_data[['timestamp', 'calculated_soc']])

            integration_results.append({
                'start_time': start_point['timestamp'],
                'end_time': end_point['timestamp'],
                'integrated_current': integrated_current,
                'start_soc': initial_soc,
                'end_soc': calculated_soc_list[-1]
            })

        soc_df = pd.concat(soc_values).drop_duplicates(subset=['timestamp']).reset_index(drop=True)
        return soc_df, integration_results


    def calculate_soh(self, integration_results):
        soh_values = []

        for result in integration_results:
            delta_soc = abs(result['start_soc'] - result['end_soc'])  # Use the actual change in SoC
            if delta_soc > 0:  # Avoid division by zero
                effective_capacity_ah = result['integrated_current']
                soh = (effective_capacity_ah / self.battery_capacity_ah) * 100
                soh_values.append({'timestamp': result['end_time'], 'soh': soh})

        soh_df = pd.DataFrame(soh_values)
        return soh_df


    def delete_existing_soc_entries(self, soc_df):
        delete_query = """
        DELETE FROM pip
        WHERE timestamp = %s AND topic = 'calculated_soc'
        """
        timestamps = [(row['timestamp'].strftime('%Y-%m-%d %H:%M:%S'),) for _, row in soc_df.iterrows() if pd.notna(row['timestamp'])]
        
        self.cursor.executemany(delete_query, timestamps)
        self.conn.commit()

    def update_database_with_soc(self, soc_df):
        # Löschen der vorhandenen Einträge mit demselben Topic und Datum
        self.delete_existing_soc_entries(soc_df)

        # Resample `soc_df` auf 5-Minuten-Intervalle und berechnen des Mittelwerts
        soc_df.set_index('timestamp', inplace=True)
        soc_df_resampled = soc_df.resample('5T').mean().dropna().reset_index()
        #soc_df_resampled['timestamp'] = soc_df_resampled['timestamp'].apply(lambda x: x.strftime('%Y-%m-%d %H:%M:%S'))
        print(soc_df_resampled)

        # Einfügen der berechneten SoC-Werte in die Datenbank
        insert_query = """
        INSERT INTO pip (timestamp, data, topic)
        VALUES (%s, %s, 'calculated_soc')
        """
        for _, row in soc_df_resampled.iterrows():
            print(row)
            print(row['timestamp'])
            record = (row['timestamp'].strftime('%Y-%m-%d %H:%M:%S'), row['calculated_soc'])
            try:
                self.cursor.execute(insert_query, record)
            except mariadb.OperationalError as e:
                print(f"Error inserting record {record}: {e}")

        self.conn.commit()






    def plot_data(self, last_points_100_df, last_points_0_df, soc_df):
        plt.figure(figsize=(14, 10))

        plt.subplot(4, 1, 1)
        plt.plot(self.data['timestamp'], self.data['battery_voltage'], label='Battery Voltage', color='blue')
        plt.scatter(last_points_100_df['timestamp'], last_points_100_df['battery_voltage'], color='green', marker='o', label='100% SoC Points')
        #plt.scatter(last_points_0_df['timestamp'], last_points_0_df['battery_voltage'], color='red', marker='x', label='0% SoC Points')
        plt.xlabel('Timestamp')
        plt.ylabel('Voltage (V)')
        plt.legend()
        plt.title('Battery Voltage over Time')

        plt.subplot(4, 1, 2)
        plt.plot(self.data['timestamp'], self.data['battery_current'], label='Battery Current', color='orange')
        plt.scatter(last_points_100_df['timestamp'], last_points_100_df['battery_current'], color='green', marker='o', label='100% SoC Points')
        #plt.scatter(last_points_0_df['timestamp'], last_points_0_df['battery_current'], color='red', marker='x', label='0% SoC Points')
        plt.xlabel('Timestamp')
        plt.ylabel('Current (A)')
        plt.legend()
        plt.title('Battery Current over Time')

        plt.subplot(4, 1, 3)
        plt.plot(soc_df['timestamp'], soc_df['calculated_soc'], label='SoC', color='purple')
        plt.xlabel('Timestamp')
        plt.ylabel('SoC (%)')
        plt.legend()
        plt.title('State of Charge (SoC) over Time')

        # plt.subplot(4, 1, 4)
        # plt.plot(soh_df['timestamp'], soh_df['soh'], label='SoH', color='brown')
        # plt.xlabel('Timestamp')
        # plt.ylabel('SoH (%)')
        # plt.legend()
        # plt.title('State of Health (SoH) over Time')



        plt.tight_layout()
        plt.show()

if __name__ == '__main__':

    # MariaDB Verbindungsdetails
    config = db_config #from config

    # Parameter festlegen
    voltage_high_threshold = 55.4  # 100% SoC
    voltage_low_threshold = 46.5  # 0% SoC
    current_low_threshold = 2  # Niedriger Strom für beide Zustände
    gap = 30  # Zeitlücke in Minuten zum  Gruppieren von Maxima/Minima
    bat_capacity = 33 * 1000 / 48 

    # Zeitpunkt X definieren
    zeitpunkt_x = (datetime.now() - timedelta(weeks=100)).strftime('%Y-%m-%d %H:%M:%S')


    # BatteryDataProcessor instanziieren und verwenden
    processor = BatteryDataProcessor(config, voltage_high_threshold, voltage_low_threshold, current_low_threshold, gap,bat_capacity)
    processor.connect_db()
    processor.fetch_data(zeitpunkt_x)
    processor.process_data()
    last_points_100_df, last_points_0_df = processor.find_soc_points()
    soc_df, integration_results = processor.calculate_resetting_soc(last_points_100_df, last_points_0_df)
    #soh_df = processor.calculate_soh(integration_results)
    processor.update_database_with_soc(soc_df)


    processor.plot_data(last_points_100_df, last_points_0_df, soc_df)





    processor.disconnect_db()



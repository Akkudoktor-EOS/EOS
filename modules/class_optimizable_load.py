import numpy as np
class OptimizableLoad:
    def __init__(self, name=None, power=0, duration=0, schedule=None):
        """
        Initialisiert eine neue optimierbare Last.

        :param name: Eindeutiger Name der Last
        :param power: Leistung der Last in kW
        :param duration: Dauer, für die die Last aktiv ist, in Stunden
        :param schedule: Ein 24-Stunden-Array (0/1), das angibt, wann die Last gestartet werden kann
        """
        self.name = name
        self.power = power
        self.duration = duration
        self.optimal_start_time = None
        if schedule is None:
            self.schedule = [1] * 24
        else:
            self.schedule = schedule

    def set_schedule(self, new_schedule):
        """
        Aktualisiert den Zeitplan, wann die Last gestartet werden kann.

        :param new_schedule: Ein 24-Stunden-Array (0/1)
        """
        self.schedule = new_schedule

    def set_optimal_start_time(self, start_time):
        """
        Setzt die optimale Startzeit für die Last.

        :param start_time: Die Stunde des Tages (0-23), zu der die Last starten soll
        """
        if 0 <= start_time < 24 and self.is_activatable(start_time):
            self.optimal_start_time = start_time

    def is_active_at_hour(self, hour):
        """
        Überprüft, ob die Last zu einer bestimmten Stunde aktiv ist, basierend auf ihrem Startzeitpunkt und der Dauer.

        :param hour: Stunde des Tages (0-23)
        :return: True, wenn die Last aktiv ist, sonst False
        """
        if self.optimal_start_time is None:
            return False
        return self.optimal_start_time <= hour < self.optimal_start_time + self.duration

    def power_at_hour(self, hour):
        """
        Gibt die Leistung der Last zu einer bestimmten Stunde zurück.

        :param hour: Stunde des Tages (0-23)
        :return: Leistung der Last in kW, wenn sie aktiv ist, sonst 0
        """
        if self.is_active_at_hour(hour):
            return self.power
        return 0

    def __str__(self):
        return f"OptimizableLoad(Name: {self.name}, Power: {self.power}kW, Duration: {self.duration}h, Schedule: {self.schedule})"

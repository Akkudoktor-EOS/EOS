from datetime import datetime

# Set the backend for matplotlib to Agg
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

from modules.class_sommerzeit import *  # Ensure this matches the actual import path

matplotlib.use("Agg")


def visualisiere_ergebnisse(
    gesamtlast,
    pv_forecast,
    strompreise,
    ergebnisse,
    discharge_hours,
    laden_moeglich,
    temperature,
    start_hour,
    prediction_hours,
    einspeiseverguetung_euro_pro_wh,
    filename="visualization_results.pdf",
    extra_data=None,
):
    #####################
    # 24-hour visualization
    #####################
    with PdfPages(filename) as pdf:
        # Load and PV generation
        plt.figure(figsize=(14, 14))
        plt.subplot(3, 3, 1)
        hours = np.arange(0, prediction_hours)

        gesamtlast_array = np.array(gesamtlast)
        # Plot individual loads
        plt.plot(hours, gesamtlast_array, label="Load (Wh)", marker="o")

        # Calculate and plot total load
        plt.plot(
            hours,
            gesamtlast_array,
            label="Total Load (Wh)",
            marker="o",
            linewidth=2,
            linestyle="--",
        )
        plt.xlabel("Hour")
        plt.ylabel("Load (Wh)")
        plt.title("Load Profiles")
        plt.grid(True)
        plt.legend()

        # Electricity prices
        hours_p = np.arange(0, len(strompreise))
        plt.subplot(3, 2, 2)
        plt.plot(
            hours_p,
            strompreise,
            label="Electricity Price (€/Wh)",
            color="purple",
            marker="s",
        )
        plt.title("Electricity Prices")
        plt.xlabel("Hour of the Day")
        plt.ylabel("Price (€/Wh)")
        plt.legend()
        plt.grid(True)

        # PV forecast
        plt.subplot(3, 2, 3)
        plt.plot(hours, pv_forecast, label="PV Generation (Wh)", marker="x")
        plt.title("PV Forecast")
        plt.xlabel("Hour of the Day")
        plt.ylabel("Wh")
        plt.legend()
        plt.grid(True)

        # Feed-in remuneration
        plt.subplot(3, 2, 4)
        plt.plot(
            hours,
            einspeiseverguetung_euro_pro_wh,
            label="Remuneration (€/Wh)",
            marker="x",
        )
        plt.title("Remuneration")
        plt.xlabel("Hour of the Day")
        plt.ylabel("€/Wh")
        plt.legend()
        plt.grid(True)

        # Temperature forecast
        plt.subplot(3, 2, 5)
        plt.title("Temperature Forecast (°C)")
        plt.plot(hours, temperature, label="Temperature (°C)", marker="x")
        plt.xlabel("Hour of the Day")
        plt.ylabel("°C")
        plt.legend()
        plt.grid(True)

        pdf.savefig()  # Save the current figure state to the PDF
        plt.close()  # Close the current figure to free up memory

        #####################
        # Start hour visualization
        #####################

        plt.figure(figsize=(14, 10))

        if ist_dst_wechsel(datetime.datetime.now()):
            hours = np.arange(start_hour, prediction_hours - 1)
        else:
            hours = np.arange(start_hour, prediction_hours)

        # Energy flow, grid feed-in, and grid consumption
        plt.subplot(3, 2, 1)
        plt.plot(hours, ergebnisse["Last_Wh_pro_Stunde"], label="Load (Wh)", marker="o")
        plt.plot(
            hours,
            ergebnisse["Haushaltsgeraet_wh_pro_stunde"],
            label="Household Device (Wh)",
            marker="o",
        )
        plt.plot(
            hours,
            ergebnisse["Netzeinspeisung_Wh_pro_Stunde"],
            label="Grid Feed-in (Wh)",
            marker="x",
        )
        plt.plot(
            hours,
            ergebnisse["Netzbezug_Wh_pro_Stunde"],
            label="Grid Consumption (Wh)",
            marker="^",
        )
        plt.plot(
            hours, ergebnisse["Verluste_Pro_Stunde"], label="Losses (Wh)", marker="^"
        )
        plt.title("Energy Flow per Hour")
        plt.xlabel("Hour")
        plt.ylabel("Energy (Wh)")
        plt.legend()

        # State of charge for batteries
        plt.subplot(3, 2, 2)
        plt.plot(
            hours, ergebnisse["akku_soc_pro_stunde"], label="PV Battery (%)", marker="x"
        )
        plt.plot(
            hours,
            ergebnisse["E-Auto_SoC_pro_Stunde"],
            label="E-Car Battery (%)",
            marker="x",
        )
        plt.legend(
            loc="upper left", bbox_to_anchor=(1, 1)
        )  # Place legend outside the plot
        plt.grid(True, which="both", axis="x")  # Grid for every hour

        ax1 = plt.subplot(3, 2, 3)
        for hour, value in enumerate(discharge_hours):
            ax1.axvspan(
                hour,
                hour + 1,
                color="red",
                ymax=value,
                alpha=0.3,
                label="Discharge Possibility" if hour == 0 else "",
            )
        for hour, value in enumerate(laden_moeglich):
            ax1.axvspan(
                hour,
                hour + 1,
                color="green",
                ymax=value,
                alpha=0.3,
                label="Charging Possibility" if hour == 0 else "",
            )
        ax1.legend(loc="upper left")
        ax1.set_xlim(0, prediction_hours)

        pdf.savefig()  # Save the current figure state to the PDF
        plt.close()  # Close the current figure to free up memory

        # Financial overview
        fig, axs = plt.subplots(1, 2, figsize=(14, 10))  # Create a 1x2 grid of subplots
        total_costs = ergebnisse["Gesamtkosten_Euro"]
        total_revenue = ergebnisse["Gesamteinnahmen_Euro"]
        total_balance = ergebnisse["Gesamtbilanz_Euro"]
        losses = ergebnisse["Gesamt_Verluste"]

        # Costs and revenues per hour on the first axis (axs[0])
        axs[0].plot(
            hours,
            ergebnisse["Kosten_Euro_pro_Stunde"],
            label="Costs (Euro)",
            marker="o",
            color="red",
        )
        axs[0].plot(
            hours,
            ergebnisse["Einnahmen_Euro_pro_Stunde"],
            label="Revenue (Euro)",
            marker="x",
            color="green",
        )
        axs[0].set_title("Financial Balance per Hour")
        axs[0].set_xlabel("Hour")
        axs[0].set_ylabel("Euro")
        axs[0].legend()
        axs[0].grid(True)

        # Summary of finances on the second axis (axs[1])
        labels = ["Total Costs [€]", "Total Revenue [€]", "Total Balance [€]"]
        values = [total_costs, total_revenue, total_balance]
        colors = ["red" if value > 0 else "green" for value in values]
        axs[1].bar(labels, values, color=colors)
        axs[1].set_title("Financial Overview")
        axs[1].set_ylabel("Euro")

        # Second axis (ax2) for losses, shared with axs[1]
        ax2 = axs[1].twinx()
        ax2.bar("Total Losses", losses, color="blue")
        ax2.set_ylabel("Losses [Wh]", color="blue")
        ax2.tick_params(axis="y", labelcolor="blue")

        pdf.savefig()  # Save the complete figure to the PDF
        plt.close()  # Close the figure

        # Additional data visualization if provided
        if extra_data is not None:
            plt.figure(figsize=(14, 10))
            plt.subplot(1, 2, 1)
            f1 = np.array(extra_data["verluste"])
            f2 = np.array(extra_data["bilanz"])
            n1 = np.array(extra_data["nebenbedingung"])
            scatter = plt.scatter(f1, f2, c=n1, cmap="viridis")

            # Add color legend
            plt.colorbar(scatter, label="Constraint")

            pdf.savefig()  # Save the complete figure to the PDF
            plt.close()  # Close the figure

            plt.figure(figsize=(14, 10))
            filtered_losses = np.array(
                [
                    v
                    for v, n in zip(
                        extra_data["verluste"], extra_data["nebenbedingung"]
                    )
                    if n < 0.01
                ]
            )
            filtered_balance = np.array(
                [
                    b
                    for b, n in zip(extra_data["bilanz"], extra_data["nebenbedingung"])
                    if n < 0.01
                ]
            )

            best_loss = min(filtered_losses)
            worst_loss = max(filtered_losses)
            best_balance = min(filtered_balance)
            worst_balance = max(filtered_balance)

            data = [filtered_losses, filtered_balance]
            labels = ["Losses", "Balance"]
            # Create plots
            fig, axs = plt.subplots(
                1, 2, figsize=(10, 6), sharey=False
            )  # Two subplots, separate y-axes

            # First violin plot for losses
            axs[0].violinplot(data[0], showmeans=True, showmedians=True)
            axs[0].set_title("Losses")
            axs[0].set_xticklabels(["Losses"])

            # Second violin plot for balance
            axs[1].violinplot(data[1], showmeans=True, showmedians=True)
            axs[1].set_title("Balance")
            axs[1].set_xticklabels(["Balance"])

            # Fine-tuning
            plt.tight_layout()

            pdf.savefig()  # Save the current figure state to the PDF
            plt.close()  # Close the figure

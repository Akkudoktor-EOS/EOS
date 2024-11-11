import datetime
import os

# Set the backend for matplotlib to Agg
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

from akkudoktoreos.class_sommerzeit import ist_dst_wechsel
from akkudoktoreos.config import output_dir

matplotlib.use("Agg")


def visualisiere_ergebnisse(
    gesamtlast,
    pv_forecast,
    strompreise,
    ergebnisse,
    ac,  # AC charging allowed
    dc,  # DC charging allowed
    discharge,  # Discharge allowed
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
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    output_file = os.path.join(output_dir, filename)
    with PdfPages(output_file) as pdf:
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
        # Plot with transparency (alpha) and different linestyles
        plt.plot(
            hours,
            ergebnisse["Last_Wh_pro_Stunde"],
            label="Load (Wh)",
            marker="o",
            linestyle="-",
            alpha=0.8,
        )
        plt.plot(
            hours,
            ergebnisse["home_appliance_wh_per_hour"],
            label="Household Device (Wh)",
            marker="o",
            linestyle="--",
            alpha=0.8,
        )
        plt.plot(
            hours,
            ergebnisse["Netzeinspeisung_Wh_pro_Stunde"],
            label="Grid Feed-in (Wh)",
            marker="x",
            linestyle=":",
            alpha=0.8,
        )
        plt.plot(
            hours,
            ergebnisse["Netzbezug_Wh_pro_Stunde"],
            label="Grid Consumption (Wh)",
            marker="^",
            linestyle="-.",
            alpha=0.8,
        )
        plt.plot(
            hours,
            ergebnisse["Verluste_Pro_Stunde"],
            label="Losses (Wh)",
            marker="^",
            linestyle="-",
            alpha=0.8,
        )

        # Title and labels
        plt.title("Energy Flow per Hour")
        plt.xlabel("Hour")
        plt.ylabel("Energy (Wh)")

        # Show legend with a higher number of columns to avoid overlap
        plt.legend(ncol=2)

        # Electricity prices
        hours_p = np.arange(0, len(strompreise))
        plt.subplot(3, 2, 3)
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

        # State of charge for batteries
        plt.subplot(3, 2, 2)
        plt.plot(hours, ergebnisse["akku_soc_pro_stunde"], label="PV Battery (%)", marker="x")
        plt.plot(
            hours,
            ergebnisse["E-Auto_SoC_pro_Stunde"],
            label="E-Car Battery (%)",
            marker="x",
        )
        plt.legend(loc="upper left", bbox_to_anchor=(1, 1))  # Place legend outside the plot
        plt.grid(True, which="both", axis="x")  # Grid for every hour

        # Plot for AC, DC charging, and Discharge status using bar charts
        ax1 = plt.subplot(3, 2, 5)
        hours = np.arange(0, prediction_hours)
        # Plot AC charging as bars (relative values between 0 and 1)
        plt.bar(hours, ac, width=0.4, label="AC Charging (relative)", color="blue", alpha=0.6)

        # Plot DC charging as bars (relative values between 0 and 1)
        plt.bar(
            hours + 0.4, dc, width=0.4, label="DC Charging (relative)", color="green", alpha=0.6
        )

        # Plot Discharge as bars (0 or 1, binary values)
        plt.bar(
            hours,
            discharge,
            width=0.4,
            label="Discharge Allowed",
            color="red",
            alpha=0.6,
            bottom=np.maximum(ac, dc),
        )

        # Configure the plot
        ax1.legend(loc="upper left")
        ax1.set_xlim(0, prediction_hours)
        ax1.set_xlabel("Hour")
        ax1.set_ylabel("Relative Power (0-1) / Discharge (0 or 1)")
        ax1.set_title("AC/DC Charging and Discharge Overview")
        ax1.grid(True)

        if ist_dst_wechsel(datetime.datetime.now()):
            hours = np.arange(start_hour, prediction_hours - 1)
        else:
            hours = np.arange(start_hour, prediction_hours)

        pdf.savefig()  # Save the current figure state to the PDF
        plt.close()  # Close the current figure to free up memory

        # Financial overview
        fig, axs = plt.subplots(1, 2, figsize=(14, 10))  # Create a 1x2 grid of subplots
        total_costs = ergebnisse["Gesamtkosten_Euro"]
        total_revenue = ergebnisse["Gesamteinnahmen_Euro"]
        total_balance = ergebnisse["Gesamtbilanz_Euro"]
        losses = ergebnisse["Gesamt_Verluste"]

        # Costs and revenues per hour on the first axis (axs[0])
        costs = ergebnisse["Kosten_Euro_pro_Stunde"]
        revenues = ergebnisse["Einnahmen_Euro_pro_Stunde"]

        # Plot costs
        axs[0].plot(
            hours,
            costs,
            label="Costs (Euro)",
            marker="o",
            color="red",
        )
        # Annotate costs
        for hour, value in enumerate(costs):
            if value is None or np.isnan(value):
                value = 0
            axs[0].annotate(
                f"{value:.2f}",
                (hour, value),
                textcoords="offset points",
                xytext=(0, 5),
                ha="center",
                fontsize=8,
                color="red",
            )

        # Plot revenues
        axs[0].plot(
            hours,
            revenues,
            label="Revenue (Euro)",
            marker="x",
            color="green",
        )
        # Annotate revenues
        for hour, value in enumerate(revenues):
            if value is None or np.isnan(value):
                value = 0
            axs[0].annotate(
                f"{value:.2f}",
                (hour, value),
                textcoords="offset points",
                xytext=(0, 5),
                ha="center",
                fontsize=8,
                color="green",
            )

        # Title and labels
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
                    for v, n in zip(extra_data["verluste"], extra_data["nebenbedingung"])
                    if n < 0.01
                ]
            )
            filtered_balance = np.array(
                [b for b, n in zip(extra_data["bilanz"], extra_data["nebenbedingung"]) if n < 0.01]
            )
            if filtered_losses.size != 0:
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
                axs[0].violinplot(data[0], positions=[1], showmeans=True, showmedians=True)
                axs[0].set(xticks=[1], xticklabels=["Losses"])

                # Second violin plot for balance
                axs[1].violinplot(data[1], positions=[1], showmeans=True, showmedians=True)
                axs[1].set(xticks=[1], xticklabels=["Balance"])

                # Fine-tuning
                plt.tight_layout()

            pdf.savefig()  # Save the current figure state to the PDF
            plt.close()  # Close the figure

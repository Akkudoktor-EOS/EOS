import json
import logging
import os
import textwrap
from collections.abc import Sequence
from typing import Callable, Optional, Union

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pendulum
from matplotlib.backends.backend_pdf import PdfPages

from akkudoktoreos.core.coreabc import ConfigMixin
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.optimization.genetic import OptimizationParameters
from akkudoktoreos.utils.datetimeutil import to_datetime

logger = get_logger(__name__)


class VisualizationReport(ConfigMixin):
    def __init__(self, filename: str = "visualization_results.pdf", version: str = "0.0.1") -> None:
        # Initialize the report with a given filename and empty groups
        self.filename = filename
        self.groups: list[list[Callable[[], None]]] = []  # Store groups of charts
        self.current_group: list[
            Callable[[], None]
        ] = []  # Store current group of charts being created
        self.pdf_pages = PdfPages(filename, metadata={})  # Initialize PdfPages without metadata
        self.version = version  # overwrite version as test for constant output of pdf for test
        self.current_time = to_datetime(as_string="YYYY-MM-DD HH:mm:ss")

    def add_chart_to_group(self, chart_func: Callable[[], None]) -> None:
        """Add a chart function to the current group."""
        self.current_group.append(chart_func)

    def finalize_group(self) -> None:
        """Finalize the current group and prepare for a new group."""
        if self.current_group:  # Check if current group has charts
            self.groups.append(self.current_group)  # Add current group to groups
        else:
            print("Finalizing an empty group!")  # Warn if group is empty
        self.current_group = []  # Reset current group for new charts

    def _initialize_pdf(self) -> None:
        """Create the output directory if it doesn't exist and initialize the PDF."""
        output_dir = self.config.general.data_output_path

        # If self.filename is already a valid path, use it; otherwise, combine it with output_dir
        if os.path.isabs(self.filename):
            output_file = self.filename
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = os.path.join(output_dir, self.filename)

        self.pdf_pages = PdfPages(
            output_file, metadata={}
        )  # Re-initialize PdfPages without metadata

    def _save_group_to_pdf(self, group: list[Callable[[], None]]) -> None:
        """Save a group of charts to the PDF."""
        fig_count = len(group)  # Number of charts in the group

        if fig_count == 0:
            print("Attempted to save an empty group to PDF!")
            return

        # Check for special charts before creating layout
        special_keywords = {"add_text_page", "add_json_page"}
        for chart_func in group:
            if any(keyword in chart_func.__qualname__ for keyword in special_keywords):
                chart_func()  # Special chart functions handle their own rendering
                return

        # Create layout only if no special charts are detected
        if fig_count == 3:
            fig = plt.figure(figsize=(14, 10))
            ax1 = fig.add_subplot(2, 1, 1)
            ax2 = fig.add_subplot(2, 2, 3)
            ax3 = fig.add_subplot(2, 2, 4)
            axs = [ax1, ax2, ax3]
        else:
            cols = 2 if fig_count > 1 else 1
            rows = (fig_count + 1) // 2
            fig, axs = plt.subplots(rows, cols, figsize=(14, 7 * rows))
            axs = list(np.array(axs).reshape(-1))

        # Add footer text with current time to each page
        if self.version == "test":
            current_time = "test"
        else:
            current_time = self.current_time
        fig.text(
            0.5,
            0.02,
            f"Generated on: {current_time} with version: {self.version}",
            ha="center",
            va="center",
            fontsize=10,
        )

        # Render each chart in its corresponding axis
        for idx, chart_func in enumerate(group):
            plt.sca(axs[idx])  # Set current axis
            chart_func()  # Render the chart

        # Save the figure to the PDF and clean up
        for idx in range(fig_count, len(axs)):
            axs[idx].set_visible(False)

        self.pdf_pages.savefig(fig)  # Save the figure to the PDF
        plt.close(fig)

    def create_line_chart_date(
        self,
        start_date: pendulum.DateTime,
        y_list: list[Union[np.ndarray, list[Optional[float]], list[float]]],
        ylabel: str,
        xlabel: Optional[str] = None,
        title: Optional[str] = None,
        labels: Optional[list[str]] = None,
        markers: Optional[list[str]] = None,
        line_styles: Optional[list[str]] = None,
        x2label: Optional[Union[str, None]] = "Hours Since Start",
    ) -> None:
        """Create a line chart and add it to the current group."""

        def chart() -> None:
            timestamps = [
                start_date.add(hours=i) for i in range(len(y_list[0]))
            ]  # 840 timestamps at 1-hour intervals

            for idx, y_data in enumerate(y_list):
                label = labels[idx] if labels else None  # Chart label
                marker = markers[idx] if markers and idx < len(markers) else "o"  # Marker style
                line_style = line_styles[idx] if line_styles and idx < len(line_styles) else "-"
                plt.plot(
                    timestamps, y_data, label=label, marker=marker, linestyle=line_style
                )  # Plot line

            # Format the time axis
            plt.gca().xaxis.set_major_formatter(
                mdates.DateFormatter("%Y-%m-%d")
            )  # Show date and time
            plt.gca().xaxis.set_major_locator(
                mdates.DayLocator(interval=1, tz=None)
            )  # Major ticks every day
            plt.gca().xaxis.set_minor_locator(mdates.HourLocator(interval=3, tz=None))
            # Minor ticks every 6 hours
            plt.gca().xaxis.set_minor_formatter(mdates.DateFormatter("%H"))
            # plt.gcf().autofmt_xdate(rotation=45, which="major")
            # Auto-format the x-axis for readability

            # Move major tick labels further down to avoid collision with minor tick labels
            for plt_label in plt.gca().get_xticklabels(which="major"):
                plt_label.set_y(-0.04)

            # Add labels, title, and legend
            if xlabel:
                plt.xlabel(xlabel)
            plt.ylabel(ylabel)
            if title:
                plt.title(title)
            if labels:
                plt.legend()
            plt.grid(True)

            # Add vertical line for the current date if within the axis range
            current_time = pendulum.now()
            if timestamps[0].subtract(hours=2) <= current_time <= timestamps[-1]:
                plt.axvline(current_time, color="r", linestyle="--", label="Now")
                plt.text(current_time, plt.ylim()[1], "Now", color="r", ha="center", va="bottom")

            # Add a second x-axis on top
            ax1 = plt.gca()
            ax2 = ax1.twiny()
            ax2.set_xlim(ax1.get_xlim())  # Align the second axis with the first

            # Generate integer hour labels
            hours_since_start = [(t - timestamps[0]).total_seconds() / 3600 for t in timestamps]
            # ax2.set_xticks(timestamps[::48])  # Set ticks every 12 hours
            # ax2.set_xticklabels([f"{int(h)}" for h in hours_since_start[::48]])
            ax2.set_xticks(timestamps[:: len(timestamps) // 24])  # Select 10 evenly spaced ticks
            ax2.set_xticklabels([f"{int(h)}" for h in hours_since_start[:: len(timestamps) // 24]])
            if x2label:
                ax2.set_xlabel(x2label)

            # Ensure ax1 and ax2 are aligned
            # assert ax1.get_xlim() == ax2.get_xlim(), "ax1 and ax2 are not aligned"

        self.add_chart_to_group(chart)  # Add chart function to current group

    def create_line_chart(
        self,
        start_hour: Optional[int],
        y_list: list[Union[np.ndarray, list[Optional[float]], list[float]]],
        title: str,
        xlabel: str,
        ylabel: str,
        labels: Optional[list[str]] = None,
        markers: Optional[list[str]] = None,
        line_styles: Optional[list[str]] = None,
    ) -> None:
        """Create a line chart and add it to the current group."""

        def chart() -> None:
            nonlocal start_hour  # Allow modifying `x` within the nested function
            if start_hour is None:
                start_hour = 0
            first_element = y_list[0]
            x: np.ndarray
            # Case 1: y_list contains np.ndarray elements
            if isinstance(first_element, np.ndarray):
                x = np.arange(
                    start_hour, start_hour + len(first_element)
                )  # Start at x and extend by ndarray length
            # Case 2: y_list contains float elements (1D list)
            elif isinstance(first_element, float):
                x = np.arange(
                    start_hour, start_hour + len(y_list)
                )  # Start at x and extend by list length
            # Case 3: y_list is a nested list of floats
            elif isinstance(first_element, list) and all(
                isinstance(i, float) for i in first_element
            ):
                max_len = max(len(sublist) for sublist in y_list)
                x = np.arange(
                    start_hour, start_hour + max_len
                )  # Start at x and extend by max sublist length
            else:
                print(f"Unsupported y_list structure: {type(y_list)}, {y_list}")
                raise TypeError(
                    "y_list elements must be np.ndarray, float, or a nested list of floats"
                )

            for idx, y_data in enumerate(y_list):
                label = labels[idx] if labels else None  # Chart label
                marker = markers[idx] if markers and idx < len(markers) else "o"  # Marker style
                line_style = (
                    line_styles[idx] if line_styles and idx < len(line_styles) else "-"
                )  # Line style
                plt.plot(x, y_data, label=label, marker=marker, linestyle=line_style)  # Plot line

            plt.title(title)  # Set title
            plt.xlabel(xlabel)  # Set x-axis label
            plt.ylabel(ylabel)  # Set y-axis label
            if labels:
                plt.legend()  # Show legend if labels are provided
            plt.grid(True)  # Show grid
            plt.xlim(x[0] - 0.5, x[-1] + 0.5)  # Adjust x-limits

        self.add_chart_to_group(chart)  # Add chart function to current group

    def create_scatter_plot(
        self,
        x: np.ndarray,
        y: np.ndarray,
        title: str,
        xlabel: str,
        ylabel: str,
        c: Optional[np.ndarray] = None,
    ) -> None:
        """Create a scatter plot and add it to the current group."""

        def chart() -> None:
            scatter = plt.scatter(x, y, c=c, cmap="viridis")  # Create scatter plot
            plt.title(title)  # Set title
            plt.xlabel(xlabel)  # Set x-axis label
            plt.ylabel(ylabel)  # Set y-axis label
            if c is not None:
                plt.colorbar(scatter, label="Constraint")  # Add colorbar if color data is provided
            plt.grid(True)  # Show grid

        self.add_chart_to_group(chart)  # Add chart function to current group

    def create_bar_chart(
        self,
        labels: list[str],
        values_list: Sequence[Union[int, float, list[Union[int, float]]]],
        title: str,
        ylabel: str,
        xlabels: Optional[list[str]] = None,
        label_names: Optional[list[str]] = None,
        colors: Optional[list[str]] = None,
        bar_width: float = 0.35,
        bottom: Optional[int] = None,
    ) -> None:
        """Create a bar chart and add it to the current group."""

        def chart() -> None:
            num_groups = len(values_list)  # Number of data groups
            num_bars = len(labels)  # Number of bars (categories)
            # Calculate the positions for each bar group on the x-axis
            x = np.arange(num_bars)  # x positions for bars
            offset = np.linspace(
                -bar_width * (num_groups - 1) / 2, bar_width * (num_groups - 1) / 2, num_groups
            )  # Bar offsets
            for i, values in enumerate(values_list):
                bottom_use = None
                if bottom == i + 1:  # Set bottom if specified
                    bottom_use = 1
                color = colors[i] if colors and i < len(colors) else None  # Bar color
                label_name = label_names[i] if label_names else None  # Bar label
                plt.bar(
                    x + offset[i],
                    values,
                    bar_width,
                    label=label_name,
                    color=color,
                    zorder=2,
                    alpha=0.6,
                    bottom=bottom_use,
                )  # Create bar
            if xlabels:
                plt.xticks(x, labels)  # Add custom labels to the x-axis
            plt.title(title)  # Set title
            plt.ylabel(ylabel)  # Set y-axis label

            if colors and label_names:
                plt.legend()  # Show legend if colors are provided
            plt.grid(True, zorder=0)  # Show grid in the background
            plt.xlim(-0.5, len(labels) - 0.5)  # Set x-axis limits

        self.add_chart_to_group(chart)  # Add chart function to current group

    def create_violin_plot(
        self, data_list: list[np.ndarray], labels: list[str], title: str, xlabel: str, ylabel: str
    ) -> None:
        """Create a violin plot and add it to the current group."""

        def chart() -> None:
            plt.violinplot(data_list, showmeans=True, showmedians=True)  # Create violin plot
            plt.xticks(np.arange(1, len(labels) + 1), labels)  # Set x-ticks and labels
            plt.title(title)  # Set title
            plt.xlabel(xlabel)  # Set x-axis label
            plt.ylabel(ylabel)  # Set y-axis label
            plt.grid(True)  # Show grid

        self.add_chart_to_group(chart)  # Add chart function to current group

    def add_text_page(self, text: str, title: Optional[str] = None, fontsize: int = 12) -> None:
        """Add a page with text content to the PDF."""

        def chart() -> None:
            fig = plt.figure(figsize=(8.5, 11))  # Create a standard page size
            plt.axis("off")  # Turn off axes for a clean page
            wrapped_text = textwrap.fill(text, width=80)  # Wrap text to fit the page width
            y = 0.95  # Start at the top of the page

            if title:
                plt.text(0.5, y, title, ha="center", va="top", fontsize=fontsize + 4, weight="bold")
                y -= 0.05  # Add space after the title

            plt.text(0.5, y, wrapped_text, ha="center", va="top", fontsize=fontsize, wrap=True)
            self.pdf_pages.savefig(fig)  # Save the figure as a page in the PDF
            plt.close(fig)  # Close the figure to free up memory

        self.add_chart_to_group(chart)  # Treat the text page as a "chart" in the group

    def add_json_page(
        self, json_obj: dict, title: Optional[str] = None, fontsize: int = 12
    ) -> None:
        """Add a page with a formatted JSON object to the PDF.

        Args:
            json_obj (dict): The JSON object to display.
            title (Optional[str]): An optional title for the page.
            fontsize (int): The font size for the JSON text.
        """

        def chart() -> None:
            # Convert JSON object to a formatted string
            json_str = json.dumps(json_obj, indent=4)

            fig = plt.figure(figsize=(8.5, 11))  # Standard page size
            plt.axis("off")  # Turn off axes for a clean page

            y = 0.95  # Start at the top of the page
            if title:
                plt.text(0.5, y, title, ha="center", va="top", fontsize=fontsize + 4, weight="bold")
                y -= 0.05  # Add space after the title

            # Split the JSON string into lines and render them
            lines = json_str.splitlines()
            for line in lines:
                plt.text(0.05, y, line, ha="left", va="top", fontsize=fontsize, family="monospace")
                y -= 0.02  # Move down for the next line

                # Stop if the text exceeds the page
                if y < 0.05:
                    break

            self.pdf_pages.savefig(fig)  # Save the figure as a page in the PDF
            plt.close(fig)  # Close the figure to free up memory

        self.add_chart_to_group(chart)  # Treat the JSON page as a "chart" in the group

    def generate_pdf(self) -> None:
        """Generate the PDF report with all the added chart groups."""
        self._initialize_pdf()  # Initialize the PDF

        for group in self.groups:
            self._save_group_to_pdf(group)  # Save each group to the PDF

        self.pdf_pages.close()  # Close the PDF to finalize the report


def prepare_visualize(
    parameters: OptimizationParameters,
    results: dict,
    filename: str = "visualization_results.pdf",
    start_hour: Optional[int] = 0,
) -> None:
    report = VisualizationReport(filename)
    next_full_hour_date = pendulum.now().start_of("hour").add(hours=1)
    # Group 1:
    print(parameters.ems.gesamtlast)
    report.create_line_chart_date(
        next_full_hour_date,  # start_date
        [
            parameters.ems.gesamtlast,
        ],
        title="Load Profile",
        # xlabel="Hours", # not enough space
        ylabel="Load (Wh)",
        labels=["Total Load (Wh)"],
    )
    report.create_line_chart_date(
        next_full_hour_date,  # start_date
        [
            parameters.ems.pv_prognose_wh,
        ],
        title="PV Forecast",
        # xlabel="Hours", # not enough space
        ylabel="PV Generation (Wh)",
    )

    report.create_line_chart_date(
        next_full_hour_date,  # start_date
        [np.full(len(parameters.ems.gesamtlast), parameters.ems.einspeiseverguetung_euro_pro_wh)],
        title="Remuneration",
        # xlabel="Hours", # not enough space
        ylabel="€/Wh",
        x2label=None,  # not enough space
    )
    if parameters.temperature_forecast:
        report.create_line_chart_date(
            next_full_hour_date,  # start_date
            [
                parameters.temperature_forecast,
            ],
            title="Temperature Forecast",
            # xlabel="Hours", # not enough space
            ylabel="°C",
            x2label=None,  # not enough space
        )
    report.finalize_group()

    # Group 2:
    report.create_line_chart_date(
        next_full_hour_date,  # start_date
        [
            results["result"]["Last_Wh_pro_Stunde"],
            results["result"]["Home_appliance_wh_per_hour"],
            results["result"]["Netzeinspeisung_Wh_pro_Stunde"],
            results["result"]["Netzbezug_Wh_pro_Stunde"],
            results["result"]["Verluste_Pro_Stunde"],
        ],
        title="Energy Flow per Hour",
        # xlabel="Date", # not enough space
        ylabel="Energy (Wh)",
        labels=[
            "Load (Wh)",
            "Household Device (Wh)",
            "Grid Feed-in (Wh)",
            "Grid Consumption (Wh)",
            "Losses (Wh)",
        ],
        markers=["o", "o", "x", "^", "^"],
        line_styles=["-", "--", ":", "-.", "-"],
    )
    report.finalize_group()

    # Group 3:
    report.create_line_chart_date(
        next_full_hour_date,  # start_date
        [results["result"]["akku_soc_pro_stunde"], results["result"]["EAuto_SoC_pro_Stunde"]],
        title="Battery SOC",
        # xlabel="Date", # not enough space
        ylabel="%",
        labels=[
            "Battery SOC (%)",
            "Electric Vehicle SOC (%)",
        ],
        markers=["o", "x"],
    )
    report.create_line_chart_date(
        next_full_hour_date,  # start_date
        [parameters.ems.strompreis_euro_pro_wh],
        # title="Electricity Price", # not enough space
        # xlabel="Date", # not enough space
        ylabel="Electricity Price (€/Wh)",
        x2label=None,  # not enough space
    )

    report.create_bar_chart(
        list(str(i) for i in range(len(results["ac_charge"]))),
        [results["ac_charge"], results["dc_charge"], results["discharge_allowed"]],
        title="AC/DC Charging and Discharge Overview",
        ylabel="Relative Power (0-1) / Discharge (0 or 1)",
        label_names=["AC Charging (relative)", "DC Charging (relative)", "Discharge Allowed"],
        colors=["blue", "green", "red"],
        bottom=3,
    )
    report.finalize_group()

    # Group 4:

    report.create_line_chart_date(
        next_full_hour_date,  # start_date
        [
            results["result"]["Kosten_Euro_pro_Stunde"],
            results["result"]["Einnahmen_Euro_pro_Stunde"],
        ],
        title="Financial Balance per Hour",
        # xlabel="Date", # not enough space
        ylabel="Euro",
        labels=["Costs", "Revenue"],
    )

    extra_data = results["extra_data"]
    report.create_scatter_plot(
        extra_data["verluste"],
        extra_data["bilanz"],
        title="",
        xlabel="losses",
        ylabel="balance",
        c=extra_data["nebenbedingung"],
    )

    values_list = [
        [
            results["result"]["Gesamtkosten_Euro"],
            results["result"]["Gesamteinnahmen_Euro"],
            results["result"]["Gesamtbilanz_Euro"],
        ]
    ]
    labels = ["Total Costs [€]", "Total Revenue [€]", "Total Balance [€]"]

    report.create_bar_chart(
        labels=labels,
        values_list=values_list,
        title="Financial Overview",
        ylabel="Euro",
        xlabels=["Total Costs [€]", "Total Revenue [€]", "Total Balance [€]"],
    )

    report.finalize_group()

    # Group 1: Scatter plot of losses vs balance with color-coded constraints
    f1 = np.array(extra_data["verluste"])  # Losses
    f2 = np.array(extra_data["bilanz"])  # Balance
    n1 = np.array(extra_data["nebenbedingung"])  # Constraints

    # Filter data where 'nebenbedingung' < 0.01
    filtered_indices = n1 < 0.01
    filtered_losses = f1[filtered_indices]
    filtered_balance = f2[filtered_indices]

    # Group 2: Violin plot for filtered losses
    if filtered_losses.size > 0:
        report.create_violin_plot(
            data_list=[filtered_losses],  # Data for filtered losses
            labels=["Filtered Losses"],  # Label for the violin plot
            title="Violin Plot for Filtered Losses (Constraint < 0.01)",
            xlabel="Losses",
            ylabel="Values",
        )
    else:
        print("No data available for filtered losses violin plot (Constraint < 0.01)")

    # Group 3: Violin plot for filtered balance
    if filtered_balance.size > 0:
        report.create_violin_plot(
            data_list=[filtered_balance],  # Data for filtered balance
            labels=["Filtered Balance"],  # Label for the violin plot
            title="Violin Plot for Filtered Balance (Constraint < 0.01)",
            xlabel="Balance",
            ylabel="Values",
        )
    else:
        print("No data available for filtered balance violin plot (Constraint < 0.01)")

    if filtered_balance.size > 0 or filtered_losses.size > 0:
        report.finalize_group()
    if logger.level == logging.DEBUG or results["fixed_seed"]:
        report.create_line_chart(
            0,
            [
                results["fitness_history"]["avg"],
                results["fitness_history"]["max"],
                results["fitness_history"]["min"],
            ],
            title=f"DEBUG: Generation Fitness for seed {results['fixed_seed']}",
            xlabel="Generation",
            ylabel="Fitness",
            labels=[
                "avg",
                "max",
                "min",
            ],
            markers=[".", ".", "."],
        )
        report.finalize_group()
    # Generate the PDF report
    report.generate_pdf()


def generate_example_report(filename: str = "example_report.pdf") -> None:
    """Generate example visualization report."""
    report = VisualizationReport(filename, "test")
    x_hours = 0  # Define x-axis start values (e.g., hours)

    # Group 1: Adding charts to be displayed on the same page
    report.create_line_chart(
        x_hours,
        [np.array([10, 20, 30, 40])],
        title="Load Profile",
        xlabel="Hours",
        ylabel="Load (Wh)",
    )
    report.create_line_chart(
        x_hours,
        [np.array([5, 15, 25, 35])],
        title="PV Forecast",
        xlabel="Hours",
        ylabel="PV Generation (Wh)",
    )
    report.create_line_chart(
        x_hours,
        [np.array([5, 15, 25, 35])],
        title="PV Forecast",
        xlabel="Hours",
        ylabel="PV Generation (Wh)",
    )
    # Note: If there are only 3 charts per page, the first is as wide as the page

    report.finalize_group()  # Finalize the first group of charts

    # Group 2: Adding more charts to be displayed on another page
    report.create_line_chart(
        x_hours,
        [np.array([0.2, 0.25, 0.3, 0.35])],
        title="Electricity Price",
        xlabel="Hours",
        ylabel="Price (€/Wh)",
    )
    report.create_bar_chart(
        ["Costs", "Revenue", "Balance"],
        [[500.0], [600.0], [100.0]],
        title="Financial Overview",
        ylabel="Euro",
        label_names=["AC Charging (relative)", "DC Charging (relative)", "Discharge Allowed"],
        colors=["red", "green", "blue"],
    )
    report.create_scatter_plot(
        np.array([5, 6, 7, 8]),
        np.array([100, 200, 150, 250]),
        title="Scatter Plot",
        xlabel="Losses",
        ylabel="Balance",
        c=np.array([0.1, 0.2, 0.3, 0.4]),
    )
    report.finalize_group()  # Finalize the second group of charts

    # Group 3: Adding a violin plot
    data = [np.random.normal(0, std, 100) for std in range(1, 5)]  # Example data for violin plot
    report.create_violin_plot(
        data,
        labels=["Group 1", "Group 2", "Group 3", "Group 4"],
        title="Violin Plot",
        xlabel="Groups",
        ylabel="Values",
    )
    data = [np.random.normal(0, 1, 100)]  # Example data for violin plot
    report.create_violin_plot(
        data, labels=["Group 1"], title="Violin Plot", xlabel="Group", ylabel="Values"
    )

    report.finalize_group()  # Finalize the third group of charts

    logger.setLevel(logging.DEBUG)  # set level for example report

    if logger.level == logging.DEBUG:
        report.create_line_chart(
            x_hours,
            [np.array([0.2, 0.25, 0.3, 0.35])],
            title="DEBUG",
            xlabel="DEBUG",
            ylabel="DEBUG",
        )
        report.finalize_group()  # Finalize the third group of charts

    report.add_text_page(
        text=" Bisher passierte folgendes:"
        "Am Anfang wurde das Universum erschaffen."
        "Das machte viele Leute sehr wütend und wurde allent-"
        "halben als Schritt in die falsche Richtung angesehen...",
        title="Don't Panic!",
        fontsize=14,
    )
    report.finalize_group()

    sample_json = {
        "name": "Visualization Report",
        "version": 1.0,
        "charts": [
            {"type": "line", "data_points": 50},
            {"type": "bar", "categories": 10},
        ],
        "metadata": {"author": "AI Assistant", "date": "2025-01-11"},
    }

    report.add_json_page(json_obj=sample_json, title="Formatted JSON Data", fontsize=10)
    report.finalize_group()

    report.create_line_chart_date(
        pendulum.now().subtract(hours=0),
        [list(np.random.random(840))],
        title="test",
        xlabel="test",
        ylabel="test",
    )
    report.finalize_group()
    # Generate the PDF report
    report.generate_pdf()


if __name__ == "__main__":
    generate_example_report()

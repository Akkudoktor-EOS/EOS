import os

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

from akkudoktoreos.config import output_dir


class VisualizationReport:
    def __init__(self, filename="visualization_results.pdf"):
        # Initialize the report with a given filename and empty groups
        self.filename = filename
        self.groups = []  # Store groups of charts
        self.current_group = []  # Store current group of charts being created
        self.pdf_pages = None  # Handle for the PDF output

    def add_chart_to_group(self, chart_func):
        """Add a chart function to the current group."""
        self.current_group.append(chart_func)

    def finalize_group(self):
        """Finalize the current group and prepare for a new group."""
        if self.current_group:  # Check if current group has charts
            self.groups.append(self.current_group)  # Add current group to groups
        else:
            print("Finalizing an empty group!")  # Warn if group is empty
        self.current_group = []  # Reset current group for new charts

    def _initialize_pdf(self):
        """Create the output directory if it doesn't exist and initialize the PDF."""
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)  # Create output directory
        output_file = os.path.join(output_dir, self.filename)  # Full path for PDF
        self.pdf_pages = PdfPages(output_file)  # Initialize PdfPages

    def _save_group_to_pdf(self, group):
        """Save a group of charts to the PDF."""
        fig_count = len(group)  # Number of charts in the group
        if fig_count == 0:
            print("Attempted to save an empty group to PDF!")  # Warn if group is empty
            return  # Prevent saving an empty group

        # Create a figure layout based on the number of charts
        if fig_count == 3:
            # Layout for three charts: 1 full-width on top, 2 below
            fig = plt.figure(figsize=(14, 10))  # Set a larger figure size
            ax1 = fig.add_subplot(2, 1, 1)  # Full-width subplot
            ax2 = fig.add_subplot(2, 2, 3)  # Bottom left subplot
            ax3 = fig.add_subplot(2, 2, 4)  # Bottom right subplot

            # Store axes in a list for easy access
            axs = [ax1, ax2, ax3]
        else:
            # Dynamic layout for any other number of charts
            cols = 2 if fig_count > 1 else 1  # Determine number of columns
            rows = (fig_count // 2) + (fig_count % 2)  # Calculate required rows
            fig, axs = plt.subplots(rows, cols, figsize=(14, 7 * rows))  # Create subplots
            axs = np.array(axs).reshape(-1)  # Flatten axes for easy indexing

        # Draw each chart in the corresponding axes
        for idx, chart_func in enumerate(group):
            plt.sca(axs[idx])  # Set current axes
            chart_func()  # Call the chart function to draw

        # Hide any unused axes
        for idx in range(fig_count, len(axs)):
            axs[idx].set_visible(False)  # Hide unused axes

        self.pdf_pages.savefig(fig)  # Save the figure to the PDF
        plt.close(fig)  # Close the figure to free up memory

    def create_line_chart(
        self, x, y_list, title, xlabel, ylabel, labels=None, markers=None, line_styles=None
    ):
        """Create a line chart and add it to the current group."""

        def chart():
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

    def create_scatter_plot(self, x, y, title, xlabel, ylabel, c=None):
        """Create a scatter plot and add it to the current group."""

        def chart():
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
        labels,
        values_list,
        title,
        ylabel,
        label_names,
        colors=None,
        bar_width=0.35,
        bottom=None,
    ):
        """Create a bar chart and add it to the current group."""

        def chart():
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

            plt.title(title)  # Set title
            plt.ylabel(ylabel)  # Set y-axis label

            if colors:
                plt.legend()  # Show legend if colors are provided
            plt.grid(True, zorder=0)  # Show grid in the background
            plt.xlim(-0.5, len(labels) - 0.5)  # Set x-axis limits

        self.add_chart_to_group(chart)  # Add chart function to current group

    def generate_pdf(self):
        """Generate the PDF report with all the added chart groups."""
        self._initialize_pdf()  # Initialize the PDF

        for group in self.groups:
            self._save_group_to_pdf(group)  # Save each group to the PDF

        self.pdf_pages.close()  # Close the PDF to finalize the report


# Example usage
report = VisualizationReport("example_report.pdf")
x_hours = np.arange(0, 4)  # Define x-axis values (e.g., hours)

# Group 1: Adding charts to be displayed on the same page
report.create_line_chart(
    x_hours, [np.array([10, 20, 30, 40])], title="Load Profile", xlabel="Hours", ylabel="Load (Wh)"
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
    [500, 600, 100],
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

# Generate the PDF report
report.generate_pdf()

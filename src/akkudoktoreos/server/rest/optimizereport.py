"""Transitional PDF report rendered from a OHMRAKEL42 planning outcome."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from akkudoktoreos.optimization.solution import PlanningOutcome


def optimization_report(outcome: "PlanningOutcome", *, hourly: bool = False) -> bytes:
    """Render a one-page optimization summary as PDF."""
    solution, result = outcome.solution, outcome.result
    lines = [
        "EOS optimization report (OHMRAKEL42)",
        f"Start: {solution.start_datetime.isoformat()}",
        f"Slot duration: {solution.interval_seconds} seconds",
        f"Simulated slots: {outcome.task.problem.slots}",
        f"Grid import: {sum(result.grid_import_wh) / 1000:.3f} kWh",
        f"Grid export: {sum(result.grid_export_wh) / 1000:.3f} kWh",
        f"Costs: {result.total_cost_eur:.4f} EUR",
        f"Feed-in revenue: {result.total_revenue_eur:.4f} EUR",
        f"Losses: {result.total_losses_wh:.1f} Wh",
        f"Objective: {outcome.breakdown.total_eur:.4f} EUR",
        "",
        "This report endpoint is deprecated.",
    ]
    if hourly:
        lines.extend(
            (
                "Read controls and result series in the response from POST /optimize.",
                "For configuration-based runs, use EOSdash or the energy-management APIs.",
            )
        )
    else:
        lines.extend(
            (
                "View schedules and series in EOSdash or use the energy-management plan",
                "and optimization solution JSON APIs.",
            )
        )
    # A single ASCII page using one of PDF's built-in fonts. Cross-reference
    # offsets are byte offsets from the header.
    text = []
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        text.append(f"({escaped}) Tj T*")
    stream = ("BT /F1 12 Tf 52 780 Td 19 TL\n" + "\n".join(text) + "\nET\n").encode("ascii")
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"endstream",
    )
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        pdf.extend(f"{offset:010} 00000 n \n".encode())
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(pdf)

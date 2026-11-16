"""Side-effect-free fleet planning API with explicit forecasts and device states."""

from fastapi import APIRouter

from akkudoktoreos.optimization.fleet import FleetRequest, FleetSolution, optimize_fleet

router = APIRouter(prefix="/v1/optimize", tags=["optimize"])


@router.post("/fleet", response_model=FleetSolution)
def post_fleet(request: FleetRequest) -> FleetSolution:
    """Plan multiple AC-connected devices; does not submit actuator commands."""
    return optimize_fleet(request)

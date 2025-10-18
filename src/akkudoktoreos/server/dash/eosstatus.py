# EOS server status information

from typing import Optional

from akkudoktoreos.config.config import SettingsEOS
from akkudoktoreos.core.emplan import EnergyManagementPlan
from akkudoktoreos.optimization.optimization import OptimizationSolution

# The latest information from the EOS server
eos_health: Optional[dict] = None
eos_solution: Optional[OptimizationSolution] = None
eos_plan: Optional[EnergyManagementPlan] = None
eos_config: Optional[SettingsEOS] = None

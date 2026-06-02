"""Memory evolution package for crystallization, consolidation, and auto-forget."""

from .auto_forget import AutoForget
from .auto_forget_service import AutoForgetService
from .consolidation_service import ConsolidationService
from .consolidator import Consolidator
from .contracts import (
    Action,
    ActionChain,
    ActionType,
    ConsolidatedPattern,
    CrystallizedMemory,
    MemoryEvolutionConfig,
    MemoryToForget,
)
from .crystallization_service import CrystallizationService
from .crystallizer import Crystallizer

__all__ = [
    "Action",
    "ActionChain",
    "ActionType",
    "AutoForget",
    "AutoForgetService",
    "ConsolidatedPattern",
    "ConsolidationService",
    "Consolidator",
    "CrystallizedMemory",
    "CrystallizationService",
    "Crystallizer",
    "MemoryEvolutionConfig",
    "MemoryToForget",
]

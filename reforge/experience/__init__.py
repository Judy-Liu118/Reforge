"""Experience loop: verifier-gated constraint cards, recalled by scope. Off by default (REFORGE_EXPERIENCE_CARDS=1).

See reforge/experience/README.md.
"""

from reforge.experience.cards import (ACTIVE, CONVENTION, EVICTED, QUARANTINED, TASK, ConstraintCard, Scope,
                                      VerifiedFailure)
from reforge.experience.loop import FLAG, ExperienceConfig, ExperienceLoop, MintResult
from reforge.experience.recall import TaskContext, TaskLevelRetriever
from reforge.experience.store import CanaryReport, ConstraintCardStore

__all__ = ["ACTIVE", "CONVENTION", "EVICTED", "QUARANTINED", "TASK", "ConstraintCard", "Scope", "VerifiedFailure",
           "FLAG", "ExperienceConfig", "ExperienceLoop", "MintResult", "TaskContext", "TaskLevelRetriever",
           "CanaryReport", "ConstraintCardStore"]

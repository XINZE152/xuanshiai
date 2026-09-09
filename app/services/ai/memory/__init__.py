"""Memory Kernel Core v1 service layer.

``app.services.ai.memory`` hosts the kernel building blocks:

- :mod:`ledger` — append-only event store with per-owner sequencing;
- :mod:`materializer` — event → view materialization (observation / claim /
  insight / state / suppression);
- :mod:`policy` — frozen source-priority / canonical-key / subject guards;
- :mod:`service` — MemoryService (propose / confirm / correct / suppress /
  lift), the only component higher layers (Shadow Write, API, backfill)
  should talk to.

Downstream consumers (Search / Compatibility / Recommend) intentionally keep
reading the legacy projections this phase: the kernel runs as Shadow Write.
"""

from app.services.ai.memory.consent_producers import (
    CONSENT_PRODUCER_DIMENSIONS,
    grant_projection_dimensions_for_consent,
    revoke_projection_dimensions_for_owner,
)
from app.services.ai.memory.consumers import (
    CounselorMemoryAdapter,
    PersonaMemoryAdapter,
    SanitizedMemoryContext,
    context_to_provider_messages,
)
from app.services.ai.memory.ledger import MemoryLedger
from app.services.ai.memory.materializer import (
    MemoryMaterializationError,
    MemoryMaterializer,
)
from app.services.ai.memory.policy import MemoryPolicy, MemoryPolicyDenied
from app.services.ai.memory.projection_policy import (
    ProjectionPolicy,
    ProjectionPolicyDenied,
    ProjectionFeatureNotEnabled,
)
from app.services.ai.memory.projections import (
    MemoryProjectionService,
    ProjectionContractError,
    ProjectionGrantDenied,
    ProjectionGrantNotFound,
)
from app.services.ai.memory.service import (
    MemoryClaimNotFound,
    MemoryClaimStateDenied,
    MemoryRevisionConflict,
    MemoryService,
)

__all__ = [
    "CONSENT_PRODUCER_DIMENSIONS",
    "CounselorMemoryAdapter",
    "context_to_provider_messages",
    "grant_projection_dimensions_for_consent",
    "MemoryClaimNotFound",
    "PersonaMemoryAdapter",
    "SanitizedMemoryContext",
    "MemoryClaimStateDenied",
    "MemoryLedger",
    "MemoryMaterializationError",
    "MemoryMaterializer",
    "MemoryPolicy",
    "MemoryPolicyDenied",
    "MemoryProjectionService",
    "MemoryRevisionConflict",
    "MemoryService",
    "ProjectionContractError",
    "ProjectionFeatureNotEnabled",
    "ProjectionGrantDenied",
    "ProjectionGrantNotFound",
    "ProjectionPolicy",
    "ProjectionPolicyDenied",
    "revoke_projection_dimensions_for_owner",
]

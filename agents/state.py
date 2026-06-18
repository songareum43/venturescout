"""Track C 에이전트 코드용 호환 export.

표준 런타임 State는 `shared.state`에 있고, 표준 스키마 계약은
`shared.contracts`에 있다. 예전 Track C 노드 파일들이 `agents.state`를
계속 import할 수 있도록 이 모듈을 남겨둔다.
"""

from __future__ import annotations

from typing import Any

from shared.contracts import (
    AgentRun,
    AnalysisJob,
    ClaimLimitation,
    Confidence,
    Decision,
    Depth,
    DocumentRecord,
    EvidenceItem,
    Hypothesis,
    IPOverlapCandidate,
    IdeaRecord,
    PatentClaim,
    Stance,
)
from shared.state import VentureScoutState


# 예전 노드 구현에서 쓰던 이름이다. 두 번째 스키마가 생기지 않도록 별칭으로 둔다.
EvidenceRef = EvidenceItem
HypothesisState = Hypothesis
AgentResult = dict[str, Any]

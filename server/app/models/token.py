"""Trust token response model matching the OTT protocol spec v0.2."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from .signals import SignalBundle


class ChecklistItem(BaseModel):
    category: str
    item: str
    status: str  # pass, fail, improve, available
    impact: str  # high, medium, low
    fix: str

    model_config = {"populate_by_name": True}


class ChecklistSummary(BaseModel):
    total: int
    passing: int
    failing: int
    improvable: int

    model_config = {"populate_by_name": True}


class CheckResponse(BaseModel):
    """Lightweight API response format (spec Section 10.1)."""
    domain: str
    checked_at: str = Field(alias="checkedAt")
    expires_at: str = Field(alias="expiresAt")
    signals: SignalBundle
    flags: list[str] = Field(default_factory=list)
    trust_score: int = Field(alias="trustScore")
    scoring_model: str = Field(default="ott-v1.2-weights", alias="scoringModel")
    site_category: str = Field(default="consumer", alias="siteCategory")
    jurisdiction: dict = Field(default_factory=dict)
    recommendation: str  # PROCEED, CAUTION, DENY
    reasoning: str
    # "ok" when the homepage fetched cleanly, "blocked" when the site
    # blocked our crawler (Cloudflare bot wall, residential IP filter,
    # etc). On "blocked", the content signal is excluded from the weighted
    # score and the remaining five signals are re-normalized to sum to 100%.
    crawlability: str = "ok"
    # "well_known" when the compositional brand anchor applied (top-10K
    # Tranco + aged + clean reputation + valid SSL), "scored" otherwise.
    # Consumers who want to filter anchored vs heuristic-only scores can
    # read this field directly rather than re-deriving from flags.
    brand_tier: str = Field(default="scored", alias="brandTier")
    checklist: list[ChecklistItem] = Field(default_factory=list)
    checklist_summary: ChecklistSummary = Field(alias="checklistSummary")
    signature: str
    issuer: str = "did:web:opentrusttoken.com"

    model_config = {"populate_by_name": True}


class CheckRequestBody(BaseModel):
    domain: str


class QueuedResponse(BaseModel):
    domain: str
    status: str = "queued"
    estimated_completion_seconds: int = Field(120, alias="estimatedCompletionSeconds")
    poll_url: str = Field(alias="pollUrl")

    model_config = {"populate_by_name": True}


class ErrorResponse(BaseModel):
    error: str
    message: str
    suggestion: Optional[str] = None
    last_known_score: Optional[int] = Field(None, alias="lastKnownScore")
    expired_at: Optional[str] = Field(None, alias="expiredAt")

    model_config = {"populate_by_name": True}

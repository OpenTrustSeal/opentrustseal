"""Parent-company linkage collector.

Looks up a domain against a static registry of known infrastructure
providers (CDNs, cloud hosts, serverless platforms, tracking vendors,
SaaS products). When a match is found, the domain inherits identity
evidence from its parent and its scoring uses the infrastructure model
instead of the default consumer-merchant model.

Why static + file-based (not ASN lookup):
- Static patterns cover 80% of the value at 1% of the complexity.
- Parent-company registries based on ASN-to-org are noisy; a domain
  resolving to AWS IP space is not necessarily "owned by" Amazon the
  way cloudfront.net is.
- Additions to this file ship with the repo and are auditable.

When no match is found, returns None. Callers that want a fallback
ASN-based lookup can add one later.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_DATA_FILE = Path(os.environ.get(
    "OTS_PARENT_COMPANIES_FILE",
    Path(__file__).resolve().parent.parent / "data" / "parent_companies.json"
))

_ENTRIES: list[dict] = []
_LOADED = False


@dataclass(frozen=True)
class ParentCompanyMatch:
    """Result of a parent-company lookup.

    parent is the canonical domain of the parent company (e.g. "amazon.com"
    for cloudfront.net). Downstream code reads the parent's OWN scored row
    from scored_results to inherit identity evidence.
    """
    parent: str
    parent_name: str
    category: str
    matched_suffix: str


def _load() -> None:
    global _ENTRIES, _LOADED
    if _LOADED:
        return
    try:
        data = json.loads(_DATA_FILE.read_text())
        _ENTRIES = data.get("entries", [])
        # Sort by suffix length descending so more-specific patterns win over
        # less-specific ones (e.g. "s3.amazonaws.com" matches before
        # "amazonaws.com" does).
        _ENTRIES.sort(key=lambda e: -len(e["suffix"]))
    except Exception:
        _ENTRIES = []
    _LOADED = True


def lookup(domain: str) -> Optional[ParentCompanyMatch]:
    """Return the first (longest-suffix) registry entry matching this domain.

    A domain matches a registry entry if the domain equals the suffix OR
    ends with "." + suffix. "foo.cloudfront.net" matches suffix
    "cloudfront.net". "cloudfront.net" itself also matches.
    """
    if not _LOADED:
        _load()
    if not domain:
        return None
    d = domain.strip().lower().rstrip(".")
    for entry in _ENTRIES:
        suffix = entry["suffix"].lower()
        if d == suffix or d.endswith("." + suffix):
            return ParentCompanyMatch(
                parent=entry["parent"],
                parent_name=entry["parent_name"],
                category=entry["category"],
                matched_suffix=suffix,
            )
    return None


def is_infrastructure_category(category: str) -> bool:
    """True if the parent-company category should trigger the infrastructure
    scoring path (content-weight deprioritized, security-headers weighted
    higher, privacy-policy absence not penalized)."""
    return category in {
        "cdn", "cloud_compute", "serverless", "hosting", "object_storage",
        "dns", "api_gateway", "tracking", "email"
    }


def reset_cache() -> None:
    """Test helper. Forces a re-read from disk on next lookup."""
    global _ENTRIES, _LOADED
    _ENTRIES = []
    _LOADED = False

"""Registration data models.

Privacy framework:
- Public fields: displayed in trust tokens and API responses
- Private fields: used for verification only, never exposed in API responses
- Verification status: public (e.g. "einVerified: true")
- Underlying data: private (e.g. actual EIN number)
"""

from pydantic import BaseModel, Field
from typing import Optional


class RegistrationRequest(BaseModel):
    """What a site owner submits during registration."""

    # Required
    domain: str
    business_name: str = Field(description="Legal business name")
    country: str = Field(description="Country of business registration")
    state_province: Optional[str] = Field(None, description="State or province")
    business_type: str = Field(description="corporation, llc, sole_proprietor, nonprofit, government, other")
    contact_email: str = Field(description="Primary contact email (private)")
    website_category: str = Field(description="ecommerce, saas, healthcare, media, education, finance, other")

    # Optional (more data = more score)
    contact_name: Optional[str] = Field(None, description="Name of person registering (private)")
    phone: Optional[str] = Field(None, description="Business phone (private)")
    address: Optional[str] = Field(None, description="Business address (private)")
    ein_tax_id: Optional[str] = Field(None, description="EIN or tax ID (private, used for verification only)")
    year_established: Optional[int] = Field(None, description="Year business was established")
    social_twitter: Optional[str] = Field(None, description="Twitter/X profile URL")
    social_linkedin: Optional[str] = Field(None, description="LinkedIn company page URL")

    # Domain verification method
    verification_method: str = Field(default="dns", description="dns or http")


class RegistrationPublicProfile(BaseModel):
    """What is shown publicly in API responses. Never includes private data."""

    domain: str
    business_name: str
    country: str
    state_province: Optional[str] = None
    business_type: str
    website_category: str
    year_established: Optional[int] = None
    registered_at: str

    # Verification statuses (public: shows WHAT was verified, not the data)
    domain_verified: bool = False
    email_domain_match: bool = False
    ein_verified: bool = False
    phone_verified: bool = False
    address_verified: bool = False
    social_verified: bool = False
    business_registry_match: bool = False
    business_name_whois_match: bool = False
    business_name_cert_match: bool = False

    # Computed
    verification_score: int = 0
    verification_details: list[str] = Field(default_factory=list)


class RegistrationPrivateRecord(BaseModel):
    """Full record stored in our database. Private fields encrypted at rest.

    NEVER returned in API responses. Used only for:
    - Internal verification processes
    - Dispute resolution (with legal authorization)
    - KYC upgrade (pre-populates fields)
    """

    domain: str
    business_name: str
    country: str
    state_province: Optional[str] = None
    business_type: str
    website_category: str
    year_established: Optional[int] = None

    # Private fields
    contact_name: Optional[str] = None
    contact_email: str
    phone: Optional[str] = None
    address: Optional[str] = None
    ein_tax_id: Optional[str] = None
    social_twitter: Optional[str] = None
    social_linkedin: Optional[str] = None

    # Metadata
    registered_at: str
    verified_at: Optional[str] = None
    verification_method: str
    ip_address: Optional[str] = None


# Registration scoring breakdown
REGISTRATION_SCORE_MAP = {
    "domain_verified": 3,       # Proved domain ownership
    "email_domain_match": 3,    # Contact email matches domain
    "business_name_matches": 5, # Name matches WHOIS or SSL cert org
    "ein_verified": 5,          # Tax ID confirmed against registry
    "phone_verified": 3,        # Phone matches what's on the site
    "address_verified": 3,      # Address confirmed as real commercial location
    "social_verified": 3,       # Social profiles link back to domain
    "business_registry": 5,     # Business found in state/national registry
}
# Maximum: 30 points added to identity signal

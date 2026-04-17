"""Trust signal models matching the OTT protocol spec v0.2."""

from pydantic import BaseModel, Field
from typing import Optional


class DomainAgeSignal(BaseModel):
    registered_date: Optional[str] = Field(None, alias="registeredDate")
    band: str = "unknown"
    score: int = 0

    model_config = {"populate_by_name": True}


class SSLSignal(BaseModel):
    valid: bool = False
    issuer: Optional[str] = None
    tls_version: Optional[str] = Field(None, alias="tlsVersion")
    hsts: bool = False
    score: int = 0

    model_config = {"populate_by_name": True}


class DNSSignal(BaseModel):
    spf: bool = False
    dmarc: bool = False
    dnssec: bool = False
    caa: bool = False
    score: int = 0


class ContentSignal(BaseModel):
    privacy_policy: bool = Field(False, alias="privacyPolicy")
    terms_of_service: bool = Field(False, alias="termsOfService")
    contact_info: bool = Field(False, alias="contactInfo")
    score: int = 0

    model_config = {"populate_by_name": True}


class ReputationSignal(BaseModel):
    malware: bool = False
    phishing: bool = False
    spam_listed: bool = Field(False, alias="spamListed")
    score: int = 0

    model_config = {"populate_by_name": True}


class IdentitySignal(BaseModel):
    verified: bool = False
    verification_tier: str = Field("automated", alias="verificationTier")
    whois_disclosed: bool = Field(False, alias="whoisDisclosed")
    business_directory: bool = Field(False, alias="businessDirectory")
    contact_on_site: bool = Field(False, alias="contactOnSite")
    score: int = 0

    model_config = {"populate_by_name": True}


class SignalBundle(BaseModel):
    domain_age: DomainAgeSignal = Field(default_factory=DomainAgeSignal, alias="domainAge")
    ssl: SSLSignal = Field(default_factory=SSLSignal)
    dns: DNSSignal = Field(default_factory=DNSSignal)
    content: ContentSignal = Field(default_factory=ContentSignal)
    reputation: ReputationSignal = Field(default_factory=ReputationSignal)
    identity: IdentitySignal = Field(default_factory=IdentitySignal)

    model_config = {"populate_by_name": True}

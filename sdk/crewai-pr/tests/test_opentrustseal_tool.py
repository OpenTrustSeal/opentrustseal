"""Unit tests for OpenTrustSealTool.

Mocks the OpenTrustSeal API so tests do not hit the live endpoint. Place
at lib/crewai-tools/tests/tools/test_opentrustseal_tool.py in the
crewAIInc/crewAI repo layout before opening the PR.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from opentrustseal_tool.opentrustseal_tool import OpenTrustSealTool


PROCEED_RESPONSE = {
    "domain": "stripe.com",
    "trustScore": 88,
    "recommendation": "PROCEED",
    "brandTier": "well_known",
    "reasoning": "Long-established publicly verified identity. Clean reputation.",
    "signals": {
        "reputation": {"score": 95, "malware": False, "phishing": False},
        "identity": {"score": 75},
        "content": {"score": 90},
        "ssl": {"score": 100},
        "dns": {"score": 95},
        "domainAge": {"score": 100},
    },
    "flags": [],
    "checklist": [],
    "jurisdiction": {"country": "US"},
    "confidence": "high",
    "cautionReason": None,
    "signature": "MEUCIQDxMockSignatureForTestPurposesAAAA",
}

CAUTION_LOW_CONFIDENCE_RESPONSE = {
    "domain": "example-blocked-merchant.com",
    "trustScore": 58,
    "recommendation": "CAUTION",
    "brandTier": "scored",
    "reasoning": "Content fetch blocked by bot protection.",
    "signals": {
        "reputation": {"score": 80, "malware": False, "phishing": False},
        "identity": {"score": 20},
        "content": {"score": 0},
        "ssl": {"score": 100},
        "dns": {"score": 60},
        "domainAge": {"score": 80},
    },
    "flags": ["CONTENT_UNSCORABLE"],
    "checklist": [],
    "jurisdiction": {"country": "US"},
    "confidence": "low",
    "cautionReason": "incomplete_evidence",
    "signature": "MEUCIQDxMockSignatureForTestPurposesBBBB",
}


def test_run_proceed_produces_expected_structure():
    tool = OpenTrustSealTool()
    mock_resp = MagicMock()
    mock_resp.json.return_value = PROCEED_RESPONSE
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.return_value = mock_resp

    with patch(
        "opentrustseal_tool.opentrustseal_tool.httpx.Client",
        return_value=mock_client,
    ):
        out = tool._run("stripe.com")

    lines = out.split("\n")
    assert lines[0] == "Domain: stripe.com"
    assert lines[1] == "Trust Score: 88/100 (PROCEED)"
    assert "Evidence confidence: high" in out
    assert "ACTION: Safe to proceed with this merchant." in out
    assert out.strip().endswith("(verify at did:web:opentrustseal.com)")


def test_run_low_confidence_caution_emits_low_dollar_guidance():
    tool = OpenTrustSealTool()
    mock_resp = MagicMock()
    mock_resp.json.return_value = CAUTION_LOW_CONFIDENCE_RESPONSE
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.return_value = mock_resp

    with patch(
        "opentrustseal_tool.opentrustseal_tool.httpx.Client",
        return_value=mock_client,
    ):
        out = tool._run("example-blocked-merchant.com")

    assert "Evidence confidence: low" in out
    assert "CAUTION reason: incomplete_evidence" in out
    assert "Low-dollar OK" in out


def test_run_handles_network_error_gracefully():
    tool = OpenTrustSealTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.side_effect = Exception("connection refused")

    with patch(
        "opentrustseal_tool.opentrustseal_tool.httpx.Client",
        return_value=mock_client,
    ):
        out = tool._run("unreachable.example")

    assert out.startswith("Error checking unreachable.example:")


def test_malware_flag_overrides_proceed_recommendation():
    resp = dict(PROCEED_RESPONSE)
    resp["signals"] = dict(resp["signals"])
    resp["signals"]["reputation"] = {"score": 0, "malware": True, "phishing": False}
    resp["recommendation"] = "DENY"
    resp["trustScore"] = 10

    tool = OpenTrustSealTool()
    mock_resp = MagicMock()
    mock_resp.json.return_value = resp
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.return_value = mock_resp

    with patch(
        "opentrustseal_tool.opentrustseal_tool.httpx.Client",
        return_value=mock_client,
    ):
        out = tool._run("malicious.example")

    assert "DO NOT proceed" in out


@pytest.mark.asyncio
async def test_arun_matches_run_output():
    tool = OpenTrustSealTool()

    mock_resp = MagicMock()
    mock_resp.json.return_value = PROCEED_RESPONSE
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)

    with patch(
        "opentrustseal_tool.opentrustseal_tool.httpx.AsyncClient",
        return_value=mock_client,
    ):
        out = await tool._arun("stripe.com")

    assert "Trust Score: 88/100 (PROCEED)" in out


def test_domain_normalization_strips_schemes_and_paths():
    tool = OpenTrustSealTool()
    assert tool._normalize_domain("https://Stripe.com/checkout") == "stripe.com"
    assert tool._normalize_domain("  HTTP://merchant.example  ") == "merchant.example"
    assert tool._normalize_domain("plain.com") == "plain.com"


def test_package_dependencies_declared():
    tool = OpenTrustSealTool()
    assert "httpx" in tool.package_dependencies


def test_env_vars_declared_as_non_required():
    tool = OpenTrustSealTool()
    names = [ev.name for ev in tool.env_vars]
    assert "OPENTRUSTSEAL_API_KEY" in names
    assert "OPENTRUSTSEAL_BASE_URL" in names
    for ev in tool.env_vars:
        assert ev.required is False

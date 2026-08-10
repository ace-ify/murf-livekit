"""Tests for Careva 100% Live Public API FastMCP Server."""

from unittest.mock import MagicMock

import pytest

from health_mcp_server import get_district_health_advisory, lookup_generic_medicine


@pytest.mark.asyncio
async def test_lookup_paracetamol_live_api():
    res = await lookup_generic_medicine("paracetamol")
    assert "paracetamol" in res.lower() or "acetaminophen" in res.lower()
    assert "generic" in res.lower()
    assert "Jan Aushadhi" in res


@pytest.mark.asyncio
async def test_lookup_metformin_live_api():
    res = await lookup_generic_medicine("metformin")
    assert "metformin" in res.lower()
    assert "Jan Aushadhi" in res


@pytest.mark.asyncio
async def test_lookup_unknown_medicine_fallback():
    res = await lookup_generic_medicine("FakeDrugXYZ999")
    assert "Jan Aushadhi" in res
    assert "PHC" in res or "medical officer" in res.lower()


@pytest.mark.asyncio
async def test_lookup_empty_query():
    res = await lookup_generic_medicine("")
    assert "naam" in res or "dawai" in res


@pytest.mark.asyncio
async def test_get_district_health_advisory_live_aqi():
    res = await get_district_health_advisory("Varanasi")
    assert "Varanasi" in res
    assert "AQI" in res or "Air Quality" in res
    assert "advisory" in res.lower() or "hawa" in res.lower()


@pytest.mark.asyncio
async def test_assistant_mcp_tools():
    from agent import Assistant

    assistant = Assistant()
    ctx = MagicMock()

    med_res = await assistant.lookup_generic_medicine(
        ctx, medicine_or_condition="paracetamol"
    )
    assert "paracetamol" in med_res.lower() or "acetaminophen" in med_res.lower()

    aqi_res = await assistant.get_district_health_advisory(ctx, district_or_city="Pune")
    assert "Pune" in aqi_res

from datetime import datetime

import pytest

from facilities import find_health_facilities


def test_find_facility_by_district():
    res = find_health_facilities("Varanasi")
    current_year = str(datetime.now().year)
    assert res["status"] == "success"
    assert len(res["facilities"]) > 0
    assert "Varanasi" in res["primary_facility"]["district"]
    assert current_year in res["spoken_summary"]


def test_find_facility_by_pincode():
    res = find_health_facilities("412207")  # Wagholi, Pune
    assert res["status"] == "success"
    assert "Wagholi" in res["primary_facility"]["name"]
    assert res["primary_facility"]["pincode"] == "412207"


def test_find_facility_with_filter():
    res = find_health_facilities("Varanasi", facility_type="CHC")
    assert res["status"] == "success"
    assert res["primary_facility"]["facility_type"] == "CHC"


def test_find_facility_not_found_fallback():
    res = find_health_facilities("Antarctica12345")
    current_year = str(datetime.now().year)
    assert res["status"] == "not_found"
    assert "108" in res["spoken_summary"]
    assert current_year in res["spoken_summary"]


@pytest.mark.asyncio
async def test_find_health_facilities_async_live():
    from facilities import find_health_facilities_async

    res = await find_health_facilities_async("Varanasi")
    current_year = str(datetime.now().year)
    assert res["status"] == "success"
    assert len(res["facilities"]) > 0
    assert current_year in res["spoken_summary"]


@pytest.mark.asyncio
async def test_find_health_facilities_async_pincode():
    from facilities import find_health_facilities_async

    res = await find_health_facilities_async("221002")
    assert res["status"] == "success"
    assert len(res["facilities"]) > 0

"""Live façade check, run only when a service URL is provided.

    DTO_FACADE_URL=http://127.0.0.1:5099/oslc/sysml pytest tests/test_facade_live.py

Kept out of the default run because it needs a server; kept in the repository because the
stub tests prove the adapter's logic and only a live read proves it works against the
service the paper describes.
"""
import os

import pytest

URL = os.environ.get("DTO_FACADE_URL")

pytestmark = pytest.mark.skipif(not URL, reason="set DTO_FACADE_URL to test against a live service")


def test_live_service_yields_counts():
    from digital_thread_overlay.facade import FacadeAdapter
    e = FacadeAdapter(timeout=10).extract(URL)
    facts = {f.key: f.value for f in e.facts}
    assert "facade:unreachable" not in facts, facts
    assert int(facts["facade:element:resources"]) > 0, "a seeded service must serve elements"
    assert len(e.sha256) == 64


def test_live_service_is_not_modified_by_reading():
    """Reading twice must not change what the service serves."""
    from digital_thread_overlay.facade import FacadeAdapter
    a = FacadeAdapter(timeout=10)
    first, second = a.extract(URL), a.extract(URL)
    assert first.sha256 == second.sha256, "a read-only facade must be idempotent"

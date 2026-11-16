"""The façade adapter: reading a service, adding nothing.

Three claims under test:
  1  it reports what the service serves, including the disagreement between two renderings;
  2  it is read-only by construction and by assertion - a façade that writes is a second
     control path onto someone else's data;
  3  an unreachable service produces a fact about the façade, not fabricated counts.
"""
from pathlib import Path

import pytest

from digital_thread_overlay import adapters
from digital_thread_overlay.facade import FacadeAdapter

BASE = "http://example.org/oslc/sysml"


def rdf(n: int) -> str:
    rows = "".join(f'<rdf:Description rdf:about="{BASE}/element/{i}"/>' for i in range(n))
    return f'<?xml version="1.0"?><rdf:RDF>{rows}</rdf:RDF>'


class Stub:
    """A fetcher that serves canned bodies and records every call."""

    def __init__(self, pages: dict[str, tuple[int, dict, str]]):
        self.pages = pages
        self.calls: list[str] = []

    def __call__(self, url: str, accept: str = "application/rdf+xml"):
        self.calls.append((url, accept))
        for suffix, response in self.pages.items():
            if url.endswith(suffix):
                return response
        return 404, {}, ""


def test_handles_only_urls():
    a = FacadeAdapter()
    assert a.handles("http://example.org/oslc/sysml")
    assert a.handles("https://example.org/x")
    assert not a.handles("/tmp/model.sysml")


def test_reads_counts_and_core_version():
    stub = Stub({
        "/sysml": (200, {"Oslc-Core-Version": "2.0"}, rdf(0)),
        "/element": (200, {}, rdf(12)),
        "/partDefinition": (200, {}, rdf(3)),
        "/relationship": (200, {}, rdf(0)),
    })
    e = FacadeAdapter(fetcher=stub).extract(BASE)
    facts = {f.key: f.value for f in e.facts}
    assert facts["facade:oslc-core-version"] == "2.0"
    assert facts["facade:element:resources"] == "12"
    assert facts["facade:partDefinition:resources"] == "3"
    assert facts["facade:relationship:resources"] == "0"
    assert e.kind == "oslc-service"
    assert e.adapter == "oslc-façade/1" or e.adapter == "oslc-facade/1"


def test_rendering_disagreement_is_recorded_not_smoothed():
    """If RDF and JSON-LD disagree on how many resources exist, that is a finding."""
    def fetcher(url, accept="application/rdf+xml"):
        if "json" in accept:
            return 200, {}, '{"member": [{}, {}, {}]}'      # 3
        if url.endswith("/element"):
            return 200, {}, rdf(4)                          # 4
        return 200, {}, rdf(0)

    facts = {f.key: f.value
             for f in FacadeAdapter(fetcher=fetcher, kinds=("element",)).extract(BASE).facts}
    assert facts["facade:element:resources"] == "4"
    assert facts["facade:element:rendering-mismatch"] == "rdf=4 json-ld=3"


def test_unreachable_service_yields_a_fact_not_a_guess():
    adapter = FacadeAdapter(
        fetcher=lambda url, accept="application/rdf+xml": (0, {"error": "connection refused"}, ""))
    e = adapter.extract(BASE)
    assert len(e.facts) == 1
    assert e.facts[0].key == "facade:unreachable"
    assert "connection refused" in e.facts[0].value
    assert not any(":resources" in f.key for f in e.facts), \
        "an unreachable service must not produce counts"


def test_facade_is_read_only():
    a = FacadeAdapter(fetcher=lambda url, accept="application/rdf+xml": (200, {}, rdf(1)))
    for verb in ("post", "put", "delete", "patch", "write"):
        assert not hasattr(a, verb), f"FacadeAdapter exposes {verb}"
    # and extraction only ever issues GET: the fetcher contract carries no method
    stub = Stub({"/sysml": (200, {}, rdf(0)), "/element": (200, {}, rdf(1)),
                 "/partDefinition": (200, {}, rdf(0)), "/relationship": (200, {}, rdf(0))})
    a2 = FacadeAdapter(fetcher=stub, kinds=("element",))
    a2.extract(BASE)
    assert stub.calls, "the adapter should have read something"
    assert all(u.startswith("http") for u, _ in stub.calls)


def test_digest_is_of_the_read_and_says_so():
    stub = Stub({"/sysml": (200, {}, rdf(0)), "/element": (200, {}, rdf(2)),
                 "/partDefinition": (200, {}, rdf(0)), "/relationship": (200, {}, rdf(0))})
    a = FacadeAdapter(fetcher=stub, kinds=("element",))
    first = a.extract(BASE)
    second = a.extract(BASE)
    assert first.sha256 == second.sha256, "the same read must digest the same"
    assert a.digest_of == "served-descriptions", \
        "the identity must be labelled as a digest of the read, not of an artifact"
    assert len(first.sha256) == 64


def test_adapters_extract_routes_a_url_to_the_facade():
    # no network is touched: the URL is unroutable, so the facade reports unreachable
    e = adapters.extract("http://127.0.0.1:9/oslc/sysml")
    assert e.adapter.startswith("oslc-facade")
    assert any(f.key == "facade:unreachable" for f in e.facts)

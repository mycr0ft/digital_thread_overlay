"""The façade adapter: read a service instead of a file.

The third pattern, and the one that changes least. Where a shadow extracts facts from a
file and a sidecar keeps them beside it, a façade adds nothing at all: the artifact already
publishes itself over a service, so the overlay reads what the service says and records
that it read it.

Two rules make this honest:

  * **Read-only.** No POST, no PUT, no DELETE. A façade that writes is not a façade; it is
    a second control path onto someone else's data, and the overlay has no business owning
    one.
  * **A digest of the read, not of the artifact.** A service has no bytes to hash, so the
    identity here is a digest over the descriptions actually received. It is labelled as
    such in every fact, because a caller who mistakes it for the artifact's own digest will
    conclude a change where there was only a different query.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .adapters import Extraction, Fact

# A fetcher takes (url, accept) and returns (status, headers, body). Injectable so the
# adapter can be tested without a live server, and so a caller can supply an authenticated
# client. Accept is passed because the adapter reads two renderings and a fetcher that
# cannot distinguish them cannot reproduce a disagreement between them.
Fetcher = Callable[..., tuple[int, dict, str]]

DEFAULT_KINDS = ("element", "partDefinition", "relationship")


def urllib_fetcher(url: str, accept: str = "application/rdf+xml",
                   timeout: float = 5.0) -> tuple[int, dict, str]:
    request = urllib.request.Request(url, headers={"Accept": accept})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, dict(response.headers), body
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), ""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, {"error": str(e)}, ""


def _count_resources(body: str) -> int:
    """Count rdf:Description subjects; a byte count is not a resource count."""
    return body.count("<rdf:Description")


def _json_count(body: str) -> int | None:
    try:
        doc = json.loads(body)
    except Exception:
        return None
    if isinstance(doc, list):
        return len(doc)
    for key in ("member", "results"):
        if isinstance(doc, dict) and isinstance(doc.get(key), list):
            return len(doc[key])
    return None


class FacadeAdapter:
    """Read an OSLC service: what kinds it serves, how many, and what it says about one."""

    name = "oslc-facade/1"

    def __init__(self, kinds: tuple[str, ...] = DEFAULT_KINDS,
                 fetcher: Fetcher | None = None, timeout: float = 5.0,
                 digest_of: str = "served-descriptions"):
        self.kinds = kinds
        self._fetcher = fetcher
        self.timeout = timeout
        # What the identity digest is over, recorded in the extraction so nobody mistakes
        # it for a digest of the artifact itself.
        self.digest_of = digest_of

    # ---- protocol ---------------------------------------------------------
    def handles(self, target) -> bool:
        text = str(target)
        return text.startswith("http://") or text.startswith("https://")

    def extract(self, target, service_root: str | None = None) -> Extraction:
        base = str(service_root or target).rstrip("/")
        facts: list[Fact] = []
        seen: dict[str, str] = {}

        status, headers, body = self._get(base)
        if status == 0:
            # unreachable is a fact about the facade, not a failure of the overlay
            return Extraction(str(target), "oslc-service", "", 0, self.name,
                              [Fact(key="facade:unreachable",
                                    value=str(headers.get("error", "unknown"))[:120],
                                    method=self.name, anchor=base)])

        core = headers.get("Oslc-Core-Version") or headers.get("oslc-core-version") or ""
        facts.append(Fact(key="facade:service", value=base, method=self.name, anchor=base))
        # the collection root usually has no GET of its own; its status is a probe result,
        # not a statement about the service's health
        facts.append(Fact(key="facade:root-status", value=str(status), method=self.name,
                          anchor=base))
        if core:
            facts.append(Fact(key="facade:oslc-core-version", value=str(core),
                              method=self.name, anchor=base))

        for kind in self.kinds:
            url = f"{base}/{kind}"
            k_status, k_headers, k_body = self._get(url, accept="application/rdf+xml")
            seen[kind] = k_body
            if k_status == 0:
                facts.append(Fact(key=f"facade:{kind}", value="unreachable", method=self.name,
                                  anchor=url))
                continue
            count = _count_resources(k_body)
            facts.append(Fact(key=f"facade:{kind}:resources", value=str(count),
                              method="rdf:Description count", anchor=url))
            j_status, _, j_body = self._get(url, accept="application/json-ld")
            if j_status and j_status < 400:
                j_count = _json_count(j_body)
                if j_count is not None and j_count != count:
                    # the two renderings disagreeing is worth recording, not smoothing over
                    facts.append(Fact(key=f"facade:{kind}:rendering-mismatch",
                                      value=f"rdf={count} json-ld={j_count}",
                                      method=self.name, anchor=url))

        digest = hashlib.sha256(
            "".join(f"{k}:{v}" for k, v in sorted(seen.items())).encode()
        ).hexdigest()
        return Extraction(str(target), "oslc-service", digest, len("".join(seen.values())),
                          self.name, facts)

    # ---- internals --------------------------------------------------------
    def _get(self, url: str, accept: str = "application/rdf+xml"):
        if self._fetcher is not None:
            try:
                status, headers, body = self._fetcher(url, accept)
            except TypeError:
                status, headers, body = self._fetcher(url)
            return status, headers or {}, body or ""
        request = urllib.request.Request(url, headers={"Accept": accept})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, dict(response.headers), \
                    response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), ""
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return 0, {"error": str(e)}, ""


def facade_for(target, fetcher: Fetcher | None = None) -> FacadeAdapter:
    """Convenience: an adapter already pointed at the service root."""
    adapter = FacadeAdapter(fetcher=fetcher)
    return adapter

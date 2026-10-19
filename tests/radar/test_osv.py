import asyncio
import json

import httpx
import pytest
import respx

from conftest import FIXTURES, fixture_bytes
from digest.http import make_client
from digest.models import Dependency
from digest.radar.osv import check_vulnerabilities

QUERYBATCH = "https://api.osv.dev/v1/querybatch"
VULN = "https://api.osv.dev/v1/vulns/{id}"
VULNS_DIR = FIXTURES / "radar/api.osv.dev/v1/vulns"


def lodash(version: str | None = "4.17.15") -> Dependency:
    return Dependency(ecosystem="npm", name="lodash", version=version, projects=["demo/web-app"])


def httpx_dep() -> Dependency:
    return Dependency(ecosystem="PyPI", name="httpx", version="0.27.0", projects=["demo/api"])


def mock_osv_fixtures() -> tuple[respx.Route, dict[str, respx.Route]]:
    batch = respx.post(QUERYBATCH).respond(
        200, content=fixture_bytes("radar/api.osv.dev/v1/querybatch.json")
    )
    details = {
        path.stem: respx.get(VULN.format(id=path.stem)).respond(200, content=path.read_bytes())
        for path in VULNS_DIR.glob("*.json")
    }
    return batch, details


def check(deps, reported=frozenset()):
    async def go():
        async with make_client() as client:
            return await check_vulnerabilities(client, deps, set(reported))

    return asyncio.run(go())


@respx.mock
def test_lodash_4_17_15_reports_vulnerabilities_from_osv() -> None:
    batch, _ = mock_osv_fixtures()

    entries = check([lodash(), httpx_dep()])

    assert json.loads(batch.calls.last.request.content) == {
        "queries": [
            {"package": {"name": "lodash", "ecosystem": "npm"}, "version": "4.17.15"},
            {"package": {"name": "httpx", "ecosystem": "PyPI"}, "version": "0.27.0"},
        ]
    }
    assert {e.package for e in entries} == {"lodash"}
    assert len(entries) == 6
    pollution = next(e for e in entries if e.key == "GHSA-p6mc-m468-83gw")
    assert pollution.kind == "vulnerability"
    assert pollution.title == "Prototype Pollution in lodash"
    assert pollution.severity == "HIGH"
    assert pollution.fixed_in == "4.17.19"  # from lodash's own ranges, not lodash-es etc.
    assert pollution.aliases == ["CVE-2020-8203"]
    assert pollution.url == "https://osv.dev/vulnerability/GHSA-p6mc-m468-83gw"
    assert pollution.projects == ["demo/web-app"]


@respx.mock
def test_already_reported_ids_and_aliases_are_skipped_without_detail_requests() -> None:
    _, details = mock_osv_fixtures()

    entries = check([lodash(), httpx_dep()], reported={"GHSA-p6mc-m468-83gw", "CVE-2020-28500"})

    keys = {e.key for e in entries}
    assert "GHSA-p6mc-m468-83gw" not in keys
    assert "GHSA-29mw-wpgm-hmr9" not in keys  # alias CVE-2020-28500 was reported
    assert len(keys) == 4
    assert not details["GHSA-p6mc-m468-83gw"].called
    assert details["GHSA-29mw-wpgm-hmr9"].called  # alias is only known from its details


@respx.mock
def test_same_issue_under_two_ids_is_listed_once_preferring_ghsa() -> None:
    respx.post(QUERYBATCH).respond(
        200, json={"results": [{"vulns": [{"id": "PYSEC-1"}, {"id": "GHSA-aaaa"}]}]}
    )
    respx.get(VULN.format(id="GHSA-aaaa")).respond(
        200, json={"id": "GHSA-aaaa", "aliases": ["PYSEC-1"], "summary": "ReDoS"}
    )
    respx.get(VULN.format(id="PYSEC-1")).respond(
        200, json={"id": "PYSEC-1", "aliases": ["GHSA-aaaa"]}
    )
    dep = Dependency(ecosystem="PyPI", name="pydantic", version="1.10.0")

    assert [e.key for e in check([dep])] == ["GHSA-aaaa"]


@respx.mock
def test_missing_details_still_report_the_id() -> None:
    respx.post(QUERYBATCH).respond(200, json={"results": [{"vulns": [{"id": "GHSA-bbbb"}]}]})
    respx.get(VULN.format(id="GHSA-bbbb")).respond(500)

    [entry] = check([lodash()])

    assert (entry.key, entry.title, entry.severity, entry.fixed_in) == (
        "GHSA-bbbb",
        "GHSA-bbbb",
        None,
        None,
    )


@respx.mock
def test_unversioned_dependencies_are_not_queried() -> None:
    batch = respx.post(QUERYBATCH).respond(200, json={"results": []})
    assert check([lodash(version=None)]) == []
    assert not batch.called


@respx.mock
def test_batch_failure_raises_for_the_caller_to_report() -> None:
    respx.post(QUERYBATCH).respond(400)
    with pytest.raises(httpx.HTTPStatusError):
        check([lodash()])


@respx.mock
def test_result_count_mismatch_is_an_error() -> None:
    respx.post(QUERYBATCH).respond(200, json={"results": []})
    with pytest.raises(ValueError):
        check([lodash()])

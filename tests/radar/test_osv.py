import asyncio
import json

import httpx
import respx

from conftest import fixture_bytes
from digest.http import make_client
from digest.models import Dependency
from digest.radar.osv import check_vulnerabilities

QUERYBATCH = "https://api.osv.dev/v1/querybatch"
VULN = "https://api.osv.dev/v1/vulns/{}"
LODASH_IDS = ["GHSA-29mw-wpgm-hmr9", "GHSA-35jh-r3h4-6jhm", "GHSA-p6mc-m468-83gw"]


def lodash(version: str | None = "4.17.15") -> Dependency:
    return Dependency(ecosystem="npm", name="lodash", version=version)


def mock_lodash_details() -> None:
    for osv_id in LODASH_IDS:
        respx.get(VULN.format(osv_id)).respond(
            200, content=fixture_bytes(f"radar_osv_{osv_id}.json")
        )


def check(deps, reported=None):
    async def go():
        async with make_client() as client:
            return await check_vulnerabilities(client, deps, reported or {})

    return asyncio.run(go())


@respx.mock
def test_lodash_4_17_15_reports_vulnerabilities_from_osv() -> None:
    route = respx.post(QUERYBATCH).respond(
        200, content=fixture_bytes("radar_osv_querybatch_lodash.json")
    )
    mock_lodash_details()

    entries = check([lodash()])

    assert json.loads(route.calls.last.request.content) == {
        "queries": [{"package": {"name": "lodash", "ecosystem": "npm"}, "version": "4.17.15"}]
    }
    assert {e.id for e in entries} == {f"vuln:{i}:npm:lodash" for i in LODASH_IDS}
    pollution = next(e for e in entries if "p6mc" in e.id)
    assert pollution.kind == "vulnerability"
    assert pollution.title == "Prototype Pollution in lodash"
    assert pollution.severity == "high"
    assert pollution.version == "4.17.19"  # first fixed version above 4.17.15
    assert pollution.installed == "4.17.15"
    assert pollution.url == "https://osv.dev/vulnerability/GHSA-p6mc-m468-83gw"


@respx.mock
def test_reported_vulnerabilities_are_not_fetched_again() -> None:
    respx.post(QUERYBATCH).respond(200, content=fixture_bytes("radar_osv_querybatch_lodash.json"))
    mock_lodash_details()
    reported = {f"vuln:{i}:npm:lodash": "2026-09-26" for i in LODASH_IDS[1:]}

    entries = check([lodash()], reported)

    assert [e.id for e in entries] == ["vuln:GHSA-29mw-wpgm-hmr9:npm:lodash"]
    assert not respx.get(VULN.format(LODASH_IDS[1])).called


@respx.mock
def test_aliases_are_reported_once() -> None:
    # PyPI often lists the same issue as both PYSEC-... and GHSA-...
    respx.post(QUERYBATCH).respond(
        200, json={"results": [{"vulns": [{"id": "PYSEC-2023-1"}, {"id": "GHSA-aaaa-bbbb-cccc"}]}]}
    )
    respx.get(VULN.format("GHSA-aaaa-bbbb-cccc")).respond(
        200, json={"id": "GHSA-aaaa-bbbb-cccc", "summary": "Bad", "aliases": ["PYSEC-2023-1"]}
    )
    respx.get(VULN.format("PYSEC-2023-1")).respond(
        200, json={"id": "PYSEC-2023-1", "aliases": ["GHSA-aaaa-bbbb-cccc"]}
    )
    dep = Dependency(ecosystem="PyPI", name="django", version="4.2.1")

    entries = check([dep])

    assert [e.id for e in entries] == ["vuln:GHSA-aaaa-bbbb-cccc:PyPI:django"]


@respx.mock
def test_missing_details_still_report_the_id() -> None:
    respx.post(QUERYBATCH).respond(200, json={"results": [{"vulns": [{"id": "GHSA-x"}]}]})
    respx.get(VULN.format("GHSA-x")).respond(500)

    entries = check([lodash()])

    assert entries[0].title == "GHSA-x"
    assert entries[0].version is None


@respx.mock
def test_dependencies_without_version_are_not_queried() -> None:
    route = respx.post(QUERYBATCH)

    assert check([lodash(None)]) == []
    assert not route.called


@respx.mock
def test_osv_failure_or_misaligned_answer_reports_nothing(caplog) -> None:
    respx.post(QUERYBATCH).mock(side_effect=httpx.ConnectError("down"))
    assert check([lodash()]) == []

    respx.post(QUERYBATCH).respond(200, json={"results": []})
    assert check([lodash()]) == []
    assert "OSV returned 0 results for 1 queries" in caplog.text

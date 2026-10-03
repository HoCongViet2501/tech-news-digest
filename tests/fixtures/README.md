# Test fixtures

Most files are real responses saved by `scripts/refresh_fixtures.py`.

Hand-built files (same shape as the real API, contents written for tests):

- `radar_manifest_package.json`, `radar_manifest_requirements.txt`, `radar_manifest_go.mod`:
  sample manifests. `radar_manifest_pyproject.toml` is a copy of this repo's `pyproject.toml`.
- `radar_releases_encode_httpx.json`: GitHub releases list; the 1.0.0 and 0.29.0b1 entries
  are invented to exercise major / prerelease / draft handling.
- `radar_osv_querybatch_lodash.json`, `radar_osv_GHSA-*.json`: OSV querybatch and vulnerability
  records for lodash 4.17.15, trimmed to the fields the radar reads.

`radar_npm_lodash.json` and `radar_pypi_httpx.json` are real registry responses
(PyPI trimmed: `releases`, `urls` and the long description removed).

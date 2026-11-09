"""Smoke test for a goldilocks-core HTTP backend: version, vocabulary, and a
real /explain (ML models + pseudopotentials) for Si scf and phonon.
Usage: python3 smoke.py http://127.0.0.1:18000"""

import json
import sys
import urllib.request

BASE = sys.argv[1].rstrip("/")
POSCAR = """Si
1.0
0.0 2.7155 2.7155
2.7155 0.0 2.7155
2.7155 2.7155 0.0
Si
2
Direct
0.0 0.0 0.0
0.25 0.25 0.25
"""


def call(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.load(response)


caps = call("/capabilities")
print("core", caps["core_version"], "vocabulary", caps["vocabulary_version"])
assert caps["core_version"] == "0.3.0", caps["core_version"]
assert caps["vocabulary_version"] == "5", caps["vocabulary_version"]
structure = {"structure_content": POSCAR, "structure_name": "POSCAR", "structure_format": "poscar"}
for task in ("scf_single_point", "phonon"):
    result = call(
        "/explain",
        {**structure, "code": caps["codes"][0]["id"], "task": task, "hpc": None,
         "overrides": {}, "fetch_missing": False},
    )
    records = result["records"]
    unresolved = sorted(k for k, v in records.items() if v["status"] != "resolved")
    print(f"{task}: {len(records)} records, blocking={len(result['blocking'])}, "
          f"unresolved={unresolved}, is_magnetic={records['is_magnetic'].get('source')}, "
          f"is_metal={records['is_metal'].get('source')}")
    assert not result["blocking"], result["blocking"]
print("SMOKE OK")

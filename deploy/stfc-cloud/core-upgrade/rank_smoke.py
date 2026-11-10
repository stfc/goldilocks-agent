"""Smoke test for goldilocks-core 0.3.1's ranking jobs, through the agent's
proxy: the old synchronous ranking is refused, listing works, a ranking job
shows a queue position and progress and finishes, and DELETE cancels one.
Standard library only. Usage: python3 rank_smoke.py http://127.0.0.1:8080"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1].rstrip("/")
FE = """data_Fe
_cell_length_a 2.8665
_cell_length_b 2.8665
_cell_length_c 2.8665
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_symmetry_space_group_name_H-M 'P 1'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Fe1 Fe 0 0 0
Fe2 Fe 0.5 0.5 0.5
"""
STRUCTURE = {"structure_content": FE, "structure_name": "Fe.cif"}


def call(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            text = response.read().decode()
            return response.status, json.loads(text) if text else None
    except urllib.error.HTTPError as error:
        return error.code, None


status, _ = call("POST", "/magnetic-orderings", {**STRUCTURE, "rank_with_mmace": True})
print("old synchronous ranking:", status)
assert status == 422, status
status, listing = call("POST", "/magnetic-orderings", STRUCTURE)
print("listing:", status, len(listing["candidates"]), "candidates")
assert status == 200

status, first = call("POST", "/magnetic-orderings/rank", STRUCTURE)
status2, second = call("POST", "/magnetic-orderings/rank", STRUCTURE)
print("submitted:", status, first, "|", status2, second)
assert status == status2 == 202 and second["position"] >= 1, (first, second)
status, _ = call("DELETE", f"/magnetic-orderings/rank/{second['job_id']}")
gone, _ = call("GET", f"/magnetic-orderings/rank/{second['job_id']}")
print("cancelled the queued one:", status, "then GET", gone)
assert status == 204 and gone == 404, (status, gone)

start = time.time()
last = None
while True:
    status, job = call("GET", f"/magnetic-orderings/rank/{first['job_id']}")
    assert status == 200, status
    now = (job["state"], json.dumps(job.get("progress")), job.get("position"))
    if now != last:
        elapsed = time.time() - start
        print(f"{elapsed:6.1f} s  {job['state']}  progress={job.get('progress')}")
        last = now
    if job["state"] in ("done", "failed"):
        break
    time.sleep(3)
assert job["state"] == "done", job.get("error")
report = job["report"]
recommended = [c for c in report["candidates"] if c.get("is_recommended")]
print(
    "ranked:",
    report["ranked"],
    "| recommended:",
    [(c["label"], c["energy_per_atom_ev"]) for c in recommended],
)
assert report["ranked"] and recommended, report.get("warnings")
print("RANK SMOKE OK")

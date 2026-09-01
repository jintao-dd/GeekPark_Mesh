import json
from app import preview_job

st = preview_job.get_state("2026-8-17")
print(json.dumps(st, ensure_ascii=False, indent=2))

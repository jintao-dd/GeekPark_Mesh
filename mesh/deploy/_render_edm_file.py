#!/usr/bin/env python3
"""Render EDM HTML from dumped issue JSON."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.edm import render_edm

inp = Path(sys.argv[1])
out = Path(sys.argv[2])
base = sys.argv[3] if len(sys.argv) > 3 else "https://mesh.geekpark.ai"

payload = json.loads(inp.read_text(encoding="utf-8"))
issue = payload["issue"]
data = payload["data"]
# strip heavy blobs not needed for template
for k in ("draft_json", "published_json", "preview_json"):
    issue.pop(k, None)

h, t = render_edm(issue, data, base)
out.write_text(h, encoding="utf-8")
txt_out = out.with_suffix(".txt")
txt_out.write_text(t, encoding="utf-8")
print("html", out, len(h))
print("text", txt_out, len(t))
print("relations", len(data.get("relations", [])))
print("question", (data.get("question") or "")[:60])

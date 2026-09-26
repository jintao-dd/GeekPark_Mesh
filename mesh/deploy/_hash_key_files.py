#!/usr/bin/env python3
import hashlib
import sys
files = [
    "app/relation_writer.py",
    "app/relation_gate.py",
    "app/relation_candidates.py",
    "app/main.py",
    "app/attribution_verify.py",
    "app/owner_guard.py",
    "app/issue_verify.py",
    "app/static/app.js",
    "app/static/dialog.js",
    "app/prompts/issue_relation_writer.md",
    "app/templates/admin.html",
    "app/templates/base.html",
    "app/templates/edm_email_inline.html",
]
root = sys.argv[1] if len(sys.argv) > 1 else "/srv/mesh"
for f in files:
    p = f"{root}/{f}"
    try:
        h = hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
        print(f"{h}  {f}")
    except FileNotFoundError:
        print(f"MISSING  {f}")

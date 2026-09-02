#!/usr/bin/env python3
import sys
from app import preview_job

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-9-2"
preview_job.start(slug, "deploy-coverage", force=True)
print(f"preview_started slug={slug}")

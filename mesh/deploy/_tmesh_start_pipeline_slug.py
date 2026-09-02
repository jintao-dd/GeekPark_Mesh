#!/usr/bin/env python3
import sys
from app import pipeline

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-02"
pipeline.start(slug, force=True)
print(f"pipeline_started slug={slug}")

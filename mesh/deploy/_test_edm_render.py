from app.edm import render_edm

h, _ = render_edm(
    {"slug": "2026-8-17", "period_label": "2026.8.17 - 8.27", "version": "v1.4", "updated_at": "now", "id": 1, "status": "published"},
    {"question": "q", "lead": "l", "kpis": [{"n": "14", "label": "x"}], "relations": [{"label": "ok", "title": "T", "body": "B", "sources": ["s"], "teams": ["A"]}]},
    "https://mesh.geekpark.ai",
)
print("hero_logo", "hero-logo" in h)
print("no_style_link", "style.css" not in h)
print("no_css_var", "var(--" not in h)
print("has_edm_css", "C6FF3F" in h)
print("abs_logo", "https://mesh.geekpark.ai/static/assets/mesh_logo.png" in h)
print("len", len(h))

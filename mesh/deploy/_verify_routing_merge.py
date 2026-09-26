#!/usr/bin/env python3
from app.relation_candidates import build_relation_candidates

items = [{
    "id": 2,
    "source_id": 11,
    "owner_team": "Global Partnership 团队",
    "pointer": "张岩",
    "entities": '["张岩","Notta"]',
    "roles": '["投资团队","编辑部"]',
    "text": "AGI 活动嘉宾张岩，投资团队用得上，编辑部采访池用得上",
    "source_label": "GP",
    "blocked": 0,
}]
c = build_relation_candidates(items)
routes = [x for x in c if x.get("candidate_kind") == "routing"]
print("routes", len(routes))
if routes:
    print("teams", routes[0]["teams"])
    print("targets", routes[0]["routing_targets"])

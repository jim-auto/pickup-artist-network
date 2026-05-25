from __future__ import annotations

import json
from pathlib import Path

from graph_model import export_html, load_graph
from scraper import SEED_FILE, build_growth_targets_payload, load_seed_entities

REVIEW_CANDIDATES_JSON = Path("data/review_candidates.json")
REVIEW_CANDIDATE_DECISIONS_JSON = Path("data/review_candidate_decisions.json")
THIN_CANDIDATE_DECISIONS_JSON = Path("data/thin_candidate_decisions.json")


def main() -> None:
    graph = load_graph("data/nodes.json", "data/edges.json")
    growth_targets_payload = build_growth_targets_payload(load_seed_entities(SEED_FILE))
    review_candidates_payload = {"generated_at": "", "candidates": []}
    review_candidate_decisions_payload = {"updated_at": "", "decisions": {}}
    thin_candidate_decisions_payload = {"updated_at": "", "decisions": {}}
    if REVIEW_CANDIDATES_JSON.exists():
        review_candidates_payload = json.loads(REVIEW_CANDIDATES_JSON.read_text(encoding="utf-8"))
    if REVIEW_CANDIDATE_DECISIONS_JSON.exists():
        review_candidate_decisions_payload = json.loads(
            REVIEW_CANDIDATE_DECISIONS_JSON.read_text(encoding="utf-8")
        )
    if THIN_CANDIDATE_DECISIONS_JSON.exists():
        thin_candidate_decisions_payload = json.loads(
            THIN_CANDIDATE_DECISIONS_JSON.read_text(encoding="utf-8")
        )
    export_html(
        graph,
        "docs/index.html",
        title="Pickup Artist Network",
        review_candidates_payload=review_candidates_payload,
        review_candidate_decisions_payload=review_candidate_decisions_payload,
        thin_candidate_decisions_payload=thin_candidate_decisions_payload,
        growth_targets_payload=growth_targets_payload,
    )
    print(f"[OK] docs/index.html generated with {len(graph.nodes)} nodes and {len(graph.edges)} edges")


if __name__ == "__main__":
    main()

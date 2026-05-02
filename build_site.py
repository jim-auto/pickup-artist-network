from __future__ import annotations

import argparse

from collector import COLLECTOR_CONFIG, X_PROFILE_CONFIG, collect_to_file
from graph_model import export_html
from scraper import (
    GENERATED_SNAPSHOT_FILE,
    SEED_FILE,
    build_graph_from_sources,
    build_growth_targets_payload,
    load_all_source_snapshots,
    load_generated_snapshots,
    load_review_candidate_decisions,
    load_seed_entities,
    materialize_inferred_social_edges,
    refresh_review_candidates,
    refresh_outputs,
    save_review_candidate_decisions,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collector -> validation -> graph -> HTML build flow for GitHub Pages."
    )
    parser.add_argument(
        "--skip-collector",
        action="store_true",
        help="Skip public-page collection and use existing generated snapshots.",
    )
    args = parser.parse_args()

    if not args.skip_collector and COLLECTOR_CONFIG.exists():
        collected = collect_to_file(
            COLLECTOR_CONFIG,
            X_PROFILE_CONFIG,
            GENERATED_SNAPSHOT_FILE,
        )
        print(f"[OK] collector refreshed {len(collected)} snapshots")

    seed_entities = load_seed_entities(SEED_FILE)
    growth_targets_payload = build_growth_targets_payload(seed_entities)
    snapshots = load_all_source_snapshots()
    decisions_payload = load_review_candidate_decisions()
    graph = build_graph_from_sources(seed_entities, snapshots)
    materialize_inferred_social_edges(
        graph,
        seed_entities,
        load_generated_snapshots(),
        decisions_payload,
    )
    refresh_outputs(graph)
    save_review_candidate_decisions(decisions_payload)
    review_candidates = refresh_review_candidates(
        seed_entities,
        load_generated_snapshots(),
        graph,
        decisions_payload=decisions_payload,
    )
    export_html(
        graph,
        "docs/index.html",
        title="Pickup Artist Network",
        review_candidates_payload=review_candidates,
        review_candidate_decisions_payload=decisions_payload,
        growth_targets_payload=growth_targets_payload,
    )
    headline = growth_targets_payload.get("headline", {})
    print(
        f"[OK] site build complete: {len(graph.nodes)} nodes / {len(graph.edges)} edges / "
        f"{len(review_candidates.get('candidates', []))} review candidates"
    )
    print(
        f"[OK] {headline.get('label', 'Real person target')}: "
        f"{headline.get('current', 0)} / {headline.get('target', 0)}"
    )


if __name__ == "__main__":
    main()

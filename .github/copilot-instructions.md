# Copilot Instructions

## Build and test commands

- Install runtime dependencies: `pip install -r requirements.txt`
- Install Playwright test dependencies: `pip install -r requirements-dev.txt`
- Run the full test suite: `python -m unittest discover -s tests`
- Run one unittest: `python -m unittest tests.test_scraper.ScraperSourceSnapshotTests.test_manual_snapshot_takes_precedence_over_generated_values`
- Run the Playwright Pages smoke test: `python -m unittest tests.test_pages_playwright.PickupArtistPagesPlaywrightTests.test_graph_data_loads_with_and_without_trailing_slash`
- Validate curated inputs without regenerating outputs: `python scraper.py --validate-only`
- Check real-person growth progress against the current target: `python scraper.py --growth-progress`
- Refresh generated snapshots from configured public sources: `python collector.py`
- Rebuild canonical graph outputs from current inputs: `python scraper.py`
- Rebuild only the published HTML from existing graph JSON: `python generate_html.py`
- Run the full publish flow locally: `python build_site.py`
- Run the CI-style publish flow without fetching fresh snapshots: `python build_site.py --skip-collector`

## High-level architecture

This repository is **JSON-first** and **manual-first**. The curated inputs are `seed_entities.txt` plus `data/source_snapshots.json`. `collector.py` is an ingestion step that reads `data/collector_sources.json` and `data/x_profile_sources.json`, fetches public pages and X profile data, and writes only the intermediate generated snapshot file `data/source_snapshots.generated.json`.

`scraper.py` is the normalization and export layer. It merges manual and generated snapshots, validates them, builds the canonical `GraphData`, materializes selected inferred social edges from generated text, and writes the derived artifacts in `data/` (`nodes.json`, `edges.json`, CSV exports, NetworkX metrics, SQLite DB, review candidate queue, and candidate decision log).

`graph_model.py` owns the shared schema and downstream exports: node/edge validation, relation querying, CSV/SQLite/NetworkX projection, clustering payloads, and static HTML generation. `export_html()` writes both `docs/index.html` and a sibling `docs/graph-data.json`; the published page reads that companion JSON at runtime instead of embedding the whole graph inline.

`build_site.py` is the publish entry point used for local full builds and GitHub Pages CI. It runs collector refresh (unless `--skip-collector` is passed), rebuilds the graph, refreshes review artifacts, and regenerates the static site.

## Key conventions

- Treat `seed_entities.txt` and `data/source_snapshots.json` as the main source files. If a change belongs in the curated graph, update those inputs and regenerate outputs instead of hand-editing `data/nodes.json`, `data/edges.json`, or `docs/graph-data.json`.
- `seed_entities.txt` uses `type|id|name|aliases|scope`. The `scope` field (`real` or `fictional`) is part of the model, and growth progress only counts `real` seed entities.
- When growing the person graph, prioritize **self-described public X profiles** and explicitly linked related accounts. Do not add people from third-party rumors, exposés, or label-only references.
- Manual snapshots take precedence over generated snapshots for the same account. When fields conflict, generated data is not allowed to silently overwrite curated data; it is merged with `needs_review` and `review_notes`.
- Snapshot, node, and edge records rely on `source_urls`, `confidence`, `evidence_kind`, `needs_review`, and `review_notes` to separate factual data from interpretation. Preserve that distinction when adding or editing graph data.
- Keep the public dataset **real-only** for curated seeds and manual snapshots; use fictional or experimental data only in tests or optional local fixtures.
- Review candidates are generated from `summary`, `profile_text`, and `pinned_post_text`, then consolidated by `source -> target -> type`. Approving a candidate promotes it into a manual observation in `data/source_snapshots.json`; dismissing it records suppression metadata in `data/review_candidate_decisions.json`.
- Collector output is intentionally constrained: generated public-page links are filtered down to high-signal X links, and optional pinned-post hints belong in `data/x_profile_sources.json` via `pinned_post_url`.
- Keep GitHub Pages path behavior intact when editing the frontend export. The repo has a Playwright smoke test that expects the site to work at both `/docs/` and `/docs` and depends on `graph-data.json` being emitted next to `index.html`.
- Preserve domain modeling conventions that are already encoded in seeds and clustering logic: `えるスタ` is a `community` node even when it appears inside person names, and `ピカ講習` / `ピカ外見コンサル` belong to the same keyword cluster.

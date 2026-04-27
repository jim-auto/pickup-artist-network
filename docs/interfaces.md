# Interfaces

最小インターフェースは JSON 正本を中心に据えた次の 7 個に絞る。

```python
add_node(graph: GraphData, node_payload: dict | Node) -> Node
add_edge(graph: GraphData, edge_payload: dict | Edge) -> Edge
load_graph(nodes_path: str = "data/nodes.json", edges_path: str = "data/edges.json") -> GraphData
save_graph(graph: GraphData, nodes_path: str = "data/nodes.json", edges_path: str = "data/edges.json") -> None
query_relations(graph: GraphData, search_term: str = "", node_type: str | None = None, edge_type: str | None = None, node_id: str | None = None, direction: str = "both") -> dict
export_html(graph: GraphData, output_path: str = "docs/index.html", title: str = "Pickup Artist Network", review_candidates_payload: dict | None = None, review_candidate_decisions_payload: dict | None = None, growth_targets_payload: dict | None = None) -> None
export_csv(graph: GraphData, nodes_csv_path: str = "data/nodes.csv", edges_csv_path: str = "data/edges.csv") -> None
```

## Intent

- `add_node(...)`
  - `id`, `type`, `name`, `aliases`, `description`, `source_urls`, `confidence` を持つ node を追加する
- `add_edge(...)`
  - `source`, `target`, `type`, `description`, `source_urls`, `confidence` を持つ edge を追加する
- `load_graph(...)`
  - JSON を読み込んで `GraphData` を返す
- `save_graph(...)`
  - `GraphData` を JSON に保存する
- `query_relations(...)`
  - 名前検索、type フィルタ、ある node の近傍取得、incoming / outgoing の切り分けに使う
- `export_html(...)`
  - GitHub Pages 向け静的 HTML を出力し、必要なら review-only candidate payload、candidate decision log payload、growth target payload も埋め込む
- `export_csv(...)`
  - JSON 正本から CSV を派生出力する

この段階では API を増やしすぎず、手動入力・可視化・軽い分析へつながる最小面積を維持する。

補助スクリプトとして、次も運用上の入口になる。

```bash
python collector.py
python scraper.py --query "shibuya" --query-direction incoming
python scraper.py --approve-candidate "<candidate-id>"
python scraper.py --dismiss-candidate "<candidate-id>"
python scraper.py --list-review-candidates
python scraper.py --list-candidate-decisions
python scraper.py --growth-progress
python build_site.py
```

- `collector.py`
  - approved public page と `data/x_profile_sources.json` の X profile から `data/source_snapshots.generated.json` を作る
- `build_site.py`
  - collector -> graph build -> review candidate export -> HTML export をまとめて回す
- `scraper.py --approve-candidate ...`
  - review-only candidate を manual snapshot observation へ昇格し、canonical graph と HTML を更新する
- `scraper.py --dismiss-candidate ...`
  - review-only candidate を dismiss 済みにし、再生成 queue から除外する
- `scraper.py --list-review-candidates`
  - 現在の review-only candidate queue を terminal に一覧表示する
- `scraper.py --list-candidate-decisions`
  - approve / dismiss 済みの candidate decision log を terminal に一覧表示する
- `scraper.py --growth-progress`
  - `seed_entities.txt` の `scope` をもとに real 側の現在値と target を terminal に表示する

補助 UI として `docs/index.html` には `needs_review` の node / edge を一覧する review queue、review-only 候補の queue、approve / dismiss 済み decision log、real growth target panel を持たせている。

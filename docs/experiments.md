# Experiments

## Seed repo から流用した構成

`sokusuu-ranking` から次の骨格を引き継いだ。

- `seed_*` ファイルを起点にする
- `scraper.py` を収集・整形の入口にする
- `generate_html.py` を GitHub Pages 出力に使う
- `data/` に JSON / CSV / 派生物を置く
- `docs/` に静的公開物を置く

今回は「数値抽出」ではなく「関係整理」に目的を置き換え、手動入力優先で組み直した。

## Comparison

| Approach | Prototype | 長所 | 短所 | 実装コスト | 今後の拡張性 | 可視化のしやすさ |
| --- | --- | --- | --- | --- | --- | --- |
| A: 素朴な JSON | `data/nodes.json`, `data/edges.json`, `docs/index.html` | 手編集しやすい、差分レビューしやすい、GitHub Pages まで最短 | 件数が増えると整合性管理が手作業寄り | 低い | 中 | 高い |
| B: NetworkX | `data/networkx_metrics.json` | centrality / PageRank / clustering など分析へ広げやすい | 正本には向かず、手編集には不向き | 低い | 高い | 中 |
| C: SQLite | `data/graph.db`, `relation_view` | クエリしやすく、将来 UI/API を生やしやすい | 初期編集フローが JSON より重い | 中 | 高い | 中 |

## Notes per approach

### Approach A

- 最小実装として最も扱いやすい
- node / edge の schema を固定しやすい
- Git 管理と GitHub Pages 公開の相性が良い

### Approach B

- 現時点では JSON から投影するだけで十分
- 分析要件が増えたときに追加コストが小さい
- 正本を NetworkX にすると手入力の UX が落ちる

### Approach C

- `relation_view` のようなビューを置けば探索クエリが簡単
- ただし今は DB を先に正本にするほどデータ量も運用複雑性もない
- 将来 API 化や複数入力源統合が必要になったら再評価しやすい

## Conclusion

今の段階では **A を正本**、**B と C を派生レイヤ** とするのが最も安い。  
まずは JSON を育て、その後の分析や検索要求が増えたら B/C の比重を上げる。

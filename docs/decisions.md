# Decisions

## Current decision

**Approach A (JSON-first) を採用する。**

## Current growth target

最初の定量目標は **real person 20人**。  
補助レンジは `community 8-12 / content 12-18 / location 6-10 / platform 6-8` とし、review queue を手で回せるサイズを維持する。

## Why

1. 手動で node / edge を追加・修正しやすい
2. Git 差分でレビューしやすい
3. `generate_html.py` から GitHub Pages へ最短でつながる
4. `sokusuu-ranking` の seed -> scraper -> docs の流れを保ちやすい

## What is deferred

- 自動スクレイピングの本格実装
- SQLite を正本にした管理画面
- 高度なクラスタ検出やランキング UI

## Adopted structure

- 収集入口: `seed_entities.txt`, `data/source_snapshots.json`, `data/collector_sources.json`
- X profile 収集入口: `data/x_profile_sources.json`
- 自動収集の中間出力: `data/source_snapshots.generated.json`
- sample 用 generated hint fixture: `data/source_snapshots.generated.hints.json`
- review-only 候補: `data/review_candidates.json`
- candidate decision log: `data/review_candidate_decisions.json`
- 正本: `data/nodes.json`, `data/edges.json`
- 派生: `data/nodes.csv`, `data/edges.csv`
- 分析試作: `data/networkx_metrics.json`
- クエリ試作: `data/graph.db`
- 公開: `docs/index.html`

## Current curation boundary

- 実在データはまず **platform / location / official public community** などの low-risk node から入れる
- official guide page のような **public content node** も low-risk source として扱ってよい
- person / community / content は少量ずつ、公開情報・source URL・confidence を揃えて追加する
- real review candidate を増やすときは、既存 location を本文で自然に言及する official tourism / visitors bureau などの safe public source を優先する
- review candidate queue は basis ごとの重複を避け、同じ `source -> target -> type` を consolidated candidate として扱う
- content source が location を言及した場合は `reference` を優先し、community source の location mention とは分けて扱う
- `seed_entities.txt` では `scope` (`real` / `fictional`) を持たせ、HTML の progress panel では real side の現在値だけを target と比較する
- 初期の構造確認用サンプルは fictional data を混在させる
- collector は canonical graph を直接触らず、generated snapshot だけを書く
- collector は public page 由来 link を **X only** に絞り、same-platform skip / allowlist / URL denylist を通した高信号リンクだけを残す
- X profile の logged-out HTML だけでは pinned status を安定検出できないため、必要な pinned-post hint は `data/x_profile_sources.json` の optional `pinned_post_url` で補う
- live public data だけでは review candidate queue が薄くなりやすいため、fictional sample cluster には generated hint fixture を別 file で足して workflow を常時確認できるようにする
- manual snapshot と generated snapshot が競合する場合は **manual を優先**し、generated 差分は review note へ退避する
- approve / dismiss の decision は `data/review_candidate_decisions.json` に候補 metadata ごと残し、HTML から triage 履歴を追えるようにする

## Revisit triggers

- node / edge 数が増えて JSON 手編集で整合性維持がつらくなったとき
- 分析指標を継続的に出したくなったとき
- 検索 UI や API が必要になったとき

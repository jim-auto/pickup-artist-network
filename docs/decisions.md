# Decisions

## Current decision

**Approach A (JSON-first) を採用する。**

## Current growth target

次の定量目標は **real person 500人**、最終目標は **1000人**。  
現在は **375人**。補助レンジは `community 8-12 / content 12-18 / location 6-10 / platform 6-8` としつつ、人物側は 20 / 50 / 100 / 200 / 500 / 1000 の段階で広げる。

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
- optional generated hint fixture: `data/source_snapshots.generated.hints.json`
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
- real person を最初に足すときは、**本人が公開 X profile で自称している** アカウントだけに絞り、第三者ラベルや噂ベースでは追加しない
- real review candidate を増やすときは、既存 location を本文で自然に言及する official tourism / visitors bureau などの safe public source を優先する
- review candidate queue は basis ごとの重複を避け、同じ `source -> target -> type` を consolidated candidate として扱う
- content source が location を言及した場合は `reference` を優先し、community source の location mention とは分けて扱う
- `seed_entities.txt` では `scope` (`real` / `fictional`) を持たせ、HTML の progress panel では real side の現在値だけを target と比較する
- 公開用 seed / manual snapshot は real-only にし、fictional fixture はテストやローカル実験用の optional file に閉じ込める
- collector は canonical graph を直接触らず、generated snapshot だけを書く
- collector は public page 由来 link を **X only** に絞り、same-platform skip / allowlist / URL denylist を通した高信号リンクだけを残す
- X profile の logged-out HTML だけでは pinned status を安定検出できないため、必要な pinned-post hint は `data/x_profile_sources.json` の optional `pinned_post_url` で補う
- generated hint fixture は必要なら別 file で使えるが、公開 build では空にして実在データだけを出す
- manual snapshot と generated snapshot が競合する場合は **manual を優先**し、generated 差分は review note へ退避する
- approve / dismiss の decision は `data/review_candidate_decisions.json` に候補 metadata ごと残し、HTML から triage 履歴を追えるようにする
- 最初の real person cluster は、本人が X bio で自称している 5 アカウントを seed に追加し、明示された活動場所だけを approve した
- 次の拡張で self-described public X profile をさらに 5 アカウント追加し、real person 10 / 20 に到達した
- その次の拡張で関連 side account を 3 件追加し、公開 graph を fictional なしの real-only 構成へ切り替えた
- さらに self-described / linked side account を 6 件追加し、matching-apps / side-account relation を明示できる範囲で増やして real person 19 / 20 まで進めた
- `sokusuu-ranking` seed とそこから辿れる side-account mention を一括スクリーニングし、公開 graph を real person 105 / 200 まで拡張した
- authenticated following で見つかった未登録 handle を public bio で再スクリーニングし、16 アカウントを追加して real person 121 / 200 まで拡張した
- 「界隈を見渡せる地図」を目指すため、成長目標を 200 から 1000 へ引き上げ、直近マイルストーンを 500 に置いた
- following をさらに広げて 23 アカウントを追加し、fallback snapshot 保護も入れて real person 144 / 1000 まで拡張した
- さらに 14 アカウントを追加し、`otaku-pua -> mendako-pua` の mentor 系 relation を明示して real person 174 / 1000 まで拡張した
- さらに 14 アカウントを追加し、`futaro-pua -> wing-nampa` や `riokun-pua -> riokun-pua-sub` などの明示 relation も足して real person 188 / 1000 まで拡張した
- さらに 4 アカウントを追加し、`kurita-pua -> sub-kurita` と `pg-yoasobi -> nanpa-pegasasu` を明示して real person 192 / 1000 まで拡張した
- さらに 15 アカウントを追加し、`nampa-poke -> pua-poke`、`tyopa-pua -> tyopa-sub`、`hantenkinoyama -> daigakusei-pua`、`namuskun -> namusubkun` などの明示 relation を足して real person 207 / 1000 まで拡張した
- さらに 5 アカウントを追加し、`yutayuta-pua -> wing-nampa` と `nanpa-zin -> pika-pua` を明示して real person 212 / 1000 まで拡張した
- さらに 7 アカウントを追加し、self-described public profile を中心に real person 219 / 1000 まで拡張した
- さらに 9 アカウントを追加し、`nampa-urajirou -> real-nampa` と `machapua3 -> xcandee` の side-account relation も入れて real person 228 / 1000 まで拡張した
- さらに 6 アカウントを追加し、self-described public profile を中心に real person 234 / 1000 まで拡張した
- さらに 6 アカウントを追加し、`kurosakikun -> k-932654` と `hameyuuuu -> yato-mote` の relation も足して real person 240 / 1000 まで拡張した
- さらに 5 アカウントを追加し、self-described public profile を中心に real person 245 / 1000 まで拡張した
- さらに 5 アカウントを追加し、`shinkawa-pua -> korilla-pua` と `mbh-hal -> gureran-m` の relation も足して real person 250 / 1000 まで拡張した
- さらに 6 アカウントを追加し、`otaku-pua -> qh0kum`、`kei-pua -> eb6lx`、`gintoki-street -> minigola-street`、`kimu-himitsu2 -> ziyuunotsubasa1 / like-himitsu` の relation も足して real person 256 / 1000 まで拡張した
- さらに 7 アカウントを追加し、`tyopa-pua -> streetkyo` の explicit reference も足して real person 263 / 1000 まで拡張した
- さらに 9 アカウントを追加し、`fake-pua`、`tenma-pua`、`slice-pua`、`instapua`、`kanbee-pua`、`atari100pua`、`osugi-pua`、`mayuge-mbh`、`kamekame-pua` を次の following seed 候補として real person 272 / 1000 まで拡張した
- さらに 17 アカウントを追加し、`street-win-pua`、`naepua`、`asai-pua`、`chami-pua`、`yuta-pua`、`oyajii-nanpa`、`taichi-pua`、`yuki100-pua`、`tensai-nanpa4`、`hpns-pua`、`tohokupua`、`ak1-pua`、`ao-pua`、`ike-pua`、`gupy-pua`、`bonnoupua2`、`idiot-pua` を self-described public profile として real person 289 / 1000 まで拡張した
- さらに 10 アカウントを追加し、`roco-neko -> roco-neko-ura` の side-account relation も足して real person 299 / 1000 まで拡張した
- さらに 8 アカウントを追加し、`chiroru-pua`、`yokono-pua`、`scream-pua`、`karon-pua`、`asumi-pua`、`taku-pua`、`nampa1998`、`puriketsu-nnp` を self-described public profile として real person 307 / 1000 まで拡張した
- さらに 9 アカウントを追加し、`ururunpua`、`motebody-pua`、`girl-pua`、`saku-pua`、`doronpa-pua`、`riku-pua0801`、`ale-puapua`、`rojou-ski`、`jaws-girlhunter` を self-described public profile として real person 316 / 1000 まで拡張した
- さらに 9 アカウントを追加し、`chamuranaoto -> pua-co` の mentor relation も足して real person 325 / 1000 まで拡張した
- さらに 9 アカウントを追加し、`so-pua`、`one-sith-pua`、`shu-pua`、`jiro-suto`、`mechinanpa`、`chiroru-pua-main`、`liberty-pai`、`to-suto-tore`、`zushi-tokyo` を self-described public profile として real person 334 / 1000 まで拡張した
- さらに 5 アカウントを追加し、`utopua2`、`daruma-nnp`、`hikosan-nn`、`seiyoku-genkai`、`nampa-mimato` を self-described public profile として real person 339 / 1000 まで拡張した
- さらに 7 アカウントを追加し、`tinder109 -> hachi-tinder02` の side-account relation も足して real person 346 / 1000 まで拡張した
- さらに 8 アカウントを追加し、`salmon-nnp`、`juju-pua`、`suto-taro`、`neru-pua`、`nanpashi-miffy`、`seizitunanpa`、`paaman-pua`、`resunnme` を self-described public profile として real person 354 / 1000 まで拡張した
- さらに 6 アカウントを追加し、`maruoooon-pua -> gureran-m` と `knk-stnn -> wing-nampa` の relation も足して real person 360 / 1000 まで拡張した
- さらに 7 アカウントを追加し、`kazu-pua -> wing-nampa` と `miraitinder6969 -> miraitinder4545` の relation も足して real person 367 / 1000 まで拡張した
- さらに 8 アカウントを追加し、`nekominto-pua`、`carbii-pua`、`ryuuk-pua`、`west-pua`、`tomo-pua11`、`senga-pua`、`kill-pua`、`sandarupua` を self-described public profile として real person 375 / 1000 まで拡張した
- X profile collector は profile の `external_url` も generated snapshot の `links` に残し、`note.mu` も note として扱う
- 日本語の短い alias（例: `新宿`, `渋谷`, `池袋`）も review candidate matcher にかかるようにする

## Revisit triggers

- node / edge 数が増えて JSON 手編集で整合性維持がつらくなったとき
- 分析指標を継続的に出したくなったとき
- 検索 UI や API が必要になったとき

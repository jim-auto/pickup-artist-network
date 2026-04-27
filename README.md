# pickup-artist-network

`sokusuu-ranking` の seed / scraper / generate_html / docs / JSON / CSV という流れを参考にしつつ、人物・コミュニティ・媒体・場所・コンテンツの関係性を手動整理して可視化するためのネットワーク分析ツールです。

## 何を可視化するか

- 誰が誰に影響を受けたか、与えたか
- 誰がどのコミュニティや媒体と関係するか
- 誰がどの場所・フィールドで活動するか
- 誰がどのコンテンツや商品に関与するか
- 誰が誰を批判・参照しているか

## 収集対象

初期段階では**手動入力を優先**します。対象は次の公開情報を想定します。

- X のプロフィール
- 固定ポスト
- プロフィール内リンク
- 公開されている自己紹介文
- note / Brain / Tips / YouTube などの説明文
- 手動で整理した関係メモ

自動取得の発想自体は `sokusuu-ranking` を参考にしますが、このリポジトリでは最初から完全自動収集に寄せません。まずは `data/nodes.json` と `data/edges.json` を手で育てられる構造を主軸にしています。

## 現在の採用方針

- **Approach A**: JSON を正本として運用
- **Approach B**: NetworkX に投影して分析指標を試す
- **Approach C**: SQLite に投影してクエリ性を試す

比較結果は `docs/experiments.md`、採用判断は `docs/decisions.md` に記録しています。

## 現在の数値目標

最初の明確な到達目標は **real person 20人** です。  
相関図として塊が見え始めつつ、まだ review queue を手動運用できる規模を狙います。

| type | target |
| --- | --- |
| person | 20 |
| community | 8-12 |
| content | 12-18 |
| location | 6-10 |
| platform | 6-8 |

補助的な段階目標:

1. Phase 1: real person 10人
2. Phase 2: real person 20人
3. Phase 3: real person 30人

## 使い方

### 1. 依存のインストール

```bash
pip install -r requirements.txt
```

### 2. seed と source snapshot を用意する

- `seed_entities.txt`
  - 起点となる人物・コミュニティ・媒体・場所・コンテンツ
  - `type|id|name|aliases|scope` の形式で、`scope` に `real` / `fictional` を持てる
  - 現在の公開用 seed は **real-only** で運用
- `data/source_snapshots.json`
  - `sokusuu-ranking` の profile / pinned / links 発想を流用したスナップショット入力
  - 完全自動スクレイピングの代わりに、まずは公開情報をここへ手動転記・整理する
- `data/collector_sources.json`
  - 軽量 collector が巡回する public page 一覧
- `data/x_profile_sources.json`
  - X profile collector が巡回する X プロフィール一覧
  - `pinned_post_url` を optional で持てる。X profile の logged-out HTML だけでは pinned status を特定できない場合があるため、必要なら status URL を明示して generated pinned hint を付ける
- `data/source_snapshots.generated.json`
  - collector が public page から生成する snapshot 出力
- `data/source_snapshots.generated.hints.json`
  - review workflow 実験用の optional hint fixture
  - 公開用データでは現在空にしておき、runtime に架空ノードを混ぜない

### 3. collector で public page 由来 snapshot を更新する

```bash
python collector.py
```

collector は canonical graph を直接更新せず、`data/source_snapshots.generated.json` だけを更新します。  
また、public page 側の auto-generated link は **X only** に絞り、same-platform link を落としたうえで高信号リンクだけを残します。

同時に、`data/x_profile_sources.json` に定義した X profile を巡回し、**bio 相当の summary / profile_text / X link** を generated snapshot に追加します。logged-out HTML に埋め込まれた user data が取れる場合はそれを優先し、薄いページでも bare URL ではなく handle ベースの summary に倒します。

`data/x_profile_sources.json` の entry に `pinned_post_url` を足した場合は、その status page も fetch して **`pinned_post_url` / `pinned_post_text` の generated hint** を埋めます。これは canonical graph ではなく generated snapshot にだけ入り、manual snapshot が引き続き優先されます。

### 4. グラフを生成 / 整形する

```bash
python scraper.py
```

入力だけ先に検証したい場合:

```bash
python scraper.py --validate-only
```

relation を CLI で確認したい場合:

```bash
python scraper.py --query "shibuya"
python scraper.py --query-node-id shibuya --query-direction incoming
python scraper.py --query "Alpha" --query-edge-type affiliation --query-json
```

review candidate を manual observation に昇格したい場合:

```bash
python scraper.py --approve-candidate "<candidate-id>"
python scraper.py --approve-candidate "<candidate-id>" --approval-note "reviewed manually"
python scraper.py --dismiss-candidate "<candidate-id>" --dismiss-note "not useful"
python scraper.py --list-review-candidates
python scraper.py --list-candidate-decisions
python scraper.py --list-review-candidates --review-json
python scraper.py --growth-progress
```

`candidate-id` は `data/review_candidates.json` または HTML の review candidate queue を見て使います。承認すると `data/source_snapshots.json` に observation を追記し、canonical graph / review candidate / `docs/index.html` まで再生成します。dismiss すると `data/review_candidate_decisions.json` に記録され、同じ candidate は再生成時に queue から除外されます。`--list-review-candidates` と `--list-candidate-decisions` を使うと、同じ triage 情報を terminal からも確認できます。`--growth-progress` は real/fictitious scope をもとに 20人目標への現在地を表示します。HTML には active queue に加えて **approved / dismissed の candidate decision log** も表示されます。

出力:

- `data/nodes.json`
- `data/edges.json`
- `data/nodes.csv`
- `data/edges.csv`
- `data/networkx_metrics.json`
- `data/graph.db`
- `data/review_candidates.json`
- `data/review_candidate_decisions.json`

### 5. GitHub Pages 用 HTML の生成

```bash
python generate_html.py
```

出力:

- `docs/index.html`

### 6. まとめて build する

```bash
python build_site.py
```

これは **collector -> validation -> graph generation -> review candidate export -> docs/index.html** をまとめて実行し、最後に real person target の現在値も表示します。

## 収集フロー

現時点のフローは次の通りです。

1. `seed_entities.txt` で起点を置く
2. `collector.py` が approved public page から `data/source_snapshots.generated.json` を書く
3. `data/source_snapshots.json` に手動 snapshot / observation を保存する
4. `scraper.py` が manual + generated snapshot を解釈して canonical な `data/nodes.json`, `data/edges.json` を出す
5. generated snapshot から review-only な `data/review_candidates.json` を作る
6. `generate_html.py` が `docs/index.html` を作る

この形にしておくことで、将来 `sokusuu-ranking` 風の取得スクリプトを作る場合も、まず snapshot を吐くところから始められます。

## 現在の seed 方針

現段階では安全性を優先し、次のように分けています。

- **実在 public node**
  - X / note / YouTube / Instagram / Brain / Tips / Shibuya / Shinjuku
  - Shibuya City Tourism Association / Shinjuku Convention & Visitors Bureau などの safe public community
  - GO TOKYO Shibuya Guide / GO TOKYO Shinjuku Guide などの official guide content
- **実在 person の追加方針**
  - まずは本人が公開 X profile で **自分で「ナンパ師」「プロナンパ師」「ストリートナンパのプロ」などと名乗っている** アカウントだけを少数 seed に追加する
  - 第三者の噂・暴露・まとめではなく、本人プロフィールや本人導線の public page を優先する
  - 公開プロフィール文で確認できる範囲だけを取り込み、推測的な所属・対立・影響関係はすぐに確定しない
  - 現在は公開 X profile ベースで **13 アカウント** まで追加済み

つまり、**実在の個人や小規模コミュニティを最初から大量投入しない**方針です。まずは official site や public institution に近い safe public community を少量ずつ足し、その次に **本人が自称している public X profile** や、そこから明示的にリンクされた関連アカウントを少数ずつ追加し、人・コミュニティ系の実データは公開情報・source URL・confidence を揃えたうえで段階的に追加します。

## GitHub Pages

`docs/index.html` を GitHub Pages の公開対象にする想定です。静的 HTML だけで動作し、ノード一覧、エッジ一覧、相関図ビュー、type フィルタ、名前検索、**選択ノードの detail panel** を提供します。

加えて、**real growth targets** panel で `seed_entities.txt` に基づく real node の現在値と target を確認できます。

公開更新の最短導線は `python build_site.py` です。

さらに `.github/workflows/pages.yml` により、`main` への push で **test -> build_site.py -> GitHub Pages deploy** が走る構成にしています。

## データの注意点

- このプロジェクトは**公開情報と手動整理**を前提とします
- 関係性の記述には解釈が混じる余地があるため、`confidence` と `source_urls` を必須にします
- snapshot と canonical graph には `evidence_kind` (`fact` / `interpretation` / `mixed`) と `needs_review` を持たせ、見直し対象を分かるようにします
- 同一 account に manual と generated の両方がある場合は **manual が優先**され、差分は `needs_review` と review note に寄せます
- HTML には **review queue** があり、`needs_review` の node / edge を visible 範囲で追えます
- HTML には generated snapshot 由来の **review candidate queue** もあり、mention match ベースの review-only relation 候補を確認できます
- review candidate は同じ `source -> target -> type` が `summary` / `profile_text` / `pinned_post_text` に重複しても、basis をまとめた 1 件として queue に出します
- review candidate は **approve / dismiss** のどちらかに流せます。approve は manual observation へ昇格、dismiss は再提案抑止に使います
- HTML には `data/review_candidate_decisions.json` 由来の **candidate decision log** もあり、approve / dismiss 済みの履歴を source / target / basis 単位で追えます
- official guide page のような content source が location を言及した場合は、review candidate を `reference` として扱います
- collector 由来の real generated text が薄い場合は、hint fixture を使った review workflow 実験はできますが、公開用データには混ぜません
- **推測と事実を分離**し、断定しすぎないことを重視します
- 名誉毀損、プライバシー侵害、嫌がらせにつながる運用は避けてください
- 現在の公開データは **実在 public node / official node / 本人公開プロフィール由来ノードのみ** です

## ディレクトリ

```text
pickup-artist-network/
├── README.md
├── PLAN.md
├── requirements.txt
├── collector.py
├── build_site.py
├── seed_entities.txt
├── scraper.py
├── generate_html.py
├── graph_model.py
├── data/
│   ├── collector_sources.json
│   ├── x_profile_sources.json
│   ├── source_snapshots.json
│   ├── source_snapshots.generated.json
│   ├── nodes.json
│   ├── edges.json
│   ├── nodes.csv
│   ├── edges.csv
│   ├── networkx_metrics.json
│   └── graph.db
└── docs/
    ├── index.html
    ├── experiments.md
    ├── decisions.md
    └── interfaces.md
```

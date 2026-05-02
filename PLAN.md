# PLAN

## Goal

`sokusuu-ranking` の雰囲気を残しつつ、ナンパ界隈の人物・コミュニティ・媒体・場所・コンテンツの関係を、**手動 first / 公開情報ベース / GitHub Pages 公開前提** で整理・可視化できるネットワーク基盤を育てる。

## Current State

- 公開データは **535 nodes / 1512 edges / 255 review candidates**
- 実在人物カウントは **508 / 1000**
- 公開 UI は **単一の account-centric graph** に整理済み
- relation semantics は `follow` / `profile_mention` / `affiliation` / `influence` などに明確化済み
- 右側 detail panel では接続ノードを type ごとに見られる
- keyword cluster は MBH / セクシーコマンドー / 味噌 / ピカ講習 / mスト部 / こりらM氏講習 / 雄華軍団 / ゴラッソ長期 / アツスト / wing長期 / えるスタ などを扱える
- canonical data の中心は引き続き  
  `seed_entities.txt` -> `data/source_snapshots.json` -> `data/nodes.json` / `data/edges.json`

## What Is Already Working

1. **JSON-first graph pipeline**
   - manual seed / snapshot から canonical node / edge を組み立てる
   - CSV / SQLite / NetworkX への投影も可能

2. **GitHub Pages publishing**
   - `python build_site.py` で publish artifact を再生成できる
   - `docs/index.html` + `docs/graph-data.json` を Pages にそのまま出せる

3. **Manual-first review workflow**
   - generated snapshot は review-only 候補として保持
   - safe な relation だけ manual observation に昇格する運用ができる

4. **Public X profile ingestion**
   - logged-out profile HTML から summary / icon / links を抽出できる
   - pinned URL の手動追加や profile mention 昇格にも対応済み

5. **Static graph UX**
   - 名前検索
   - node / edge type filter
   - keyword cluster picker
   - relation-based clustering
   - detail panel から接続ノードへジャンプ

## Major Progress So Far

### Foundation

- `sokusuu-ranking` 由来の seed / scraper / HTML export / docs 構成を移植
- Approach A を正本として採用
- Approach B (NetworkX) / C (SQLite) は薄い実験として維持
- docs / interface / experiment comparison まで整備済み

### Data Safety / Modeling

- manual snapshot が generated snapshot より常に優先される
- `reference` のような曖昧 edge をやめ、`follow` と `profile_mention` に分割
- review metadata (`needs_review`, `evidence_kind`) は内部で維持しつつ、公開 UI はかなり簡素化
- 公開版は **real-only** を基本方針に切り替え済み

### UI / Readability

- 全体グラフの複雑さを削って account-centric view に一本化
- detail panel の情報量を改善
- キーワード群の直接選択を追加
- 目障りな `人物` / `要確認` / `事実` 系の visible tag を削除

### Graph Growth

- following-guided wave を重ねて **500 実在人物** を突破
- `えるスタ` を community として正しくモデリング
- `utopua2`, `molmol-1919`, `nampa-girl`, `igaku-sato`, `kgori-0412`, `sen-xxv`, `nrtq5ihqepycy0n`, `25basabe`, `na-tu-sb` などの public-profile-backed node を追加
- `palace-chilll` や `natu-douga` のような business / content side node も、関係が自然なものだけ増やしている

## Current Problems

### 1. 1000 人まで増やす主戦略は following expansion

公開プロフィールだけでも増やせるが、増加速度はどうしても鈍い。  
1000 人規模に到達するには、**既存 seed account の following を辿って未登録 handle を大量発掘する流れ** が必要。

### 2. X authenticated following collection is currently blocked

collector 側には以下がすでにある:

- auth state を使った authenticated following 収集
- cookie file fallback
- followed handle -> tracked account edge 化

ただし今の環境では:

- `data\\.x_auth_state.json` が未作成
- `data\\.x_cookies.txt` も未作成
- headless Playwright / stealth login は X 側で `JavaScriptを使用できません` ページに落とされる
- reference repo (`influencer_tweet_collector`) 由来の `undetected_chromedriver` 方式も、driver version と X login flow の両面でそのままでは安定しない

つまり、**following 収集ロジックはあるが、認証確立が未解決** という状態。

### 3. Public-profile-only growth is still useful but slower

認証がない間も、profile text 内の side account / related account / business account を増やすことで前進はできる。  
ただし 100 人単位で一気に増やすには効率が足りない。

## Constraints

- 過剰設計しない
- 手動編集しやすさを優先する
- confidence と source_urls を必須にする
- 事実・引用・推測を混ぜない
- 公開可能性を強く意識する
- seed -> source snapshots -> canonical graph の段階を崩さない
- 公開 graph は self-described public profiles と明示 relation を基本にする
- 危ない推測や弱い alias match は採用しない

## Immediate Plan

### Track A: Recover authenticated following expansion

最優先。

1. `collector.py --login-x` で通常ブラウザの auth state を保存する
2. それで 1 account の following 取得を通す
3. 取得済み seed batch に対して following 収集を再実行する
4. unseen handle を screening し、public self-described profile だけ seed 化する
5. 必要なら explicit relation も最小限で昇格する

これが通れば、**100 人単位の拡張** が現実的になる。

### Track B: Keep slow-but-safe profile growth while auth is blocked

認証が通るまでの暫定線。

1. 既存 node description / generated snapshot / review candidate から未登録 `@handle` を抽出
2. 実際に public profile が読める handle だけ採用
3. 既存 graph に自然につながる side account / business / content account を優先
4. 1 wave あたり 3-10 ノードずつ安全に増やす

### Track C: Preserve graph readability while scaling

ノード数だけ増えても読めなくなると意味が薄いので、以下は継続監視する。

1. keyword cluster の過剰な誤吸着を防ぐ
2. 弱い substring match を review-only に留める
3. public UI に noisy status を戻さない
4. detail panel で関係を追える状態を維持する

## Near-Term Milestones

1. **Auth recovery milestone**
   - auth_state または cookie による following 収集を 1 account で成功させる

2. **Next growth milestone**
   - 550 real people 到達
   - following reuse が通れば短期で突破可能

3. **Mid growth milestone**
   - 600-700 real people 到達
   - cluster / side-account / community relation の密度も同時に上げる

4. **Final target**
   - 1000 real people
   - ただし人数だけでなく、主要 cluster の relation richness も維持する

## Concrete Next Execution Steps

1. 通常ブラウザ login 完了後に `data\\.x_auth_state.json` を保存
2. `collect_authenticated_following_handles()` を seed の代表アカウントで smoke test
3. passing したら `x_profile_sources.json` の seed 群に対して following recollect
4. unseen handles を候補一覧化
5. public self-description があるものだけ seed / snapshot に昇格
6. `python build_site.py --skip-collector` で再生成
7. 実在人数と relation 増加量を記録

## Known Blockers

- headless 自動ログインは X 側にブロックされる可能性が高い
- authenticated following expansion は auth_state/cookie ができない限り本格始動できない
- logged-out following page は現在の collector path ではほぼ使えない

## Decision For Now

このプロジェクトの次のブレイクスルーは、  
**新しい抽象化でも UI 改修でもなく、X 認証を通した following 収集の再開** である。

認証が通れば、現在のデータモデル・review workflow・Pages UI はそのまま活かして、  
1000 人目標へかなり強く前進できる。

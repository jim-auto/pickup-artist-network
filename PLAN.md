# PLAN

## Goal

`sokusuu-ranking` の雰囲気を残しながら、関係性の整理と GitHub Pages 可視化に向いた手動-first のネットワーク分析基盤を作る。

## Phases

1. 完了: JSON を正本にした最小グラフ実装を作る
2. 完了: Fictional sample data で JSON / CSV / HTML を成立させる
3. 完了: 同じ JSON から NetworkX と SQLite の試作を生やす
4. 完了: 比較結果を踏まえて採用案を固定する
5. 完了: profile / pinned / links の source snapshot 層を追加する
6. 完了: 少量の公開情報ベース seed を safe な platform / location 中心で入れる
7. 完了: snapshot schema の検証と `--validate-only` を足して入力事故を減らす
8. 完了: HTML に detail panel を足して source/evidence を追いやすくする
9. 完了: 公開情報ページから source snapshot を書き出す軽量 collector を足す
10. 完了: `evidence_kind` / `needs_review` を snapshot と canonical graph に通す
11. 完了: `python build_site.py` で publish flow をまとめる
12. 完了: collector の link 抽出を絞って、generated edge のノイズをさらに減らす
13. 完了: HTML に review queue を足して `needs_review` を追いやすくする
14. 完了: GitHub Actions で Pages build/deploy を自動化する
15. 完了: X profile collector を追加し、generated snapshot layer に統合する
16. 完了: manual > generated の優先ルールを入れ、差分は review note に逃がす
17. 完了: generated snapshot の summary / profile_text を圧縮して node description のノイズを減らす
18. 完了: X の logged-out HTML に埋め込まれた user data を優先し、summary を bare URL fallback から脱却させる
19. 完了: relation を CLI から query できる入口を足し、manual curation 中に HTML を開かず近傍確認できるようにする
20. 完了: fictional sample cluster を追加して、seed_entities と source_snapshots から graph を太らせる実例を増やす
21. 完了: X profile source に optional な pinned_post_url を持たせ、generated snapshot に pinned_post_text の軽量ヒントを載せられるようにする
22. 完了: generated snapshot の text から review-only relation candidate を作り、`data/review_candidates.json` と HTML queue に出す
23. 完了: `python scraper.py --approve-candidate <candidate-id>` で review candidate を manual observation に昇格させ、graph と HTML を再生成できるようにする
24. 完了: review candidate の type heuristic を少し強くし、`data/review_candidate_decisions.json` で dismiss を永続化する
25. 完了: HTML に candidate decision log を足し、approve / dismiss の triage 履歴を source / target / basis ごとに追えるようにする
26. 完了: terminal からも review candidate queue と decision log を確認できる CLI 一覧表示を足す
27. 完了: safe public community source を追加し、real build でも Shibuya / Shinjuku 向けの review candidate が自然に出る状態を作る
28. 完了: review candidate queue を source/target/type 単位で統合し、real candidate を 2 件 manual observation に昇格させる
29. 完了: official guide content source を追加し、content -> location の real `reference` relation も canonical graph に流せるようにする
30. 完了: `seed_entities.txt` に real / fictional scope を持たせ、HTML に real growth target panel を追加する
31. 完了: terminal / build log からも real growth target を確認できる CLI と build 出力を足す
32. 次段階: real person 500人マイルストーン / 最終1000人目標に向けて safe source cluster を増やし、person node を段階的に投入する

## Constraints

- 過剰設計しない
- 手動編集しやすさを優先する
- confidence と source_urls を必須にする
- 事実・引用・推測を混ぜない
- 公開可能性を強く意識する
- seed -> source snapshots -> canonical graph という段階を崩さない

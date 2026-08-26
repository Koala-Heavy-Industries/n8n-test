# <組織名>-ledger — メンバー・機器台帳

開発プロジェクトの**権限台帳**。n8n による IT オペレーション自動化
([設計ドキュメント](https://github.com/Koala-Heavy-Industries/n8n-test))が
読み書きするデータの実体で、ワークフローやコードは含まない。

**このリポジトリへの PR のマージが、権限付与・剥奪の承認そのもの**になる。

> ⚠️ 本番では個人情報(氏名・メールアドレス・所属会社・契約期限)を含む。
> 閲覧範囲は限定し、Org 全体には公開しない。

## 構成

```
members/<id>.yaml     あるべき状態(desired)。人が PR で編集する
catalog/              サービスカタログ・役割/チームマトリクス・PC必須ソフト
state/                実態(actual)。bot だけが書き込む。人は編集しない
├── members/<id>.yaml   付与実態と未完了タスク
├── pcs/<資産管理番号>.yaml
└── servers/<資産管理番号>.yaml
```

- `<id>` はメールアドレスのローカル部を正規化した slug。**不変**に扱い、
  メールアドレスが変わっても `email:` フィールドだけを書き換える。
- `state/` は bot(リコンサイラ)の管理領域。手で編集しない(CI が拒否する。
  また bot の書き込みと衝突して PR がマージできなくなる)。

## 変更のしかた

| やりたいこと | 方法 |
|---|---|
| メンバーを追加 | `members/<id>.yaml` を新規作成する PR |
| 役割変更・チーム異動 | `role:` / `teams:` を書き換える PR |
| メンバーを削除 | `status: left` と `left_at:` にする PR(ファイルは消さない) |
| 契約を延長(協力会社) | `contract_until:` を更新する PR |
| サービス・役割・チームの定義変更 | `catalog/` の PR(**全メンバーに影響しうる**) |

n8n のフォームから申請すると、上記の PR が自動作成される。
エンジニアは直接 PR を書いてもよい(リコンサイラは変更の出どころを問わない)。

マージ後、リコンサイラが `members/` と `state/` の差分を計算し、
Keycloak・GitHub などへ付与/剥奪を適用して `state/` に記録する。
自動化できない操作(ライセンス購入など)はタスクとして起票され、
完了までリマインドされる。

## members/<id>.yaml の書き方

```yaml
name: 山田 太郎
email: taro@example.com
affiliation: employee          # employee | partner:<会社名>
role: developer                # catalog/role-matrix.yaml に存在する役割
teams: [alpha]                 # catalog/team-matrix.yaml のキー。兼務は複数
status: active                 # active | left
accounts:
  github: taro-yamada          # サービス固有 ID
sponsor: pm@example.com        # partner のみ必須(受入責任者)
contract_until: 2027-03-31     # partner のみ。期限前に更新確認が届く
# effective_from: 2026-09-01   # 役割変更の適用日(任意)
# left_at: 2026-09-30          # 削除の発効日(status: left とセット。当日いっぱい有効)
# immediate: true              # 即時遮断(削除時のみ)
# extra_grants:                # 個人単位の例外(原則禁止。期限必須)
#   - { service: github, grant: "team:secops", review_until: 2026-12-31 }
```

## PR に対する自動チェック

PR を出すと CI([`.github/workflows/validate.yml`](.github/workflows/validate.yml))が次を行う。

| チェック | 内容 |
|---|---|
| スキーマ・制約の検証 | 役割名・チーム名の存在、affiliation の形式、協力会社の `sponsor` / `contract_until` 必須、`admin` は従業員のみ、`extra_grants` の `review_until` 必須、email の重複、役割×チームの未定義組合せ、カタログ側の整合 |
| `state/` の保護 | `state/` を変更する PR を拒否する(自動化の管理領域のため) |
| 権限差分のプレビュー | 「付与 / 剥奪」の表を PR にコメントする。カタログ変更時は影響人数を強調し、剥奪が多い場合は警告する |

役割名のタイポのように、これまで黙って「権限が付かない」で終わっていた失敗を
マージ前に止められる。レビュアーは YAML の1行差分ではなく**権限への影響**を見て承認できる。

ローカルで同じ検証を走らせる場合:

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12 sh -c \
  "pip install -q pyyaml && git config --global --add safe.directory /w && python ci/ledger.py validate"
```

## 現状

この台帳は雛形から作成された。以下を自組織に合わせて調整すること:

- `catalog/role-matrix.yaml` / `team-matrix.yaml` — 実際の役割・チームに置き換える
- `catalog/services.yaml` — 対象サービスと担当者のプレースホルダ
- `catalog/pc-software.yaml` — 必須ソフト
- ブランチ保護(レビュー必須・自己承認禁止・force push 禁止)と
  Rulesets による bot の書き込みパス制限(`state/` のみ)
- ブランチ保護と bot の書き込みパス制限 — **検証環境では設定できない**(下記)

### 検証環境の制約: ブランチ保護が使えない

このリポジトリは個人アカウント(GitHub Free)のプライベートリポジトリのため、
ブランチ保護・Rulesets が利用できない(API は 403 を返す)。

**検証への影響は限定的**で、PR の作成・マージ・マージ Webhook は通常どおり
動作するため、リコンサイラの検証(PR マージ → 差分計算 → 付与 → `state/` 記録)は
そのまま行える。設定できないのは**強制**の部分だけ:

| 設計上の要求 | 検証環境 | 本番(GHEC) |
|---|---|---|
| 直接 push の禁止(PR 必須) | 規約で運用 | Rulesets で強制 |
| 申請者≠承認者・自己承認禁止 | 検証不可(単独アカウントのため実質不可) | Rulesets で強制 |
| force push 禁止 | 規約で運用 | Rulesets で強制 |
| bot の書き込みを `state/` に限定 | 規約で運用 | Rulesets のパス制限で強制 |

本番の台帳は GHEC の Org 配下に置くため、これらはすべて利用できる。
**本番移行時の必須チェック項目**として扱う。

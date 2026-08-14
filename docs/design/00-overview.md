# 00. 全体像 — スコープと役割分担

## 背景と目的

開発プロジェクト(n8n中心)の運用のうち、次の2領域を n8n で自動化し、対応漏れをなくす。

1. **メンバーライフサイクル管理** — メンバーの追加・役割変更・削除。
   SAML/SSO ではカバーできない個別サービスの操作を n8n が担う。
2. **開発PC購入時のタスク管理** — マシン名の決定、NetBox への登録、
   必須ソフトのライセンス購入リマインドを申請起点で促し、漏れを防ぐ。

### 前提: IdP の位置づけ

- このプロジェクトの IdP(**Keycloak**)は**プロジェクト自身が保有・管理**し、
  従業員と協力会社メンバーの両方が登録される。
- HR・社内情シスが管理する全社 IdP は**別系統**で、協力会社メンバーは登録されない。
- したがって本自動化では、**プロジェクト IdP のユーザーライフサイクル自体を
  管理対象に含める**(サービスカタログの1行として `execute: api` で扱う)。
  協力会社メンバーにとっては、このフローが事実上の入退場処理となる。
- 従業員の退職は全社 IdP・HR 側で先に起こり、プロジェクト IdP には自動伝播しない。
  この検知の手当てを削除フローと監査に組み込む(→ [01](01-member-lifecycle.md))。

## SAML/SSO と n8n の役割分担

| 操作 | 担当 | 備考 |
|---|---|---|
| 認証・シングルサインオン | SAML/SSO(IdP) | SSO対応SaaSへのログイン |
| SSO対応SaaSのアカウント作成 | JIT プロビジョニング(初回 SSO ログイン時) | Keycloak は SCIM を標準では持たないため。停止は Keycloak アカウント停止で実質無効化される |
| プロジェクト IdP のユーザー作成・変更・停止 | **n8n(API)** | IdP はプロジェクト保有のため、ユーザーライフサイクル自体を本フローが管理(上記前提) |
| GitHub Organization への招待・削除、Team 所属 | **n8n(API)** | SSOログインはできても Org/Team 所属は別管理 |
| 有償ライセンスのシート購入・解約(JetBrains 等) | **n8n(手動タスク+リマインド)** | 購入・契約はAPI化が困難。漏れ防止が主目的 |
| Claude アカウントの追加・削除 | **n8n(当面は手動タスク)** | Anthropic Admin API 導入で自動化へ昇格可能 |
| NetBox へのデバイス登録・アカウント管理 | **n8n(API)** | PC購入フローの中核 |
| PC購入時のチェックリスト(命名・登録・ライセンス) | **n8n** | SAMLとは無関係の運用タスク |

IdP は **Keycloak** に確定。コネクタは Keycloak Admin REST API を使う
(ユーザー作成・グループ所属・停止。→ [05](05-environment.md))。
Keycloak は SCIM を標準では持たないため、SSO 対応 SaaS 側のアカウント作成は
初回 SSO ログイン時の JIT プロビジョニングを基本とし、SCIM が必要になったら
拡張の導入を検討する。IdP 経由のカバー範囲が広がったら、該当サービスを
[サービスカタログ](03-service-catalog.md)で `execute: idp` に変えるだけでよい。

## 基本方針(5原則)

1. **自動化できる部分は自動実行、できない部分はタスク化+リマインドで漏れを防ぐ**
   サービスとの連携形態は、操作(追加・変更・削除)ごとに
   **実行方式 × 確認方式 × 枠** の3軸で分類する。SCIM/SAMLで完結、APIで自動実行、
   枠の手動調達+自動割当、管理者への依頼+APIでの事後把握……は、
   すべてこの3軸の組み合わせとして表せる。実行が人手でも確認(事後検証)だけを
   自動化して漏れ検出を効かせる、といった分担がとれるのがポイント。
   → [03. サービスカタログ「分類の3軸」](03-service-catalog.md#分類の3軸)
2. **対象サービスはコードではなくデータで管理**
   サービスの増減はカタログ(データ)の行の追加・無効化で対応し、
   入口ワークフローの改修を不要にする。→ [03. サービスカタログ](03-service-catalog.md)
3. **通知・承認チャネルは抽象化**
   `notify` / `request-approval` を汎用サブワークフローとして切り出し、
   Gmail / Slack 等の実装は後から差し替え可能にする。→ [04. 通知・承認の抽象化](04-notification-abstraction.md)
4. **Git 台帳による宣言的な状態管理とバックストップ監査**
   台帳は Git リポジトリで持つ。あるべき状態(`members/`)を人が PR で宣言し、
   実態(`state/`)をリコンサイラが収束・記録する。desired ≠ state の差分が
   そのまま「やり残し」として可視化され、定期監査が未収束・期限超過・
   実サービスとの不整合を検出して通知する。フロー単体の成功に頼らず、
   二重に漏れを防ぐ。→ [03. 台帳リポジトリ](03-service-catalog.md#台帳リポジトリgit)
5. **承認を挟む(申請者と承認者の分離)**
   権限付与・剥奪・削除は必ず承認ステップを通す。メンバー系の承認は
   台帳 PR のレビュー+マージとして実装する(→ [01](01-member-lifecycle.md))。

## ワークフロー全体マップ

```mermaid
flowchart LR
    subgraph entry["入口(申請)"]
        A1[メンバー申請フォーム3種 台帳PRを自動作成]
        A2[直接PR エンジニア・緊急対応]
        A4[PC購入登録フォーム]
    end

    subgraph ledger["台帳(Git リポジトリ)"]
        D1[(members/ あるべき状態)]
        D3[(catalog/ カタログ類)]
        D2[(state/ 実態と未完了タスク)]
    end

    subgraph core["中核・共通サブワークフロー"]
        B0[reconcile リコンサイラ]
        B2[notify 通知]
        B3[ledger-read / ledger-write]
        B4[service-* ハンドラ群]
    end

    subgraph backstop["バックストップ(Schedule Trigger)"]
        C1[remind-scheduler リマインド送信]
        C2[weekly-audit 週次監査]
        C3[consistency-audit 整合性監査]
    end

    A1 --> D1
    A2 --> D1
    A4 --> B4
    D1 -->|レビュー・マージ = 承認| B0
    B0 --> B4
    B0 --> D2
    B4 --> EXT[Keycloak / GitHub / NetBox などの外部API]
    backstop --> B3
    backstop --> B2
```

## ワークフロー一覧

| ワークフロー | 種別 | トリガー | 概要 | 詳細 |
|---|---|---|---|---|
| member-request(追加・変更・削除フォーム) | 入口 | Form | 申請内容から台帳リポジトリへの PR を自動作成 | [01](01-member-lifecycle.md) |
| reconcile | 中核 | Webhook(マージ)+日次 | 台帳の desired と state の差分を計算し、付与・剥奪を収束させる | [01](01-member-lifecycle.md) |
| pc-purchase | 入口 | Form | PC購入登録。命名・NetBox登録・ライセンスタスク | [02](02-pc-purchase.md) |
| request-approval | サブ | Execute Workflow | 承認依頼(メンバー系= PR レビュー実装、汎用=再開リンク実装) | [04](04-notification-abstraction.md) |
| notify | サブ | Execute Workflow | チャネル非依存の通知 | [04](04-notification-abstraction.md) |
| ledger-read / ledger-write | サブ | Execute Workflow | 台帳アクセスの一元化 | [03](03-service-catalog.md) |
| service-keycloak / service-github ほか | サブ | Execute Workflow | 自動実行系ハンドラ(コネクタ / PR生成 / 準備物生成) | [03](03-service-catalog.md) |
| remind-scheduler | 定期 | Schedule(毎日) | 未完了タスクへのリマインド・エスカレーション | [04](04-notification-abstraction.md) |
| weekly-audit | 定期 | Schedule(週次) | 未完了・期限超過の集計レポート | [01](01-member-lifecycle.md) / [02](02-pc-purchase.md) |
| consistency-audit | 定期 | Schedule(週次) | 実サービス(GitHub等)と台帳の突合 | [01](01-member-lifecycle.md) |

## ドキュメント構成

- [01. メンバーライフサイクル](01-member-lifecycle.md)
- [02. 開発PC購入フロー](02-pc-purchase.md)
- [03. サービスカタログと台帳](03-service-catalog.md)
- [04. 通知・承認の抽象化](04-notification-abstraction.md)
- [05. 実行環境設計](05-environment.md)

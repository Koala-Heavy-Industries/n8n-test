# n8n ITオペレーション自動化

開発プロジェクトの運用タスクを n8n で自動化し、SAML/SSO でカバーできない
対応の漏れをなくすプロジェクト。対象は次の3領域。

1. **メンバーライフサイクル管理** — 追加・役割変更・削除
   (GitHub、有償ライセンス、Claude アカウントなど。対象サービスは増減する前提)
2. **開発PC登録時のタスク管理** — 新規購入も他部署からの搬入も同じフローで登録。
   コンピュータ名・資産管理番号(手入力、仮→正式)・NetBox 登録・
   必須ソフトのライセンス購入リマインド・IP アドレスの回収
3. **サーバ・VM の一覧と IP の管理** — 物理サーバ(直接 OS / ハイパーバイザー)
   と VM を NetBox で一元管理し、登録の入口と実態との突合を n8n が担う

## ステータス

**骨格が動作。**
設計(00〜08)に加え、**台帳の PR をマージすると Keycloak に反映される**
一連のループが実機で動く状態。

| ロードマップ | 状態 |
|---|---|
| 1. 検証環境(n8n / Keycloak / Mailpit) | ✅ 完了 |
| 2. 台帳リポジトリ([khi-ledger](https://github.com/Koala-Heavy-Industries/khi-ledger)) | ✅ 完了 — 構成・カタログ・CI(スキーマ検証 / state 保護 / 権限差分プレビュー)|
| 3. 土台サブWF | ✅ 完了 — `ledger-read` / `ledger-write` / `notify` / `request-approval` すべて実機で動作確認 |
| 4. リコンサイラ + service-keycloak | ✅ 完了 — 台帳の変更が Keycloak に反映されるところまで実機で確認 |
| 5. remind-scheduler・タスク起票・完了リンク | ✅ 完了 — 起票→リマインド→完了→収束、エスカレーションまで確認 |
| 6. PC 登録(NetBox) | 🟡 環境・`pc-register` / `netbox-assign-ip` 実装済み。**動作未検証**(Docker のメモリ不足で NetBox と n8n を同時に動かせず中断) |
| 7〜11(サーバ/VM、service-github、監査、安全装置ほか) | ⬜ 未着手 |

→ [実装ロードマップ](docs/design/05-environment.md#実装フェーズのロードマップ)

## 検証環境の起動

> **Docker に 4 GiB 以上のメモリを割り当ててください。** 2 GiB 程度だと
> n8n が繰り返し停止します(→ [05](docs/design/05-environment.md#必要なリソース))。

```bash
cp .env.example .env       # 秘密値を埋める(openssl rand -hex 32 など)
docker compose up -d
./scripts/setup-keycloak.py    # realm・グループ・n8n用サービスアカウント(冪等)
./scripts/setup-netbox.py      # APIトークン・サイト・ロール・カスタムフィールド(冪等)
```

| サービス | URL | 用途 |
|---|---|---|
| n8n | http://localhost:5678 | ワークフロー |
| Keycloak | http://localhost:8080 | プロジェクト IdP(realm: `khi-dev`) |
| Mailpit | http://localhost:8025 | 送信メールの受信箱(チャネル未決のまま検証する) |
| NetBox | http://localhost:8000 | 実機情報(コンピュータ名・資産管理番号・IP)の Source of Truth |

`setup-keycloak.py` が出力する Client Secret と、khi-ledger 用の
fine-grained PAT(Contents: Read and write のみ)を `.env` に入れてから:

```bash
./scripts/import-credentials.py   # .env から n8n の Credential を作成(冪等)
./scripts/deploy-workflows.py     # workflows/*.json をインポート・有効化・再起動
```

## ワークフローの開発

ワークフローは UI ではなく `workflows/*.json` として Git 管理し、CLI で
インポートする(定義が Git に残り、[08 のワークフロー定義監査](docs/design/08-safeguards.md)にも接続できる)。

```bash
./scripts/deploy-workflows.py                              # 反映
./scripts/run-workflow.py ledger-read '{"kind":"members"}' # 入力を与えて実行
./scripts/run-workflow.py reconcile '{}'                   # 台帳と実サービスを収束させる
```

JSON には top-level の `id` を必ず付ける(無いと再インポートで重複する)。

## 運用ハンドブック

仕組みの説明と、役割ごとの操作手順(申請する人 / タスク担当者 / 受入責任者 / 運用者):
**[docs/handbook.html](docs/handbook.html)**

設計の判断とその理由は下の設計ドキュメントに、実際の使い方はハンドブックにあります。

## 設計ドキュメント(読み順)

| ドキュメント | 内容 |
|---|---|
| [00. 全体像](docs/design/00-overview.md) | スコープ、SAML/SSO との役割分担、基本方針、ワークフロー全体マップ |
| [01. メンバーライフサイクル](docs/design/01-member-lifecycle.md) | 追加・役割変更・削除のフロー、失敗時の扱い、監査 |
| [02. 開発PC登録フロー](docs/design/02-pc-register.md) | 新規購入・搬入の登録、仮情報の正式化、NetBox 連携、ライセンス購入タスク |
| [03. サービスカタログと台帳](docs/design/03-service-catalog.md) | サービス増減に耐えるデータ設計、役割マトリクス、台帳スキーマ |
| [04. 通知・承認の抽象化](docs/design/04-notification-abstraction.md) | チャネル後決めの仕組み、承認リンク方式、リマインドループ |
| [05. 実行環境設計](docs/design/05-environment.md) | docker compose 構成、認証情報、実装ロードマップ |
| [06. GitHub(GHEC)連携設計](docs/design/06-github-teams.md) | 非EMU前提、ネストTeam構造、命名規則、権限レベルとブランチ保護・CODEOWNERSの境界 |
| [07. サーバ・VM 管理設計](docs/design/07-servers.md) | NetBoxでのモデリング(device・クラスタ・VM・IPAM)、登録フォーム、ハイパーバイザー実態との突合 |
| [08. 統制と安全装置](docs/design/08-safeguards.md) | fail-open/closed方針、脅威モデル(n8n自体の特権)、リコンサイラの安全装置、break-glass、定期再認定、個人情報、監視の監視 |

## 設計上の重要な決定(サマリ)

- **連携形態は操作ごとに「実行方式 × 確認方式 × 枠」の3軸で分類**する。SCIM完結・API自動・枠の手動調達+自動割当・依頼+事後把握などはすべて軸の組み合わせで表現でき、実行が手動でも確認だけを自動化する分担がとれる。自動化できない部分はタスク化+リマインドで漏れを防ぐ。
- **台帳は Git リポジトリ(宣言的リコンサイル)** — あるべき状態を PR で宣言し(承認= PR レビュー+マージ)、リコンサイラが実サービスとの差分を収束させる。追加・役割変更・削除は同じループの3つの現れで、`git log` がそのまま監査証跡になる。
- **IdP はプロジェクト保有の Keycloak** — 従業員と協力会社メンバーが登録され、ユーザーライフサイクル自体を管理対象に含む。アカウント停止(+セッション失効)が削除時のキルスイッチ。協力会社メンバーは契約期限起点の時限付与。
- **対象サービスはデータ(カタログ)で管理** — 増減はカタログ行の追加・無効化のみで、ワークフローの改修は不要。
- **通知・承認チャネルは抽象化** — Gmail / Slack は後決め。承認・完了報告は Wait ノードの再開リンク方式でチャネル非依存。
- **NetBox が実機情報(コンピュータ名・資産管理番号・IP アドレス)の Source of Truth** — 情シスの正式採番が未定でも仮の値で登録を先行し、正式化と IP 未入力をタスクとして追跡する。
- **台帳+定期監査のバックストップ** — フロー単体の成功に頼らず、未完了・期限超過・実サービスとの不整合を週次(高リスクは日次)で必ず浮上させ、半期の再認定で全メンバーの権限を洗い直す。
- **失敗の倒れる方向を明示し、自動化の特権を自覚する** — どの失敗が fail-open(アクセスが残る)かを一覧化し、受容したリスクは監査で補う。**n8n 自体が承認プロセスを迂回できる特権点**であることを前提に、ワークフロー定義の監査・bot の書き込み範囲の強制・リコンサイラのサーキットブレーカー・外部からの死活監視を置く。

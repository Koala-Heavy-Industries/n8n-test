# 03. サービスカタログと台帳

対象サービスが増減しても入口ワークフローを改修せずに済ませるための中核設計。
**サービスをコードではなくデータとして持ち、サービスごとの連携形態の違いは
「分類の3軸」の値の違いとして表現する。**

## 分類の3軸

サービスとの連携形態(SCIM/SAMLで完結、APIで自動実行、枠の手動調達+自動割当、
管理者への依頼+事後把握、IaC経由、本人セルフサービス……)を「パターン」として
列挙・追加していくのではなく、**操作(add / change / remove)ごと**に次の3軸の
組み合わせで表す。新しい連携形態は原則「新しい組み合わせ」にすぎず、
カタログの構造も入口ワークフローも変わらない。

### 軸1: 実行方式(execute)

| 値 | 意味 | n8n の動き |
|---|---|---|
| `idp` | SCIM/SAML で IdP 側が完結 | 実行なし(台帳への記録のみ) |
| `api` | API で直接実行できる | コネクタサブWFを実行 |
| `iac-pr` | Terraform 等のコード管理下 | 設定リポジトリへの PR を自動作成。マージが承認と実行を兼ね、適用結果は事後検証で確認 |
| `prepared` | 実行は人だが準備は自動化できる | 貼り付け用 CSV・招待文面・対象画面への直リンク等を生成し、タスクに添付して起票 |
| `manual` | 完全に人手 | タスク起票のみ |

本人が操作するセルフサービス型(招待リンクからの自己サインアップ等)は、
タスクの担当(assignee)を管理者ではなく**本人**にした `prepared` / `manual` として表す。

### 軸2: 確認方式(verify)

実行方式とは**独立に**選ぶ。書き込み API がなくても読み取り API がある
サービスは多く、確認だけでも自動化する価値が大きい
(タスクの自動クローズ、リマインド精度の向上、フロー外変更の検出)。

| 値 | 意味 | タスクのクローズ条件 |
|---|---|---|
| `api` | 読み取り API のポーリングで反映を確認 | remind-scheduler の事後検証で反映を検知したら**自動クローズ**(人の完了報告は不要) |
| `event` | Webhook・監査ログ・通知メールのパースで検知 | イベント受信で自動クローズ |
| `human` | 人の完了報告 | 完了リンク([04](04-notification-abstraction.md#チャネル非依存の承認完了報告方式))のクリック |
| `none` | 確認手段なし | 完了リンク+定期棚卸し([01](01-member-lifecycle.md#バックストップ監査))で補完 |

`execute: manual` + `verify: api` の組み合わせが「管理者に依頼を通知し、
管理者が対応したことを API で事後的に把握する」パターンにあたる。
この組み合わせでは、リマインドが「依頼済みだが未反映」という事実に基づいて
送られるため、済んだ作業への催促が起きない。

### 軸3: 枠(capacity)

シート数など上限枠のあるサービスは、**「枠の調達」と「枠への割当」を別の操作として
分離**する。追加・削除フローが扱うのは割当・解放で、調達は独立したタスク
(それ自体が実行方式を持つ。多くは `manual` / `prepared`)。

capacity ブロックの内容:

| 項目 | 説明 |
|---|---|
| tracked | 枠を管理するか |
| total_source | 総枠数の把握方法(`api` / `ledger`=台帳で手動管理) |
| used_source | 使用数の把握方法(`api` / `ledger`) |
| threshold | 空き枠がこの数を下回ったら調達タスクを自動起票する(購入リードタイムでオンボーディングを詰まらせない先回り補充) |
| procurement | 調達タスクのテンプレート(担当・手順・期限日数) |

- 割当時に空き枠がない場合は調達タスクを起票し、割当を「予約」として
  タスク台帳に残して、充足後に再開する。
- 空き枠の監視(threshold 判定)は remind-scheduler の日次実行に含める
  ([04](04-notification-abstraction.md#リマインドループ共通型))。
- 削除時は割当解放により空き枠が戻ったことを verify 方式で確認する。

補足: 期限付き付与でサービス側が自動失効する形態(JIT アクセス等)は、
remove を `{execute: idp または api, verify: api}` とみなし、
削除フローでは失効確認のみを行う対象にする。

## サービスカタログ

カタログの1行 = 1サービス。メンバーライフサイクルの各フローは、
実行時にカタログの有効行を読み込んで対象サービスを列挙する。

| 列 | 型 | 説明 |
|---|---|---|
| service_id | 文字列 | 一意なID(例: `github`) |
| name | 文字列 | 表示名 |
| operations | JSON | 操作(add / change / remove)ごとの3軸設定(下記) |
| capacity | JSON | 枠ブロック(枠なしのサービスは省略) |
| enabled | 真偽 | 無効化フラグ(物理削除はしない) |

operations の形:

```jsonc
{
  "add":    { "execute": "api",    "verify": "api",  "connector": "service-github" },
  "change": { "execute": "manual", "verify": "none",
              "task_template": { "assignee": "...", "steps": "...", "due_days": 7 } },
  "remove": { "execute": "api",    "verify": "api",  "connector": "service-github" }
}
```

- **同一サービスでも操作ごとに方式は違いうる**(招待は API だがロール変更は
  管理画面のみ、など)ため、3軸はサービス単位でなく操作単位で持つ。
- execute が `api` / `iac-pr` の操作はハンドラサブWF名(`connector`)を、
  `prepared` / `manual` はタスクテンプレート(`task_template`)を持つ。
  `prepared` は準備物を生成するサブWF名(`preparer`)も併記する。

### 初期データ(例)

| service_id | add | change | remove | capacity |
|---|---|---|---|---|
| github | api / api | api / api | api / api | なし(シート課金の枠管理が必要になったら tracked にするだけ) |
| paid-license(JetBrains等) | manual / human | manual / human | manual / human | あり: total=ledger、threshold=1、調達=manual |
| claude | manual / human | manual / human | manual / human | あり: 当面 total=ledger |

Claude は **Anthropic Admin API の導入時に、各操作を `api / api` に、capacity の
把握を `api` に書き換えてコネクタを1本追加するだけで自動化に昇格できる**。
軸の値の変更だけで済み、フロー側の改修は発生しない。

### サービスの増減・昇格手順

| 変更 | 手順 | 入口WFの改修 |
|---|---|---|
| サービスを追加 | カタログに1行追加。execute が `api` / `iac-pr` / `prepared` の操作にはハンドラサブWFを1本作成 | 不要 |
| サービスを外す | `enabled: false` に変更(履歴保持のため物理削除しない) | 不要 |
| 方式の昇格(`manual`→`api`、`none`→`api` 等) | ハンドラ作成+該当 operation の軸値を変更 | 不要 |

## 役割マトリクス

役割 × サービス → 付与内容(grant)。追加時はこの表から grant を引き、
役割変更時は新旧の grant 差分を適用する。

| 役割 \ サービス | github | paid-license | claude |
|---|---|---|---|
| developer | team: `dev-members` | JetBrains 1シート | 標準シート |
| reviewer | team: `dev-members`, `reviewers` | JetBrains 1シート | 標準シート |
| pm | team: `pm` | − | 標準シート |
| admin | team: `dev-members`, org role: owner | JetBrains 1シート | 管理者シート |

※ 役割名・値は例。**grant の値はカタログ側では不透明な文字列/JSONとして扱い、
解釈はハンドラ(自動実行時)またはタスク本文への埋め込み(人手実行時)に委ねる。**
これによりサービスごとの表現の違いがマトリクスの構造に影響しない。
`−` は付与なし(削除時の棚卸しでは verify が `human` / `none` のサービスを
念のため確認対象に含める。→ [01. 削除フロー](01-member-lifecycle.md#削除フローmember-remove))。

## ハンドラの入出力インターフェース

自動実行系のハンドラサブWFは全て同じ入出力に従う。
入口フローはハンドラ名をカタログから読んで Execute Workflow で呼び出すだけ。

```jsonc
// 入力
{
  "operation": "add | change | remove",
  "member": { "email": "...", "name": "...", "service_account": "..." }, // service_account は台帳のサービス固有ID
  "grant": "<役割マトリクスの値>",          // add / change で使用
  "previous_grant": "<旧役割の値>"          // change のみ
}
// 出力(種別ごと)
{ "ok": true, "detail": "..." }             // connector(api): 実行結果。失敗時は ok: false + 理由。冪等に作る
{ "ok": true, "pr_url": "..." }             // iac-pr: 作成した PR の URL(タスク台帳に記録し、マージを verify で確認)
{ "ok": true, "attachment": "..." }         // preparer(prepared): タスクに添付する準備物(CSV・文面・リンク等)
```

## 台帳スキーマ

台帳アクセスは `ledger-read` / `ledger-write` サブWFに一元化し、
格納先を差し替えても呼び出し側が変わらないようにする。

### メンバー台帳

| 列 | 説明 |
|---|---|
| email(キー) / name / role | 基本情報と現在の役割 |
| status | `provisioning` → `active` → `offboarding` → `removed` |
| service_accounts | JSON。サービス固有IDと付与済み grant(例: `{"github": {"username": "hogeo", "grant": "dev-members"}}`) |
| joined_at / left_at / updated_at | 日付・履歴 |

### PC台帳

| 列 | 説明 |
|---|---|
| netbox_device_id(キー) | NetBox 側の device ID(詳細は NetBox が正) |
| machine_name / user_email / serial / purchased_at | 基本情報 |
| status | `registering` → `license-pending` → `active` → `retired` |

### タスク台帳

| 列 | 説明 |
|---|---|
| task_id(キー) | 一意ID |
| kind | `member-add` / `member-remove` / `role-change` / `pc-license` / `procurement` / `audit` など |
| subject / description | 件名と手順(タスクテンプレートから展開。`prepared` の準備物や `iac-pr` の PR URL もここに載る) |
| assignee | 担当者(通知先。セルフサービス型では本人) |
| due | 期限 |
| status | `open` / `done` / `failed` / `escalated` / `cancelled` |
| verify / verify_ref | 確認方式(カタログから複写)と検証用の参照(コネクタ名・クエリ等)。remind-scheduler が自動クローズ判定に使う |
| remind_count / last_reminded_at | リマインド制御(→ [04](04-notification-abstraction.md)) |
| source | 起票元(申請ID・実行ID)。追跡用 |
| complete_url | 完了報告リンク(verify が `human` / `none` の場合に使用) |

## 格納先の選定

| 候補 | 長所 | 短所 |
|---|---|---|
| **n8n Data Tables(第一候補)** | n8n 内蔵で追加インフラ不要。ワークフローから最速で読み書きできる | 一覧性・共有 UI が弱い。バックアップが n8n DB 依存 |
| Google Sheets | 閲覧・手修正・共有が容易 | API クオータ、手編集によるスキーマ崩れリスク |
| NocoDB(self-host) | UI と API の両立、スキーマ強制 | 運用コンポーネントが増える |

**方針**: 検証は n8n Data Tables で開始する。運用に乗せて「人が台帳を直接見たい」
需要が強くなったら Sheets / NocoDB へ移行する。移行影響は `ledger-read` /
`ledger-write` の内部実装のみ(呼び出し側は無改修)。
サービスカタログ・役割マトリクス・必須ソフトカタログも同じ格納先に置く。

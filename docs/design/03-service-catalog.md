# 03. サービスカタログと台帳リポジトリ

対象サービスが増減しても入口ワークフローを改修せずに済ませるための中核設計。
**サービスをコードではなくデータとして持ち、サービスごとの連携形態の違いは
「分類の3軸」の値の違いとして表現する。**
カタログと台帳の実体はどちらも Git リポジトリ([後述](#台帳リポジトリgit))。

## 分類の3軸

サービスとの連携形態(SCIM/SAMLで完結、APIで自動実行、枠の手動調達+自動割当、
管理者への依頼+事後把握、IaC経由、本人セルフサービス……)を「パターン」として
列挙・追加していくのではなく、**操作(add / change / remove)ごと**に次の3軸の
組み合わせで表す。新しい連携形態は原則「新しい組み合わせ」にすぎず、
カタログの構造もリコンサイラも変わらない。

### 軸1: 実行方式(execute)

| 値 | 意味 | n8n の動き |
|---|---|---|
| `idp` | SCIM/SAML/JIT で IdP 側から連鎖して完結 | 実行なし(state への記録のみ) |
| `api` | API で直接実行できる | コネクタサブWFを実行 |
| `iac-pr` | Terraform 等のコード管理下 | 設定リポジトリへの PR を自動作成。マージが承認と実行を兼ね、適用結果は事後検証で確認 |
| `prepared` | 実行は人だが準備は自動化できる | 貼り付け用 CSV・招待文面・対象画面への直リンク等を生成し、タスクに添付して起票 |
| `manual` | 完全に人手 | タスク起票のみ |

本人が操作するセルフサービス型(招待リンクからの自己サインアップ等)は、
タスクの担当(assignee)を管理者ではなく**本人**にした `prepared` / `manual` として表す。

**規則: remove 操作に `idp` は使えない。**
Keycloak のアカウント停止で以降の SSO ログインは止まるが、
**JIT プロビジョニングで作成された SaaS 側のアカウントは残る**。
シート課金のサービスでは退職後も枠と費用を消費し続けるため、
remove は最低でも `{execute: manual, verify: api}`(削除依頼+API で残存確認)
とし、確認手段がなければ `{manual, human}`(棚卸し確認タスク)とする。
add / change に `idp` を使うのは問題ない(JIT で作られることが期待どおりのため)。
この規則は台帳リポジトリの CI で検証する。

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
  state に残して、充足後に再開する。
- 空き枠の監視(threshold 判定)は remind-scheduler の日次実行に含める
  ([04](04-notification-abstraction.md#リマインドループ共通型))。
- 削除時は割当解放により空き枠が戻ったことを verify 方式で確認する。

補足: 期限付き付与でサービス側が自動失効する形態(JIT アクセス等)は、
remove を `{execute: idp または api, verify: api}` とみなし、
削除時は失効確認のみを行う対象にする。

## サービスカタログ

実体は台帳リポジトリの `catalog/services.yaml`。1エントリ = 1サービスで、
リコンサイラは実行時に有効なエントリを読み込んで対象サービスを列挙する。
**カタログの変更も PR を通る**ため、「どのサービスをどう扱うか」の変更自体が
レビューと履歴の対象になる。

| 項目 | 型 | 説明 |
|---|---|---|
| service_id | 文字列 | 一意なID(例: `github`) |
| name | 文字列 | 表示名 |
| operations | マップ | 操作(add / change / remove)ごとの3軸設定(下記) |
| capacity | マップ | 枠ブロック(枠なしのサービスは省略) |
| order | 数値 | 実行順。Keycloak を最優先にする(追加時は下流 SCIM/JIT の前提、削除時はキルスイッチ)ために使う |
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
| idp(Keycloak) | api / api | api / api | api / api | なし。order 最優先。Admin REST API でユーザー作成・グループ所属・停止(enabled: false)を行い、SSO 対応 SaaS へは JIT プロビジョニングで連鎖 |
| github | api / api | api / api | api / api | なし(シート課金の枠管理が必要になったら tracked にするだけ) |
| paid-license(JetBrains等) | manual / human | manual / human | manual / human | あり: total=ledger、threshold=1、調達=manual |
| claude | manual / human | manual / human | manual / human | あり: 当面 total=ledger |

Claude は **Anthropic Admin API の導入時に、各操作を `api / api` に、capacity の
把握を `api` に書き換えてコネクタを1本追加するだけで自動化に昇格できる**。
軸の値の変更だけで済み、リコンサイラの改修は発生しない。

### サービスの増減・昇格手順

| 変更 | 手順 | リコンサイラの改修 |
|---|---|---|
| サービスを追加 | `catalog/services.yaml` に1エントリ追加する PR。execute が `api` / `iac-pr` / `prepared` の操作にはハンドラサブWFを1本作成 | 不要 |
| サービスを外す | `enabled: false` にする PR(履歴保持のため物理削除しない) | 不要 |
| 方式の昇格(`manual`→`api`、`none`→`api` 等) | ハンドラ作成+該当 operation の軸値を変更する PR | 不要 |

## 役割マトリクスとチームマトリクス

grant の源泉は2枚のマトリクス+個人例外で、リコンサイラは次の和集合として
「あるべき grant 集合」を計算する:

**あるべき grant 集合 = 役割マトリクス(role) ∪ ⋃ チームマトリクス(team, role) ∪ extra_grants**

### 役割マトリクス(`catalog/role-matrix.yaml`)

役割 × サービス → **チームに依存しない横断的な権限**。
チーム別の権限はここには書かない(チームマトリクスへ)。

| 役割 \ サービス | idp(Keycloak グループ) | github | paid-license | claude |
|---|---|---|---|---|
| developer | −(チーム側で付与) | −(チーム側で付与) | JetBrains 1シート | 標準シート |
| reviewer | − | − | JetBrains 1シート | 標準シート |
| pm | `pm` | team: `pm` | − | 標準シート |
| admin | `admins` | org role: owner | JetBrains 1シート | 管理者シート |

### チームマトリクス(`catalog/team-matrix.yaml`)

チーム × サービス → **チーム別の権限**。役割との掛け合わせはセル内の
テンプレートで表現する(役割×チームごとに役割を増やす方向は組合せ爆発するので
採らない)。

```yaml
alpha:
  idp:    "group:team-alpha"          # Keycloak グループ(SSO対応SaaS側の範囲制御)
  github: "team:alpha-{role_suffix}"  # developer→alpha-devs / reviewer→alpha-reviewers
beta:
  idp:    "group:team-beta"
  github: "team:beta-{role_suffix}"
```

チームの新設・廃止はこのファイルへの行追加・削除の PR。
GitHub 側の Team 構造(ネスト・命名・権限レベル)は [06](06-github-teams.md)。

### 個人単位の例外(extra_grants)

原則はマトリクス経由(必要ならチームを作る)。どうしても個人単位の付与が
必要な場合のみ `members/*.yaml` の `extra_grants:` に書けるが、
**CI が警告ラベルを付け、`review_until:`(見直し期限)を必須**とする。
期限が来たら remind-scheduler が棚卸しを促す。無期限の個人例外は認めない。

### 共通の規約

- affiliation による役割制約(例: `admin` は `employee` のみ)は台帳リポジトリの
  CI 検証で強制する。
- 役割名・チーム名・値は例。**grant の値はカタログ側では不透明な文字列/JSONとして
  扱い、解釈はハンドラ(自動実行時)またはタスク本文への埋め込み(人手実行時)に
  委ねる。**これによりサービスごとの表現の違いがマトリクスの構造に影響しない。
- `−` は付与なし(削除時の棚卸しでは verify が `human` / `none` のサービスを
  念のため確認対象に含める。→ [01. 削除](01-member-lifecycle.md))。

## ハンドラの入出力インターフェース

自動実行系のハンドラサブWFは全て同じ入出力に従う。
リコンサイラはハンドラ名をカタログから読んで Execute Workflow で呼び出すだけ。

```jsonc
// 入力
{
  "operation": "add | change | remove",
  "member": { "email": "...", "name": "...", "service_account": "..." }, // service_account は members/ の accounts: の値
  "grant": "<役割マトリクスの値>",          // add / change で使用
  "previous_grant": "<旧役割の値>"          // change のみ
}
// 出力(種別ごと)
{ "ok": true, "detail": "..." }             // connector(api): 実行結果。失敗時は ok: false + 理由。冪等に作る
{ "ok": true, "pr_url": "..." }             // iac-pr: 作成した PR の URL(state に記録し、マージを verify で確認)
{ "ok": true, "attachment": "..." }         // preparer(prepared): タスクに添付する準備物(CSV・文面・リンク等)
```

## 台帳リポジトリ(Git)

台帳の実体は専用の Git リポジトリ(プレースホルダ `LEDGER_REPO`)。
**あるべき状態(desired)を人が PR で編集し、実態(state)を bot が記録する。**
台帳アクセスは従来どおり `ledger-read` / `ledger-write` サブWFに一元化する
(内部実装が GitHub API の読み取り/コミットになるだけで、呼び出し側は不変)。

```
ledger-repo/
├── members/<id>.yaml        # desired: 人が PR で編集(要レビュー)
├── catalog/
│   ├── services.yaml        # サービスカタログ(3軸・order・capacity)
│   ├── role-matrix.yaml     # 役割マトリクス(横断的な権限)
│   ├── team-matrix.yaml     # チームマトリクス(チーム別の権限)
│   └── pc-software.yaml     # PC 必須ソフトカタログ
└── state/
    ├── members/<id>.yaml    # actual: 付与実態と未完了タスク(bot が記録)
    ├── pcs/<資産管理番号>.yaml      # PC 台帳(bot が記録。仮番号のこともある)
    └── servers/<資産管理番号>.yaml  # 物理サーバ台帳(同上。VM は NetBox のみ → 07)
```

### 書き込み権限の規約

| パス | 書く人 | 保護 |
|---|---|---|
| `members/` `catalog/` | 人(+bot の自動 PR) | PR 必須・レビュー必須(ブランチ保護)。CI がスキーマ・制約を検証し、権限差分をコメント |
| `state/` | bot のみ | bot マシンアカウントは保護をバイパスして直接コミット。人による `state/` 編集は CI で拒否 |

bot は保護をバイパスできるため、**「bot は `state/` しか書かない」は規約であって
技術的強制ではない**。GitHub Rulesets のパス制限で bot Token の書き込み対象を
`state/` に限定し、規約を強制に変える
(→ [08](08-safeguards.md#脅威モデルと残余リスク))。

### メンバー ID(`<id>`)の規則

- `<id>` は**メールアドレスのローカル部を小文字化し、記号を `-` に正規化した slug**
  (例: `hogeo@example.com` → `hogeo`)。衝突する場合のみ末尾に連番を付ける。
- **`<id>` は不変**として扱い、メールアドレスが変わってもファイル名は変えない
  (`email:` フィールドを書き換える PR で対応する)。
  ファイル名を人の同定キーにせず、`email:` を実体の同定に使う。
  ID とメールの両方で重複がないことを CI で検証する。
- PC・サーバの state はファイル名が変わりうる(資産管理番号の正式化で
  bot が git mv する → [02](02-pc-register.md))が、メンバーは
  リネームを発生させない設計とする。

### members/<id>.yaml(desired)

```yaml
name: Hoge Hogeo
email: hogeo@example.com
affiliation: employee          # employee | partner:<会社名>
role: developer                # 役割マトリクスに存在する役割名
teams: [alpha]                 # 所属チーム(チームマトリクスのキー)。兼務は複数書く
status: active                 # active | left
accounts:                      # サービス固有 ID(人が宣言)
  github: hogeo
sponsor: pm@example.com        # partner のみ必須(受入責任者)
# extra_grants:                # 個人単位の例外(原則禁止。CI が警告し期限必須)
#   - { service: github, grant: "team:secops", review_until: 2026-12-31 }
# contract_until: 2027-03-31   # partner のみ。時限付与の起点
# effective_from: 2026-09-01   # 役割変更の適用日(任意)
# left_at: 2026-09-30          # 削除の発効日(status: left とセット)
# immediate: true              # 即時遮断(削除時のみ)
```

status は `active` / `left` の2値だけ。旧設計の `provisioning` / `offboarding` に
あたる中間状態は「desired ≠ state(未収束)」として自然に表現されるため、
明示の状態機械は持たない。

### state/members/<id>.yaml(actual、bot 管理)

```yaml
grants:
  idp:    { grant: "group:dev", since: 2026-08-20, verified_at: 2026-08-22 }
  github: { grant: "team:dev-members", since: 2026-08-20 }
tasks:
  - id: t-0042
    kind: member-add           # member-add / role-change / member-remove / procurement / audit など
    subject: JetBrains シート購入
    assignee: buy@example.com
    due: 2026-08-27
    status: open               # open / done / failed / escalated / cancelled
    verify: human              # カタログから複写。自動クローズ判定に使う
    remind_count: 1
    last_reminded_at: 2026-08-24
    complete_url: https://...  # verify が human / none の完了報告リンク
    source: pr-123             # 起票元(PR・実行ID)。追跡用
attempts: { github: 0 }        # 失敗の再試行カウント(上限超過でエスカレーション)
```

タスク台帳は独立させず、各メンバー(および各 PC)の state ファイル内に持つ。
remind-scheduler や監査は `state/` を走査して横断ビューを作る。
リマインドのたびに bot コミットが発生するが、小規模運用ではノイズとして許容する
(気になったら remind メタデータだけ外部ストアに逃がす余地を残す)。

### state/pcs/<資産管理番号>.yaml

PC 登録フローは宣言型ではなくイベント駆動の記録簿型のまま。
**キーは資産管理番号**(仮の番号のこともある)。正式化で番号が変わったら
bot がファイルをリネーム(git mv)して記録を引き継ぐ(NetBox device ID と
git 履歴が連続性を保つ)。実機情報の正は NetBox。

```yaml
netbox_device_id: 42
user: hogeo@example.com
acquisition: purchase          # purchase(新規購入) | transfer(他部署からの搬入)
acquired_at: 2026-08-15
computer_name: { value: dev-hogeo-tmp, provisional: true }   # 手入力。情シス採番前は仮
asset_no:      { value: KARI-0012,     provisional: true }   # 手入力の仮番号
ip_address: null
status: license-pending        # registering → license-pending → active → retired
tasks:
  - { id: t-0051, kind: pc-license,       subject: JetBrains 購入,                 status: open, verify: human, blocking: true }
  - { id: t-0052, kind: pc-official-info, subject: 正式な資産管理番号と名前の反映, status: open, verify: api,   blocking: true }
  - { id: t-0053, kind: pc-ip,            subject: IP アドレスの登録,              status: open, verify: api,   blocking: false }
```

タスクの `blocking`(既定 true)は親レコードの完了判定に効く: `false` の
タスクは完了判定を妨げないが、リマインドと監査の対象には含まれ続ける
(例: PC の IP 登録は強制しないが未入力を追いかける)。詳細は
[02](02-pc-register.md)。

`state/servers/<資産管理番号>.yaml`(物理サーバ台帳)も同じスキーマを使う
(ライセンスタスクがない点だけ異なる)。VM は Git 台帳に載せず NetBox のみで
管理する(→ [07](07-servers.md))。

### 並行書き込みの制御

`state/` にはマージ Webhook 起動のリコンサイラ、日次リコンサイル、
remind-scheduler が**同時に**コミットしうる。n8n に分散ロックはないため、
`ledger-write` 側で明示的に制御する。

- **直列化**: `ledger-write` サブワークフローを**同時実行数 1**(n8n の
  ワークフロー設定)にし、書き込みを1本の待ち行列に通す。
- **楽観的排他+リトライ**: 読み取り時のファイル SHA を保持し、更新時に
  その SHA を指定する(GitHub Contents API)。SHA 不一致(他の実行が先に
  書いた)なら**読み直して差分を再適用し、リトライ**する
  (指数バックオフ、上限回数)。上限超過はエスカレーション。
- **書き込み単位を小さく**: 1回のコミットは1ファイル(1メンバー / 1機器)に
  限定し、衝突の確率と再適用の複雑さを下げる。
- 呼び出し側はこの制御を意識しない(`ledger-write` の内部に隠蔽する)。

### 履歴・監査証跡

`git log` がそのまま監査証跡になる(誰がいつ何を承認・マージし、
bot がいつ何を付与・剥奪したか)。専用の履歴テーブルは持たない。
ただしこれが監査証跡として成立するには **force push の禁止**と
**bot 侵害時の改竄可能性の認識**が前提になる
(→ [08](08-safeguards.md#git-log-が監査証跡の前提条件))。

### 検討の経緯(採用しなかった候補)

| 候補 | 見送りの主な理由 |
|---|---|
| n8n Data Tables | 変更履歴が残らず、アクセス権台帳として監査に弱い。閲覧が n8n ログイン者限定 |
| Google Sheets | 手編集でスキーマが崩れる。変更履歴が粗い |
| NocoDB / Baserow 等 | 運用コンポーネントが増える。非エンジニアが台帳を直接編集する需要が強くなったら再検討 |

Git 台帳の弱点はクエリ不可な点だが、メンバー数百人規模までは
n8n 側での全ファイル走査で問題にならない。

# 06. GitHub(GHEC)連携設計

チームごとにアクセス可能範囲が異なる GitHub(GHEC)を、Team 構造と
チームマトリクスでどう表現するかをまとめる。

## 前提

- GitHub Enterprise Cloud、**非 EMU**(メンバーは個人アカウント。
  Org への SAML SSO は Keycloak)。
- GHEC の Team Sync(IdP グループ → Team の自動同期)は Entra ID / Okta 限定で、
  **Keycloak では使えない**。したがって Team 所属は n8n がコネクタ
  (`service-github`)で管理する。カタログで github を `api / api` とした裏付け。

## 「チーム」と「Team」の区別

| 層 | 例 | どこに現れるか |
|---|---|---|
| **チーム**(組織上の概念) | `alpha`, `beta` | `members/*.yaml` の `teams:`、チームマトリクスのキー |
| **Team**(GitHub のリソース) | `alpha-devs`, `alpha-reviewers` | チームマトリクスのセル値(grant)として導出 |

1つのチームがサービスごとに複数の実装物へ翻訳される(GitHub では役割別の
Team、Keycloak ではグループ `team-alpha`)。人が宣言するのは組織上の事実
(`teams: [alpha]`)だけで、どの Team に入るかはマトリクスが導出する。

## Team 構造(ネスト)

```
all-members                      ← 親Team。共通リポジトリをここに1回付与
├── alpha-devs                   ← alpha × developer
│   └── alpha-reviewers          ← alpha × reviewer(alpha-devs の子)
├── beta-devs
│   └── beta-reviewers
└── pm                           ← チーム横断の役割
```

GitHub のネスト Team は**権限が親→子、メンバーシップが子→親**に流れる:
親 Team のリポジトリ権限は子 Team のメンバー全員に継承され、
子のメンバーは親のメンバーとしても扱われる(@mention・アクセス判定に含まれる)。

この構造の効果:

1. **共通リポジトリは親に1回付与するだけ**。新チーム追加時の「共通リポジトリを
   見られない」事故と、共通リポジトリ追加時に全 Team へ付与して回る作業
   (N×M の管理)が構造的に消える。
2. **マトリクスから共通アクセスが消える**。横断的なアクセスはデータではなく
   GitHub 側の構造(親への付与)に1回だけ現れる。
3. **剥奪が連動する**。子 Team から外れると親経由の共通アクセスも消える。
   兼務者は最後の所属を失った瞬間に共通アクセスが消える(残留漏れ防止)。
4. **監査が薄くなる**。consistency-audit は子 Team のメンバーシップだけ
   突合すればよい。

注意点:

- 権限は**加算のみ**(子で親より狭められない)。親に置く権限は最小(read 中心)
  にし、機密リポジトリの権限を親に付けない。
- ネストした Team は secret visibility にできない(Team 名は組織内に見える)。
- どのチームにも属さない人は親 Team への直接所属で救う
  (役割マトリクスの github 列に `team:all-members` が現れる唯一のケース)。
- 役割の階層もネストで表現できる: `alpha-reviewers` を `alpha-devs` の子に
  すると、reviewer は dev の権限を継承した上に上乗せされる。

## 命名規則と対応表

Team 名は Org 内で一意で、API・mention は slug(小文字英数とハイフン)で参照する
(`@{GITHUB_ORG}/alpha-devs`)。`{チーム名}-{役割系統}` に統一し、チームマトリクス
のテンプレート `team:{team}-{role_suffix}` からの機械展開・mention・API パスを
一致させる。

| チーム | 役割 | GitHub Team | 備考 |
|---|---|---|---|
| alpha | developer | `alpha-devs` | |
| alpha | reviewer | `alpha-reviewers` | `alpha-devs` の子 |
| beta | developer | `beta-devs` | |
| − | pm | `pm` | チーム横断。役割マトリクス側から付与、`all-members` 直下 |
| − | admin | (Team なし) | Team ではなく org role: owner で付与 |

### 未定義の(チーム, 役割)組合せの扱い

`pm` の人が `teams: [alpha]` を持つ場合、テンプレートの展開結果
(`alpha-pms`)はこの表に存在しない。**存在しない Team への付与を試みると
実行時エラーになるため、CI で事前に弾く。**

- チームマトリクスの `github` セルには、**役割ごとに展開可能な組合せを明示**する
  (例: alpha は `developer` / `reviewer` のみ)。
- 明示されていない組合せの `members/*.yaml` を CI が**エラー**にする
  (黙ってスキップしない。付与漏れが静かに発生するのを防ぐ)。
- 意図的に「そのチームではその役割に GitHub 権限を与えない」場合は、
  マトリクスに**明示的な「付与なし」**として書く。未定義と区別する。

## リポジトリ権限と「レビュアーの力」の出どころ

リポジトリ権限は read / triage / write / maintain / admin の5段階。
この設計では devs = write、reviewers = maintain とする(親から write を継承し、
明示付与で maintain。権限は常に全経路の最大)。

| Team | リポジトリ | 権限 |
|---|---|---|
| `all-members` | `docs`, `dev-tools` | read / write |
| `alpha-devs` | `alpha-app`, `alpha-infra` | write |
| `alpha-reviewers` | 同上 | maintain |

ただし **write と maintain の差は小さく(maintain は非破壊的なリポジトリ管理が
少し増えるだけ)、レビュアーの実効的な力は権限レベルからは出てこない**。
それを与えるのは、ブランチ保護と CODEOWNERS が Team を参照することである:

- CODEOWNERS に `* @{GITHUB_ORG}/alpha-reviewers` → 保護ブランチへの PR は
  reviewers Team の承認が必須になる
- ブランチ保護で「必須承認レビュー」+「Code Owner のレビュー必須」を有効化
- 必要なら「レビューを却下できる人」「保護ブランチにマージできる人」も
  reviewers Team に限定する

実務上の注意: ブランチ保護の必須承認としてカウントされるレビューには
**write 以上が必要**(read でもレビューコメントは書けるが必須承認を満たせない)。
reviewers は devs の子として write を継承するため、この条件は構造的に満たされる。

## スコープ境界

本設計(台帳+リコンサイラ)が管理するのは「**人 ↔ Team 所属**」まで。
「Team ↔ リポジトリの権限レベル」「ブランチ保護・CODEOWNERS」は
リポジトリ構成側の管理でありスコープ外とする。この部分の変更管理が必要に
なったら、リポジトリ設定を Terraform 等でコード化して `iac-pr` 方式
([03](03-service-catalog.md#分類の3軸))を足すのが筋。

**ただし監査はスコープ外にしない。**
リポジトリへの個別 collaborator 招待(outside collaborator)は
**Team モデルを完全に迂回するアクセス経路**であり、権限レベルの管理を
スコープ外にしたことと、その存在を検出しないことは別問題である。

## 監査(consistency-audit の GitHub 部分)

| 検査 | API | 判定 |
|---|---|---|
| Org メンバーの突合 | `GET /orgs/{org}/members` | 台帳にいない人が Org にいる / `left` の人が残っている → 異常 |
| Team メンバーシップの突合 | `GET /orgs/{org}/teams/{slug}/members` | あるべき grant との差分 → 異常 |
| **outside collaborator の検出** | `GET /orgs/{org}/outside_collaborators`、各リポジトリの `GET /repos/{owner}/{repo}/collaborators?affiliation=direct` | **存在自体を異常として報告**する(Team 経由でないアクセス。正当な例外は台帳の `extra_grants` に記録されているべき) |
| **Org owner の検出** | `GET /orgs/{org}/members?role=admin` | 台帳で `admin` 役割でない owner → 異常。**高リスクのため日次**(→ [08](08-safeguards.md#監査頻度)) |
| 保留中の招待 | `GET /orgs/{org}/invitations` | 長期未承諾の招待の可視化(誤ったユーザー名宛の招待の発見にもなる) |

## GitHub ユーザー名の本人性

`accounts.github` は申請者の自己申告で、CI が検証できるのは書式だけである。
タイポや他人のユーザー名を書けば、**その相手に Org 招待が飛ぶ**。

- 非 EMU では招待の承諾が必要なため、誤招待がそのままアクセスにはならない
  (致命傷にはなりにくい)。しかし誤った相手に組織の存在と Team 名が露出する。
- 手当て(実装フェーズで選択):
  1. **招待をメールアドレス宛にする**(`POST /orgs/{org}/invitations` の `email`)
     — 本人のメールアドレスは Keycloak と同じ値で検証済みのため、
     ユーザー名の申告ミスの影響を受けない。承諾したアカウントを
     `accounts.github` に**事後記録**する(bot が state に書く)。
  2. ユーザー名申告を採る場合は、招待前に
     `GET /users/{username}` で実在とプロフィールを確認し、
     PR の差分プレビューに表示して承認者に目視確認させる。
- **推奨は 1**(メール宛招待)。人が入力する識別子を1つ減らせる。

## コネクタ(service-github)の操作対応

| 操作 | API | 備考 |
|---|---|---|
| add | `POST /orgs/{org}/invitations`(**team_ids に子 Team を指定**。宛先は本人のメールアドレス。上記「本人性」参照) | 招待の承諾と同時に Team 所属が成立する。承諾待ちの間は desired ≠ state が残り、日次リコンサイルが承諾後の収束を確認する |
| change | `PUT` / `DELETE /orgs/{org}/teams/{team_slug}/memberships/{username}` | チーム異動・役割変更の差分適用。username は `members/*.yaml` の `accounts.github` |
| remove | `DELETE /orgs/{org}/members/{username}` | Org からの削除で全 Team から同時に外れる |
| verify | `GET /orgs/{org}/members`、`GET /orgs/{org}/teams/{team_slug}/members` | consistency-audit とタスク自動クローズに使用 |

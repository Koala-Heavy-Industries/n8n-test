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

## コネクタ(service-github)の操作対応

| 操作 | API | 備考 |
|---|---|---|
| add | `POST /orgs/{org}/invitations`(**team_ids に子 Team を指定**) | 招待の承諾と同時に Team 所属が成立する。承諾待ちの間は desired ≠ state が残り、日次リコンサイルが承諾後の収束を確認する |
| change | `PUT` / `DELETE /orgs/{org}/teams/{team_slug}/memberships/{username}` | チーム異動・役割変更の差分適用。username は `members/*.yaml` の `accounts.github` |
| remove | `DELETE /orgs/{org}/members/{username}` | Org からの削除で全 Team から同時に外れる |
| verify | `GET /orgs/{org}/members`、`GET /orgs/{org}/teams/{team_slug}/members` | consistency-audit とタスク自動クローズに使用 |

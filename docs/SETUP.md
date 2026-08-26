# 別環境でのセットアップ手順

**このリポジトリを新しい環境(別の会社・別のマシン)で一から構築する手順。**
検証済みの順序で書いてある。上から順に実行すること。

設計の背景は [docs/design/](design/)、実装の詳細は
[09. ワークフロー実装](design/09-workflows.md)、運用は
[ハンドブック](handbook.html)を参照。

---

## 0. 前提

| 必要なもの | 条件 |
|---|---|
| Docker | **メモリ 4 GiB 以上**を割り当てること。2 GiB 程度だと n8n が繰り返し停止して構築できない |
| GitHub アカウント | 台帳リポジトリを作れること。**Organization は必須ではない**(ただし GitHub Team 連携を使うなら必要) |
| `gh` CLI | ログイン済み(`gh auth status` で確認) |
| Python 3 | スクリプト実行用(標準ライブラリのみ使用) |

Colima を使っている場合のメモリ変更:

```bash
colima stop && colima start --memory 6 --cpu 4
```

Docker Desktop の場合は 設定 → Resources → Memory から変更する。
**この設定を怠ると、途中でワークフローのインポートが `exit 137` で失敗する。**

---

## 1. 台帳リポジトリを作る

権限データの置き場。この n8n-test とは**別のリポジトリ**にする
(個人情報を含むため閲覧範囲を分ける。→ [08](design/08-safeguards.md#台帳リポジトリの個人情報))。

```bash
# 雛形をコピーして新しいリポジトリにする(<組織>-ledger は任意の名前)
cp -r ledger-template ../my-ledger
cd ../my-ledger
git init -b main && git add -A
git commit -m "台帳の初期構成"
gh repo create my-ledger --private --source=. --push
cd -
```

雛形には次が入っている:

- `catalog/` — サービスカタログ・役割マトリクス・チームマトリクス・PC必須ソフト
- `ci/ledger.py` と `.github/workflows/validate.yml` — PR の自動検証
- `members/` `state/` — 空のディレクトリ

**この時点でカタログは検証用の値のまま。**あとで自組織の役割・チーム・
サービスに置き換える(手順 8)。

### ブランチ保護(本番では必須)

「申請者≠承認者」を規約ではなく強制にするため、リポジトリ設定で:

- main への直接 push を禁止(PR 必須)
- レビュー必須・自己承認禁止
- force push 禁止
- Rulesets で bot Token の書き込み可能パスを `state/` に限定

**GitHub Free の個人アカウント + プライベートリポジトリでは設定できない。**
その場合は規約運用になるため、本番移行時の必須項目として残しておくこと。

---

## 2. GitHub トークンを作る

台帳を読み書きするための fine-grained PAT。
https://github.com/settings/personal-access-tokens/new

| 項目 | 設定 |
|---|---|
| Repository access | **作成した台帳リポジトリのみ** |
| Contents | **Read and write**(ファイルの読み書き、ブランチ作成) |
| Pull requests | **Read and write**(申請フォームからの PR 作成) |

**両方必要。** Contents だけだとブランチ作成までは成功して PR 作成で 403 になる。
権限が足りないときは、GitHub の応答ヘッダ `x-accepted-github-permissions` に
必要な権限が書かれているので、それを見て特定できる。

---

## 3. 環境変数を用意する

```bash
cp .env.example .env
```

`.env` を開いて秘密値を埋める。生成は `openssl rand -hex 32` など。

| 変数 | 値 |
|---|---|
| `N8N_ENCRYPTION_KEY` | 生成。**紛失すると全 credential が復元不能**。別途バックアップすること |
| `N8N_DB_PASSWORD` / `KEYCLOAK_DB_PASSWORD` / `NETBOX_DB_PASSWORD` | 生成 |
| `KEYCLOAK_ADMIN_PASSWORD` / `NETBOX_ADMIN_PASSWORD` | 生成 |
| `NETBOX_SECRET_KEY` | 生成(50文字以上) |
| `NETBOX_TOKEN_PEPPER` | 生成。**これが無いと NetBox の API トークンを作れない** |
| `GITHUB_TOKEN` | 手順 2 のトークン |
| `LEDGER_REPO` | `<オーナー>/<台帳リポジトリ名>` |
| `GITHUB_ORG` | GitHub の Organization 名(Team 連携を使う場合) |
| `NETBOX_TOKEN` / `KEYCLOAK_CLIENT_SECRET` | **空のままでよい**。次の手順で自動生成される |

宛先(`ADMIN_EMAILS` など)は検証中はそのままでよい。Mailpit が全部受け取る。

---

## 4. 起動する

```bash
docker compose up -d
```

NetBox は初回のマイグレーションで1〜2分かかる。次で応答を確認:

```bash
curl -s -o /dev/null -w "n8n %{http_code}\n"      http://localhost:5678/healthz
curl -s -o /dev/null -w "keycloak %{http_code}\n" http://localhost:8080/realms/master
curl -s -o /dev/null -w "netbox %{http_code}\n"   http://localhost:8000/api/   # 403 でよい(認証が要るだけ)
```

---

## 5. Keycloak と NetBox を初期設定する

```bash
./scripts/setup-keycloak.py   # realm・グループ・n8n用サービスアカウント
./scripts/setup-netbox.py     # APIトークン・サイト・ロール・カスタムフィールド
```

どちらも**何度実行してもよい**(冪等)。

- `setup-keycloak.py` が最後に出力する **Client Secret** を `.env` の
  `KEYCLOAK_CLIENT_SECRET` に貼る。
- `setup-netbox.py` は API トークンを生成して**自動的に `.env` に書き戻す**
  (NetBox の新方式ではトークンの平文を作成時にしか取得できないため)。

**Keycloak のグループは `catalog/` の `group:` の値と一致している必要がある。**
カタログを変えたら `scripts/setup-keycloak.py` の `GROUPS` も合わせる。

---

## 6. 認証情報とワークフローを投入する

```bash
./scripts/import-credentials.py   # .env から n8n の Credential を作成
./scripts/deploy-workflows.py     # workflows/*.json をインポート・有効化・再起動
```

`import-credentials.py` は秘密値をファイルに残さず、コンテナへ直接渡す。

---

## 7. 動作を確認する

```bash
# 台帳が読めるか
./scripts/run-workflow.py ledger-read '{"kind":"catalog"}'

# 収束させる(初回はカタログのサンプルメンバーが対象)
./scripts/run-workflow.py reconcile '{}'

# 監査
curl -s -X POST -d '{}' http://localhost:5678/webhook/weekly-audit
```

| 確認先 | URL |
|---|---|
| n8n | http://localhost:5678 |
| Keycloak | http://localhost:8080(realm を `khi-dev` に切り替える) |
| NetBox | http://localhost:8000 |
| Mailpit(送信メール) | http://localhost:8025 |

申請フォームの URL は n8n の各 form ワークフローの webhookId で決まる
(`/form/<webhookId>`)。JSON に固定値で書いてあるので環境が変わっても同じ。
→ [README の申請フォーム](../README.md#申請フォーム)

---

## 8. 自組織に合わせる

ここまでは検証用の値で動く状態。実運用に向けて置き換える。

| 対象 | 内容 |
|---|---|
| `catalog/role-matrix.yaml` | 実際の役割と、役割ごとの権限 |
| `catalog/team-matrix.yaml` | 実際のチームと、チームごとの権限 |
| `catalog/services.yaml` | 対象サービス。担当者のプレースホルダ(`LICENSE_ASSIGNEE` 等)は `.env` のキー名で書く |
| `catalog/pc-software.yaml` | PC の必須ソフト |
| `members/sample-*.yaml` | **削除する**(検証用サンプル) |
| `.env` の宛先 | `APPROVER_EMAILS` / `ADMIN_EMAILS` / `LICENSE_ASSIGNEE` を実在のアドレスに |
| `scripts/setup-keycloak.py` の `GROUPS` | カタログの `group:` に合わせる |
| 通知チャネル | `workflows/notify.json` の中身を Gmail / Slack に差し替え(入出力は変えない) |

**本番移行時の必須チェック**(→ [08](design/08-safeguards.md)):

- 台帳リポジトリのブランチ保護と Rulesets
- `N8N_ENCRYPTION_KEY` の別管理バックアップ
- n8n 自体のアクセス制御(管理者を最小限に)
- 外部からの死活監視(ハートビート)
- 台帳の個人情報の保持期間を決める

---

## つまずいたら

多くは既知の制約。まず
[05 の「構築時に判明した制約」](design/05-environment.md#構築時に判明した制約検証済み)
を見ること。特に多いのは次の3つ。

| 症状 | 原因 |
|---|---|
| ワークフローのインポートが `exit 137` で失敗、n8n が再起動を繰り返す | **Docker のメモリ不足**。4 GiB 以上にする |
| Webhook が 404、ワークフローは active なのに動かない | 起動直後で登録が済んでいない。数十秒待つ。それでも駄目なら Webhook を持つフローで Execute Workflow の宛先に式を使っていないか確認(**有効化に失敗する**) |
| PR 作成が 403 | トークンに `Pull requests: Read and write` が無い |

ワークフローの検証方法は
[09 の「検証のしかた」](design/09-workflows.md#検証のしかた)にまとめてある。

# このリポジトリで作業するときに

## 何をするプロジェクトか

メンバーのアカウント・権限と、PC / サーバの資産情報を、**台帳リポジトリに
書かれた状態へ自動で合わせ続ける**仕組み。n8n のワークフローで実装している。

中核は「追加処理」「削除処理」が存在しないこと。あるのは
**あるべき状態(desired)と実態(state)の差を埋めるループ**だけで、
追加・役割変更・チーム異動・削除はその現れ方が違うだけ。

## リポジトリは2つある

| | 中身 | 性質 |
|---|---|---|
| **このリポジトリ(n8n-test)** | 設計ドキュメント、ワークフロー定義、compose、スクリプト | 仕組みそのもの |
| **台帳リポジトリ**(`.env` の `LEDGER_REPO`) | `members/`(人が PR で編集)、`catalog/`、`state/`(bot が記録) | 自動化が読み書きするデータ。**個人情報を含む** |

台帳の雛形は `ledger-template/` にある(新環境ではこれをコピーして作る)。

## どこを読むか

| 知りたいこと | 場所 |
|---|---|
| 別環境で一から構築する | **[docs/SETUP.md](docs/SETUP.md)** |
| 実装がどう組まれているか、n8n の癖 | **[docs/design/09-workflows.md](docs/design/09-workflows.md)** |
| なぜその設計にしたか | [docs/design/00〜08](docs/design/) |
| 利用者向けの操作手順 | [docs/handbook.html](docs/handbook.html) |
| 現在の進捗 | [README.md](README.md) |

**実装で踏んだ n8n の落とし穴は
[05 の「構築時に判明した制約」](docs/design/05-environment.md#構築時に判明した制約検証済み)
に全部書いてある。ワークフローを触る前に読むこと。**

## 守ること

| 決まり | 理由 |
|---|---|
| **ワークフローは n8n の画面で編集しない。** `workflows/*.json` を直して `./scripts/deploy-workflows.py` | 定義を Git に残すため。画面での変更は次のデプロイで消える |
| **ワークフロー JSON には top-level の `id` を必ず付ける** | 無いと再インポートで重複する |
| **台帳の `state/` を手で編集しない** | 自動化の管理領域。実態と食い違うと収束の判断が狂う。CI でも拒否している |
| **`.env` をコミットしない** | `.gitignore` 済み。秘密値が入っている |
| **desired(`members/` `catalog/`)を bot が直接書かない** | 必ず PR にする(`ledger-propose` を使う)。承認と履歴を残すため |
| **ドキュメントの記述と実装がずれたら、ドキュメントを直す** | 設計と実装の乖離が最も危険。実装で判明したことは 05 か 09 に追記する |

## よく使うコマンド

```bash
docker compose up -d                                       # 起動
./scripts/deploy-workflows.py                              # ワークフローを反映
./scripts/run-workflow.py ledger-read '{"kind":"members"}' # サブワークフローを実行
./scripts/run-workflow.py reconcile '{}'                   # 今すぐ収束させる
./scripts/run-workflow.py pc-register '{...}' --webhook pc-register  # 入口フロー
```

`setup-keycloak.py` / `setup-netbox.py` / `import-credentials.py` はすべて冪等。

## 検証の勘所

- **待機を含むフロー(承認)や多段のサブワークフロー呼び出しは CLI では動かない。**
  Webhook を持つものは `--webhook`、無いものは一時的な Webhook ワークフロー経由で
  稼働中インスタンスに実行させる。
- 送信メールは Mailpit(http://localhost:8025)で確認する。
  通知チャネルが未決のままでも全フローを検証できる設計になっている。
- 実行が失敗したら n8n の DB を見るのが早い
  (`execution_entity` / `execution_data`。→ [09](docs/design/09-workflows.md))。

## 環境の要件

**Docker に 4 GiB 以上のメモリが要る。** 足りないと n8n が繰り返し停止し、
ワークフローのインポートが `exit 137` で失敗する。実際にこれで詰まった。

## 未実装のもの

`service-github`(Organization が必要)、サーバ・VM 登録(ハイパーバイザー製品が未確定)、
安全装置(ハートビート・定期再認定・ワークフロー定義の監査・バックアップ)。
詳細と理由は [09 の「未実装」](docs/design/09-workflows.md#未実装)。

# 07. サーバ・VM 管理設計

物理サーバ(直接 OS 運用 / ハイパーバイザー運用)と、その上の VM の
**一覧と IP アドレス**を管理する。

## 方針

- **前提**: IP はすべて固定(DHCP なし)。短命の VM はなく、VM の作成は
  低頻度で意図的な行為である。
- この前提では **全 VM の NetBox 登録は必須**と結論づける。固定 IP を消費する
  存在が1台でも漏れると IPAM に穴が開き、IP 重複を検出できなくなるため。
  登録の手間は低頻度ゆえに無視でき、台帳が陳腐化する速度も遅い。
- **一覧の正は NetBox**(dcim + virtualization + IPAM)。Git 台帳には複製しない。
  「一覧を見る」は NetBox の UI / API そのもの。
- **NetBox は IP の記録簿ではなく払い出し元**とする(後述)。
- n8n の役割は (1) 登録の入口(フォーム)、(2) 仮情報の正式化と IP の回収、
  (3) ハイパーバイザーの実態と NetBox の突合。

## NetBox でのモデリング

| 対象 | NetBox 上の表現 | 備考 |
|---|---|---|
| 物理サーバ(直接 OS) | dcim/devices、role: `server` | platform に OS(Ubuntu 等)。PC と同じ device モデル |
| 物理サーバ(ハイパーバイザー) | 上記 device + virtualization/clusters のホストとして紐付け | platform にハイパーバイザー(Proxmox / ESXi 等)、cluster type も同製品 |
| VM | virtualization/virtual-machines(cluster 所属) | **資産管理番号なし**(資産はホスト側)。name はクラスタ内一意 |
| IP アドレス | ipam/ip-addresses をインターフェースに割当、primary IP 設定 | PC と同じ。機器一覧・IP 一覧は NetBox の標準ビュー |

```mermaid
flowchart TD
    S1[物理サーバ 直接OS] --> NB[(NetBox dcim)]
    S2[物理サーバ ハイパーバイザー] --> NB
    S2 --- CL[クラスタ virtualization]
    CL --- VM1[VM]
    CL --- VM2[VM]
    S1 -.-> IPAM[(IPAM primary IP)]
    VM1 -.-> IPAM
    VM2 -.-> IPAM
```

## IP の払い出し

DHCP がない環境では、IP の採番管理が必ずどこかで(多くは手元の表で)行われて
いる。これを NetBox に寄せ、**記録して重複を検出するのではなく、払い出しの
時点で衝突しなくする**:

- 登録フォーム(vm-register / server-register。PC 登録も同様に利用可)に
  「IP を自動払い出し」オプションを設け、NetBox の available-ips API
  (`POST /api/ipam/prefixes/{id}/available-ips/`)で対象プレフィックスの
  次の空き IP を取得して割り当てる。
- 手入力の場合も NetBox 上で重複チェックしてから割り当てる。
- **NetBox を経由しない IP の割り当ては運用ルールとして禁止**する。
  これにより突合が「使用中だが NetBox にない IP はすべて異常」と
  断定できるようになる(グレーゾーンがない)。

## 登録フロー

### server-register(物理サーバ登録)

[PC 登録](02-pc-register.md)と同じ骨格(手入力の仮情報 → `provisional` タグ →
正式化タスク、IP 任意 → 回収タスク)。PC 固有のライセンスタスクは起票しない。

| 項目 | 必須 | 備考 |
|---|---|---|
| 管理者名 / 管理者メール | ✔ | 台帳の `members/` と突合 |
| 資産管理番号 | ✔ | **手入力・同定キー**(`state/servers/<資産管理番号>.yaml`)。未定なら仮の値+「仮」チェック |
| ホスト名 | ✔ | **手入力**。未定なら仮の値+「仮」チェック |
| 機種・モデル / 設置場所 | ✔ | site / rack は任意 |
| 稼働形態 | ✔ | ドロップダウン: 直接OS / ハイパーバイザー |
| OS・ハイパーバイザー種別 | ✔ | NetBox の platform に記録 |
| IPアドレス | − | 任意。未入力なら回収タスク(`blocking: false`) |

稼働形態がハイパーバイザーの場合は、NetBox に**クラスタを自動作成**して
ホストとして紐付ける(以降の VM 登録の受け皿になる)。
タスク管理は `state/servers/<資産管理番号>.yaml`(スキーマは
[state/pcs と同じ](03-service-catalog.md#台帳リポジトリgit)。正式化=blocking、
IP=非blocking)。

### vm-register(VM登録)

軽量なフォーム。**Git 台帳の state は持たない**(継続タスクがないため。
実態との差は hypervisor-sync が検出する)。

| 項目 | 必須 | 備考 |
|---|---|---|
| 所属クラスタ | ✔ | NetBox のクラスタ一覧から選択 |
| VM 名 | ✔ | クラスタ内で一意(NetBox で重複チェック) |
| 管理者メール / 用途 | ✔ | NetBox の VM に owner 相当の標準フィールドはないため、**カスタムフィールド `owner_email` / `purpose` を作成して格納する**(tenant は組織単位の概念で個人の管理者には合わないため使わない)。物理サーバ側も同じカスタムフィールドを使う |
| スペック(vCPU / メモリ / ディスク) | − | 任意 |
| IPアドレス | ✔ | **必須**。手入力(重複チェックあり)または NetBox から自動払い出し。VM は作成時点で IP が決まっているはずなので、「後で回収」のタスクは設けない |

VM の変更・削除は NetBox の直接編集でよい(下記の突合が実態との差を検出する)。

### device-update(機器情報更新)

PC・サーバ共通の**機器情報更新フォーム**。
資産管理番号(仮でも可)で対象を指定し、正式な資産管理番号・ホスト名・IP を
部分更新する。動作は [02 の正式化](02-pc-register.md#仮情報と正式化)と同じ
(**更新時も重複チェック**+NetBox 更新+state のリネーム/更新+
タスク自動クローズ)。VM は対象外(NetBox 直接編集で足りる)。

## 実態との突合(hypervisor-sync)

サービスカタログの[3軸の考え方](03-service-catalog.md#分類の3軸)を機器管理に
適用し、**まず verify から始めて execute へ昇格**する:

1. **第1段階(verify)**: 定期(日次)にハイパーバイザー API から VM 一覧を
   取得し、NetBox と突合。差分をレポートする:
   - NetBox に未登録の VM(フォーム登録漏れ・野良 VM)
   - 実体が消えたのに NetBox に残っている VM
   - IP・スペックの不一致

   IP がすべて固定で NetBox 払い出しの前提では、**検出された差分はすべて
   「異常」と断定できる**(グレーゾーンがなく、監査が鋭い)。
2. **第2段階(execute への昇格)— 保留オプション**: 差分の自動登録・
   クリーンアップへの昇格は技術的には可能だが、VM の増減が低頻度な前提では
   手動登録+検出で十分に回る。また自動ミラーにすると野良 VM も自動登録されて
   しまい、「あってはいけないものの検出」能力が落ちる。**必要が生じるまで
   昇格しない。**

直接 OS のサーバについても、突合手段が用意できれば(死活監視や
IP スキャンとの照合)同じ枠組みで将来追加できる。

## 監査への追加

weekly-audit のレポート対象に加える:

- `provisional` のままの物理サーバ(正式化の滞留)
- IP 未登録の物理サーバ(NetBox を直接走査。VM は登録時に IP 必須のため対象外)
- hypervisor-sync が検出した差分の放置(未登録 VM・消し忘れ・IP 不一致)

## 未確定事項

- **ハイパーバイザー製品**(Proxmox / ESXi / Hyper-V 等)。hypervisor-sync の
  コネクタ実装と NetBox の cluster type は製品で決まる。実装フェーズまでに確定する。

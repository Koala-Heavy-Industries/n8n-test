#!/usr/bin/env python3
"""NetBox の初期セットアップ(冪等)。

API トークンと、PC・サーバ登録に必要なマスタを作成する。

  ./scripts/setup-netbox.py

作るもの:
  - API トークン(生成して .env に書き戻す。イメージの SUPERUSER_API_TOKEN は効かない)
  - タグ provisional(仮の資産管理番号・名前が入っているデバイスの目印)
  - サイト / メーカー / デバイスタイプ(既定 generic-laptop)
  - デバイスロール dev-pc, server
  - カスタムフィールド owner_email, purpose(NetBox に owner 相当の標準項目がないため)
  - 検証用の IPAM プレフィックス

設計: docs/design/02-pc-register.md / 07-servers.md
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://localhost:8000"


def load_env(path=".env"):
    env = {}
    p = Path(path)
    if not p.exists():
        print("✗ .env がありません", file=sys.stderr)
        sys.exit(1)
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def api(method, path, token, data=None):
    url = f"{BASE}{path}"
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as res:
            raw = res.read()
            return res.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return e.code, raw.decode(errors="replace")


def ensure(token, path, lookup, payload, label):
    """lookup(クエリ文字列)で探し、無ければ作る。"""
    status, found = api("GET", f"{path}?{lookup}", token)
    if status == 200 and found and found.get("count"):
        print(f"= 既存: {label}")
        return found["results"][0]
    status, created = api("POST", path, token, payload)
    if status not in (200, 201):
        print(f"✗ {label} の作成に失敗: {status} {created}", file=sys.stderr)
        sys.exit(1)
    print(f"✓ 作成: {label}")
    return created


def ensure_token(env):
    """API トークンを用意する。

    NetBox の新方式(v2)ではトークン値はサーバが生成し、作成時にしか平文を得られない。
    そのため「.env の値が使えるなら再利用、駄目なら作り直して .env に書き戻す」形にする。
    認証情報の形式は nbt_<key>.<plaintext> で、ヘッダは `Bearer`(v1 の `Token` ではない)。
    """
    key = env.get("NETBOX_TOKEN", "")
    if key:
        status, _ = api("GET", "/api/status/", key)
        if status == 200:
            print("= API トークンは既存(.env の値が有効)")
            return key
        print("… .env のトークンが使えないため作り直します")

    script = (
        "from users.models import User, Token\n"
        "u = User.objects.get(username='%s')\n"
        "Token.objects.filter(user=u, description='n8n itops').delete()\n"
        "t = Token(user=u, description='n8n itops')\n"
        "t.save()\n"
        # v2 トークンの認証情報は nbt_<key>.<plaintext>(Bearer スキーム)
        "print('NEWTOKEN=' + (f'nbt_{t.key}.{t.token}' if t.key else t.token))\n"
        % env.get("NETBOX_ADMIN", "admin")
    )
    res = subprocess.run(
        ["docker", "compose", "exec", "-T", "netbox",
         "/opt/netbox/venv/bin/python", "/opt/netbox/netbox/manage.py", "shell", "-c", script],
        capture_output=True, text=True,
    )
    out = res.stdout + res.stderr
    token = next((l.split("=", 1)[1].strip() for l in out.splitlines() if l.startswith("NEWTOKEN=")), None)
    if not token:
        print(f"✗ トークン作成に失敗:\n{out[-800:]}", file=sys.stderr)
        sys.exit(1)

    # .env に書き戻す(平文はこの一度しか取得できないため)
    p = Path(".env")
    lines = p.read_text(encoding="utf-8").splitlines()
    lines = [f"NETBOX_TOKEN={token}" if l.startswith("NETBOX_TOKEN=") else l for l in lines]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("✓ API トークンを作成し .env に書き戻しました")
    print("  → n8n に反映するには ./scripts/import-credentials.py を実行してください")
    return token


def main():
    env = load_env()
    token = ensure_token(env)

    status, _ = api("GET", "/api/status/", token)
    if status != 200:
        print(f"✗ API に接続できません: {status}", file=sys.stderr)
        sys.exit(1)
    print(f"✓ API 疎通 ({BASE})")

    # 仮情報の目印
    ensure(token, "/api/extras/tags/", "slug=provisional",
           {"name": "provisional", "slug": "provisional", "color": "ffc107",
            "description": "資産管理番号・名前が仮の値"}, "タグ provisional")

    site = ensure(token, "/api/dcim/sites/", "slug=khi-office",
                  {"name": "KHI Office", "slug": "khi-office", "status": "active"}, "サイト KHI Office")

    mf = ensure(token, "/api/dcim/manufacturers/", "slug=generic",
                {"name": "Generic", "slug": "generic"}, "メーカー Generic")

    ensure(token, "/api/dcim/device-types/", "slug=generic-laptop",
           {"model": "Generic Laptop", "slug": "generic-laptop", "manufacturer": mf["id"], "u_height": 0},
           "デバイスタイプ generic-laptop(機種不明時の既定)")

    ensure(token, "/api/dcim/device-types/", "slug=generic-server",
           {"model": "Generic Server", "slug": "generic-server", "manufacturer": mf["id"], "u_height": 1},
           "デバイスタイプ generic-server")

    for slug, name, color in (("dev-pc", "開発PC", "2196f3"), ("server", "サーバ", "4caf50")):
        ensure(token, "/api/dcim/device-roles/", f"slug={slug}",
               {"name": name, "slug": slug, "color": color}, f"デバイスロール {slug}")

    # NetBox に「持ち主」に相当する標準項目が無いためカスタムフィールドで持つ
    for cf_name, label in (("owner_email", "利用者・管理者のメール"), ("purpose", "用途")):
        status, found = api("GET", f"/api/extras/custom-fields/?name={cf_name}", token)
        if status == 200 and found and found.get("count"):
            print(f"= 既存: カスタムフィールド {cf_name}")
            continue
        payload = {
            "object_types": ["dcim.device", "virtualization.virtualmachine"],
            "name": cf_name, "label": label, "type": "text", "required": False,
        }
        status, created = api("POST", "/api/extras/custom-fields/", token, payload)
        if status not in (200, 201):
            # 旧バージョンは content_types
            payload["content_types"] = payload.pop("object_types")
            status, created = api("POST", "/api/extras/custom-fields/", token, payload)
        if status not in (200, 201):
            print(f"✗ カスタムフィールド {cf_name} の作成に失敗: {status} {created}", file=sys.stderr)
            sys.exit(1)
        print(f"✓ 作成: カスタムフィールド {cf_name}")

    # IP の払い出し元(検証用)
    ensure(token, "/api/ipam/prefixes/", "prefix=10.20.0.0/24",
           {"prefix": "10.20.0.0/24", "status": "active", "site": site["id"],
            "description": "開発PC・サーバ用(検証)"}, "プレフィックス 10.20.0.0/24")

    print()
    print("─" * 62)
    print("n8n の Credential(HTTP Header Auth)に設定する値:")
    print("  Name  : Authorization")
    print(f"  Value : Bearer {token}")
    print(f"  管理画面: {BASE}/  (ユーザー {env.get('NETBOX_ADMIN', 'admin')})")
    print("─" * 62)


if __name__ == "__main__":
    main()

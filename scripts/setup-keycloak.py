#!/usr/bin/env python3
"""Keycloak の初期セットアップ(冪等)。

realm・グループ・n8n 用サービスアカウント・SMTP(Mailpit)を作成する。
何度実行しても同じ状態になる。

  ./scripts/setup-keycloak.py

グループは台帳のカタログ(khi-ledger/catalog/*.yaml)の grant 値
"group:<名前>" に対応する。カタログを変えたらここも合わせる。

設計: docs/design/05-environment.md / 03-service-catalog.md
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = os.environ.get("KEYCLOAK_BASE_URL", "http://localhost:8080")
REALM = os.environ.get("KEYCLOAK_REALM", "khi-dev")
CLIENT_ID = "n8n"

# 台帳カタログの group: に対応(role-matrix.yaml / team-matrix.yaml)
GROUPS = ["pm", "admins", "team-alpha", "team-beta"]

# n8n のサービスアカウントに与える realm-management ロール(最小権限)
SA_ROLES = ["manage-users", "query-users", "query-groups"]


def load_env(path=".env"):
    env = {}
    p = Path(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def request(method, path, token=None, data=None, form=None):
    url = f"{BASE}{path}"
    headers = {}
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
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


def die(msg):
    print(f"  ✗ {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    env = load_env()
    admin_user = env.get("KEYCLOAK_ADMIN", "admin")
    admin_pass = env.get("KEYCLOAK_ADMIN_PASSWORD")
    if not admin_pass:
        die(".env に KEYCLOAK_ADMIN_PASSWORD がありません")

    # --- 管理トークン取得 ---
    status, tok = request(
        "POST",
        "/realms/master/protocol/openid-connect/token",
        form={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": admin_user,
            "password": admin_pass,
        },
    )
    if status != 200:
        die(f"管理トークンの取得に失敗: {status} {tok}")
    token = tok["access_token"]
    print(f"✓ 管理トークン取得 ({BASE})")

    # --- realm ---
    status, _ = request("GET", f"/admin/realms/{REALM}", token)
    if status == 404:
        status, body = request(
            "POST",
            "/admin/realms",
            token,
            data={"realm": REALM, "enabled": True, "displayName": "KHI Dev Project"},
        )
        if status not in (201, 409):
            die(f"realm 作成に失敗: {status} {body}")
        print(f"✓ realm 作成: {REALM}")
    else:
        print(f"= realm は既存: {REALM}")

    # --- SMTP(Mailpit) ---
    # コンテナ間は mailpit:1025。ホストから実行する場合も同じ値でよい
    # (設定は Keycloak コンテナ内から使われるため)
    status, body = request(
        "PUT",
        f"/admin/realms/{REALM}",
        token,
        data={
            "realm": REALM,
            "smtpServer": {
                "host": "mailpit",
                "port": "1025",
                "from": "keycloak@example.com",
                "fromDisplayName": "KHI IdP",
                "ssl": "false",
                "starttls": "false",
                "auth": "false",
            },
        },
    )
    if status not in (204, 200):
        die(f"SMTP 設定に失敗: {status} {body}")
    print("✓ SMTP を Mailpit(mailpit:1025)に設定")

    # --- グループ ---
    status, existing = request("GET", f"/admin/realms/{REALM}/groups", token)
    have = {g["name"] for g in (existing or [])}
    for name in GROUPS:
        if name in have:
            print(f"= グループは既存: {name}")
            continue
        status, body = request(
            "POST", f"/admin/realms/{REALM}/groups", token, data={"name": name}
        )
        if status not in (201, 409):
            die(f"グループ作成に失敗 {name}: {status} {body}")
        print(f"✓ グループ作成: {name}")

    # --- n8n サービスアカウントクライアント ---
    status, clients = request(
        "GET", f"/admin/realms/{REALM}/clients?clientId={CLIENT_ID}", token
    )
    if not clients:
        status, body = request(
            "POST",
            f"/admin/realms/{REALM}/clients",
            token,
            data={
                "clientId": CLIENT_ID,
                "name": "n8n reconciler",
                "enabled": True,
                "publicClient": False,
                "serviceAccountsEnabled": True,
                "standardFlowEnabled": False,
                "directAccessGrantsEnabled": False,
            },
        )
        if status not in (201, 409):
            die(f"クライアント作成に失敗: {status} {body}")
        print(f"✓ クライアント作成: {CLIENT_ID}")
        status, clients = request(
            "GET", f"/admin/realms/{REALM}/clients?clientId={CLIENT_ID}", token
        )
    else:
        print(f"= クライアントは既存: {CLIENT_ID}")
    client_uuid = clients[0]["id"]

    # --- realm-management ロールの付与 ---
    status, rm = request(
        "GET", f"/admin/realms/{REALM}/clients?clientId=realm-management", token
    )
    if not rm:
        die("realm-management クライアントが見つかりません")
    rm_uuid = rm[0]["id"]

    status, sa_user = request(
        "GET", f"/admin/realms/{REALM}/clients/{client_uuid}/service-account-user", token
    )
    if status != 200:
        die(f"サービスアカウントユーザーの取得に失敗: {status} {sa_user}")
    sa_id = sa_user["id"]

    status, all_roles = request(
        "GET", f"/admin/realms/{REALM}/clients/{rm_uuid}/roles", token
    )
    status, assigned = request(
        "GET",
        f"/admin/realms/{REALM}/users/{sa_id}/role-mappings/clients/{rm_uuid}",
        token,
    )
    assigned_names = {r["name"] for r in (assigned or [])}
    to_add = [
        {"id": r["id"], "name": r["name"]}
        for r in all_roles
        if r["name"] in SA_ROLES and r["name"] not in assigned_names
    ]
    if to_add:
        status, body = request(
            "POST",
            f"/admin/realms/{REALM}/users/{sa_id}/role-mappings/clients/{rm_uuid}",
            token,
            data=to_add,
        )
        if status != 204:
            die(f"ロール付与に失敗: {status} {body}")
        print(f"✓ ロール付与: {', '.join(r['name'] for r in to_add)}")
    else:
        print(f"= ロールは付与済み: {', '.join(SA_ROLES)}")

    # --- クライアントシークレット ---
    status, secret = request(
        "GET", f"/admin/realms/{REALM}/clients/{client_uuid}/client-secret", token
    )
    if status != 200:
        die(f"シークレットの取得に失敗: {status} {secret}")

    print()
    print("─" * 62)
    print("n8n の Credential(OAuth2 Client Credentials)に設定する値:")
    print(f"  Access Token URL : http://keycloak:8080/realms/{REALM}/protocol/openid-connect/token")
    print(f"  Client ID        : {CLIENT_ID}")
    print(f"  Client Secret    : {secret['value']}")
    print(f"  Admin API Base   : http://keycloak:8080/admin/realms/{REALM}")
    print("─" * 62)


if __name__ == "__main__":
    main()

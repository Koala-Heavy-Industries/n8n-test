#!/usr/bin/env python3
"""n8n の Credential を .env から作成する(冪等)。

秘密値はコンテナに直接パイプで渡し、ファイルとしてリポジトリに残さない。
n8n は N8N_ENCRYPTION_KEY で暗号化して保存する。

  ./scripts/import-credentials.py

.env に必要な値:
  GITHUB_TOKEN            khi-ledger 用の fine-grained PAT(Contents: Read and write)
  KEYCLOAK_CLIENT_SECRET  setup-keycloak.py が出力する値
  NETBOX_TOKEN            setup-netbox.py が生成して .env に書き戻す値

設計: docs/design/05-environment.md(認証情報の方針)
"""
import json
import subprocess
import sys
from pathlib import Path


def load_env(path=".env"):
    env = {}
    p = Path(path)
    if not p.exists():
        print("✗ .env がありません(cp .env.example .env)", file=sys.stderr)
        sys.exit(1)
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def main():
    env = load_env()
    creds = []
    skipped = []

    token = env.get("GITHUB_TOKEN", "")
    if token:
        creds.append({
            "id": "github-ledger",
            "name": "GitHub (khi-ledger)",
            "type": "httpHeaderAuth",
            "data": {"name": "Authorization", "value": f"Bearer {token}"},
        })
    else:
        skipped.append("GITHUB_TOKEN(台帳の読み書き)")

    secret = env.get("KEYCLOAK_CLIENT_SECRET", "")
    if secret:
        realm = env.get("KEYCLOAK_REALM", "khi-dev")
        url = env.get("KEYCLOAK_URL", "http://keycloak:8080")
        creds.append({
            "id": "keycloak-sa",
            "name": "Keycloak (n8n service account)",
            "type": "oAuth2Api",
            "data": {
                "grantType": "clientCredentials",
                "accessTokenUrl": f"{url}/realms/{realm}/protocol/openid-connect/token",
                "clientId": "n8n",
                "clientSecret": secret,
                "scope": "",
                "authQueryParameters": "",
                "authentication": "body",
            },
        })
    else:
        skipped.append("KEYCLOAK_CLIENT_SECRET(Keycloak 操作)")

    netbox = env.get("NETBOX_TOKEN", "")
    if netbox:
        creds.append({
            "id": "netbox",
            "name": "NetBox",
            "type": "httpHeaderAuth",
            # v2 トークンは Bearer nbt_<key>.<plaintext> 形式
            "data": {"name": "Authorization", "value": f"Bearer {netbox}"},
        })
    else:
        skipped.append("NETBOX_TOKEN(機器情報の読み書き)")

    # 通知の検証用。Mailpit は認証なしで受ける
    creds.append({
        "id": "smtp-mailpit",
        "name": "SMTP (Mailpit)",
        "type": "smtp",
        "data": {
            "user": "",
            "password": "",
            "host": "mailpit",
            "port": 1025,
            "secure": False,
            "disableStartTls": True,
        },
    })

    if not creds:
        print("✗ 取り込む credential がありません", file=sys.stderr)
        sys.exit(1)

    payload = json.dumps(creds, ensure_ascii=False)
    cmd = [
        "docker", "compose", "exec", "-T", "n8n",
        "sh", "-c",
        "cat > /tmp/creds.json && n8n import:credentials --input=/tmp/creds.json; rc=$?; rm -f /tmp/creds.json; exit $rc",
    ]
    res = subprocess.run(cmd, input=payload, capture_output=True, text=True)
    for line in (res.stdout + res.stderr).splitlines():
        if "Successfully imported" in line or "error" in line.lower():
            print("  " + line.strip())
    if res.returncode != 0:
        print(f"✗ インポートに失敗しました (exit {res.returncode})", file=sys.stderr)
        sys.exit(res.returncode)

    print(f"✓ {len(creds)} 件の credential を登録: " + ", ".join(c["name"] for c in creds))
    if skipped:
        print("… 未設定のためスキップ:")
        for s in skipped:
            print(f"    - {s}")


if __name__ == "__main__":
    main()

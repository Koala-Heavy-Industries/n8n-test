#!/usr/bin/env python3
"""workflows/*.json を n8n に反映する(インポート → 有効化 → 再起動)。

  ./scripts/deploy-workflows.py

- ワークフロー JSON は top-level の `id` で同一性を保つ(無いと再インポートで重複する)
- Execute Workflow から呼ばれるサブワークフローは active である必要があるため、
  すべて有効化する
- n8n は起動中の変更を拾わないので最後に再起動する
"""
import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

WF_DIR = pathlib.Path("workflows")


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main():
    files = sorted(WF_DIR.glob("*.json"))
    if not files:
        print("✗ workflows/*.json がありません", file=sys.stderr)
        sys.exit(1)

    ids = []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        if not d.get("id"):
            print(f"✗ {f.name} に top-level の id がありません(再インポートで重複します)",
                  file=sys.stderr)
            sys.exit(1)
        ids.append((d["id"], d["name"]))

    res = sh(["docker", "compose", "exec", "-T", "n8n",
              "n8n", "import:workflow", "--separate", "--input=/workflows"])
    if "Successfully imported" not in (res.stdout + res.stderr):
        print("✗ インポートに失敗:", file=sys.stderr)
        print((res.stdout + res.stderr)[-1500:], file=sys.stderr)
        sys.exit(1)
    print(f"✓ {len(ids)} 件をインポート")

    for wid, name in ids:
        sh(["docker", "compose", "exec", "-T", "n8n",
            "n8n", "update:workflow", f"--id={wid}", "--active=true"])
    print(f"✓ 有効化: {', '.join(n for _, n in ids)}")

    sh(["docker", "compose", "restart", "n8n"])
    for _ in range(60):
        try:
            with urllib.request.urlopen("http://localhost:5678/healthz", timeout=2) as r:
                if r.status == 200:
                    print("✓ n8n 再起動完了")
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    print("✗ n8n の再起動を確認できませんでした", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()

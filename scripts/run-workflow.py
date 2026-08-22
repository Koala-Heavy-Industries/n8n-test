#!/usr/bin/env python3
"""サブワークフローに入力を与えて実行し、結果を表示する(開発・検証用)。

  ./scripts/run-workflow.py ledger-read '{"kind": "members"}'
  ./scripts/run-workflow.py ledger-read '{"kind":"members","id":"sample-dev"}' --raw

仕組み: 入力を返す Code ノード + Execute Workflow ノードだけの一時ワークフローを
作り、CLI で実行してから削除する。n8n の API キーを使わずに済ませるため。

注意: 呼び出される側のワークフローは active である必要がある
      (docker compose exec -T n8n n8n update:workflow --id=<id> --active=true
       のあと n8n を再起動)。
"""
import argparse
import json
import re
import subprocess
import sys

TMP_ID = "tmprunnerwf0001"


def sh(cmd, stdin=None):
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True)


def find_workflow_id(name_or_id):
    res = sh(["docker", "compose", "exec", "-T", "n8n", "n8n", "list:workflow"])
    for line in res.stdout.splitlines():
        if "|" not in line:
            continue
        wid, _, wname = line.partition("|")
        if wid.strip() == name_or_id or wname.strip() == name_or_id:
            return wid.strip()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow", help="ワークフロー名または ID")
    ap.add_argument("input", nargs="?", default="{}", help="入力 JSON")
    ap.add_argument("--raw", action="store_true", help="生の実行結果を表示")
    args = ap.parse_args()

    target = find_workflow_id(args.workflow)
    if not target:
        print(f"✗ ワークフローが見つかりません: {args.workflow}", file=sys.stderr)
        sys.exit(1)

    try:
        payload = json.loads(args.input)
    except json.JSONDecodeError as e:
        print(f"✗ 入力 JSON が不正です: {e}", file=sys.stderr)
        sys.exit(1)

    caller = {
        "id": TMP_ID,
        "name": "tmp-runner",
        "nodes": [
            {"parameters": {}, "id": "e0000000-0000-4000-8000-000000000001",
             "name": "Trigger", "type": "n8n-nodes-base.executeWorkflowTrigger",
             "typeVersion": 1, "position": [0, 0]},
            {"parameters": {"jsCode": f"return [{{json: {json.dumps(payload)}}}];"},
             "id": "e0000000-0000-4000-8000-000000000002", "name": "Input",
             "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [200, 0]},
            {"parameters": {"workflowId": {"__rl": True, "value": target, "mode": "id"},
                            "options": {}},
             "id": "e0000000-0000-4000-8000-000000000003", "name": "Call",
             "type": "n8n-nodes-base.executeWorkflow", "typeVersion": 1.2,
             "position": [400, 0]},
        ],
        "connections": {
            "Trigger": {"main": [[{"node": "Input", "type": "main", "index": 0}]]},
            "Input": {"main": [[{"node": "Call", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }

    sh(["docker", "compose", "exec", "-T", "n8n", "sh", "-c",
        "cat > /tmp/wf.json && n8n import:workflow --input=/tmp/wf.json >/dev/null 2>&1; rm -f /tmp/wf.json"],
       stdin=json.dumps(caller, ensure_ascii=False))

    res = sh(["docker", "compose", "exec", "-e", "N8N_RUNNERS_BROKER_PORT=5699", "-T",
              "n8n", "n8n", "execute", "--id", TMP_ID])
    raw = res.stdout + res.stderr

    # 後始末(CLI に delete コマンドが無いため DB から消す)
    sh(["docker", "compose", "exec", "-T", "n8n-postgres", "psql", "-U", "n8n", "-d", "n8n",
        "-c", f"DELETE FROM workflow_entity WHERE id = '{TMP_ID}';"])

    if args.raw:
        print(raw)
        return

    # 実行結果 JSON はログ行に挟まれて出力される(前後に余分な行がある)。
    # 行頭 "{" の候補を後ろから試し、raw_decode で末尾の余剰を無視して読む。
    data = None
    decoder = json.JSONDecoder()
    for m in reversed(list(re.finditer(r"^\{$", raw, re.M))):
        try:
            data, _ = decoder.raw_decode(raw[m.start():])
            break
        except json.JSONDecodeError:
            continue
    if data is None:
        print(raw[-2000:])
        sys.exit(1)

    rd = data.get("data", {}).get("resultData", {})
    status = data.get("status")

    def show_error(err, where):
        print(f"✗ 実行失敗 (status={status})")
        print(f"  ノード : {err.get('node', {}).get('name', where)}")
        msg = err.get("message") or err.get("description") or str(err)[:300]
        print(f"  内容   : {msg}")

    if rd.get("error"):
        show_error(rd["error"], "?")
        sys.exit(1)

    # サブワークフロー側のエラーは Call ノードの run に現れる
    for node, runs in (rd.get("runData") or {}).items():
        for run in runs:
            if run.get("error"):
                show_error(run["error"], node)
                sys.exit(1)

    run = rd.get("runData", {}).get("Call")
    out = []
    if run:
        for branch in run[0].get("data", {}).get("main", []):
            out.extend(item.get("json") for item in branch)
    print(f"✓ status={status}, 出力 {len(out)} 件")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

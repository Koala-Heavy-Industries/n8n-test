#!/usr/bin/env python3
"""台帳の検証と、PR への権限差分プレビュー。

  python ci/ledger.py validate              スキーマ・制約の検証
  python ci/ledger.py guard-state --base X  state/ の人手編集を拒否
  python ci/ledger.py preview --base X      権限差分の Markdown を出力

`members/` の書き方は README、判断の背景は n8n-test の docs/design を参照。

注意: あるべき grant の算出ロジックは n8n の reconcile ワークフローと同じ規則を
実装している(役割マトリクス ∪ チームマトリクス ∪ extra_grants)。
どちらかを変えたら両方を合わせること。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MEMBERS = ROOT / "members"
CATALOG = ROOT / "catalog"

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
AFFILIATION_RE = re.compile(r"^(employee|partner:.+)$")


def load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_catalog():
    services = load(CATALOG / "services.yaml").get("services", [])
    role_matrix = load(CATALOG / "role-matrix.yaml")
    team_matrix = load(CATALOG / "team-matrix.yaml").get("teams", {})
    return services, role_matrix, team_matrix


def desired_grants(member: dict, services, roles, teams) -> dict[str, list[str]]:
    """あるべき grant 集合。reconcile の「差分を計算する」と同じ規則。"""
    result: dict[str, list[str]] = {}
    if member.get("status") == "left":
        return {s["service_id"]: [] for s in services if s.get("enabled") is not False}

    for svc in services:
        if svc.get("enabled") is False:
            continue
        sid = svc["service_id"]
        grants: list[str] = []

        from_role = (roles.get("roles", {}).get(member.get("role"), {}) or {}).get(sid)
        if from_role:
            grants.append(from_role)

        for team in member.get("teams") or []:
            cell = (teams.get(team, {}) or {}).get(sid)
            value = cell.get(member.get("role")) if isinstance(cell, dict) else cell
            if value:
                grants.append(value)

        for extra in member.get("extra_grants") or []:
            if extra.get("service") == sid and extra.get("grant"):
                grants.append(extra["grant"])

        result[sid] = sorted(set(grants))
    return result


# ── 検証 ────────────────────────────────────────────────


def validate() -> list[str]:
    errors: list[str] = []
    services, role_matrix, team_matrix = load_catalog()
    service_ids = {s["service_id"] for s in services}
    valid_roles = set((role_matrix.get("roles") or {}).keys())
    valid_teams = set(team_matrix.keys())

    # --- カタログ自体の検証 ---
    for svc in services:
        sid = svc.get("service_id", "(不明)")
        for op, spec in (svc.get("operations") or {}).items():
            if op not in ("add", "change", "remove"):
                errors.append(f"catalog/services.yaml: {sid} に不正な操作 '{op}'")
                continue
            execute = spec.get("execute")
            if execute not in ("idp", "api", "iac-pr", "prepared", "manual"):
                errors.append(f"catalog/services.yaml: {sid}.{op} の execute が不正: {execute}")
            # JIT で作られた SaaS アカウントが残るため remove に idp は使えない
            if op == "remove" and execute == "idp":
                errors.append(
                    f"catalog/services.yaml: {sid}.remove に execute: idp は使えません"
                    "(JIT 作成されたアカウントとシート課金が残るため)"
                )
            if execute in ("api", "iac-pr") and not spec.get("connector"):
                errors.append(f"catalog/services.yaml: {sid}.{op} に connector がありません")
            if execute in ("manual", "prepared") and not spec.get("task_template"):
                errors.append(f"catalog/services.yaml: {sid}.{op} に task_template がありません")

    for role, cells in (role_matrix.get("roles") or {}).items():
        for sid in (cells or {}):
            if sid not in service_ids:
                errors.append(f"catalog/role-matrix.yaml: {role} が未知のサービス '{sid}' を参照")

    for team, cells in team_matrix.items():
        for sid, cell in (cells or {}).items():
            if sid not in service_ids:
                errors.append(f"catalog/team-matrix.yaml: {team} が未知のサービス '{sid}' を参照")
            if isinstance(cell, dict):
                for role in cell:
                    if role not in valid_roles:
                        errors.append(
                            f"catalog/team-matrix.yaml: {team}.{sid} が未知の役割 '{role}' を参照"
                        )

    # --- メンバーの検証 ---
    constraints = role_matrix.get("constraints") or []
    seen_emails: dict[str, str] = {}

    for path in sorted(MEMBERS.glob("*.yaml")):
        mid = path.stem
        where = f"members/{path.name}"
        if not ID_RE.match(mid):
            errors.append(f"{where}: ファイル名が不正です(小文字英数と . _ - のみ)")

        try:
            m = load(path)
        except yaml.YAMLError as e:
            errors.append(f"{where}: YAML として読めません: {e}")
            continue
        if not isinstance(m, dict):
            errors.append(f"{where}: マッピングではありません")
            continue

        for field in ("name", "email", "affiliation", "role", "status"):
            if not m.get(field):
                errors.append(f"{where}: {field} は必須です")

        email = m.get("email", "")
        if email and not EMAIL_RE.match(str(email)):
            errors.append(f"{where}: email の形式が不正です: {email}")
        if email in seen_emails:
            errors.append(f"{where}: email が {seen_emails[email]} と重複しています: {email}")
        elif email:
            seen_emails[email] = where

        affiliation = str(m.get("affiliation", ""))
        if affiliation and not AFFILIATION_RE.match(affiliation):
            errors.append(
                f"{where}: affiliation は employee か partner:<会社名> です: {affiliation}"
            )

        role = m.get("role")
        if role and role not in valid_roles:
            errors.append(
                f"{where}: 未知の役割 '{role}'(有効: {', '.join(sorted(valid_roles))})"
            )

        teams = m.get("teams")
        if teams is not None and not isinstance(teams, list):
            errors.append(f"{where}: teams は配列です")
            teams = []
        for team in teams or []:
            if team not in valid_teams:
                errors.append(
                    f"{where}: 未知のチーム '{team}'(有効: {', '.join(sorted(valid_teams))})"
                )
                continue
            # 役割×チームの未定義組合せは黙って無視せずエラーにする
            for sid, cell in (team_matrix.get(team) or {}).items():
                if isinstance(cell, dict) and role and role not in cell:
                    errors.append(
                        f"{where}: チーム '{team}' のサービス '{sid}' に役割 '{role}' の"
                        "定義がありません(付与しないなら null を明記してください)"
                    )

        status = m.get("status")
        if status not in (None, "active", "left"):
            errors.append(f"{where}: status は active か left です: {status}")
        if status == "left" and not m.get("left_at"):
            errors.append(f"{where}: status: left には left_at が必要です")

        for field in ("left_at", "contract_until", "effective_from"):
            value = m.get(field)
            if value is not None and not DATE_RE.match(str(value)):
                errors.append(f"{where}: {field} は YYYY-MM-DD 形式です: {value}")

        if affiliation.startswith("partner:"):
            for field in ("sponsor", "contract_until"):
                if not m.get(field):
                    errors.append(f"{where}: 協力会社メンバーには {field} が必要です")

        for constraint in constraints:
            if role == constraint.get("role"):
                allowed = constraint.get("allowed_affiliation") or []
                base = affiliation.split(":")[0]
                if allowed and base not in allowed:
                    errors.append(
                        f"{where}: 役割 '{role}' は {', '.join(allowed)} のみに付与できます"
                        f"(現在: {affiliation})"
                    )

        for extra in m.get("extra_grants") or []:
            if extra.get("service") not in service_ids:
                errors.append(f"{where}: extra_grants が未知のサービスを参照: {extra.get('service')}")
            if not extra.get("grant"):
                errors.append(f"{where}: extra_grants に grant がありません")
            if not extra.get("review_until"):
                errors.append(f"{where}: extra_grants には review_until(見直し期限)が必須です")

    return errors


# ── state/ の保護 ────────────────────────────────────────


def changed_files(base: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"origin/{base}...HEAD"],
        capture_output=True, text=True, cwd=ROOT,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def guard_state(base: str) -> list[str]:
    """state/ は bot の管理領域。人の PR で触られていたら止める。"""
    touched = [f for f in changed_files(base) if f.startswith("state/")]
    if not touched:
        return []
    return [
        "state/ は自動化の管理領域のため PR では変更できません: " + ", ".join(touched),
        "  実態と食い違うと収束の判断が狂います。意図的な修正が必要な場合は運用者に相談してください。",
    ]


# ── 権限差分プレビュー ───────────────────────────────────


def file_at(ref: str, path: str):
    out = subprocess.run(
        ["git", "show", f"{ref}:{path}"], capture_output=True, text=True, cwd=ROOT
    )
    if out.returncode != 0:
        return None
    return yaml.safe_load(out.stdout) or {}


def catalog_at(ref: str):
    services = (file_at(ref, "catalog/services.yaml") or {}).get("services", [])
    roles = file_at(ref, "catalog/role-matrix.yaml") or {}
    teams = (file_at(ref, "catalog/team-matrix.yaml") or {}).get("teams", {})
    return services, roles, teams


def preview(base: str) -> str:
    base_ref = f"origin/{base}"
    changed = changed_files(base)
    if not any(f.startswith(("members/", "catalog/")) for f in changed):
        return ""

    before_cat = catalog_at(base_ref)
    after_cat = load_catalog()

    ids = {p.stem for p in MEMBERS.glob("*.yaml")}
    out = subprocess.run(
        ["git", "ls-tree", "--name-only", base_ref, "members/"],
        capture_output=True, text=True, cwd=ROOT,
    )
    ids |= {Path(line).stem for line in out.stdout.splitlines() if line.endswith(".yaml")}

    rows: list[str] = []
    total_add = total_remove = affected = 0

    for mid in sorted(ids):
        before_m = file_at(base_ref, f"members/{mid}.yaml")
        after_path = MEMBERS / f"{mid}.yaml"
        after_m = load(after_path) if after_path.exists() else None

        before = desired_grants(before_m, *before_cat) if before_m else {}
        after = desired_grants(after_m, *after_cat) if after_m else {}

        member_rows: list[str] = []
        for sid in sorted(set(before) | set(after)):
            b, a = set(before.get(sid, [])), set(after.get(sid, []))
            added, removed = sorted(a - b), sorted(b - a)
            if not added and not removed:
                continue
            total_add += len(added)
            total_remove += len(removed)
            member_rows.append(
                f"| `{mid}` | `{sid}` | {' '.join(f'`{g}`' for g in added) or '—'} "
                f"| {' '.join(f'`{g}`' for g in removed) or '—'} |"
            )
        if member_rows:
            affected += 1
            rows.extend(member_rows)

    if not rows:
        return "### 権限への影響\n\nこの変更による権限の増減はありません。\n"

    catalog_changed = any(f.startswith("catalog/") for f in changed)
    header = ["### 権限への影響", ""]
    if catalog_changed:
        header += [
            f"> **カタログの変更です。{affected} 人に影響します。**",
            "> 1行の編集が全メンバーに波及します。内容を確認してください。",
            "",
        ]
    header += [
        f"付与 **{total_add}** 件 / 剥奪 **{total_remove}** 件(対象 {affected} 人)",
        "",
        "| メンバー | サービス | 付与 | 剥奪 |",
        "|---|---|---|---|",
    ]

    footer = []
    if total_remove > 0:
        footer = [
            "",
            "⚠️ **剥奪が含まれます。** 意図した変更か確認してください。",
        ]
        limit = 5
        if total_remove > limit:
            footer.append(
                f"剥奪が {total_remove} 件あり、リコンサイラの停止ライン({limit} 件)を超えています。"
                "このままマージすると適用は自動的に停止し、管理者の確認が必要になります。"
            )
    return "\n".join(header + rows + footer) + "\n"


# ── エントリポイント ─────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    g = sub.add_parser("guard-state")
    g.add_argument("--base", default="main")
    p = sub.add_parser("preview")
    p.add_argument("--base", default="main")
    p.add_argument("--out", default=None)
    args = parser.parse_args()

    if args.cmd == "validate":
        errors = validate()
    elif args.cmd == "guard-state":
        errors = guard_state(args.base)
    else:
        body = preview(args.base)
        if args.out and body:
            Path(args.out).write_text(body, encoding="utf-8")
        else:
            print(body)
        return 0

    if errors:
        print(f"✗ {len([e for e in errors if not e.startswith('  ')])} 件の問題が見つかりました\n")
        for e in errors:
            print(f"  {e}" if not e.startswith("  ") else e)
        return 1
    print("✓ 問題なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""命令行入口。

用法示例：
  python -I -m bicycle_rack_visibility_scope.cli import --data-dir data --store .store
  python -I -m bicycle_rack_visibility_scope.cli evaluate --store .store --report-out reports
  python -I -m bicycle_rack_visibility_scope.cli reconcile --store .store
  python -I -m bicycle_rack_visibility_scope.cli trace --store .store --scope-id TS-03
  python -I -m bicycle_rack_visibility_scope.cli gaps --store .store
  python -I -m bicycle_rack_visibility_scope.cli impact-rack --store .store --installation-id RI-002
  python -I -m bicycle_rack_visibility_scope.cli impact-review --store .store --batch RB-2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .evaluation import Policy, run_evaluation, reconcile_scopes
from .governance import (
    coverage_gaps,
    impact_of_rack_change,
    impact_of_review_batch,
    trace_scope,
)
from .loader import MaterialError
from .reports import (
    render_evaluation_text,
    render_impact_text,
    render_reconcile_text,
    write_evaluation_csv,
    write_evaluation_json,
)
from .store import Store

TABLE_CHOICES = ("vehicles", "racks", "events", "observations", "scopes")


def _store(args) -> Store:
    return Store(Path(args.store))


def cmd_import(args) -> int:
    store = _store(args)
    if args.file:
        if args.table is None:
            print("单文件导入必须指定 --table", file=sys.stderr)
            return 2
        batch = store.import_table_file(args.table, Path(args.file))
    else:
        batch = store.import_bundle(Path(args.data_dir))
    print(f"导入批次 {batch.batch_id}：来源 {batch.source}")
    for table, count in sorted(batch.counts.items()):
        print(f"  - {table}: {count} 条（按主键 upsert，重复导入幂等）")
    return 0


def _evaluate(store: Store, policy: Policy):
    return run_evaluation(store, policy)


def cmd_evaluate(args) -> int:
    store = _store(args)
    policy = Policy(min_trips=args.min_trips, min_positive=args.min_positive)
    evaluations = _evaluate(store, policy)
    print(render_evaluation_text(evaluations))
    if args.report_out:
        out = Path(args.report_out)
        out.mkdir(parents=True, exist_ok=True)
        write_evaluation_json(evaluations, out / "evaluation.json")
        write_evaluation_csv(evaluations, out / "evaluation.csv")
        print(f"机器可读报告已写入 {out / 'evaluation.json'} 与 {out / 'evaluation.csv'}")
    return 0


def cmd_reconcile(args) -> int:
    store = _store(args)
    evaluations = _evaluate(store, Policy())
    results = reconcile_scopes(evaluations, store.scopes)
    print(render_reconcile_text(results))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def cmd_trace(args) -> int:
    store = _store(args)
    scope = store.scopes.get(args.scope_id)
    if scope is None:
        print(f"未找到试用范围 {args.scope_id}", file=sys.stderr)
        return 1
    evaluations = _evaluate(store, Policy())
    from .timeline import RackTimeline, build_trip_evidence
    timeline = RackTimeline(list(store.racks.values()))
    evidence = build_trip_evidence(
        list(store.events.values()), list(store.observations.values()), timeline)
    lineage = trace_scope(scope, evaluations, evidence, store.vehicles)
    print(json.dumps(lineage.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_gaps(args) -> int:
    store = _store(args)
    evaluations = _evaluate(store, Policy())
    gaps = coverage_gaps(evaluations)
    print("# 覆盖缺口清单")
    if not gaps:
        print("所有评估单元视野与载荷覆盖完整。")
        return 0
    print(json.dumps(gaps, ensure_ascii=False, indent=2))
    return 0


def cmd_impact_rack(args) -> int:
    store = _store(args)
    # impact 需要旧记录副本；从当前仓库无法取得已被替换的旧值，
    # 因此由 --old-file 指向更正前的 CSV（至少含该 installation_id 一行）。
    from .loader import load_racks
    old_rows = [r for r in load_racks(Path(args.old_file))
                if r.installation_id == args.installation_id]
    if not old_rows:
        print(f"旧资料中找不到 {args.installation_id}", file=sys.stderr)
        return 1
    evaluations = _evaluate(store, Policy())
    payload = impact_of_rack_change(old_rows[0], store, evaluations)
    print(render_impact_text("车架配置更正影响分析", payload))
    return 0


def cmd_impact_review(args) -> int:
    store = _store(args)
    evaluations = _evaluate(store, Policy())
    payload = impact_of_review_batch(args.batch, store, evaluations)
    print(render_impact_text(f"复核批次 {args.batch} 影响分析", payload))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bicycle_rack_visibility_scope",
        description="自行车架遮挡条件下的辅助驾驶适用性评估（本地合成资料）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="持续导入 CSV 资料（幂等 upsert）")
    p_import.add_argument("--store", default=".store")
    p_import.add_argument("--data-dir", default="data", help="整套 CSV 目录")
    p_import.add_argument("--file", help="单个 CSV 文件路径")
    p_import.add_argument("--table", choices=TABLE_CHOICES)
    p_import.set_defaults(func=cmd_import)

    p_eval = sub.add_parser("evaluate", help="执行适用性评估")
    p_eval.add_argument("--store", default=".store")
    p_eval.add_argument("--report-out", help="报告输出目录（JSON + CSV）")
    p_eval.add_argument("--min-trips", type=int, default=3)
    p_eval.add_argument("--min-positive", type=int, default=2)
    p_eval.set_defaults(func=cmd_evaluate)

    p_rec = sub.add_parser("reconcile", help="核验试用名单并给出建议状态")
    p_rec.add_argument("--store", default=".store")
    p_rec.add_argument("--json-out")
    p_rec.set_defaults(func=cmd_reconcile)

    p_trace = sub.add_parser("trace", help="还原范围决定引用的行程级条件")
    p_trace.add_argument("--store", default=".store")
    p_trace.add_argument("--scope-id", required=True)
    p_trace.set_defaults(func=cmd_trace)

    p_gaps = sub.add_parser("gaps", help="列出覆盖缺口")
    p_gaps.add_argument("--store", default=".store")
    p_gaps.set_defaults(func=cmd_gaps)

    p_ir = sub.add_parser("impact-rack", help="车架配置更正的影响分析")
    p_ir.add_argument("--store", default=".store")
    p_ir.add_argument("--installation-id", required=True)
    p_ir.add_argument("--old-file", required=True,
                      help="更正前的 rack_installations.csv（保留旧值用）")
    p_ir.set_defaults(func=cmd_impact_rack)

    p_ib = sub.add_parser("impact-review", help="新复核批次的影响分析")
    p_ib.add_argument("--store", default=".store")
    p_ib.add_argument("--batch", required=True)
    p_ib.set_defaults(func=cmd_impact_review)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except MaterialError as exc:
        print(f"资料校验失败：{exc}", file=sys.stderr)
        return 1
    except (ValueError, KeyError) as exc:
        print(f"输入错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

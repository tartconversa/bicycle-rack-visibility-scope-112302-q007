"""报告渲染：中文文本报告与 JSON / CSV 机器可读输出。"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .domain import EvidenceClass
from .evaluation import ScopeEvaluation

DECISION_LABEL = {
    "eligible_trial": "可进入受限试用",
    "needs_samples": "需补充样本",
    "blocked": "否决（安全红线或证据失效）",
}

EVIDENCE_LABEL = {
    EvidenceClass.CONFIRMED_RECOGNITION.value: "确认识别",
    EvidenceClass.NOT_RECOGNIZED.value: "未识别",
    EvidenceClass.REVIEW_UNCERTAIN.value: "复核不确定",
    EvidenceClass.REVIEW_REVISED.value: "复核已更正",
    EvidenceClass.UNOBSERVED.value: "未观测",
    EvidenceClass.NOT_EVALUABLE.value: "无法评估",
}


def render_evaluation_text(evaluations: list[ScopeEvaluation]) -> str:
    lines = ["# 自行车架辅助驾驶适用性评估报告", ""]
    counts = {"eligible_trial": 0, "needs_samples": 0, "blocked": 0}
    for ev in evaluations:
        counts[ev.decision] += 1
    lines.append(
        f"评估单元 {len(evaluations)} 个：可进入受限试用 {counts['eligible_trial']}，"
        f"需补充样本 {counts['needs_samples']}，否决 {counts['blocked']}。")
    lines.append("证据口径：仅统计 V15 行程且车架登记已确认生效；"
                 "未观测/无法评估不计为识别成功。")
    lines.append("")
    for ev in evaluations:
        lines.append(f"## {ev.model} / {ev.hardware} / {ev.rack_model} / "
                     f"{ev.mount_position} / {ev.region} / {ev.camera_config}")
        lines.append(f"- 结论：{DECISION_LABEL[ev.decision]}（{ev.decision}）")
        lines.append(f"- 样本行程 {len(ev.trip_ids)} 次：{', '.join(ev.trip_ids) or '无'}")
        evidence_parts = [
            f"{EVIDENCE_LABEL[k]} {v}" for k, v in ev.evidence_counts.items() if v
        ]
        lines.append(f"- 证据分布：{'；'.join(evidence_parts) or '无'}")
        if ev.positive_trips:
            lines.append(f"- 确认识别行程：{', '.join(ev.positive_trips)}")
        if ev.intervention_trips:
            lines.append(f"- 真实接管行程：{', '.join(ev.intervention_trips)}")
        vis_parts = [f"{cell.key} {cell.observed}（识别 {cell.positives}）"
                     for cell in ev.visibility_coverage.values()]
        lines.append(f"- 视野覆盖：{'；'.join(vis_parts)}")
        load_parts = [f"{cell.key} {cell.observed}（识别 {cell.positives}）"
                      for cell in ev.load_coverage.values()]
        lines.append(f"- 载荷覆盖：{'；'.join(load_parts) or '无'}")
        for reason in ev.reasons:
            lines.append(f"- 依据：{reason}")
        lines.append("")
    return "\n".join(lines)


def write_evaluation_json(evaluations: list[ScopeEvaluation], path: Path) -> None:
    path.write_text(
        json.dumps([e.to_dict() for e in evaluations],
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_evaluation_csv(evaluations: list[ScopeEvaluation], path: Path) -> None:
    columns = [
        "model", "hardware", "rack_model", "mount_position", "region",
        "camera_config", "decision", "trip_count", "confirmed_recognition",
        "not_recognized", "review_uncertain", "review_revised",
        "unobserved", "not_evaluable", "intervention_trips",
        "missing_visibility", "missing_loads", "reasons",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for ev in evaluations:
        writer.writerow({
            "model": ev.model,
            "hardware": ev.hardware,
            "rack_model": ev.rack_model,
            "mount_position": ev.mount_position,
            "region": ev.region,
            "camera_config": ev.camera_config,
            "decision": ev.decision,
            "trip_count": len(ev.trip_ids),
            "confirmed_recognition": ev.evidence_counts["confirmed_recognition"],
            "not_recognized": ev.evidence_counts["not_recognized"],
            "review_uncertain": ev.evidence_counts["review_uncertain"],
            "review_revised": ev.evidence_counts["review_revised"],
            "unobserved": ev.evidence_counts["unobserved"],
            "not_evaluable": ev.evidence_counts["not_evaluable"],
            "intervention_trips": ";".join(ev.intervention_trips),
            "missing_visibility": ";".join(ev.missing_visibility),
            "missing_loads": ";".join(ev.missing_loads),
            "reasons": " | ".join(ev.reasons),
        })
    path.write_text("﻿" + buffer.getvalue(), encoding="utf-8")


def render_reconcile_text(results: list[dict]) -> str:
    lines = ["# 试用名单核验建议", ""]
    for r in results:
        lines.append(
            f"- {r['scope_id']}：当前 {r['current_status']} → 建议 {r['recommended_status']}")
        for reason in r["reasons"]:
            lines.append(f"    · {reason}")
    lines.append("")
    lines.append("说明：建议仅为系统核验结果，状态变更需评估负责人审批后生效。")
    return "\n".join(lines)


def render_impact_text(title: str, payload: dict) -> str:
    lines = [f"# {title}", ""]
    lines.append(json.dumps(payload, ensure_ascii=False, indent=2))
    return "\n".join(lines)

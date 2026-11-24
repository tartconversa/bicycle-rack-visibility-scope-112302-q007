"""适用性评估引擎。

评估单元（车型配置）= model × hardware × rack_model × mount_position ×
load_state × region × camera_config 的实际组合，仅统计 V15 行程中
"车架已确认生效" 的样本。V14 行程只作为历史基线对照，不构成新功能证据。

结论采用保守门槛（见 Policy），任何一条硬性安全红线（真实接管、
未识别、复核更正）都会否决；样本不足或维度覆盖缺失则判为需补样本，
绝不把 "没有报告目标" 计为成功。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .domain import (
    EvidenceClass,
    RackState,
    ScopeStatus,
    TripEvidence,
    Vehicle,
    Visibility,
)
from .timeline import RackTimeline, build_trip_evidence


@dataclass(frozen=True)
class Policy:
    """评估门槛。默认值偏保守，可由调用方覆盖但不得隐式放宽。"""

    min_trips: int = 3
    min_positive: int = 2
    # 每个维度都必须出现过至少 1 次有效（可评估）行程，否则覆盖缺失
    require_visibility: tuple[str, ...] = (
        Visibility.CLEAR.value, Visibility.PARTIAL.value)
    require_loads: tuple[str, ...] = ("empty", "loaded")
    feature_version: str = "V15"


@dataclass
class CoverageCell:
    key: str
    observed: int = 0          # 该维度取值下的有效行程数
    positives: int = 0


@dataclass
class ScopeEvaluation:
    """一个车型配置的评估结果，字段即解释材料。"""

    key: tuple
    model: str
    hardware: str
    rack_model: str
    mount_position: str
    region: str
    camera_config: str
    loads_seen: tuple[str, ...]
    trip_ids: tuple[str, ...]
    evidence_counts: dict[str, int]
    positive_trips: tuple[str, ...]
    negative_trips: tuple[str, ...]
    intervention_trips: tuple[str, ...]
    visibility_coverage: dict[str, CoverageCell]
    load_coverage: dict[str, CoverageCell]
    missing_visibility: tuple[str, ...]
    missing_loads: tuple[str, ...]
    hard_blocks: tuple[str, ...]
    untrusted_config_trips: tuple[str, ...]
    decision: str              # eligible_trial | needs_samples | blocked
    reasons: tuple[str, ...]
    evaluated_at: str
    policy: dict

    def to_dict(self) -> dict:
        return {
            "key": list(self.key),
            "model": self.model,
            "hardware": self.hardware,
            "rack_model": self.rack_model,
            "mount_position": self.mount_position,
            "region": self.region,
            "camera_config": self.camera_config,
            "loads_seen": list(self.loads_seen),
            "trip_ids": list(self.trip_ids),
            "evidence_counts": dict(self.evidence_counts),
            "positive_trips": list(self.positive_trips),
            "negative_trips": list(self.negative_trips),
            "intervention_trips": list(self.intervention_trips),
            "untrusted_config_trips": list(self.untrusted_config_trips),
            "visibility_coverage": {k: v.__dict__ for k, v in
                                    self.visibility_coverage.items()},
            "load_coverage": {k: v.__dict__ for k, v in self.load_coverage.items()},
            "missing_visibility": list(self.missing_visibility),
            "missing_loads": list(self.missing_loads),
            "hard_blocks": list(self.hard_blocks),
            "decision": self.decision,
            "reasons": list(self.reasons),
            "evaluated_at": self.evaluated_at,
            "policy": self.policy,
        }


# 可评估证据：排除 "无法评估"（资料缺失/遮挡）与未观测
_EVALUABLE = frozenset({
    EvidenceClass.CONFIRMED_RECOGNITION,
    EvidenceClass.NOT_RECOGNIZED,
    EvidenceClass.REVIEW_UNCERTAIN,
    EvidenceClass.REVIEW_REVISED,
})


class Evaluator:
    def __init__(self, vehicles: list[Vehicle], timeline: RackTimeline,
                 policy: Policy | None = None):
        self.vehicles = {v.vehicle_id: v for v in vehicles}
        self.timeline = timeline
        self.policy = policy or Policy()

    def evaluate(self, evidence: list[TripEvidence]) -> list[ScopeEvaluation]:
        groups: dict[tuple, list[TripEvidence]] = {}
        for te in evidence:
            if te.trip.version != self.policy.feature_version:
                continue  # V14 等历史版本不构成本功能证据
            vehicle = self.vehicles.get(te.trip.vehicle_id)
            if vehicle is None:
                continue
            # 配置未确认（pending/revised/冲突/无登记）的行程不丢弃，
            # 归入带 "未确认配置" 标记的单元，作为不可用证据参与否决判定，
            # 但永远不能单独支撑 "可试用" 结论。
            rack = te.snapshot.installation
            if rack is None:
                if te.snapshot.state is RackState.ABSENT:
                    continue  # 无车架行程与本功能无关
                # 冲突等场景无单一登记：用候选记录拼接维度并标记未确认
                if te.snapshot.candidates:
                    rack = te.snapshot.candidates[0]
                else:
                    continue
            key = (vehicle.model, vehicle.hardware, rack.rack_model,
                   rack.mount_position, vehicle.region, vehicle.camera_config)
            groups.setdefault(key, []).append(te)

        return [self._evaluate_group(key, rows) for key, rows in
                sorted(groups.items())]

    def _evaluate_group(self, key: tuple, rows: list[TripEvidence]) -> ScopeEvaluation:
        model, hardware, rack_model, mount_position, region, camera_config = key
        reasons: list[str] = []
        hard_blocks: list[str] = []

        evidence_counts = {c.value: 0 for c in EvidenceClass}
        positive: list[str] = []
        negative: list[str] = []
        interventions: list[str] = []
        untrusted: list[str] = []
        vis_cells = {v: CoverageCell(key=v) for v in
                     (Visibility.CLEAR.value, Visibility.PARTIAL.value,
                      Visibility.OCCLUDED.value)}
        load_cells: dict[str, CoverageCell] = {}
        loads_seen: set[str] = set()

        for te in rows:
            evidence_counts[te.evidence.value] += 1
            if te.snapshot.state is not RackState.ACTIVE:
                untrusted.append(te.trip.trip_id)
            rack = te.snapshot.installation or (
                te.snapshot.candidates[0] if te.snapshot.candidates else None)
            if rack is not None:
                loads_seen.add(rack.load_state)
                load_cell = load_cells.setdefault(rack.load_state,
                                                  CoverageCell(key=rack.load_state))
            vis = te.trip.rear_visibility.value
            if te.evidence in _EVALUABLE:
                vis_cells[vis].observed += 1
                if rack is not None:
                    load_cell.observed += 1
            if te.is_positive and te.snapshot.state is RackState.ACTIVE:
                # 只有配置已确认的确认识别才算正向证据
                positive.append(te.trip.trip_id)
                vis_cells[vis].positives += 1
                if rack is not None:
                    load_cell.positives += 1
            if te.evidence in (EvidenceClass.NOT_RECOGNIZED,
                               EvidenceClass.REVIEW_REVISED):
                negative.append(te.trip.trip_id)
            if te.trip.has_real_intervention:
                interventions.append(te.trip.trip_id)

        # 硬性安全红线
        if interventions:
            hard_blocks.append(
                f"存在真实驾驶员接管: {', '.join(sorted(interventions))}")
        if evidence_counts[EvidenceClass.NOT_RECOGNIZED.value]:
            hard_blocks.append(
                f"存在复核确认的未识别行程 {evidence_counts[EvidenceClass.NOT_RECOGNIZED.value]} 次")
        if evidence_counts[EvidenceClass.REVIEW_REVISED.value]:
            hard_blocks.append(
                f"存在已更正/撤回的复核 {evidence_counts[EvidenceClass.REVIEW_REVISED.value]} 条，证据不可用")
        if untrusted:
            hard_blocks.append(
                "存在车架配置未确认（待复核/已更正/登记冲突）的行程: "
                + ", ".join(sorted(untrusted)))

        evaluable = sum(evidence_counts[c.value] for c in _EVALUABLE)
        missing_vis = tuple(v for v in self.policy.require_visibility
                            if vis_cells[v].observed == 0)
        missing_loads = tuple(load for load in self.policy.require_loads
                              if load_cells.get(load, CoverageCell(key=load)).observed == 0)
        if missing_vis:
            reasons.append(f"摄像头视野覆盖缺失: {', '.join(missing_vis)}")
        if missing_loads:
            reasons.append(f"载荷条件覆盖缺失: {', '.join(missing_loads)}")

        n_trips = len(rows)
        n_positive = len(positive)
        if evaluable < self.policy.min_trips:
            reasons.append(
                f"有效行程不足: {evaluable} < 政策门槛 {self.policy.min_trips}")
        if n_positive < self.policy.min_positive:
            reasons.append(
                f"确认识别行程不足: {n_positive} < 政策门槛 {self.policy.min_positive}")
        unobserved = evidence_counts[EvidenceClass.UNOBSERVED.value]
        not_evaluable = evidence_counts[EvidenceClass.NOT_EVALUABLE.value]
        # 未观测/无法评估只做披露，不否决也不计为成功
        disclaimers: list[str] = []
        if unobserved:
            disclaimers.append(f"{unobserved} 次未观测行程，不计为识别成功")
        if not_evaluable:
            disclaimers.append(f"{not_evaluable} 次无法评估行程（资料缺失或遮挡）")

        if hard_blocks:
            decision = "blocked"
            reasons = hard_blocks + reasons
        elif not reasons:
            decision = "eligible_trial"
            reasons.append(
                f"满足门槛: 有效行程 {evaluable}，确认识别 {n_positive}，"
                "视野与载荷覆盖完整，无安全红线")
        else:
            decision = "needs_samples"
        reasons.extend(disclaimers)

        return ScopeEvaluation(
            key=key,
            model=model,
            hardware=hardware,
            rack_model=rack_model,
            mount_position=mount_position,
            region=region,
            camera_config=camera_config,
            loads_seen=tuple(sorted(loads_seen)),
            trip_ids=tuple(sorted(te.trip.trip_id for te in rows)),
            evidence_counts=evidence_counts,
            positive_trips=tuple(sorted(positive)),
            negative_trips=tuple(sorted(negative)),
            intervention_trips=tuple(sorted(interventions)),
            untrusted_config_trips=tuple(sorted(untrusted)),
            visibility_coverage=vis_cells,
            load_coverage=load_cells,
            missing_visibility=missing_vis,
            missing_loads=missing_loads,
            hard_blocks=tuple(hard_blocks),
            decision=decision,
            reasons=tuple(reasons),
            evaluated_at=datetime.now(timezone.utc).isoformat(),
            policy={
                "min_trips": self.policy.min_trips,
                "min_positive": self.policy.min_positive,
                "require_visibility": list(self.policy.require_visibility),
                "require_loads": list(self.policy.require_loads),
                "feature_version": self.policy.feature_version,
            },
        )


def run_evaluation(store, policy: Policy | None = None) -> list[ScopeEvaluation]:
    """便捷入口：从 Store 直接构建证据并评估。"""
    timeline = RackTimeline(list(store.racks.values()))
    evidence = build_trip_evidence(
        list(store.events.values()),
        list(store.observations.values()),
        timeline,
    )
    return Evaluator(list(store.vehicles.values()), timeline, policy).evaluate(evidence)


def reconcile_scopes(
    evaluations: list[ScopeEvaluation],
    existing: dict,
) -> list[dict]:
    """把评估结论与既有试用名单对齐，产出建议状态（不直接改写名单）。

    - eligible_trial → 建议 partial（受限试用）；
    - needs_samples  → 建议 proposed（继续拟建/补样本）；
    - blocked        → 建议暂停（hold），已接受的范围也必须降级复核。
    返回每条名单记录的核验结果，含支撑的评估 key 与原因，供负责人审批。
    """
    index = {e.key: e for e in evaluations}
    results = []
    for scope in existing.values():
        matches = [e for e in evaluations
                   if e.model == scope.model and e.hardware == scope.hardware
                   and e.rack_model == scope.rack_model
                   and e.region == scope.region]
        if not matches:
            results.append({
                "scope_id": scope.scope_id,
                "current_status": scope.status.value,
                "recommended_status": "hold",
                "matched_keys": [],
                "reasons": ("名单引用的车型配置在 V15 已确认车架样本中无任何证据",),
            })
            continue
        worst = _worst(matches)
        recommendation = {
            "eligible_trial": ScopeStatus.PARTIAL.value,
            "needs_samples": ScopeStatus.PROPOSED.value,
            "blocked": "hold",
        }[worst.decision]
        if scope.status is ScopeStatus.ACCEPTED and worst.decision != "eligible_trial":
            recommendation = "hold"
        results.append({
            "scope_id": scope.scope_id,
            "current_status": scope.status.value,
            "recommended_status": recommendation,
            "matched_keys": [list(e.key) for e in matches],
            "reasons": worst.reasons,
        })
    return results


def _worst(matches: list[ScopeEvaluation]) -> ScopeEvaluation:
    order = {"blocked": 0, "needs_samples": 1, "eligible_trial": 2}
    return sorted(matches, key=lambda e: order[e.decision])[0]

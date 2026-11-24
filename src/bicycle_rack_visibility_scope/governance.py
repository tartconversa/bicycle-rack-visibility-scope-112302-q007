"""评估治理：决定溯源、覆盖缺口与更正影响分析。

负责人需要回答三类问题：
1. 某个范围决定引用了哪些条件（证据链）？
2. 当前证据遗漏了哪些覆盖（维度缺口）？
3. 车架配置更正 / 新复核批次会影响哪些待执行对象？

历史行程按当时安装状态与证据版本保留，因此影响分析只针对
"尚未生效" 的对象（proposed/partial），已发生的行程不回改。
"""
from __future__ import annotations

from dataclasses import dataclass

from .domain import (
    EvidenceClass,
    RackInstallation,
    RackState,
    ScopeStatus,
    TripEvidence,
)
from .evaluation import ScopeEvaluation
from .timeline import RackTimeline, build_trip_evidence


@dataclass
class ScopeLineage:
    scope_id: str
    matched_evaluation_keys: list[list[str]]
    trips: list[dict]
    evidence_version: str
    source_version: str

    def to_dict(self) -> dict:
        return {
            "scope_id": self.scope_id,
            "matched_evaluation_keys": self.matched_evaluation_keys,
            "trips": self.trips,
            "evidence_version": self.evidence_version,
            "source_version": self.source_version,
        }


def trace_scope(scope, evaluations: list[ScopeEvaluation],
                evidence: list[TripEvidence], vehicles: dict) -> ScopeLineage:
    """还原一条试用范围决定所引用的全部行程级条件。"""
    matched = [e for e in evaluations
               if e.model == scope.model and e.hardware == scope.hardware
               and e.rack_model == scope.rack_model and e.region == scope.region]
    trip_index = {te.trip.trip_id: te for te in evidence}
    trips: list[dict] = []
    for ev in matched:
        for tid in ev.trip_ids:
            te = trip_index[tid]
            vehicle = vehicles.get(te.trip.vehicle_id)
            rack = te.snapshot.installation
            trips.append({
                "trip_id": tid,
                "vehicle_id": te.trip.vehicle_id,
                "model": vehicle.model if vehicle else None,
                "version": te.trip.version,
                "started_at": te.trip.started_at.isoformat(),
                "rack_state": te.snapshot.state.value,
                "installation_id": rack.installation_id if rack else None,
                "mount_position": rack.mount_position if rack else None,
                "load_state": rack.load_state if rack else None,
                "rear_visibility": te.trip.rear_visibility.value,
                "upload_state": te.trip.upload_state.value,
                "evidence_class": te.evidence.value,
                "intervention_id": te.trip.intervention_id or None,
                "reasons": list(te.reasons),
            })
    trips.sort(key=lambda d: d["started_at"])
    return ScopeLineage(
        scope_id=scope.scope_id,
        matched_evaluation_keys=[list(e.key) for e in matched],
        trips=trips,
        evidence_version="V15-rack-assist-eval-v1",
        source_version=scope.source_version,
    )


def coverage_gaps(evaluations: list[ScopeEvaluation]) -> list[dict]:
    """汇总所有评估单元的覆盖缺口，按缺口类型分组便于补样本排期。"""
    gaps: list[dict] = []
    for ev in evaluations:
        if not (ev.missing_visibility or ev.missing_loads):
            continue
        gaps.append({
            "key": list(ev.key),
            "decision": ev.decision,
            "missing_visibility": list(ev.missing_visibility),
            "missing_loads": list(ev.missing_loads),
            "trip_count": len(ev.trip_ids),
            "evidence_counts": dict(ev.evidence_counts),
        })
    return gaps


def impact_of_rack_change(
    changed_installation: RackInstallation,
    store,
    evaluations: list[ScopeEvaluation],
) -> dict:
    """车架配置更正后的影响面分析。

    比对更正前后（以 store 中已替换的新登记 vs. 调用方提供的旧登记）
    受影响车辆的行程证据，并列出需要重新核验的待执行名单。
    已发生行程保留旧快照；这里只标记名单层面的待核验对象。
    """
    old = changed_installation  # 调用方传入的是被替换前的副本
    new = store.racks.get(old.installation_id)
    affected_vehicle_ids = {old.vehicle_id}
    if new is not None:
        affected_vehicle_ids.add(new.vehicle_id)

    timeline = RackTimeline(list(store.racks.values()))
    evidence = build_trip_evidence(
        list(store.events.values()),
        list(store.observations.values()),
        timeline,
    )
    affected_trips = [
        te for te in evidence
        if te.trip.vehicle_id in affected_vehicle_ids
    ]
    # 评估键来自受影响车辆本身的属性 + 涉事登记的车架维度；
    # 即使登记区间晚于现有行程（尚无行程落入新区间），名单仍需重核。
    rack_dimensions = {(old.rack_model, old.mount_position)}
    if new is not None:
        rack_dimensions.add((new.rack_model, new.mount_position))
    affected_keys = set()
    for te in affected_trips:
        if te.snapshot.state is RackState.ABSENT:
            continue
        key = _key_for_trip(te, store.vehicles)
        if key:
            affected_keys.add(tuple(key))
    for vehicle_id in affected_vehicle_ids:
        vehicle = store.vehicles.get(vehicle_id)
        if vehicle is None:
            continue
        for rack_model, mount_position in rack_dimensions:
            affected_keys.add((vehicle.model, vehicle.hardware, rack_model,
                               mount_position, vehicle.region, vehicle.camera_config))

    pending_scopes = []
    for scope in store.scopes.values():
        if scope.status is ScopeStatus.ACCEPTED:
            continue  # 已生效名单不在自动重核范围，需单独审批流程
        hit = any(
            scope.model == k[0] and scope.hardware == k[1]
            and scope.rack_model == k[2] and scope.region == k[4]
            for k in affected_keys if k
        )
        if hit:
            pending_scopes.append({
                "scope_id": scope.scope_id,
                "status": scope.status.value,
                "source_version": scope.source_version,
            })

    return {
        "installation_id": old.installation_id,
        "old_record": {
            "vehicle_id": old.vehicle_id,
            "rack_model": old.rack_model,
            "mount_position": old.mount_position,
            "load_state": old.load_state,
            "valid_from": old.valid_from.isoformat(),
            "valid_to": old.valid_to.isoformat(),
            "review_state": old.review_state.value,
        },
        "new_record": None if new is None else {
            "vehicle_id": new.vehicle_id,
            "rack_model": new.rack_model,
            "mount_position": new.mount_position,
            "load_state": new.load_state,
            "valid_from": new.valid_from.isoformat(),
            "valid_to": new.valid_to.isoformat(),
            "review_state": new.review_state.value,
        },
        "affected_vehicle_ids": sorted(affected_vehicle_ids),
        "affected_trip_ids": sorted(te.trip.trip_id for te in affected_trips),
        "affected_evaluation_keys": [list(k) for k in sorted(affected_keys) if k],
        "pending_scopes_to_recheck": pending_scopes,
        "note": "历史行程保留当时安装状态快照；仅待执行名单需重新核验",
    }


def impact_of_review_batch(batch_id: str, store,
                           evaluations: list[ScopeEvaluation]) -> dict:
    """新复核批次导入后，标记受影响的行程与待执行名单。"""
    touched_obs = [o for o in store.observations.values()
                   if o.review_batch == batch_id]
    touched_trips = sorted({o.trip_id for o in touched_obs})

    timeline = RackTimeline(list(store.racks.values()))
    evidence = build_trip_evidence(
        list(store.events.values()),
        list(store.observations.values()),
        timeline,
    )
    evidence_by_trip = {te.trip.trip_id: te for te in evidence}
    touched_evidence = [evidence_by_trip[t] for t in touched_trips if t in evidence_by_trip]
    uncertain_or_revised = [
        te.trip.trip_id for te in touched_evidence
        if te.evidence in (EvidenceClass.REVIEW_UNCERTAIN,
                           EvidenceClass.REVIEW_REVISED,
                           EvidenceClass.NOT_RECOGNIZED)
    ]

    affected_keys = {
        tuple(_key_for_trip(te, store.vehicles))
        for te in touched_evidence
        if te.snapshot.state is not RackState.ABSENT
    }
    affected_keys.discard(())
    pending_scopes = []
    for scope in store.scopes.values():
        if scope.status is ScopeStatus.ACCEPTED:
            continue
        hit = any(
            scope.model == k[0] and scope.hardware == k[1]
            and scope.rack_model == k[2] and scope.region == k[4]
            for k in affected_keys if k
        )
        if hit:
            pending_scopes.append({
                "scope_id": scope.scope_id,
                "status": scope.status.value,
                "source_version": scope.source_version,
            })
    return {
        "review_batch": batch_id,
        "observation_ids": sorted(o.observation_id for o in touched_obs),
        "trip_ids": touched_trips,
        "trips_with_non_positive_evidence": sorted(uncertain_or_revised),
        "affected_evaluation_keys": [list(k) for k in sorted(affected_keys) if k],
        "pending_scopes_to_recheck": pending_scopes,
    }


def _key_for_trip(te: TripEvidence, vehicles: dict):
    vehicle = vehicles.get(te.trip.vehicle_id)
    rack = te.snapshot.installation
    if vehicle is None or rack is None:
        return ()
    return (vehicle.model, vehicle.hardware, rack.rack_model,
            rack.mount_position, vehicle.region, vehicle.camera_config)

"""时间线判定：按行程时点还原实际生效配置，并对行程证据分级。

关键原则
--------
1. 车架拆装时间可能晚于车辆登记，判定只看行程时刻落在哪些登记区间内，
   与登记写入时间无关。
2. 同一车辆同一时刻存在多条生效登记属于配置冲突，不能任选一条。
3. 事后更正不回改历史行程：行程证据在导入时按当时安装状态固化，
   更正只触发对 "尚未生效的试用名单" 的重新核验。
4. 没有观测记录是 "未观测"，不是识别成功；资料缺失/遮挡是 "无法评估"。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from .domain import (
    DriveEvent,
    EvidenceClass,
    RackInstallation,
    RackReview,
    RackSnapshot,
    RackState,
    RearObservation,
    TripEvidence,
    UploadState,
    Visibility,
)


class RackTimeline:
    """每辆车的登记区间索引，支持任意时点查询。"""

    def __init__(self, racks: list[RackInstallation]):
        self._by_vehicle: dict[str, list[RackInstallation]] = defaultdict(list)
        for rack in racks:
            self._by_vehicle[rack.vehicle_id].append(rack)
        for entries in self._by_vehicle.values():
            entries.sort(key=lambda r: (r.valid_from, r.installation_id))

    def resolve(self, vehicle_id: str, day: date) -> RackSnapshot:
        candidates = tuple(
            r for r in self._by_vehicle.get(vehicle_id, ()) if r.covers(day)
        )
        if not candidates:
            return RackSnapshot(
                state=RackState.ABSENT,
                reasons=("行程时点无生效车架登记",),
            )
        if len(candidates) > 1:
            return RackSnapshot(
                state=RackState.CONFLICTED,
                candidates=candidates,
                reasons=(
                    "同一时点存在多条生效登记: "
                    + ", ".join(r.installation_id for r in candidates),
                ),
            )
        only = candidates[0]
        if only.review_state is RackReview.PENDING:
            return RackSnapshot(
                state=RackState.PENDING,
                installation=only,
                candidates=candidates,
                reasons=("登记仍待人工复核，配置未确认",),
            )
        if only.review_state is RackReview.REVISED:
            return RackSnapshot(
                state=RackState.CONFLICTED,
                installation=only,
                candidates=candidates,
                reasons=("唯一生效登记已被更正/撤回，需重新核验",),
            )
        return RackSnapshot(
            state=RackState.ACTIVE,
            installation=only,
            candidates=candidates,
            reasons=(),
        )


def classify_trip(
    event: DriveEvent,
    snapshot: RackSnapshot,
    observations: list[RearObservation],
) -> TripEvidence:
    """把一次行程归入单一证据等级（后摄目标维度）。

    判定顺序（先不可评估，再复核质量，最后检出与否）：
    - 资料缺失（upload missing）或后摄完全遮挡 → 无法评估；
    - 后摄观测中出现 revised 复核 → 复核已更正，证据不可用；
    - 后摄观测全部 uncertain → 复核不确定；
    - 有 confirmed 复核：检出 yes → 确认识别，全部 no → 未识别；
    - 后摄无任何观测记录 → 未观测（不得视为识别成功）。
    侧置摄像头（camera=side）只作背景信息，不参与后摄判定。
    """
    reasons: list[str] = list(snapshot.reasons)
    rear = tuple(o for o in observations if o.camera == "rear")

    if event.upload_state is UploadState.MISSING:
        return TripEvidence(event, snapshot, EvidenceClass.NOT_EVALUABLE,
                            rear, tuple(reasons + ["行程资料缺失，无法评估"]))
    if event.rear_visibility is Visibility.OCCLUDED:
        return TripEvidence(event, snapshot, EvidenceClass.NOT_EVALUABLE,
                            rear, tuple(reasons + ["后摄完全遮挡，无法评估"]))
    if not rear:
        return TripEvidence(event, snapshot, EvidenceClass.UNOBSERVED,
                            rear, tuple(reasons + ["后摄无目标记录，属于未观测"]))

    revised = tuple(o for o in rear if o.review_state.value == "revised")
    if revised:
        ids = ", ".join(o.observation_id for o in revised)
        return TripEvidence(event, snapshot, EvidenceClass.REVIEW_REVISED,
                            rear, tuple(reasons + [f"复核已更正/撤回: {ids}"]))

    confirmed = tuple(o for o in rear if o.review_state.value == "confirmed")
    uncertain = tuple(o for o in rear if o.review_state.value == "uncertain")
    if not confirmed:
        ids = ", ".join(o.observation_id for o in uncertain) or "无确认复核"
        return TripEvidence(event, snapshot, EvidenceClass.REVIEW_UNCERTAIN,
                            rear, tuple(reasons + [f"复核结论不确定: {ids}"]))

    if any(o.detected for o in confirmed):
        ids = ", ".join(o.observation_id for o in confirmed if o.detected)
        return TripEvidence(event, snapshot, EvidenceClass.CONFIRMED_RECOGNITION,
                            rear, tuple(reasons + [f"复核确认识别: {ids}"]))
    ids = ", ".join(o.observation_id for o in confirmed)
    return TripEvidence(event, snapshot, EvidenceClass.NOT_RECOGNIZED,
                        rear, tuple(reasons + [f"复核确认未检出: {ids}"]))


def build_trip_evidence(
    events: list[DriveEvent],
    observations: list[RearObservation],
    timeline: RackTimeline,
) -> list[TripEvidence]:
    """为全部行程固化当时点证据（历史快照，不随后续更正而改变）。"""
    obs_by_trip: dict[str, list[RearObservation]] = defaultdict(list)
    for obs in observations:
        obs_by_trip[obs.trip_id].append(obs)
    result: list[TripEvidence] = []
    for event in sorted(events, key=lambda e: e.started_at):
        snapshot = timeline.resolve(event.vehicle_id, event.started_at.date())
        result.append(classify_trip(event, snapshot, obs_by_trip.get(event.trip_id, [])))
    return result

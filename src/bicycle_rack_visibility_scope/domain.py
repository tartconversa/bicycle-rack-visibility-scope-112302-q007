"""领域模型与取值字典。

本模块只描述业务概念，不读写文件、不做 IO。所有时间统一使用 UTC。

证据分级是本评估的核心：没有报告目标 ≠ 正确识别，因此
"未观测 / 未识别 / 复核不确定" 必须与 "确认识别" 严格区分。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# 取值字典（与 docs/资料契约.md 保持一致；新增取值必须先更新契约）
# ---------------------------------------------------------------------------

class RackReview(str, Enum):
    """车架安装登记的人工复核来源状态。"""

    CONFIRMED = "confirmed"   # 已确认，可作为生效配置证据
    PENDING = "pending"       # 待复核：配置存在但尚未被确认
    REVISED = "revised"       # 已更正：记录被修订，需重新核验引用它的决定


class Visibility(str, Enum):
    """行程开始时记录的后方摄像头可见区域。"""

    CLEAR = "clear"           # 视野完整
    PARTIAL = "partial"       # 部分遮挡（有车架不等于必然遮挡，以本字段为准）
    OCCLUDED = "occluded"     # 完全遮挡


class UploadState(str, Enum):
    """行程资料上传状态。"""

    COMPLETE = "complete"
    LATE = "late"
    MISSING = "missing"       # 资料缺失：不能当作零事件


class ObservationReview(str, Enum):
    """后方目标人工复核结论（独立于车架复核来源）。"""

    CONFIRMED = "confirmed"
    UNCERTAIN = "uncertain"
    REVISED = "revised"


class ScopeStatus(str, Enum):
    """试用范围决定状态。"""

    PROPOSED = "proposed"     # 拟建，尚未生效
    PARTIAL = "partial"       # 受限试用
    ACCEPTED = "accepted"     # 已接受（仍可能因配置更正被重新核验）


# 证据分级（评估输出使用，取值稳定，勿随意改名）
class EvidenceClass(str, Enum):
    CONFIRMED_RECOGNITION = "confirmed_recognition"  # 确认识别：复核确认且检出
    NOT_RECOGNIZED = "not_recognized"                # 未识别：有复核记录但未检出
    REVIEW_UNCERTAIN = "review_uncertain"            # 复核不确定
    REVIEW_REVISED = "review_revised"                # 复核已更正/撤回，证据不可用
    UNOBSERVED = "unobserved"                        # 未观测：该摄像头无目标记录
    NOT_EVALUABLE = "not_evaluable"                  # 资料缺失/遮挡，无法评估


# 车架在某一时点的配置状态
class RackState(str, Enum):
    ACTIVE = "active"             # 恰好一条已确认登记生效
    PENDING = "pending"           # 生效登记存在但仍待复核
    CONFLICTED = "conflicted"     # 同一车辆多条生效登记冲突
    ABSENT = "absent"             # 该时点无生效登记
    UNKNOWN = "unknown"           # 无法判定（登记区间不合法等）


VERSIONS = ("V14", "V15")
MOUNT_POSITIONS = ("hitch", "roof", "rear-door")
LOAD_STATES = ("empty", "loaded", "unknown")
CAMERAS = ("rear", "side")
TARGET_CLASSES = ("pedestrian", "vehicle", "object")
# 干预记录中的占位/无效编号（不是真实接管事件）
INTERVENTION_PLACEHOLDERS = frozenset({"", "E-00"})


@dataclass(frozen=True)
class Vehicle:
    vehicle_id: str
    model: str
    hardware: str
    region: str
    camera_config: str


@dataclass(frozen=True)
class RackInstallation:
    installation_id: str
    vehicle_id: str
    rack_model: str
    mount_position: str
    load_state: str
    valid_from: date
    valid_to: date
    review_state: RackReview

    @property
    def is_trusted(self) -> bool:
        """只有 confirmed 登记可直接作为生效配置证据。"""
        return self.review_state is RackReview.CONFIRMED

    def covers(self, day: date) -> bool:
        """闭区间 [valid_from, valid_to]。"""
        return self.valid_from <= day <= self.valid_to


@dataclass(frozen=True)
class DriveEvent:
    trip_id: str
    vehicle_id: str
    version: str
    started_at: datetime
    rear_visibility: Visibility
    upload_state: UploadState
    intervention_id: str  # 空串或 E-00 表示无真实接管记录

    @property
    def has_real_intervention(self) -> bool:
        return self.intervention_id not in INTERVENTION_PLACEHOLDERS


@dataclass(frozen=True)
class RearObservation:
    observation_id: str
    trip_id: str
    target_class: str
    camera: str
    review_state: ObservationReview
    detected: bool
    review_batch: str


@dataclass(frozen=True)
class TrialScope:
    scope_id: str
    model: str
    hardware: str
    rack_model: str
    region: str
    status: ScopeStatus
    vehicle_count: int
    source_version: str


@dataclass(frozen=True)
class RackSnapshot:
    """某次行程时点上车架配置的判定结果（保留当时状态，事后更正不回改）。"""

    state: RackState
    installation: Optional[RackInstallation] = None
    candidates: tuple[RackInstallation, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TripEvidence:
    """单次行程在 "带车架 + 后摄" 条件下的证据归类。"""

    trip: DriveEvent
    snapshot: RackSnapshot
    evidence: EvidenceClass
    observations: tuple[RearObservation, ...]
    reasons: tuple[str, ...]

    @property
    def is_positive(self) -> bool:
        return self.evidence is EvidenceClass.CONFIRMED_RECOGNITION

    @property
    def is_negative(self) -> bool:
        """明确不利证据：未识别 / 复核更正 / 真实接管。"""
        if self.evidence in (
            EvidenceClass.NOT_RECOGNIZED,
            EvidenceClass.REVIEW_REVISED,
        ):
            return True
        return self.trip.has_real_intervention

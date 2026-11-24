"""CSV 导入与校验。

数据文件被视为不可信输入：
- 使用 utf-8-sig 读取（契约规定带 BOM）；
- 逐行做字段、枚举、时间、引用完整性校验；
- 任何错误都抛出带文件名与行号的 MaterialError，绝不静默忽略。

调用方应使用解释器隔离模式（python -I）运行，避免当前目录中
被植入的同名模块影响标准库导入。
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

from .domain import (
    DriveEvent,
    LOAD_STATES,
    MOUNT_POSITIONS,
    ObservationReview,
    RackInstallation,
    RackReview,
    RearObservation,
    TARGET_CLASSES,
    CAMERAS,
    TrialScope,
    ScopeStatus,
    UploadState,
    Vehicle,
    VERSIONS,
    Visibility,
)


class MaterialError(ValueError):
    """资料不符合契约时抛出，消息包含文件名与行号。"""


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise MaterialError(f"文件不存在: {path}")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise MaterialError(f"文件缺少表头: {path}")
        rows = list(reader)
    return list(reader.fieldnames), rows


def _check_headers(path: Path, actual: list[str], expected: list[str]) -> None:
    if actual != expected:
        raise MaterialError(
            f"{path.name} 表头不匹配: 期望 {expected}, 实际 {actual}"
        )


def _require(value: str | None, filename: str, line: int, field: str) -> str:
    if value is None or value == "":
        raise MaterialError(f"{filename} 第 {line} 行字段 {field} 为空")
    return value


def _enum(value: str, allowed: tuple[str, ...], filename: str, line: int, field: str) -> str:
    if value not in allowed:
        raise MaterialError(
            f"{filename} 第 {line} 行字段 {field} 取值非法: {value!r}，允许 {allowed}"
        )
    return value


def _parse_date(value: str, filename: str, line: int, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise MaterialError(
            f"{filename} 第 {line} 行字段 {field} 日期非法: {value!r}"
        ) from exc


def _parse_dt(value: str, filename: str, line: int, field: str) -> datetime:
    try:
        # 合成资料统一为 2026-11-02T01:00Z 形式
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MaterialError(
            f"{filename} 第 {line} 行字段 {field} 时间戳非法: {value!r}"
        ) from exc


def _parse_int(value: str, filename: str, line: int, field: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise MaterialError(
            f"{filename} 第 {line} 行字段 {field} 整数非法: {value!r}"
        ) from exc


VEHICLE_COLUMNS = ["vehicle_id", "model", "hardware", "region", "camera_config"]
RACK_COLUMNS = [
    "installation_id", "vehicle_id", "rack_model", "mount_position",
    "load_state", "valid_from", "valid_to", "review_state",
]
EVENT_COLUMNS = [
    "trip_id", "vehicle_id", "version", "started_at",
    "rear_visibility", "upload_state", "intervention_id",
]
OBS_COLUMNS = [
    "observation_id", "trip_id", "target_class", "camera",
    "review_state", "detected", "review_batch",
]
SCOPE_COLUMNS = [
    "scope_id", "model", "hardware", "rack_model", "region",
    "status", "vehicle_count", "source_version",
]


def load_vehicles(path: Path) -> list[Vehicle]:
    name, rows = _read_rows(path)
    _check_headers(path, name, VEHICLE_COLUMNS)
    out: list[Vehicle] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        vid = _require(row.get("vehicle_id"), path.name, i, "vehicle_id")
        if vid in seen:
            raise MaterialError(f"{path.name} 第 {i} 行车辆编号重复: {vid}")
        seen.add(vid)
        out.append(Vehicle(
            vehicle_id=vid,
            model=_require(row.get("model"), path.name, i, "model"),
            hardware=_require(row.get("hardware"), path.name, i, "hardware"),
            region=_require(row.get("region"), path.name, i, "region"),
            camera_config=_require(row.get("camera_config"), path.name, i, "camera_config"),
        ))
    return out


def load_racks(path: Path) -> list[RackInstallation]:
    name, rows = _read_rows(path)
    _check_headers(path, name, RACK_COLUMNS)
    out: list[RackInstallation] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        rid = _require(row.get("installation_id"), path.name, i, "installation_id")
        if rid in seen:
            raise MaterialError(f"{path.name} 第 {i} 行安装编号重复: {rid}")
        seen.add(rid)
        vf = _parse_date(_require(row.get("valid_from"), path.name, i, "valid_from"),
                         path.name, i, "valid_from")
        vt = _parse_date(_require(row.get("valid_to"), path.name, i, "valid_to"),
                         path.name, i, "valid_to")
        if vf > vt:
            raise MaterialError(
                f"{path.name} 第 {i} 行 valid_from({vf}) 晚于 valid_to({vt})"
            )
        out.append(RackInstallation(
            installation_id=rid,
            vehicle_id=_require(row.get("vehicle_id"), path.name, i, "vehicle_id"),
            rack_model=_require(row.get("rack_model"), path.name, i, "rack_model"),
            mount_position=_enum(
                _require(row.get("mount_position"), path.name, i, "mount_position"),
                MOUNT_POSITIONS, path.name, i, "mount_position"),
            load_state=_enum(
                _require(row.get("load_state"), path.name, i, "load_state"),
                LOAD_STATES, path.name, i, "load_state"),
            valid_from=vf,
            valid_to=vt,
            review_state=RackReview(_enum(
                _require(row.get("review_state"), path.name, i, "review_state"),
                tuple(s.value for s in RackReview),
                path.name, i, "review_state")),
        ))
    return out


def load_events(path: Path) -> list[DriveEvent]:
    name, rows = _read_rows(path)
    _check_headers(path, name, EVENT_COLUMNS)
    out: list[DriveEvent] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        tid = _require(row.get("trip_id"), path.name, i, "trip_id")
        if tid in seen:
            raise MaterialError(f"{path.name} 第 {i} 行行程编号重复: {tid}")
        seen.add(tid)
        # intervention_id 允许为空（无接管记录），占位 E-00 也不是真实事件
        intervention = (row.get("intervention_id") or "").strip()
        out.append(DriveEvent(
            trip_id=tid,
            vehicle_id=_require(row.get("vehicle_id"), path.name, i, "vehicle_id"),
            version=_enum(
                _require(row.get("version"), path.name, i, "version"),
                VERSIONS, path.name, i, "version"),
            started_at=_parse_dt(
                _require(row.get("started_at"), path.name, i, "started_at"),
                path.name, i, "started_at"),
            rear_visibility=Visibility(_enum(
                _require(row.get("rear_visibility"), path.name, i, "rear_visibility"),
                tuple(s.value for s in Visibility),
                path.name, i, "rear_visibility")),
            upload_state=UploadState(_enum(
                _require(row.get("upload_state"), path.name, i, "upload_state"),
                tuple(s.value for s in UploadState),
                path.name, i, "upload_state")),
            intervention_id=intervention,
        ))
    return out


def load_observations(path: Path, known_trip_ids: set[str] | None = None) -> list[RearObservation]:
    name, rows = _read_rows(path)
    _check_headers(path, name, OBS_COLUMNS)
    out: list[RearObservation] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        oid = _require(row.get("observation_id"), path.name, i, "observation_id")
        if oid in seen:
            raise MaterialError(f"{path.name} 第 {i} 行观测编号重复: {oid}")
        seen.add(oid)
        trip_id = _require(row.get("trip_id"), path.name, i, "trip_id")
        if known_trip_ids is not None and trip_id not in known_trip_ids:
            raise MaterialError(
                f"{path.name} 第 {i} 行 trip_id={trip_id!r} 在行程表中不存在")
        detected = _enum(
            _require(row.get("detected"), path.name, i, "detected"),
            ("yes", "no"), path.name, i, "detected")
        out.append(RearObservation(
            observation_id=oid,
            trip_id=_require(row.get("trip_id"), path.name, i, "trip_id"),
            target_class=_enum(
                _require(row.get("target_class"), path.name, i, "target_class"),
                TARGET_CLASSES, path.name, i, "target_class"),
            camera=_enum(
                _require(row.get("camera"), path.name, i, "camera"),
                CAMERAS, path.name, i, "camera"),
            review_state=ObservationReview(_enum(
                _require(row.get("review_state"), path.name, i, "review_state"),
                tuple(s.value for s in ObservationReview),
                path.name, i, "review_state")),
            detected=(detected == "yes"),
            review_batch=_require(row.get("review_batch"), path.name, i, "review_batch"),
        ))
    return out


def load_scopes(path: Path) -> list[TrialScope]:
    name, rows = _read_rows(path)
    _check_headers(path, name, SCOPE_COLUMNS)
    out: list[TrialScope] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        sid = _require(row.get("scope_id"), path.name, i, "scope_id")
        if sid in seen:
            raise MaterialError(f"{path.name} 第 {i} 行范围编号重复: {sid}")
        seen.add(sid)
        count = _parse_int(
            _require(row.get("vehicle_count"), path.name, i, "vehicle_count"),
            path.name, i, "vehicle_count")
        if count < 0:
            raise MaterialError(f"{path.name} 第 {i} 行 vehicle_count 为负数")
        out.append(TrialScope(
            scope_id=sid,
            model=_require(row.get("model"), path.name, i, "model"),
            hardware=_require(row.get("hardware"), path.name, i, "hardware"),
            rack_model=_require(row.get("rack_model"), path.name, i, "rack_model"),
            region=_require(row.get("region"), path.name, i, "region"),
            status=ScopeStatus(_enum(
                _require(row.get("status"), path.name, i, "status"),
                tuple(s.value for s in ScopeStatus),
                path.name, i, "status")),
            vehicle_count=count,
            source_version=_require(row.get("source_version"), path.name, i, "source_version"),
        ))
    return out


def load_bundle(data_dir: Path) -> dict[str, list]:
    """一次性导入契约规定的五个文件，并做跨文件引用完整性检查。"""
    vehicles = load_vehicles(data_dir / "vehicles.csv")
    racks = load_racks(data_dir / "rack_installations.csv")
    events = load_events(data_dir / "drive_events.csv")
    trip_ids = {e.trip_id for e in events}
    observations = load_observations(
        data_dir / "rear_observations.csv", known_trip_ids=trip_ids)
    scopes = load_scopes(data_dir / "trial_scopes.csv")

    vehicle_ids = {v.vehicle_id for v in vehicles}
    _check_refs(racks, lambda r: r.vehicle_id, vehicle_ids, "rack_installations.csv", "vehicle_id")
    _check_refs(events, lambda e: e.vehicle_id, vehicle_ids, "drive_events.csv", "vehicle_id")
    return {
        "vehicles": vehicles,
        "racks": racks,
        "events": events,
        "observations": observations,
        "scopes": scopes,
    }


def _check_refs(rows, attr, known: set[str], filename: str, field: str) -> None:
    for idx, row in enumerate(rows, start=2):
        key = attr(row)
        if key not in known:
            raise MaterialError(
                f"{filename} 第 {idx} 行 {field}={key!r} 在主表中不存在"
            )

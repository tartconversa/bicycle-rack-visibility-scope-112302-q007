"""本地持久化存储：持续导入、按主键 upsert、导入批次可追溯。

设计为零依赖 JSONL 快照仓库（适合本地合成数据规模）：
- 每张表一个 JSONL 文件，每行一条记录，按业务主键去重；
- 每次 import 产生一个批次（ImportBatch），记录来源文件、条数与时间；
- 重新导入同一文件是幂等的：同主键记录被整体替换而非追加；
- 历史行程证据在评估时按当时安装状态计算，存储层不回改旧事实。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .domain import (
    DriveEvent,
    ObservationReview,
    RackInstallation,
    RackReview,
    RearObservation,
    ScopeStatus,
    TrialScope,
    UploadState,
    Vehicle,
    Visibility,
)
from .loader import (
    load_bundle,
    load_events,
    load_observations,
    load_racks,
    load_scopes,
    load_vehicles,
)

TABLE_FILES = {
    "vehicles": "vehicles.jsonl",
    "racks": "rack_installations.jsonl",
    "events": "drive_events.jsonl",
    "observations": "rear_observations.jsonl",
    "scopes": "trial_scopes.jsonl",
}
_BATCH_FILE = "import_batches.jsonl"


class ImportBatch:
    def __init__(self, batch_id: str, source: str, counts: dict[str, int],
                 imported_at: datetime):
        self.batch_id = batch_id
        self.source = source
        self.counts = counts
        self.imported_at = imported_at

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "source": self.source,
            "counts": self.counts,
            "imported_at": self.imported_at.isoformat(),
        }


class Store:
    """JSONL 快照仓库。同进程内对象缓存 + 显式 flush 落盘。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.vehicles: dict[str, Vehicle] = {}
        self.racks: dict[str, RackInstallation] = {}
        self.events: dict[str, DriveEvent] = {}
        self.observations: dict[str, RearObservation] = {}
        self.scopes: dict[str, TrialScope] = {}
        self.batches: list[ImportBatch] = []
        self._load()

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    def _path(self, table: str) -> Path:
        return self.root / TABLE_FILES[table]

    def _load(self) -> None:
        for line in self._safe_readlines(self._path("vehicles")):
            d = json.loads(line)
            self.vehicles[d["vehicle_id"]] = Vehicle(**d)
        for line in self._safe_readlines(self._path("racks")):
            d = json.loads(line)
            self.racks[d["installation_id"]] = RackInstallation(
                **{**d, "review_state": RackReview(d["review_state"]),
                   "valid_from": _date(d["valid_from"]),
                   "valid_to": _date(d["valid_to"])})
        for line in self._safe_readlines(self._path("events")):
            d = json.loads(line)
            self.events[d["trip_id"]] = DriveEvent(
                **{**d, "rear_visibility": Visibility(d["rear_visibility"]),
                   "upload_state": UploadState(d["upload_state"]),
                   "started_at": datetime.fromisoformat(d["started_at"])})
        for line in self._safe_readlines(self._path("observations")):
            d = json.loads(line)
            self.observations[d["observation_id"]] = RearObservation(
                **{**d, "review_state": ObservationReview(d["review_state"])})
        for line in self._safe_readlines(self._path("scopes")):
            d = json.loads(line)
            self.scopes[d["scope_id"]] = TrialScope(
                **{**d, "status": ScopeStatus(d["status"])})
        for line in self._safe_readlines(self.root / _BATCH_FILE):
            d = json.loads(line)
            self.batches.append(ImportBatch(
                d["batch_id"], d["source"], d["counts"],
                datetime.fromisoformat(d["imported_at"])))

    @staticmethod
    def _safe_readlines(path: Path) -> list[str]:
        if not path.exists():
            return []
        return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def flush(self) -> None:
        self._write_table(self._path("vehicles"), self.vehicles.values(),
                          lambda v: asdict(v))
        self._write_table(self._path("racks"), self.racks.values(), _rack_dict)
        self._write_table(self._path("events"), self.events.values(), _event_dict)
        self._write_table(self._path("observations"),
                          self.observations.values(), _obs_dict)
        self._write_table(self._path("scopes"), self.scopes.values(), _scope_dict)
        batch_path = self.root / _BATCH_FILE
        batch_path.write_text(
            "\n".join(json.dumps(b.to_dict(), ensure_ascii=False) for b in self.batches)
            + ("\n" if self.batches else ""),
            encoding="utf-8",
        )

    @staticmethod
    def _write_table(path: Path, rows, encoder) -> None:
        ordered = sorted(rows, key=lambda r: _first_key(r))
        path.write_text(
            "\n".join(json.dumps(encoder(r), ensure_ascii=False) for r in ordered)
            + ("\n" if ordered else ""),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # 导入
    # ------------------------------------------------------------------
    def import_bundle(self, data_dir: Path, source: str | None = None) -> ImportBatch:
        """导入契约规定的整套 CSV（先全量校验通过，再统一落库）。"""
        bundle = load_bundle(data_dir)
        return self._apply_bundle(bundle, source or str(data_dir))

    def import_table_file(self, table: str, path: Path) -> ImportBatch:
        """导入单个 CSV 文件（持续接入场景）。"""
        if table == "vehicles":
            rows = load_vehicles(path)
        elif table == "racks":
            rows = load_racks(path)
        elif table == "events":
            rows = load_events(path)
        elif table == "observations":
            rows = load_observations(path)
        elif table == "scopes":
            rows = load_scopes(path)
        else:
            raise KeyError(f"未知表: {table}")
        return self._apply_bundle({table: rows}, str(path))

    def _apply_bundle(self, bundle: dict[str, list], source: str) -> ImportBatch:
        # 引用完整性：单表导入时也要与库内既有主表核对
        self._referential_check(bundle)
        counts: dict[str, int] = {}
        if "vehicles" in bundle:
            counts["vehicles"] = self._upsert(self.vehicles, bundle["vehicles"],
                                              lambda r: r.vehicle_id)
        if "racks" in bundle:
            counts["racks"] = self._upsert(self.racks, bundle["racks"],
                                           lambda r: r.installation_id)
        if "events" in bundle:
            counts["events"] = self._upsert(self.events, bundle["events"],
                                            lambda r: r.trip_id)
        if "observations" in bundle:
            counts["observations"] = self._upsert(
                self.observations, bundle["observations"],
                lambda r: r.observation_id)
        if "scopes" in bundle:
            counts["scopes"] = self._upsert(self.scopes, bundle["scopes"],
                                            lambda r: r.scope_id)
        batch = ImportBatch(
            batch_id=f"B{len(self.batches) + 1:03d}",
            source=source,
            counts=counts,
            imported_at=datetime.now(timezone.utc),
        )
        self.batches.append(batch)
        self.flush()
        return batch

    @staticmethod
    def _upsert(target: dict, rows, key) -> int:
        for row in rows:
            target[key(row)] = row
        return len(rows)

    def _referential_check(self, bundle: dict) -> None:
        vehicle_ids = set(self.vehicles) | {v.vehicle_id for v in bundle.get("vehicles", [])}
        for rack in bundle.get("racks", []):
            if rack.vehicle_id not in vehicle_ids:
                raise ValueError(f"车架登记 {rack.installation_id} 引用未知车辆 {rack.vehicle_id}")
        event_vehicles = vehicle_ids
        for event in bundle.get("events", []):
            if event.vehicle_id not in event_vehicles:
                raise ValueError(f"行程 {event.trip_id} 引用未知车辆 {event.vehicle_id}")
        trip_ids = set(self.events) | {e.trip_id for e in bundle.get("events", [])}
        for obs in bundle.get("observations", []):
            if obs.trip_id not in trip_ids:
                raise ValueError(f"观测 {obs.observation_id} 引用未知行程 {obs.trip_id}")


def _first_key(row) -> str:
    return next(iter(asdict(row).values()))


def _date(value) -> date:
    from datetime import date as _d
    return value if isinstance(value, _d) else _d.fromisoformat(value)


def _rack_dict(r: RackInstallation) -> dict:
    return {
        "installation_id": r.installation_id,
        "vehicle_id": r.vehicle_id,
        "rack_model": r.rack_model,
        "mount_position": r.mount_position,
        "load_state": r.load_state,
        "valid_from": r.valid_from.isoformat(),
        "valid_to": r.valid_to.isoformat(),
        "review_state": r.review_state.value,
    }


def _event_dict(e: DriveEvent) -> dict:
    return {
        "trip_id": e.trip_id,
        "vehicle_id": e.vehicle_id,
        "version": e.version,
        "started_at": e.started_at.isoformat(),
        "rear_visibility": e.rear_visibility.value,
        "upload_state": e.upload_state.value,
        "intervention_id": e.intervention_id,
    }


def _obs_dict(o: RearObservation) -> dict:
    return {
        "observation_id": o.observation_id,
        "trip_id": o.trip_id,
        "target_class": o.target_class,
        "camera": o.camera,
        "review_state": o.review_state.value,
        "detected": o.detected,
        "review_batch": o.review_batch,
    }


def _scope_dict(s: TrialScope) -> dict:
    return {
        "scope_id": s.scope_id,
        "model": s.model,
        "hardware": s.hardware,
        "rack_model": s.rack_model,
        "region": s.region,
        "status": s.status.value,
        "vehicle_count": s.vehicle_count,
        "source_version": s.source_version,
    }

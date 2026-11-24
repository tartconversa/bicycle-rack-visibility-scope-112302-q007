"""合成测试数据生成器（固定种子，仅供本地流程测试）。

与 data/ 中基线资料相互独立，输出到调用方指定的空目录。
生成场景刻意覆盖评估边界：
- 车架登记晚于车辆登记、同车多登记冲突、pending/revised 登记；
- 后摄完全遮挡、资料缺失、无后观测（未观测）；
- confirmed 检出/未检出、uncertain、revised 复核；
- 真实接管 E-04 与占位编号 E-00/空值并存；
- 一个证据充分的 "可试用" 组合与一个覆盖缺失的 "需补样本" 组合。
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HEADER = "﻿"  # UTF-8 BOM，与契约一致

VEHICLES = [
    # group A：证据充分的可试用组合 M-X/H3/RACK-X/hitch/R-E/CAM-9
    ("V-101", "M-X", "H3", "R-E", "CAM-9"),
    ("V-102", "M-X", "H3", "R-E", "CAM-9"),
    ("V-103", "M-X", "H3", "R-E", "CAM-9"),
    # group B：视野/载荷覆盖缺失的组合 M-Y/H4/RACK-Y/roof/R-W/CAM-8
    ("V-201", "M-Y", "H4", "R-W", "CAM-8"),
    ("V-202", "M-Y", "H4", "R-W", "CAM-8"),
    # group C：含真实接管与未识别，必须被否决 M-Z/H5/RACK-Z/rear-door/R-N/CAM-7
    ("V-301", "M-Z", "H5", "R-N", "CAM-7"),
    ("V-302", "M-Z", "H5", "R-N", "CAM-7"),
    # 无车架行程车辆（对照）
    ("V-401", "M-Q", "H6", "R-S", "CAM-6"),
]

# (installation_id, vehicle_id, rack_model, mount, load, from, to, review)
RACKS = [
    ("RI-101", "V-101", "RACK-X", "hitch", "empty", "2026-10-01", "2026-12-31", "confirmed"),
    ("RI-102", "V-102", "RACK-X", "hitch", "loaded", "2026-10-01", "2026-12-31", "confirmed"),
    ("RI-103", "V-103", "RACK-X", "hitch", "loaded", "2026-10-01", "2026-12-31", "confirmed"),
    ("RI-201", "V-201", "RACK-Y", "roof", "loaded", "2026-10-01", "2026-10-31", "confirmed"),
    # 同车第二条登记待复核，区间在现有行程之后；更正后仍影响 V-201 对应名单
    ("RI-202", "V-201", "RACK-Y", "roof", "empty", "2026-11-01", "2026-12-31", "pending"),
    # 冲突：V-301 同时有两条生效登记
    ("RI-301", "V-301", "RACK-Z", "rear-door", "empty", "2026-10-01", "2026-12-31", "confirmed"),
    ("RI-302", "V-301", "RACK-Z", "rear-door", "loaded", "2026-11-10", "2026-12-31", "confirmed"),
    ("RI-303", "V-302", "RACK-Z", "rear-door", "empty", "2026-10-01", "2026-12-31", "revised"),
]

# (trip, vehicle, version, time, visibility, upload, intervention)
EVENTS = [
    # group A：4 次 V15，clear+partial、empty+loaded，均确认检出
    ("T-101", "V-101", "V15", "2026-10-05T08:00Z", "clear", "complete", ""),
    ("T-102", "V-102", "V15", "2026-10-06T08:00Z", "partial", "complete", ""),
    ("T-103", "V-103", "V15", "2026-10-07T08:00Z", "clear", "late", ""),
    ("T-104", "V-101", "V15", "2026-10-08T08:00Z", "partial", "complete", ""),
    # group A 的 V14 历史行程，不应进入 V15 评估
    ("T-199", "V-101", "V14", "2026-09-01T08:00Z", "clear", "complete", ""),
    # group B：只有 partial + loaded，缺 clear/empty
    ("T-201", "V-201", "V15", "2026-10-05T09:00Z", "partial", "complete", ""),
    ("T-202", "V-201", "V15", "2026-10-06T09:00Z", "occluded", "missing", ""),
    # group C：一次真实接管，一次确认未识别
    ("T-301", "V-302", "V15", "2026-10-05T10:00Z", "clear", "complete", "E-04"),
    ("T-302", "V-302", "V15", "2026-10-06T10:00Z", "partial", "complete", ""),
    # 无车架对照行程
    ("T-401", "V-401", "V15", "2026-10-05T11:00Z", "clear", "complete", ""),
    # 未观测：有车架、行程完整但无任何 rear 观测
    ("T-105", "V-102", "V15", "2026-10-09T08:00Z", "clear", "complete", ""),
]

# (obs, trip, target, camera, review, detected, batch)
OBS = [
    ("O-101", "T-101", "vehicle", "rear", "confirmed", "yes", "RB-1"),
    ("O-102", "T-102", "pedestrian", "rear", "confirmed", "yes", "RB-1"),
    ("O-103", "T-103", "vehicle", "rear", "confirmed", "yes", "RB-2"),
    ("O-104", "T-104", "object", "rear", "confirmed", "yes", "RB-2"),
    ("O-199", "T-199", "vehicle", "rear", "confirmed", "no", "RB-0"),
    ("O-201", "T-201", "vehicle", "rear", "uncertain", "yes", "RB-1"),
    # T-301：接管 + 确认检出（接管本身仍是硬红线）
    ("O-301", "T-301", "vehicle", "rear", "confirmed", "yes", "RB-1"),
    # T-302：复核确认未识别
    ("O-302", "T-302", "vehicle", "rear", "confirmed", "no", "RB-1"),
    # 侧摄记录，不参与后摄判定
    ("O-105", "T-101", "pedestrian", "side", "confirmed", "yes", "RB-1"),
]

SCOPES = [
    ("TS-101", "M-X", "H3", "RACK-X", "R-E", "proposed", 3, "CFG-1"),
    ("TS-201", "M-Y", "H4", "RACK-Y", "R-W", "proposed", 2, "CFG-1"),
    ("TS-301", "M-Z", "H5", "RACK-Z", "R-N", "accepted", 2, "CFG-1"),
]


def _write(path: Path, header: list[str], rows: list[tuple]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(HEADER)
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)


def generate(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = [p.name for p in out_dir.iterdir()]
    contract_files = {"vehicles.csv", "rack_installations.csv", "drive_events.csv",
                      "rear_observations.csv", "trial_scopes.csv"}
    overlap = contract_files & set(existing)
    if overlap:
        raise SystemExit(f"目标目录非空且含契约文件，拒绝覆盖: {sorted(overlap)}")

    _write(out_dir / "vehicles.csv",
           ["vehicle_id", "model", "hardware", "region", "camera_config"], VEHICLES)
    _write(out_dir / "rack_installations.csv",
           ["installation_id", "vehicle_id", "rack_model", "mount_position",
            "load_state", "valid_from", "valid_to", "review_state"], RACKS)
    _write(out_dir / "drive_events.csv",
           ["trip_id", "vehicle_id", "version", "started_at", "rear_visibility",
            "upload_state", "intervention_id"], EVENTS)
    _write(out_dir / "rear_observations.csv",
           ["observation_id", "trip_id", "target_class", "camera", "review_state",
            "detected", "review_batch"], OBS)
    _write(out_dir / "trial_scopes.csv",
           ["scope_id", "model", "hardware", "rack_model", "region", "status",
            "vehicle_count", "source_version"], SCOPES)
    print(f"已生成合成资料到 {out_dir}（固定场景，无随机种子依赖）")


def main() -> None:
    parser = argparse.ArgumentParser(description="生成本地测试用合成资料")
    parser.add_argument("--out", required=True, help="输出目录（须为空或不含契约文件）")
    args = parser.parse_args()
    generate(Path(args.out))


if __name__ == "__main__":
    main()

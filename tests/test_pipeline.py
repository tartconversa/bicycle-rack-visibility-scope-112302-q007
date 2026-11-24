"""端到端测试：持续导入 → 评估 → 名单核验 → 更正/复核影响分析。"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from bicycle_rack_visibility_scope.cli import main
from bicycle_rack_visibility_scope.domain import RackReview
from bicycle_rack_visibility_scope.evaluation import (
    Policy,
    run_evaluation,
    reconcile_scopes,
)
from bicycle_rack_visibility_scope.governance import (
    coverage_gaps,
    impact_of_rack_change,
    impact_of_review_batch,
    trace_scope,
)
from bicycle_rack_visibility_scope.store import Store
from bicycle_rack_visibility_scope.timeline import RackTimeline, build_trip_evidence
from tools.generate_data import generate


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.data_dir = self.tmp / "fixture"
        self.store_dir = self.tmp / "store"
        generate(self.data_dir)
        self.store = Store(self.store_dir)
        self.store.import_bundle(self.data_dir)
        self.policy = Policy()  # 默认门槛 min_trips=3, min_positive=2

    def tearDown(self):
        self._tmp.cleanup()

    def _evaluations(self):
        return run_evaluation(self.store, self.policy)

    def test_import_is_idempotent_and_batched(self):
        first = len(list(self.store_dir.glob("*.jsonl")))
        self.assertGreaterEqual(first, 5)
        self.store.import_bundle(self.data_dir)  # 再导一次
        self.assertEqual(len(self.store.vehicles), 8)
        self.assertEqual(len(self.store.events), 11)
        self.assertEqual(len(self.store.batches), 2)
        # 重新打开仓库，数据持久化且不重复
        reopened = Store(self.store_dir)
        self.assertEqual(len(reopened.events), 11)
        self.assertEqual(len(reopened.batches), 2)

    def test_group_a_eligible_group_b_needs_samples_group_c_blocked(self):
        evals = {e.key: e for e in self._evaluations()}
        eligible = [e for e in evals.values() if e.decision == "eligible_trial"]
        self.assertTrue(any(e.model == "M-X" for e in eligible))
        mx = next(e for e in eligible if e.model == "M-X")
        self.assertGreaterEqual(
            mx.evidence_counts["confirmed_recognition"], self.policy.min_positive)
        self.assertNotIn("occluded", mx.missing_visibility)

        my = next(e for e in evals.values() if e.model == "M-Y")
        self.assertEqual(my.decision, "needs_samples")
        self.assertIn("clear", my.missing_visibility)
        self.assertIn("empty", my.missing_loads)

        mz = next(e for e in evals.values() if e.model == "M-Z")
        self.assertEqual(mz.decision, "blocked")
        self.assertTrue(mz.hard_blocks)
        self.assertIn("T-301", mz.intervention_trips)
        self.assertIn("T-302", mz.negative_trips)

    def test_v14_trips_excluded_from_v15_evidence(self):
        evals = self._evaluations()
        for ev in evals:
            self.assertNotIn("T-199", ev.trip_ids)

    def test_unobserved_trip_not_counted_as_success(self):
        mx = next(e for e in self._evaluations() if e.model == "M-X")
        # T-105 无后摄观测，应计入 unobserved，而非 confirmed
        self.assertIn("T-105", mx.trip_ids)
        self.assertGreaterEqual(mx.evidence_counts["unobserved"], 1)
        self.assertNotIn("T-105", mx.positive_trips)

    def test_vehicle_without_rack_has_no_evaluation_unit(self):
        evals = self._evaluations()
        self.assertFalse(any(e.model == "M-Q" for e in evals))

    def test_reconcile_downgrades_accepted_scope_with_bad_evidence(self):
        results = {r["scope_id"]: r for r in
                   reconcile_scopes(self._evaluations(), self.store.scopes)}
        self.assertEqual(results["TS-101"]["recommended_status"], "partial")
        self.assertEqual(results["TS-201"]["recommended_status"], "proposed")
        # TS-301 当前 accepted，但证据被否决，必须建议 hold
        self.assertEqual(results["TS-301"]["recommended_status"], "hold")

    def test_trace_scope_lists_trip_level_conditions(self):
        scope = self.store.scopes["TS-101"]
        timeline = RackTimeline(list(self.store.racks.values()))
        evidence = build_trip_evidence(
            list(self.store.events.values()),
            list(self.store.observations.values()), timeline)
        lineage = trace_scope(scope, self._evaluations(), evidence,
                              self.store.vehicles)
        traced_trips = {t["trip_id"] for t in lineage.trips}
        self.assertIn("T-101", traced_trips)
        self.assertNotIn("T-199", traced_trips)
        sample = next(t for t in lineage.trips if t["trip_id"] == "T-101")
        self.assertEqual(sample["evidence_class"], "confirmed_recognition")
        self.assertEqual(sample["rack_state"], "active")

    def test_coverage_gaps_report(self):
        gaps = coverage_gaps(self._evaluations())
        models = {g["key"][0] for g in gaps}
        self.assertIn("M-Y", models)
        self.assertNotIn("M-X", models)

    def test_rack_correction_rechecks_only_pending_scopes(self):
        old = self.store.racks["RI-202"]
        corrected = type(old)(
            installation_id=old.installation_id,
            vehicle_id=old.vehicle_id,
            rack_model=old.rack_model,
            mount_position=old.mount_position,
            load_state=old.load_state,
            valid_from=old.valid_from,
            valid_to=old.valid_to,
            review_state=RackReview.CONFIRMED,  # pending → confirmed
        )
        self.store.racks["RI-202"] = corrected
        self.store.flush()
        payload = impact_of_rack_change(old, self.store, self._evaluations())
        recheck_ids = {s["scope_id"] for s in payload["pending_scopes_to_recheck"]}
        # RI-202 挂在 V-201（M-Y/RACK-Y/R-W），对应 TS-201（proposed）
        self.assertIn("TS-201", recheck_ids)
        # 已接受的 TS-301 不进入自动重核名单
        self.assertNotIn("TS-301", recheck_ids)
        self.assertIn("V-201", payload["affected_vehicle_ids"])

    def test_review_batch_impact_flags_non_positive(self):
        payload = impact_of_review_batch("RB-1", self.store, self._evaluations())
        self.assertIn("T-302", payload["trips_with_non_positive_evidence"])
        scopes = {s["scope_id"] for s in payload["pending_scopes_to_recheck"]}
        # 已接受（accepted）的 TS-301 不进入自动重核名单
        self.assertNotIn("TS-301", scopes)
        self.assertIn("TS-201", scopes)

    def test_cli_evaluate_and_reconcile(self):
        report_dir = self.tmp / "reports"
        rc = main(["evaluate", "--store", str(self.store_dir),
                   "--report-out", str(report_dir)])
        self.assertEqual(rc, 0)
        data = json.loads((report_dir / "evaluation.json").read_text(encoding="utf-8"))
        self.assertTrue(data)
        self.assertTrue((report_dir / "evaluation.csv").exists())
        rc = main(["reconcile", "--store", str(self.store_dir)])
        self.assertEqual(rc, 0)
        rc = main(["gaps", "--store", str(self.store_dir)])
        self.assertEqual(rc, 0)
        rc = main(["trace", "--store", str(self.store_dir),
                   "--scope-id", "TS-101"])
        self.assertEqual(rc, 0)

    def test_cli_single_file_import(self):
        before = len(self.store.batches)
        rc = main(["import", "--store", str(self.store_dir),
                   "--table", "vehicles",
                   "--file", str(self.data_dir / "vehicles.csv")])
        self.assertEqual(rc, 0)
        # CLI 写入磁盘后重新打开仓库验证批次已持久化
        reopened = Store(self.store_dir)
        self.assertEqual(len(reopened.batches), before + 1)

    def test_cli_rejects_bad_csv_with_clean_error(self):
        bad = self.tmp / "bad_vehicles.csv"
        bad.write_text(
            "﻿vehicle_id,model,hardware,region,camera_config\n"
            "V-X,,H1,R-S,CAM-1\n", encoding="utf-8")
        rc = main(["import", "--store", str(self.tmp / "empty-store"),
                   "--table", "vehicles", "--file", str(bad)])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()

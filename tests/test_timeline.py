"""单元测试：领域时间线与证据分级。"""
from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from bicycle_rack_visibility_scope.domain import (
    DriveEvent,
    EvidenceClass,
    RackInstallation,
    RackReview,
    RackState,
    RearObservation,
    ObservationReview,
    UploadState,
    Visibility,
)
from bicycle_rack_visibility_scope.timeline import RackTimeline, classify_trip


def _rack(rid="RI-1", vehicle="V-1", review=RackReview.CONFIRMED,
          vf=date(2026, 10, 1), vt=date(2026, 12, 31), **kw):
    return RackInstallation(
        installation_id=rid, vehicle_id=vehicle,
        rack_model=kw.get("rack_model", "RACK-X"),
        mount_position=kw.get("mount_position", "hitch"),
        load_state=kw.get("load_state", "empty"),
        valid_from=vf, valid_to=vt, review_state=review)


def _event(tid="T-1", vehicle="V-1", version="V15",
           vis=Visibility.CLEAR, upload=UploadState.COMPLETE,
           intervention="", when=datetime(2026, 10, 10, tzinfo=timezone.utc)):
    return DriveEvent(tid, vehicle, version, when, vis, upload, intervention)


def _obs(oid="O-1", trip="T-1", camera="rear",
         review=ObservationReview.CONFIRMED, detected=True):
    return RearObservation(oid, trip, "vehicle", camera, review, detected, "RB-1")


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.timeline = RackTimeline([
            _rack("RI-1", "V-1"),
            _rack("RI-2", "V-2", review=RackReview.PENDING),
            _rack("RI-3", "V-3", review=RackReview.REVISED),
            # V-4 两条重叠登记（冲突）
            _rack("RI-4", "V-4", vf=date(2026, 10, 1), vt=date(2026, 12, 31)),
            _rack("RI-5", "V-4", vf=date(2026, 11, 1), vt=date(2026, 12, 31),
                  rack_model="RACK-Y"),
            # V-5 区间外登记
            _rack("RI-6", "V-5", vf=date(2026, 9, 1), vt=date(2026, 9, 30)),
        ])

    def test_active_confirmed(self):
        snap = self.timeline.resolve("V-1", date(2026, 10, 10))
        self.assertIs(snap.state, RackState.ACTIVE)
        self.assertEqual(snap.installation.installation_id, "RI-1")

    def test_pending_and_revised(self):
        self.assertIs(self.timeline.resolve("V-2", date(2026, 10, 10)).state,
                      RackState.PENDING)
        snap = self.timeline.resolve("V-3", date(2026, 10, 10))
        self.assertIs(snap.state, RackState.CONFLICTED)
        self.assertIn("更正", snap.reasons[0])

    def test_overlapping_installations_conflict(self):
        snap = self.timeline.resolve("V-4", date(2026, 11, 15))
        self.assertIs(snap.state, RackState.CONFLICTED)
        self.assertEqual(len(snap.candidates), 2)

    def test_out_of_range_is_absent(self):
        self.assertIs(self.timeline.resolve("V-5", date(2026, 10, 10)).state,
                      RackState.ABSENT)
        self.assertIs(self.timeline.resolve("V-999", date(2026, 10, 10)).state,
                      RackState.ABSENT)

    def test_interval_boundaries_inclusive(self):
        # 闭区间：起止当天都算生效
        self.assertIs(self.timeline.resolve("V-1", date(2026, 10, 1)).state,
                      RackState.ACTIVE)
        self.assertIs(self.timeline.resolve("V-1", date(2026, 12, 31)).state,
                      RackState.ACTIVE)


class EvidenceClassificationTests(unittest.TestCase):
    def _snap(self, review=RackReview.CONFIRMED):
        timeline = RackTimeline([_rack(review=review)])
        return timeline.resolve("V-1", date(2026, 10, 10))

    def test_confirmed_detection_is_positive(self):
        te = classify_trip(_event(), self._snap(), [_obs(detected=True)])
        self.assertIs(te.evidence, EvidenceClass.CONFIRMED_RECOGNITION)
        self.assertTrue(te.is_positive)

    def test_confirmed_no_detection_is_not_recognized(self):
        te = classify_trip(_event(), self._snap(), [_obs(detected=False)])
        self.assertIs(te.evidence, EvidenceClass.NOT_RECOGNIZED)
        self.assertTrue(te.is_negative)

    def test_uncertain_review_separated(self):
        te = classify_trip(_event(), self._snap(),
                           [_obs(review=ObservationReview.UNCERTAIN)])
        self.assertIs(te.evidence, EvidenceClass.REVIEW_UNCERTAIN)

    def test_revised_observation_invalidates(self):
        te = classify_trip(_event(), self._snap(),
                           [_obs(review=ObservationReview.REVISED, detected=True)])
        self.assertIs(te.evidence, EvidenceClass.REVIEW_REVISED)
        self.assertTrue(te.is_negative)

    def test_missing_observation_is_unobserved_not_success(self):
        te = classify_trip(_event(), self._snap(), [])
        self.assertIs(te.evidence, EvidenceClass.UNOBSERVED)
        self.assertFalse(te.is_positive)

    def test_side_camera_only_is_still_unobserved(self):
        te = classify_trip(_event(), self._snap(),
                           [_obs(camera="side", detected=True)])
        self.assertIs(te.evidence, EvidenceClass.UNOBSERVED)

    def test_missing_upload_not_evaluable(self):
        te = classify_trip(_event(upload=UploadState.MISSING), self._snap(), [])
        self.assertIs(te.evidence, EvidenceClass.NOT_EVALUABLE)

    def test_occluded_not_evaluable(self):
        te = classify_trip(
            _event(vis=Visibility.OCCLUDED), self._snap(),
            [_obs(detected=True)])
        self.assertIs(te.evidence, EvidenceClass.NOT_EVALUABLE)

    def test_real_intervention_flag(self):
        self.assertTrue(_event(intervention="E-04").has_real_intervention)
        self.assertFalse(_event(intervention="E-00").has_real_intervention)
        self.assertFalse(_event(intervention="").has_real_intervention)

    def test_mixed_confirmed_and_uncertain_uses_confirmed(self):
        te = classify_trip(_event(), self._snap(), [
            _obs("O-1", review=ObservationReview.UNCERTAIN, detected=True),
            _obs("O-2", review=ObservationReview.CONFIRMED, detected=True),
        ])
        self.assertIs(te.evidence, EvidenceClass.CONFIRMED_RECOGNITION)


if __name__ == "__main__":
    unittest.main()

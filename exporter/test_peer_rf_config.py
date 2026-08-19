"""Peer-RF config enroll / backfill isolation (Bugbot: cache, re-enroll, concurrent write)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

import bridge_peer_rf as prf
from bridge_peer_rf import PeerRfTargetConfig


def _isolate_config(tmp: str) -> None:
    prf.PEER_RF_CONFIG_PATH = os.path.join(tmp, "peer_rf_config.json")
    prf._PEER_RF_CACHE = None


def test_re_enroll_keeps_custom_targets():
    with tempfile.TemporaryDirectory() as tmp:
        _isolate_config(tmp)
        custom = [{"target": "Module2_Current_A", "peer_features": ["Module1_Current_A"]}]
        first = prf.enroll_peer_rf_machine("2103-176030", targets=custom)
        assert first["alreadyEnrolled"] is False
        second = prf.enroll_peer_rf_machine("2103-176030", targets=None)
        assert second["alreadyEnrolled"] is True
        assert second["targets"] == [
            {"target": "Module2_Current_A", "peerFeatures": ["Module1_Current_A"]}
        ]
        raw = json.loads(Path(prf.PEER_RF_CONFIG_PATH).read_text())
        assert raw["machines"]["2103-176030"]["targets"] == custom


def test_concurrent_enrolls_keep_both_machines():
    with tempfile.TemporaryDirectory() as tmp:
        _isolate_config(tmp)
        errors: list[BaseException] = []

        def enroll(mid: str) -> None:
            try:
                prf.enroll_peer_rf_machine(mid)
            except BaseException as e:
                errors.append(e)

        t1 = threading.Thread(target=enroll, args=("m-a",))
        t2 = threading.Thread(target=enroll, args=("m-b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert errors == []
        raw = json.loads(Path(prf.PEER_RF_CONFIG_PATH).read_text())
        assert set(raw["machines"]) == {"m-a", "m-b"}


def test_backfill_one_machine_does_not_shrink_live_cache():
    a = PeerRfTargetConfig("m1", "Module1_Current_A", ["Module2_Current_A"])
    b = PeerRfTargetConfig("m2", "Module1_Current_A", ["Module2_Current_A"])
    prf._PEER_RF_CACHE = [a, b]
    seen: list[list[str]] = []

    def fake_historical(machine_id=None):
        seen.append([e.machine_id for e in prf.load_peer_rf_targets()])
        assert machine_id == "m1"

    orig = prf.backfill_peer_rf_historical_data
    prf.backfill_peer_rf_historical_data = fake_historical  # type: ignore[method-assign]
    try:
        result = prf.backfill_peer_rf_machine("m1")
        assert result["ok"] is True
        assert seen == [["m1", "m2"]]
        assert [e.machine_id for e in prf.load_peer_rf_targets()] == ["m1", "m2"]
    finally:
        prf.backfill_peer_rf_historical_data = orig  # type: ignore[method-assign]
        prf._PEER_RF_CACHE = None


def test_select_backfill_targets_filters_without_touching_cache():
    a = PeerRfTargetConfig("m1", "Module1_Current_A", ["Module2_Current_A"])
    b = PeerRfTargetConfig("m2", "Module1_Current_A", ["Module2_Current_A"])
    prf._PEER_RF_CACHE = [a, b]
    try:
        selected = prf.select_peer_rf_backfill_targets("m2")
        assert [e.machine_id for e in selected] == ["m2"]
        assert [e.machine_id for e in prf.load_peer_rf_targets()] == ["m1", "m2"]
    finally:
        prf._PEER_RF_CACHE = None

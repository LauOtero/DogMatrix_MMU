"""Pruebas de las estrategias de selector servo/indexed/multi-gear (F-30)."""

from __future__ import annotations

from types import SimpleNamespace

from dog_matrix.selector import (
    IndexedSelector,
    MultiGearSelector,
    Selector,
    ServoSelector,
)


def test_servo_selector_angle_and_callback():
    angles = []
    strategy = ServoSelector(None, {"servo_pitch_deg": 45.0}, gates=4, servo=angles.append)
    assert strategy.select_gate(2)
    assert strategy.get_position() == 90.0
    assert angles == [90.0]
    assert not strategy.select_gate(9)


def test_servo_selector_angle_table():
    strategy = ServoSelector(
        None, {"servo_angles": {0: 10.0, 1: 170.0}}, gates=2
    )
    strategy.select_gate(1)
    assert strategy.get_position() == 170.0


def test_indexed_selector_offsets():
    strategy = IndexedSelector(None, {"index_offsets": [0.0, 5.0, 12.5]}, gates=4)
    strategy.select_gate(2)
    assert strategy.get_position() == 12.5
    strategy.select_gate(3)  # sin offset -> index_pitch
    assert strategy.get_position() == 3.0


def test_multigear_selector():
    strategy = MultiGearSelector(None, {"gears_per_gate": 2}, gates=6)
    assert strategy.select_gate(4)
    assert strategy.is_at_gate(4)
    assert strategy.gears_per_gate == 2


def test_selector_factory_picks_servo_from_profile():
    profile = SimpleNamespace(
        topology_type="selector", selector_type="servo", gates=4, topology={"servo_pitch_deg": 30}
    )
    selector = Selector(None, {}, motion=None, profile=profile)
    assert isinstance(selector._strategy, ServoSelector)
    assert selector.select_gate(1)
    assert selector.get_position() == 30.0


def test_selector_factory_picks_indexed_and_multigear():
    indexed = SimpleNamespace(topology_type="selector", selector_type="indexed", gates=3, topology={})
    assert isinstance(Selector(None, {}, motion=None, profile=indexed)._strategy, IndexedSelector)
    multigear = SimpleNamespace(topology_type="selector", selector_type="multi_gear", gates=3, topology={})
    assert isinstance(Selector(None, {}, motion=None, profile=multigear)._strategy, MultiGearSelector)

# Copyright (c) 2026, Renaud Allard <renaud@allard.it>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""Unit tests for the year-to-date fold.

The rule that decides what the YTD sensors publish lives in one pure
function, so every branch is exercised here directly rather than through
a Home Assistant tick. Each test names the physical situation it stands
for; the coordinator tests then check that the two callers hand the fold
the right evidence.
"""

from __future__ import annotations

from collections.abc import Callable

from custom_components.be_water_prices.coordinator import _fold, _YtdCycle, _YtdFold

_METER = "sensor.water_meter"
_OTHER = "sensor.other_meter"
_YEAR = 2026


def _bill(m3: float) -> float | None:
    """A stand-in tariff: a fee plus a linear rate, so costs are checkable."""
    return round(40.0 + 2.0 * m3, 2)


def _round(
    cycle: _YtdCycle,
    *,
    reading: float | None = None,
    recorder_m3: float | None = None,
    recorder_ok: bool | None = True,
    hold_m3: float | None = None,
    hold_run: int = 0,
    now_year: int = _YEAR,
    meter: str = _METER,
    cost_of: Callable[[float], float | None] = _bill,
) -> _YtdFold:
    return _fold(
        cycle,
        now_year=now_year,
        meter=meter,
        reading=reading,
        recorder_m3=recorder_m3,
        recorder_ok=recorder_ok,
        hold_m3=hold_m3,
        hold_run=hold_run,
        cost_of=cost_of,
    )


def _anchored(m3: float, offset_m3: float, cost: float | None = None) -> _YtdCycle:
    """A cycle tracking the live meter: a figure and the frame behind it."""
    return _YtdCycle(meter=_METER, year=_YEAR, m3=m3, cost=cost, offset_m3=offset_m3)


def _served(m3: float, cost: float | None = None) -> _YtdCycle:
    """A cycle fed by the recorder alone: a figure, but no frame."""
    return _YtdCycle(meter=_METER, year=_YEAR, m3=m3, cost=cost)


def test_a_first_reading_is_framed_by_the_recorder_figure() -> None:
    """The year's consumption places the meter's Jan 1 reading."""
    out = _round(_YtdCycle(), reading=100.0, recorder_m3=20.0)

    assert out.m3 == 20.0
    assert out.cost == _bill(20.0)
    assert out.cycle == _anchored(20.0, 80.0, cost=_bill(20.0))


def test_an_empty_recorder_year_starts_the_cycle_at_zero() -> None:
    """A year the recorder says is genuinely empty begins at this reading."""
    out = _round(_YtdCycle(), reading=100.0, recorder_m3=0.0)

    assert out.m3 == 0.0
    assert out.cycle.offset_m3 == 100.0


def test_nothing_at_all_publishes_nothing() -> None:
    """No frame, no figure, no recorder answer: the sensors stay unknown."""
    out = _round(_YtdCycle())

    assert out.m3 is None
    assert out.cost is None
    assert out.cycle == _YtdCycle(meter=_METER)


def test_a_framed_reading_extends_the_year() -> None:
    out = _round(_anchored(20.0, 80.0), reading=105.0)

    assert out.m3 == 25.0
    assert out.cycle == _anchored(25.0, 80.0, cost=_bill(25.0))


def test_a_reading_below_the_mark_republishes_the_mark() -> None:
    """A glitch down, or a down-rounded cumulative reading."""
    out = _round(_anchored(25.0, 80.0), reading=104.0)

    assert out.m3 == 25.0
    assert out.cycle.offset_m3 == 80.0


def test_an_implausible_jump_is_held_for_one_reading() -> None:
    """A garbage spike must not become the year's mark."""
    out = _round(_anchored(25.0, 80.0), reading=400.0)

    assert out.m3 == 25.0
    assert out.hold_m3 == 400.0
    assert out.cycle.m3 == 25.0


def test_a_repeated_jump_is_a_genuine_catch_up() -> None:
    """A meter really sitting there reports the same figure again."""
    out = _round(_anchored(25.0, 80.0), reading=401.0, hold_m3=400.0)

    assert out.m3 == 321.0
    assert out.hold_m3 is None


def test_a_normal_reading_releases_a_held_jump() -> None:
    out = _round(_anchored(25.0, 80.0), reading=106.0, hold_m3=400.0)

    assert out.m3 == 26.0
    assert out.hold_m3 is None


def test_a_single_reading_below_the_frame_is_held() -> None:
    """A rebooting meter reporting 0 is a glitch, not a swap."""
    out = _round(_anchored(25.0, 80.0), reading=50.0)

    assert out.m3 == 25.0
    assert out.hold_run == 1
    assert out.cycle.offset_m3 == 80.0


def test_a_reading_back_above_the_frame_clears_the_swap_run() -> None:
    out = _round(_anchored(25.0, 80.0), reading=106.0, hold_run=2)

    assert out.m3 == 26.0
    assert out.hold_run == 0


def test_a_sustained_run_below_the_frame_is_a_meter_swap() -> None:
    """The new meter climbs from ~0, so the year restarts on it."""
    out = _round(_anchored(25.0, 80.0, cost=500.0), reading=12.0, hold_run=2)

    assert out.m3 == 0.0
    # The cost floor restarts with the cycle, or the new meter's first
    # month would be billed at the old meter's peak.
    assert out.cost == _bill(0.0)
    assert out.cycle == _anchored(0.0, 12.0, cost=_bill(0.0))
    assert out.hold_run == 0


def test_a_swap_ignores_a_recorder_total_spanning_the_old_meter() -> None:
    out = _round(_anchored(25.0, 80.0), reading=12.0, hold_run=2, recorder_m3=30.0)

    assert out.m3 == 0.0
    assert out.cycle.offset_m3 == 12.0


def test_a_recorder_figure_above_the_mark_is_published() -> None:
    """The meter is down and the recorder knows more than the mark does."""
    out = _round(_anchored(5.0, 100.0), recorder_m3=30.0)

    assert out.m3 == 30.0
    assert out.cycle.m3 == 30.0
    assert out.cycle.offset_m3 == 100.0


def test_a_recorder_figure_below_the_mark_does_not_walk_it_back() -> None:
    """The recorder's daily total trails the live meter after a dropout."""
    out = _round(_anchored(50.0, 80.0), recorder_m3=49.7)

    assert out.m3 == 50.0
    assert out.cycle.m3 == 50.0


def test_an_unreadable_recorder_keeps_serving_the_mark() -> None:
    out = _round(_anchored(50.0, 80.0), recorder_ok=False)

    assert out.m3 == 50.0


def test_a_reading_frames_the_year_from_a_recorder_served_figure() -> None:
    """The meter returns after a dropout; the year's 12 m3 must survive."""
    out = _round(_served(12.0), reading=4520.0)

    assert out.m3 == 12.0
    assert out.cycle.offset_m3 == 4508.0


def test_a_single_reading_below_a_served_figure_is_held() -> None:
    """A reading under the year's own consumption cannot date from January.

    With no frame the year's own figure is the bar, so such a reading gets
    the same treatment as one below a frame: held, and counted towards a
    swap rather than used to place the cycle start in the future.
    """
    out = _round(_served(70.0), reading=5.0)

    assert out.m3 == 70.0
    assert out.cycle.offset_m3 is None
    assert out.hold_run == 1


def test_a_sustained_run_below_a_served_figure_is_a_meter_swap() -> None:
    """A meter replaced while the year had no frame must not stall it.

    Nothing frames these readings, so without the swap run the year would
    sit at its served figure until the raw reading climbed past it, which
    for a counter that has just reset is the rest of the year.
    """
    cycle = _served(70.0, cost=_bill(70.0))
    hold_m3: float | None = None
    hold_run = 0
    published = []
    for reading in (5.0, 6.0, 7.0, 8.0):
        out = _round(cycle, reading=reading, hold_m3=hold_m3, hold_run=hold_run)
        cycle, hold_m3, hold_run = out.cycle, out.hold_m3, out.hold_run
        published.append(out.m3)

    assert published == [70.0, 70.0, 0.0, 1.0]
    assert cycle == _anchored(1.0, 7.0, cost=_bill(1.0))


def test_a_reading_back_above_a_served_figure_clears_the_run() -> None:
    out = _round(_served(70.0), reading=75.0, hold_run=2)

    assert out.m3 == 70.0
    assert out.cycle.offset_m3 == 5.0
    assert out.hold_run == 0


def test_a_rolled_over_cycle_starts_the_new_year_at_zero() -> None:
    last_year = _YtdCycle(meter=_METER, year=_YEAR - 1, m3=100.0, cost=999.0, offset_m3=4000.0)

    out = _round(last_year, reading=4105.0)

    assert out.m3 == 0.0
    # Last year's EUR 999 peak must not clamp the new year's opening bill.
    assert out.cost == _bill(0.0)
    assert out.cycle == _anchored(0.0, 4105.0, cost=_bill(0.0))


def test_a_rolled_over_cycle_defers_when_the_recorder_cannot_be_read() -> None:
    """A failed query is not an empty year, so the year is not restarted."""
    last_year = _YtdCycle(meter=_METER, year=_YEAR - 1, m3=0.0, offset_m3=4000.0)

    out = _round(last_year, reading=4520.0, recorder_m3=None, recorder_ok=False)

    assert out.m3 is None
    assert out.cost is None
    # The stamp is kept: it is what stops the next reading starting over.
    assert out.cycle == last_year


def test_a_rolled_over_cycle_publishes_the_new_year_recorder_figure() -> None:
    last_year = _YtdCycle(meter=_METER, year=_YEAR - 1, m3=100.0, cost=999.0, offset_m3=4000.0)

    out = _round(last_year, recorder_m3=2.0)

    assert out.m3 == 2.0
    assert out.cost == _bill(2.0)
    assert out.cycle == _served(2.0, cost=_bill(2.0))


def test_a_repointed_meter_does_not_inherit_the_old_frame() -> None:
    """A stray reading from the new meter must not anchor on the old cycle."""
    out = _round(_anchored(20.0, 80.0, cost=500.0), meter=_OTHER, reading=5000.0)

    assert out.m3 is None
    assert out.cycle == _YtdCycle(meter=_OTHER)


def test_a_repointed_meter_anchors_on_its_own_recorder_figure() -> None:
    out = _round(_anchored(20.0, 80.0), meter=_OTHER, reading=5000.0, recorder_m3=40.0)

    assert out.m3 == 40.0
    assert out.cycle.meter == _OTHER
    assert out.cycle.offset_m3 == 4960.0


def test_a_known_meter_starts_the_year_when_nothing_places_it() -> None:
    """Restarting in January with the recorder empty must not cost a day."""
    last_year = _YtdCycle(meter=_METER, year=_YEAR - 1, m3=100.0, offset_m3=4000.0)

    out = _round(last_year, reading=4105.0, recorder_ok=None)

    assert out.m3 == 0.0
    assert out.cycle.offset_m3 == 4105.0


def test_the_cost_floor_holds_when_the_tariff_drops() -> None:
    out = _round(_anchored(25.0, 80.0, cost=500.0), reading=105.0)

    assert out.m3 == 25.0
    assert out.cost == 500.0
    assert out.cycle.cost == 500.0


def test_a_region_with_no_bill_math_publishes_a_volume_and_no_cost() -> None:
    out = _round(_anchored(20.0, 80.0, cost=500.0), reading=105.0, cost_of=lambda _m3: None)

    assert out.m3 == 25.0
    assert out.cost is None
    assert out.cycle.cost == 500.0

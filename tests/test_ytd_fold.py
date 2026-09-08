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

import random
from collections.abc import Callable

from custom_components.be_water_prices.coordinator import (
    _fold,
    _migrate_cycle_to_v2,
    _YtdCycle,
    _YtdFold,
)

_METER = "sensor.water_meter"
_OTHER = "sensor.other_meter"
_YEAR = 2026


def _bill(m3: float) -> float | None:
    """A stand-in tariff: a fee plus a linear rate, so costs are checkable."""
    return round(40.0 + 2.0 * m3, 2)


# What the household fingerprints to. Rounds that mean to keep a floor
# pass the same one the cycle carries; a round where the household itself
# changed passes a different one.
_BASIS = "stand-in"


def _round(
    cycle: _YtdCycle,
    *,
    reading: float | None = None,
    recorder_m3: float | None = None,
    recorder_ok: bool | None = True,
    hold_m3: float | None = None,
    hold_run: int = 0,
    hold_span_s: float = 0.0,
    run_m3: float | None = None,
    elapsed_s: float = 86400.0,
    high_m3: float | None = None,
    now_year: int = _YEAR,
    meter: str = _METER,
    basis: str = _BASIS,
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
        hold_span_s=hold_span_s,
        run_m3=run_m3,
        elapsed_s=elapsed_s,
        high_m3=high_m3,
        basis=basis,
        cost_of=cost_of,
    )


def _anchored(m3: float, offset_m3: float, cost: float | None = None) -> _YtdCycle:
    """A cycle tracking the live meter: a figure and the frame behind it."""
    return _YtdCycle(meter=_METER, year=_YEAR, m3=m3, cost=cost, offset_m3=offset_m3, basis=_BASIS)


def _served(m3: float, cost: float | None = None) -> _YtdCycle:
    """A cycle fed by the recorder alone: a figure, but no frame."""
    return _YtdCycle(meter=_METER, year=_YEAR, m3=m3, cost=cost, basis=_BASIS)


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


def test_a_dip_after_a_restart_does_not_rebuild_the_frame() -> None:
    """A restart leaves no high-water mark, so a low reading proves nothing.

    Rebuilding the frame on it drops the frame beneath the meter and
    every later reading is over-reported for the rest of the year.
    """
    out = _round(_anchored(25.0, 80.0), reading=100.0, recorder_m3=25.0, high_m3=None)

    assert out.m3 == 25.0
    assert out.cycle.offset_m3 == 80.0


def test_a_dip_with_a_mark_behind_it_still_rebuilds_the_frame() -> None:
    """The correction is only deferred, not lost: one round later it fires."""
    out = _round(_anchored(25.0, 80.0), reading=100.0, recorder_m3=25.0, high_m3=100.0)

    assert out.m3 == 25.0
    assert out.cycle.offset_m3 == 75.0


def test_a_missing_reading_lapses_a_held_jump() -> None:
    """The hold only means anything against the reading right after it."""
    out = _round(_anchored(25.0, 80.0), reading=None, hold_m3=400.0)

    assert out.hold_m3 is None


def test_a_reading_under_the_bar_lapses_a_held_jump() -> None:
    """A dip says nothing about a spike held above it, so the hold goes.

    Left standing, the hold would wave through whatever spike arrived
    next, however much later and however unrelated.
    """
    out = _round(_anchored(25.0, 80.0), reading=3.0, hold_m3=400.0)

    assert out.hold_m3 is None
    assert out.hold_run == 1


def test_a_burst_of_low_readings_is_not_a_swap() -> None:
    """Three readings inside a second are a glitch, not a replacement.

    The live path folds on every state event, so a meter that drops out
    and reconnects can produce a whole confirmation run in no time at
    all. A real replacement keeps reading low for far longer.
    """
    out = _round(
        _anchored(25.0, 80.0), reading=3.0, hold_run=2, run_m3=3.0, hold_span_s=0.4, elapsed_s=0.3
    )

    assert out.cycle.offset_m3 == 80.0
    assert out.m3 == 25.0
    assert out.hold_run == 3


def test_a_run_that_lasts_is_still_a_swap() -> None:
    """Spread over hours, the same three readings do re-anchor the year."""
    out = _round(
        _anchored(25.0, 80.0),
        reading=3.0,
        hold_run=2,
        run_m3=3.0,
        hold_span_s=3600.0,
        elapsed_s=3600.0,
    )

    assert out.cycle.offset_m3 == 3.0
    assert out.m3 == 0.0


def test_readings_that_disagree_do_not_confirm_a_replacement() -> None:
    """A run is readings that stand together, not merely readings under the bar.

    A replaced register climbs from where the last reading left it. Two
    honest readings at the meter's position with one dropout between them
    used to count as three, and the frame was rebuilt on whichever came
    last: a dropout at the end put the frame far below the meter, and the
    year over-reported by the whole register from then on.
    """
    out = _round(_anchored(3.978, 1967.457), reading=3.009, hold_run=2, run_m3=1520.514)

    assert out.cycle.offset_m3 == 1967.457
    assert out.m3 == 3.978
    assert out.hold_run == 1
    assert out.run_m3 == 3.009


def test_a_new_run_is_anchored_on_its_own_first_reading() -> None:
    """A run that has ended leaves its value behind, and it must not anchor
    the next one. The reading that starts a run is what the run stands at,
    or a stale value from an earlier one decides which readings may join it.
    """
    out = _round(_anchored(25.0, 80.0), reading=30.0, hold_run=0, run_m3=60.0)

    assert out.hold_run == 1
    assert out.run_m3 == 30.0


def test_a_new_run_does_not_inherit_the_persistence_of_the_last() -> None:
    """The span says how long this run has lasted, so it starts again with it.

    Carried over, ten minutes a previous run had earned would be waiting
    for the next dropout, and two readings that have nothing to do with
    each other would re-anchor the year between them.
    """
    out = _round(_anchored(5.0, 1500.0), reading=3.0, hold_run=2, run_m3=1400.0, hold_span_s=3600.0)

    assert out.hold_run == 1
    assert out.hold_span_s == 0.0
    assert out.cycle.offset_m3 == 1500.0
    assert out.m3 == 5.0


def test_the_quiet_before_a_run_does_not_count_as_persistence() -> None:
    """An hour of silence, then three low readings inside a second: a glitch.

    The span measures how long the run has lasted, so the gap before its
    first reading is not evidence. Counting it made any burst after an
    ordinary reporting interval look like a replacement.
    """
    first = _round(_anchored(25.0, 80.0), reading=10.0, elapsed_s=3600.0)
    assert first.hold_run == 1
    assert first.hold_span_s == 0.0
    second = _round(
        first.cycle,
        reading=11.0,
        hold_run=first.hold_run,
        hold_span_s=first.hold_span_s,
        elapsed_s=0.2,
    )
    third = _round(
        second.cycle,
        reading=12.0,
        hold_run=second.hold_run,
        hold_span_s=second.hold_span_s,
        elapsed_s=0.2,
    )

    assert third.cycle.offset_m3 == 80.0
    assert third.m3 == 25.0


def test_a_confirmed_swap_takes_the_mark_with_it() -> None:
    """The old meter's high-water mark must not outlive the old meter.

    A new meter starts far below the one it replaced, so a mark left
    behind sits above every reading it will ever produce and the frame
    can never be corrected again this year.
    """
    out = _round(_anchored(25.0, 80.0), reading=3.0, hold_run=2, run_m3=3.0, high_m3=105.0)

    assert out.cycle.offset_m3 == 3.0
    assert out.high_m3 == 3.0


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


def test_a_reading_that_refutes_the_hold_faces_the_jump_test_itself() -> None:
    """A hold is not a licence for whatever arrives next.

    Releasing on any second reading meant one spike opened the door for
    the next, whatever it was, and that one went into the year's mark
    untested. A reading well below the held value refutes it rather
    than confirming it, so it has to stand on its own.
    """
    out = _round(_anchored(25.0, 80.0), reading=250.0, hold_m3=400.0)

    assert out.m3 == 25.0
    assert out.hold_m3 == 250.0


def test_a_reading_above_the_hold_still_confirms_it() -> None:
    """A register that has climbed past the held value is a real catch-up."""
    out = _round(_anchored(25.0, 80.0), reading=900.0, hold_m3=400.0)

    assert out.m3 == 820.0
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
    out = _round(_anchored(25.0, 80.0, cost=500.0), reading=12.0, hold_run=2, run_m3=12.0)

    assert out.m3 == 0.0
    # The cost floor restarts with the cycle, or the new meter's first
    # month would be billed at the old meter's peak.
    assert out.cost == _bill(0.0)
    assert out.cycle == _anchored(0.0, 12.0, cost=_bill(0.0))
    assert out.hold_run == 0


def test_a_swap_ignores_a_recorder_total_spanning_the_old_meter() -> None:
    out = _round(_anchored(25.0, 80.0), reading=12.0, hold_run=2, run_m3=12.0, recorder_m3=30.0)

    assert out.m3 == 0.0
    assert out.cycle.offset_m3 == 12.0


def test_a_run_below_a_frame_built_too_high_rebuilds_the_frame() -> None:
    """A spike can frame the year far above the meter, and every honest
    reading then falls under a bar it can never reach. The meter still shows
    more water than the year has used, so nothing has been replaced: the
    frame is what is wrong, and it is rebuilt under the reading rather than
    the year being started over.
    """
    out = _round(_anchored(5.0, 1500.0), reading=1400.0, hold_run=2, run_m3=1400.0)

    assert out.m3 == 5.0
    assert out.cycle.offset_m3 == 1395.0
    assert out.hold_run == 0
    assert out.high_m3 == 1400.0


def test_a_rebuilt_frame_measures_the_next_reading() -> None:
    """The point of rebuilding is that the live path starts working again."""
    out = _round(_anchored(5.0, 1500.0), reading=1400.0, hold_run=2, run_m3=1400.0)
    later = _round(out.cycle, reading=1407.0, high_m3=out.high_m3)

    assert later.m3 == 12.0


def test_a_rebuilt_frame_keeps_the_recorder_figure_and_the_cost_floor() -> None:
    """Nothing about the year itself changed, so neither may be discarded.

    A replacement drops both, because the year restarts on the new meter. A
    frame that was merely too high is a correction to the frame alone, and
    the bill has to stay where the year had already taken it.
    """
    out = _round(
        _anchored(5.0, 1500.0, cost=500.0),
        reading=1400.0,
        hold_run=2,
        run_m3=1400.0,
        recorder_m3=8.0,
    )

    assert out.m3 == 8.0
    assert out.cycle.offset_m3 == 1392.0
    assert out.cost == 500.0
    assert out.cycle.cost == 500.0


def test_the_years_consumption_tells_a_new_register_from_a_high_frame() -> None:
    """Both readings sit far below the same too-high frame, and only one of
    them could have measured the water the year has already used. That is
    what separates a replaced meter from a frame that needs correcting.
    """
    replaced = _round(_anchored(20.0, 1500.0), reading=19.0, hold_run=2, run_m3=19.0)
    corrected = _round(_anchored(20.0, 1500.0), reading=21.0, hold_run=2, run_m3=21.0)

    assert replaced.m3 == 0.0
    assert replaced.cycle.offset_m3 == 19.0
    assert corrected.m3 == 20.0
    assert corrected.cycle.offset_m3 == 1.0


def test_a_meter_reading_exactly_the_years_consumption_was_framed_at_zero() -> None:
    """The boundary belongs to the frame, not to a replacement.

    A meter showing precisely the water the year has used is one that has
    been measuring since January from an empty register. A meter replaced
    part way through the year cannot show that: the year would hold what
    the old one measured on top of it.
    """
    out = _round(_anchored(20.0, 1500.0), reading=20.0, hold_run=2, run_m3=20.0)

    assert out.m3 == 20.0
    assert out.cycle.offset_m3 == 0.0


def test_a_recorder_figure_above_the_mark_is_published() -> None:
    """The meter is down and the recorder knows more than the mark does.

    The figure is published, but the frame is left alone. A recorder figure
    above the year's own figure only says the figure is behind, which is
    the ordinary state whenever a reading was held or missed; it is not
    evidence that the frame is wrong. See the two tests below for what
    treating it as such costs.
    """
    out = _round(_anchored(5.0, 100.0), recorder_m3=30.0)

    assert out.m3 == 30.0
    assert out.cycle.m3 == 30.0
    assert out.cycle.offset_m3 == 100.0


def test_a_confirmed_jump_the_recorder_contradicts_rebuilds_the_frame() -> None:
    """Two readings agreeing place the meter, never the frame behind it.

    Once the frame has been re-anchored onto a reading the meter never
    really showed, the meter's return reads as a jump and the reading after
    it confirms the meter really is up there. What that cannot confirm is
    that the year used the difference, and the recorder says it did not, so
    the frame is rebuilt on what the year knows instead of billing it.
    """
    out = _round(_anchored(7.0, 3.0), reading=1523.0, recorder_m3=8.0, hold_m3=1520.0)

    assert out.m3 == 8.0
    assert out.cycle.offset_m3 == 1515.0


def test_a_confirmed_catch_up_the_recorder_backs_is_still_billed() -> None:
    """A meter that really did advance during an outage is not refused.

    The recorder saw the same catch-up, so the frame and the year agree and
    the jump is the water it says it is. Refusing this would be the cure
    doing more harm than the disease.
    """
    out = _round(_anchored(20.0, 80.0), reading=250.0, recorder_m3=170.0, hold_m3=249.0)

    assert out.m3 == 170.0
    assert out.cycle.offset_m3 == 80.0


def test_the_live_meter_running_ahead_of_the_recorder_keeps_the_frame() -> None:
    """The recorder's daily total trails the live reading by the day in
    progress. That gap is ordinary, not a frame sitting under the meter,
    and a rule without room for it would rebuild the frame every round.
    """
    out = _round(_anchored(20.0, 80.0), reading=125.0, recorder_m3=44.0, hold_m3=124.0)

    assert out.m3 == 45.0
    assert out.cycle.offset_m3 == 80.0


def test_a_lagging_recorder_does_not_unseat_a_frame_the_mark_agrees_with() -> None:
    """What the year knows is its own figure, not only the latest answer.

    A recorder that has fallen behind the mark it once produced must not
    pull the frame down to itself: the mark is evidence too, and it is the
    higher of the two.
    """
    out = _round(_anchored(150.0, 80.0), reading=225.0, recorder_m3=20.0, hold_m3=224.0)

    assert out.m3 == 150.0
    assert out.cycle.offset_m3 == 80.0


def test_a_confirmed_jump_stands_when_nothing_can_contradict_it() -> None:
    """A recorder answer is what makes the frame decidable, and this round
    has none, so the confirmed reading is taken as it has always been.
    """
    out = _round(_anchored(7.0, 3.0), reading=1523.0, hold_m3=1520.0)

    assert out.m3 == 1520.0
    assert out.cycle.offset_m3 == 3.0


def _replay(
    cycle: _YtdCycle, rounds: list[tuple[float | None, float | None]]
) -> list[float | None]:
    """Feed each fold's own result back in, as both callers do."""
    published: list[float | None] = []
    hold_m3: float | None = None
    hold_run = 0
    run_m3: float | None = None
    for reading, recorder_m3 in rounds:
        out = _round(
            cycle,
            reading=reading,
            recorder_m3=recorder_m3,
            hold_m3=hold_m3,
            hold_run=hold_run,
            run_m3=run_m3,
        )
        cycle, hold_m3, hold_run = out.cycle, out.hold_m3, out.hold_run
        run_m3 = out.run_m3
        published.append(out.m3)
    return published


def test_a_recorder_figure_a_hair_above_the_year_keeps_the_frame() -> None:
    """The recorder and the frame compute the same water two ways.

    The recorder sums a year of daily deltas while the frame subtracts two
    readings, so the two disagree in the last bits over identical water. If
    that counted as evidence against the frame, the frame would be dropped
    on a routine tick and rebuilt around the recorder's figure, throwing
    away everything drawn while the meter was down.
    """
    summed = 0.0
    for _ in range(220):
        summed += 0.4
    assert summed > 88.0  # 88.00000000000017

    # The meter read 4000 on Jan 1 and 4088 before it dropped out; 30 m³ is
    # drawn during the outage, so it comes back at 4118.
    assert _replay(
        _anchored(88.0, 4000.0, cost=300.0),
        [(None, summed), (4118.0, None), (4119.0, None)],
    ) == [summed, 118.0, 119.0]


def test_a_garbled_low_reading_cannot_reframe_the_year() -> None:
    """A reading too low to belong to the year must not anchor it either.

    A meter coming back from the outage that made the tick query is exactly
    the one likely to emit a truncated register or a dropped digit. Such a
    reading is held as a glitch, and it must not become the frame by any
    route: the figure is monotonic, so anchoring on it would pin both
    sensors for the rest of the year.
    """
    # Jan 1 the meter read 4000; it now reads 4045 but reports 404.
    assert _replay(
        _anchored(40.0, 4000.0, cost=120.0),
        [(404.0, 45.0), (4046.0, None), (4047.0, None), (4048.0, None)],
    ) == [45.0, 46.0, 47.0, 48.0]


def test_a_reading_frames_a_year_that_has_a_figure_but_no_frame() -> None:
    out = _round(_served(30.0), reading=106.0)

    assert out.m3 == 30.0
    assert out.cycle.offset_m3 == 76.0
    assert _round(out.cycle, reading=107.0).m3 == 31.0


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
    run_m3: float | None = None
    published = []
    for reading in (5.0, 6.0, 7.0, 8.0):
        out = _round(cycle, reading=reading, hold_m3=hold_m3, hold_run=hold_run, run_m3=run_m3)
        cycle, hold_m3, hold_run = out.cycle, out.hold_m3, out.hold_run
        run_m3 = out.run_m3
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


def test_the_live_cycle_migrates_to_its_figure_and_frame() -> None:
    old = {"meter": "m", "year": _YEAR, "baseline_m3": 4000.0, "live_hwm_m3": 4100.0}

    assert _migrate_cycle_to_v2(old) == {
        "meter": "m",
        "year": _YEAR,
        "m3": 100.0,
        "cost": None,
        "offset_m3": 4000.0,
    }


def test_a_cost_mark_written_before_its_year_key_takes_the_anchor_year() -> None:
    """The key being absent is a release that predates it."""
    old = {"meter": "m", "year": _YEAR - 1, "baseline_m3": 4000.0, "live_hwm_m3": 4100.0}

    got = _migrate_cycle_to_v2({**old, "cost_hwm": 999.0})

    assert got["year"] == _YEAR - 1
    assert got["cost"] == 999.0


def test_a_cost_mark_that_cannot_be_dated_is_dropped() -> None:
    """The key present and None is a release that had nothing to date.

    Such a mark could never be released, so carrying it would pin the bill
    at an old peak in this year and every year after it.
    """
    old = {"meter": "m", "year": None, "baseline_m3": None, "live_hwm_m3": None}

    assert _migrate_cycle_to_v2({**old, "cost_hwm": 999.0}) == {
        "meter": "m",
        "year": None,
        "m3": None,
        "cost": None,
        "offset_m3": None,
    }
    assert _migrate_cycle_to_v2({**old, "cost_hwm": 999.0, "cost_year": None})["cost"] is None


def test_a_dated_cost_mark_survives_a_cycle_that_never_anchored() -> None:
    old = {
        "meter": "m",
        "year": None,
        "baseline_m3": None,
        "live_hwm_m3": None,
        "cost_hwm": 177.25,
        "cost_year": _YEAR,
    }

    assert _migrate_cycle_to_v2(old) == {
        "meter": "m",
        "year": _YEAR,
        "m3": None,
        "cost": 177.25,
        "offset_m3": None,
    }


def test_two_old_stamps_that_disagree_resolve_to_the_newer_year() -> None:
    """A meter down across Jan 1 dated its two figures differently.

    The live anchor stayed on last year while the recorder-served figure and
    the cost mark moved to this one. One record holds one year, so the newer
    wins and the frame behind the older figure goes with it.
    """
    old = {
        "meter": "m",
        "year": _YEAR - 1,
        "baseline_m3": 4000.0,
        "live_hwm_m3": 4100.0,
        "cost_hwm": 50.0,
        "cost_year": _YEAR,
        "served_hwm_m3": 7.0,
    }

    assert _migrate_cycle_to_v2(old) == {
        "meter": "m",
        "year": _YEAR,
        "m3": 7.0,
        "cost": 50.0,
        "offset_m3": None,
    }


def test_migrating_a_record_that_is_already_current_changes_nothing() -> None:
    """A store an older release handed back still holds the new shape.

    Home Assistant re-stamps a store with its own minor version after any
    migration, so a rollback rewrites the record it could not read under the
    old label. Folding that as if it were the old shape empties it.
    """
    current = {"meter": "m", "year": _YEAR, "m3": 50.0, "cost": 210.0, "offset_m3": 4000.0}

    assert _migrate_cycle_to_v2(current) == current
    assert _migrate_cycle_to_v2(_migrate_cycle_to_v2(current)) == current


def test_a_migrated_record_loads_as_a_cycle() -> None:
    """The migration's keys are the ones async_load_ytd_state reads."""
    old = {"meter": "m", "year": _YEAR, "baseline_m3": 4000.0, "live_hwm_m3": 4100.0}

    assert _YtdCycle(**_migrate_cycle_to_v2(old)) == _YtdCycle(
        meter="m", year=_YEAR, m3=100.0, cost=None, offset_m3=4000.0
    )


def test_a_recorder_figure_alone_does_not_move_the_frame() -> None:
    """A figure without a reading says how much water, not where the meter is.

    Moving the frame on it would guess at a meter position, and the guess is
    wrong whenever the year's figure is merely behind: a reading held as a
    spike, or one never seen, leaves it there, and the correction would then
    count the same water twice.
    """
    out = _round(_anchored(50.0, 4000.0), recorder_m3=200.0, high_m3=4200.0)

    assert out.m3 == 200.0
    assert out.cycle.offset_m3 == 4000.0
    # The meter's own reading still produces the truth on the next round.
    assert _round(out.cycle, reading=4201.0, high_m3=4200.0).m3 == 201.0


def test_a_reading_and_a_figure_together_rebuild_the_frame() -> None:
    """The one pair that dates the year's consumption to a meter position.

    The frame was left at 100 while a recorder figure of 30 was published, so
    it produces 7 at a reading of 107 and the year would sit there until the
    meter reached 130. Read together, the two rebuild it as 107 - 30.
    """
    out = _round(_anchored(30.0, 100.0), reading=107.0, recorder_m3=30.0, high_m3=106.0)

    assert out.m3 == 30.0
    assert out.cycle.offset_m3 == 77.0
    assert _round(out.cycle, reading=108.0, high_m3=107.0).m3 == 31.0


def test_a_dip_does_not_rebuild_the_frame_even_alongside_a_figure() -> None:
    """A reading under the highest the meter has shown is a dip, not a move."""
    out = _round(_anchored(25.0, 80.0), reading=104.0, recorder_m3=25.0, high_m3=105.0)

    assert out.m3 == 25.0
    assert out.cycle.offset_m3 == 80.0


def test_a_rejected_reading_does_not_rebuild_the_frame() -> None:
    """A garbled register must not anchor the year by any route."""
    out = _round(_anchored(40.0, 4000.0), reading=404.0, recorder_m3=45.0, high_m3=4040.0)

    assert out.m3 == 45.0
    assert out.cycle.offset_m3 == 4000.0
    assert _round(out.cycle, reading=4046.0, high_m3=4040.0).m3 == 46.0


def _honest_run(seed: int, *, dropouts: bool, catchups: bool) -> str | None:
    """Replay one randomised sequence of an honest meter and recorder.

    Nothing lies: the meter only climbs and the recorder reports the year's
    true consumption. Only the timing varies, and the tick asks the recorder
    exactly when the coordinator's own gate would.
    """
    rnd = random.Random(seed)
    jan1 = rnd.choice([0.0, 100.0, 4000.0, 987654.0])
    true = jan1
    cycle = _YtdCycle(meter=_METER)
    hold_m3: float | None = None
    high_m3: float | None = None
    hold_run = 0
    recorder_ok = True
    last_published: float | None = None
    for step in range(rnd.randrange(6, 45)):
        big = catchups and rnd.random() < 0.08
        true = round(true + (rnd.uniform(100.0, 300.0) if big else rnd.uniform(0.0, 3.0)), 3)
        truth = round(true - jan1, 6)
        readable = True if not dropouts else (step == 0 or rnd.random() > 0.25)
        reading = true if readable else None
        framed = (
            reading - cycle.offset_m3
            if reading is not None and cycle.offset_m3 is not None and cycle.year == _YEAR
            else None
        )
        # The coordinator's own recorder-query gate, stale frame included.
        must_ask = (
            cycle.meter != _METER
            or cycle.year != _YEAR
            or cycle.offset_m3 is None
            or reading is None
            or (cycle.m3 is not None and framed is not None and cycle.m3 > framed)
        )
        asked = must_ask and (step == 0 or rnd.random() > 0.5)
        out = _round(
            cycle,
            reading=reading,
            recorder_m3=truth if asked else None,
            recorder_ok=recorder_ok,
            hold_m3=hold_m3,
            hold_run=hold_run,
            high_m3=high_m3,
        )
        cycle, hold_m3, hold_run, high_m3 = out.cycle, out.hold_m3, out.hold_run, out.high_m3
        if out.m3 is None:
            continue
        if last_published is not None and out.m3 < last_published - 1e-9:
            return f"decrease {last_published} -> {out.m3}"
        if out.m3 > truth + 1e-6:
            return f"over-reported {out.m3} against truth {truth}"
        if reading is not None and asked and abs(out.m3 - truth) > 1e-6:
            return f"a reading and a figure together gave {out.m3}, truth {truth}"
        last_published = out.m3
    return None


def test_the_figure_never_decreases_and_never_exceeds_the_truth() -> None:
    """The two bounds that matter, over randomised honest sequences.

    A decrease is read by the statistics engine as a cycle reset, and the
    figure is a high-water mark that also floors the bill, so anything it
    over-reports is there until January.
    """
    for dropouts, catchups in ((False, False), (True, False), (True, True)):
        failures = [
            (s, r)
            for s in range(1500)
            if (r := _honest_run(s, dropouts=dropouts, catchups=catchups))
        ]
        assert not failures, (dropouts, catchups, failures[:3])


def test_a_refuted_spike_does_not_raise_the_high_water_mark() -> None:
    """Only an admitted reading says where the register has been.

    A held spike raised the mark for the life of the process, and since
    the frame correction requires the next reading to reach that mark, no
    real reading could ever satisfy it again until a restart.
    """
    spike = _round(_anchored(25.0, 80.0), reading=999_999.0, high_m3=105.0)
    assert spike.hold_m3 == 999_999.0
    assert spike.high_m3 == 105.0

    # A normal reading afterwards is admitted and does raise it.
    normal = _round(spike.cycle, reading=106.0, hold_m3=spike.hold_m3, high_m3=spike.high_m3)
    assert normal.m3 == 26.0
    assert normal.high_m3 == 106.0


def test_a_floor_measured_for_other_options_does_not_clamp() -> None:
    """The social tariff granted in July published 437.56 where 87.51 was owed."""
    cycle = _anchored(60.0, 1000.0, cost=_bill(60.0))
    assert cycle.cost == 160.0
    cheaper = _round(
        cycle,
        reading=1060.0,
        basis="social-tariff-on",
        cost_of=lambda m3: round(0.20 * (40.0 + 2.0 * m3), 2),
    )
    assert cheaper.m3 == 60.0
    assert cheaper.cost == 32.0
    assert cheaper.cycle.basis == "social-tariff-on"
    assert cheaper.cycle.cost == 32.0


def test_the_floor_still_clamps_for_the_same_household() -> None:
    """A clock step, or a card that came back cheaper, must not publish a drop."""
    cycle = _anchored(60.0, 1000.0, cost=_bill(60.0))
    out = _round(cycle, reading=1060.0, cost_of=lambda m3: 10.0)
    assert out.cost == 160.0
    assert out.cycle.cost == 160.0


def test_a_record_written_before_the_basis_existed_rebuilds_its_floor_once() -> None:
    """An upgrade must not carry a floor it cannot account for."""
    old = _YtdCycle(meter=_METER, year=_YEAR, m3=60.0, cost=999.0, offset_m3=1000.0)
    assert old.basis is None
    out = _round(old, reading=1060.0)
    assert out.cost == _bill(60.0)
    assert out.cycle.basis == _BASIS


def test_a_confirmed_swap_asks_for_the_recorder_next_round() -> None:
    """The frame a swap builds is self-consistent, so nothing re-queried it."""
    cycle = _anchored(40.0, 1194.5)
    out = _round(cycle, reading=0.0, hold_run=2, hold_span_s=700.0, run_m3=0.0, high_m3=1234.5)
    assert out.cycle.offset_m3 == 0.0
    assert out.m3 == 0.0
    assert out.swapped is True


def test_an_ordinary_round_does_not_ask_for_arbitration() -> None:
    out = _round(_anchored(40.0, 1000.0), reading=1041.0)
    assert out.swapped is False
    assert out.m3 == 41.0


def test_a_frame_rebuilt_under_a_sound_reading_is_not_a_swap() -> None:
    """Reading still above what the year used means the frame was too high."""
    cycle = _anchored(40.0, 1194.5)
    out = _round(cycle, reading=60.0, hold_run=2, hold_span_s=700.0, run_m3=60.0, high_m3=1234.5)
    assert out.swapped is False
    assert out.m3 == 40.0

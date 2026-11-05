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

"""The year to date as a cycle the meter's readings are folded into: what a
reading adds, what is held until the next one confirms it, and when the
meter counts as replaced.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

_LOGGER = logging.getLogger(__name__)


# Consecutive sub-baseline readings required before treating the meter as
# swapped (re-anchoring YTD to ~0). A single low value is held as a glitch so
# a rebooting meter reporting 0 does not floor the running figure.
_SWAP_CONFIRM_READINGS = 3


# ...and how long that run has to last. Counting readings alone is a
# proxy for persistence that only holds on the daily tick. The live path
# fires on every state event, so three of them can land inside a second
# -- a meter that drops out and reconnects, or a burst of attribute-only
# updates carrying the same stale value -- and re-anchor the year on a
# glitch. A real replacement keeps reading low for far longer than this.
_SWAP_CONFIRM_SPAN_S = 600.0


# What no household draws in a day: a year's water, near enough, in one of
# them. Two things measure by it. A daily statistics bucket claiming more
# than this, plus what the days standing behind it could hold, is a re-based
# or reset register rather than water and is dropped. And two readings
# further apart than this are about separate events: it is how far below a
# held jump the next reading may stand and still confirm it, and how far
# from the run before it a sub-baseline reading may stand and still join it.
#
# It was the bound on a single live report too, and _MAX_STEP_M3 took that
# over because at ten times the size it tested nothing: a reading 90 m3
# above where the meter stood was taken on sight and pinned the year.
_IMPLAUSIBLE_JUMP_M3 = 100.0


# The most one meter report may add to the year before it is held for
# confirmation. A household uses 80-100 m3 in a year, so ten of them in a
# single step is already far past anything real. _IMPLAUSIBLE_JUMP_M3 was
# the only bound here and it is ten times looser: below it nothing was
# tested at all, and a reading 90 m3 above where the meter stood was taken
# on sight and pinned the year 1254 EUR over. Refusing here only ever
# delays a figure -- the next reading confirms it, or the recorder settles
# it on the next tick -- so it can afford to be tight.
_MAX_STEP_M3 = 30.0


_SECONDS_PER_DAY = 86400.0


# What a day out of sight may add to the step bound. A household uses
# 80-100 m3 in a year, so a full cubic metre a day is already several
# times any of them and covers a heavy one comfortably: three months off
# the air buys 90 m3 on top of the ordinary bound, and a meter reporting
# once a day buys one. _IMPLAUSIBLE_JUMP_M3 is the wrong rate for this
# even though it is the other bound in here, because it is the figure a
# day cannot exceed rather than one a day plausibly reaches, and using it
# handed the whole of that ceiling to every daily-reporting meter.
_AWAY_M3_PER_DAY = 1.0


# How far a recorder answer may trail the meter and still be current.
# Home Assistant compiles statistics every five minutes, so a query that
# has not lost history is at most a few minutes of flow behind the reading
# it is being compared with.
_RECORDER_LAG_M3 = 2.0


@dataclass(frozen=True)
class _YtdCycle:
    """One year's running consumption, and the frame that produces it.

    ``m3`` is what the year has published: the high-water mark of every
    figure it has seen, whatever produced them. ``offset_m3`` is the
    meter's cumulative reading at the start of the cycle (Jan 1, or the
    moment of a confirmed meter swap), so a live reading ``r`` contributes
    ``r - offset_m3``. A cycle with a figure but no frame is one being
    served straight from the recorder: the year's consumption is known,
    the reading that would produce it is not.

    ``basis`` is who the cost was computed for: the operator, the commune
    and the options that price them. The floor must not outlive that. A
    household granted the social tariff in July, or one whose commune is
    resolved for the first time, gets a cheaper bill for reasons that have
    nothing to do with a dip.

    ``year`` dates the whole record. A stamp from a previous year makes
    ``m3``, ``cost`` and ``offset_m3`` invisible without erasing them,
    because the stamp is what tells a cycle that has merely rolled over
    from one that never existed, and only the latter may start a year at
    zero.
    """

    meter: str | None = None
    year: int | None = None
    m3: float | None = None
    cost: float | None = None
    offset_m3: float | None = None
    # What the cost floor was computed under: the tariff's own rates plus
    # the options that price them. A floor only holds while these hold. An
    # older record has no basis and simply rebuilds its floor once.
    basis: str | None = None
    # The highest figure the recorder has reported for this cycle, counting
    # what the reader took back out of misread days. It only ever climbs
    # while its history is intact, since consumption accumulates, so an
    # answer below it is a database that has lost some and must not be
    # believed against the year.
    recorder_hwm: float | None = None
    # When a reading was last folded into this cycle, as epoch seconds.
    # Persisted, because the gap that matters most is the one across a
    # restart and no in-process clock survives that. It is what the step
    # bound is scaled by: a meter comes back carrying whatever was drawn
    # while nobody was looking, and how much that can be depends entirely
    # on how long nobody was looking.
    #
    # Out of the comparison, because it moves on every reading and the
    # comparison is what marks the record for saving. A meter reporting
    # every ten seconds says nothing new about the year most of the time,
    # and writing the Store for each of those reports is a great deal of
    # flash wear on the hardware this usually runs on. It rides along with
    # the next real change instead, so what is persisted may trail the
    # figure in hand by however long the year sat still.
    seen_at: float | None = field(default=None, compare=False)
    # When this cycle's figure started counting, as epoch seconds: the
    # moment a confirmed swap, or a meter the recorder holds no statistics
    # for, restarted the year.
    # None is 1 January of ``year``. Persisted, because Home Assistant's
    # statistics open a new cycle whenever a total's last_reset moves,
    # backwards included, and a start forgotten over a restart would fall
    # back to 1 January and count the year a second time.
    started_at: float | None = None

    @property
    def started(self) -> datetime | None:
        """``started_at`` as the moment the YTD sensors report as their reset."""
        if self.started_at is None:
            return None
        return datetime.fromtimestamp(self.started_at, UTC)


@dataclass(frozen=True)
class _YtdFold:
    """What :func:`_fold` decided: the new cycle, and what to publish."""

    cycle: _YtdCycle
    m3: float | None
    cost: float | None
    hold_m3: float | None
    hold_run: int
    hold_span_s: float
    run_m3: float | None
    high_m3: float | None
    # Set by the round that treats the meter as replaced. The frame is
    # then built on a register with no history, so the year's figure rests
    # on nothing but that reading until something dates it: the next tick
    # asks the recorder rather than trusting the frame it just built.
    swapped: bool = False
    # Set by a round that has met evidence it cannot weigh on its own and
    # wants the recorder on the next tick. Distinct from ``swapped``,
    # which also says the year restarted.
    arbitrate: bool = False


def _fold(
    cycle: _YtdCycle,
    *,
    now_year: int,
    meter: str,
    reading: float | None,
    recorder_m3: float | None,
    recorder_taken_back: float,
    recorder_has_statistic: bool,
    recorder_ok: bool | None,
    hold_m3: float | None,
    hold_run: int,
    hold_span_s: float,
    run_m3: float | None,
    elapsed_s: float,
    now_ts: float,
    high_m3: float | None,
    basis: str,
    cost_of: Callable[[float], float | None],
) -> _YtdFold:
    """Fold one round of evidence into the year-to-date cycle.

    The whole rule, with no clock, no I/O and no coordinator state. Both
    callers, the daily tick and the live meter event, hand over the
    evidence they have and get back the new record plus the figures to
    publish. The tick can bring a recorder answer, the live path never
    does; nothing else distinguishes them.

    ``recorder_m3`` is this year's consumption as the recorder reports it,
    or ``None`` when it was not asked or could not answer, and
    ``recorder_taken_back`` is how much the reader took back out of days it
    found to be misreads to arrive at it. ``recorder_ok``
    tells those apart for the one decision that needs it: ``False`` when
    the query failed, ``True`` when it succeeded, ``None`` when nothing has
    asked yet. ``recorder_has_statistic`` is ``False`` when the recorder
    holds no statistic for the meter at all, which it answers with the
    same ``0.0`` as a year that has used no water: a renamed or deleted
    meter, or one Home Assistant never compiled statistics for. That zero
    may still start an empty year and settle a corroborated step beyond
    the bound, but it never lowers a year that has a figure or refuses a
    reading on its strength. ``hold_m3`` is a reading held pending
    confirmation by the next one, ``hold_run`` counts consecutive readings
    below the frame and ``run_m3`` is the value that run is sitting at; all
    three are transient and none is persisted.

    Every figure a round produces is a candidate, and the published one is
    the highest of the candidates and the mark already standing. That
    comparison is source-blind, which is what keeps a year-to-date figure
    from walking backwards when the evidence changes hands.
    """
    if cycle.meter != meter:
        # Repointed at a different meter: its cumulative reading has nothing
        # to do with the old one's, so the record goes rather than being
        # reinterpreted. The year's figure is then rebuilt from the new
        # meter alone, which is the user's only escape from a meter that
        # was wrong all along.
        cycle = _YtdCycle(meter=meter)
        hold_m3 = None
        hold_run = 0
        hold_span_s = 0.0
        high_m3 = None

    current = cycle.year == now_year
    mark = cycle.m3 if current else None
    # A floor only speaks for the household it was measured for. Enabling
    # the social tariff, registering a resident, or resolving the commune
    # the household actually lives in all lower the bill for the rest of
    # the year, and clamping those to a figure measured under the old
    # answer published 437.56 EUR where 87.51 was owed. A cheaper card is
    # not in that set and is still clamped. Consumption is unaffected: no
    # rate has any say in the m3 mark.
    floor = cycle.cost if current and cycle.basis == basis else None
    offset = cycle.offset_m3 if current else None
    recorder_hwm = cycle.recorder_hwm if current else None
    started_at = cycle.started_at if current else None
    # Whether this meter's year restarted after 1 January. After a swap the
    # recorder's total for the meter still holds the old register's water
    # and stands above anything the new one can show, which the frame
    # rebuild below has to allow for. Read from the record rather than held
    # by the caller, since a restart between the swap and the tick that
    # brings the year back would otherwise lose it. A meter the recorder
    # holds no statistics for is stamped the same way when its year starts
    # empty, but its frame starts at its own register and the recorder has
    # no old water to put above it.
    after_swap = started_at is not None
    if cycle.year is None and not recorder_has_statistic:
        # A record with no year, a first setup or a repoint, only gets one
        # from a recorder answer, so this is the round that says when its
        # figure started. One the recorder holds statistics for carries on
        # from 1 January: a rename moves them to the new id, and the year
        # it reports is the same water, so a new cycle in Home Assistant's
        # statistics would count it twice. One it holds none for starts
        # empty here, and the water the year had summed before it has to
        # stay in the sum rather than come back out as a negative change.
        # A round that publishes nothing leaves the record as it found it,
        # so the live path, which never asks the recorder, decides nothing.
        started_at = now_ts
    if not current:
        hold_m3 = None
        hold_run = 0
        hold_span_s = 0.0
        high_m3 = None
    # The highest reading the meter has shown this cycle, and what tells a
    # meter climbing past a stale frame from one dipping below a sound one.
    # Only an admitted reading raises it (see the end of the round): a spike
    # that was held and then refuted used to set it for the life of the
    # process, and no real reading could clear it again, so the frame
    # correction below was switched off until the next restart.
    # How long the meter has been out of sight, across restarts included.
    # A backward clock step reads as no gap at all rather than a negative
    # one, which is the cautious way round.
    seen_at = cycle.seen_at
    away_s = 0.0 if seen_at is None else max(0.0, now_ts - seen_at)
    # What one report may add before it is held: the ordinary bound, plus
    # whatever the household could have drawn while the meter was out of
    # sight. A meter that was away comes back carrying all of it and that
    # step is real however large, but how large it can be depends on how
    # long nobody was looking.
    #
    # The gap used to count for nothing but its own existence. One dropped
    # report bought the same 100 m3 as three months off the air, which is
    # about five hours of a domestic connection at full bore or 437 days
    # of an average household, and a spike arriving on the round after a
    # single dropout was taken on sight for 993 EUR.
    step_bound = _MAX_STEP_M3 + away_s / _SECONDS_PER_DAY * _AWAY_M3_PER_DAY
    was_high = high_m3
    # A record still carrying a stamp has history on this meter, so a
    # reading it cannot place comes from a meter that has been running all
    # along. A cleared record has no such history and must wait for the
    # recorder rather than declare the year starts at the first reading it
    # happens to see.
    known_meter = cycle.year is not None

    candidate: float | None = None
    swapped = False
    arbitrate = False
    seen = [figure for figure in (mark, recorder_m3) if figure is not None]
    # What a reading has to clear to belong to this cycle. A framed year
    # measures against the frame. A year served from the recorder has no
    # frame yet, so its own consumption stands in: a meter that has been
    # running since January cannot read below what the year has used.
    bar = offset if offset is not None else (max(seen) if seen else None)
    if reading is None:
        # No reading to confirm or refute a held jump, and the hold only
        # means anything against the reading that follows it directly.
        # Keeping it would let the next spike through unheld, whenever it
        # came.
        hold_m3 = None
    elif bar is not None and reading < bar:
        # Below the bar is either a transient glitch (a rebooting meter
        # reporting 0, a dropout) or a genuine meter swap. Distinguish by
        # persistence: a lone low reading is held rather than flooring the
        # year, and only a sustained run re-anchors.
        if hold_run == 0 or run_m3 is None or abs(reading - run_m3) > _IMPLAUSIBLE_JUMP_M3:
            # Being under the bar is not enough to join a run. A replaced
            # register climbs from where the last reading left it, so a
            # reading that stands nowhere near the ones before it is a
            # separate event and starts a run of its own. Counting them
            # together let two honest readings and one dropout confirm a
            # replacement between them, and the frame was then rebuilt on
            # whichever of the three happened to come last.
            run_m3 = reading
            hold_run = 0
            hold_span_s = 0.0
        hold_run += 1
        if hold_run > 1:
            # The span is how long the run itself has lasted. The gap
            # before its first reading is the meter's ordinary silence,
            # and counting it let three readings inside a second pass
            # for ten minutes after any quiet hour.
            hold_span_s += max(0.0, elapsed_s)
        # A reading under the bar says nothing about a jump held above it,
        # so the hold lapses here too rather than standing until some
        # later spike walks straight past it.
        hold_m3 = None
        if hold_run >= _SWAP_CONFIRM_READINGS and hold_span_s >= _SWAP_CONFIRM_SPAN_S:
            base = max(seen) if seen else None
            # A year with no frame whose figure the meter's own statistics
            # gave, a first setup or a repoint, measures readings against
            # that figure. Home Assistant's sum runs straight through a
            # replacement, so a register fitted earlier in the year sits
            # below it by everything the old one measured, and taking that
            # for a swap restarted a year that had not restarted.
            served_by_recorder = offset is None and recorder_hwm is not None
            if base is not None and (reading >= base or served_by_recorder):
                # The run is sustained, but the meter still shows more water
                # than the year has used, so it cannot be the fresh register a
                # replacement leaves behind. What it sits below is a frame
                # built too high, from a reading that overstated the meter,
                # and the frame is the part that has to give: rebuild it under
                # this reading so the year carries on from the figure it has
                # already published instead of starting over. The figure the
                # frame is rebuilt against is the one standing, so the round
                # publishes what it already published; the recorder answer is
                # kept, because it is still about this meter. A year served
                # from the recorder gets the same frame under a register
                # replaced earlier in the year, below zero if need be.
                offset = reading - base
                # Ask the recorder on the next tick, as the swap below
                # does. A frame rebuilt this way reproduces the figure
                # already standing, so the stale-frame gate in
                # _compute_ytd is satisfied from the moment it is built
                # and nothing would ever query again: a run of low
                # readings that was really a dropout left the year on a
                # frame nobody could check, 540.23 m3 against 40.30.
                arbitrate = True
                hold_m3 = None
                hold_run = 0
                hold_span_s = 0.0
            else:
                # Reading below what the year has already used: no meter that
                # measured that water can show this, so the register is a new
                # one. Zero the record before the figures are compared below,
                # or the old mark resurrects itself through the comparison and
                # the swap never takes effect.
                swapped = True
                started_at = now_ts
                offset = reading
                mark = 0.0
                floor = None
                candidate = 0.0
                # The year restarts on a register with no history, so what
                # the recorder reported for the old meter, and any water it
                # could not see there, go with it.
                recorder_hwm = None
                hold_m3 = None
                hold_run = 0
                hold_span_s = 0.0
            # Whichever way it went, the reading is where the meter stands
            # now. Leaving an older mark behind would keep every later reading
            # under a mark it cannot reach, and the frame could never be
            # corrected again this year.
            high_m3 = reading
    elif offset is None:
        # No frame this year yet, and the reading clears the bar. Build the
        # frame from the highest figure the year already has, so the reading
        # continues what is published instead of restarting it.
        hold_run = 0
        hold_span_s = 0.0
        if seen:
            base = max(seen)
            offset = reading - base
            candidate = base
            # A frame built on a recorder answer reproduces that answer
            # from then on, so the stale-frame gate never asks again. An
            # answer read before a misread's correction was compiled still
            # holds the misread, and with nothing asking again the reader's
            # take-back never reached the year. One more question on the
            # next tick settles it.
            arbitrate = recorder_m3 is not None or recorder_hwm is not None
        elif known_meter and recorder_ok is not False:
            # Nothing to place the reading against, and no reason to believe
            # the year holds anything: it starts here. A failed query is not
            # such a reason, since the year may well have consumption we
            # simply could not read, and anchoring would discard it.
            offset = reading
            candidate = 0.0
    else:
        hold_run = 0
        hold_span_s = 0.0
        framed = reading - offset
        # A held jump is released by a reading that stands behind it --
        # the meter really is up there. A reading that lands somewhere
        # else entirely refutes the hold instead of confirming it, and
        # has to face the jump test on its own rather than being waved
        # through on the strength of the value it just contradicted.
        corroborated = hold_m3 is not None and reading >= hold_m3 - _IMPLAUSIBLE_JUMP_M3
        if corroborated and seen and framed - max(seen) > step_bound:
            # Two readings agreeing establish where the meter stands, never
            # where it stood when the cycle opened, so a step this size is
            # either a frame sitting under the meter or water the year has
            # genuinely used since anyone last looked. Nothing in a reading
            # tells those apart.
            if recorder_m3 is not None:
                # The recorder reads the same meter's own statistics and
                # says the year used far less, so the frame is what is
                # wrong: that is what a re-anchor onto a reading the meter
                # never really showed leaves behind. Rebuild it against
                # what the year knows instead of billing the difference.
                # A meter with no statistic lands here too. Its zero proves
                # nothing, but nothing else will ever settle the step
                # either, and arbitrating it on every tick would leave the
                # year stuck below a frame the meter has left behind.
                offset = reading - max(seen)
            else:
                # The live path never carries a recorder answer, so this
                # was the branch that billed the whole of a re-based
                # register: 1012.85 m3 where 12.8 was owed, and nothing but
                # the recorder can take a mark back, so it stood until
                # January. Publish nothing and put the question to the
                # recorder on the next tick.
                arbitrate = True
            hold_m3 = None
        elif corroborated:
            candidate = framed
            hold_m3 = None
        elif mark is not None and framed - mark > step_bound:
            # A step this large in one report is a garbage value far more
            # often than real usage, and only the recorder can take the mark
            # back down, so taking it would pin the year on a household whose
            # recorder never speaks. Hold it for one reading: a meter really
            # sitting there repeats the jump, while a spike is followed by
            # normal values.
            hold_m3 = reading
        else:
            candidate = framed
            hold_m3 = None

    spoken_before = recorder_hwm is not None
    if (
        recorder_has_statistic
        and recorder_m3 is not None
        and (recorder_hwm is None or recorder_m3 + recorder_taken_back >= recorder_hwm - 1e-6)
    ):
        # A recorder answer only climbs while its history is intact, since
        # consumption accumulates. One at or above every previous answer is
        # therefore a database that has not lost any of the year, and it
        # read the same meter's own statistics for all of it, so water it
        # has no record of did not flow. That is not proof every day was
        # read: a day the reader refuses stays refused on every later
        # query, and the answers keep climbing short of it. One below is a
        # database that has lost some, and it is ignored rather than
        # believed against the year.
        #
        # The answer compared is the one before the reader took a misread
        # back out of the day it landed in. A spike corrected after
        # midnight is still in the answer read before the correction's
        # hour is compiled, which is the very tick the correction queries
        # on, and the corrected answer below it would otherwise read as a
        # lost day and keep the spike until the year caught up with it.
        # Added back, the same water can sum to a binary residue below the
        # answer it was in, which is no lost day either.
        #
        # None of this holds for a meter the recorder has no statistic
        # for. Its zero repeats on every query, so a second one passed for
        # a history that had stayed whole, and a meter renamed after New
        # Year, or one with no state class whose reading stepped back a
        # little, had its year taken down to nothing and saved that way.
        whole = recorder_m3 + recorder_taken_back
        recorder_hwm = whole if recorder_hwm is None else max(recorder_hwm, whole)
        if (
            candidate is not None
            and candidate - recorder_m3 > _RECORDER_LAG_M3
            and (spoken_before or mark is None or recorder_m3 >= mark)
        ):
            # Published figures are the maximum of what a round holds, so
            # without this the recorder loses to the reading rather than
            # settling it: a meter 90 m3 above where it stands pinned the
            # year 1254 EUR over with the recorder saying otherwise in the
            # same round.
            _LOGGER.warning(
                "a reading of the water meter implying %.1f m3 for the year against the "
                "recorder's %.1f; taking the recorder, which has the year's own statistics "
                "behind it",
                candidate,
                recorder_m3,
            )
            candidate = None
        if (
            spoken_before
            and reading is not None
            and reading > 0
            and (bar is None or reading >= bar)
            and mark is not None
            and mark - recorder_m3 > _RECORDER_LAG_M3
        ):
            # And the mark comes down with it. A spike smaller than the step
            # bound is admitted on sight, raises the mark, and the mark only
            # ever climbed, so it stood until January however plainly the
            # recorder contradicted it. The frame is rebuilt under the
            # corrected figure and the cost floor goes with it, or the
            # correction would show on the volume and not on the bill.
            #
            # Only against a recorder that has already spoken for this
            # year. One answering for the first time has nothing behind it
            # to show its history is whole, and a database that lost the
            # year before anyone asked would read exactly like this.
            #
            # And only on a round whose reading sits in the frame. The
            # spike this is for leaves the meter there, while a dropout
            # tick would rebuild the frame on the glitch value, and the
            # meter coming back would then be held and arbitrated against
            # the lowered figure. A tick with no reading has no frame to
            # rebuild and only dipped the year until the next one.
            #
            # The frame alone does not catch a reading of 0 once a meter
            # swap has put it below zero, since every reading clears a
            # negative bar. The 0 is refused on its own for that reason.
            # The highest reading on the register would not do as a bar,
            # because the spike this takes back is what raised it.
            _LOGGER.warning(
                "the water meter's year stood at %.1f m3 against the recorder's %.1f; "
                "taking the recorder, which has the year's own statistics behind it",
                mark,
                recorder_m3,
            )
            mark = recorder_m3
            floor = None
            offset = reading - recorder_m3
            high_m3 = reading

    if candidate is not None and reading is not None:
        # The meter was seen, which means it gave an answer this round could
        # place. A reading held for confirmation or refused as a glitch is
        # not that, and stamping on those spent the allowance a real
        # catch-up needed: a garbage value arriving first after an outage
        # took the whole of it and the reading behind it, which really did
        # carry three months of water, was held as a spike.
        seen_at = now_ts
        if high_m3 is None or reading > high_m3:
            high_m3 = reading

    if swapped:
        # The year restarts on the new meter, so a recorder total spanning
        # the old one says nothing about it.
        recorder_m3 = None
    figures = [figure for figure in (mark, candidate, recorder_m3) if figure is not None]
    if not figures:
        # Nothing is known about this year. Leave the record alone, stamp and
        # all, and report nothing rather than publish a zero it has not
        # earned: the stamp is what stops the next reading starting the year
        # over.
        return _YtdFold(
            cycle,
            None,
            None,
            hold_m3,
            hold_run,
            hold_span_s,
            run_m3,
            high_m3,
            swapped,
            arbitrate,
        )
    published = max(figures)

    if (
        offset is not None
        and reading is not None
        and recorder_m3 is not None
        and candidate is not None
        and was_high is not None
        and reading >= was_high
        and published > reading - offset
        and (reading >= published or after_swap)
    ):
        # A reading and a recorder figure read in the same round are the only
        # pair that dates the year's consumption to a meter position, so this
        # is the only place the frame can be corrected without guessing.
        #
        # It also takes a high-water mark to compare against. Without one
        # the record has seen nothing on this meter yet -- a restart, a
        # fresh year -- and a reading that happens to dip cannot be told
        # from one that climbs, so a single low sample would rebuild the
        # frame beneath the meter and over-report every day until January.
        # Waiting a round for the mark to re-establish itself costs the
        # correction nothing. The
        # figure on its own says how much water the year has seen but not
        # where the meter stood when it did, and the frame's own last position
        # is not an answer: a reading held as a spike, or one never seen at
        # all, leaves it behind the meter, and correcting against it would
        # count the same water twice.
        #
        # Rebuilt this way the frame produces exactly what is being published,
        # so the meter carries on from there instead of having to climb back
        # up to where the frame had got to on its own.
        #
        # A register holding less water than the year has used cannot have
        # measured all of it, so the frame is normally left alone there.
        # A replaced meter is the exception. Home Assistant's statistics run
        # straight through the swap, so the recorder's total keeps the old
        # meter's water and the new register stays below it all year. Left
        # on the swap reading, the frame produced less than the recorder had
        # published, every live reading was lost under it, and the year moved
        # only when a daily tick asked the recorder again. The frame goes
        # below zero here instead. Only a reading at or above the highest the
        # meter has shown gets this far, so a dip cannot pull it down.
        offset = reading - published

    cost = cost_of(published)
    if cost is not None:
        if floor is not None and cost < floor:
            # Consumption comes down only where the recorder has corrected
            # it, and that path drops the floor itself. The bill is recomputed
            # each tick from a freshly fetched tariff and an elapsed-day
            # fraction, so it needs this floor of its own to stop a lower
            # tariff or a backward clock step publishing a decrease.
            cost = floor
        else:
            floor = cost
    return _YtdFold(
        _YtdCycle(
            meter=meter,
            year=now_year,
            m3=published,
            cost=floor,
            offset_m3=offset,
            basis=basis,
            recorder_hwm=recorder_hwm,
            seen_at=seen_at,
            started_at=started_at,
        ),
        published,
        cost,
        hold_m3,
        hold_run,
        hold_span_s,
        run_m3,
        high_m3,
        swapped,
        arbitrate,
    )

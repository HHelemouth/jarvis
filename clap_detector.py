"""Double-clap detection that looks at the *shape* of a sound, not only its volume.

Volume alone is not enough: a voice or music reaches the same RMS level as a clap.
A clap collapses within ~100 ms, a voice or a song stays loud. So a peak above the
threshold is only a candidate: we wait DECAY_S after the peak and require the level
to have dropped under DECAY_RATIO * peak before counting it as a clap.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("jarvis")


@dataclass
class ClapSettings:
    min_rms: float = 0.28          # absolute floor for a peak to be considered
    spike_ratio: float = 8.0       # peak must also be this many times the noise floor
    peak_window_s: float = 0.02    # time allowed for the level to keep rising after onset
    decay_s: float = 0.10          # when to check that the sound has collapsed
    decay_check_s: float = 0.02    # width of the check window ending at decay_s
    decay_ratio: float = 0.35      # level must fall under this fraction of the peak
    min_gap_s: float = 0.12        # min time between the two claps
    max_gap_s: float = 0.35        # max time between the two claps
    rearm_ratio: float = 0.5       # after a rejected sound, wait until level < threshold * this
    noise_alpha: float = 0.995     # smoothing of the background noise estimate


@dataclass
class _Candidate:
    onset: float
    peak: float
    peak_t: float
    threshold: float
    after_max: float = 0.0  # loudest level seen inside the decay check window


class ClapDetector:
    """Feed one RMS level per audio block; returns "double" when a double clap is confirmed."""

    def __init__(self, settings: ClapSettings | None = None, debug: bool = False) -> None:
        self.s = settings or ClapSettings()
        self.debug = debug
        self.noise_floor = 0.005
        self._cand: _Candidate | None = None
        self._hold = False
        self._first_clap: float | None = None
        # Debug-only tracking of peaks that stayed under the threshold.
        self._weak_max = 0.0

    @property
    def threshold(self) -> float:
        return max(self.s.min_rms, self.noise_floor * self.s.spike_ratio)

    def feed(self, level: float, now: float) -> str | None:
        s = self.s
        threshold = self.threshold

        if self._cand is not None:
            return self._follow_candidate(level, now)

        if self._hold:
            if level < threshold * s.rearm_ratio:
                self._hold = False
            return None

        if level >= threshold:
            self._flush_weak(threshold)
            self._cand = _Candidate(onset=now, peak=level, peak_t=now, threshold=threshold)
            return None

        self.noise_floor = s.noise_alpha * self.noise_floor + (1 - s.noise_alpha) * level
        self.noise_floor = max(self.noise_floor, 1e-6)
        if self.debug:
            self._track_weak(level, threshold)
        return None

    def _follow_candidate(self, level: float, now: float) -> str | None:
        s = self.s
        c = self._cand
        assert c is not None

        if now - c.onset <= s.peak_window_s:
            if level > c.peak:
                c.peak = level
                c.peak_t = now
            return None

        since_peak = now - c.peak_t
        if since_peak >= s.decay_s - s.decay_check_s:
            c.after_max = max(c.after_max, level)
        if since_peak < s.decay_s:
            return None

        self._cand = None
        ratio = c.after_max / c.peak if c.peak > 0 else 1.0
        is_clap = c.after_max < s.decay_ratio * c.peak
        if self.debug:
            log.info(
                "PIC  niveau=%.3f  seuil=%.3f  apres 100 ms=%.3f (%d %% du pic)  -> %s",
                c.peak,
                c.threshold,
                c.after_max,
                round(ratio * 100),
                "CLAP" if is_clap else "rejete (voix ou musique : le son reste fort)",
            )
        if not is_clap:
            self._hold = True
            self._first_clap = None
            return None
        return self._register_clap(c.onset)

    def _register_clap(self, t: float) -> str | None:
        s = self.s
        first = self._first_clap
        if first is None or t - first > s.max_gap_s:
            if self.debug and first is not None:
                log.info("     ecart %.2f s trop long (max %.2f s) : on repart de zero", t - first, s.max_gap_s)
            self._first_clap = t
            return "clap"
        gap = t - first
        if gap < s.min_gap_s:
            if self.debug:
                log.info("     ecart %.2f s trop court (min %.2f s) : ignore", gap, s.min_gap_s)
            return None
        self._first_clap = None
        if self.debug:
            log.info("     DOUBLE CLAP (ecart %.2f s)", gap)
        return "double"

    def _track_weak(self, level: float, threshold: float) -> None:
        low = threshold * 0.4
        if level >= low:
            self._weak_max = max(self._weak_max, level)
        elif self._weak_max > 0 and level < low * 0.8:
            self._flush_weak(threshold)

    def _flush_weak(self, threshold: float) -> None:
        if self.debug and self._weak_max > 0:
            log.info(
                "pic  niveau=%.3f  seuil=%.3f  -> trop faible, ignore",
                self._weak_max,
                threshold,
            )
        self._weak_max = 0.0

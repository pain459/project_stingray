"""Pure-Python port of Sensirion's Gas Index Algorithm (v3.2.0).

Source: https://github.com/Sensirion/gas-index-algorithm
(sensirion_gas_index_algorithm.c / .h, BSD-3-Clause)

The SGP41 datasheet does *not* provide a direct SRAW -> index formula. The
official algorithm maintains a running mean/variance estimate of the raw
signal, applies a MOX model, a scaled sigmoid and an adaptive low-pass filter.
This module is a faithful translation of that reference implementation so the
driver reports real VOC/NOx indices rather than a linear approximation.

No state persistence is implemented (out of scope): restarts begin a fresh
initial-learning phase.
"""

from __future__ import annotations

import math

ALGORITHM_TYPE_VOC = 0
ALGORITHM_TYPE_NOX = 1

DEFAULT_SAMPLING_INTERVAL = 1.0
INITIAL_BLACKOUT = 45.0
INDEX_GAIN = 230.0
SRAW_STD_INITIAL = 50.0
SRAW_STD_BONUS_VOC = 220.0
SRAW_STD_NOX = 2000.0
TAU_MEAN_HOURS = 12.0
TAU_VARIANCE_HOURS = 12.0
TAU_INITIAL_MEAN_VOC = 20.0
TAU_INITIAL_MEAN_NOX = 1200.0
INIT_DURATION_MEAN_VOC = 3600.0 * 0.75
INIT_DURATION_MEAN_NOX = 3600.0 * 4.75
INIT_TRANSITION_MEAN = 0.01
TAU_INITIAL_VARIANCE = 2500.0
INIT_DURATION_VARIANCE_VOC = 3600.0 * 1.45
INIT_DURATION_VARIANCE_NOX = 3600.0 * 5.70
INIT_TRANSITION_VARIANCE = 0.01
GATING_THRESHOLD_VOC = 340.0
GATING_THRESHOLD_NOX = 30.0
GATING_THRESHOLD_INITIAL = 510.0
GATING_THRESHOLD_TRANSITION = 0.09
GATING_VOC_MAX_DURATION_MINUTES = 60.0 * 3.0
GATING_NOX_MAX_DURATION_MINUTES = 60.0 * 12.0
GATING_MAX_RATIO = 0.3
SIGMOID_L = 500.0
SIGMOID_K_VOC = -0.0065
SIGMOID_X0_VOC = 213.0
SIGMOID_K_NOX = -0.0101
SIGMOID_X0_NOX = 614.0
VOC_INDEX_OFFSET_DEFAULT = 100.0
NOX_INDEX_OFFSET_DEFAULT = 1.0
LP_TAU_FAST = 20.0
LP_TAU_SLOW = 500.0
LP_ALPHA = -0.2
VOC_SRAW_MINIMUM = 20000
NOX_SRAW_MINIMUM = 10000
PERSISTENCE_UPTIME_GAMMA = 3.0 * 3600.0
GAMMA_SCALING = 64.0
ADDITIONAL_GAMMA_MEAN_SCALING = 8.0
FIX16_MAX = 32767.0


class GasIndexAlgorithm:
    """Sensirion VOC/NOx index algorithm.

    Args:
        algorithm_type: ``ALGORITHM_TYPE_VOC`` or ``ALGORITHM_TYPE_NOX``.
        sampling_interval: seconds between :meth:`process` calls (1 s here).
    """

    def __init__(
        self,
        algorithm_type: int = ALGORITHM_TYPE_VOC,
        sampling_interval: float = DEFAULT_SAMPLING_INTERVAL,
    ) -> None:
        self.algorithm_type = algorithm_type
        self.sampling_interval = sampling_interval
        if algorithm_type == ALGORITHM_TYPE_NOX:
            self.index_offset = NOX_INDEX_OFFSET_DEFAULT
            self.sraw_minimum = NOX_SRAW_MINIMUM
            self.gating_max_duration_minutes = GATING_NOX_MAX_DURATION_MINUTES
            self.init_duration_mean = INIT_DURATION_MEAN_NOX
            self.init_duration_variance = INIT_DURATION_VARIANCE_NOX
            self.gating_threshold = GATING_THRESHOLD_NOX
        else:
            self.index_offset = VOC_INDEX_OFFSET_DEFAULT
            self.sraw_minimum = VOC_SRAW_MINIMUM
            self.gating_max_duration_minutes = GATING_VOC_MAX_DURATION_MINUTES
            self.init_duration_mean = INIT_DURATION_MEAN_VOC
            self.init_duration_variance = INIT_DURATION_VARIANCE_VOC
            self.gating_threshold = GATING_THRESHOLD_VOC
        self.index_gain = INDEX_GAIN
        self.tau_mean_hours = TAU_MEAN_HOURS
        self.tau_variance_hours = TAU_VARIANCE_HOURS
        self.sraw_std_initial = SRAW_STD_INITIAL
        self.reset()

    def reset(self) -> None:
        self.uptime = 0.0
        self.sraw = 0.0
        self.gas_index = 0.0
        self._init_instances()

    def _init_instances(self) -> None:
        self._set_mean_variance_parameters()
        self._mox_model_set_parameters(self._get_std(), self._get_mean())
        if self.algorithm_type == ALGORITHM_TYPE_NOX:
            self._sigmoid_scaled_set_parameters(
                SIGMOID_X0_NOX, SIGMOID_K_NOX, NOX_INDEX_OFFSET_DEFAULT
            )
        else:
            self._sigmoid_scaled_set_parameters(
                SIGMOID_X0_VOC, SIGMOID_K_VOC, VOC_INDEX_OFFSET_DEFAULT
            )
        self._adaptive_lowpass_set_parameters()

    # -- mean/variance estimator -------------------------------------------

    def _set_mean_variance_parameters(self) -> None:
        self.mve_initialized = False
        self.mve_mean = 0.0
        self.mve_sraw_offset = 0.0
        self.mve_std = self.sraw_std_initial
        self.mve_gamma_mean_initial = (
            ADDITIONAL_GAMMA_MEAN_SCALING
            * GAMMA_SCALING
            * (self.sampling_interval / 3600.0)
            / (self.tau_mean_hours + self.sampling_interval / 3600.0)
        )
        self.mve_gamma_variance_initial = (
            GAMMA_SCALING
            * (self.sampling_interval / 3600.0)
            / (self.tau_variance_hours + self.sampling_interval / 3600.0)
        )
        tau_initial_mean = (
            TAU_INITIAL_MEAN_NOX
            if self.algorithm_type == ALGORITHM_TYPE_NOX
            else TAU_INITIAL_MEAN_VOC
        )
        self.mve_gamma_initial_mean = (
            ADDITIONAL_GAMMA_MEAN_SCALING
            * GAMMA_SCALING
            * self.sampling_interval
            / (tau_initial_mean + self.sampling_interval)
        )
        self.mve_gamma_initial_variance = (
            GAMMA_SCALING
            * self.sampling_interval
            / (TAU_INITIAL_VARIANCE + self.sampling_interval)
        )
        self.mve_gamma_mean = 0.0
        self.mve_gamma_variance = 0.0
        self.mve_uptime_gamma = 0.0
        self.mve_uptime_gating = 0.0
        self.mve_gating_duration_minutes = 0.0

    def _get_std(self) -> float:
        return self.mve_std

    def _get_mean(self) -> float:
        return self.mve_mean + self.mve_sraw_offset

    def _sigmoid_set_parameters(self, x0: float, k: float) -> None:
        self._sig_k = k
        self._sig_x0 = x0

    def _sigmoid_process(self, sample: float) -> float:
        x = self._sig_k * (sample - self._sig_x0)
        if x < -50.0:
            return 1.0
        if x > 50.0:
            return 0.0
        return 1.0 / (1.0 + math.exp(x))

    def _mve_calculate_gamma(self) -> None:
        uptime_limit = FIX16_MAX - self.sampling_interval
        if self.mve_uptime_gamma < uptime_limit:
            self.mve_uptime_gamma += self.sampling_interval
        if self.mve_uptime_gating < uptime_limit:
            self.mve_uptime_gating += self.sampling_interval

        self._sigmoid_set_parameters(self.init_duration_mean, INIT_TRANSITION_MEAN)
        sigmoid_gamma_mean = self._sigmoid_process(self.mve_uptime_gamma)
        gamma_mean = self.mve_gamma_mean + (
            (self.mve_gamma_initial_mean - self.mve_gamma_mean) * sigmoid_gamma_mean
        )

        gating_threshold_mean = self.gating_threshold + (
            (GATING_THRESHOLD_INITIAL - self.gating_threshold)
            * self._sigmoid_process(self.mve_uptime_gating)
        )
        self._sigmoid_set_parameters(
            gating_threshold_mean, GATING_THRESHOLD_TRANSITION
        )
        sigmoid_gating_mean = self._sigmoid_process(self.gas_index)
        self.mve_gamma_mean = sigmoid_gating_mean * gamma_mean

        self._sigmoid_set_parameters(
            self.init_duration_variance, INIT_TRANSITION_VARIANCE
        )
        sigmoid_gamma_variance = self._sigmoid_process(self.mve_uptime_gamma)
        gamma_variance = self.mve_gamma_variance + (
            (self.mve_gamma_initial_variance - self.mve_gamma_variance)
            * (sigmoid_gamma_variance - sigmoid_gamma_mean)
        )
        gating_threshold_variance = self.gating_threshold + (
            (GATING_THRESHOLD_INITIAL - self.gating_threshold)
            * self._sigmoid_process(self.mve_uptime_gating)
        )
        self._sigmoid_set_parameters(
            gating_threshold_variance, GATING_THRESHOLD_TRANSITION
        )
        sigmoid_gating_variance = self._sigmoid_process(self.gas_index)
        self.mve_gamma_variance = sigmoid_gating_variance * gamma_variance

        self.mve_gating_duration_minutes += self.sampling_interval / 60.0 * (
            ((1.0 - sigmoid_gating_mean) * (1.0 + GATING_MAX_RATIO))
            - GATING_MAX_RATIO
        )
        self.mve_gating_duration_minutes = max(
            0.0, self.mve_gating_duration_minutes
        )
        if self.mve_gating_duration_minutes > self.gating_max_duration_minutes:
            self.mve_uptime_gating = 0.0

    def _mve_process(self, sraw: float) -> None:
        if not self.mve_initialized:
            self.mve_initialized = True
            self.mve_sraw_offset = sraw
            self.mve_mean = 0.0
            return
        if self.mve_mean >= 100.0 or self.mve_mean <= -100.0:
            self.mve_sraw_offset += self.mve_mean
            self.mve_mean = 0.0
        sraw -= self.mve_sraw_offset
        self._mve_calculate_gamma()
        delta = (sraw - self.mve_mean) / GAMMA_SCALING
        c = self.mve_std - delta if delta < 0.0 else self.mve_std + delta
        additional_scaling = 1.0
        if c > 1440.0:
            additional_scaling = (c / 1440.0) * (c / 1440.0)
        self.mve_std = math.sqrt(
            additional_scaling
            * (GAMMA_SCALING - self.mve_gamma_variance)
            * (
                (
                    self.mve_std
                    * (self.mve_std / (GAMMA_SCALING * additional_scaling))
                )
                + (
                    (self.mve_gamma_variance * delta) / additional_scaling * delta
                )
            )
        )
        self.mve_mean += (
            self.mve_gamma_mean * delta / ADDITIONAL_GAMMA_MEAN_SCALING
        )

    # -- MOX model ----------------------------------------------------------

    def _mox_model_set_parameters(self, sraw_std: float, sraw_mean: float) -> None:
        self.mox_sraw_std = sraw_std
        self.mox_sraw_mean = sraw_mean

    def _mox_model_process(self, sraw: float) -> float:
        if self.algorithm_type == ALGORITHM_TYPE_NOX:
            return (sraw - self.mox_sraw_mean) / SRAW_STD_NOX * self.index_gain
        return (
            (sraw - self.mox_sraw_mean)
            / (-1.0 * (self.mox_sraw_std + SRAW_STD_BONUS_VOC))
            * self.index_gain
        )

    # -- scaled sigmoid -----------------------------------------------------

    def _sigmoid_scaled_set_parameters(
        self, x0: float, k: float, offset_default: float
    ) -> None:
        self.ss_k = k
        self.ss_x0 = x0
        self.ss_offset_default = offset_default

    def _sigmoid_scaled_process(self, sample: float) -> float:
        x = self.ss_k * (sample - self.ss_x0)
        if x < -50.0:
            return SIGMOID_L
        if x > 50.0:
            return 0.0
        if sample >= 0.0:
            if self.ss_offset_default == 1.0:
                shift = (500.0 / 499.0) * (1.0 - self.index_offset)
            else:
                shift = (SIGMOID_L - 5.0 * self.index_offset) / 4.0
            return (SIGMOID_L + shift) / (1.0 + math.exp(x)) - shift
        return (
            self.index_offset
            / self.ss_offset_default
            * (SIGMOID_L / (1.0 + math.exp(x)))
        )

    # -- adaptive lowpass ---------------------------------------------------

    def _adaptive_lowpass_set_parameters(self) -> None:
        self.alp_a1 = self.sampling_interval / (
            LP_TAU_FAST + self.sampling_interval
        )
        self.alp_a2 = self.sampling_interval / (
            LP_TAU_SLOW + self.sampling_interval
        )
        self.alp_initialized = False

    def _adaptive_lowpass_process(self, sample: float) -> float:
        if not self.alp_initialized:
            self.alp_x1 = sample
            self.alp_x2 = sample
            self.alp_x3 = sample
            self.alp_initialized = True
        self.alp_x1 = (1.0 - self.alp_a1) * self.alp_x1 + self.alp_a1 * sample
        self.alp_x2 = (1.0 - self.alp_a2) * self.alp_x2 + self.alp_a2 * sample
        abs_delta = abs(self.alp_x1 - self.alp_x2)
        f1 = math.exp(LP_ALPHA * abs_delta)
        tau_a = (LP_TAU_SLOW - LP_TAU_FAST) * f1 + LP_TAU_FAST
        a3 = self.sampling_interval / (self.sampling_interval + tau_a)
        self.alp_x3 = (1.0 - a3) * self.alp_x3 + a3 * sample
        return self.alp_x3

    # -- public API ---------------------------------------------------------

    @property
    def warming_up(self) -> bool:
        """True during the initial blackout, when :meth:`process` returns 0.

        The reference algorithm returns 0 for its first samples while it learns
        a baseline. Zero is a sentinel, not a real index, so callers must not
        publish it as a measurement.
        """
        return self.uptime <= INITIAL_BLACKOUT

    def process(self, sraw: int) -> int:
        """Feed one raw signal sample and return the integer gas index (1..500).

        Returns 0 during the algorithm's initial 45-sample blackout period.
        """
        if self.uptime <= INITIAL_BLACKOUT:
            self.uptime += self.sampling_interval
        else:
            if 0 < sraw < 65000:
                if sraw < self.sraw_minimum + 1:
                    sraw = self.sraw_minimum + 1
                elif sraw > self.sraw_minimum + 32767:
                    sraw = self.sraw_minimum + 32767
                self.sraw = float(sraw - self.sraw_minimum)
            if (
                self.algorithm_type == ALGORITHM_TYPE_VOC
                or self.mve_initialized
            ):
                self.gas_index = self._mox_model_process(self.sraw)
                self.gas_index = self._sigmoid_scaled_process(self.gas_index)
            else:
                self.gas_index = self.index_offset
            self.gas_index = self._adaptive_lowpass_process(self.gas_index)
            if self.gas_index < 0.5:
                self.gas_index = 0.5
            if self.sraw > 0.0:
                self._mve_process(self.sraw)
                self._mox_model_set_parameters(self._get_std(), self._get_mean())
        return int(self.gas_index + 0.5)

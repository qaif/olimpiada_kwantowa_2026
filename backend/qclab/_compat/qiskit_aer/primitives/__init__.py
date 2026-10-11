"""Moduł zgodności z Qiskitem – wszystko liczy ``qclab`` (docs/tasks/QC-01.md § 2)."""

from qclab.backends import StatevectorEstimator as EstimatorV2
from qclab.backends import StatevectorSampler as SamplerV2

__all__ = ["EstimatorV2", "SamplerV2"]

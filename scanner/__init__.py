"""NIFTY tail-risk scanner — deterministic signal core.

The signal path (data → metrics → gates → decision) is pure Python with fixed
thresholds from config.yaml. No randomness, no LLM. Same data in → same signal
out, so it can be unit-tested and backtested.
"""

__version__ = "0.1.0"

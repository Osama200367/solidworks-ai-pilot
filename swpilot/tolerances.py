"""Shared geometric tolerance.

1e-6 mm = 1 nm: far below manufacturing relevance, far above float noise
at the coordinate magnitudes we deal with (~1e2 mm). Lives in a neutral
module so both the command layer (macro validation) and the mock backend
(geometry predicates) apply the identical tolerance — a macro-accepted
file must never fail the simulator's equivalent check.
"""

EPS = 1e-6

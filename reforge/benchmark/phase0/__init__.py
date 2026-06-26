"""Phase 0 instrument calibration — corpus, driver, report.

Single purpose: prove the four runtime code paths that Phase 1/2 will
rely on are alive and reachable, without measuring any result-direction
claim. Go / no-go gates are documented in
``docs/eval/PHASE0_CORPUS.md`` and ``docs/eval/PHASE0_METRICS.md``;
the corresponding markdown report is emitted to
``docs/eval/PHASE0_CALIBRATION.md`` by ``python -m
reforge.benchmark.phase0``.
"""

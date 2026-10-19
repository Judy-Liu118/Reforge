"""Online, gold-free learning loop over the data-analysis agent.

Control path (what decides retry / card / review) sees only verifier output.
Gold, when a task has one, is wrapped in tasks.GoldRef and read exclusively
by shadow.py, after the task's decision is committed to the loop log.
"""

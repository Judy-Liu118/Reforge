"""Shared HTML layout shipped with every dashboard page.

Inline strings — CDN-loaded Tailwind + Alpine + Chart.js, no build step.
"""

BASE_HEAD = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Reforge Runtime Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script defer src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <style>
    body { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; }
    .pill { display:inline-block; padding:0.1em 0.5em; border-radius:9999px; font-size:0.75em; }
    .ev-EXECUTION_SUCCEEDED { background:#dcfce7; color:#166534; }
    .ev-EXECUTION_FAILED    { background:#fee2e2; color:#991b1b; }
    .ev-RECOVERY_ATTEMPTED  { background:#fef3c7; color:#92400e; }
    .ev-POLICY_DECIDED      { background:#dbeafe; color:#1e40af; }
    .ev-TASK_COMPLETED      { background:#e0e7ff; color:#3730a3; }
    .ev-EVALUATION_COMPLETED{ background:#f3e8ff; color:#6b21a8; }
    .ev-REFLECTION_GENERATED{ background:#ccfbf1; color:#115e59; }
    .ev-EXECUTION_STARTED   { background:#e5e7eb; color:#374151; }
    .out-SUCCESS            { background:#dcfce7; color:#166534; }
    .out-RECOVERED          { background:#fef3c7; color:#92400e; }
    .out-FAILED             { background:#fee2e2; color:#991b1b; }
    .out-DENIED             { background:#fde2e8; color:#9d174d; }
    .out-EXPECTED_FAILURE   { background:#e0e7ff; color:#3730a3; }
  </style>
</head>
"""

NAV = """\
<nav class="bg-slate-900 text-slate-100 px-6 py-3 flex items-center gap-6">
  <a href="/" class="font-bold tracking-tight text-lg">Reforge</a>
  <a href="/" class="hover:underline">Dashboard</a>
  <a href="/skills" class="hover:underline">Skills</a>
  <a href="/memory" class="hover:underline">Memory</a>
  <span class="text-xs text-slate-400 ml-auto">Runtime governance / event-sourced</span>
</nav>
"""

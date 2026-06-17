"""Web dashboard for Reforge runtime observability.

Extends EventLogObserver's JSON+SSE API with HTML pages that visualise:
  - live event stream (SSE)
  - session list + outcome distribution
  - per-session event timeline
  - memory store contents
  - registered skill catalogue (built-in + MCP)

Zero external dependencies — stdlib http.server only. Frontend uses CDN
Tailwind + Alpine.js + Chart.js, no build step.
"""

from reforge.observability.dashboard.server import DashboardServer

__all__ = ["DashboardServer"]

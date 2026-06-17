"""HTML page templates for the Reforge dashboard.

Each page lives in its own module so individual pages stay under the
400-line limit (see CLAUDE.md). The public API is unchanged: callers
import HOME_HTML / SESSION_HTML / MEMORY_HTML / SKILLS_HTML from this
package exactly as before.
"""

from reforge.observability.dashboard.pages.home import HOME_HTML
from reforge.observability.dashboard.pages.memory import MEMORY_HTML
from reforge.observability.dashboard.pages.session import SESSION_HTML
from reforge.observability.dashboard.pages.skills import SKILLS_HTML

__all__ = ["HOME_HTML", "MEMORY_HTML", "SESSION_HTML", "SKILLS_HTML"]

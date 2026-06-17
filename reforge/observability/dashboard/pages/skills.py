"""Skills catalogue page: lists all registered Skill names + their MCP origin."""

from reforge.observability.dashboard.pages._layout import BASE_HEAD, NAV

SKILLS_HTML = (
    BASE_HEAD
    + """\
<body class="bg-slate-50 text-slate-900" x-data="skillsPage()" x-init="init()">
"""
    + NAV
    + """\
  <main class="max-w-6xl mx-auto p-6 space-y-4">
    <header>
      <h1 class="text-xl font-bold">Skill Registry</h1>
      <p class="text-sm text-slate-500">All registered capabilities. <span class="text-violet-700 font-semibold">mcp.*</span> items come from connected MCP servers.</p>
    </header>

    <section class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <template x-for="s in skills" :key="s.name">
        <div class="bg-white rounded-lg shadow p-4">
          <div class="flex items-center gap-2">
            <code class="font-bold" :class="s.is_mcp ? 'text-violet-700' : 'text-indigo-700'" x-text="s.name"></code>
            <span x-show="s.is_mcp" class="pill bg-violet-100 text-violet-800">MCP</span>
          </div>
          <p class="text-sm text-slate-700 mt-1" x-text="s.description"></p>
          <details class="mt-2 text-xs text-slate-500">
            <summary class="cursor-pointer">input schema</summary>
            <pre class="mt-1 p-2 bg-slate-50 rounded overflow-x-auto" x-text="JSON.stringify(s.input_schema, null, 2)"></pre>
          </details>
        </div>
      </template>
    </section>
  </main>

<script>
function skillsPage() {
  return {
    skills: [],
    async init() {
      const data = await fetch('/api/skills').then(r => r.json());
      this.skills = data.skills;
    }
  };
}
</script>
</body></html>
"""
)

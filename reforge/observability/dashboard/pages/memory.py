"""Memory page: Recovery / Failure / Success patterns accumulated across sessions."""

from reforge.observability.dashboard.pages._layout import BASE_HEAD, NAV

MEMORY_HTML = (
    BASE_HEAD
    + """\
<body class="bg-slate-50 text-slate-900" x-data="memoryPage()" x-init="init()">
"""
    + NAV
    + """\
  <main class="max-w-6xl mx-auto p-6 space-y-4">
    <header>
      <h1 class="text-xl font-bold">Memory Substrate</h1>
      <p class="text-sm text-slate-500">Recovery / Failure / Success patterns accumulated across sessions.</p>
    </header>

    <section class="flex gap-2">
      <template x-for="(c, t) in counts" :key="t">
        <button class="px-3 py-1 rounded border text-sm" :class="filter === t ? 'bg-indigo-600 text-white' : 'bg-white'"
                @click="filter = (filter === t ? null : t); refresh()">
          <span x-text="t"></span>: <span x-text="c"></span>
        </button>
      </template>
    </section>

    <section class="bg-white rounded-lg shadow divide-y">
      <template x-for="(r,i) in records" :key="i">
        <div class="p-3 text-sm">
          <div class="flex items-center gap-2 mb-1">
            <span class="pill" :class="'out-' + (r.outcome || '')" x-text="r._memory_type || ''"></span>
            <span class="text-slate-500 text-xs" x-text="r.timestamp || ''"></span>
            <span class="text-slate-500 text-xs ml-auto" x-text="r.error_type || ''"></span>
          </div>
          <div class="font-medium" x-text="r.user_request || r.request || ''"></div>
          <div class="text-xs text-slate-500 mt-1" x-text="r.recovery_action || r.repair_strategy || ''"></div>
        </div>
      </template>
      <div x-show="records.length === 0" class="p-4 italic text-slate-400">No memory records yet.</div>
    </section>
  </main>

<script>
function memoryPage() {
  return {
    records: [],
    counts: {},
    filter: null,
    async init() { await this.refresh(); },
    async refresh() {
      const url = '/api/memory' + (this.filter ? '?type=' + this.filter : '');
      const data = await fetch(url).then(r => r.json());
      this.records = data.records;
      this.counts = data.counts;
    }
  };
}
</script>
</body></html>
"""
)

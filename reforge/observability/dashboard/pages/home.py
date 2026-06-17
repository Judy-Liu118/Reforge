"""Home dashboard page: summary stats, outcome chart, live event stream, sessions."""

from reforge.observability.dashboard.pages._layout import BASE_HEAD, NAV

HOME_HTML = (
    BASE_HEAD
    + """\
<body class="bg-slate-50 text-slate-900" x-data="home()" x-init="init()">
"""
    + NAV
    + """\
  <main class="max-w-7xl mx-auto p-6 space-y-6">

    <!-- top row: summary stat cards + outcome chart -->
    <section class="grid grid-cols-1 md:grid-cols-4 gap-4">
      <div class="bg-white rounded-lg shadow p-4">
        <div class="text-xs uppercase text-slate-500">Sessions</div>
        <div class="text-3xl font-bold" x-text="summary.session_count ?? 0"></div>
      </div>
      <div class="bg-white rounded-lg shadow p-4">
        <div class="text-xs uppercase text-slate-500">Events</div>
        <div class="text-3xl font-bold" x-text="summary.total_events ?? 0"></div>
      </div>
      <div class="bg-white rounded-lg shadow p-4">
        <div class="text-xs uppercase text-slate-500">Skills</div>
        <div class="text-3xl font-bold" x-text="skillCount"></div>
      </div>
      <div class="bg-white rounded-lg shadow p-4 col-span-1 md:col-span-1">
        <div class="text-xs uppercase text-slate-500 mb-1">Live</div>
        <div class="flex items-center gap-2">
          <span class="inline-block w-2 h-2 rounded-full" :class="streamActive ? 'bg-emerald-500 animate-pulse' : 'bg-slate-400'"></span>
          <span class="text-sm" x-text="streamActive ? 'connected' : 'disconnected'"></span>
        </div>
      </div>
    </section>

    <section class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div class="bg-white rounded-lg shadow p-4">
        <h2 class="text-sm uppercase text-slate-500 mb-2">Event type distribution</h2>
        <canvas id="kindChart" height="180"></canvas>
      </div>
      <div class="bg-white rounded-lg shadow p-4">
        <h2 class="text-sm uppercase text-slate-500 mb-2">Live event stream</h2>
        <div class="max-h-64 overflow-y-auto text-xs space-y-1" id="liveLog">
          <template x-for="ev in liveEvents.slice(-50).reverse()" :key="ev._k">
            <div class="flex gap-2 border-b border-slate-100 py-1">
              <span class="text-slate-400 w-20" x-text="ev.timestamp?.slice(11,19) || ''"></span>
              <span class="pill" :class="'ev-' + ev.kind" x-text="ev.kind"></span>
              <span class="text-slate-700 truncate" x-text="(ev.session_id || '').slice(0,8) + ' / ' + summarize(ev)"></span>
            </div>
          </template>
          <div x-show="liveEvents.length === 0" class="text-slate-400 italic">waiting for events...</div>
        </div>
      </div>
    </section>

    <section class="bg-white rounded-lg shadow p-4">
      <h2 class="text-sm uppercase text-slate-500 mb-3">Sessions</h2>
      <table class="w-full text-sm">
        <thead class="text-xs uppercase text-slate-500 border-b">
          <tr>
            <th class="text-left py-2">Session</th>
            <th class="text-left">Events</th>
            <th class="text-left">Last activity</th>
          </tr>
        </thead>
        <tbody>
          <template x-for="s in sessions" :key="s.id">
            <tr class="border-b border-slate-100 hover:bg-slate-50">
              <td class="py-1.5"><a class="text-indigo-600 hover:underline" :href="'/sessions/' + s.id" x-text="s.id"></a></td>
              <td x-text="s.events"></td>
              <td x-text="s.last"></td>
            </tr>
          </template>
          <tr x-show="sessions.length === 0">
            <td colspan="3" class="py-3 italic text-slate-400">No sessions yet.</td>
          </tr>
        </tbody>
      </table>
    </section>
  </main>

<script>
function summarize(ev) {
  const p = ev.payload || {};
  switch (ev.kind) {
    case 'EXECUTION_STARTED':    return (p.task || '').slice(0, 80);
    case 'EXECUTION_SUCCEEDED':  return (p.output_summary || p.task || '').slice(0, 80);
    case 'EXECUTION_FAILED':     return (p.error || p.category || '').slice(0, 80);
    case 'RECOVERY_ATTEMPTED':   return `#${p.attempt ?? '?'} ${p.strategy || ''}`;
    case 'EVALUATION_COMPLETED': return `score=${(p.score ?? 0).toFixed(2)} ${p.passed ? 'pass' : 'fail'}`;
    case 'REFLECTION_GENERATED': return (p.summary || '').slice(0, 80);
    case 'POLICY_DECIDED':       return `${p.decision || ''} / ${p.reason || ''}`;
    case 'TASK_COMPLETED':       return `${p.outcome || ''} / ${p.reason || ''}`;
    default: return JSON.stringify(p).slice(0, 80);
  }
}

function home() {
  return {
    summary: {},
    sessions: [],
    skillCount: 0,
    streamActive: false,
    liveEvents: [],
    chart: null,
    init() {
      this.refresh();
      setInterval(() => this.refresh(), 5000);
      this.connectStream();
    },
    async refresh() {
      const [sum, sess, skills] = await Promise.all([
        fetch('/api/summary').then(r => r.json()),
        fetch('/api/sessions').then(r => r.json()),
        fetch('/api/skills').then(r => r.json()),
      ]);
      this.summary = sum;
      this.skillCount = skills.count;
      this.sessions = sess.map(id => {
        const evs = this.liveEvents.filter(e => e.session_id === id);
        return {
          id,
          events: '-',
          last: evs.length ? (evs[evs.length-1].timestamp || '').slice(0,19) : '-',
        };
      });
      // Per-session counts via a second pass
      this.sessions.forEach(async s => {
        const evs = await fetch('/api/events?session_id=' + encodeURIComponent(s.id)).then(r => r.json());
        s.events = evs.length;
        if (evs.length) s.last = (evs[evs.length-1].timestamp || '').slice(0,19);
      });
      this.renderChart(sum.by_kind || {});
    },
    renderChart(byKind) {
      const labels = Object.keys(byKind);
      const data = Object.values(byKind);
      const ctx = document.getElementById('kindChart');
      if (!ctx) return;
      if (this.chart) { this.chart.destroy(); }
      this.chart = new Chart(ctx, {
        type: 'doughnut',
        data: { labels, datasets: [{ data,
          backgroundColor: ['#10b981','#ef4444','#f59e0b','#3b82f6','#6366f1','#8b5cf6','#14b8a6','#94a3b8']
        }]},
        options: { plugins: { legend: { position: 'right', labels: { font: { size: 10 } } } } }
      });
    },
    connectStream() {
      const es = new EventSource('/api/events/stream');
      es.onopen = () => { this.streamActive = true; };
      es.onerror = () => { this.streamActive = false; };
      let k = 0;
      es.onmessage = (e) => {
        try {
          const ev = JSON.parse(e.data);
          ev._k = ++k;
          this.liveEvents.push(ev);
          if (this.liveEvents.length > 200) this.liveEvents = this.liveEvents.slice(-200);
        } catch {}
      };
    }
  };
}
</script>
</body></html>
"""
)

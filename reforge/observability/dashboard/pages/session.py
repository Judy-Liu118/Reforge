"""Session detail page: timeline + retry trace + policy decisions + raw events.

Three projections off the same `events` array (no new API calls):

  - retryTimeline    — pair each EXECUTION_FAILED with the RECOVERY_ATTEMPTED
                       that links to it via parent_event_id, plus the next
                       EXECUTION_SUCCEEDED/_FAILED outcome of that attempt.
                       Surfaces the self-healing loop as one horizontal strip.
  - policyDecisions  — every POLICY_DECIDED event with decision + reason,
                       in time order. The governor's voice as a flat trace.
  - events           — raw event table (kept verbatim from before for parity).
"""

from reforge.observability.dashboard.pages._layout import BASE_HEAD, NAV

SESSION_HTML = (
    BASE_HEAD
    + """\
<body class="bg-slate-50 text-slate-900" x-data="sessionPage()" x-init="init()">
"""
    + NAV
    + """\
  <main class="max-w-6xl mx-auto p-6 space-y-6">
    <header class="flex items-baseline gap-4">
      <h1 class="text-xl font-bold">Session <span x-text="sessionId" class="font-mono"></span></h1>
      <span class="text-xs text-slate-500" x-show="traceId">trace <span class="font-mono" x-text="traceId"></span></span>
      <a href="/" class="ml-auto text-sm text-indigo-600 hover:underline">back</a>
    </header>

    <!-- Retry timeline -->
    <section class="bg-white rounded-lg shadow p-4">
      <div class="flex items-baseline justify-between mb-3">
        <h2 class="text-sm font-semibold tracking-tight text-slate-700">Retry timeline</h2>
        <span class="text-xs text-slate-400" x-text="retryTimeline.length + ' attempt(s)'"></span>
      </div>
      <div x-show="retryTimeline.length === 0" class="text-xs italic text-slate-400">
        No self-healing recovery attempts in this session.
      </div>
      <ol x-show="retryTimeline.length" class="flex flex-wrap gap-3">
        <template x-for="(step, i) in retryTimeline" :key="i">
          <li class="border border-slate-200 rounded-md p-3 min-w-[14rem] bg-slate-50">
            <div class="flex items-baseline justify-between">
              <span class="text-xs font-semibold text-slate-500">attempt #<span x-text="step.attempt"></span></span>
              <span class="pill" :class="'out-' + step.outcomeStyle" x-text="step.outcome"></span>
            </div>
            <div class="mt-1 text-xs">
              <div><span class="text-slate-400">cause:</span> <span x-text="step.failureCategory"></span></div>
              <div><span class="text-slate-400">strategy:</span> <span x-text="step.strategy"></span></div>
              <div class="text-slate-500 truncate" :title="step.error" x-text="step.error"></div>
            </div>
          </li>
        </template>
      </ol>
    </section>

    <!-- Policy decision trace -->
    <section class="bg-white rounded-lg shadow p-4">
      <div class="flex items-baseline justify-between mb-3">
        <h2 class="text-sm font-semibold tracking-tight text-slate-700">Policy decision trace</h2>
        <span class="text-xs text-slate-400" x-text="policyDecisions.length + ' decision(s)'"></span>
      </div>
      <div x-show="policyDecisions.length === 0" class="text-xs italic text-slate-400">
        Governor produced no POLICY_DECIDED events for this session.
      </div>
      <ol x-show="policyDecisions.length" class="space-y-2">
        <template x-for="(d, i) in policyDecisions" :key="i">
          <li class="flex items-baseline gap-3 text-sm">
            <span class="text-xs text-slate-400 w-20 shrink-0" x-text="(d.timestamp || '').slice(11,19)"></span>
            <span class="pill" :class="decisionStyle(d.decision)" x-text="d.decision"></span>
            <span class="text-slate-700" x-text="d.reason"></span>
          </li>
        </template>
      </ol>
    </section>

    <!-- Raw event table -->
    <section class="bg-white rounded-lg shadow">
      <div class="px-4 pt-4 pb-2 flex items-baseline justify-between">
        <h2 class="text-sm font-semibold tracking-tight text-slate-700">Raw events</h2>
        <span class="text-xs text-slate-400" x-text="events.length + ' event(s)'"></span>
      </div>
      <table class="w-full text-sm">
        <thead class="text-xs uppercase text-slate-500 border-b">
          <tr>
            <th class="text-left p-2 w-44">Timestamp</th>
            <th class="text-left w-56">Kind</th>
            <th class="text-left">Summary</th>
          </tr>
        </thead>
        <tbody>
          <template x-for="(ev,i) in events" :key="i">
            <tr class="border-b border-slate-100 align-top">
              <td class="p-2 text-slate-500 text-xs" x-text="(ev.timestamp || '').slice(0,19)"></td>
              <td class="p-2"><span class="pill" :class="'ev-' + ev.kind" x-text="ev.kind"></span></td>
              <td class="p-2 whitespace-pre-wrap" x-text="summarize(ev) || JSON.stringify(ev.payload, null, 2)"></td>
            </tr>
          </template>
          <tr x-show="events.length === 0">
            <td colspan="3" class="p-4 italic text-slate-400">No events for this session.</td>
          </tr>
        </tbody>
      </table>
    </section>
  </main>

<script>
function summarize(ev) {
  const p = ev.payload || {};
  switch (ev.kind) {
    case 'EXECUTION_STARTED':    return p.task || '';
    case 'EXECUTION_SUCCEEDED':  return p.output_summary || p.task || '';
    case 'EXECUTION_FAILED':     return `${p.category || ''}: ${p.error || ''}`;
    case 'RECOVERY_ATTEMPTED':   return `attempt #${p.attempt ?? '?'} via ${p.strategy || ''}`;
    case 'EVALUATION_COMPLETED': return `score=${(p.score ?? 0).toFixed(2)} ${p.passed ? 'pass' : 'fail'}`;
    case 'REFLECTION_GENERATED': return p.summary || '';
    case 'POLICY_DECIDED':       return `${p.decision || ''} / ${p.reason || ''}`;
    case 'TASK_COMPLETED':       return `${p.outcome || ''} / ${p.reason || ''}`;
    default: return JSON.stringify(p);
  }
}

function sessionPage() {
  return {
    sessionId: location.pathname.replace(/^\\/sessions\\//, ''),
    events: [],
    retryTimeline: [],
    policyDecisions: [],
    traceId: '',
    async init() {
      this.events = await fetch('/api/events?session_id=' + encodeURIComponent(this.sessionId)).then(r => r.json());
      this.traceId = (this.events.find(e => e.trace_id) || {}).trace_id || '';
      this.retryTimeline = buildRetryTimeline(this.events);
      this.policyDecisions = this.events.filter(e => e.kind === 'POLICY_DECIDED').map(e => ({
        timestamp: e.timestamp,
        decision: (e.payload || {}).decision || '',
        reason: (e.payload || {}).reason || '',
      }));
    },
    decisionStyle(d) {
      const m = {
        'RETRY':  'out-RECOVERED',
        'ACCEPT': 'out-SUCCESS',
        'STOP':   'out-FAILED',
        'DENY':   'out-DENIED',
      };
      return m[d] || 'ev-POLICY_DECIDED';
    }
  };
}

function buildRetryTimeline(events) {
  // Pair RECOVERY_ATTEMPTED with the EXECUTION_FAILED it links to via
  // parent_event_id, and look up the next outcome (EXECUTION_SUCCEEDED or
  // a subsequent EXECUTION_FAILED) by time so we can label each step.
  const byId = {};
  events.forEach(e => { byId[e.event_id] = e; });

  const steps = [];
  events.forEach((e, idx) => {
    if (e.kind !== 'RECOVERY_ATTEMPTED') return;
    const cause = e.parent_event_id ? byId[e.parent_event_id] : null;
    let outcome = 'PENDING';
    let outcomeStyle = 'RECOVERED';
    let err = '';
    for (let j = idx + 1; j < events.length; j++) {
      const nx = events[j];
      if (nx.kind === 'EXECUTION_SUCCEEDED') {
        outcome = 'SUCCESS'; outcomeStyle = 'SUCCESS'; break;
      }
      if (nx.kind === 'EXECUTION_FAILED') {
        outcome = 'FAILED'; outcomeStyle = 'FAILED';
        err = ((nx.payload || {}).error) || '';
        break;
      }
      if (nx.kind === 'TASK_COMPLETED') {
        const o = (nx.payload || {}).outcome || '';
        outcome = o || 'DONE'; outcomeStyle = o || 'RECOVERED'; break;
      }
    }
    const p = e.payload || {};
    steps.push({
      attempt: p.attempt ?? '?',
      strategy: p.strategy || '(unspecified)',
      failureCategory: cause ? (cause.payload || {}).category || 'unknown' : 'unknown',
      error: err || (cause ? (cause.payload || {}).error || '' : ''),
      outcome,
      outcomeStyle,
    });
  });
  return steps;
}
</script>
</body></html>
"""
)

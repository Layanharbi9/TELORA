/* Static demo build of TELORA's api.js.
   Same interface as the real client, but every answer comes from demo-data.json:
   responses recorded from TELORA's FastAPI service and SQLite catalog. Builds replay
   a recorded agent run whose needs best match the customer's choice; nothing calls
   OpenAI and nothing leaves the browser. Changes (apply, cancel) live in memory only. */
const api = (() => {
  const data = fetch('js/demo-data.json').then(r => r.json());
  let cid = null, seq = 0;
  const local = {};            // customer_id -> {plan, requests} after demo changes
  const runOf = {};            // demo request_id -> recorded run id
  const clone = v => JSON.parse(JSON.stringify(v));
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const now = () => new Date().toISOString();

  async function session() {
    const d = await data;
    if (!cid) throw new Error('Choose a customer to start the demo session');
    if (!local[cid]) local[cid] = { plan: clone(d.plan[cid]), requests: clone(d.requests[cid]) };
    return { d, s: local[cid] };
  }

  function note(text) { window.dispatchEvent(new CustomEvent('telora-demo-note', { detail: text })); }

  /* score each recorded run against the chosen needs, levels and budget */
  function bestRun(d, req) {
    const want = [...req.needs].sort((a, b) => a.priority - b.priority);
    const norm = s => String(s || '').toLowerCase();
    let best = null, top = -Infinity;
    for (const [id, run] of Object.entries(d.runs)) {
      let score = 0;
      want.forEach((w, i) => {
        const same = run.needs[i], any = run.needs.find(n => norm(n.need) === norm(w.need));
        if (same && norm(same.need) === norm(w.need)) score += 3; else if (any) score += 2;
        if (any && norm(any.level) === norm(w.level)) score += 1;
      });
      if (run.budget && req.preferred_budget_sar) score -= Math.min(2, Math.abs(run.budget - req.preferred_budget_sar) / 150);
      if (run.customer_id === cid) score += 0.3;
      if (score > top) { top = score; best = id; }
    }
    return best;
  }

  function planFrom(d, sol, prev, requestId) {
    const p = d.packages[sol.base_package.package_id] || {};
    const addons = sol.addons.map(a => d.addons[a.addon_id]).filter(Boolean);
    return {
      base_package: {
        package_id: p.package_id, package_name: p.package_name, monthly_price_sar: p.monthly_price_sar,
        data_gb: p.data_gb, voice_minutes: p.voice_minutes, sms: p.sms, social_data_gb: p.social_data_gb,
        streaming_benefit: p.streaming_benefit, gaming_benefit: p.gaming_benefit, roaming_data_gb: p.roaming_data_gb,
        international_minutes: p.international_minutes, multi_sim: p.multi_sim, data_sharing: p.data_sharing,
        included_benefits: [p.streaming_benefit, p.gaming_benefit].filter(v => v && v !== 'None')
      },
      active_addons: clone(addons),
      total_monthly_price: sol.total_monthly_price,
      updated_by: { request_id: requestId, applied_at: now(),
        previous_plan: { name: prev.base_package.package_name, addons: (prev.active_addons || []).map(a => a.addon_name),
          total_price: prev.total_monthly_price } },
      updated_at: now()
    };
  }
  const summary = plan => plan && ({ name: plan.base_package.package_name, package_ids: [plan.base_package.package_id],
    addon_names: (plan.active_addons || []).map(a => a.addon_name), total_price: plan.total_monthly_price });

  /* replay a run as the same SSE events the real service sends */
  function replay(request_id, run, onEvent, animate) {
    let stopped = false;
    (async () => {
      const steps = animate
        ? [[700, 'understanding'], [1500, 'finding'], [1300, 'checking'], [1800, 'building']]
        : [[0, 'finding'], [0, 'checking'], [0, 'building']];
      for (let i = 0; i < steps.length; i++) {
        const [ms, stage] = steps[i];
        if (ms) await wait(ms);
        if (stopped) return;
        onEvent({ request_id, stage, status: 'done' });
        const next = steps[i + 1];
        if (next && animate) onEvent({ request_id, stage: next[1], status: 'active' });
      }
      if (stopped) return;
      onEvent({ request_id, stage: 'done', status: 'done',
        data: { solutions: clone(run.solutions), recommendation_available: run.recommendation_available } });
    })();
    return { cancel() { stopped = true; } };
  }

  return {
    async listDemoCustomers() { return (await data).customers; },
    async startDemoSession(customer_id) { cid = customer_id; return { customer_id }; },
    async endSession() { cid = null; note(null); return null; },
    async me() { const { d } = await session(); return d.me[cid]; },
    async myPlan() { const { s } = await session(); return clone(s.plan); },
    async myRequests() { const { s } = await session(); return clone(s.requests); },
    async adminOverview() { return (await data).admin.overview; },
    async adminRequests() { return (await data).admin.requests; },
    async adminRequest(id) { return (await data).admin.request[id]; },
    async adminCustomers() { return (await data).admin.customers; },
    async adminCustomer(id) { return (await data).admin.customer[id]; },
    async adminAgents() { return (await data).admin.agents; },
    async adminCatalog() { return (await data).admin.catalog; },
    async adminSystem() {
      return { configured: false, reason: 'Speed and cost are read live from LangSmith, so they are not part of this static demo.' };
    },

    build(requirements, onEvent) {
      let handle = null, cancelled = false;
      onEvent({ request_id: null, stage: 'understanding', status: 'active' });
      (async () => {
        const { d, s } = await session();
        const runId = bestRun(d, requirements), run = d.runs[runId];
        const request_id = `REQ-DEMO-${String(++seq).padStart(3, '0')}`;
        runOf[request_id] = runId;
        s.requests.unshift({ request_id, created_at: now(), updated_at: now(), status: 'awaiting_selection',
          business_status: null, solutions_count: run.solutions.length,
          needs: [...requirements.needs].sort((a, b) => a.priority - b.priority),
          budget: requirements.preferred_budget_sar, previous_plan: null, new_plan: null, order_id: null, error_message: null });
        note('Replaying a recorded TELORA run for: ' + run.needs.map(n => `${n.need} (${n.level})`).join(' · ') +
          (run.budget ? ` · ${run.budget} SAR budget` : ''));
        if (!cancelled) handle = replay(request_id, run, onEvent, true);
      })();
      return { cancel() { cancelled = true; if (handle) handle.cancel(); } };
    },

    reopen(request_id, onEvent) {
      let handle = { cancel() {} };
      (async () => {
        const { d, s } = await session();
        const r = s.requests.find(x => x.request_id === request_id);
        const run = d.runs[runOf[request_id] || request_id];
        if (r && r.status === 'awaiting_selection' && run) handle = replay(request_id, run, onEvent, false);
        else onEvent({ request_id, stage: 'done', status: r ? r.status : 'completed' });
      })();
      return { cancel() { handle.cancel(); } };
    },

    async apply(request_id, solution_id) {
      const { d, s } = await session();
      const run = d.runs[runOf[request_id] || request_id];
      const sol = run && run.solutions.find(x => x.solution_id === solution_id);
      if (!sol) throw new Error('Selected solution is not part of this request');
      await wait(600);
      const before = s.plan;
      s.plan = planFrom(d, sol, before, request_id);
      const r = s.requests.find(x => x.request_id === request_id);
      const order_id = `ORD-DEMO-${String(seq).padStart(3, '0')}`;
      if (r) Object.assign(r, { status: 'completed', updated_at: now(), previous_plan: summary(before),
        new_plan: summary(s.plan), order_id });
      note(null);
      return { success: true, order_id, request_id, status: 'ACTIVE', plan: clone(s.plan) };
    },

    async cancelRequest(request_id) {
      const { s } = await session();
      const r = s.requests.find(x => x.request_id === request_id);
      if (!r || r.status !== 'awaiting_selection') throw new Error('This request can no longer be cancelled.');
      Object.assign(r, { status: 'cancelled', updated_at: now() });
      return { success: true, request_id, status: 'cancelled', changed: true };
    }
  };
})();

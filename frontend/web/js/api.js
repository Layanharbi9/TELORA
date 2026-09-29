/* The only browser module that calls TELORA's FastAPI service. */
const api = (() => {
  async function request(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options
    });
    const body = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) throw new Error(body?.detail || `TELORA request failed (${response.status})`);
    return body;
  }

  /* Follow a persisted request's SSE stream; for a request already waiting for
     selection it replays the stored solutions at once (nothing is regenerated). */
  function follow(request_id, onEvent) {
    const source = new EventSource(`/api/build/${encodeURIComponent(request_id)}/events`, { withCredentials: true });
    source.onmessage = event => {
      try {
        const payload = JSON.parse(event.data);
        onEvent(payload);
        if (payload.stage === 'done' || payload.status === 'failed') source.close();
      } catch (error) {
        source.close();
        onEvent({ request_id, stage: 'building', status: 'failed', category: 'connection', message: error.message });
      }
    };
    source.onerror = () => {
      source.close();
      onEvent({ request_id, stage: 'building', status: 'failed', category: 'connection',
        message: 'The connection to TELORA was interrupted. Check Requests before retrying.' });
    };
    return source;
  }

  return {
    async listDemoCustomers() { return request('/api/demo/customers'); },
    async startDemoSession(customer_id) {
      return request('/api/demo/session', { method: 'POST', body: JSON.stringify({ customer_id }) });
    },
    async endSession() { return request('/api/session/end', { method: 'POST' }); },
    async me() { return request('/api/me'); },
    async myPlan() { return request('/api/me/plan'); },
    async myRequests() { return request('/api/me/requests'); },
    async adminOverview() { return request('/api/admin/overview'); },
    async adminRequests() { return request('/api/admin/requests'); },
    async adminRequest(id) { return request(`/api/admin/requests/${encodeURIComponent(id)}`); },
    async adminCustomers() { return request('/api/admin/customers'); },
    async adminCustomer(id) { return request(`/api/admin/customers/${encodeURIComponent(id)}`); },
    async adminAgents() { return request('/api/admin/agents'); },
    async adminCatalog() { return request('/api/admin/catalog'); },
    async adminSystem(days, refresh) { return request(`/api/admin/system?days=${days}${refresh ? '&refresh=true' : ''}`); },

    build(requirements, onEvent) {
      let source = null;
      let cancelled = false;
      (async () => {
        try {
          onEvent({ request_id: null, stage: 'understanding', status: 'active' });
          const { request_id } = await request('/api/build', {
            method: 'POST', body: JSON.stringify(requirements)
          });
          if (cancelled) return;
          source = follow(request_id, onEvent);
        } catch (error) {
          onEvent({ request_id: null, stage: 'understanding', status: 'failed', message: error.message });
        }
      })();
      return { cancel() { cancelled = true; if (source) source.close(); } };
    },

    /* Reopen an existing request's solutions (View solutions). Closing it never cancels the request. */
    reopen(request_id, onEvent) {
      const source = follow(request_id, onEvent);
      return { cancel() { source.close(); } };
    },

    async apply(request_id, solution_id) {
      return request(`/api/requests/${encodeURIComponent(request_id)}/apply`, {
        method: 'POST', body: JSON.stringify({ solution_id })
      });
    },

    async cancelRequest(request_id) {
      return request(`/api/requests/${encodeURIComponent(request_id)}/cancel`, { method: 'POST' });
    }
  };
})();

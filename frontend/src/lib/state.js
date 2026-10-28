/** Only server snapshots live here. Demonstration data has a separate explicit entry point. */
export function emptySnapshot() {
  return {
    projects: [],
    tasks: [],
    machines: [],
    agents: [],
    messages: [],
    events: [],
    artifacts: [],
    storage: { used: 0, quota: 5e9 },
  };
}

/** A failed response must not dispatch the same unchanged request twice. */
export function createSubmissionQueue(send, makeId) {
  const pending = new Map();
  return {
    async submit(path, body) {
      const key = JSON.stringify([path, body]);
      if (!pending.has(key)) pending.set(key, makeId());
      const result = await send(path, { ...body, client_request_id: pending.get(key) });
      pending.delete(key);
      return result;
    },
    clear() {
      pending.clear();
    },
  };
}

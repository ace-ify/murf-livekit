'use client';

import { useCallback, useEffect, useState } from 'react';

// Day 7 — the human's view of open escalations.
// SECURITY: this GET is unauthenticated by design for local dev. It lists health
// complaints, so do not deploy this page publicly without real auth. Writes (PATCH)
// require ADMIN_TOKEN and fail closed.
interface Escalation {
  id: number;
  ref: string;
  caller_user_id: string;
  caller_name: string;
  language: string;
  urgency: 'low' | 'medium' | 'high' | 'emergency';
  what_happened: string;
  already_checked: string;
  followup_method: string;
  callback_phone: string;
  status: 'open' | 'acknowledged' | 'resolved';
  resolution_note: string;
  created_at: string;
  updated_at: string;
}

const URGENCY_STYLE: Record<string, string> = {
  emergency: 'bg-rose-600 text-white',
  high: 'bg-orange-500 text-white',
  medium: 'bg-amber-400 text-amber-950',
  low: 'bg-slate-300 text-slate-800',
};

const TABS = ['open', 'acknowledged', 'resolved', ''] as const;

export default function AdminPage() {
  const [status, setStatus] = useState<string>('open');
  const [rows, setRows] = useState<Escalation[]>([]);
  const [token, setToken] = useState('');
  const [msg, setMsg] = useState('');

  useEffect(() => setToken(localStorage.getItem('careva_admin_token') ?? ''), []);

  const load = useCallback(async () => {
    const res = await fetch(`/api/escalations?status=${status}`, { cache: 'no-store' });
    const data = await res.json();
    setRows(data.escalations ?? []);
  }, [status]);

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  const patch = async (esc: Escalation, next: string) => {
    const note =
      next === 'resolved' ? (prompt(`What did you do for ${esc.ref}?`) ?? '') : esc.resolution_note;
    localStorage.setItem('careva_admin_token', token);
    const res = await fetch('/api/escalations', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'x-admin-token': token },
      body: JSON.stringify({ ref: esc.ref, status: next, note }),
    });
    const data = await res.json();
    setMsg(res.ok ? `${esc.ref} → ${next}` : `Failed: ${data.error ?? res.status}`);
    load();
  };

  return (
    <main className="mx-auto max-w-5xl p-6 font-sans text-slate-900 dark:text-slate-100">
      <h1 className="text-2xl font-black">Careva — human help queue</h1>
      <p className="mt-1 text-sm text-slate-500">
        Escalations raised by the voice agent after the caller gave permission. Summaries are
        PII-scrubbed. Never promise a caller an instant reply.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {TABS.map((t) => (
          <button
            key={t || 'all'}
            onClick={() => setStatus(t)}
            className={`rounded-lg px-3 py-1.5 text-xs font-bold ${
              status === t
                ? 'bg-slate-900 text-white dark:bg-white dark:text-slate-900'
                : 'bg-slate-200 dark:bg-slate-800'
            }`}
          >
            {t || 'all'}
          </button>
        ))}
        <input
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="ADMIN_TOKEN (needed to change status)"
          className="ml-auto w-64 rounded-lg border border-slate-300 px-2 py-1.5 text-xs dark:border-slate-700 dark:bg-slate-900"
        />
      </div>

      {msg && <p className="mt-3 text-xs font-bold text-sky-600">{msg}</p>}

      <div className="mt-4 flex flex-col gap-3">
        {rows.length === 0 && (
          <p className="rounded-xl border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500 dark:border-slate-700">
            No {status || ''} escalations. A normal conversation should not create one.
          </p>
        )}

        {rows.map((r) => (
          <article
            key={r.id}
            className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900"
          >
            <header className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-sm font-black">{r.ref}</span>
              <span
                className={`rounded-full px-2 py-0.5 text-[10px] font-black uppercase ${URGENCY_STYLE[r.urgency] ?? URGENCY_STYLE.low}`}
              >
                {r.urgency}
              </span>
              <span className="rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-bold uppercase dark:bg-slate-800">
                {r.status}
              </span>
              <span className="text-xs text-slate-500">
                {r.caller_name || 'unknown'} · {r.caller_user_id} · {r.language}
              </span>
              <span className="ml-auto text-[11px] text-slate-400">{r.created_at}</span>
            </header>

            <p className="mt-2 text-sm">{r.what_happened}</p>
            <dl className="mt-2 grid gap-1 text-xs text-slate-600 sm:grid-cols-2 dark:text-slate-400">
              <div>
                <dt className="inline font-bold">Agent already checked: </dt>
                <dd className="inline">{r.already_checked || '—'}</dd>
              </div>
              <div>
                <dt className="inline font-bold">Preferred follow-up: </dt>
                <dd className="inline">{r.followup_method || '—'}</dd>
              </div>
              {r.resolution_note && (
                <div className="sm:col-span-2">
                  <dt className="inline font-bold">Resolution: </dt>
                  <dd className="inline">{r.resolution_note}</dd>
                </div>
              )}
            </dl>

            <div className="mt-3 flex flex-wrap items-center gap-2">
              {r.status === 'open' && (
                <button
                  onClick={() => patch(r, 'acknowledged')}
                  className="rounded-lg bg-amber-500 px-3 py-1.5 text-xs font-bold text-white"
                >
                  Acknowledge
                </button>
              )}
              {r.status !== 'resolved' && (
                <button
                  onClick={() => patch(r, 'resolved')}
                  className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white"
                >
                  Resolve
                </button>
              )}
              <code className="ml-auto rounded bg-slate-100 px-2 py-1 text-[10px] text-slate-600 dark:bg-slate-800 dark:text-slate-400">
                uv run src/escalations.py resolve {r.ref} --note &quot;...&quot; --call
              </code>
            </div>
          </article>
        ))}
      </div>
    </main>
  );
}

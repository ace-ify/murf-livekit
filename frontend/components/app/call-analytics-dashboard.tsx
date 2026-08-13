'use client';

import React, { useCallback, useEffect, useState } from 'react';
import {
  Activity,
  CheckCircle2,
  Clock,
  PhoneCall,
  PhoneMissed,
  PhoneOff,
  RefreshCw,
  TrendingUp,
  Wifi,
  XCircle,
} from 'lucide-react';
import { cn } from '@/lib/shadcn/utils';

interface CallStats {
  total: number;
  successful: number;
  failed: number;
  no_answer: number;
  in_progress: number;
  avg_duration_secs: number;
  success_rate: number;
}

interface FailureBreakdown {
  outcome_reason: string;
  cnt: number;
}

interface CallRecord {
  id: number;
  channel: 'browser' | 'sip';
  started_at: string;
  ended_at: string | null;
  duration_secs: number | null;
  outcome: 'success' | 'failed' | 'no_answer' | 'in_progress';
  outcome_reason: string;
  escalation_created: number;
  user_turns: number;
  agent_turns: number;
}

interface ApiResponse {
  stats: CallStats;
  failure_breakdown: FailureBreakdown[];
  recent_calls: CallRecord[];
}

const EMPTY_STATS: CallStats = {
  total: 0, successful: 0, failed: 0, no_answer: 0,
  in_progress: 0, avg_duration_secs: 0, success_rate: 0,
};

function formatDuration(secs: number | null): string {
  if (!secs || secs < 1) return '—';
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return iso;
  }
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  } catch {
    return '';
  }
}

function humanReason(reason: string): string {
  const map: Record<string, string> = {
    escalation_created: 'Escalated to worker',
    conversation_completed: 'Triage completed',
    user_declined_early: 'Caller hung up',
    silent_disconnect: 'No response',
    outbound_not_answered: 'Not answered',
  };
  return map[reason] ?? reason.replace(/_/g, ' ');
}

const OUTCOME_CONFIG = {
  success: { label: 'Success', cls: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/70 dark:text-emerald-300' },
  failed: { label: 'Failed', cls: 'bg-rose-100 text-rose-800 dark:bg-rose-900/70 dark:text-rose-300' },
  no_answer: { label: 'No answer', cls: 'bg-amber-100 text-amber-800 dark:bg-amber-900/70 dark:text-amber-300' },
  in_progress: { label: 'Live', cls: 'bg-sky-100 text-sky-800 dark:bg-sky-900/70 dark:text-sky-300 animate-pulse' },
};

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
  color,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: number | string;
  sub?: string;
  color: string;
}) {
  return (
    <div className="clay-card flex flex-col gap-2 p-4">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-black uppercase tracking-wider text-slate-500 dark:text-slate-400">
          {label}
        </span>
        <div className={cn('flex size-7 items-center justify-center rounded-xl', color)}>
          <Icon className="size-3.5" />
        </div>
      </div>
      <span className="text-3xl font-black tabular-nums text-slate-900 dark:text-white">{value}</span>
      {sub && <span className="text-[10px] font-semibold text-slate-400">{sub}</span>}
    </div>
  );
}

export function CallAnalyticsDashboard() {
  const [data, setData] = useState<ApiResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch('/api/calls');
      if (res.ok) {
        const json = (await res.json()) as ApiResponse;
        setData(json);
        setLastUpdated(new Date());
      }
    } catch {
      // silently degrade — show whatever state we have
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 15_000);
    return () => clearInterval(id);
  }, [fetchData]);

  const stats = data?.stats ?? EMPTY_STATS;
  const calls = data?.recent_calls ?? [];
  const failures = data?.failure_breakdown ?? [];

  const successPct = stats.success_rate;
  const failedPct = stats.total > 0 ? Math.round(((stats.failed + stats.no_answer) / (stats.total - stats.in_progress || 1)) * 100) : 0;

  return (
    <section className="clay-card flex flex-1 flex-col gap-4 overflow-y-auto p-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <Activity className="size-4 stroke-[2.5] text-sky-500" />
          <h2 className="text-sm font-black uppercase tracking-wide text-slate-900 dark:text-white">
            Call Analytics
          </h2>
          <span className="clay-pill bg-white/80 px-2 py-0.5 font-mono text-[9px] font-bold text-slate-500 dark:bg-slate-800 dark:text-slate-400">
            Careva · Health Access
          </span>
        </div>
        <div className="flex items-center gap-2">
          {lastUpdated && (
            <span className="font-mono text-[10px] text-slate-400">
              {lastUpdated.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
          <button
            onClick={fetchData}
            disabled={loading}
            className="clay-btn flex size-7 items-center justify-center rounded-xl text-slate-500 transition-all hover:text-slate-900 disabled:opacity-40 dark:hover:text-white"
            title="Refresh"
          >
            <RefreshCw className={cn('size-3.5', loading && 'animate-spin')} />
          </button>
        </div>
      </div>

      {/* Success definition notice */}
      <div className="clay-card-flat rounded-2xl border border-emerald-200/60 bg-emerald-50/60 px-4 py-2.5 text-[11px] font-medium text-emerald-800 dark:border-emerald-800/40 dark:bg-emerald-950/40 dark:text-emerald-300">
        <span className="font-black">Success = </span>caller received triage guidance or was escalated to a human health worker
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          icon={PhoneCall}
          label="Total Calls"
          value={stats.total}
          sub={stats.in_progress > 0 ? `${stats.in_progress} live` : `avg ${formatDuration(stats.avg_duration_secs)}`}
          color="bg-sky-100 text-sky-700 dark:bg-sky-900/60 dark:text-sky-300"
        />
        <StatCard
          icon={CheckCircle2}
          label="Successful"
          value={stats.successful}
          sub={`${successPct}% success rate`}
          color="bg-emerald-100 text-emerald-700 dark:bg-emerald-900/60 dark:text-emerald-300"
        />
        <StatCard
          icon={XCircle}
          label="Failed"
          value={stats.failed}
          sub="caller left early"
          color="bg-rose-100 text-rose-700 dark:bg-rose-900/60 dark:text-rose-300"
        />
        <StatCard
          icon={PhoneOff}
          label="No Answer"
          value={stats.no_answer}
          sub="silent or outbound"
          color="bg-amber-100 text-amber-700 dark:bg-amber-900/60 dark:text-amber-300"
        />
      </div>

      {/* Success rate bar + failure breakdown */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {/* Success rate bar */}
        <div className="clay-card p-4">
          <div className="mb-3 flex items-center justify-between">
            <span className="text-[10px] font-black uppercase tracking-wider text-slate-500">Success Rate</span>
            <span className="font-mono text-xl font-black text-emerald-600 dark:text-emerald-400">{successPct}%</span>
          </div>
          <div className="h-3 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
            <div
              className="h-full rounded-full bg-gradient-to-r from-emerald-400 to-teal-500 transition-all duration-700"
              style={{ width: `${successPct}%` }}
            />
          </div>
          <div className="mt-2 flex justify-between text-[10px] font-semibold text-slate-400">
            <span>0%</span>
            <span>100%</span>
          </div>
          {stats.avg_duration_secs > 0 && (
            <div className="mt-3 flex items-center gap-1.5 text-[11px] text-slate-500">
              <Clock className="size-3 shrink-0" />
              <span>Avg duration: <strong className="text-slate-700 dark:text-slate-300">{formatDuration(stats.avg_duration_secs)}</strong></span>
            </div>
          )}
        </div>

        {/* Failure type breakdown */}
        <div className="clay-card p-4">
          <span className="mb-3 block text-[10px] font-black uppercase tracking-wider text-slate-500">
            Failure Breakdown
          </span>
          {failures.length === 0 ? (
            <div className="flex h-full min-h-[60px] items-center justify-center text-[11px] text-slate-400">
              No failures recorded yet
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {failures.map((f) => {
                const total = failures.reduce((sum, x) => sum + x.cnt, 0);
                const pct = total > 0 ? Math.round((f.cnt / total) * 100) : 0;
                return (
                  <div key={f.outcome_reason} className="flex flex-col gap-0.5">
                    <div className="flex items-center justify-between text-[10px]">
                      <span className="font-semibold text-slate-600 dark:text-slate-300">{humanReason(f.outcome_reason)}</span>
                      <span className="font-mono font-bold text-slate-500">{f.cnt} ({pct}%)</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                      <div
                        className="h-full rounded-full bg-rose-400 transition-all duration-500"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Stacked bar: success vs failed vs no_answer */}
      {stats.total > 0 && (
        <div className="clay-card p-4">
          <div className="mb-2 flex items-center gap-1.5">
            <TrendingUp className="size-3.5 text-slate-400" />
            <span className="text-[10px] font-black uppercase tracking-wider text-slate-500">Call Outcomes</span>
          </div>
          <div className="flex h-4 overflow-hidden rounded-full">
            {stats.successful > 0 && (
              <div
                className="bg-emerald-400 transition-all duration-700"
                style={{ width: `${Math.round((stats.successful / stats.total) * 100)}%` }}
                title={`Success: ${stats.successful}`}
              />
            )}
            {stats.failed > 0 && (
              <div
                className="bg-rose-400 transition-all duration-700"
                style={{ width: `${Math.round((stats.failed / stats.total) * 100)}%` }}
                title={`Failed: ${stats.failed}`}
              />
            )}
            {stats.no_answer > 0 && (
              <div
                className="bg-amber-400 transition-all duration-700"
                style={{ width: `${Math.round((stats.no_answer / stats.total) * 100)}%` }}
                title={`No answer: ${stats.no_answer}`}
              />
            )}
            {stats.in_progress > 0 && (
              <div
                className="animate-pulse bg-sky-400 transition-all duration-700"
                style={{ width: `${Math.round((stats.in_progress / stats.total) * 100)}%` }}
                title={`Live: ${stats.in_progress}`}
              />
            )}
          </div>
          <div className="mt-2 flex flex-wrap gap-3 text-[10px] font-semibold">
            <span className="flex items-center gap-1"><span className="size-2 rounded-full bg-emerald-400" />Success</span>
            <span className="flex items-center gap-1"><span className="size-2 rounded-full bg-rose-400" />Failed</span>
            <span className="flex items-center gap-1"><span className="size-2 rounded-full bg-amber-400" />No answer</span>
            {stats.in_progress > 0 && <span className="flex items-center gap-1"><span className="size-2 animate-pulse rounded-full bg-sky-400" />Live</span>}
          </div>
        </div>
      )}

      {/* Call history */}
      <div className="clay-card overflow-hidden p-0">
        <div className="flex items-center justify-between border-b border-slate-200/60 px-4 py-3 dark:border-slate-800/60">
          <span className="text-[10px] font-black uppercase tracking-wider text-slate-500">
            Recent Calls
          </span>
          <span className="clay-pill bg-white/80 px-2 py-0.5 font-mono text-[9px] font-bold text-slate-500 dark:bg-slate-800">
            {calls.length} shown
          </span>
        </div>

        {calls.length === 0 ? (
          <div className="flex h-28 items-center justify-center text-xs font-medium text-slate-400">
            No calls recorded yet — make a call to see it here
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="border-b border-slate-200/60 dark:border-slate-800/60">
                  {['#', 'Channel', 'Date', 'Time', 'Duration', 'Outcome', 'Reason', 'Turns'].map((h) => (
                    <th
                      key={h}
                      className="px-3 py-2 text-left font-black uppercase tracking-wider text-slate-400"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {calls.map((c) => {
                  const cfg = OUTCOME_CONFIG[c.outcome] ?? OUTCOME_CONFIG.failed;
                  return (
                    <tr
                      key={c.id}
                      className="border-b border-slate-100/60 transition-colors last:border-0 hover:bg-slate-50/60 dark:border-slate-800/40 dark:hover:bg-slate-800/30"
                    >
                      <td className="px-3 py-2 font-mono font-bold text-slate-400">#{c.id}</td>
                      <td className="px-3 py-2">
                        <span className="flex items-center gap-1 font-semibold text-slate-600 dark:text-slate-300">
                          {c.channel === 'sip' ? (
                            <PhoneMissed className="size-3 text-violet-500" />
                          ) : (
                            <Wifi className="size-3 text-sky-500" />
                          )}
                          {c.channel}
                        </span>
                      </td>
                      <td className="px-3 py-2 font-mono text-slate-400">{formatDate(c.started_at)}</td>
                      <td className="px-3 py-2 font-mono text-slate-500">{formatTime(c.started_at)}</td>
                      <td className="px-3 py-2 font-mono font-semibold text-slate-600 dark:text-slate-300">
                        {formatDuration(c.duration_secs)}
                      </td>
                      <td className="px-3 py-2">
                        <span className={cn('clay-pill px-2 py-0.5 font-black', cfg.cls)}>
                          {cfg.label}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-slate-500 dark:text-slate-400">
                        {humanReason(c.outcome_reason)}
                        {c.escalation_created === 1 && (
                          <span className="ml-1.5 clay-pill bg-violet-100 px-1.5 py-0.5 text-[9px] font-black text-violet-800 dark:bg-violet-900/60 dark:text-violet-300">
                            ESC
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 font-mono text-slate-400">
                        {c.user_turns}u / {c.agent_turns}a
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

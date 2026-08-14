import { NextResponse } from 'next/server';
import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

export const revalidate = 0;

function openDb() {
  const dbPath = path.resolve(process.cwd(), '../backend/data/helpline.db');
  if (!fs.existsSync(dbPath)) return null;
  const db = new DatabaseSync(dbPath);
  db.exec('PRAGMA busy_timeout = 3000');
  return db;
}

function tableExists(db: DatabaseSync, name: string): boolean {
  const row = db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(name);
  return !!row;
}

const EMPTY = {
  stats: {
    total: 0,
    successful: 0,
    failed: 0,
    no_answer: 0,
    in_progress: 0,
    avg_duration_secs: 0,
    success_rate: 0,
  },
  recent_calls: [],
};

export async function GET() {
  const db = openDb();
  if (!db) return NextResponse.json(EMPTY, { headers: { 'Cache-Control': 'no-store' } });

  try {
    if (!tableExists(db, 'calls')) {
      return NextResponse.json(EMPTY, { headers: { 'Cache-Control': 'no-store' } });
    }

    const row = db
      .prepare(
        `SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN outcome = 'success'     THEN 1 ELSE 0 END) AS successful,
          SUM(CASE WHEN outcome = 'failed'      THEN 1 ELSE 0 END) AS failed,
          SUM(CASE WHEN outcome = 'no_answer'   THEN 1 ELSE 0 END) AS no_answer,
          SUM(CASE WHEN outcome = 'in_progress' THEN 1 ELSE 0 END) AS in_progress,
          ROUND(AVG(CASE WHEN duration_secs IS NOT NULL
                          AND outcome NOT IN ('in_progress')
                         THEN duration_secs END), 1) AS avg_duration_secs
        FROM calls`
      )
      .get() as Record<string, number | null>;

    const total = Number(row.total ?? 0);
    const successful = Number(row.successful ?? 0);
    const failed = Number(row.failed ?? 0);
    const no_answer = Number(row.no_answer ?? 0);
    const in_progress = Number(row.in_progress ?? 0);
    const avg_duration_secs = Number(row.avg_duration_secs ?? 0);
    const completed = total - in_progress;
    const success_rate = completed > 0 ? Math.round((successful / completed) * 100) : 0;

    // Failure reason breakdown (advanced optional feature)
    const failureRows = db
      .prepare(
        `SELECT outcome_reason, COUNT(*) AS cnt
         FROM calls
         WHERE outcome IN ('failed', 'no_answer')
         GROUP BY outcome_reason
         ORDER BY cnt DESC`
      )
      .all() as Array<{ outcome_reason: string; cnt: number }>;

    // Recent calls — no PII (room_name is just a room identifier, not a phone number)
    const recentRows = db
      .prepare(
        `SELECT id, channel, started_at, ended_at, duration_secs,
                outcome, outcome_reason, escalation_created, user_turns, agent_turns
         FROM calls
         ORDER BY started_at DESC
         LIMIT 20`
      )
      .all();

    return NextResponse.json(
      {
        stats: {
          total,
          successful,
          failed,
          no_answer,
          in_progress,
          avg_duration_secs,
          success_rate,
        },
        failure_breakdown: failureRows,
        recent_calls: recentRows,
      },
      { headers: { 'Cache-Control': 'no-store' } }
    );
  } catch (err) {
    console.error('GET /api/calls error:', err);
    return NextResponse.json(EMPTY, { headers: { 'Cache-Control': 'no-store' } });
  } finally {
    db.close();
  }
}

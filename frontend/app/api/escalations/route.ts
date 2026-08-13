import { NextResponse } from 'next/server';
import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

export const revalidate = 0;

const STATUSES = ['open', 'acknowledged', 'resolved'];

// Same file the Python agent writes through aiosqlite. WAL is enabled by db.init_db(),
// so a concurrent agent write no longer means SQLITE_BUSY here.
function openDb() {
  const dbPath = path.resolve(process.cwd(), '../backend/data/helpline.db');
  if (!fs.existsSync(dbPath)) return null;
  const db = new DatabaseSync(dbPath);
  db.exec('PRAGMA busy_timeout = 3000');
  return db;
}

const ref = (id: number) => `ESC-${String(id).padStart(4, '0')}`;

export async function GET(req: Request) {
  try {
    const status = new URL(req.url).searchParams.get('status') ?? 'open';
    const db = openDb();
    if (!db)
      return NextResponse.json({ escalations: [] }, { headers: { 'Cache-Control': 'no-store' } });

    try {
      const rows = status
        ? db
            .prepare(
              'SELECT * FROM escalations WHERE status = ? ORDER BY created_at DESC LIMIT 100'
            )
            .all(status)
        : db.prepare('SELECT * FROM escalations ORDER BY created_at DESC LIMIT 100').all();
      const escalations = rows.map((r) => ({ ...r, ref: ref(Number(r.id)) }));
      return NextResponse.json({ escalations }, { headers: { 'Cache-Control': 'no-store' } });
    } finally {
      db.close();
    }
  } catch (error) {
    console.error('Escalations GET error:', error);
    // Table missing (agent never ran) is not an error worth a 500 on a dashboard.
    return NextResponse.json({ escalations: [] }, { headers: { 'Cache-Control': 'no-store' } });
  }
}

export async function PATCH(req: Request) {
  // Fails closed: with ADMIN_TOKEN unset nobody can write, so a forgotten env var
  // cannot leave status updates open to the internet.
  const expected = process.env.ADMIN_TOKEN;
  if (!expected || req.headers.get('x-admin-token') !== expected) {
    return NextResponse.json(
      { error: 'forbidden — set ADMIN_TOKEN and send x-admin-token' },
      { status: 403 }
    );
  }

  try {
    const body = await req.json().catch(() => ({}));
    const escRef = String(body?.ref ?? '');
    const status = String(body?.status ?? '').toLowerCase();
    const note = String(body?.note ?? '');
    const id = Number(escRef.replace(/\D/g, ''));
    if (!id || !STATUSES.includes(status)) {
      return NextResponse.json({ error: 'bad ref or status' }, { status: 400 });
    }

    const db = openDb();
    if (!db) return NextResponse.json({ error: 'database not found' }, { status: 404 });
    try {
      const res = db
        .prepare(
          'UPDATE escalations SET status = ?, resolution_note = ?, updated_at = ? WHERE id = ?'
        )
        .run(status, note, new Date().toISOString(), id);
      if (res.changes === 0) return NextResponse.json({ error: 'not found' }, { status: 404 });
      const row = db.prepare('SELECT * FROM escalations WHERE id = ?').get(id);
      return NextResponse.json({ escalation: { ...row, ref: ref(id) } });
    } finally {
      db.close();
    }
  } catch (error) {
    console.error('Escalations PATCH error:', error);
    return NextResponse.json({ error: 'internal error' }, { status: 500 });
  }
}

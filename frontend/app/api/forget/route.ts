import { NextResponse } from 'next/server';
import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

export const revalidate = 0;

export async function POST(req: Request) {
  try {
    const cookieHeader = req.headers.get('cookie') || '';
    const cookieMatch = cookieHeader.match(/careva_caller_id=([^;]+)/);
    const callerId = cookieMatch ? cookieMatch[1] : null;

    const body = await req.json().catch(() => ({}));
    const targetId = body?.user_id || callerId;
    const targetName = body?.name;

    const dbPath = path.resolve(process.cwd(), '../backend/data/helpline.db');
    if (fs.existsSync(dbPath)) {
      try {
        const db = new DatabaseSync(dbPath);
        if (targetId && targetName) {
          db.prepare(
            'DELETE FROM callers WHERE user_id = ? OR LOWER(name) = LOWER(?) OR name = ?'
          ).run(targetId, targetName, targetName);
        } else if (targetId) {
          db.prepare(
            'DELETE FROM callers WHERE user_id = ? OR LOWER(name) = LOWER(?) OR name = ?'
          ).run(targetId, targetId, targetId);
        } else if (targetName) {
          db.prepare('DELETE FROM callers WHERE LOWER(name) = LOWER(?) OR name = ?').run(
            targetName,
            targetName
          );
        } else {
          // If no specific caller ID passed, wipe recent callers or all
          db.prepare('DELETE FROM callers').run();
        }
        db.close();
      } catch (dbErr) {
        console.error('Error deleting from SQLite:', dbErr);
      }
    }

    const headers = new Headers({
      'Cache-Control': 'no-store',
      'Set-Cookie': 'careva_caller_id=; Path=/; Max-Age=0; SameSite=Lax',
    });

    return NextResponse.json(
      { success: true, message: 'All personal records and memory have been permanently deleted.' },
      { headers }
    );
  } catch (error) {
    if (error instanceof Error) {
      console.error('Forget API error:', error);
      return new NextResponse(error.message, { status: 500 });
    }
    return new NextResponse('Internal Server Error', { status: 500 });
  }
}

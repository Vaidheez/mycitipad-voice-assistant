import { NextResponse } from 'next/server';
import path from 'node:path';
import fs from 'node:fs';
import { DatabaseSync } from 'node:sqlite';

function getDb(): DatabaseSync | null {
  const candidates = [
    process.env.MCP_DB_PATH,
    path.resolve(process.cwd(), '../backend/data/mycitipad.db'),
    path.resolve(process.cwd(), 'backend/data/mycitipad.db'),
    path.resolve(process.cwd(), '../backend/backend/data/mycitipad.db'),
  ].filter(Boolean) as string[];

  for (const p of candidates) {
    if (fs.existsSync(p)) {
      try {
        return new DatabaseSync(p);
      } catch (e) {
        console.error('Error opening SQLite at', p, e);
      }
    }
  }
  return null;
}

export async function POST() {
  try {
    const db = getDb();
    if (!db) {
      return NextResponse.json({ error: 'Database not found' }, { status: 500 });
    }

    const now = new Date();
    const isoNow = now.toISOString();

    // Insert a realistic sample booking
    const bookingRef = `MCP-VIEW-${Math.floor(100000 + Math.random() * 900000)}`;
    const slotStart = new Date(now.getTime() + 24 * 3600 * 1000).toISOString().slice(0, 16).replace('T', ' ');

    try {
      db.prepare(
        `INSERT OR IGNORE INTO bookings 
         (booking_ref, property_id, slot_start, slot_end, caller_name, caller_email, caller_phone, status, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, 'confirmed', ?)`
      ).run(
        bookingRef,
        'prop-canary-01',
        slotStart,
        slotStart,
        'Sarah Jenkins',
        'sarah.jenkins@example.com',
        '07700 900123',
        isoNow
      );
    } catch {
      // Ignore if exists
    }

    // Insert sample call logs
    const sampleLogs = [
      {
        id: `call-demo-${Date.now()}-1`,
        category: 'viewing',
        outcome: 'viewing_booked',
        summary: 'Viewing scheduled for 14 Canary Wharf Riverside on Thursday at 2:00 PM',
        details: JSON.stringify({
          full_name: 'Sarah Jenkins',
          email: 'sarah.jenkins@example.com',
          property: '14 Canary Wharf Riverside',
          move_in_date: 'Next month',
          occupants: '1 person',
          booking_ref: bookingRef,
        }),
      },
      {
        id: `call-demo-${Date.now()}-2`,
        category: 'emergency',
        outcome: 'safety_disclosed_transferred',
        summary: 'Tenant reported gas odor in corridor. Advised 0800 111 999 safety protocol.',
        details: JSON.stringify({
          full_name: 'Marcus Vance',
          contact_number: '07700 900789',
          current_address: 'Flat 4B, Shoreditch High Street',
          requirement: 'Suspected gas leak near meter',
        }),
      },
      {
        id: `call-demo-${Date.now()}-3`,
        category: 'sales_lead',
        outcome: 'lead_captured',
        summary: 'Cash buyer inquiring about 2-bedroom purchase opportunities in Greenwich.',
        details: JSON.stringify({
          full_name: 'David Patel',
          email: 'david.patel@example.com',
          contact_number: '07700 900456',
          requirement: 'Budget up to £650,000 for 2-bed flat',
        }),
      },
    ];

    for (const log of sampleLogs) {
      db.prepare(
        `INSERT OR IGNORE INTO call_logs (call_id, started_at, ended_at, category, outcome, details, transcript)
         VALUES (?, ?, ?, ?, ?, ?, ?)`
      ).run(log.id, isoNow, isoNow, log.category, log.outcome, log.details, 'Voice interaction completed.');
    }

    // Insert a pending callback
    db.prepare(
      `INSERT OR IGNORE INTO callback_requests (callback_id, call_id, name, phone, reason, priority, status, created_at)
       VALUES (?, ?, ?, ?, ?, 'high', 'pending', ?)`
    ).run(
      `cb-demo-${Date.now()}`,
      `call-demo-${Date.now()}-2`,
      'Marcus Vance',
      '07700 900789',
      'Gas emergency follow-up and contractor dispatch',
      isoNow
    );

    // Insert a consented caller profile
    db.prepare(
      `INSERT OR IGNORE INTO callers (user_id, name, email, phone, language_preference, facts, consent_at, last_interaction)
       VALUES (?, ?, ?, ?, 'en-GB', ?, ?, ?)`
    ).run(
      `usr-sarah-${Date.now()}`,
      'Sarah Jenkins',
      'sarah.jenkins@example.com',
      '07700 900123',
      JSON.stringify(['Looking for 2-bed in Canary Wharf', 'Available for viewings on Thursday afternoons']),
      isoNow,
      isoNow
    );

    return NextResponse.json({ success: true, message: 'Sample demo data populated successfully' });
  } catch (error: unknown) {
    console.error('Seed API error:', error);
    const msg = error instanceof Error ? error.message : 'Unknown error';
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}

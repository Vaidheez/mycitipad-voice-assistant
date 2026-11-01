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

export async function GET() {
  try {
    const db = getDb();
    if (!db) {
      return NextResponse.json(
        { error: 'Database file not found. Please ensure backend has run db.py.' },
        { status: 500 }
      );
    }

    // 1. Summary Statistics
    const totalCalls = (db.prepare('SELECT COUNT(*) as c FROM call_logs').get() as { c: number }).c;
    const totalBookings = (db.prepare('SELECT COUNT(*) as c FROM bookings').get() as { c: number }).c;
    const pendingCallbacks = (
      db.prepare("SELECT COUNT(*) as c FROM callback_requests WHERE status = 'pending'").get() as { c: number }
    ).c;
    const emergencies = (
      db.prepare("SELECT COUNT(*) as c FROM call_logs WHERE category = 'emergency'").get() as { c: number }
    ).c;
    const maintenanceRequests = (
      db.prepare("SELECT COUNT(*) as c FROM call_logs WHERE category = 'maintenance_request'").get() as { c: number }
    ).c;
    const salesLeads = (
      db.prepare("SELECT COUNT(*) as c FROM call_logs WHERE category = 'sales_lead'").get() as { c: number }
    ).c;
    const callerProfiles = (db.prepare('SELECT COUNT(*) as c FROM callers').get() as { c: number }).c;
    const propertiesCount = (db.prepare('SELECT COUNT(*) as c FROM properties').get() as { c: number }).c;

    // 2. Confirmed Bookings with Property details
    const bookings = db
      .prepare(
        `SELECT b.booking_ref, b.property_id, b.slot_start, b.slot_end, 
                b.caller_name, b.caller_email, b.caller_phone, b.status, b.created_at,
                p.address as property_address, p.price_gbp, p.department
         FROM bookings b
         LEFT JOIN properties p ON b.property_id = p.property_id
         ORDER BY b.created_at DESC`
      )
      .all();

    // 3. Call Logs
    const rawLogs = db
      .prepare(
        `SELECT call_id, user_id, started_at, ended_at, category, outcome, details, transcript
         FROM call_logs
         ORDER BY started_at DESC
         LIMIT 100`
      )
      .all() as Array<Record<string, unknown>>;

    const callLogs = rawLogs.map((log) => {
      let parsedDetails = null;
      if (typeof log.details === 'string') {
        try {
          parsedDetails = JSON.parse(log.details);
        } catch {
          parsedDetails = log.details;
        }
      }
      return {
        ...log,
        category: (log.category as string) || 'general_enquiry',
        outcome: (log.outcome as string) || 'in_progress',
        summary: (log.summary as string) || 'Voice call session',
        details: parsedDetails,
      };
    });

    // 4. Callback Requests Queue
    const callbacks = db
      .prepare(
        `SELECT callback_id, call_id, name, phone, reason, priority, status, created_at
         FROM callback_requests
         ORDER BY created_at DESC`
      )
      .all();

    // 5. Handover Requests
    const handovers = db
      .prepare(
        `SELECT request_id, call_id, reason, status, created_at
         FROM handover_requests
         ORDER BY created_at DESC`
      )
      .all();

    // 6. Caller Memory Profiles (with parsed facts)
    const rawCallers = db
      .prepare(
        `SELECT user_id, name, email, phone, language_preference, facts, consent_at, last_interaction
         FROM callers
         ORDER BY last_interaction DESC`
      )
      .all() as Array<Record<string, unknown>>;

    const callers = rawCallers.map((c) => {
      let parsedFacts: string[] = [];
      if (typeof c.facts === 'string') {
        try {
          parsedFacts = JSON.parse(c.facts);
        } catch {
          parsedFacts = [];
        }
      }
      return {
        ...c,
        facts: parsedFacts,
      };
    });

    // 7. Properties
    const properties = db
      .prepare(
        `SELECT property_id, address, postcode_area, department, status, bedrooms, price_gbp, source_url
         FROM properties
         ORDER BY department ASC, price_gbp ASC`
      )
      .all();

    return NextResponse.json({
      summary: {
        totalCalls,
        totalBookings,
        pendingCallbacks,
        emergencies,
        maintenanceRequests,
        salesLeads,
        callerProfiles,
        propertiesCount,
      },
      bookings,
      callLogs,
      callbacks,
      handovers,
      callers,
      properties,
      timestamp: new Date().toISOString(),
    });
  } catch (error: unknown) {
    console.error('Dashboard API Error:', error);
    const msg = error instanceof Error ? error.message : 'Unknown server error';
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}

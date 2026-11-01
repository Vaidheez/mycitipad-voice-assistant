'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';

interface CallRecord {
  id: string;
  name: string;
  phone: string;
  email: string;
  property: string;
  type: string; // 'Maintenance' | 'Lettings' | 'Sales' | 'Emergency' | 'General'
  topic: string;
  level: 'red' | 'amber' | 'green';
  time: string;
  summary: string;
  task: string;
  state: string;
  transcript: string;
  note?: string;
  isLive?: boolean;
}

const DEFAULT_SEEDS: CallRecord[] = [
  {
    id: 'C-1048',
    name: 'Alex Morgan',
    phone: '07700 900111',
    email: 'alex@example.com',
    property: 'Cascade Court, Battersea',
    type: 'Maintenance',
    topic: 'Active water leak reported',
    level: 'red',
    time: '10:14',
    summary:
      'Caller reports a significant leak near electrics. Safety guidance and human escalation required.',
    task: 'Urgent team escalation',
    state: 'Open',
    transcript:
      'Caller: There is water coming through the ceiling near a light fitting.\nAI: Please keep clear of any electrical hazard. If anyone is in immediate danger, call 999. May I take details once you are safe?',
  },
  {
    id: 'C-1047',
    name: 'Samira Khan',
    phone: '07700 900112',
    email: 'samira@example.com',
    property: 'Ambassador Building, Nine Elms',
    type: 'Maintenance',
    topic: 'Emergency call — transfer failed',
    level: 'red',
    time: '10:06',
    summary:
      'Emergency maintenance flagged. Human transfer not confirmed; callback number captured.',
    task: 'Immediate callback and escalation',
    state: 'Open',
    transcript:
      'Caller: I need someone urgently.\nAI: I can help capture your details. If anyone is in immediate danger call 999. I will try to pass you to our team.',
  },
  {
    id: 'C-1046',
    name: 'Jamie Patel',
    phone: '07700 900113',
    email: 'jamie@example.com',
    property: '48 Liner',
    type: 'Lettings',
    topic: 'Viewing requested',
    level: 'amber',
    time: '09:52',
    summary:
      'Prospective tenant wants to view a property; availability and Outlook booking need confirmation.',
    task: 'Check availability and create Outlook booking',
    state: 'Open',
    transcript:
      'Caller: I would like to arrange a viewing.\nAI: I will take a few details and check availability. Confirmation will follow only once a booking is created.',
  },
  {
    id: 'C-1045',
    name: 'Taylor Reed',
    phone: '07700 900114',
    email: 'taylor@example.com',
    property: 'Queens Grove, NW8',
    type: 'Sales',
    topic: 'Sales lead — valuation enquiry',
    level: 'amber',
    time: '09:45',
    summary: 'Caller requested contact from the sales team; no valuation or price was promised.',
    task: 'Assign sales follow-up',
    state: 'Open',
    transcript:
      'Caller: I am considering selling and would like to speak with your team.\nAI: A member of our sales team will be in touch.',
  },
  {
    id: 'C-1044',
    name: 'Morgan Lee',
    phone: '07700 900115',
    email: 'morgan@example.com',
    property: 'Hereford Road, W2',
    type: 'Maintenance',
    topic: 'Washing machine seal issue',
    level: 'amber',
    time: '09:28',
    summary:
      'Routine maintenance request captured. Agent review and work-order routing are pending.',
    task: 'Review maintenance request',
    state: 'Open',
    transcript:
      'Caller: The washing machine seal has mould.\nAI: I will take some details so the team can review the next steps.',
  },
  {
    id: 'C-1043',
    name: 'Casey Brown',
    phone: '07700 900116',
    email: 'casey@example.com',
    property: 'Dawson Place, W2',
    type: 'Lettings',
    topic: 'General letting enquiry',
    level: 'green',
    time: '09:10',
    summary: 'General enquiry recorded. Caller informed that the team will be in touch.',
    task: 'No immediate action',
    state: 'Logged',
    transcript:
      'Caller: Is this property available?\nAI: Thank you. One of our team will be in touch.',
  },
  {
    id: 'C-1042',
    name: 'Riley Scott',
    phone: '07700 900117',
    email: 'riley@example.com',
    property: 'Cascade Court, Battersea',
    type: 'Lettings',
    topic: 'Viewing recorded',
    level: 'green',
    time: '08:58',
    summary:
      'Enquiry information logged as a demo. This sample does not represent an actual booked appointment.',
    task: 'No immediate action',
    state: 'Logged',
    transcript:
      'Caller: I am enquiring about a viewing.\nAI: I will collect your preferences so the team can assist.',
  },
];

export default function DashboardPage() {
  const [rows, setRows] = useState<CallRecord[]>(DEFAULT_SEEDS);
  const [selectedId, setSelectedId] = useState<string>('C-1048');
  const [activeFilter, setActiveFilter] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [activeSection, setActiveSection] = useState<string>('overview');
  const [liveSync, setLiveSync] = useState<boolean>(true);
  const [loading, setLoading] = useState<boolean>(false);

  // Modal State
  const [modalOpen, setModalOpen] = useState<boolean>(false);
  const [pendingAction, setPendingAction] = useState<'assign' | 'note' | 'resolve' | null>(null);
  const [actionNote, setActionNote] = useState<string>('');

  // Fetch real calls from /api/dashboard and merge with seeds
  const fetchLiveCalls = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch('/api/dashboard');
      if (!res.ok) return;
      const data = await res.json();

      const liveCalls: CallRecord[] = [];

      // Map Call Logs from SQLite
      if (Array.isArray(data.callLogs) && data.callLogs.length > 0) {
        data.callLogs.forEach((log: Record<string, unknown>) => {
          const category = String(log.category || 'other');
          let level: 'red' | 'amber' | 'green' = 'green';
          let type = 'Lettings';
          let task = 'No immediate action';
          let state = 'Logged';

          if (category === 'emergency') {
            level = 'red';
            type = 'Maintenance';
            task = 'Urgent emergency escalation';
            state = 'Open';
          } else if (category === 'maintenance_request') {
            level = 'amber';
            type = 'Maintenance';
            task = 'Review work-order routing';
            state = 'Open';
          } else if (category === 'sales_lead') {
            level = 'amber';
            type = 'Sales';
            task = 'Assign sales team follow-up';
            state = 'Open';
          } else if (category === 'viewing') {
            level = 'amber';
            type = 'Lettings';
            task = 'Confirm calendar slot & send invite';
            state = 'Open';
          }

          const details = (log.details as Record<string, unknown>) || {};
          const callerName = String(details.caller_name || details.name || log.user_id || 'Caller');
          const callerPhone = String(details.caller_phone || details.phone || '—');
          const callerEmail = String(details.caller_email || details.email || '—');
          const property = String(
            details.property_address || details.property || 'London Portfolio'
          );
          const outcome = String(log.outcome || 'Conversation recorded');

          const started = log.started_at ? new Date(String(log.started_at)) : new Date();
          const timeStr = isNaN(started.getTime())
            ? 'Recent'
            : started.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });

          const callSid = String(log.call_id || '').substring(0, 8);
          liveCalls.push({
            id: `LIVE-${callSid || Math.floor(Math.random() * 1000)}`,
            name: callerName,
            phone: callerPhone,
            email: callerEmail,
            property: property,
            type: type,
            topic: outcome,
            level: level,
            time: timeStr,
            summary: String(
              details.summary ||
                `Live caller conversation logged via voice receptionist (${category}).`
            ),
            task: task,
            state: state,
            transcript: String(log.transcript || 'No transcript recorded for this turn.'),
            isLive: true,
          });
        });
      }

      // Merge live calls with default seeds (live calls appear at top)
      setRows((prev) => {
        const liveIds = new Set(liveCalls.map((c) => c.id));
        const keptPrev = prev.filter((r) => !r.isLive || liveIds.has(r.id));
        const combined = [...liveCalls, ...keptPrev.filter((r) => !liveIds.has(r.id))];
        return combined.length > 0 ? combined : DEFAULT_SEEDS;
      });
    } catch (err) {
      console.warn('Dashboard live sync note:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchLiveCalls();
  }, [fetchLiveCalls]);

  useEffect(() => {
    if (!liveSync) return;
    const interval = setInterval(fetchLiveCalls, 5000);
    return () => clearInterval(interval);
  }, [liveSync, fetchLiveCalls]);

  // Statistics Calculation
  const counts = useMemo(() => {
    const c = { red: 0, amber: 0, green: 0 };
    rows.forEach((r) => {
      c[r.level] = (c[r.level] || 0) + 1;
    });
    return c;
  }, [rows]);

  // Filtered Calls
  const filteredList = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return rows
      .filter((r) => {
        const matchesFilter =
          activeFilter === 'all' ||
          r.level === activeFilter ||
          r.type.toLowerCase() === activeFilter.toLowerCase();
        const matchesSearch =
          !q ||
          r.name.toLowerCase().includes(q) ||
          r.property.toLowerCase().includes(q) ||
          r.topic.toLowerCase().includes(q) ||
          r.id.toLowerCase().includes(q);
        return matchesFilter && matchesSearch;
      })
      .sort((a, b) => {
        const rank = { red: 0, amber: 1, green: 2 };
        return rank[a.level] - rank[b.level];
      });
  }, [rows, activeFilter, searchQuery]);

  // Selected Call Record
  const selectedRecord = useMemo(() => {
    return rows.find((x) => x.id === selectedId) || filteredList[0] || rows[0];
  }, [rows, selectedId, filteredList]);

  // Helper Labels
  const getLevelLabel = (l: 'red' | 'amber' | 'green') => {
    switch (l) {
      case 'red':
        return 'Critical · Urgent';
      case 'amber':
        return 'Action required';
      case 'green':
        return 'Routine / logged';
    }
  };

  // Actions
  const openAction = (act: 'assign' | 'note' | 'resolve') => {
    setPendingAction(act);
    setActionNote('');
    setModalOpen(true);
  };

  const handleModalConfirm = () => {
    if (!selectedRecord) return;
    setRows((prev) =>
      prev.map((r) => {
        if (r.id !== selectedRecord.id) return r;
        const updated = { ...r };
        if (pendingAction === 'assign') {
          updated.state = 'Assigned (demo)';
          updated.task = 'Follow up with assigned operations team';
        } else if (pendingAction === 'resolve') {
          updated.state = 'Reviewed (demo)';
          updated.level = 'green';
          updated.task = 'No immediate action required';
        }
        if (actionNote.trim()) {
          updated.note = actionNote.trim();
        }
        return updated;
      })
    );
    setModalOpen(false);
  };

  const handleResetDemo = () => {
    setRows(DEFAULT_SEEDS);
    setSelectedId('C-1048');
    setActiveFilter('all');
    setActiveSection('overview');
    setSearchQuery('');
  };

  return (
    <div className="dashboard-root">
      <div className="app">
        {/* Left Sidebar */}
        <aside className="sidebar">
          <div className="logo">
            MY<span>CITI</span>PAD{' '}
            <span style={{ fontSize: '11px', color: '#a1bcd1', letterSpacing: 0 }}>AI</span>
          </div>

          <div>
            <div className="smallcap">WORKSPACE</div>
            <div className="nav">
              <button
                className={activeSection === 'overview' && activeFilter === 'all' ? 'active' : ''}
                onClick={() => {
                  setActiveSection('overview');
                  setActiveFilter('all');
                }}
              >
                ▦ &nbsp; Overview
              </button>
              <button
                className={activeFilter === 'red' ? 'active' : ''}
                onClick={() => {
                  setActiveSection('red');
                  setActiveFilter('red');
                }}
              >
                ⚑ &nbsp; Urgent alerts
                {counts.red > 0 && <span className="count">{counts.red}</span>}
              </button>
              <button
                className={activeFilter === 'amber' ? 'active' : ''}
                onClick={() => {
                  setActiveSection('amber');
                  setActiveFilter('amber');
                }}
              >
                ◷ &nbsp; Action queue
              </button>
              <button
                className={activeFilter === 'green' ? 'active' : ''}
                onClick={() => {
                  setActiveSection('green');
                  setActiveFilter('green');
                }}
              >
                ☑ &nbsp; Routine / done
              </button>
              <button
                className={activeSection === 'all' && activeFilter === 'all' ? 'active' : ''}
                onClick={() => {
                  setActiveSection('all');
                  setActiveFilter('all');
                }}
              >
                ☏ &nbsp; All calls
              </button>
            </div>
          </div>

          <div>
            <div className="smallcap">OPERATIONS & TOOLS</div>
            <div className="nav">
              <Link href="/" className="nav-link">
                🎙 &nbsp; Voice Assistant UI
              </Link>
              <button
                onClick={() => {
                  const el = document.getElementById('roadmap-section');
                  el?.scrollIntoView({ behavior: 'smooth' });
                }}
              >
                ◈ &nbsp; Integrations & CRM
              </button>
            </div>
          </div>

          <div className="footer">
            <span className="online" /> Receptionist operations • Stage 1
            <br />
            <span style={{ display: 'inline-block', marginTop: '8px' }}>
              Connected to LiveKit & MyCitiPad SQLite DB
            </span>
          </div>
        </aside>

        {/* Main Content Area */}
        <main className="main">
          {/* Top Bar */}
          <div className="top">
            <div>
              <div className="eyebrow">OPERATIONS / RECEPTIONIST</div>
              <h1>
                AI Receptionist <span style={{ fontWeight: 400 }}>Dashboard</span>
              </h1>
              <div className="sub">Every conversation tracked. Every priority visible.</div>
            </div>

            <div className="topright">
              <button
                className={`pill ${liveSync ? 'live' : ''}`}
                onClick={() => setLiveSync(!liveSync)}
                title="Toggle live database polling"
              >
                <span className={`sync-dot ${liveSync ? 'active' : ''}`} />
                {liveSync ? 'Live Sync (5s)' : 'Sync Paused'}
              </button>

              <button className="btn" onClick={fetchLiveCalls} title="Refresh immediately">
                ↻ {loading ? 'Syncing...' : 'Refresh'}
              </button>

              <button className="btn" onClick={handleResetDemo}>
                ↻ Reset demo
              </button>

              <Link href="/" className="btn primary">
                🎙 Open Voice Call
              </Link>
            </div>
          </div>

          {/* KPI Stat Cards (4 columns) */}
          <div className="stats">
            <div className="stat blue">
              <div className="k">Total conversations</div>
              <div className="number">{rows.length}</div>
              <div className="minor">All logged interactions</div>
            </div>
            <div className="stat red">
              <div className="k">Red · Critical / urgent</div>
              <div className="number">{counts.red}</div>
              <div className="minor">Requires immediate review</div>
            </div>
            <div className="stat amber">
              <div className="k">Amber · Action required</div>
              <div className="number">{counts.amber}</div>
              <div className="minor">Tasks awaiting follow-up</div>
            </div>
            <div className="stat green">
              <div className="k">Green · Routine / done</div>
              <div className="number">{counts.green}</div>
              <div className="minor">No immediate action</div>
            </div>
          </div>

          {/* Two Column Split: Inbox & Detail View */}
          <div className="two">
            {/* Left Column: Inbox List */}
            <section className="panel">
              <div className="panelhead">
                <div>
                  <h2>Receptionist inbox</h2>
                  <div className="caption">Prioritised by urgency and pending action</div>
                </div>
                <input
                  className="search"
                  placeholder="Search caller or property"
                  aria-label="Search calls"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                />
              </div>

              {/* Filter Pills */}
              <div className="filters">
                <button
                  className={activeFilter === 'all' ? 'active' : ''}
                  onClick={() => setActiveFilter('all')}
                >
                  All
                </button>
                <button
                  className={activeFilter === 'red' ? 'active' : ''}
                  onClick={() => setActiveFilter('red')}
                >
                  🔴 Urgent
                </button>
                <button
                  className={activeFilter === 'amber' ? 'active' : ''}
                  onClick={() => setActiveFilter('amber')}
                >
                  🟠 Action
                </button>
                <button
                  className={activeFilter === 'green' ? 'active' : ''}
                  onClick={() => setActiveFilter('green')}
                >
                  🟢 Routine
                </button>
                <button
                  className={activeFilter === 'lettings' ? 'active' : ''}
                  onClick={() => setActiveFilter('lettings')}
                >
                  Lettings
                </button>
                <button
                  className={activeFilter === 'sales' ? 'active' : ''}
                  onClick={() => setActiveFilter('sales')}
                >
                  Sales
                </button>
                <button
                  className={activeFilter === 'maintenance' ? 'active' : ''}
                  onClick={() => setActiveFilter('maintenance')}
                >
                  Maintenance
                </button>
              </div>

              {/* Call Rows List */}
              <div className="calls">
                {filteredList.length > 0 ? (
                  filteredList.map((r) => (
                    <div
                      key={r.id}
                      className={`call ${selectedRecord?.id === r.id ? 'selected' : ''}`}
                      onClick={() => setSelectedId(r.id)}
                    >
                      <span className={`dot ${r.level}`} />
                      <div className="callbody">
                        <div className="calltitle">
                          <span>{r.topic}</span>
                          <span className="time">{r.time}</span>
                        </div>
                        <div className="detail">
                          {r.name} · {r.property}
                          <br />
                          {r.type} · {r.id}
                          {r.isLive && (
                            <span className="live-pill" style={{ marginLeft: '6px' }}>
                              LIVE CALL
                            </span>
                          )}
                        </div>
                        <span className={`tag ${r.level}`}>{getLevelLabel(r.level)}</span>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="empty">No matching calls found</div>
                )}
              </div>
            </section>

            {/* Right Column: Conversation Details */}
            <section className="panel">
              <div className="panelhead">
                <div>
                  <h2>Conversation details</h2>
                  <div className="caption">Select an item to review and manage</div>
                </div>
                <span className="meta">
                  {selectedRecord?.isLive ? 'LIVE DB RECORD' : 'STAGE 1 RECORD'}
                </span>
              </div>

              {selectedRecord ? (
                <div className="detailview">
                  <span className={`tag ${selectedRecord.level}`}>
                    {getLevelLabel(selectedRecord.level)}
                  </span>
                  <h3>{selectedRecord.name}</h3>
                  <div className="sub">
                    {selectedRecord.topic} • {selectedRecord.id}
                  </div>

                  <dl className="info">
                    <dt>Category</dt>
                    <dd>{selectedRecord.type}</dd>
                    <dt>Property</dt>
                    <dd>{selectedRecord.property}</dd>
                    <dt>Phone</dt>
                    <dd>{selectedRecord.phone}</dd>
                    <dt>Email</dt>
                    <dd>{selectedRecord.email}</dd>
                    <dt>Status</dt>
                    <dd>{selectedRecord.state}</dd>
                    <dt>Next action</dt>
                    <dd>{selectedRecord.task}</dd>
                  </dl>

                  <div className="note">
                    <b>AI conversation summary</b>
                    {selectedRecord.summary}
                  </div>

                  <div className="note" style={{ marginTop: '10px' }}>
                    <b>Transcript excerpt</b>
                    <div style={{ whiteSpace: 'pre-line' }}>{selectedRecord.transcript}</div>
                  </div>

                  {selectedRecord.note && (
                    <div className="note" style={{ marginTop: '10px' }}>
                      <b>Latest internal note</b>
                      {selectedRecord.note}
                    </div>
                  )}

                  <div className="actionrow">
                    <button className="btn primary" onClick={() => openAction('assign')}>
                      Assign to team
                    </button>
                    <button className="btn" onClick={() => openAction('note')}>
                      Add note
                    </button>
                    {selectedRecord.level !== 'green' && (
                      <button className="btn" onClick={() => openAction('resolve')}>
                        Mark reviewed
                      </button>
                    )}
                  </div>
                  <p className="muted" style={{ marginTop: '14px' }}>
                    Actions update this workspace session in real-time.
                  </p>
                </div>
              ) : (
                <div className="empty">Select a conversation to inspect details</div>
              )}
            </section>
          </div>

          {/* Call Journey Flowchart */}
          <div className="activity">
            <div className="panel" style={{ paddingTop: '4px' }}>
              <div className="panelhead">
                <div>
                  <h2>Receptionist call journey</h2>
                  <div className="caption">Based on MyCitiPad Stage 1 call-handling rules</div>
                </div>
              </div>
              <div className="flowline">
                <b>Incoming call</b> → <b>Lettings / Sales</b> → <b>Enquiry / Maintenance</b> →{' '}
                <b>Capture details</b> → <b>Route or action</b> → <b>Save call + transcript</b>
              </div>
            </div>
          </div>

          {/* Connected Workspace & Roadmap */}
          <div className="activity" id="roadmap-section">
            <div className="panelhead" style={{ paddingLeft: 0 }}>
              <div>
                <h2>Connected workspace & roadmap</h2>
                <div className="caption">
                  Future integration points for a single source of truth
                </div>
              </div>
            </div>
            <div className="activitygrid">
              <div className="integration">
                <div className="name">☎ Calls & transcripts</div>
                <p>Call log, transcript, structured enquiries, priority and handover status.</p>
                <span className="road live">STAGE 1 WORKFLOW • LIVE CONNECTED</span>
              </div>
              <div className="integration">
                <div className="name">◈ WhatsApp</div>
                <p>Bring incoming messages into the same shared inbox, tagged by RAG priority.</p>
                <span className="road">FUTURE • NOT CONNECTED</span>
              </div>
              <div className="integration">
                <div className="name">▦ MRI CRM</div>
                <p>
                  Map callers to contacts, properties, lettings leads, sales leads and work orders.
                </p>
                <span className="road">FUTURE • NOT CONNECTED</span>
              </div>
            </div>
          </div>

          <div className="muted" style={{ marginTop: '20px' }}>
            Production dashboard connected to MyCitiPad Voice Assistant API & SQLite database.
            Sample seeds ensure visibility of emergency, maintenance, and lettings workflows.
          </div>
        </main>
      </div>

      {/* Action Modal */}
      <div
        className={`modalbg ${modalOpen ? 'open' : ''}`}
        role="dialog"
        aria-modal="true"
        onClick={(e) => {
          if (e.target === e.currentTarget) setModalOpen(false);
        }}
      >
        <div className="modal">
          <h2>
            {pendingAction === 'assign'
              ? 'Assign this item'
              : pendingAction === 'note'
                ? 'Add internal note'
                : 'Mark item reviewed'}
          </h2>
          <p className="muted">
            {pendingAction === 'assign'
              ? 'Record an internal team assignment for operations follow-up.'
              : pendingAction === 'note'
                ? 'Add details or follow-up instructions to the call record.'
                : 'Mark this call reviewed; it will move to the Routine (green) queue.'}
          </p>
          <textarea
            placeholder="Add an internal note or assignment details..."
            value={actionNote}
            onChange={(e) => setActionNote(e.target.value)}
          />
          <div className="actionrow">
            <button className="btn primary" onClick={handleModalConfirm}>
              Save internal update
            </button>
            <button className="btn" onClick={() => setModalOpen(false)}>
              Cancel
            </button>
          </div>
          <p className="notice">
            Updates the active call state across the operations dashboard session.
          </p>
        </div>
      </div>

      {/* Scoped CSS replicating mycitipad receptionist dashboard stage 1.html */}
      <style jsx global>{`
        .dashboard-root {
          font-family:
            Inter,
            ui-sans-serif,
            system-ui,
            -apple-system,
            Segoe UI,
            sans-serif;
          color: #152338;
          background: #f5f7fb;
          min-height: 100vh;
        }
        .dashboard-root * {
          box-sizing: border-box;
        }
        .app {
          display: grid;
          grid-template-columns: 230px 1fr;
          min-height: 100vh;
        }
        .sidebar {
          background: #10243b;
          color: #d6e1ee;
          padding: 26px 16px;
          display: flex;
          flex-direction: column;
          gap: 24px;
        }
        .logo {
          font-size: 21px;
          font-weight: 800;
          letter-spacing: -0.7px;
          padding: 0 12px;
          color: white;
        }
        .logo span {
          color: #58c6bd;
        }
        .smallcap {
          font-size: 10px;
          text-transform: uppercase;
          letter-spacing: 1.6px;
          color: #859bb3;
          margin: 8px 12px;
        }
        .nav {
          display: flex;
          flex-direction: column;
          gap: 5px;
        }
        .nav button,
        .nav .nav-link {
          display: flex;
          gap: 11px;
          align-items: center;
          text-align: left;
          border: 0;
          background: none;
          color: #b8c7d7;
          border-radius: 9px;
          padding: 12px;
          font-weight: 600;
          cursor: pointer;
          font-size: 13px;
          text-decoration: none;
          transition:
            background 0.15s,
            color 0.15s;
        }
        .nav button.active,
        .nav button:hover,
        .nav .nav-link:hover {
          background: #23435d;
          color: white;
        }
        .nav .count {
          margin-left: auto;
          background: #dd514f;
          color: white;
          border-radius: 50px;
          padding: 1px 7px;
          font-size: 11px;
        }
        .sidebar .footer {
          margin-top: auto;
          font-size: 12px;
          color: #9ab1c8;
          padding: 12px;
          border-top: 1px solid #294159;
          line-height: 1.5;
        }
        .online {
          display: inline-block;
          width: 8px;
          height: 8px;
          background: #5ad5a9;
          border-radius: 50%;
          margin-right: 5px;
        }
        .main {
          padding: 28px 32px;
          max-width: 1660px;
          width: 100%;
          margin: auto;
        }
        .top {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: center;
          margin-bottom: 24px;
        }
        .eyebrow {
          font-size: 11px;
          font-weight: 800;
          letter-spacing: 1.4px;
          color: #54877e;
          text-transform: uppercase;
        }
        .top h1 {
          font-size: 28px;
          letter-spacing: -1px;
          margin: 6px 0;
          color: #152338;
        }
        .sub {
          color: #708096;
          font-size: 13px;
        }
        .topright {
          display: flex;
          align-items: center;
          gap: 10px;
        }
        .pill,
        .btn {
          border: 1px solid #dce4ec;
          border-radius: 9px;
          padding: 10px 13px;
          background: white;
          font-size: 12px;
          font-weight: 700;
          color: #34475f;
          cursor: pointer;
          text-decoration: none;
          display: inline-flex;
          align-items: center;
          gap: 6px;
          transition:
            background 0.15s,
            border-color 0.15s;
        }
        .btn:hover {
          background: #f1f5f9;
        }
        .pill.live {
          color: #17654f;
          background: #eef9f5;
          border-color: #bee5d8;
        }
        .sync-dot {
          display: inline-block;
          width: 7px;
          height: 7px;
          border-radius: 50%;
          background: #8b99aa;
        }
        .sync-dot.active {
          background: #25a77b;
          animation: pulse 1.5s infinite;
        }
        @keyframes pulse {
          0% {
            opacity: 1;
          }
          50% {
            opacity: 0.4;
          }
          100% {
            opacity: 1;
          }
        }
        .primary {
          background: #153e51 !important;
          color: white !important;
          border-color: #153e51 !important;
        }
        .primary:hover {
          background: #1c4e66 !important;
        }
        .stats {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 14px;
        }
        .stat {
          background: white;
          border: 1px solid #e7ecf3;
          border-radius: 14px;
          padding: 19px;
          box-shadow: 0 2px 10px rgba(25, 50, 80, 0.02);
        }
        .stat .k {
          font-size: 12px;
          color: #73839a;
          font-weight: 700;
        }
        .stat .number {
          font-size: 32px;
          font-weight: 800;
          margin-top: 12px;
          letter-spacing: -1.2px;
          color: #152338;
        }
        .stat .minor {
          font-size: 11px;
          color: #8b97a8;
          margin-top: 6px;
        }
        .stat.red {
          border-top: 4px solid #e45b61;
        }
        .stat.amber {
          border-top: 4px solid #efb54f;
        }
        .stat.green {
          border-top: 4px solid #51b893;
        }
        .stat.blue {
          border-top: 4px solid #6b94ca;
        }
        .two {
          display: grid;
          grid-template-columns: minmax(0, 1.75fr) minmax(310px, 1fr);
          gap: 18px;
          margin-top: 20px;
        }
        .panel {
          background: white;
          border: 1px solid #e7ecf3;
          border-radius: 14px;
          box-shadow: 0 2px 10px rgba(25, 50, 80, 0.02);
          overflow: hidden;
        }
        .panelhead {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 20px 20px 15px;
          gap: 10px;
        }
        .panel h2 {
          font-size: 16px;
          margin: 0;
          letter-spacing: -0.2px;
          color: #152338;
        }
        .caption {
          font-size: 12px;
          color: #8594a7;
          margin-top: 4px;
        }
        .filters {
          display: flex;
          gap: 7px;
          padding: 0 20px 15px;
          flex-wrap: wrap;
        }
        .filters button {
          font-size: 11px;
          border: 1px solid #e0e6ef;
          background: #fff;
          border-radius: 20px;
          padding: 7px 11px;
          cursor: pointer;
          color: #5a6b81;
          font-weight: 700;
          transition:
            background 0.15s,
            border-color 0.15s;
        }
        .filters button.active {
          background: #1b4557;
          border-color: #1b4557;
          color: #fff;
        }
        .search {
          padding: 9px 11px;
          border: 1px solid #dce4ec;
          border-radius: 8px;
          width: 190px;
          max-width: 100%;
          font: inherit;
          font-size: 12px;
          outline: none;
        }
        .search:focus {
          border-color: #1b4557;
        }
        .calls {
          max-height: 530px;
          overflow-y: auto;
        }
        .call {
          display: flex;
          gap: 12px;
          padding: 15px 20px;
          border-top: 1px solid #f0f3f7;
          cursor: pointer;
          align-items: start;
          transition: background 0.15s;
        }
        .call:hover,
        .call.selected {
          background: #f7fafc;
        }
        .dot {
          height: 10px;
          width: 10px;
          flex: none;
          border-radius: 50%;
          margin-top: 7px;
        }
        .dot.red {
          background: #df515b;
        }
        .dot.amber {
          background: #efae3a;
        }
        .dot.green {
          background: #48b68a;
        }
        .callbody {
          flex: 1;
          min-width: 0;
        }
        .calltitle {
          display: flex;
          justify-content: space-between;
          gap: 9px;
          font-size: 13px;
          font-weight: 800;
          color: #152338;
        }
        .time {
          font-size: 11px;
          color: #94a1b1;
          font-weight: 500;
          white-space: nowrap;
        }
        .detail {
          font-size: 12px;
          color: #74849a;
          line-height: 1.6;
          margin-top: 3px;
        }
        .tag {
          display: inline-block;
          font-size: 10px;
          font-weight: 800;
          padding: 4px 8px;
          border-radius: 5px;
          margin-top: 7px;
        }
        .tag.red {
          color: #b8323c;
          background: #fff0f0;
        }
        .tag.amber {
          color: #9a660e;
          background: #fff5dd;
        }
        .tag.green {
          color: #228b65;
          background: #e5f8f0;
        }
        .live-pill {
          display: inline-block;
          font-size: 9px;
          font-weight: 800;
          color: #127958;
          background: #dff6ed;
          padding: 2px 5px;
          border-radius: 4px;
        }
        .detailview {
          padding: 0 20px 22px;
        }
        .detailview h3 {
          font-size: 20px;
          margin: 10px 0 4px;
          color: #152338;
        }
        .info {
          display: grid;
          grid-template-columns: 100px 1fr;
          gap: 12px;
          margin: 18px 0;
          font-size: 12px;
        }
        .info dt {
          color: #8b99aa;
        }
        .info dd {
          margin: 0;
          color: #30455d;
          font-weight: 650;
        }
        .note {
          border: 1px solid #e7ecf3;
          border-radius: 9px;
          padding: 13px;
          background: #fafbfd;
          font-size: 12px;
          line-height: 1.65;
          color: #45576c;
        }
        .note b {
          display: block;
          margin-bottom: 5px;
          color: #172b43;
        }
        .actionrow {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
          margin-top: 15px;
        }
        .activity {
          margin-top: 20px;
        }
        .activitygrid {
          display: grid;
          grid-template-columns: 1fr 1fr 1fr;
          gap: 14px;
        }
        .integration {
          padding: 16px 18px;
          border: 1px solid #e7ecf3;
          background: white;
          border-radius: 12px;
        }
        .integration .name {
          font-weight: 800;
          font-size: 14px;
          color: #152338;
        }
        .integration p {
          font-size: 12px;
          line-height: 1.5;
          color: #73839a;
          margin: 8px 0;
        }
        .road {
          font-size: 10px;
          color: #8c669d;
          background: #f5edfa;
          padding: 4px 8px;
          border-radius: 5px;
          display: inline-block;
          font-weight: 800;
        }
        .road.live {
          color: #1b8a65;
          background: #e9f8f0;
        }
        .empty {
          padding: 30px;
          text-align: center;
          color: #94a1b1;
          font-size: 13px;
        }
        .flowline {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
          padding: 0 20px 20px;
          font-size: 11px;
          color: #667990;
        }
        .flowline b {
          padding: 7px 10px;
          background: #f4f7fb;
          border-radius: 7px;
          color: #34536a;
        }
        .meta {
          font-size: 11px;
          color: #9aa7b5;
          margin-left: 5px;
          font-weight: 700;
          letter-spacing: 0.5px;
        }
        .modalbg {
          position: fixed;
          inset: 0;
          background: rgba(11, 26, 44, 0.6);
          display: none;
          align-items: center;
          justify-content: center;
          padding: 18px;
          z-index: 50;
        }
        .modalbg.open {
          display: flex;
        }
        .modal {
          background: white;
          padding: 24px;
          border-radius: 14px;
          max-width: 480px;
          width: 100%;
          box-shadow: 0 20px 70px rgba(0, 0, 0, 0.2);
        }
        .modal h2 {
          margin-top: 0;
          font-size: 18px;
          color: #152338;
        }
        .modal textarea {
          width: 100%;
          min-height: 90px;
          border: 1px solid #dce4ec;
          border-radius: 8px;
          padding: 12px;
          font: inherit;
          font-size: 13px;
          outline: none;
        }
        .modal textarea:focus {
          border-color: #153e51;
        }
        .muted {
          color: #8290a3;
          font-size: 12px;
        }
        .notice {
          font-size: 12px;
          background: #f0f5f9;
          border-radius: 8px;
          padding: 10px;
          margin-top: 10px;
          color: #50657e;
        }

        @media (max-width: 1000px) {
          .two {
            grid-template-columns: 1fr;
          }
          .stats {
            grid-template-columns: repeat(2, 1fr);
          }
        }
        @media (max-width: 700px) {
          .app {
            grid-template-columns: 1fr;
          }
          .sidebar {
            padding: 15px;
            gap: 12px;
          }
          .nav {
            flex-direction: row;
            overflow-x: auto;
          }
          .nav button,
          .nav .nav-link {
            white-space: nowrap;
          }
          .sidebar .smallcap,
          .sidebar .footer {
            display: none;
          }
          .main {
            padding: 20px 14px;
          }
          .top {
            align-items: flex-start;
            flex-direction: column;
          }
          .activitygrid {
            grid-template-columns: 1fr;
          }
          .search {
            width: 125px;
          }
        }
      `}</style>
    </div>
  );
}

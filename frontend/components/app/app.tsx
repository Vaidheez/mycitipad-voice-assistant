'use client';

import { useMemo } from 'react';
import Link from 'next/link';
import { LayoutDashboard } from 'lucide-react';
import { TokenSource } from 'livekit-client';
import { useSession } from '@livekit/components-react';
import { WarningIcon } from '@phosphor-icons/react/dist/ssr';
import type { AppConfig } from '@/app-config';
import { AgentSessionProvider } from '@/components/agents-ui/agent-session-provider';
import { StartAudioButton } from '@/components/agents-ui/start-audio-button';
import { ViewController } from '@/components/app/view-controller';
import { Toaster } from '@/components/ui/sonner';
import { useAgentErrors } from '@/hooks/useAgentErrors';
import { useDebugMode } from '@/hooks/useDebug';
import { getOrCreateCallerId } from '@/lib/callerId';
import { getSandboxTokenSource } from '@/lib/utils';

const IN_DEVELOPMENT = process.env.NODE_ENV !== 'production';

function AppSetup() {
  useDebugMode({ enabled: IN_DEVELOPMENT });
  useAgentErrors();

  return null;
}

interface AppProps {
  appConfig: AppConfig;
}

export function App({ appConfig }: AppProps) {
  const callerId = useMemo(() => getOrCreateCallerId(), []);

  const tokenSource = useMemo(() => {
    const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL || '';
    const endpoint = backendUrl ? `${backendUrl}/api/token` : '/api/token';
    return typeof process.env.NEXT_PUBLIC_CONN_DETAILS_ENDPOINT === 'string'
      ? getSandboxTokenSource(appConfig)
      : TokenSource.endpoint(endpoint, {
          headers: callerId ? { 'x-mcp-caller-id': callerId } : {},
        });
  }, [appConfig, callerId]);

  const session = useSession(
    tokenSource,
    useMemo(
      () => ({
        agentName: appConfig.agentName,
        participantAttributes: callerId ? { mcp_caller_id: callerId } : undefined,
      }),
      [appConfig.agentName, callerId]
    )
  );

  return (
    <AgentSessionProvider session={session}>
      <AppSetup />
      <header className="fixed top-4 right-4 z-40 flex items-center gap-2">
        <Link
          href="/dashboard"
          className="inline-flex items-center gap-1.5 rounded-full border border-slate-700/80 bg-slate-900/80 px-3.5 py-1.5 text-xs font-medium text-slate-200 shadow-md backdrop-blur transition hover:border-emerald-500/50 hover:bg-slate-800 hover:text-white"
        >
          <LayoutDashboard className="h-3.5 w-3.5 text-emerald-400" />
          <span>Staff Dashboard</span>
        </Link>
      </header>
      <main className="grid h-svh grid-cols-1 place-content-center">
        <ViewController appConfig={appConfig} />
      </main>
      <StartAudioButton label="Start Audio" />
      <Toaster
        icons={{
          warning: <WarningIcon weight="bold" />,
        }}
        position="top-center"
        className="toaster group"
        style={
          {
            '--normal-bg': 'var(--popover)',
            '--normal-text': 'var(--popover-foreground)',
            '--normal-border': 'var(--border)',
          } as React.CSSProperties
        }
      />
    </AgentSessionProvider>
  );
}

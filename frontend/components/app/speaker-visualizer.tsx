'use client';

import React, { useMemo } from 'react';
import { Track } from 'livekit-client';
import { useLocalParticipant, useTrackVolume, useVoiceAssistant } from '@livekit/components-react';
import type { UiState } from '@/lib/callState';

interface SpeakerVisualizerProps {
  uiState: UiState;
}

export function SpeakerVisualizer({ uiState }: SpeakerVisualizerProps) {
  const { audioTrack: agentAudioTrack } = useVoiceAssistant();
  const { localParticipant } = useLocalParticipant();

  const micPublication = localParticipant?.getTrackPublication(Track.Source.Microphone);
  const micTrack = micPublication?.audioTrack;

  const agentVolume = useTrackVolume(agentAudioTrack);
  const callerVolume = useTrackVolume(micTrack);

  // Derive bar heights for assistant speaking (multiplied with variation for visual liveliness)
  const assistantHeights = useMemo(() => {
    const vol = Math.min(Math.max(agentVolume, 0), 1);
    const base = 8;
    return [
      Math.min(52, base + vol * 38),
      Math.min(68, base + vol * 58),
      Math.min(84, base + vol * 76),
      Math.min(68, base + vol * 60),
      Math.min(52, base + vol * 40),
    ];
  }, [agentVolume]);

  // Derive mic pulse scale for caller speaking
  const callerScale = useMemo(() => {
    const vol = Math.min(Math.max(callerVolume, 0), 1);
    return 1 + vol * 0.45;
  }, [callerVolume]);

  return (
    <div className="flex h-32 w-full items-center justify-center" aria-hidden="true">
      {/* Ready State: Static Mic Icon */}
      {uiState === 'ready' && (
        <div className="flex size-20 items-center justify-center rounded-full border border-[var(--mcp-text)]/15 bg-[var(--mcp-surface)] text-[var(--mcp-text)] shadow-sm">
          <svg
            className="size-9"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={1.75}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15a3 3 0 003-3V6a3 3 0 00-6 0v6a3 3 0 003 3z"
            />
          </svg>
        </div>
      )}

      {/* Connecting State: Spinner */}
      {uiState === 'connecting' && (
        <div className="flex size-20 items-center justify-center">
          <svg
            className="size-10 animate-spin text-[var(--mcp-accent)] motion-reduce:animate-none"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="3"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
            />
          </svg>
        </div>
      )}

      {/* Listening State: Neutral caller reactive mic pulse */}
      {uiState === 'listening' && (
        <div className="relative flex size-20 items-center justify-center">
          {/* Animated pulsing outer ring reacting to caller volume */}
          <span
            className="absolute inset-0 rounded-full bg-[var(--mcp-text)] opacity-15 transition-transform duration-100 ease-out motion-reduce:transform-none"
            style={{
              transform: `scale(${callerScale})`,
            }}
          />
          <div className="relative flex size-16 items-center justify-center rounded-full border border-[var(--mcp-text)]/20 bg-[var(--mcp-surface)] text-[var(--mcp-text)] shadow-sm">
            <svg
              className="size-8"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15a3 3 0 003-3V6a3 3 0 00-6 0v6a3 3 0 003 3z"
              />
            </svg>
          </div>
        </div>
      )}

      {/* Speaking State: Assistant speaking bars reacting to agent volume */}
      {uiState === 'speaking' && (
        <div className="flex items-center justify-center gap-2">
          {/* Motion safe animated bars */}
          <div className="flex items-center gap-2 motion-reduce:hidden">
            {assistantHeights.map((h, i) => (
              <span
                key={i}
                className="w-2.5 rounded-full bg-[var(--mcp-accent)] transition-all duration-75 ease-out"
                style={{
                  height: `${h}px`,
                }}
              />
            ))}
          </div>

          {/* Reduced motion static indicator */}
          <div className="hidden size-16 items-center justify-center rounded-full bg-[var(--mcp-accent)]/15 text-[var(--mcp-accent)] motion-reduce:flex">
            <svg
              className="size-8"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M19.114 5.636a9 9 0 010 12.728M16.463 8.288a5.25 5.25 0 010 7.424M6.75 8.25l4.72-4.72a.75.75 0 011.28.53v15.88a.75.75 0 01-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.01 9.01 0 012.25 12c0-.83.112-1.633.322-2.396C2.806 8.757 3.63 8.25 4.51 8.25H6.75z"
              />
            </svg>
          </div>
        </div>
      )}

      {/* Ended State: Static call ended indicator */}
      {uiState === 'ended' && (
        <div className="flex size-20 items-center justify-center rounded-full border border-[var(--mcp-text)]/15 bg-[var(--mcp-surface)] text-[var(--mcp-text)]/60 shadow-sm">
          <svg
            className="size-8"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={1.75}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15M12 9l-3 3m0 0l3 3m-3-3h12.75"
            />
          </svg>
        </div>
      )}
    </div>
  );
}

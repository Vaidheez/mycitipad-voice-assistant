'use client';

import React, { useCallback, useEffect, useState } from 'react';
import Image from 'next/image';
import { RoomEvent } from 'livekit-client';
import { useRoomContext, useSessionContext, useVoiceAssistant } from '@livekit/components-react';
import { type UiState, toUiState } from '@/lib/callState';
import { type MicErrorMessage, micMessage } from '@/lib/micErrors';
import { SpeakerVisualizer } from './speaker-visualizer';

const STATE_HEADLINES: Record<UiState, string> = {
  ready: 'Ready when you are',
  connecting: 'Connecting you to our assistant. Please wait.',
  listening: 'Listening to you',
  speaking: 'Assistant is speaking',
  ended: 'Call ended. Thanks for calling MyCitiPad.',
};

export function MyCitiPadCard() {
  const session = useSessionContext();
  const room = useRoomContext();
  const { state: agentState } = useVoiceAssistant();

  const [hasEnded, setHasEnded] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [micError, setMicError] = useState<MicErrorMessage | null>(null);
  const [logoLoaded, setLogoLoaded] = useState(true);

  // Compute the current 5-state UI state
  const uiState = toUiState(session.isConnected, hasEnded, agentState, isStarting);

  // Handle active call end event from LiveKit room
  useEffect(() => {
    if (!room) return;

    const handleDisconnect = () => {
      // Mark as ended only if we had an active connected session
      setHasEnded(true);
    };

    room.on(RoomEvent.Disconnected, handleDisconnect);
    return () => {
      room.off(RoomEvent.Disconnected, handleDisconnect);
    };
  }, [room]);

  // Request mic permission before joining room, then start LiveKit session
  const handleStartCall = useCallback(async () => {
    setMicError(null);
    setIsStarting(true);
    setHasEnded(false);

    try {
      if (typeof navigator !== 'undefined' && navigator.mediaDevices?.getUserMedia) {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        // Stop initial test tracks so LiveKit can take ownership of audio input
        stream.getTracks().forEach((track) => track.stop());
      }

      await session.start();
    } catch (err: unknown) {
      const parsed = micMessage(err);
      setMicError(parsed);
      // Ensure we return to ready state, not ended
      setHasEnded(false);
    } finally {
      setIsStarting(false);
    }
  }, [session]);

  // End active call
  const handleEndCall = useCallback(async () => {
    setHasEnded(true);
    try {
      await session.end();
    } catch {
      // Disconnect cleanly
    }
  }, [session]);

  // Start a second call after call ended without page refresh
  const handleStartNewCall = useCallback(async () => {
    setHasEnded(false);
    await handleStartCall();
  }, [handleStartCall]);

  return (
    <div className="w-full max-w-[420px] px-4 py-6">
      <main
        className="w-full rounded-2xl border border-[var(--mcp-text)]/10 bg-[var(--mcp-surface)] p-6 shadow-xl transition-all sm:p-8"
        style={{
          backgroundColor: 'var(--mcp-surface)',
          color: 'var(--mcp-text)',
        }}
      >
        {/* Brand Header */}
        <header className="flex flex-col items-center text-center">
          <div className="mb-4 flex items-center justify-center">
            {logoLoaded ? (
              <Image
                src="/mycitipad-logo.png"
                alt="MyCitiPad"
                width={160}
                height={40}
                className="h-10 w-auto object-contain"
                priority
                onError={() => setLogoLoaded(false)}
              />
            ) : (
              <div className="flex items-center gap-2 rounded-lg bg-[var(--mcp-primary)] px-3.5 py-1.5 text-sm font-semibold text-white">
                <span className="font-bold tracking-tight">MyCitiPad</span>
              </div>
            )}
          </div>

          <h1 className="text-xl font-bold tracking-tight text-[var(--mcp-text)] sm:text-2xl">
            My Pad. My Space. My Way.
          </h1>
          <p className="mt-1.5 text-xs text-[var(--mcp-text)]/75 sm:text-sm">
            Talk to our virtual assistant about lettings, sales or maintenance.
          </p>
        </header>

        {/* Microphone Permission / Device Error Banner */}
        {micError && (
          <div
            role="alert"
            className="mt-5 rounded-xl border border-[var(--mcp-danger)] bg-[var(--mcp-danger)]/10 p-4 text-left"
          >
            <div className="flex items-start gap-2.5">
              <svg
                className="mt-0.5 size-5 shrink-0 text-[var(--mcp-danger)]"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"
                />
              </svg>
              <div className="flex-1">
                <h2 className="text-sm font-semibold text-[var(--mcp-text)]">{micError.title}</h2>
                <p className="mt-1 text-xs leading-relaxed text-[var(--mcp-text)]/85">
                  {micError.body}
                </p>
                <button
                  type="button"
                  onClick={handleStartCall}
                  className="mt-3 inline-flex items-center justify-center rounded-lg bg-[var(--mcp-primary)] px-3.5 py-1.5 text-xs font-semibold text-white shadow-xs transition-colors hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--mcp-accent)]"
                >
                  Try again
                </button>
              </div>
            </div>
            <p className="mt-3 border-t border-[var(--mcp-danger)]/20 pt-2.5 text-[11px] text-[var(--mcp-text)]/75">
              Or call us on{' '}
              <a
                href="tel:+442035766575"
                className="font-medium underline hover:text-[var(--mcp-accent)]"
              >
                +44 (0)20 3576 6575
              </a>
              .
            </p>
          </div>
        )}

        {/* Visualizer & Speaker Indicator */}
        <div className="my-6">
          <SpeakerVisualizer uiState={uiState} />
        </div>

        {/* Status Headline (Polite Screen Reader Announcement) */}
        <div className="text-center">
          <p aria-live="polite" className="text-sm font-medium text-[var(--mcp-text)] sm:text-base">
            {STATE_HEADLINES[uiState]}
          </p>
        </div>

        {/* Control Button */}
        <div className="mt-6 flex flex-col items-center">
          {uiState === 'ready' && (
            <button
              type="button"
              onClick={handleStartCall}
              className="w-full rounded-xl bg-[var(--mcp-primary)] px-5 py-3 text-sm font-semibold text-white shadow-sm transition-all hover:opacity-95 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--mcp-accent)]"
            >
              Start call
            </button>
          )}

          {uiState === 'connecting' && (
            <button
              type="button"
              disabled
              aria-disabled="true"
              className="w-full cursor-not-allowed rounded-xl bg-[var(--mcp-text)]/15 px-5 py-3 text-sm font-semibold text-[var(--mcp-text)]/50"
            >
              Connecting...
            </button>
          )}

          {(uiState === 'listening' || uiState === 'speaking') && (
            <button
              type="button"
              onClick={handleEndCall}
              className="w-full rounded-xl bg-[var(--mcp-danger)] px-5 py-3 text-sm font-semibold text-white shadow-sm transition-all hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--mcp-danger)]"
            >
              End call
            </button>
          )}

          {uiState === 'ended' && (
            <button
              type="button"
              onClick={handleStartNewCall}
              className="w-full rounded-xl bg-[var(--mcp-primary)] px-5 py-3 text-sm font-semibold text-white shadow-sm transition-all hover:opacity-95 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--mcp-accent)]"
            >
              Start a new call
            </button>
          )}
        </div>

        {/* Footer Note */}
        <footer className="mt-6 text-center">
          <p className="text-[11px] text-[var(--mcp-text)]/60">This call is recorded.</p>
        </footer>
      </main>
    </div>
  );
}

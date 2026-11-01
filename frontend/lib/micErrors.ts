export interface MicErrorMessage {
  title: string;
  body: string;
}

/**
 * Returns user-facing error guidance in British English based on microphone errors.
 *
 * @param err Unknown error caught from navigator.mediaDevices.getUserMedia or LiveKit media device errors
 */
export function micMessage(err: unknown): MicErrorMessage {
  const errorName =
    err instanceof Error
      ? err.name
      : typeof err === 'object' && err !== null && 'name' in err
        ? String((err as { name: unknown }).name)
        : '';

  const errorMessage = err instanceof Error ? err.message : typeof err === 'string' ? err : '';

  const normalized = (errorName + ' ' + errorMessage).toLowerCase();

  if (
    errorName === 'NotAllowedError' ||
    errorName === 'PermissionDeniedError' ||
    normalized.includes('permission denied') ||
    normalized.includes('not allowed') ||
    normalized.includes('permission dismissed')
  ) {
    return {
      title: 'Microphone is blocked',
      body: 'MyCitiPad needs your microphone so the assistant can hear you. Click the padlock icon next to the web address, set Microphone to Allow, then press Try again.',
    };
  }

  if (
    errorName === 'NotFoundError' ||
    errorName === 'DevicesNotFoundError' ||
    normalized.includes('not found') ||
    normalized.includes('devices not found')
  ) {
    return {
      title: 'No microphone found',
      body: 'Connect a microphone or headset, then press Try again.',
    };
  }

  if (
    errorName === 'NotReadableError' ||
    errorName === 'TrackStartError' ||
    normalized.includes('could not start') ||
    normalized.includes('in use')
  ) {
    return {
      title: 'Microphone is in use',
      body: 'Another app may be using your microphone. Close it, then press Try again.',
    };
  }

  return {
    title: "Couldn't start the call",
    body: 'Check your microphone and internet connection, then press Try again.',
  };
}

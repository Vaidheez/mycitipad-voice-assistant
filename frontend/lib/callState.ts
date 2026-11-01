export type UiState = 'ready' | 'connecting' | 'listening' | 'speaking' | 'ended';

/**
 * Maps room connection status, active-call end flag, and LiveKit agent state
 * to one of the 5 defined MyCitiPad UI states.
 *
 * @param roomConnected Whether the LiveKit room session is connected
 * @param hasEnded Whether the call has concluded (room disconnected or agent left after active call)
 * @param agentState LiveKit agent state (e.g. 'speaking', 'listening', 'thinking', 'initializing', etc.)
 * @param isConnecting Optional explicit connecting flag when connection attempt is in progress
 */
export function toUiState(
  roomConnected: boolean,
  hasEnded: boolean,
  agentState: string,
  isConnecting: boolean = false
): UiState {
  if (hasEnded) {
    return 'ended';
  }

  if (!roomConnected) {
    return isConnecting || agentState === 'connecting' ? 'connecting' : 'ready';
  }

  if (agentState === 'speaking') {
    return 'speaking';
  }

  if (agentState === 'listening' || agentState === 'thinking') {
    return 'listening';
  }

  // Connected but agent not yet ready
  return 'connecting';
}

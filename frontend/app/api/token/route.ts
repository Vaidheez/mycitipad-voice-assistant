import { NextResponse } from 'next/server';
import { AccessToken, type AccessTokenOptions, type VideoGrant } from 'livekit-server-sdk';
import { RoomConfiguration } from '@livekit/protocol';

type ConnectionDetails = {
  serverUrl: string;
  roomName: string;
  participantName: string;
  participantToken: string;
};

const API_KEY = process.env.LIVEKIT_API_KEY;
const API_SECRET = process.env.LIVEKIT_API_SECRET;
const LIVEKIT_URL = process.env.LIVEKIT_URL;
const AGENT_NAME = process.env.AGENT_NAME;

export const revalidate = 0;

export async function POST(req: Request) {
  try {
    if (LIVEKIT_URL === undefined) {
      throw new Error('LIVEKIT_URL is not defined');
    }
    if (API_KEY === undefined) {
      throw new Error('LIVEKIT_API_KEY is not defined');
    }
    if (API_SECRET === undefined) {
      throw new Error('LIVEKIT_API_SECRET is not defined');
    }

    // Parse body and extract optional caller ID
    const body = await req.json().catch(() => ({}));
    const callerId =
      body?.participantAttributes?.mcp_caller_id ||
      body?.participant_attributes?.mcp_caller_id ||
      body?.mcp_caller_id ||
      req.headers.get('x-mcp-caller-id');

    let roomConfig: RoomConfiguration | undefined;
    if (body?.room_config) {
      roomConfig = RoomConfiguration.fromJson(body.room_config, { ignoreUnknownFields: true });
    }

    const defaultAgent = AGENT_NAME || 'my-agent';
    if (!roomConfig) {
      roomConfig = RoomConfiguration.fromJson(
        { agents: [{ agentName: defaultAgent }] },
        { ignoreUnknownFields: true }
      );
    } else if (!roomConfig.agents || roomConfig.agents.length === 0) {
      const configJson = (roomConfig.toJson() || {}) as Record<string, unknown>;
      roomConfig = RoomConfiguration.fromJson(
        { ...configJson, agents: [{ agentName: defaultAgent }] },
        { ignoreUnknownFields: true }
      );
    }

    const participantName = 'user';
    const participantIdentity = `voice_assistant_user_${Math.floor(Math.random() * 10_000)}`;
    const roomName = `voice_assistant_room_${Math.floor(Math.random() * 10_000)}`;

    const attributes: Record<string, string> = {};
    if (callerId) {
      attributes['mcp_caller_id'] = String(callerId);
    }

    const participantToken = await createParticipantToken(
      {
        identity: participantIdentity,
        name: participantName,
        attributes,
        metadata: callerId ? JSON.stringify({ mcp_caller_id: callerId }) : undefined,
      },
      roomName,
      roomConfig
    );

    const data: ConnectionDetails = {
      serverUrl: LIVEKIT_URL,
      roomName,
      participantName,
      participantToken,
    };
    const headers = new Headers({
      'Cache-Control': 'no-store',
    });
    return NextResponse.json(data, { headers });
  } catch (error) {
    if (error instanceof Error) {
      console.error(error);
      return new NextResponse(error.message, { status: 500 });
    }
  }
}

function createParticipantToken(
  userInfo: AccessTokenOptions,
  roomName: string,
  roomConfig?: RoomConfiguration
): Promise<string> {
  const at = new AccessToken(API_KEY, API_SECRET, {
    ...userInfo,
    ttl: '15m',
  });
  const grant: VideoGrant = {
    room: roomName,
    roomJoin: true,
    canPublish: true,
    canPublishData: true,
    canSubscribe: true,
  };
  at.addGrant(grant);

  if (roomConfig) {
    at.roomConfig = roomConfig;
  }

  return at.toJwt();
}

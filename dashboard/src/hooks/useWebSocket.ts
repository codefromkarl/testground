import { useCallback, useEffect, useRef, useState } from 'react';
import type { ObsEvent, WSMessage } from '../types';

interface UseWebSocketOptions {
  sessionId: string | null;
  eventTypes?: string[];
  onEvent?: (event: ObsEvent) => void;
  reconnectInterval?: number;
  maxRetries?: number;
}

interface UseWebSocketReturn {
  connected: boolean;
  events: ObsEvent[];
  lastEvent: ObsEvent | null;
  send: (data: Record<string, unknown>) => void;
  clearEvents: () => void;
}

export function useWebSocket({
  sessionId,
  eventTypes,
  onEvent,
  reconnectInterval = 3000,
  maxRetries = 20,
}: UseWebSocketOptions): UseWebSocketReturn {
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<ObsEvent[]>([]);
  const [lastEvent, setLastEvent] = useState<ObsEvent | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);
  const mountedRef = useRef(true);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const clearEvents = useCallback(() => setEvents([]), []);

  const connect = useCallback(() => {
    if (!sessionId) return;

    // Build WS URL
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const params = eventTypes?.length
      ? `?event_types=${eventTypes.join(',')}`
      : '';
    const url = `${protocol}//${host}/ws/events/${sessionId}${params}`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) return;
      setConnected(true);
      retriesRef.current = 0;
    };

    ws.onmessage = (ev) => {
      if (!mountedRef.current) return;
      try {
        const msg: WSMessage = JSON.parse(ev.data);

        // Handle control messages
        if (msg.type === 'connected' || msg.type === 'heartbeat' || msg.type === 'pong' || msg.type === 'subscribed' || msg.type === 'unsubscribed') {
          return;
        }

        // It's an ObsEvent
        const obsEvent = msg as ObsEvent;
        setLastEvent(obsEvent);
        setEvents((prev) => [...prev, obsEvent]);
        onEventRef.current?.(obsEvent);
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onclose = () => {
      if (!mountedRef.current) return;
      setConnected(false);
      wsRef.current = null;

      if (retriesRef.current < maxRetries) {
        retriesRef.current++;
        setTimeout(() => {
          if (mountedRef.current) connect();
        }, reconnectInterval);
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [sessionId, eventTypes, reconnectInterval, maxRetries]);

  const send = useCallback((data: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [connect]);

  return { connected, events, lastEvent, send, clearEvents };
}

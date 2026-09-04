import React, { createContext, useContext, useState, useEffect, useRef, useCallback } from 'react';
import { WS_URL, API_URL } from '../config';

const WebSocketContext = createContext(null);

export const WebSocketProvider = ({ children }) => {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState('connecting');
  const [cameraId, setCameraId] = useState('0');
  const ws = useRef(null);
  const latestPayloadRef = useRef(null);
  const rafRef = useRef(null);
  const retryCount = useRef(0);
  const retryTimeout = useRef(null);
  const isMounted = useRef(true);

  const connect = useCallback(async () => {
    if (!isMounted.current) return;

    setStatus('connecting');

    // Wake up the backend first (Render free tier sleeps after inactivity)
    try {
      await fetch(`${API_URL}/`, { method: 'GET', signal: AbortSignal.timeout(10000) });
    } catch (_) {
      // Backend might still be waking up, proceed anyway
    }

    const url = `${WS_URL}/ws/feed?camera_id=${cameraId}`;

    try {
      if (ws.current) {
        ws.current.onclose = null;
        ws.current.close();
      }

      ws.current = new WebSocket(url);

      ws.current.onopen = () => {
        if (!isMounted.current) return;
        console.log('WS Connected');
        setStatus('connected');
        retryCount.current = 0;
      };

      ws.current.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        latestPayloadRef.current = payload;
        if (rafRef.current !== null) return;
        rafRef.current = window.requestAnimationFrame(() => {
          if (isMounted.current) setData(latestPayloadRef.current);
          rafRef.current = null;
        });
      };

      ws.current.onclose = () => {
        if (!isMounted.current) return;
        console.log('WS Disconnected');
        setStatus('disconnected');
        // Exponential backoff: 2s, 4s, 8s, max 15s
        const delay = Math.min(2000 * Math.pow(1.5, retryCount.current), 15000);
        retryCount.current += 1;
        console.log(`Reconnecting in ${delay}ms (attempt ${retryCount.current})`);
        retryTimeout.current = setTimeout(connect, delay);
      };

      ws.current.onerror = (err) => {
        console.error('WS Error', err);
        ws.current.close();
      };
    } catch (err) {
      console.error('WS setup failed', err);
      retryTimeout.current = setTimeout(connect, 5000);
    }
  }, [cameraId]);

  useEffect(() => {
    isMounted.current = true;
    connect();
    return () => {
      isMounted.current = false;
      clearTimeout(retryTimeout.current);
      if (rafRef.current !== null) {
        window.cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      if (ws.current) {
        ws.current.onclose = null;
        ws.current.close();
      }
    };
  }, [connect]);

  return (
    <WebSocketContext.Provider value={{ data, status, cameraId, setCameraId }}>
      {children}
    </WebSocketContext.Provider>
  );
};

export const useWebSocketData = () => {
  const context = useContext(WebSocketContext);
  if (!context) {
    throw new Error('useWebSocketData must be used within a WebSocketProvider');
  }
  return context;
};

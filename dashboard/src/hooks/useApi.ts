import { useCallback, useEffect, useRef, useState } from 'react';
import type { HealthResponse, SessionListResponse } from '../types';

const API_BASE = '/api';

interface ApiState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

export function useApi<T>(
  url: string | null,
  options?: { skip?: boolean }
): ApiState<T> & { refetch: () => void } {
  const [state, setState] = useState<ApiState<T>>({
    data: null,
    loading: false,
    error: null,
  });
  const abortRef = useRef<AbortController | null>(null);

  const fetchData = useCallback(() => {
    if (!url || options?.skip) {
      setState({ data: null, loading: false, error: null });
      return;
    }

    // Cancel any in-flight request
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setState((prev) => ({ ...prev, loading: true, error: null }));

    fetch(`${API_BASE}${url}`, { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
        return res.json();
      })
      .then((data) => {
        if (!controller.signal.aborted) {
          setState({ data, loading: false, error: null });
        }
      })
      .catch((err) => {
        if (err.name !== 'AbortError') {
          setState({ data: null, loading: false, error: err.message });
        }
      });
  }, [url, options?.skip]);

  useEffect(() => {
    fetchData();
    return () => abortRef.current?.abort();
  }, [fetchData]);

  return { ...state, refetch: fetchData };
}

// Specific hooks
export function useSessions(project?: string, limit = 50) {
  const params = new URLSearchParams();
  if (project) params.set('project', project);
  params.set('limit', String(limit));
  return useApi<SessionListResponse>(`/sessions?${params.toString()}`);
}

export function useHealth() {
  return useApi<HealthResponse>('/health');
}

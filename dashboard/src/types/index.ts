// ─── Event Types ──────────────────────────────────────────────

export interface ObsEvent {
  event_id: string;
  type: string;
  timestamp: number;
  data: Record<string, unknown>;
  source?: {
    framework?: string;
    project?: string;
    test_name?: string;
  };
  trace_id?: string;
  session_id?: string;
}

export interface EventStats {
  [eventType: string]: number;
}

// ─── Session Types ───────────────────────────────────────────

export interface Session {
  session_id: string;
  project?: string;
  framework?: string;
  started_at: number;
  ended_at?: number;
  total_tests?: number;
  passed_tests?: number;
  failed_tests?: number;
  duration_ms?: number;
  gate_result?: GateResult;
  metadata?: Record<string, unknown>;
  event_stats?: EventStats;
  total_events?: number;
}

export interface SessionListResponse {
  sessions: Session[];
  count: number;
}

// ─── Analysis Types ──────────────────────────────────────────

export interface Finding {
  id?: string;
  severity: 'critical' | 'high' | 'medium' | 'low';
  category: string;
  title: string;
  description: string;
  evidence?: string;
  suggested_fix?: string;
  confidence?: number;
}

export interface AnalysisResult {
  analysis_id: string;
  session_id: string;
  timestamp: number;
  analyzer: string;
  findings: Finding[];
  confidence: number;
  summary: string;
  recommendations?: string[];
}

export interface AnalysisListResponse {
  session_id: string;
  analyses: AnalysisResult[];
  count: number;
}

// ─── Gate Types ──────────────────────────────────────────────

export interface GateResult {
  verdict: 'PASS' | 'FAIL' | 'UNKNOWN';
  message?: string;
  score?: number;
  checks?: GateCheck[];
}

export interface GateCheck {
  name: string;
  passed: boolean;
  message?: string;
}

// ─── Screenshot Types ────────────────────────────────────────

export interface Screenshot {
  screenshot_id: string;
  session_id: string;
  filename?: string;
  context?: string;
  timestamp: number;
  size_bytes?: number;
  width?: number;
  height?: number;
  metadata?: Record<string, unknown>;
  base64_data?: string;
}

export interface ScreenshotListResponse {
  status: string;
  screenshots: Screenshot[];
  count: number;
}

// ─── State Types ─────────────────────────────────────────────

export interface StateSnapshot {
  snapshot_id: string;
  session_id: string;
  timestamp: number;
  index: number;
  state: Record<string, unknown>;
}

export interface StateDiff {
  from_index: number;
  to_index: number;
  added: Record<string, unknown>;
  removed: Record<string, unknown>;
  modified: Record<string, { from: unknown; to: unknown }>;
  has_changes: boolean;
}

export interface StateTimelineEntry {
  snapshot_id: string;
  timestamp: number;
  index: number;
  changes: {
    added: Record<string, unknown>;
    removed: Record<string, unknown>;
    modified: Record<string, { from: unknown; to: unknown }>;
  };
}

// ─── Project Types ───────────────────────────────────────────

export interface ProjectSummary {
  project: string;
  total_sessions: number;
  total_events: number;
  pass_rate: number;
  avg_duration_ms: number;
  recent_sessions: Session[];
}

// ─── Health Types ────────────────────────────────────────────

export interface HealthResponse {
  status: string;
  timestamp: number;
  version: string;
}

// ─── WebSocket Types ─────────────────────────────────────────

export type WSMessage =
  | { type: 'connected'; session_id: string; event_types_filter: string[] | null }
  | { type: 'heartbeat'; timestamp: number }
  | { type: 'pong' }
  | { type: 'subscribed'; event_types_filter: string[] | null }
  | { type: 'unsubscribed' }
  | ObsEvent;

// ─── API Types ───────────────────────────────────────────────

export type TimelineResponse = {
  session_id: string;
  events: ObsEvent[];
  count: number;
  timeline_items: TimelineItem[];
};

export interface TimelineItem {
  id: string;
  group: string;
  start: number;
  content: string;
  className: string;
  data: ObsEvent;
}

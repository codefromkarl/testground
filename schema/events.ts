/**
 * 统一测试事件模型 — TypeScript 类型定义
 *
 * 所有测试框架（Vitest / gdUnit4 / Playwright / Airtest）
 * 产生的事件都转换为这个格式。
 */

// ─── 事件类型枚举 ──────────────────────────────────────────

/** 测试生命周期事件 */
export type TestLifecycleType =
  | "test.start"
  | "test.end"
  | "test.skip"
  | "test.fail"
  | "test.error";

/** 断言事件 */
export type AssertionType =
  | "assert.pass"
  | "assert.fail"
  | "assert.semantic";

/** 用户操作事件 */
export type ActionType =
  | "action.click"
  | "action.input"
  | "action.navigate"
  | "action.wait"
  | "action.screenshot";

/** 游戏特有事件 */
export type GameEventType =
  | "game.state_change"
  | "game.scene_load"
  | "game.signal_emit"
  | "game.save"
  | "game.load";

/** Agent 特有事件 */
export type AgentEventType =
  | "agent.llm_call"
  | "agent.tool_call"
  | "agent.tool_result"
  | "agent.thinking";

/** 观测事件 */
export type ObservationType =
  | "observation.snapshot"
  | "observation.coverage"
  | "observation.anomaly";

/** 报告事件 */
export type ReportType =
  | "report.summary"
  | "report.bug_candidate"
  | "report.gate_result";

/** 所有事件类型 */
export type ObsEventType =
  | TestLifecycleType
  | AssertionType
  | ActionType
  | GameEventType
  | AgentEventType
  | ObservationType
  | ReportType;

// ─── 框架和项目标识 ────────────────────────────────────────

export type Framework =
  | "vitest"
  | "gdunit4"
  | "playwright"
  | "airtest"
  | "custom";

export type Project =
  | "travel-agent"
  | "pogongshichongzou"
  | "loopexpedition"
  | string; // 允许扩展

// ─── 事件来源 ──────────────────────────────────────────────

export interface EventSource {
  framework: Framework;
  project: Project;
  file?: string;
  test_name?: string;
  suite?: string;
}

// ─── 核心事件结构 ──────────────────────────────────────────

export interface ObsEvent {
  /** 唯一标识 */
  event_id: string;

  /** 测试会话 ID */
  session_id: string;

  /** Unix 毫秒时间戳 */
  timestamp: number;

  /** 事件来源 */
  source: EventSource;

  /** 事件类型 */
  type: ObsEventType;

  /** 事件数据（不同类型有不同的结构） */
  data: Record<string, unknown>;

  /** 父事件 ID（用于嵌套/因果链） */
  parent_event_id?: string;

  /** 跨系统追踪 ID */
  trace_id?: string;

  /** 追踪 span ID */
  span_id?: string;
}

// ─── 事件数据类型（按事件类型分） ─────────────────────────

/** test.start 数据 */
export interface TestStartData {
  full_name: string;
  tags?: string[];
}

/** test.end / test.fail 数据 */
export interface TestEndData {
  duration_ms: number;
  state: "pass" | "fail" | "skip";
  errors?: Array<{
    message: string;
    stack?: string;
    expected?: unknown;
    actual?: unknown;
  }>;
  assertions?: Array<{
    name: string;
    state: "pass" | "fail";
    duration?: number;
  }>;
}

/** assert.* 数据 */
export interface AssertionData {
  assertion_name: string;
  passed: boolean;
  expected?: unknown;
  actual?: unknown;
  message?: string;
  details?: Record<string, unknown>;
}

/** assert.semantic 数据（AI 语义断言） */
export interface SemanticAssertionData extends AssertionData {
  evaluator: string;
  score: number; // 0-1
  dimensions?: Record<string, number>;
  llm_model?: string;
  llm_reasoning?: string;
}

/** action.* 数据 */
export interface ActionData {
  target?: string;
  value?: unknown;
  duration_ms?: number;
  screenshot_base64?: string;
  metadata?: Record<string, unknown>;
}

/** game.state_change 数据 */
export interface GameStateData {
  scene_path?: string;
  node_path?: string;
  state: Record<string, unknown>;
  previous_state?: Record<string, unknown>;
}

/** game.signal_emit 数据 */
export interface GameSignalData {
  signal_name: string;
  source_node: string;
  args?: unknown[];
}

/** agent.llm_call 数据 */
export interface LlmCallData {
  model: string;
  input_tokens?: number;
  output_tokens?: number;
  duration_ms?: number;
  prompt?: string;
  response?: string;
  error?: string;
}

/** agent.tool_call 数据 */
export interface ToolCallData {
  tool_name: string;
  input: unknown;
  cost_tier?: string;
}

/** agent.tool_result 数据 */
export interface ToolResultData {
  tool_name: string;
  input: unknown;
  output: unknown;
  duration_ms?: number;
  success: boolean;
  error?: string;
}

/** observation.snapshot 数据 */
export interface SnapshotData {
  tree_summary?: string;
  runtime_state?: Record<string, unknown>;
  has_screenshot: boolean;
  screenshot_base64?: string;
}

/** observation.coverage 数据 */
export interface CoverageData {
  seen_events: string[];
  seen_obs_keys: string[];
  event_coverage: number; // 0-1
  obs_coverage: number; // 0-1
  total_flows?: number;
  covered_flows?: number;
  uncovered_flows?: string[];
}

/** observation.anomaly 数据 */
export interface AnomalyData {
  severity: "high" | "medium" | "low";
  category: string;
  description: string;
  step?: number;
  evidence: Record<string, unknown>;
}

/** report.summary 数据 */
export interface SummaryData {
  total_tests: number;
  passed: number;
  failed: number;
  skipped: number;
  duration_ms: number;
  pass_rate: number;
}

/** report.bug_candidate 数据 */
export interface BugCandidateData {
  severity: "high" | "medium" | "low";
  category: string;
  description: string;
  step?: number;
  evidence: Record<string, unknown>;
  confidence: number;
}

/** report.gate_result 数据 */
export interface GateResultData {
  verdict: "PASS" | "FAIL";
  rules: Record<string, {
    value: number;
    threshold: number;
    comparator: string;
    pass: boolean;
  }>;
}

// ─── 测试会话 ──────────────────────────────────────────────

export interface ObsSession {
  session_id: string;
  project: Project;
  framework: Framework;
  started_at: number;
  ended_at?: number;
  total_tests?: number;
  passed_tests?: number;
  failed_tests?: number;
  duration_ms?: number;
  gate_result?: GateResultData;
  metadata?: Record<string, unknown>;
}

// ─── AI 分析结果 ───────────────────────────────────────────

export interface AnalysisResult {
  analysis_id: string;
  session_id: string;
  timestamp: number;
  analyzer: string;
  findings: Array<Record<string, unknown>>;
  confidence: number;
  summary: string;
  recommendations: string[];
}

// ─── Timeline 可视化格式 ───────────────────────────────────

export interface TimelineItem {
  id: string;
  group: string;
  start: number;
  end?: number;
  content: string;
  className?: string;
  data: ObsEvent;
}

export interface TimelineGroup {
  id: string;
  content: string;
  subgroupOrder?: string;
}

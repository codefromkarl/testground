/**
 * Vitest Reporter 适配器 — 连接 TravelAgent 测试
 *
 * 将 Vitest 测试事件转换为统一格式并发送到观测网关。
 *
 * 用法：在 vitest.config.ts 中添加：
 * ```ts
 * import { ObservabilityReporter } from './testing-observability/adapters/vitest/reporter';
 *
 * export default defineConfig({
 *   test: {
 *     reporters: ['default', new ObservabilityReporter('http://localhost:8900')],
 *   },
 * });
 * ```
 */

import type { Reporter, TestCase, TestModule, TestResult } from "vitest/reporters";

interface EventSource {
  framework: string;
  project: string;
  file?: string;
  test_name?: string;
  suite?: string;
}

interface TestEvent {
  event_id: string;
  session_id: string;
  timestamp: number;
  source: EventSource;
  type: string;
  data: Record<string, unknown>;
  parent_event_id?: string;
  trace_id?: string;
  span_id?: string;
}

interface SessionSummary {
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  duration_ms: number;
}

function generateId(): string {
  return `evt_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

function generateSessionId(): string {
  return `session_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * Vitest 可观测性 Reporter
 *
 * 将测试生命周期事件（start / end / fail / skip）
 * 转换为统一事件格式并 POST 到观测网关。
 */
export class ObservabilityReporter implements Reporter {
  private gatewayUrl: string;
  private project: string;
  private sessionId: string;
  private startTime: number = 0;
  private summary: SessionSummary = { total: 0, passed: 0, failed: 0, skipped: 0, duration_ms: 0 };

  constructor(gatewayUrl: string = "http://localhost:8900", project: string = "travel-agent") {
    this.gatewayUrl = gatewayUrl.replace(/\/$/, "");
    this.project = project;
    this.sessionId = generateSessionId();
  }

  /** Vitest 生命周期：整个测试运行开始 */
  onInit(): void {
    this.startTime = Date.now();
    this._emit({
      event_id: generateId(),
      session_id: this.sessionId,
      timestamp: this.startTime,
      source: { framework: "vitest", project: this.project },
      type: "session.start",
      data: { session_id: this.sessionId },
    });
  }

  /** Vitest 生命周期：单个测试用例开始 */
  onTestBegin(test: TestCase): void {
    this.summary.total++;
    this._emit({
      event_id: generateId(),
      session_id: this.sessionId,
      timestamp: Date.now(),
      source: this._testSource(test),
      type: "test.start",
      data: {
        test_name: test.name,
        full_name: test.fullName,
        suite: test.suite?.name,
      },
    });
  }

  /** Vitest 生命周期：单个测试用例结束 */
  onTestFinished(test: TestCase, result: TestResult): void {
    const passed = result.state === "pass";
    if (passed) this.summary.passed++;
    else if (result.state === "skip") this.summary.skipped++;
    else this.summary.failed++;

    this._emit({
      event_id: generateId(),
      session_id: this.sessionId,
      timestamp: Date.now(),
      source: this._testSource(test),
      type: passed ? "test.end" : "test.fail",
      data: {
        test_name: test.name,
        full_name: test.fullName,
        duration_ms: result.duration ?? 0,
        state: result.state,
        errors: result.errors?.map((e) => ({
          message: e.message,
          stack: e.stack,
          expected: e.expected,
          actual: e.actual,
        })),
        retry_count: result.retryCount ?? 0,
      },
    });
  }

  /** Vitest 生命周期：整个测试运行结束 */
  onFinished(): void {
    this.summary.duration_ms = Date.now() - this.startTime;
    this._emit({
      event_id: generateId(),
      session_id: this.sessionId,
      timestamp: Date.now(),
      source: { framework: "vitest", project: this.project },
      type: "session.end",
      data: {
        session_id: this.sessionId,
        summary: { ...this.summary },
        pass_rate: this.summary.total > 0 ? this.summary.passed / this.summary.total : 0,
      },
    });
    // 同时更新会话
    this._updateSession();
  }

  // ─── 内部方法 ──────────────────────────────────────────

  private _testSource(test: TestCase): EventSource {
    return {
      framework: "vitest",
      project: this.project,
      file: test.file?.name,
      test_name: test.name,
      suite: test.suite?.name,
    };
  }

  private _emit(event: TestEvent): void {
    try {
      // 使用同步 fetch（Vitest reporter 回调中 async 可能有问题）
      // 降级为 fire-and-forget
      fetch(`${this.gatewayUrl}/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(event),
      }).catch(() => {
        // 静默失败，不阻塞测试
      });
    } catch {
      // 静默失败
    }
  }

  private _updateSession(): void {
    try {
      fetch(`${this.gatewayUrl}/sessions/${this.sessionId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ended_at: Date.now(),
          total_tests: this.summary.total,
          passed_tests: this.summary.passed,
          failed_tests: this.summary.failed,
          duration_ms: this.summary.duration_ms,
        }),
      }).catch(() => {});
    } catch {
      // 静默失败
    }
  }
}

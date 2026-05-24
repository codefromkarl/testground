# 统一测试观测平台设计方案

> 基于 TravelAgent / pogongshichongzou / loopexpedition 三个项目的实际测试现状

---

## 一、现状分析

### 三个项目的测试现状

| 维度 | TravelAgent (TS) | pogongshichongzou (Godot) | loopexpedition (Godot) |
|------|------------------|---------------------------|------------------------|
| **框架** | Vitest | gdUnit4 + 自定义 smoke | gdUnit4 + Python AI 框架 |
| **测试层数** | 5层 (unit/integration/e2e/eval/cross-layer) | 3层 (unit/smoke/contract) | 5层 (unit/integration/bot/e2e/coverage) |
| **报告格式** | Vitest JSON + 自定义 eval report | 控制台输出 | 结构化 JSON (gate_result) |
| **可观测性** | traceId/spanId 传播 | 无 | RuntimeObservation + telemetry |
| **AI 能力** | LLM-as-Judge 评估 | AI supervised run (Dart) | LLM engine + BugDiscovery + CoverageTracker |
| **覆盖率** | 代码覆盖率 | 无 | 流程覆盖率 (45 flows) |

### 已有的优秀模式（直接复用）

```
TravelAgent 的评估框架
├── 结构化断言 (assertTripPlanStructure)
├── LLM-as-Judge (SemanticEvaluator)
└── 质量守卫 (quality-guard.test.ts) — 元测试

loopexpedition 的 AI 测试框架
├── RuntimeObservation — 运行时观测
├── BugDiscovery — 异常检测
├── CoverageTracker — 覆盖率追踪
├── SummaryReport — 结构化报告
└── EpisodeRunner — 情景编排

pogongshichongzou 的场景测试
├── 场景驱动 (.tscn + .gd 配对)
└── Contract Test (契约测试)
```

### 当前的核心问题

```
1. 三个项目的测试数据格式互不兼容
2. 没有统一的时间线视图
3. AI 分析能力分散在各项目中
4. 跨项目的测试结果无法聚合
5. 缺少"测试行为"本身的可观测性
```

---

## 二、平台定位

### 你要做什么

```
不是：重写测试框架
而是：统一观测层 + 分析层 + 可视化层
```

### 核心价值

```
测试框架 (Vitest/gdUnit4/Playwright)
    ↓ 产生事件
统一事件协议 (Unified Event Protocol)
    ↓ 存储
时序数据库 (ClickHouse/SQLite)
    ↓ 分析
AI 分析层 (异常检测/质量评估/趋势预测)
    ↓ 展示
Timeline UI (可视化回放)
```

---

## 三、统一事件模型

### 核心事件结构

```typescript
// 这是你真正要定义的东西
interface TestEvent {
  // === 元数据 ===
  event_id: string;           // 唯一标识
  session_id: string;         // 测试会话 ID
  timestamp: number;          // Unix ms
  
  // === 来源 ===
  source: {
    framework: "vitest" | "gdunit4" | "playwright" | "airtest" | "custom";
    project: string;          // "travel-agent" | "pogongshichongzou" | "loopexpedition"
    file?: string;            // 测试文件路径
    test_name?: string;       // 测试名称
  };
  
  // === 事件类型 ===
  type: TestEventType;
  
  // === 数据 ===
  data: Record<string, unknown>;
  
  // === 关联 ===
  parent_event_id?: string;   // 父事件（用于嵌套）
  trace_id?: string;          // 跨系统追踪 ID
  span_id?: string;           // 追踪 span
}

// 事件类型枚举
type TestEventType =
  // 测试生命周期
  | "test.start"
  | "test.end"
  | "test.skip"
  | "test.fail"
  | "test.error"
  
  // 断言
  | "assert.pass"
  | "assert.fail"
  | "assert.semantic"         // AI 语义断言
  
  // 执行
  | "action.click"
  | "action.input"
  | "action.navigate"
  | "action.wait"
  | "action.screenshot"
  
  // 游戏特有
  | "game.state_change"
  | "game.scene_load"
  | "game.signal_emit"
  | "game.save"
  | "game.load"
  
  // Agent 特有
  | "agent.llm_call"
  | "agent.tool_call"
  | "agent.tool_result"
  | "agent.thinking"
  
  // 观测
  | "observation.snapshot"
  | "observation.coverage"
  | "observation.anomaly"
  
  // 报告
  | "report.summary"
  | "report.bug_candidate"
  | "report.gate_result";
```

### 项目特定扩展

```typescript
// TravelAgent 扩展
interface TravelAgentEvent extends TestEvent {
  data: {
    tool_name?: string;
    tool_input?: unknown;
    tool_output?: unknown;
    llm_model?: string;
    llm_tokens?: number;
    eval_score?: number;
    eval_dimensions?: Record<string, number>;
  };
}

// 游戏项目扩展
interface GameTestEvent extends TestEvent {
  data: {
    scene_path?: string;
    node_path?: string;
    signal_name?: string;
    game_state?: Record<string, unknown>;
    screenshot_base64?: string;
    frame_number?: number;
  };
}
```

---

## 四、适配器层（连接现有框架）

### 架构

```
┌─────────────────────────────────────────────────────────┐
│                    统一事件网关                           │
│                  (Event Gateway)                         │
└─────────┬───────────────┬───────────────┬───────────────┘
          │               │               │
    ┌─────▼─────┐   ┌─────▼─────┐   ┌─────▼─────┐
    │  Vitest   │   │  gdUnit4  │   │ Playwright│
    │  Adapter  │   │  Adapter  │   │  Adapter  │
    └───────────┘   └───────────┘   └───────────┘
```

### Vitest Adapter (TravelAgent)

```typescript
// src/testing-observability/vitest-adapter.ts
import type { Reporter, TestCase, TestResult } from "vitest/reporters";
import { EventGateway } from "./gateway";

export class ObservabilityReporter implements Reporter {
  private gateway: EventGateway;

  constructor(gatewayUrl: string) {
    this.gateway = new EventGateway(gatewayUrl);
  }

  onTestBegin(test: TestCase) {
    this.gateway.emit({
      event_id: generateId(),
      session_id: this.sessionId,
      timestamp: Date.now(),
      source: {
        framework: "vitest",
        project: "travel-agent",
        file: test.file?.name,
        test_name: test.name,
      },
      type: "test.start",
      data: {
        full_name: test.fullName,
        suite: test.suite?.name,
      },
    });
  }

  onTestFinished(test: TestCase, result: TestResult) {
    this.gateway.emit({
      event_id: generateId(),
      session_id: this.sessionId,
      timestamp: Date.now(),
      source: {
        framework: "vitest",
        project: "travel-agent",
        file: test.file?.name,
        test_name: test.name,
      },
      type: result.state === "pass" ? "test.end" : "test.fail",
      data: {
        duration_ms: result.duration,
        state: result.state,
        errors: result.errors?.map(e => ({
          message: e.message,
          stack: e.stack,
        })),
        assertions: result.assertionResults?.map(a => ({
          name: a.fullName,
          state: a.status,
          duration: a.duration,
        })),
      },
    });
  }
}
```

### gdUnit4 Adapter (Godot 项目)

```gdscript
# addons/test-observability/observer.gd
extends RefCounted

var _gateway_url: String
var _session_id: String
var _http: HTTPRequest

func _init(gateway_url: String) -> void:
    _gateway_url = gateway_url
    _session_id = _generate_session_id()
    _http = HTTPRequest.new()

func emit(type: String, data: Dictionary) -> void:
    var event := {
        "event_id": _generate_id(),
        "session_id": _session_id,
        "timestamp": Time.get_unix_time_from_system() * 1000,
        "source": {
            "framework": "gdunit4",
            "project": ProjectSettings.get_setting("application/config/name"),
        },
        "type": type,
        "data": data,
    }
    _send(event)

func on_test_start(test_name: String, file_path: String) -> void:
    emit("test.start", {
        "test_name": test_name,
        "file": file_path,
    })

func on_test_end(test_name: String, passed: bool, duration_ms: int) -> void:
    emit("test.end" if passed else "test.fail", {
        "test_name": test_name,
        "passed": passed,
        "duration_ms": duration_ms,
    })

func on_assertion(name: String, passed: bool, details: Dictionary = {}) -> void:
    emit("assert.pass" if passed else "assert.fail", {
        "assertion_name": name,
        "details": details,
    })

func emit_game_state(scene_path: String, state: Dictionary) -> void:
    emit("game.state_change", {
        "scene": scene_path,
        "state": state,
    })

func emit_screenshot(image: Image, context: String = "") -> void:
    emit("action.screenshot", {
        "context": context,
        "image_base64": Marshalls.raw_to_base64(image.save_png_to_buffer()),
    })
```

### Python Adapter (loopexpedition 的 AI 测试)

```python
# scripts/ai_testing/event_adapter.py
"""将现有 AI 测试框架的遥测数据转换为统一事件格式。"""

from __future__ import annotations
import time
import uuid
from typing import Any, Dict, Optional
import httpx


class UnifiedEventEmitter:
    """将 loopexpedition 的 telemetry 转换为统一事件。"""

    def __init__(self, gateway_url: str, project: str = "loopexpedition") -> None:
        self.gateway_url = gateway_url
        self.project = project
        self.session_id = str(uuid.uuid4())
        self._client = httpx.Client(timeout=5.0)

    def emit(self, event_type: str, data: Dict[str, Any], 
             trace_id: Optional[str] = None) -> None:
        event = {
            "event_id": str(uuid.uuid4()),
            "session_id": self.session_id,
            "timestamp": int(time.time() * 1000),
            "source": {
                "framework": "custom",
                "project": self.project,
            },
            "type": event_type,
            "data": data,
        }
        if trace_id:
            event["trace_id"] = trace_id
        self._client.post(f"{self.gateway_url}/events", json=event)

    def from_observation(self, obs: "Observation") -> None:
        """转换 RuntimeObservation 为统一事件。"""
        self.emit("observation.snapshot", {
            "tree_summary": obs.tree_summary(),
            "runtime_state": obs.runtime_state,
            "has_screenshot": bool(obs.screenshot_base64),
        })

    def from_telemetry(self, telemetry: Dict[str, Any]) -> None:
        """转换单条 telemetry 记录为统一事件。"""
        event_type = telemetry.get("type", "game.state_change")
        self.emit(event_type, telemetry)

    def from_bug_candidate(self, bug: "BugCandidate") -> None:
        """转换 BugCandidate 为统一事件。"""
        self.emit("report.bug_candidate", {
            "severity": bug.severity,
            "category": bug.category,
            "description": bug.description,
            "step": bug.step,
            "evidence": bug.evidence,
        })

    def from_coverage(self, tracker: "CoverageTracker") -> None:
        """转换覆盖率数据为统一事件。"""
        self.emit("observation.coverage", {
            "seen_events": list(tracker._seen_events),
            "seen_obs_keys": list(tracker._seen_obs_keys),
            "event_coverage": tracker.event_coverage,
            "obs_coverage": tracker.obs_coverage,
        })
```

---

## 五、存储层

### 方案选择

```
Phase 1: SQLite（零依赖，本地开发）
Phase 2: ClickHouse（生产环境，时序优化）
```

### 表结构

```sql
-- 统一事件表
CREATE TABLE test_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    timestamp INTEGER NOT NULL,  -- Unix ms
    
    -- 来源
    framework TEXT NOT NULL,
    project TEXT NOT NULL,
    file TEXT,
    test_name TEXT,
    
    -- 事件
    type TEXT NOT NULL,
    data TEXT NOT NULL,  -- JSON
    
    -- 关联
    parent_event_id TEXT,
    trace_id TEXT,
    span_id TEXT,
    
    -- 索引
    INDEX idx_session (session_id),
    INDEX idx_timestamp (timestamp),
    INDEX idx_type (type),
    INDEX idx_project (project),
    INDEX idx_trace (trace_id)
);

-- 测试会话表
CREATE TABLE test_sessions (
    session_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    framework TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    total_tests INTEGER,
    passed_tests INTEGER,
    failed_tests INTEGER,
    duration_ms INTEGER,
    gate_result TEXT,  -- JSON: 门禁结果
    metadata TEXT      -- JSON: 额外元数据
);

-- AI 分析结果表
CREATE TABLE ai_analyses (
    analysis_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    analyzer TEXT NOT NULL,  -- "bug_discovery" | "semantic_eval" | "quality_guard"
    result TEXT NOT NULL,    -- JSON
    confidence REAL,
    INDEX idx_session (session_id)
);
```

---

## 六、AI 分析层

### 整合三个项目的 AI 能力

```
┌─────────────────────────────────────────────────────────┐
│                   AI Analysis Engine                     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │  Bug        │  │  Semantic   │  │  Quality    │     │
│  │  Discovery  │  │  Evaluator  │  │  Guard      │     │
│  │  (loopexp)  │  │  (Travel)   │  │  (Travel)   │     │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘     │
│         │               │               │             │
│         └───────────────┼───────────────┘             │
│                         │                             │
│                  ┌──────▼──────┐                      │
│                  │  Anomaly    │                      │
│                  │  Detector   │                      │
│                  └──────┬──────┘                      │
│                         │                             │
│                  ┌──────▼──────┐                      │
│                  │  Trend      │                      │
│                  │  Analyzer   │                      │
│                  └─────────────┘                      │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 分析器接口

```python
# ai_analyzers/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class AnalysisResult:
    analyzer: str
    session_id: str
    findings: List[Dict[str, Any]]
    confidence: float  # 0-1
    summary: str
    recommendations: List[str]


class BaseAnalyzer(ABC):
    """所有 AI 分析器的基类。"""
    
    @abstractmethod
    def analyze(self, events: List[Dict[str, Any]]) -> AnalysisResult:
        """分析一组事件，返回结果。"""
        ...

    @abstractmethod
    def name(self) -> str:
        ...
```

### Bug Discovery 分析器（来自 loopexpedition）

```python
# ai_analyzers/bug_discovery.py
from .base import BaseAnalyzer, AnalysisResult


class BugDiscoveryAnalyzer(BaseAnalyzer):
    """基于 episode trace 的异常检测。"""
    
    def __init__(self, max_steps_without_progress: int = 8) -> None:
        self.max_steps_without_progress = max_steps_without_progress

    def name(self) -> str:
        return "bug_discovery"

    def analyze(self, events: list[dict]) -> AnalysisResult:
        findings = []
        
        # 检测卡住状态
        stuck_events = self._detect_stuck_states(events)
        findings.extend(stuck_events)
        
        # 检测异常奖励
        reward_anomalies = self._detect_reward_anomalies(events)
        findings.extend(reward_anomalies)
        
        # 检测未探索路径
        unexplored = self._detect_unexplored_actions(events)
        findings.extend(unexplored)
        
        return AnalysisResult(
            analyzer=self.name(),
            session_id=events[0]["session_id"] if events else "",
            findings=findings,
            confidence=0.8,
            summary=f"发现 {len(findings)} 个潜在问题",
            recommendations=self._generate_recommendations(findings),
        )
```

### Semantic Evaluator（来自 TravelAgent）

```python
# ai_analyzers/semantic_eval.py
from .base import BaseAnalyzer, AnalysisResult


class SemanticEvaluator(BaseAnalyzer):
    """LLM-as-Judge 语义评估。"""
    
    def __init__(self, llm_client) -> None:
        self.llm = llm_client

    def name(self) -> str:
        return "semantic_eval"

    def analyze(self, events: list[dict]) -> AnalysisResult:
        # 提取 Agent 交互事件
        agent_events = [e for e in events if e["type"].startswith("agent.")]
        
        findings = []
        for event in agent_events:
            if event["type"] == "agent.tool_result":
                score = self._evaluate_tool_output(event)
                if score < 0.6:
                    findings.append({
                        "type": "low_quality_output",
                        "tool": event["data"].get("tool_name"),
                        "score": score,
                        "output": event["data"].get("output")[:200],
                    })
        
        return AnalysisResult(
            analyzer=self.name(),
            session_id=events[0]["session_id"] if events else "",
            findings=findings,
            confidence=0.7,
            summary=f"评估了 {len(agent_events)} 个 Agent 交互",
            recommendations=[],
        )
```

---

## 七、Timeline 可视化

### 前端方案

```
Phase 1: 基于 vis-timeline 的简单 Timeline
Phase 2: 基于 React Flow 的交互式 Timeline
Phase 3: 参考 Perfetto 的高级 Trace Viewer
```

### Timeline 数据格式

```typescript
interface TimelineItem {
  id: string;
  group: string;        // 分组：按项目/按测试文件/按事件类型
  start: number;        // 时间戳
  end?: number;         // 结束时间（可选）
  content: string;      // 显示内容
  className?: string;   // 样式类名
  data: TestEvent;      // 原始事件数据
}

interface TimelineGroup {
  id: string;
  content: string;
  subgroupOrder?: string;
}
```

### 可视化页面结构

```
┌─────────────────────────────────────────────────────────────┐
│  🔍 Filter: [Project ▼] [Framework ▼] [Type ▼] [Time Range]│
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  travel-agent ──────●────────●──────●───────────────●────── │
│                     │        │      │               │       │
│  pogongshichongzou ─┼──●─────┼──────┼──────●────────┼────── │
│                     │  │     │      │      │        │       │
│  loopexpedition ────┼──┼─────●──────┼──────┼────────●────── │
│                     │  │     │      │      │        │       │
│  ───────────────────┴──┴─────┴──────┴──────┴────────┴────── │
│  10:00              10:05   10:10  10:15  10:20    10:25    │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  📊 Event Detail                                            │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Type: agent.tool_call                               │   │
│  │ Tool: search_weather                                │   │
│  │ Input: {"city": "杭州", "date": "2026-05-20"}      │   │
│  │ Duration: 234ms                                     │   │
│  │ Status: ✅                                          │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  🤖 AI Analysis                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ ⚠️ 检测到异常: weather API 返回空数据 (confidence: 0.9)│   │
│  │ 💡 建议: 检查 mock 数据是否覆盖该场景                 │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 八、API 网关

### FastAPI 实现

```python
# gateway/main.py
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional
import sqlite3
import json

app = FastAPI(title="Test Observability Gateway")

# 数据库连接
db = sqlite3.connect("test_observability.db", check_same_thread=False)


class TestEvent(BaseModel):
    event_id: str
    session_id: str
    timestamp: int
    source: dict
    type: str
    data: dict
    parent_event_id: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None


@app.post("/events")
async def ingest_event(event: TestEvent, background_tasks: BackgroundTasks):
    """接收测试事件。"""
    background_tasks.add_task(store_event, event)
    background_tasks.add_task(trigger_analysis, event)
    return {"status": "accepted", "event_id": event.event_id}


@app.post("/events/batch")
async def ingest_events(events: List[TestEvent], background_tasks: BackgroundTasks):
    """批量接收测试事件。"""
    for event in events:
        background_tasks.add_task(store_event, event)
    return {"status": "accepted", "count": len(events)}


@app.get("/sessions/{session_id}/timeline")
async def get_timeline(session_id: str):
    """获取测试会话的时间线数据。"""
    cursor = db.execute(
        "SELECT * FROM test_events WHERE session_id = ? ORDER BY timestamp",
        (session_id,)
    )
    events = [dict(zip([col[0] for col in cursor.description], row)) 
              for row in cursor.fetchall()]
    return {"session_id": session_id, "events": events}


@app.get("/sessions/{session_id}/analysis")
async def get_analysis(session_id: str):
    """获取 AI 分析结果。"""
    cursor = db.execute(
        "SELECT * FROM ai_analyses WHERE session_id = ? ORDER BY timestamp",
        (session_id,)
    )
    analyses = [dict(zip([col[0] for col in cursor.description], row)) 
                for row in cursor.fetchall()]
    return {"session_id": session_id, "analyses": analyses}


@app.get("/projects/{project}/summary")
async def get_project_summary(project: str, days: int = 7):
    """获取项目测试摘要。"""
    # ...
    pass


@app.get("/sessions/{session_id}/gate")
async def get_gate_result(session_id: str):
    """获取门禁结果（兼容 loopexpedition 的 gate_result 格式）。"""
    cursor = db.execute(
        "SELECT gate_result FROM test_sessions WHERE session_id = ?",
        (session_id,)
    )
    row = cursor.fetchone()
    if row and row[0]:
        return json.loads(row[0])
    return {"verdict": "UNKNOWN"}
```

---

## 九、实施路线

### Phase 1: 基础设施（1-2 周）

```
目标：能接收事件、存储、查询

□ 定义统一事件 schema (TypeScript/Python 类型)
□ 实现 SQLite 存储层
□ 实现 FastAPI 网关 (POST /events, GET /timeline)
□ 实现 Vitest Reporter (TravelAgent)
□ 实现 Python EventEmitter (loopexpedition)
□ 基础 Timeline 页面 (vis-timeline)
```

### Phase 2: 分析能力（2-3 周）

```
目标：能自动分析测试结果

□ 整合 BugDiscovery (从 loopexpedition 迁移)
□ 整合 SemanticEvaluator (从 TravelAgent 迁移)
□ 整合 QualityGuard (从 TravelAgent 迁移)
□ 实现 AnomalyDetector (跨项目异常检测)
□ AI 分析结果存储和查询
□ Timeline 页面集成 AI 分析标注
```

### Phase 3: 高级功能（3-4 周）

```
目标：生产级可用

□ gdUnit4 Adapter (pogongshichongzou)
□ Playwright Adapter (Web UI 测试)
□ ClickHouse 存储后端
□ 截图/视频关联
□ 跨项目对比分析
□ 趋势分析和预测
□ 告警和通知
```

### Phase 4: 生态集成（持续）

```
目标：与现有工具链深度集成

□ CI/CD 集成 (GitHub Actions)
□ Slack/飞书通知
□ Grafana 仪表板
□ 自定义规则引擎
□ 测试生成建议
```

---

## 十、目录结构

```
testground/
├── schema/
│   ├── events.ts              # TypeScript 事件类型定义
│   ├── events.py              # Python 事件类型定义
│   └── events.json            # JSON Schema (用于验证)
│
├── gateway/
│   ├── main.py                # FastAPI 网关
│   ├── storage.py             # 存储层
│   ├── models.py              # 数据模型
│   └── requirements.txt
│
├── adapters/
│   ├── vitest/
│   │   └── reporter.ts        # Vitest Reporter
│   ├── gdunit4/
│   │   └── observer.gd        # gdUnit4 Observer
│   ├── playwright/
│   │   └── reporter.ts        # Playwright Reporter
│   └── python/
│       └── emitter.py         # Python EventEmitter
│
├── analyzers/
│   ├── base.py                # 分析器基类
│   ├── bug_discovery.py       # 异常检测
│   ├── semantic_eval.py       # 语义评估
│   ├── quality_guard.py       # 质量守卫
│   ├── anomaly_detector.py    # 跨项目异常检测
│   └── trend_analyzer.py      # 趋势分析
│
├── timeline/
│   ├── index.html             # Timeline 页面
│   ├── app.ts                 # 前端逻辑
│   └── styles.css             # 样式
│
├── docker-compose.yml         # 本地开发环境
└── README.md
```

---

## 十一、与现有系统的关系

```
┌─────────────────────────────────────────────────────────────┐
│                    现有测试框架                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │  Vitest  │  │  gdUnit4 │  │Playwright│  │  Airtest │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘   │
│       │             │             │             │         │
│       └─────────────┼─────────────┼─────────────┘         │
│                     │             │                       │
│              ┌──────▼─────────────▼──────┐                │
│              │    统一事件适配器层         │                │
│              │    (Adapters)              │                │
│              └──────────────┬────────────┘                │
│                             │                             │
│  ┌──────────────────────────▼──────────────────────────┐  │
│  │              统一观测平台 (本方案)                    │  │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌───────┐ │  │
│  │  │ Gateway │  │ Storage │  │ AI 分析  │  │Timeline│ │  │
│  │  └─────────┘  └─────────┘  └─────────┘  └───────┘ │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 十二、关键决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 事件格式 | JSON | 兼容性好，易于扩展 |
| 存储 | SQLite → ClickHouse | 渐进式，先简单后高性能 |
| 网关 | FastAPI | Python 生态好，async 支持 |
| Timeline | vis-timeline | 轻量，易集成 |
| AI 分析 | 整合现有代码 | 不重复造轮子 |
| 部署 | Docker Compose | 本地开发友好 |

---

## 十三、下一步行动

1. **立即可做**：定义 `schema/events.ts` 和 `schema/events.py`
2. **第一周**：实现 `gateway/` + `adapters/vitest/`
3. **第二周**：实现 `timeline/` 基础页面
4. **第三周**：整合 `analyzers/bug_discovery.py`

---

## 附录：与你已有代码的映射

| 你的代码 | 平台组件 | 迁移方式 |
|----------|----------|----------|
| TravelAgent `evaluators.test.ts` | `analyzers/semantic_eval.py` | 提取评估逻辑 |
| TravelAgent `quality-guard.test.ts` | `analyzers/quality_guard.py` | 提取守卫规则 |
| TravelAgent `trace-context.ts` | `schema/events.ts` 的 trace 字段 | 复用 traceId 生成 |
| loopexpedition `bug_discovery.py` | `analyzers/bug_discovery.py` | 直接迁移 |
| loopexpedition `coverage_tracker.py` | `analyzers/coverage.py` | 直接迁移 |
| loopexpedition `runtime_observation.py` | `adapters/python/emitter.py` | 适配数据格式 |
| loopexpedition `summary_report.py` | `gateway/` 的报告 API | 参考格式 |
| pogongshichongzou smoke tests | `adapters/gdunit4/observer.gd` | 新建适配器 |

# 统一测试观测平台

> 跨项目测试事件采集、AI 分析与 Timeline 可视化

## 定位

**不是**重写测试框架，**而是**在 Vitest / gdUnit4 / Playwright 之上搭建统一观测层。

```
执行层: Vitest / gdUnit4 / Playwright (不动)
    ↓ 产生事件
适配层: Reporter / Observer / EventEmitter (薄适配器)
    ↓ 统一格式
存储层: SQLite → ClickHouse (渐进)
    ↓ 查询
分析层: BugDiscovery + SemanticEval + QualityGuard (整合)
    ↓ 展示
可视化层: Timeline UI (vis-timeline)
```

## 支持的项目

| 项目 | 框架 | 适配器 |
|------|------|--------|
| TravelAgent | Vitest | `adapters/vitest/reporter.ts` |
| pogongshichongzou | gdUnit4 | `adapters/gdunit4/observer.gd` |
| loopexpedition | gdUnit4 + Python | `adapters/python/emitter.py` |

## 快速开始

```bash
# 安装依赖
make install

# 启动网关
make gateway

# 运行测试
make test

# 打开 Timeline (另起终端)
make timeline
# 访问 http://localhost:8901
```

## 项目结构

```
testground/
├── schema/                 # 统一事件模型
│   ├── events.ts           # TypeScript 类型
│   ├── events.py           # Python 类型 + 工厂函数
│   └── events.schema.json  # JSON Schema
├── gateway/                # FastAPI 网关
│   ├── main.py             # API 路由
│   ├── storage.py          # SQLite 存储
│   └── requirements.txt
├── adapters/               # 各框架适配器
│   ├── vitest/reporter.ts  # Vitest Reporter (TS)
│   ├── gdunit4/observer.gd # gdUnit4 Observer (GDScript)
│   └── python/emitter.py   # Python EventEmitter
├── analyzers/              # AI 分析器
│   ├── base.py             # 基类
│   ├── bug_discovery.py    # 异常检测
│   ├── semantic_eval.py    # 语义评估
│   ├── quality_guard.py    # 质量守卫
│   └── anomaly_detector.py # 跨项目异常检测
├── timeline/               # Timeline 前端
│   └── index.html
├── tests/                  # 平台自身测试
│   └── test_platform.py
├── docker-compose.yml
├── Dockerfile
├── Makefile
└── pyproject.toml
```

## 事件模型

所有测试事件统一为 `TestEvent` 格式：

```json
{
  "event_id": "uuid",
  "session_id": "session-xxx",
  "timestamp": 1716500000000,
  "source": { "framework": "vitest", "project": "travel-agent" },
  "type": "agent.tool_call",
  "data": { "tool_name": "search_weather", "input": {...} },
  "trace_id": "trace_xxx"
}
```

支持的事件类型：`test.*`, `assert.*`, `action.*`, `game.*`, `agent.*`, `observation.*`, `report.*`

## 各项目接入方式

### TravelAgent (Vitest)

```ts
// vitest.config.ts
import { ObservabilityReporter } from './testing-observability/adapters/vitest/reporter';

export default defineConfig({
  test: {
    reporters: ['default', new ObservabilityReporter('http://localhost:8900')],
  },
});
```

### pogongshichongzou / loopexpedition (gdUnit4)

```gdscript
# 测试脚本中
var observer = load("res://addons/test-observability/observer.gd").new("http://localhost:8900")
observer.on_test_start("test_battle", "res://test/unit/test_battle.gd")
# ... 执行测试 ...
observer.on_test_end("test_battle", true, 120)
```

### loopexpedition (Python AI 测试)

```python
from adapters.python.emitter import emitter_session

with emitter_session("http://localhost:8900") as emitter:
    emitter.from_observation(obs)
    emitter.from_bug_candidate(bug)
    emitter.from_gate_result(gate_result)
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/events` | 接收单个事件 |
| POST | `/events/batch` | 批量接收事件 |
| GET | `/sessions/{id}/timeline` | 获取时间线 |
| GET | `/sessions/{id}/analysis` | 获取 AI 分析 |
| GET | `/sessions/{id}/gate` | 获取门禁结果 |
| GET | `/projects/{name}/summary` | 项目统计 |
| GET | `/health` | 健康检查 |

## AI 分析器

### 传统分析器（规则引擎）

- **BugDiscoveryAnalyzer**: 检测连续失败、慢测试、重复失败、未完成测试
- **SemanticEvaluator**: LLM-as-Judge 语义质量评估
- **QualityGuard**: 断言密度、测试粒度、错误覆盖检查
- **AnomalyDetector**: 跨项目通过率、事件分布、时间异常检测

### 分析流水线（多窄 Agent 架构）

受 [evilsocket/audit](https://github.com/evilsocket/audit) 启发的 5 阶段分析流水线：

```
Recon → Hunt(并行) → Validate(对抗) → Feedback(扩散) → Report
```

| 阶段 | 职责 | 模式 |
|------|------|------|
| **Recon** | 扫描事件，生成窄范围分析任务 | LLM / 规则 |
| **Hunt** | 多个窄 Agent 并行检测（flaky/regression/coverage/semantic/perf） | LLM / 规则 |
| **Validate** | 对抗验证：用不同 prompt 视角推翻 findings | 仅 LLM |
| **Feedback** | 从已确认 findings 提取模式，扩散检测 | LLM / 规则 |
| **Report** | 汇总报告，输出质量分和行动建议 | LLM / 规则 |

用法：

```python
from analyzers import AnalysisPipeline, PipelineConfig, PipelineState

state = PipelineState(db_path)
config = PipelineConfig(use_llm=False)  # 规则引擎模式
# config = PipelineConfig(use_llm=True)  # LLM 模式
pipeline = AnalysisPipeline(state=state, config=config)

result = pipeline.run(events, session_id="sess-1")
print(result.confirmed_findings)  # 已确认的问题
print(result.quality_score)        # 0-100 质量分
print(result.recommendations)      # 行动建议
```

详见 `analyzers/pipeline/` 目录。

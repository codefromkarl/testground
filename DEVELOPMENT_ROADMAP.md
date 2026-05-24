# 测试观测平台 — 开发方向分析

> 基于当前代码库的深度分析

---

## 一、当前状态评估

### 已完成 ✅

| 模块 | 完成度 | 说明 |
|------|--------|------|
| Schema 层 | 95% | TypeScript/Python 类型完整，工厂函数齐全 |
| Gateway | 70% | 14 个端点，SQLite 存储，基础 CRUD |
| Adapters | 60% | Vitest/gdUnit4/Python 基础适配 |
| Analyzers | 75% | 4 个分析器，规则引擎为主 |
| Timeline | 30% | 基础 HTML，仅支持查看 |
| CLI | 70% | 6 个命令，基础功能 |
| 测试 | 81% | 56 个测试，覆盖核心逻辑 |

### 关键数据

```
代码行数: 3,745 行 Python
测试数量: 56 个
覆盖率: 81%
API 端点: 14 个
分析器: 4 个
适配器: 3 个
```

---

## 二、开发方向矩阵

### 按优先级排序

```
高优先级 (核心功能完善)
├── 1. LLM 集成 — SemanticEvaluator 真实评估
├── 2. 实时事件流 — WebSocket/SSE
├── 3. 存储层升级 — ClickHouse 支持
└── 4. CI/CD 集成 — GitHub Actions

中优先级 (功能扩展)
├── 5. 更多适配器 — Playwright/Airtest
├── 6. Dashboard — React/Vue 重构
├── 7. 告警系统 — Slack/飞书通知
└── 8. 报告生成 — PDF/HTML 报告

低优先级 (生态完善)
├── 9. 用户认证 — API Key/OAuth
├── 10. API 文档 — OpenAPI/Swagger
├── 11. 插件系统 — 自定义分析器
└── 12. 多租户 — 项目隔离
```

---

## 三、详细开发方向

### 1. LLM 集成 (高优先级)

**当前问题**: SemanticEvaluator 使用规则引擎，无法做语义评估

**目标**: 接入 Claude/GPT API，实现真正的 AI 评估

```python
# 当前 (规则引擎)
def evaluate(input, output):
    if len(output) < 10:
        return 0.1
    return 0.8

# 目标 (LLM 评估)
async def evaluate(input, output):
    response = await llm.complete(f"""
        评估以下输出质量:
        输入: {input}
        输出: {output}
        返回 0-1 分数和原因
    """)
    return parse_score(response)
```

**工作量**: 2-3 天

---

### 2. 实时事件流 (高优先级)

**当前问题**: 客户端需要轮询才能获取新事件

**目标**: WebSocket/SSE 实时推送

```python
# 新增端点
@app.websocket("/ws/events/{session_id}")
async def event_stream(websocket: WebSocket, session_id: str):
    await websocket.accept()
    async for event in event_store.subscribe(session_id):
        await websocket.send_json(event)
```

**工作量**: 2-3 天

---

### 3. 存储层升级 (高优先级)

**当前问题**: SQLite 仅适合单机开发

**目标**: 支持 ClickHouse 用于生产环境

```python
# 抽象存储接口
class StorageBackend(ABC):
    @abstractmethod
    async def store_event(self, event: TestEvent): ...
    
    @abstractmethod
    async def query_events(self, query: EventQuery) -> List[TestEvent]: ...

# 实现
class SQLiteStorage(StorageBackend): ...
class ClickHouseStorage(StorageBackend): ...
```

**工作量**: 3-5 天

---

### 4. CI/CD 集成 (高优先级)

**当前问题**: 无自动化测试和部署

**目标**: GitHub Actions 自动运行测试、构建、部署

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install -r requirements.txt
      - run: pytest --cov
      - run: coverage report
      
  deploy:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - run: docker build -t test-observability .
      - run: docker push ...
```

**工作量**: 1-2 天

---

### 5. 更多适配器 (中优先级)

**当前问题**: 仅支持 Vitest/gdUnit4/Python

**目标**: 支持更多测试框架

```typescript
// Playwright 适配器
export class PlaywrightReporter implements Reporter {
  onTestBegin(test: TestCase) {
    emit({
      type: 'test.start',
      source: { framework: 'playwright', project: this.project },
      data: { test_name: test.title }
    });
  }
}

// Airtest 适配器 (Python)
class AirtestAdapter:
    def on_assertion(self, name, passed, screenshot):
        self.emit('assert.pass' if passed else 'assert.fail', {
            'assertion_name': name,
            'screenshot_base64': encode(screenshot)
        })
```

**工作量**: 每个适配器 1-2 天

---

### 6. Dashboard 重构 (中优先级)

**当前问题**: Timeline 页面是简单 HTML，功能有限

**目标**: React/Vue 重构，支持交互式分析

```
dashboard/
├── src/
│   ├── components/
│   │   ├── Timeline.tsx        # 交互式时间线
│   │   ├── EventDetail.tsx     # 事件详情面板
│   │   ├── AnalysisReport.tsx  # 分析报告
│   │   ├── ProjectOverview.tsx # 项目概览
│   │   └── Alerts.tsx          # 告警管理
│   ├── hooks/
│   │   ├── useEvents.ts        # 事件数据
│   │   └── useWebSocket.ts     # 实时连接
│   └── pages/
│       ├── Dashboard.tsx       # 主面板
│       ├── Sessions.tsx        # 会话列表
│       └── Analysis.tsx        # 分析详情
```

**工作量**: 5-7 天

---

### 7. 告警系统 (中优先级)

**当前问题**: 无主动告警

**目标**: 检测异常时自动通知

```python
class AlertManager:
    def __init__(self):
        self.rules = []
        self.channels = []
    
    def add_rule(self, rule: AlertRule):
        self.rules.append(rule)
    
    async def check(self, events: List[TestEvent]):
        for rule in self.rules:
            if rule.matches(events):
                await self.notify(rule, events)

# 告警规则
rules = [
    PassRateDropRule(threshold=0.8),
    SlowTestRule(threshold=10000),
    CrashDetectionRule(),
    RegressionRule(),
]

# 通知渠道
channels = [
    SlackChannel(webhook_url="..."),
    FeishuChannel(webhook_url="..."),
    EmailChannel(smtp_config="..."),
]
```

**工作量**: 3-4 天

---

### 8. 报告生成 (中优先级)

**当前问题**: 无结构化报告输出

**目标**: 生成 PDF/HTML 测试报告

```python
class ReportGenerator:
    def generate(self, session_id: str, format: str = "html") -> Path:
        events = storage.get_session_events(session_id)
        analyses = storage.get_session_analyses(session_id)
        
        if format == "html":
            return self._generate_html(events, analyses)
        elif format == "pdf":
            return self._generate_pdf(events, analyses)
        elif format == "json":
            return self._generate_json(events, analyses)
```

**工作量**: 2-3 天

---

### 9. 用户认证 (低优先级)

**当前问题**: API 无认证

**目标**: API Key 或 OAuth 认证

```python
# API Key 认证
@app.middleware("http")
async def auth_middleware(request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    
    api_key = request.headers.get("X-API-Key")
    if not api_key or not validate_key(api_key):
        return JSONResponse(status_code=401, content={"error": "Invalid API key"})
    
    return await call_next(request)
```

**工作量**: 2-3 天

---

### 10. API 文档 (低优先级)

**当前问题**: 无完整 API 文档

**目标**: OpenAPI/Swagger 自动生成

```python
# FastAPI 已支持自动文档
# 需要完善 Pydantic 模型的描述
class TestEvent(BaseModel):
    """测试事件"""
    event_id: str = Field(description="事件唯一标识")
    session_id: str = Field(description="测试会话 ID")
    timestamp: int = Field(description="Unix 毫秒时间戳")
    # ...
```

**工作量**: 1 天

---

### 11. 插件系统 (低优先级)

**当前问题**: 分析器硬编码

**目标**: 支持自定义分析器插件

```python
# 插件接口
class AnalyzerPlugin(ABC):
    @abstractmethod
    def name(self) -> str: ...
    
    @abstractmethod
    def analyze(self, events: List[TestEvent]) -> AnalysisResult: ...

# 插件加载
plugin_dir = Path("./plugins")
for plugin_file in plugin_dir.glob("*.py"):
    module = import_module(plugin_file.stem)
    if hasattr(module, "Plugin"):
        analyzer_registry.register(module.Plugin())
```

**工作量**: 2-3 天

---

### 12. 多租户 (低优先级)

**当前问题**: 所有项目共享同一命名空间

**目标**: 项目级别的数据隔离

```python
# 项目隔离
class TenantStorage:
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.db = f"tenant_{tenant_id}.db"
    
    async def store_event(self, event: TestEvent):
        # 自动添加 tenant_id
        event.tenant_id = self.tenant_id
        # ...
```

**工作量**: 3-5 天

---

## 四、技术债务

### 需要修复

| 问题 | 优先级 | 工作量 |
|------|--------|--------|
| gateway/main.py 覆盖率 63% | 中 | 1 天 |
| TestEvent/TestSession 类名冲突 pytest 警告 | 低 | 0.5 天 |
| 缺少类型提示 (部分函数) | 低 | 1 天 |
| 无 API 错误处理规范 | 中 | 1 天 |

### 需要重构

| 模块 | 问题 | 建议 |
|------|------|------|
| gateway/main.py | 函数过长 | 拆分为 routes/ 目录 |
| analyzers/ | 缺少配置 | 支持 YAML 配置 |
| cli.py | 参数解析 | 使用 click 替代 argparse |

---

## 五、开发路线图

### Phase 1: 核心完善 (1-2 周)

```
Week 1:
├── LLM 集成 (SemanticEvaluator)
├── 实时事件流 (WebSocket)
└── CI/CD 集成 (GitHub Actions)

Week 2:
├── 存储层抽象 (支持 ClickHouse)
├── API 错误处理规范化
└── 测试覆盖率提升 (85%+)
```

### Phase 2: 功能扩展 (2-3 周)

```
Week 3-4:
├── Playwright 适配器
├── 告警系统
└── 报告生成

Week 5:
├── Dashboard 重构 (React)
└── 用户认证
```

### Phase 3: 生态完善 (持续)

```
├── 插件系统
├── 多租户支持
├── API 文档完善
└── 社区适配器
```

---

## 六、建议的下一步

### 立即可做 (今天)

1. **修复 pytest 警告** — 重命名 TestEvent/TestSession 类
2. **完善 README** — 添加使用示例和架构图
3. **添加 GitHub Actions** — 基础 CI

### 本周可做

1. **LLM 集成** — SemanticEvaluator 接入 Claude API
2. **WebSocket 支持** — 实时事件推送
3. **存储层抽象** — 准备 ClickHouse 支持

### 本月可做

1. **Dashboard 重构** — React 前端
2. **告警系统** — Slack/飞书通知
3. **更多适配器** — Playwright/Airtest

---

## 七、总结

**当前平台已完成 MVP**，核心功能可用：

- ✅ 统一事件模型
- ✅ 基础 API 网关
- ✅ 3 个适配器
- ✅ 4 个分析器
- ✅ CLI 工具
- ✅ 56 个测试

**下一步发展方向**：

1. **LLM 集成** — 让分析器真正智能化
2. **实时能力** — WebSocket 实时推送
3. **生产就绪** — ClickHouse + CI/CD + 认证
4. **前端升级** — React Dashboard
5. **生态扩展** — 更多适配器 + 插件系统

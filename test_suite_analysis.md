# 测试套件分析报告：loopexpedition & stardrifter → 当前项目通用化

## 一、三个项目测试架构概览

| 维度 | 当前项目 (testground) | loopepedition | stardrifter |
|------|----------------------|---------------|-------------|
| **核心框架** | pytest + pytest-asyncio | GdUnit4 (GDScript) + pytest (Python) | pytest + pytest-xdist + hypothesis |
| **测试语言** | Python | GDScript/C# + Python | Python |
| **测试规模** | ~17 个测试文件 | ~200+ GDScript 测试 + ~30 Python 测试 | ~400+ 测试文件 |
| **并行执行** | ❌ 无 | ✅ GDUNIT_PARALLEL | ✅ pytest-xdist (-n auto) |
| **属性测试** | ❌ 无 | ❌ 无 | ✅ hypothesis |
| **截图对比** | ✅ VisualAsserter | ✅ ScreenshotTestHelper | ✅ ScreenshotComparer + VisualAssertions |
| **E2E 驱动** | ✅ GodotDriver + EventBridge | ✅ E2EClient (TCP JSON) | ✅ UI driver + Godot script runner |
| **录制回放** | ✅ GameRecorder/Replayer | ✅ battle_replay_runner | ✅ scene/turn replay |
| **AI 测试** | ✅ test_godot_agents | ✅ ai_testing/ + bot/ | ✅ ai_agent/ + combat trainer |
| **分层标记** | asyncio / llm / slow | 目录分层 (unit/integration/bot/e2e) | fast / medium / slow / godot |

---

## 二、可集成的通用测试能力（按优先级）

### 🔴 P0 — 高度通用，建议立即集成

#### 1. 统一测试分层标记体系

**来源**：stardrifter (pytest markers) + loopepedition (目录分层)

**现状问题**：当前项目只有 `asyncio` / `llm` / `slow` 三个 marker，不够精细。

**可集成内容**：
```python
# pyproject.toml 建议新增 markers
markers = [
    "fast: <1s per test (unit, no I/O)",
    "medium: <5s per test (integration, mock I/O)",
    "slow: >5s (e2e, Godot runtime)",
    "godot: requires Godot binary",
    "llm: requires LLM API",
    "visual: requires screenshot comparison",
    "replay: requires recording/replay infrastructure",
]
```

**Makefile 对应目标**（参考 loopepedition）：
```makefile
test-fast:    ## 本地快速反馈 (<5s)
	pytest -m fast -x

test-medium:  ## 集成测试
	pytest -m "medium and not slow" -x

test-slow:    ## E2E + Godot 运行时
	pytest -m slow --tb=short

test-godot:   ## 需要 Godot 的测试
	pytest -m godot --tb=short
```

---

#### 2. 截图/视觉回归测试框架

**来源**：stardrifter `tests/visual/framework/`（最完整）+ loopepedition `ScreenshotTestHelper`

**stardrifter 提供的能力**：
- `ScreenshotComparer` — 像素级/容差级/结构相似性/感知哈希 4 种对比模式
- `VisualRegressionDetector` — 基线 diff 检测
- `VisualAssertions` — 断言 API 封装
- `ImageRecognizer` / `TemplateMatcher` — 模板匹配
- `UITreeValidator` — UI 树元素检测与属性验证
- `UIElementVerifier` — 布局检查

**loopepedition 提供的能力**：
- `ScreenshotTestHelper` — Godot 内 SubViewport 离屏渲染（headless 兼容）
- golden image 自动创建（首次运行保存为基线）

**建议集成方案**：
```
tests/visual/
├── framework/
│   ├── screenshot.py      ← 从 stardrifter 集成（ComparisonMode, ScreenshotComparer）
│   ├── visual_assertions.py ← 从 stardrifter 集成
│   ├── diff_utils.py      ← 新增：像素 diff 计算
│   └── capture_utils.py   ← 新增：Godot/其他驱动截图封装
├── baselines/             ← golden image 基线目录
└── conftest.py            ← xvfb / godot binary fixtures
```

---

#### 3. Godot 运行时测试 Runner

**来源**：stardrifter `tests/integration/godot_runtime/runner.py`

**stardrifter 的核心设计**：
```python
def run_godot_script(
    script_name: str,
    extra_args: Sequence[str] | None = None,
    timeout_seconds: int | float = 120,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """通过 pytest 调用 Godot headless 运行 GDScript 测试脚本"""
```

**当前项目可复用**：当前项目的 `GodotDriver` 是 async TCP 驱动，stardrifter 的 runner 是 subprocess 驱动。两者互补：
- `GodotDriver`: 运行时交互（适合 bot/E2E）
- `run_godot_script`: 一次性测试脚本（适合 CI 批量验证）

**建议集成**：将 stardrifter 的 runner.py 适配为当前项目的 `drivers/godot/script_runner.py`

---

#### 4. 测试分层守卫 (Layer Guard)

**来源**：loopepedition `scripts/test_layer_guard.py`

**核心能力**：检测测试文件是否被错误地放在了错误的分层目录中：
- `bot/` 目录中的测试不应混入 `unit/`
- `e2e/` 目录中的测试不应 import 真实数据库连接
- 通过 AST 扫描检测违规 import 和 fixture 使用

**建议集成**：作为 pre-commit 或 CI 检查，防止测试漂移

---

### 🟡 P1 — 通用性高，建议逐步集成

#### 5. 测试数据工厂 (Test Data Factory)

**来源**：loopepedition `test/helpers/test_fixtures.gd`

**loopepedition 的双层设计**：
```gdscript
# 数据工厂（小粒度）
static func make_enemy(id: String) -> EnemyData
static func make_hero(class_id: String) -> HeroData

# Builder（fluent API）
var builder := TestFixtures.Builder.new(suite)
builder.with_hero("knight").with_party_size(3).build()
```

**当前项目现状**：`_make_event()`, `make_events_batch()` 等分散在各个测试文件中

**建议集成**：创建 `tests/factories/` 目录，统一事件/会话/截图的工厂函数

---

#### 6. 快照对比基础设施

**来源**：loopepedition `GoldenSnapshotComparator` + 当前项目 `deep_diff`

**loopepedition 的设计亮点**：
- `DEFAULT_TOLERANCE` — 按字段定义容差（如 `hero_hp: 5`, `boss_meter_progress: 0.05`）
- `DiffReport` — 结构化 diff 报告（regressions + warnings）
- 里程碑对齐（按 index 对齐快照序列）

**当前项目的 `deep_diff`**：
- 支持嵌套字典 ADDED / REMOVED / MODIFIED
- `ChangeType` 枚举
- 路径追踪（`player.pos.x`）

**建议集成**：将 loopepedition 的容差快照对比概念融入 `test_state_tracker.py`，扩展 `deep_diff` 支持容差比较

---

#### 7. 属性/不变量测试 (Property-based Testing)

**来源**：stardrifter `tests/property/`

**stardrifter 使用 hypothesis**：
```python
from hypothesis import given, strategies as st

@given(st.lists(st.integers(min_value=0), min_size=1))
def test_economy_invariant_non_negative(resources):
    """经济系统不变量：资源永不负数"""
    ...
```

**测试文件**：
- `test_economy_invariants.py`
- `test_risk_invariants.py`
- `test_scene_lifecycle_invariants.py`

**建议集成**：为当前项目的 `schema/events.py` 和 `analyzers/` 添加不变量测试，验证：
- 事件序列的合法性（如 `test.start` 必须在 `test.end` 之前）
- 数值边界（duration_ms >= 0）
- 状态机转换合法性

---

#### 8. AI/Bot 测试框架

**来源**：loopepedition `test/bot/` + stardrifter `tests/unit/test_ai_*.py`

**loopepedition 提供的模式**：
- `BotTestHelper` — 统一的 orchestrator 创建
- `test_bot_assertions.gd` — 断言模式（生命周期、经济、技能等）
- `test_bot_strategy.gd` — 策略验证
- `test_bot_performance_baseline.gd` — 性能基线

**stardrifter 提供的模式**：
- `test_ai_combat_trainer.py` — AI 训练环境
- `test_ai_guardrail_workflow.py` — 护栏工作流
- `test_ai_llm_reviewer.py` — LLM 评审

**建议集成**：当前项目的 `test_godot_agents.py` 可扩展为更完整的 agent 测试矩阵

---

#### 9. 录制回放验证

**来源**：当前项目 `test_recorder.py` + stardrifter `tests/replay/`

**当前项目已有**：
- `GameRecorder` / `GameReplayer`
- `RecordedAction` / `RecordingResult`

**stardrifter 提供的新维度**：
- `test_scene_replay_reconstruction.py` — 场景重建一致性
- `test_turn_replay_consistency.py` — 回合级回放一致性

**建议集成**：为 recorder 添加回放一致性验证（录制 → 回放 → 对比最终状态）

---

### 🟢 P2 — 场景特定，按需集成

#### 10. 性能预算校验

**来源**：loopepedition `scripts/test_performance_budget.sh`

**能力**：检测测试执行时间是否超过预算阈值，生成性能回归报告

**建议**：当测试规模增大后集成

---

#### 11. UI 树验证引擎

**来源**：stardrifter `tests/visual/framework/ui_tree.py`

**能力**：通过 Godot 导出 UI 树 JSON，验证元素存在性、类型、布局

**建议**：如果当前项目需要验证 Godot UI 结构，可集成

---

#### 12. E2E Client (TCP JSON 协议)

**来源**：loopepedition `godot/test/e2e/e2e_client.py`

**能力**：轻量级 TCP 客户端，通过 length-prefixed JSON 与 Godot 自动化服务器通信

**当前项目已有**：`GodotDriver` (async TCP) + `EventBridge` (HTTP)

**建议**：loopepedition 的 E2EClient 更轻量，可作为 GodotDriver 的补充（同步场景）

---

## 三、不建议集成的部分

| 能力 | 原因 |
|------|------|
| GdUnit4 测试本身 | 当前项目是 Python 主导的测试平台，不运行 GDScript 测试 |
| RPG 领域特定测试 | `rpg_battle_core_smoke.gd` 等过于领域特定 |
| Stardrifter 的 combat 特定 runtime 测试 | 150+ 个 combat 相关 Godot runtime 测试，过于领域特定 |
| loopepedition 的 placement MCTS | 战斗核心特定的 MCTS 验证 |

---

## 四、集成路线图建议

### Phase 1: 标记体系 + 分层守卫（1-2 天）
1. 扩展 `pyproject.toml` markers（fast/medium/slow/godot/visual）
2. 从 loopepedition 移植 `test_layer_guard.py`
3. 更新 Makefile 测试目标

### Phase 2: 视觉测试框架（3-5 天）
1. 从 stardrifter 移植 `tests/visual/framework/`
2. 集成 loopepedition 的 golden image 自动创建逻辑
3. 添加 `tests/visual/conftest.py`（xvfb / godot fixtures）

### Phase 3: Godot Script Runner（2-3 天）
1. 从 stardrifter 适配 `run_godot_script()`
2. 统一 GODOT_BIN 发现逻辑
3. 集成到 `drivers/godot/`

### Phase 4: 属性测试 + 不变量（2-3 天）
1. 安装 hypothesis
2. 为 schema/events.py 添加不变量测试
3. 为 analyzers/pipeline/ 添加消融测试扩展

### Phase 5: 数据工厂 + 快照容差（3-5 天）
1. 统一 `tests/factories/` 目录
2. 扩展 `deep_diff` 支持容差比较
3. 将 `GoldenSnapshotComparator` 概念 Python 化

---

## 五、文件级可移植清单

| 源项目 | 源文件 | 目标位置 | 工作量 | 优先级 |
|--------|--------|----------|--------|--------|
| stardrifter | `tests/visual/framework/screenshot.py` | `tests/visual/framework/` | 中 | P0 |
| stardrifter | `tests/visual/framework/visual_assertions.py` | `tests/visual/framework/` | 中 | P0 |
| stardrifter | `tests/integration/godot_runtime/runner.py` | `drivers/godot/script_runner.py` | 中 | P0 |
| stardrifter | `tests/property/test_*.py` (模式) | `tests/property/` | 低 | P1 |
| stardrifter | `tests/replay/test_*.py` (模式) | `tests/replay/` | 低 | P1 |
| loopepedition | `scripts/test_layer_guard.py` | `tests/guard/` 或 `scripts/` | 低 | P0 |
| loopepedition | `scripts/test_performance_budget.sh` | `scripts/` | 低 | P2 |
| loopepedition | `godot/test/helpers/test_fixtures.gd` (设计模式) | `tests/factories/` | 中 | P1 |
| loopepedition | `godot/test/helpers/golden_snapshot_comparator.gd` (设计模式) | `tests/snapshot/` | 中 | P1 |
| loopepedition | `godot/test/e2e/e2e_client.py` | `drivers/godot/e2e_client.py` | 中 | P2 |

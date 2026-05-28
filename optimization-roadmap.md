# 测试套件深化与优化路线图

> 基于 stardrifter + loopepedition 测试集成后的现状分析

---

## 一、现状快照

| 指标 | 数值 |
|------|------|
| 总测试数 | **582** |
| 有分层标记的测试 | **~20** (仅新增的视觉+属性测试) |
| 无标记的历史测试 | **~560** |
| 视觉框架 smoke 测试 | **14 passed** |
| 属性测试 | **7 passed, 1 skipped** |
| 分层守卫 ERROR | **0** |
| 分层守卫 WARNING | **22** (标记缺失 + 重型 I/O) |
| CI 工作流 | **3 个** (ci, godot-ci, godot-e2e) |
| 测试并行执行 | ❌ 无 (pytest-xdist 未启用) |

**核心问题**：582 个测试中只有 20 个有分层标记 → `make test-fast` / `make test-medium` 几乎为空，分层体系**有名无实**。

---

## 二、优化维度与优先级

### 🔴 P0 — 阻塞分层体系生效

#### 1. 为全部 560+ 个历史测试补充分层标记

**现状**：`make test-fast` 只能跑 ~14 个视觉 smoke 测试，`make test-medium` 只能跑 ~7 个属性测试。大量历史测试（test_drivers.py 66 个、test_alerts.py 34 个、test_platform.py 47 个等）都没有标记。

**影响**：分层标记体系形同虚设，开发者无法按速度过滤测试。

**方案**：批量为现有测试文件添加标记：

```python
# test_alerts.py — 纯逻辑，无 I/O
pytestmark = pytest.mark.fast

# test_drivers.py — mock TCP，无真实网络
pytestmark = pytest.mark.medium

# test_e2e_bridge.py — 真实 HTTP
pytestmark = pytest.mark.slow

# test_screenshots.py — 文件 I/O + PIL
pytestmark = pytest.mark.medium

# test_llm_eval.py — LLM API 调用
pytestmark = [pytest.mark.slow, pytest.mark.llm]
```

**批量策略**：
1. 用 test_layer_guard.py 的扫描结果作为依据（它已识别出哪些文件有重型 I/O）
2. 对每个历史文件添加 `pytestmark = pytest.mark.xxx` 模块级标记
3. 个别测试方法若与文件级别不同，单独覆盖

**预计工作量**：~2-3 小时（17 个历史测试文件）

---

#### 2. CI 工作流同步更新

**现状**：`.github/workflows/ci.yml` 仍使用旧的 `-m "not llm"`，没有 test-guard 检查，没有分层测试矩阵。

**优化方案**：

```yaml
# ci.yml 优化后
jobs:
  test-fast:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.13" }
      - run: pip install -r requirements.txt
      - run: pytest -m fast -v --tb=short  # 仅 <1s 测试

  test-medium:
    runs-on: ubuntu-latest
    needs: test-fast
    steps:
      - run: pytest -m medium -v --tb=short  # 集成测试

  test-guard:
    runs-on: ubuntu-latest
    steps:
      - run: python scripts/test_layer_guard.py --staged
```

**优势**：
- `test-fast` 作为门禁，秒级反馈
- `test-medium` 在 fast 通过后运行
- `test-guard` 防止分层漂移

---

### 🟡 P1 — 框架能力深化

#### 3. 视觉测试与 GodotDriver 集成

**现状**：视觉框架只有 synthetic PIL image 的 smoke 测试，没有真正的游戏截图对比。

**深化路径**：

```
Phase 3A: GodotDriver 截图 + 视觉断言集成
  - 在 test_screenshots.py 中使用 VisualAssertions 替代现有手写对比
  - 通过 GodotDriver.screenshot() 获取真实游戏截图
  - 用 VisualRegressionDetector 对比 baseline

Phase 3B: CI 视觉回归门禁
  - godot-ci.yml 中增加视觉测试步骤
  - 上传 baseline diff 到 artifact
  - 超过容差时标记 PR

Phase 3C: baseline 管理策略
  - 当前：自动创建（首次运行保存为 golden）
  - 优化：baseline 变更需 PR 审批
    - baseline 文件存到 git LFS 或单独仓库
    - CI 中检测到 baseline 变更时标记 "visual-change" label
```

**代码示例**（test_screenshots.py 改造）：

```python
@pytest.mark.visual
class TestScreenshotVisualRegression:
    def test_main_menu_baseline(self, godot_driver, tmp_path):
        driver = godot_driver
        screenshot = driver.screenshot()  # 获取 Godot 截图
        path = tmp_path / "main_menu.png"
        screenshot.save(path)

        va = VisualAssertions()
        va.assert_no_visual_regression(
            "main_menu",
            str(path),
            tolerance=0.05,
        )
```

---

#### 4. 属性测试扩展到核心领域

**现状**：只有事件 schema 的不变量测试（7 个 cases）。

**扩展方向**：

| 领域 | 不变量 | 来源参考 |
|------|--------|----------|
| **事件序列** | test.start 必须在 test.end 之前 | stardrifter `test_turn_replay_consistency` |
| **数值边界** | duration_ms >= 0, score in [0, 100] | stardrifter `test_economy_invariants` |
| **状态机** | PipelineState 转换合法性 | 当前 `test_architecture_validation.py` |
| **数据一致性** | session_id 在整个序列中不变 | loopepedition `ExpeditionSnapshot` |
| **预算约束** | token_usage <= budget_limit | stardrifter `test_risk_invariants` |

**高价值目标**：`analyzers/pipeline/orchestrator.py` 的状态转换

```python
@given(st.lists(st.sampled_from(["INIT", "COLLECT", "ANALYZE", "VALIDATE", "REPORT"])))
def test_pipeline_state_transitions_valid(self, states):
    """Pipeline 状态只能按合法路径转换"""
    valid = {"INIT": ["COLLECT"], "COLLECT": ["ANALYZE"], ...}
    for i in range(1, len(states)):
        assert states[i] in valid.get(states[i-1], [])
```

---

#### 5. Godot Script Runner 实际应用

**现状**：`drivers/godot/script_runner.py` 已就绪，但没有任何测试调用它。

**应用场景**：

```python
# tests/integration/test_godot_script_runner.py
@pytest.mark.godot
class TestGodotScriptRunner:
    def test_runs_gdscript_health_check(self):
        """运行 Godot 健康检查脚本"""
        result = run_godot_script(
            "health_check.gd",
            project_path="/path/to/game",
            timeout_seconds=30,
        )
        assert result.returncode == 0
        assert "HEALTH_OK" in result.stdout

    def test_headless_renders_screenshot(self):
        """验证 headless Godot 能产出截图"""
        result = run_godot_script(
            "capture_title_screen.gd",
            headless=True,
            env={"OUTPUT_DIR": "/tmp/screenshots"},
        )
        screenshot = Path("/tmp/screenshots/title.png")
        assert screenshot.exists()
        assert screenshot.stat().st_size > 1000
```

**前置条件**：需要为被测游戏项目提供 GDScript 测试 runner 脚本。

---

#### 6. 快照对比容差机制（loopepedition 模式）

**现状**：`drivers/godot/state_tracker.py` 的 `deep_diff` 只有 ADDED/REMOVED/MODIFIED，没有容差。

**loopepedition 的设计亮点**：
- `DEFAULT_TOLERANCE = {"hero_hp": 5, "boss_meter_progress": 0.05}`
- 按字段定义容差，而非全局容差

**集成方案**：

```python
# drivers/godot/state_tracker.py 扩展
@dataclass
class DiffConfig:
    """字段级容差配置"""
    tolerances: Dict[str, Union[int, float]] = field(default_factory=dict)
    strict_fields: Set[str] = field(default_factory=set)  # 必须完全匹配

def deep_diff_with_tolerance(
    old: dict, new: dict,
    config: DiffConfig = None,
) -> List[StateChange]:
    ...
```

**应用场景**：游戏状态快照对比（hero_hp 允许 ±5 波动，但 hero_level 必须精确匹配）。

---

### 🟢 P2 — 工程效率优化

#### 7. pytest-xdist 并行执行

**现状**：582 个测试串行运行，覆盖率测试超时 60s。

**优化**：添加 pytest-xdist（stardrifter 已使用 `-n auto`）

```toml
# pyproject.toml
[tool.pytest.ini_options]
addopts = "-n auto --dist=loadgroup"
```

**预期效果**：CI 测试时间从 ~3min → ~1min（4 核并行）

**注意事项**：
- 使用 `:memory:` SQLite 的测试可以安全并行
- 文件级 SQLite 的测试需要 `loadgroup` 或串行标记

---

#### 8. 测试数据工厂统一

**现状**：工厂函数分散在各测试文件中：
- `test_architecture_validation.py`: `_evt()`, `_batch()`
- `test_pipeline.py`: `make_event()`, `make_events_batch()`
- `test_godot_agents.py`: `_make_event()`
- `test_e2e_bridge.py`: 内联 fixture

**loopepedition 模式**：`TestFixtures` 双层设计（make_* 静态方法 + Builder fluent API）

**方案**：

```
tests/factories/
├── __init__.py
├── events.py          # make_event, make_events_batch
├── sessions.py        # make_session, make_obs_session
├── screenshots.py     # make_test_png, make_gradient_png
└── fixtures.py        # pytest fixtures 封装
```

**收益**：
- 消除 4 个测试文件中的重复工厂函数
- 统一的事件构造逻辑（避免字段遗漏）
- 支持渐进式迁移（先统一新测试，再迁移旧测试）

---

#### 9. pre-commit 集成 test-guard

**现状**：`make lint` 已包含 test-guard，但开发者可能忘记运行。

**方案**：在 `.pre-commit-config.yaml` 中添加：

```yaml
repos:
  - repo: local
    hooks:
      - id: test-layer-guard
        name: Test Layer Guard
        entry: python scripts/test_layer_guard.py --staged
        language: system
        pass_filenames: false
        stages: [pre-commit]
```

**效果**：每次 commit 自动检查新增测试文件的分层合规性。

---

#### 10. 覆盖率盲区分析

**现状**：ci.yml 覆盖 gateway/schema/analyzers/adapters，但缺少：
- `drivers/` 模块（除了 test_drivers.py 的部分覆盖）
- `tests/` 自身（meta-coverage）
- `scripts/` 工具脚本

**建议**：

```yaml
# ci.yml
- run: |
    pytest tests/ \
      --cov=gateway --cov=schema --cov=analyzers \
      --cov=drivers --cov=scripts \
      --cov-report=xml --cov-report=term-missing
```

**目标**：识别未测试的代码路径，指导测试补全。

---

#### 11. 安装依赖规范化

**现状**：pytest-subtests 是手动安装的，requirements.txt 没有更新。CI 环境可能缺少它导致 schemathesis 插件冲突。

**修复**：

```txt
# requirements.txt 或 requirements-dev.txt
pytest>=8.0
pytest-asyncio>=0.24.0
pytest-cov>=5.0
pytest-xdist>=3.5
pytest-subtests>=0.15    # 修复 schemathesis 冲突
hypothesis>=6.140        # 属性测试
```

---

## 三、推荐执行顺序

```
Week 1: 标记全覆盖 (P0)
  ├── 为 17 个历史测试文件添加 pytestmark
  ├── 更新 CI 工作流 (ci.yml)
  └── 添加 pre-commit test-guard hook

Week 2: 框架深化 (P1)
  ├── 视觉测试 + GodotDriver 集成
  ├── 属性测试扩展到 pipeline/analyzers
  ├── Godot Script Runner 实际应用
  └── 快照容差机制集成

Week 3: 工程效率 (P2)
  ├── pytest-xdist 并行执行
  ├── 测试数据工厂统一
  ├── 覆盖率盲区分析 + 补测
  └── 依赖规范化 (requirements-dev.txt)
```

---

## 四、量化收益预估

| 优化项 | 当前状态 | 目标状态 | 收益 |
|--------|----------|----------|------|
| 分层标记覆盖 | ~3% (20/582) | 100% | `make test-fast` 秒级反馈 |
| CI 测试时间 | ~3min 串行 | ~1min 并行 | 开发者等待时间 ↓ 66% |
| 视觉测试 | 0 个真实用例 | 5-10 个游戏截图 | 防止 UI 回归 |
| 属性测试 | 7 cases | 30+ cases | 发现边界条件 bug |
| 分层守卫 | 手动运行 | pre-commit 自动 | 分层漂移零发生 |
| 覆盖率 | ~60% (估计) | >80% | 减少生产 bug |

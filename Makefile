.PHONY: help install gateway test test-fast test-medium test-slow test-godot test-visual test-guard lint clean docker-up docker-down \
	bench-pogong bench-loop visual-demo protocol-init protocol-stats \
	godot-bench godot-e2e dashboard dashboard-build dashboard-install

help:  ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## 安装依赖
	pip install -r gateway/requirements.txt
	pip install pytest httpx opencv-python numpy Pillow

gateway:  ## 启动网关 (开发模式)
	cd gateway && uvicorn main:app --reload --host 0.0.0.0 --port 8900

test:  ## 运行平台自身测试（排除 llm 和 slow）
	python -m pytest tests/ -v -m "not llm and not slow" --tb=short --cov=gateway --cov=schema --cov=analyzers --cov=adapters/python --cov-report=term-missing

test-fast:  ## 本地快速反馈 (<1s, unit only)
	python -m pytest tests/ -v -m fast --tb=short -x

test-medium:  ## 集成测试 (<5s, mock I/O)
	python -m pytest tests/ -v -m medium --tb=short -x

test-slow:  ## E2E + Godot 运行时 (>5s)
	python -m pytest tests/ -v -m slow --tb=short

test-godot:  ## 需要 Godot 的测试
	python -m pytest tests/ -v -m godot --tb=short

test-visual:  ## 视觉/截图测试
	python -m pytest tests/ -v -m visual --tb=short

test-guard:  ## 测试分层守卫
	python scripts/test_layer_guard.py

lint:  ## 代码检查 (ruff + test guard)
	ruff check .
	ruff format --check .
	python scripts/test_layer_guard.py

clean:  ## 清理临时文件
	rm -f gateway/test_observability.db
	rm -rf __pycache__ */__pycache__ */*/__pycache__
	rm -rf .pytest_cache */.pytest_cache
	rm -rf test_screenshots

docker-up:  ## Docker 启动
	docker-compose up -d --build

docker-down:  ## Docker 停止
	docker-compose down

dashboard:  ## 启动 Dashboard 前端 (开发模式)
	cd dashboard && npm run dev

dashboard-build:  ## 构建 Dashboard 前端
	cd dashboard && npm run build

dashboard-install:  ## 安装 Dashboard 依赖
	cd dashboard && npm install

timeline:  ## 打开 Timeline 页面（旧版）
	@echo "打开 http://localhost:8901"
	@python -m http.server 8901 --directory timeline 2>/dev/null &

demo: gateway  ## 启动网关并注入演示数据
	@sleep 2 && python tests/inject_demo_data.py

# ─── Godot 游戏评估命令 ───────────────────────────────────

PGC_PATH    ?= $(HOME)/Develop/playground/pogongshichongzou/godot
LOOP_PATH   ?= $(HOME)/Develop/playground/loopexpedition/godot
GODOT_BIN   ?= godot

bench-pogong:  ## 评估破宫之十重奏 (OpenGame-Bench 三维评分)
	@echo "=== 评估破宫之十重奏 ==="
	python -c "\
from drivers.godot.bench import GameBench;\
bench = GameBench('$(PGC_PATH)', 'pogongshichongzou', '$(GODOT_BIN)');\
result = bench.evaluate(run_headless=False);\
import json; print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False));\
print(f'\\n总分: {result.total_score:.1f}/100 ({\"通过\" if result.passed else \"未通过\"})')"

bench-loop:  ## 评估 Loop Expedition (OpenGame-Bench 三维评分)
	@echo "=== 评估 Loop Expedition ==="
	python -c "\
from drivers.godot.bench import GameBench;\
bench = GameBench('$(LOOP_PATH)', 'loopexpedition', '$(GODOT_BIN)');\
result = bench.evaluate(run_headless=False);\
import json; print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False));\
print(f'\\n总分: {result.total_score:.1f}/100 ({\"通过\" if result.passed else \"未通过\"})')"

protocol-init:  ## 初始化调试协议 (两个项目)
	@echo "=== 初始化调试协议 ==="
	python -c "\
from drivers.godot.debug_protocol import create_seed_protocol;\
for name in ['pogongshichongzou', 'loopexpedition']:\
    p = create_seed_protocol(name); path = p.save();\
    print(f'{name}: {p.stats()}');\
    print(f'  → {path}')"

protocol-stats:  ## 查看协议统计
	python -c "\
from drivers.godot.debug_protocol import DebugProtocol;\
for name in ['pogongshichongzou', 'loopexpedition']:\
    p = DebugProtocol.load_or_create(name);\
    print(f'{name}: {p.stats()}')"

visual-demo:  ## 视觉断言演示 (需要截图)
	@echo "=== 视觉断言演示 ==="
	@echo "请先通过 GodotDriver 截图后再运行 visual_assertion"
	python -c "\
from drivers.godot.visual import VisualAsserter;\
print('VisualAsserter 已就绪');\
print('用法:');\
print('  asserter = VisualAsserter()');\
print('  result = asserter.assert_exists(\"screenshot.png\", TemplateMatch(template_path=\"button.png\"))')"

# ─── 部署到游戏项目 ───────────────────────────────────────

deploy-pogong:  ## 部署测试适配器到破宫之十重奏
	@echo "=== 部署到 pogongshichongzou ==="
	@mkdir -p $(PGC_PATH)/addons/test_observability
	cp adapters/godot/observer.gd $(PGC_PATH)/addons/test_observability/observer.gd
	cp adapters/gdunit4/observer.gd $(PGC_PATH)/addons/test_observability/observer_compat.gd
	@echo "✅ 已部署统一 observer (godot/) + 兼容层 (gdunit4/)"

deploy-loop:  ## 部署测试适配器到 Loop Expedition
	@echo "=== 部署到 loopexpedition ==="
	@mkdir -p $(LOOP_PATH)/addons/test_observability
	cp adapters/godot/observer.gd $(LOOP_PATH)/addons/test_observability/observer.gd
	cp adapters/gdunit4/observer.gd $(LOOP_PATH)/addons/test_observability/observer_compat.gd
	@echo "✅ 已部署统一 observer (godot/) + 兼容层 (gdunit4/)"

# ─── CI Godot 测试命令 ─────────────────────────────────────

godot-bench:  ## 运行 GameBench 三维评估 (CI 模式)
	@echo "=== GameBench 三维评估 ==="
	@PROJECT_PATH="${PROJECT_PATH:-.}"; \
	PROJECT_NAME="${PROJECT_NAME:-}"; \
	python -c "\
import sys, json; \
from drivers.godot.bench import GameBench; \
bench = GameBench( \
    project_path='$${PROJECT_PATH}', \
    project_name='$${PROJECT_NAME}', \
    godot_path='$(GODOT_BIN)', \
); \
result = bench.evaluate(run_headless=False); \
print(f'项目: {result.project_name}'); \
print(f'总分: {result.total_score:.1f}/100'); \
print(f'状态: {\"通过\" if result.passed else \"未通过\"}'); \
[sys.exit(1) if not result.passed else None]"

godot-e2e:  ## 运行 EventBridge E2E 测试 (CI 模式)
	@echo "=== EventBridge E2E 测试 ==="
	@python scripts/ci_e2e_smoke.py \
		--gateway-url "$${GATEWAY_URL:-http://localhost:8900}" \
		--project "$${PROJECT_NAME:-e2e_test}"

deploy: deploy-pogong deploy-loop  ## 部署到所有游戏项目

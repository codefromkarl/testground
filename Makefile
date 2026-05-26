.PHONY: help install gateway test lint clean docker-up docker-down \
        bench-pogong bench-loop visual-demo protocol-init protocol-stats

help:  ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## 安装依赖
	pip install -r gateway/requirements.txt
	pip install pytest httpx opencv-python numpy Pillow

gateway:  ## 启动网关 (开发模式)
	cd gateway && uvicorn main:app --reload --host 0.0.0.0 --port 8900

test:  ## 运行平台自身测试
	python -m pytest tests/ -v -m "not llm" --tb=short --cov=gateway --cov=schema --cov=analyzers --cov=adapters/python --cov-report=term-missing

lint:  ## 代码检查 (ruff)
	ruff check .
	ruff format --check .

clean:  ## 清理临时文件
	rm -f gateway/test_observability.db
	rm -rf __pycache__ */__pycache__ */*/__pycache__
	rm -rf .pytest_cache */.pytest_cache
	rm -rf test_screenshots

docker-up:  ## Docker 启动
	docker-compose up -d --build

docker-down:  ## Docker 停止
	docker-compose down

timeline:  ## 打开 Timeline 页面
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

deploy: deploy-pogong deploy-loop  ## 部署到所有游戏项目

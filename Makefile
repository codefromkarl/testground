.PHONY: help install gateway test lint clean docker-up docker-down

help:  ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## 安装依赖
	pip install -r gateway/requirements.txt
	pip install pytest httpx

gateway:  ## 启动网关 (开发模式)
	cd gateway && uvicorn main:app --reload --host 0.0.0.0 --port 8900

test:  ## 运行平台自身测试
	python -m pytest tests/ -v --tb=short

lint:  ## 代码检查
	python -m py_compile schema/events.py
	python -m py_compile gateway/storage.py
	python -m py_compile gateway/main.py
	python -m py_compile analyzers/__init__.py
	python -m py_compile adapters/python/emitter.py
	@echo "✅ 语法检查通过"

clean:  ## 清理临时文件
	rm -f gateway/test_observability.db
	rm -rf __pycache__ */__pycache__ */*/__pycache__
	rm -rf .pytest_cache */.pytest_cache

docker-up:  ## Docker 启动
	docker-compose up -d --build

docker-down:  ## Docker 停止
	docker-compose down

timeline:  ## 打开 Timeline 页面
	@echo "打开 http://localhost:8901"
	@python -m http.server 8901 --directory timeline 2>/dev/null &

demo: gateway  ## 启动网关并注入演示数据
	@sleep 2 && python tests/inject_demo_data.py

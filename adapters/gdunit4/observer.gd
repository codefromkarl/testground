## gdUnit4 可观测性适配器 — 兼容层
##
## 本文件是 adapters/godot/observer.gd 的薄兼容层。
## 保留 RefCounted 基类以兼容无场景树的 gdUnit4 测试环境，
## 内部自动创建 Node 实例并委托给统一适配器。
##
## 旧代码无需修改即可继续使用：
##   var observer = load("res://addons/test-observability/observer.gd").new("http://localhost:8900")
##   observer.on_test_start("test_battle", "res://test/unit/test_battle.gd")
##   observer.on_test_end("test_battle", true, 120)
##
## 新功能（emit_save, emit_screenshot, on_visual_assert 等）同样可用。
## 如果测试环境有场景树，适配器会自动接入并启用批量发送和心跳。
##
extends RefCounted

## godot/observer.gd 的 GDScript 引用，延迟加载。
var _ObserverScript: GDScript = null
## godot/observer.gd 的 Node 实例，作为实际事件处理器。
var _impl: Node = null
## Gateway 服务地址。
var _gateway_url: String
## 项目标识。
var _project: String


## 创建兼容层实例。自动读取项目配置并延迟初始化内部 Node。
## [param gateway_url]  gateway 服务地址，默认 http://localhost:8900
## [param project]      项目标识，默认从 ProjectSettings 读取
func _init(gateway_url: String = "http://localhost:8900", project: String = "") -> void:
	_gateway_url = gateway_url.rstrip("/")
	_project = project if project != "" else ProjectSettings.get_setting("application/config/name", "godot-game")
	# 延迟创建实现节点（需要 SceneTree）
	call_deferred("_setup_impl")


## 尝试按优先级加载 godot/observer.gd 脚本。
## 优先部署路径，回退到开发路径。
func _resolve_observer_script() -> void:
	var paths := [
		"res://addons/test_observability/observer.gd",
		"res://adapters/godot/observer.gd",
	]
	for p in paths:
		if ResourceLoader.exists(p):
			_ObserverScript = load(p)
			break


## 延迟初始化：创建 godot/observer.gd 实例并尝试加入场景树。
func _setup_impl() -> void:
	_resolve_observer_script()
	if not _ObserverScript:
		return
	_impl = _ObserverScript.new(_gateway_url, _project)
	# 尝试将 Node 实例加入场景树以启用 Timer/HTTPRequest
	var tree := Engine.get_main_loop()
	if tree is SceneTree and tree.current_scene:
		tree.current_scene.add_child(_impl)


# ─── 会话管理 ──────────────────────────────────────────────


## 创建远端会话记录。
## [param metadata] 会话元数据，如 {"env": "ci", "branch": "main"}
func create_session(metadata: Dictionary = {}) -> void:
	if _impl:
		_impl.create_session(metadata)


## 结束远端会话，上报汇总数据。
## [param total_tests]  总测试数
## [param passed]       通过数
## [param failed]       失败数
## [param duration_ms]  总耗时（毫秒）
## [param gate_result]  质量门禁判决详情（可选）
func end_session(total_tests: int, passed: int, failed: int, duration_ms: int, gate_result: Dictionary = {}) -> void:
	if _impl:
		_impl.end_session(total_tests, passed, failed, duration_ms, gate_result)


# ─── 测试生命周期 ──────────────────────────────────────────


## 标记测试开始。
## [param test_name]   测试名称
## [param file_path]   测试文件路径（可选）
## [param suite_name]  测试套件名（可选）
func on_test_start(test_name: String, file_path: String = "", suite_name: String = "") -> void:
	if _impl:
		_impl.on_test_start(test_name, file_path, suite_name)


## 标记测试结束。根据 passed 自动选择 test.end 或 test.fail。
## [param test_name]   测试名称
## [param passed]      是否通过
## [param duration_ms] 耗时（毫秒）
## [param errors]      失败时的错误信息数组
## [param file_path]   测试文件路径（可选）
func on_test_end(test_name: String, passed: bool, duration_ms: int, errors: Array = [], file_path: String = "") -> void:
	if _impl:
		_impl.on_test_end(test_name, passed, duration_ms, errors, file_path)


## 标记测试跳过。
## [param test_name]  测试名称
## [param reason]     跳过原因
## [param file_path]  测试文件路径（可选）
func on_test_skip(test_name: String, reason: String = "", file_path: String = "") -> void:
	if _impl:
		_impl.on_test_skip(test_name, reason, file_path)


# ─── 断言 ──────────────────────────────────────────────────


## 通用断言事件。兼容两种签名：
##   模式1 — on_assertion("eq", true, expected, actual, "msg")
##   模式2 — on_assertion("eq", true, details={"k": "v"})
## [param assertion_name] 断言名称
## [param passed]          是否通过
## [param expected]        期望值（模式1）
## [param actual]          实际值（模式1）
## [param message]         断言消息（模式1）
## [param details]         详情字典（模式2，与 expected/actual 互斥）
func on_assertion(assertion_name: String, passed: bool,
				  expected: Variant = null, actual: Variant = null,
				  message: String = "", details: Dictionary = {}) -> void:
	if _impl:
		_impl.on_assertion(assertion_name, passed, expected, actual, message, details)


## Airtest 风格视觉模板匹配断言。
## [param template_name] 模板图片名称
## [param matched]       是否匹配成功
## [param confidence]    匹配置信度 (0.0~1.0)
## [param position]      匹配位置 [x, y]（可选）
func on_visual_assert(template_name: String, matched: bool,
					  confidence: float = 0.0, position: Array = []) -> void:
	if _impl:
		_impl.on_visual_assert(template_name, matched, confidence, position)


# ─── OpenGame 调试协议 ────────────────────────────────────


## 调试规则匹配事件。
## [param entry_id]    规则条目 ID
## [param error_code]  匹配到的错误码
func on_debug_match(entry_id: String, error_code: String) -> void:
	if _impl:
		_impl.on_debug_match(entry_id, error_code)


## 调试修复应用事件。
## [param entry_id]         规则条目 ID
## [param fix_description]  修复方案描述
## [param applied]          修复是否成功应用
func on_debug_repair(entry_id: String, fix_description: String, applied: bool) -> void:
	if _impl:
		_impl.on_debug_repair(entry_id, fix_description, applied)


## 调试规则进化事件。
## [param new_rules]  新增规则数量
func on_debug_evolve(new_rules: int) -> void:
	if _impl:
		_impl.on_debug_evolve(new_rules)


# ─── OpenGame-Bench 评估 ──────────────────────────────────


## Benchmark 维度评估结果。
## [param dimension]  评估维度名
## [param score]      评分 (0.0~1.0)
## [param passed]     是否通过阈值
func on_bench_result(dimension: String, score: float, passed: bool) -> void:
	if _impl:
		_impl.on_bench_result(dimension, score, passed)


# ─── 游戏状态事件 ──────────────────────────────────────────


## 场景切换事件（带前一场景信息）。
## [param scene_path]       新场景路径
## [param previous_scene]   切换前的场景路径（可选）
func on_scene_change(scene_path: String, previous_scene: String = "") -> void:
	if _impl:
		_impl.on_scene_change(scene_path, previous_scene)


## 通用游戏状态变更事件（key-value diff）。
## [param key]         状态键名
## [param old_value]   变更前的值
## [param new_value]   变更后的值
func on_state_change(key: String, old_value: Variant, new_value: Variant) -> void:
	if _impl:
		_impl.on_state_change(key, old_value, new_value)


## 信号发射事件（带节点路径）。
## [param node_path]    发射信号的节点路径
## [param signal_name]  信号名称
## [param args]         信号参数数组（可选）
func on_signal_emitted(node_path: String, signal_name: String, args: Array = []) -> void:
	if _impl:
		_impl.on_signal_emitted(node_path, signal_name, args)


## 发送完整游戏状态快照。
## [param scene_path]       当前场景路径
## [param state]            当前状态字典
## [param previous_state]   上次状态快照（可选，用于对比）
func emit_game_state(scene_path: String, state: Dictionary, previous_state: Dictionary = {}) -> void:
	if _impl:
		_impl.emit_game_state(scene_path, state, previous_state)


## 场景加载事件（简化版，无前一场景信息）。
## [param scene_path]  新场景路径
func emit_scene_load(scene_path: String) -> void:
	if _impl:
		_impl.emit_scene_load(scene_path)


## 信号事件（兼容旧版 source_node 参数名）。
## [param signal_name]   信号名称
## [param source_node]   发射信号的节点标识
## [param args]          信号参数数组（可选）
func emit_signal_event(signal_name: String, source_node: String, args: Array = []) -> void:
	if _impl:
		_impl.emit_signal_event(signal_name, source_node, args)


## 存档事件。
## [param slot]  存档槽位名，默认 "default"
func emit_save(slot: String = "default") -> void:
	if _impl:
		_impl.emit_save(slot)


## 读档事件。
## [param slot]  存档槽位名，默认 "default"
func emit_load(slot: String = "default") -> void:
	if _impl:
		_impl.emit_load(slot)


# ─── 截图 ──────────────────────────────────────────────────


## 截图事件。将 Image 编码为 base64 PNG 上报。
## [param image]    Godot Image 对象
## [param context]  可选描述文字
func emit_screenshot(image: Image, context: String = "") -> void:
	if _impl:
		_impl.emit_screenshot(image, context)


# ─── 门禁结果 ──────────────────────────────────────────────


## 质量门禁判决事件。
## [param verdict]  判决结果: "pass" / "fail" / "warn"
## [param rules]    各规则判定详情字典
func emit_gate_result(verdict: String, rules: Dictionary) -> void:
	if _impl:
		_impl.emit_gate_result(verdict, rules)


# ─── Bug 候选 ──────────────────────────────────────────────


## Bug 候选上报。
## [param severity]      严重程度: "critical" / "high" / "medium" / "low"
## [param category]      Bug 分类
## [param description]   Bug 描述
## [param evidence]      证据字典（可选）
func emit_bug_candidate(severity: String, category: String, description: String, evidence: Dictionary = {}) -> void:
	if _impl:
		_impl.emit_bug_candidate(severity, category, description, evidence)

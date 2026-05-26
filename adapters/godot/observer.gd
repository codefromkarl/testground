## Testground 统一观测桥接适配器
##
## 将 gdUnit4 / Godot 游戏测试事件桥接到统一观测平台 (testground gateway)。
## 兼容 pogongshichongzou 和 loopexpedition 两个项目。
##
## 功能概览:
##   - gdUnit4 测试生命周期事件 (start/end/skip)
##   - 断言事件 (通用 + Airtest 视觉模板匹配)
##   - OpenGame 调试协议 (match/repair/evolve)
##   - OpenGame-Bench 评估事件
##   - 游戏状态事件 (场景/状态/信号/存档/截图)
##   - 批量发送 + 定时 flush + 心跳探测
##   - 会话管理 + 质量门禁 + Bug 候选上报
##
## 用法 (在 gdUnit4 测试脚本或游戏场景中):
##   const Observer = preload("res://addons/test_observability/observer.gd")
##   var _obs = Observer.new("http://localhost:8900", "pogongshichongzou")
##   add_child(_obs)  # 必须加入场景树才能使用 Timer 和 HTTPRequest
##
##   func test_battle():
##     _obs.on_test_start("test_battle")
##     # ... 执行测试 ...
##     _obs.on_test_end("test_battle", true, 120)
##
## 会话管理:
##   _obs.create_session({"env": "ci"})
##   _obs.end_session(10, 8, 2, 5000)
##
## 游戏事件:
##   _obs.emit_save("slot_1")
##   _obs.emit_load("slot_1")
##   _obs.emit_screenshot(get_viewport().get_texture().get_image(), "battle_result")
##
## 报告事件:
##   _obs.emit_gate_result("pass", {"rules": {}})
##   _obs.emit_bug_candidate("high", "crash", "Game crashes on save", {})
##
## 集成 Airtest 视觉断言:
##   _obs.on_visual_assert("button_template", matched, confidence)
##
## 集成 OpenGame Debug Protocol:
##   _obs.on_debug_match(entry_id, error_code)
##   _obs.on_debug_repair(entry_id, fix_description)
##
extends Node

## Gateway 默认地址。
const DEFAULT_GATEWAY := "http://localhost:8900"
## 批量发送阈值：队列满此数量时自动 flush。
const EVENT_BATCH_SIZE := 10
## 定时 flush 间隔（毫秒），由 Timer 驱动。
const FLUSH_INTERVAL_MS := 2000

## Gateway URL（不含末尾 /）。
var _gateway_url: String = ""
## 项目标识，用于 source.project 字段。
var _project: String = ""
## 当前会话 ID，由 _generate_session_id() 生成。
var _session_id: String = ""
## 测试框架标识，固定为 "gdunit4"。
var _framework: String = "gdunit4"
## 待发送事件队列，达到 EVENT_BATCH_SIZE 时自动批量发送。
var _event_queue: Array[Dictionary] = []
## 定时器，每 FLUSH_INTERVAL_MS 毫秒触发一次 _flush_events()。
var _flush_timer: Timer = null
## HTTP 请求节点，复用同一个以减少连接开销。
var _http: HTTPRequest = null
## Gateway 连通状态，由心跳探测更新。
var _connected: bool = false


## 创建 Observer 实例。自动从项目配置读取项目名并生成会话 ID。
## [param gateway_url]  gateway 服务地址，默认 http://localhost:8900
## [param project]       项目标识，默认从 ProjectSettings 读取应用名
func _init(gateway_url: String = DEFAULT_GATEWAY, project: String = "unknown") -> void:
	_gateway_url = gateway_url.rstrip("/")
	_project = project if project != "unknown" else ProjectSettings.get_setting("application/config/name", "unknown")
	_session_id = _generate_session_id()


## 节点就绪时创建 HTTPRequest 和 Timer 子节点，并发送首次心跳探测。
func _ready() -> void:
	_http = HTTPRequest.new()
	_http.timeout = 5.0
	add_child(_http)
	_http.request_completed.connect(_on_request_completed)

	_flush_timer = Timer.new()
	_flush_timer.wait_time = FLUSH_INTERVAL_MS / 1000.0
	_flush_timer.one_shot = false
	_flush_timer.timeout.connect(_flush_events)
	add_child(_flush_timer)
	_flush_timer.start()

	# 测试连接
	_send_heartbeat()


## 节点退出场景树时停止定时器、flush 剩余事件、释放 HTTP 资源。
func _exit_tree() -> void:
	if _flush_timer:
		_flush_timer.stop()
	_flush_events()
	if _http:
		_http.queue_free()


# ─── 会话管理 ──────────────────────────────────────────────


## 创建远端会话记录。可选传入 metadata 字典附加会话元信息。
## [param metadata] 会话元数据，如 {"env": "ci", "branch": "main"}
func create_session(metadata: Dictionary = {}) -> void:
	var body := JSON.stringify({
		"session_id": _session_id,
		"project": _project,
		"framework": _framework,
		"metadata": metadata,
	})
	_send_single_request("/sessions", body)


## 结束远端会话，上报汇总数据。
## [param total_tests]  总测试数
## [param passed]       通过数
## [param failed]       失败数
## [param duration_ms]  总耗时（毫秒）
## [param gate_result]  可选，质量门禁判决详情字典
func end_session(total_tests: int, passed: int, failed: int, duration_ms: int, gate_result: Dictionary = {}) -> void:
	var data := {
		"ended_at": Time.get_unix_time_from_system() * 1000,
		"total_tests": total_tests,
		"passed_tests": passed,
		"failed_tests": failed,
		"duration_ms": duration_ms,
	}
	if not gate_result.is_empty():
		data["gate_result"] = gate_result
	_send_single_request("/sessions/%s" % _session_id, JSON.stringify(data), HTTPClient.METHOD_PUT)


# ─── gdUnit4 测试生命周期 ──────────────────────────────────


## 标记测试开始。
## [param test_name]   测试名称
## [param file_path]   测试文件路径（可选）
## [param suite_name]  测试套件名（可选）
func on_test_start(test_name: String, file_path: String = "", suite_name: String = "") -> void:
	var data := {
		"test_name": test_name,
		"file": file_path,
	}
	if suite_name != "":
		data["suite"] = suite_name
	_emit_event("test.start", data)


## 标记测试结束。根据 passed 自动选择 test.end 或 test.fail 事件类型。
## [param test_name]   测试名称
## [param passed]      是否通过
## [param duration_ms] 耗时（毫秒）
## [param errors]      失败时的错误信息数组
## [param file_path]   测试文件路径（可选）
func on_test_end(test_name: String, passed: bool, duration_ms: int, errors: Array = [], file_path: String = "") -> void:
	var event_type := "test.end" if passed else "test.fail"
	var data := {
		"test_name": test_name,
		"passed": passed,
		"duration_ms": duration_ms,
		"errors": errors,
	}
	if file_path != "":
		data["file"] = file_path
	_emit_event(event_type, data)


## 标记测试跳过。
## [param test_name]  测试名称
## [param reason]     跳过原因
## [param file_path]  测试文件路径（可选）
func on_test_skip(test_name: String, reason: String = "", file_path: String = "") -> void:
	var data := {
		"test_name": test_name,
		"reason": reason,
	}
	if file_path != "":
		data["file"] = file_path
	_emit_event("test.skip", data)


# ─── 断言事件 ──────────────────────────────────────────────


## 通用断言事件。支持两种调用模式：
##   模式1 — expected/actual: on_assertion("eq", true, 42, 42, "should match")
##   模式2 — details dict:    on_assertion("eq", true, details={"key": "value"})
## [param assertion_name] 断言名称
## [param passed]          是否通过
## [param expected]        期望值（模式1）
## [param actual]          实际值（模式1）
## [param message]         断言消息（模式1）
## [param details]         详情字典（模式2，与 expected/actual 互斥）
func on_assertion(assertion_name: String, passed: bool,
				  expected: Variant = null, actual: Variant = null,
				  message: String = "", details: Dictionary = {}) -> void:
	var data := {
		"assertion_name": assertion_name,
		"passed": passed,
	}
	if not details.is_empty():
		data["details"] = details
	else:
		data["expected"] = str(expected)
		data["actual"] = str(actual)
		data["message"] = message
	_emit_event("assert.pass" if passed else "assert.fail", data)


# ─── Airtest 风格视觉断言 ─────────────────────────────────


## Airtest 风格视觉模板匹配断言。上报模板名、匹配结果、置信度和位置。
## [param template_name] 模板图片名称
## [param matched]       是否匹配成功
## [param confidence]    匹配置信度 (0.0~1.0)
## [param position]      匹配位置 [x, y]（可选）
func on_visual_assert(template_name: String, matched: bool,
					  confidence: float = 0.0, position: Array = []) -> void:
	_emit_event("assert.pass" if matched else "assert.fail", {
		"assertion_type": "visual_template",
		"template_name": template_name,
		"matched": matched,
		"confidence": confidence,
		"position": position,
	})


# ─── OpenGame 风格调试协议事件 ──────────────────────────────


## OpenGame 调试协议 — 规则匹配事件。当调试规则命中时调用。
## [param entry_id]    规则条目 ID
## [param error_code]  匹配到的错误码
func on_debug_match(entry_id: String, error_code: String) -> void:
	_emit_event("debug.match", {
		"entry_id": entry_id,
		"error_code": error_code,
	})


## OpenGame 调试协议 — 修复应用事件。当调试修复被尝试时调用。
## [param entry_id]         规则条目 ID
## [param fix_description]  修复方案描述
## [param applied]          修复是否成功应用
func on_debug_repair(entry_id: String, fix_description: String, applied: bool) -> void:
	_emit_event("debug.repair", {
		"entry_id": entry_id,
		"fix_description": fix_description,
		"applied": applied,
	})


## OpenGame 调试协议 — 规则进化事件。当新规则被自动发现时调用。
## [param new_rules]  新增规则数量
func on_debug_evolve(new_rules: int) -> void:
	_emit_event("debug.evolve", {
		"new_rules": new_rules,
	})


# ─── OpenGame-Bench 风格评估事件 ───────────────────────────


## OpenGame-Bench 维度评估结果。每个维度独立评分和通过判定。
## [param dimension]  评估维度名，如 "combat", "exploration", "ui"
## [param score]      评分 (0.0~1.0)
## [param passed]     是否通过阈值
func on_bench_result(dimension: String, score: float, passed: bool) -> void:
	_emit_event("bench.%s" % dimension, {
		"dimension": dimension,
		"score": score,
		"passed": passed,
	})


# ─── 游戏状态事件 ──────────────────────────────────────────


## 场景切换事件，包含前一场景信息。适用于需要追踪场景切换链的场景。
## [param scene_path]       新场景路径
## [param previous_scene]   切换前的场景路径（可选）
func on_scene_change(scene_path: String, previous_scene: String = "") -> void:
	_emit_event("game.scene_load", {
		"scene_path": scene_path,
		"previous_scene": previous_scene,
	})


## 通用游戏状态变更事件。记录 key 对应的值从 old_value 变为 new_value。
## [param key]         状态键名
## [param old_value]   变更前的值
## [param new_value]   变更后的值
func on_state_change(key: String, old_value: Variant, new_value: Variant) -> void:
	_emit_event("game.state_change", {
		"key": key,
		"old_value": str(old_value),
		"new_value": str(new_value),
	})


## 信号发射事件（带节点路径）。用于追踪场景树中特定节点发出的信号。
## [param node_path]    发射信号的节点路径
## [param signal_name]  信号名称
## [param args]         信号参数数组（可选）
func on_signal_emitted(node_path: String, signal_name: String, args: Array = []) -> void:
	_emit_event("game.signal_emit", {
		"node_path": node_path,
		"signal_name": signal_name,
		"args": args,
	})


## 发送完整游戏状态快照。适用于需要记录某一时刻完整状态的场景。
## [param scene_path]       当前场景路径
## [param state]            当前状态字典
## [param previous_state]   上次状态快照，用于对比差异（可选）
func emit_game_state(scene_path: String, state: Dictionary, previous_state: Dictionary = {}) -> void:
	var data := {
		"scene_path": scene_path,
		"state": state,
	}
	if not previous_state.is_empty():
		data["previous_state"] = previous_state
	_emit_event("game.state_change", data)


## 场景加载事件（简化版）。仅记录新场景路径，无前一场景信息。
## [param scene_path]  新场景路径
func emit_scene_load(scene_path: String) -> void:
	_emit_event("game.scene_load", {"scene_path": scene_path})


## 信号事件（带 source_node 参数名）。兼容旧版 API。
## [param signal_name]   信号名称
## [param source_node]   发射信号的节点标识
## [param args]          信号参数数组（可选）
func emit_signal_event(signal_name: String, source_node: String, args: Array = []) -> void:
	_emit_event("game.signal_emit", {
		"signal_name": signal_name,
		"source_node": source_node,
		"args": args,
	})


# ─── 存档 / 读档事件 ──────────────────────────────────────


## 存档事件。上报玩家存档操作到 gateway。
## [param slot]  存档槽位名，默认 "default"
func emit_save(slot: String = "default") -> void:
	_emit_event("game.save", {"slot": slot})


## 读档事件。上报玩家读档操作到 gateway。
## [param slot]  存档槽位名，默认 "default"
func emit_load(slot: String = "default") -> void:
	_emit_event("game.load", {"slot": slot})


# ─── 截图事件 ──────────────────────────────────────────────


## 截图事件。将 Godot Image 对象编码为 base64 PNG 并上报到 gateway。
## [param image]    Godot Image 对象（如 get_viewport().get_texture().get_image()）
## [param context]  可选描述文字，如 "battle_result", "bug_evidence"
func emit_screenshot(image: Image, context: String = "") -> void:
	var b64 := Marshalls.raw_to_base64(image.save_png_to_buffer())
	_emit_event("action.screenshot", {
		"context": context,
		"image_base64": b64,
		"width": image.get_width(),
		"height": image.get_height(),
	})


# ─── 门禁结果 ──────────────────────────────────────────────


## 质量门禁判决事件。上报质量门禁的最终判定结果。
## [param verdict]  判决结果: "pass" / "fail" / "warn"
## [param rules]    各规则判定详情字典，如 {"coverage": "pass", "lint": "fail"}
func emit_gate_result(verdict: String, rules: Dictionary) -> void:
	_emit_event("report.gate_result", {
		"verdict": verdict,
		"rules": rules,
	})


# ─── Bug 候选 ──────────────────────────────────────────────


## Bug 候选上报。记录疑似 Bug 的信息和证据，供后续分析。
## [param severity]      严重程度: "critical" / "high" / "medium" / "low"
## [param category]      Bug 分类，如 "crash", "logic", "visual", "performance"
## [param description]   Bug 描述文字
## [param evidence]      证据字典，如 {"screenshot": "base64...", "log": "..."}
func emit_bug_candidate(severity: String, category: String, description: String, evidence: Dictionary = {}) -> void:
	_emit_event("report.bug_candidate", {
		"severity": severity,
		"category": category,
		"description": description,
		"evidence": evidence,
	})


# ─── 内部实现 ──────────────────────────────────────────────


## 将事件加入批量队列。当队列达到 EVENT_BATCH_SIZE 时自动触发 _flush_events()。
## 每个事件自动附带 event_id、session_id、timestamp 和 source 元数据。
## [param event_type]  事件类型，如 "test.start", "game.save", "assert.fail"
## [param data]        事件数据字典
func _emit_event(event_type: String, data: Dictionary) -> void:
	var event := {
		"event_id": _generate_id(),
		"session_id": _session_id,
		"timestamp": Time.get_ticks_msec(),
		"source": {
			"framework": _framework,
			"project": _project,
		},
		"type": event_type,
		"data": data,
	}
	_event_queue.append(event)

	if _event_queue.size() >= EVENT_BATCH_SIZE:
		_flush_events()


## 批量发送队列中的事件到 gateway /events/batch 端点。
## 发送失败时事件放回队列等待重试。
func _flush_events() -> void:
	if _event_queue.is_empty() or not _http:
		return

	var batch := _event_queue.duplicate()
	_event_queue.clear()

	var json_body := JSON.stringify({"events": batch})
	var headers := ["Content-Type: application/json"]

	var url := "%s/events/batch" % _gateway_url
	var err := _http.request(url, headers, HTTPClient.METHOD_POST, json_body)
	if err != OK:
		# 发送失败，放回队列
		_event_queue = batch + _event_queue


## 发送单条 HTTP 请求到 gateway（用于会话管理等非批量端点）。
## [param path]    API 路径，如 "/sessions"
## [param body]    JSON 请求体
## [param method]  HTTP 方法，默认 POST
func _send_single_request(path: String, body: String, method: HTTPClient.Method = HTTPClient.METHOD_POST) -> void:
	if not _http:
		return
	var url := _gateway_url + path
	var headers := ["Content-Type: application/json"]
	_http.request(url, headers, method, body)


## 发送 GET /health 心跳请求，探测 gateway 连通性。
func _send_heartbeat() -> void:
	if not _http:
		return
	var url := "%s/health" % _gateway_url
	_http.request(url, [], HTTPClient.METHOD_GET)


## HTTP 请求完成回调。根据响应码更新 _connected 连通状态。
## [param result]    请求结果码
## [param code]      HTTP 响应状态码
## [param _headers]  响应头（未使用）
## [param body]      响应体
func _on_request_completed(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	if code == 200:
		_connected = true
	else:
		_connected = false


## 生成会话 ID，格式 "session-{ticks}-{random}" 保证唯一性。
func _generate_session_id() -> String:
	return "session-%d-%d" % [Time.get_ticks_msec(), randi() % 10000]


## 生成事件 ID（类 UUID v4 格式），保证每个事件的唯一标识。
func _generate_id() -> String:
	var rng := RandomNumberGenerator.new()
	rng.randomize()
	return "%08x-%04x-%04x-%04x-%08x%04x" % [
		rng.randi(),
		rng.randi() & 0xFFFF,
		(rng.randi() & 0x0FFF) | 0x4000,
		(rng.randi() & 0x3FFF) | 0x8000,
		rng.randi(),
		rng.randi() & 0xFFFF,
	]

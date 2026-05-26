## Godot 游戏自动化测试 — 观测桥接适配器
##
## 将 gdUnit4 测试事件桥接到统一观测平台 (testground gateway)。
## 兼容 pogongshichongzou 和 loopexpedition 两个项目。
##
## 用法 (在 gdUnit4 测试脚本中):
##   const Observer = preload("res://addons/test_observability/observer.gd")
##   var _obs = Observer.new("http://localhost:8900", "pogongshichongzou")
##
##   func test_battle():
##     _obs.on_test_start("test_battle")
##     # ... 执行测试 ...
##     _obs.on_test_end("test_battle", true, 120)
##
## 集成 Airtest 视觉断言:
##   _obs.on_visual_assert("button_template", matched, confidence)
##
## 集成 OpenGame Debug Protocol:
##   _obs.on_debug_match(entry_id, error_code)
##   _obs.on_debug_repair(entry_id, fix_description)
##
extends Node

const DEFAULT_GATEWAY := "http://localhost:8900"
const EVENT_BATCH_SIZE := 10
const FLUSH_INTERVAL_MS := 2000

var _gateway_url: String = ""
var _project: String = ""
var _session_id: String = ""
var _framework: String = "gdunit4"
var _event_queue: Array[Dictionary] = []
var _flush_timer: Timer = null
var _http: HTTPRequest = null
var _connected: bool = false


func _init(gateway_url: String = DEFAULT_GATEWAY, project: String = "unknown") -> void:
	_gateway_url = gateway_url
	_project = project
	_session_id = _generate_session_id()


func _ready() -> void:
	_http = HTTPRequest.new()
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


func _exit_tree() -> void:
	if _flush_timer:
		_flush_timer.stop()
	_flush_events()
	if _http:
		_http.queue_free()


# ─── gdUnit4 测试生命周期 ──────────────────────────────────


func on_test_start(test_name: String, file_path: String = "") -> void:
	_emit_event("test.start", {
		"test_name": test_name,
		"file": file_path,
	})


func on_test_end(test_name: String, passed: bool, duration_ms: int, errors: Array = []) -> void:
	var event_type := "test.end" if passed else "test.fail"
	_emit_event(event_type, {
		"test_name": test_name,
		"passed": passed,
		"duration_ms": duration_ms,
		"errors": errors,
	})


func on_test_skip(test_name: String, reason: String = "") -> void:
	_emit_event("test.skip", {
		"test_name": test_name,
		"reason": reason,
	})


# ─── 断言事件 ──────────────────────────────────────────────


func on_assertion(assertion_name: String, passed: bool,
				  expected: Variant = null, actual: Variant = null,
				  message: String = "") -> void:
	_emit_event("assert.pass" if passed else "assert.fail", {
		"assertion_name": assertion_name,
		"passed": passed,
		"expected": str(expected),
		"actual": str(actual),
		"message": message,
	})


# ─── Airtest 风格视觉断言 ─────────────────────────────────


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


func on_debug_match(entry_id: String, error_code: String) -> void:
	_emit_event("debug.match", {
		"entry_id": entry_id,
		"error_code": error_code,
	})


func on_debug_repair(entry_id: String, fix_description: String, applied: bool) -> void:
	_emit_event("debug.repair", {
		"entry_id": entry_id,
		"fix_description": fix_description,
		"applied": applied,
	})


func on_debug_evolve(new_rules: int) -> void:
	_emit_event("debug.evolve", {
		"new_rules": new_rules,
	})


# ─── OpenGame-Bench 风格评估事件 ───────────────────────────


func on_bench_result(dimension: String, score: float, passed: bool) -> void:
	_emit_event("bench.%s" % dimension, {
		"dimension": dimension,
		"score": score,
		"passed": passed,
	})


# ─── 游戏状态事件 ──────────────────────────────────────────


func on_scene_change(scene_path: String, previous_scene: String = "") -> void:
	_emit_event("game.scene_load", {
		"scene_path": scene_path,
		"previous_scene": previous_scene,
	})


func on_state_change(key: String, old_value: Variant, new_value: Variant) -> void:
	_emit_event("game.state_change", {
		"key": key,
		"old_value": str(old_value),
		"new_value": str(new_value),
	})


func on_signal_emitted(node_path: String, signal_name: String, args: Array = []) -> void:
	_emit_event("game.signal_emit", {
		"node_path": node_path,
		"signal_name": signal_name,
		"args": args,
	})


# ─── 内部实现 ──────────────────────────────────────────────


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


func _send_heartbeat() -> void:
	if not _http:
		return
	var url := "%s/health" % _gateway_url
	_http.request(url, [], HTTPClient.METHOD_GET)


func _on_request_completed(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	if code == 200:
		_connected = true
	else:
		_connected = false


func _generate_session_id() -> String:
	return "session-%d-%d" % [Time.get_ticks_msec(), randi() % 10000]


func _generate_id() -> String:
	# 简单 UUID v4 替代
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

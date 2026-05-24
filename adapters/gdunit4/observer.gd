# addons/test-observability/observer.gd
# gdUnit4 可观测性适配器 — 连接 Godot 测试项目
#
# 用法：在测试脚本中：
#   var observer = load("res://addons/test-observability/observer.gd").new("http://localhost:8900")
#   observer.on_test_start("test_battle", "res://test/unit/test_battle.gd")
#   # ... 执行测试 ...
#   observer.on_test_end("test_battle", true, 120)
#
# 或注册为 Autoload 自动截获信号。

extends RefCounted

var _gateway_url: String
var _project: String
var _session_id: String
var _http: HTTPRequest
var _start_time: int

const Framework := "gdunit4"


func _init(gateway_url: String = "http://localhost:8900", project: String = "") -> void:
	_gateway_url = gateway_url.rstrip("/")
	_project = project if project != "" else ProjectSettings.get_setting("application/config/name", "godot-game")
	_session_id = "session_%d_%s" % [Time.get_unix_time_from_system() * 1000, _rand_id()]
	_start_time = Time.get_unix_time_from_system() * 1000
	# 延迟创建 HTTPRequest（需要 SceneTree）
	call_deferred("_setup_http")


func _setup_http() -> void:
	_http = HTTPRequest.new()
	_http.timeout = 5.0
	# 尝试加入场景树
	var tree = Engine.get_main_loop()
	if tree is SceneTree:
		var root = tree.current_scene
		if root:
			root.add_child(_http)


func _rand_id() -> String:
	var chars := "abcdefghijklmnopqrstuvwxyz0123456789"
	var result := ""
	for i in 8:
		result += chars[randi() % chars.length()]
	return result


func _generate_event_id() -> String:
	return "evt_%d_%s" % [Time.get_unix_time_from_system() * 1000, _rand_id()]


# ─── 会话管理 ──────────────────────────────────────────────


func create_session(metadata: Dictionary = {}) -> void:
	var body := JSON.stringify({
		"session_id": _session_id,
		"project": _project,
		"framework": Framework,
		"metadata": metadata,
	})
	_send_request("/sessions", body)


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
	_send_request("/sessions/%s" % _session_id, JSON.stringify(data), HTTPClient.METHOD_PUT)


# ─── 测试生命周期 ──────────────────────────────────────────


func on_test_start(test_name: String, file_path: String = "", suite_name: String = "") -> void:
	var event := _make_event("test.start", {
		"test_name": test_name,
		"file": file_path,
		"suite": suite_name,
	}, file_path, test_name)
	_send_event(event)


func on_test_end(test_name: String, passed: bool, duration_ms: int, errors: Array = [], file_path: String = "") -> void:
	var event := _make_event("test.end" if passed else "test.fail", {
		"test_name": test_name,
		"passed": passed,
		"duration_ms": duration_ms,
		"errors": errors,
	}, file_path, test_name)
	_send_event(event)


func on_test_skip(test_name: String, reason: String = "", file_path: String = "") -> void:
	var event := _make_event("test.skip", {
		"test_name": test_name,
		"reason": reason,
	}, file_path, test_name)
	_send_event(event)


# ─── 断言 ──────────────────────────────────────────────────


func on_assertion(assertion_name: String, passed: bool, details: Dictionary = {}) -> void:
	var event := _make_event("assert.pass" if passed else "assert.fail", {
		"assertion_name": assertion_name,
		"passed": passed,
		"details": details,
	})
	_send_event(event)


# ─── 游戏事件 ──────────────────────────────────────────────


func emit_game_state(scene_path: String, state: Dictionary, previous_state: Dictionary = {}) -> void:
	var data := {
		"scene_path": scene_path,
		"state": state,
	}
	if not previous_state.is_empty():
		data["previous_state"] = previous_state
	_send_event(_make_event("game.state_change", data))


func emit_scene_load(scene_path: String) -> void:
	_send_event(_make_event("game.scene_load", {"scene_path": scene_path}))


func emit_signal_event(signal_name: String, source_node: String, args: Array = []) -> void:
	_send_event(_make_event("game.signal_emit", {
		"signal_name": signal_name,
		"source_node": source_node,
		"args": args,
	}))


func emit_save(slot: String = "default") -> void:
	_send_event(_make_event("game.save", {"slot": slot}))


func emit_load(slot: String = "default") -> void:
	_send_event(_make_event("game.load", {"slot": slot}))


# ─── 截图 ──────────────────────────────────────────────────


func emit_screenshot(image: Image, context: String = "") -> void:
	var b64 := Marshalls.raw_to_base64(image.save_png_to_buffer())
	_send_event(_make_event("action.screenshot", {
		"context": context,
		"image_base64": b64,
		"width": image.get_width(),
		"height": image.get_height(),
	}))


# ─── 门禁结果 ──────────────────────────────────────────────


func emit_gate_result(verdict: String, rules: Dictionary) -> void:
	_send_event(_make_event("report.gate_result", {
		"verdict": verdict,
		"rules": rules,
	}))


# ─── Bug 候选 ──────────────────────────────────────────────


func emit_bug_candidate(severity: String, category: String, description: String, evidence: Dictionary = {}) -> void:
	_send_event(_make_event("report.bug_candidate", {
		"severity": severity,
		"category": category,
		"description": description,
		"evidence": evidence,
	}))


# ─── 内部辅助 ──────────────────────────────────────────────


func _make_event(type: String, data: Dictionary, file_path: String = "", test_name: String = "") -> Dictionary:
	var source := {
		"framework": Framework,
		"project": _project,
	}
	if file_path != "":
		source["file"] = file_path
	if test_name != "":
		source["test_name"] = test_name

	return {
		"event_id": _generate_event_id(),
		"session_id": _session_id,
		"timestamp": Time.get_unix_time_from_system() * 1000,
		"source": source,
		"type": type,
		"data": data,
	}


func _send_event(event: Dictionary) -> void:
	_send_request("/events", JSON.stringify(event))


func _send_request(path: String, body: String, method: HTTPClient.Method = HTTPClient.METHOD_POST) -> void:
	if _http == null:
		return
	var url := _gateway_url + path
	var headers := ["Content-Type: application/json"]
	_http.request(url, headers, method, body)

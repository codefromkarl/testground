"""LLM 语义评估测试

测试 SemanticEvaluator 的 LLM 评估路径和规则引擎 fallback。
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from schema.events import EventSource, TestEvent, create_agent_tool_call
from analyzers.llm_client import LLMClient, LLMConfig, LLMError, is_llm_available
from analyzers.semantic_eval import SemanticEvaluator, EVAL_PROMPT_TEMPLATE


# ─── LLMClient 测试 ─────────────────────────────────────────


class TestLLMConfig:
    """测试 LLM 配置"""

    def test_default_config(self):
        config = LLMConfig()
        assert config.api_base == "http://localhost:6011/v1"
        assert config.model == "gpt"
        assert config.temperature == 0.1

    def test_custom_config(self):
        config = LLMConfig(
            api_base="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4",
            temperature=0.5,
        )
        assert config.api_base == "https://api.openai.com/v1"
        assert config.api_key == "sk-test"
        assert config.model == "gpt-4"

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("LLM_API_BASE", "https://custom.api.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-env-key")
        monkeypatch.setenv("LLM_MODEL", "gpt-4-turbo")

        config = LLMConfig()
        assert config.api_base == "https://custom.api.com/v1"
        assert config.api_key == "sk-env-key"
        assert config.model == "gpt-4-turbo"


class TestLLMClient:
    """测试 LLM 客户端"""

    def _make_client(self) -> LLMClient:
        return LLMClient(
            config=LLMConfig(api_base="http://test.api/v1", api_key="sk-test")
        )

    def test_chat_success(self):
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello!"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client.client, "post", return_value=mock_response) as mock_post:
            result = client.chat("Hi")
            assert result == "Hello!"
            mock_post.assert_called_once()
            # 验证请求参数
            call_args = mock_post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert payload["model"] == "gpt"
            assert payload["messages"][-1]["content"] == "Hi"

    def test_chat_with_system(self):
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "OK"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client.client, "post", return_value=mock_response):
            client.chat("test", system="You are helpful")
            # 验证 messages 包含 system
            call_args = client.client.post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert payload["messages"][0]["role"] == "system"
            assert payload["messages"][0]["content"] == "You are helpful"

    def test_chat_json_success(self):
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"score": 0.8, "reason": "good"}'}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client.client, "post", return_value=mock_response):
            result = client.chat_json("evaluate this")
            assert result == {"score": 0.8, "reason": "good"}

    def test_chat_json_from_code_block(self):
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '```json\n{"score": 0.9, "reason": "excellent"}\n```'}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client.client, "post", return_value=mock_response):
            result = client.chat_json("evaluate this")
            assert result == {"score": 0.9, "reason": "excellent"}

    def test_chat_http_error_raises_llm_error(self):
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = Exception("HTTP Error")

        import httpx
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=mock_response
        )

        with patch.object(client.client, "post", return_value=mock_response):
            with pytest.raises(LLMError, match="HTTP 500"):
                client.chat("test")

    def test_chat_json_parse_error(self):
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "not json at all"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client.client, "post", return_value=mock_response):
            with pytest.raises(LLMError, match="JSON 解析失败"):
                client.chat_json("test")

    def test_context_manager(self):
        client = self._make_client()
        with client:
            pass
        assert client._client is None or client._client.is_closed


class TestIsLLmAvailable:
    """测试 LLM 可用性检测"""

    def test_with_api_key(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        assert is_llm_available() is True

    def test_with_localhost(self, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setenv("LLM_API_BASE", "http://localhost:6011/v1")
        assert is_llm_available() is True

    def test_with_127(self, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setenv("LLM_API_BASE", "http://127.0.0.1:8080/v1")
        assert is_llm_available() is True

    def test_no_config(self, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("LLM_API_BASE", raising=False)
        assert is_llm_available() is False


# ─── SemanticEvaluator LLM 测试 ─────────────────────────────


class TestSemanticEvaluatorLLM:
    """测试 SemanticEvaluator 的 LLM 评估路径"""

    def _make_events(self, tool_name, input_data, output_data, success=True):
        source = EventSource(framework="vitest", project="test")
        return [
            create_agent_tool_call("sess", source, tool_name, input_data).to_dict(),
            TestEvent(
                event_id="evt-result",
                session_id="sess",
                timestamp=1000,
                source=source,
                type="agent.tool_result",
                data={
                    "tool_name": tool_name,
                    "input": input_data,
                    "output": output_data,
                    "success": success,
                },
            ).to_dict(),
        ]

    def test_llm_evaluation_low_quality(self):
        """LLM 评估低质量输出"""
        mock_client = MagicMock(spec=LLMClient)
        mock_client.chat_json.return_value = {
            "score": 0.3,
            "reason": "输出不完整，缺少关键信息"
        }

        evaluator = SemanticEvaluator(llm_client=mock_client)
        assert evaluator.uses_llm is True

        events = self._make_events("search", "query", "incomplete result")
        result = evaluator.analyze(events)

        assert len(result.findings) == 1
        finding = result.findings[0]
        assert finding["category"] == "low_quality"
        assert finding["score"] == 0.3
        assert finding["eval_method"] == "llm"
        assert "不完整" in finding["reason"]
        assert "LLM" in result.summary

    def test_llm_evaluation_good_quality(self):
        """LLM 评估高质量输出 — 不产生 findings"""
        mock_client = MagicMock(spec=LLMClient)
        mock_client.chat_json.return_value = {
            "score": 0.9,
            "reason": "输出完整且准确"
        }

        evaluator = SemanticEvaluator(llm_client=mock_client)
        events = self._make_events("search", "query", "complete answer")
        result = evaluator.analyze(events)

        assert len(result.findings) == 0

    def test_llm_fallback_to_rule_engine(self):
        """LLM 调用失败时降级到规则引擎"""
        mock_client = MagicMock(spec=LLMClient)
        mock_client.chat_json.side_effect = LLMError("API unavailable")

        evaluator = SemanticEvaluator(
            llm_client=mock_client,
            eval_fn=lambda inp, out: 0.2,  # 自定义评估函数作为 fallback
        )

        events = self._make_events("search", "query", "some output")
        result = evaluator.analyze(events)

        # 应该降级到自定义评估函数
        assert len(result.findings) == 1
        assert result.findings[0]["eval_method"] == "custom_fn"

    def test_no_llm_uses_rule_engine(self):
        """无 LLM 时使用规则引擎"""
        evaluator = SemanticEvaluator(use_llm=False)
        assert evaluator.uses_llm is False

        events = self._make_events("search", "query", "bad", success=True)
        evaluator._eval_fn = lambda inp, out: len(out) / 100
        result = evaluator.analyze(events)

        assert len(result.findings) == 1
        assert result.findings[0]["eval_method"] == "custom_fn"
        assert "规则引擎" in result.summary

    def test_tool_failure_always_detected(self):
        """工具失败始终被检测（不依赖 LLM）"""
        mock_client = MagicMock(spec=LLMClient)
        evaluator = SemanticEvaluator(llm_client=mock_client)

        events = self._make_events("search", "query", "", success=False)
        result = evaluator.analyze(events)

        assert len(result.findings) == 1
        assert result.findings[0]["category"] == "tool_failure"
        assert result.findings[0]["eval_method"] == "rule"
        # LLM 不应被调用
        mock_client.chat_json.assert_not_called()

    def test_empty_output_always_detected(self):
        """空输出始终被检测（不依赖 LLM）"""
        mock_client = MagicMock(spec=LLMClient)
        evaluator = SemanticEvaluator(llm_client=mock_client)

        events = self._make_events("search", "query", "")
        result = evaluator.analyze(events)

        assert len(result.findings) == 1
        assert result.findings[0]["category"] == "empty_output"
        assert result.findings[0]["eval_method"] == "rule"
        mock_client.chat_json.assert_not_called()

    def test_llm_auto_detection(self, monkeypatch):
        """自动检测 LLM 可用性"""
        monkeypatch.setenv("LLM_API_KEY", "sk-test")

        # Mock LLMClient to avoid actual initialization
        with patch("analyzers.semantic_eval.LLMClient") as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value = mock_instance

            evaluator = SemanticEvaluator()
            assert evaluator.uses_llm is True
            MockClient.assert_called_once()

    def test_llm_auto_detection_unavailable(self, monkeypatch):
        """LLM 不可用时自动降级"""
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("LLM_API_BASE", raising=False)

        evaluator = SemanticEvaluator()
        assert evaluator.uses_llm is False

    def test_confidence_higher_with_llm(self):
        """使用 LLM 时 confidence 更高"""
        mock_client = MagicMock(spec=LLMClient)
        mock_client.chat_json.return_value = {"score": 0.9, "reason": "good"}

        evaluator_llm = SemanticEvaluator(llm_client=mock_client)
        evaluator_rule = SemanticEvaluator(use_llm=False)

        events = self._make_events("t", "i", "o")
        result_llm = evaluator_llm.analyze(events)
        result_rule = evaluator_rule.analyze(events)

        assert result_llm.confidence > result_rule.confidence

    def test_prompt_template_format(self):
        """验证 prompt 模板格式正确"""
        prompt = EVAL_PROMPT_TEMPLATE.format(input="test input", output="test output")
        assert "test input" in prompt
        assert "test output" in prompt
        assert "JSON" in prompt
        assert "score" in prompt


class TestSemanticEvaluatorIntegration:
    """集成测试 — 使用 mock HTTP 响应"""

    def test_full_llm_evaluation_flow(self):
        """完整的 LLM 评估流程"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"score": 0.4, "reason": "输出过于简短，缺乏细节"}
                        )
                    }
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        client = LLMClient(config=LLMConfig(api_key="sk-test"))
        evaluator = SemanticEvaluator(llm_client=client)

        source = EventSource(framework="vitest", project="test")
        events = [
            create_agent_tool_call(
                "sess", source, "search_hotel", {"city": "杭州"}
            ).to_dict(),
            TestEvent(
                event_id="evt-result",
                session_id="sess",
                timestamp=1000,
                source=source,
                type="agent.tool_result",
                data={
                    "tool_name": "search_hotel",
                    "input": {"city": "杭州"},
                    "output": "没找到",
                    "success": True,
                },
            ).to_dict(),
        ]

        with patch.object(client.client, "post", return_value=mock_response):
            result = evaluator.analyze(events)

        assert len(result.findings) == 1
        assert result.findings[0]["score"] == 0.4
        assert "简短" in result.findings[0]["reason"]


# ─── assert_trip_plan_structure 测试（保留原有）──────────────


class TestTripPlanStructure:
    """测试结构化断言"""

    def test_complete_plan(self):
        from analyzers.semantic_eval import assert_trip_plan_structure

        output = "目的地：杭州，第一天游览西湖，第二天去灵隐寺，住宿推荐西湖边酒店，预算约2000元"
        results = assert_trip_plan_structure(output)
        passed = [r for r in results if r["passed"]]
        assert len(passed) >= 4

    def test_incomplete_plan(self):
        from analyzers.semantic_eval import assert_trip_plan_structure

        output = "去杭州玩"
        results = assert_trip_plan_structure(output)
        failed = [r for r in results if not r["passed"]]
        assert len(failed) >= 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

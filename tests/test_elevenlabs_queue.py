import unittest
from unittest.mock import Mock, patch

from app.services.elevenlabs_audio import create_agent, update_agent


class ElevenLabsQueueTests(unittest.TestCase):
    def test_create_and_sync_agents_enable_three_minute_queue(self):
        response = Mock()
        response.json.return_value = {"agent_id": "agent-test"}
        with patch("app.services.elevenlabs_audio._request", return_value=response) as request:
            create_agent(conversation_config={"agent": {"language": "ru"}}, api_key="test")
            update_agent(agent_id="agent-test", conversation_config={"agent": {"language": "ru"}}, api_key="test")
        self.assertEqual(request.call_count, 2)
        for call in request.call_args_list:
            platform = call.kwargs["json"]["platform_settings"]
            self.assertEqual(platform, {"queueing_config": {"enabled": True, "wait_timeout_seconds": 180}})


if __name__ == "__main__":
    unittest.main()

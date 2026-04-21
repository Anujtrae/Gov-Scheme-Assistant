import importlib.util
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "app.py"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

spec = importlib.util.spec_from_file_location("kau_app", APP_PATH)
app_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app_module)
app = app_module.app


class AppRoutesTestCase(unittest.TestCase):
    def setUp(self):
        app.testing = True
        self.client = app.test_client()
        self.original_agent_key = app_module.AI_AGENT_API_KEY
        self.original_gemini_key = app_module.GEMINI_API_KEY
        app_module.GEMINI_API_KEY = ""

    def tearDown(self):
        app_module.AI_AGENT_API_KEY = self.original_agent_key
        app_module.GEMINI_API_KEY = self.original_gemini_key

    def test_home_page_loads(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"AI-Powered Government", response.data)

    def test_result_page_with_matching_scheme(self):
        response = self.client.post(
            "/result",
            data={
                "age": "18_25",
                "income": "below_1_lakh",
                "occupation": "student",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Scholarship Scheme", response.data)

    def test_result_page_validation_error(self):
        response = self.client.post(
            "/result",
            data={
                "age": "",
                "income": "",
                "occupation": "",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Input validation failed", response.data)

    def test_api_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.is_json)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")

    def test_assistant_endpoint_returns_reply(self):
        response = self.client.post("/api/assistant", json={"message": "I am a student with low income"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.is_json)
        self.assertIn("reply", response.get_json())

    def test_assistant_endpoint_message_validation(self):
        response = self.client.post("/api/assistant", json={"message": ""})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json().get("error"), "Please provide a non-empty 'message' field.")

    def test_assistant_endpoint_api_key_enforcement(self):
        app_module.AI_AGENT_API_KEY = "test-secret"

        unauthorized = self.client.post("/api/assistant", json={"message": "hello"})
        self.assertEqual(unauthorized.status_code, 401)

        authorized = self.client.post(
            "/api/assistant",
            json={"message": "hello"},
            headers={"X-API-Key": "test-secret"},
        )
        self.assertEqual(authorized.status_code, 200)

    def test_404_web_vs_api_response_format(self):
        web_response = self.client.get("/does-not-exist")
        self.assertEqual(web_response.status_code, 404)
        self.assertIn(b"Error 404", web_response.data)

        api_response = self.client.get("/api/does-not-exist")
        self.assertEqual(api_response.status_code, 404)
        self.assertTrue(api_response.is_json)
        self.assertEqual(api_response.get_json().get("error"), "Resource not found")


if __name__ == "__main__":
    unittest.main()

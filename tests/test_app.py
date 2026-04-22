import importlib.util
import json
import pathlib
import sys
import tempfile
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
        self.original_users_file = app_module.USERS_FILE

        self.temp_dir = tempfile.TemporaryDirectory()
        app_module.USERS_FILE = str(pathlib.Path(self.temp_dir.name) / "users.json")
        with open(app_module.USERS_FILE, "w", encoding="utf-8") as file_handle:
            json.dump({"users": []}, file_handle)

        app_module.AI_AGENT_API_KEY = ""
        app_module.GEMINI_API_KEY = ""

    def tearDown(self):
        app_module.AI_AGENT_API_KEY = self.original_agent_key
        app_module.GEMINI_API_KEY = self.original_gemini_key
        app_module.USERS_FILE = self.original_users_file
        self.temp_dir.cleanup()

    def _load_users(self):
        with open(app_module.USERS_FILE, "r", encoding="utf-8") as file_handle:
            payload = json.load(file_handle)
        return payload.get("users", [])

    def _signup(self, email="test@example.com", password="secret123", name="Test User"):
        return self.client.post(
            "/signup",
            data={
                "name": name,
                "email": email,
                "password": password,
                "confirm_password": password,
            },
            follow_redirects=True,
        )

    def _logout(self):
        return self.client.get("/logout", follow_redirects=True)

    # ─── Auth Tests ───────────────────────────────

    def test_home_page_shows_auth_when_logged_out(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Sign In", response.data)
        self.assertIn(b"Sign Up", response.data)

    def test_signup_creates_user_and_redirects_to_app(self):
        response = self._signup()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Find the best government schemes for you", response.data)

        users = self._load_users()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["email"], "test@example.com")
        self.assertGreaterEqual(len(users[0].get("login_history", [])), 1)

    def test_signin_after_logout(self):
        self._signup()
        self._logout()

        response = self.client.post(
            "/signin",
            data={"email": "test@example.com", "password": "secret123"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Find the best government schemes for you", response.data)

    # ─── Result Page Tests ────────────────────────

    def test_result_requires_login(self):
        response = self.client.post(
            "/result",
            data={
                "age": "18_25",
                "income": "below_1_lakh",
                "occupation": "student",
            },
        )
        self.assertEqual(response.status_code, 302)

    def test_result_page_with_matching_scheme_logs_activity(self):
        self._signup()
        response = self.client.post(
            "/result",
            data={
                "age": "18_25",
                "income": "below_1_lakh",
                "occupation": "student",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Recommended schemes for your profile", response.data)
        self.assertIn(b"Rajarshi Chhatrapati Shahu Maharaj Scholarship", response.data)
        self.assertIn(b"Apply on Official Portal", response.data)

        users = self._load_users()
        actions = [event.get("action") for event in users[0].get("activities", [])]
        self.assertIn("scheme_search", actions)

    def test_result_page_validation_error(self):
        self._signup()
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

    # ─── Assistant Tests ──────────────────────────

    def test_assistant_requires_login(self):
        response = self.client.post("/api/assistant", json={"message": "hello"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json().get("error"), "Please sign in to use the assistant.")

    def test_assistant_endpoint_returns_reply(self):
        self._signup()
        response = self.client.post("/api/assistant", json={"message": "I am a student with low income"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.is_json)
        self.assertIn("reply", response.get_json())

    def test_assistant_complex_query_includes_apply_links(self):
        self._signup()
        response = self.client.post(
            "/api/assistant",
            json={"message": "I am 22 years old student, income below 2 lakh. How to apply?"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.is_json)
        reply = response.get_json().get("reply", "")
        self.assertIn("Application flow", reply)
        self.assertIn("Apply:", reply)

    def test_assistant_endpoint_message_validation(self):
        self._signup()
        response = self.client.post("/api/assistant", json={"message": ""})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json().get("error"), "Please provide a non-empty 'message' field.")

    def test_assistant_endpoint_api_key_enforcement_when_logged_in(self):
        self._signup()
        app_module.AI_AGENT_API_KEY = "test-secret"

        unauthorized = self.client.post("/api/assistant", json={"message": "hello"})
        self.assertEqual(unauthorized.status_code, 401)

        authorized = self.client.post(
            "/api/assistant",
            json={"message": "hello"},
            headers={"X-API-Key": "test-secret"},
        )
        self.assertEqual(authorized.status_code, 200)

    def test_assistant_logs_activity(self):
        self._signup()
        response = self.client.post("/api/assistant", json={"message": "I am a student"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("reply", response.get_json())

        users = self._load_users()
        actions = [event.get("action") for event in users[0].get("activities", [])]
        self.assertIn("assistant_query", actions)

    # ─── History Tests ────────────────────────────

    def test_history_page_loads_for_authenticated_user(self):
        self._signup()
        response = self.client.get("/history")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Account History", response.data)
        self.assertIn(b"Task Activity", response.data)

    # ─── API Tests ────────────────────────────────

    def test_api_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.is_json)
        self.assertEqual(response.get_json()["status"], "ok")

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

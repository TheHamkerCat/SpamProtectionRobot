import json
import unittest

import httpx

from spr.arq import ArqClient, ArqError, NsfwResult, SpamModel, SpamResult

# ARQ's /nlp answer for one message, and its /nlp/model answer
NLP_RESULT = {
    "spam": 0.9863, "category": "crypto_trading", "category_probabilities": {"crypto_trading": 0.81},
    "harm": 2, "harm_probabilities": [0.02, 0.1, 0.88], "abusive": 0.0123, "language": "english",
}
MODEL_INFO = {
    "version": "20261109-test", "created": "2026-11-09T00:00:00+00:00",
    "thresholds": {"strict": {"spam_above": 0.7175}, "normal": {"spam_above": 0.8826}},
}
NSFW_RESULT = {"drawings": 3.2, "hentai": 3.6, "neutral": 93.0, "porn": 0.02, "sexy": 0.03, "is_nsfw": False}


class ArqClientTest(unittest.IsolatedAsyncioTestCase):
    """The client against a fake ARQ (httpx.MockTransport, no network)"""

    async def asyncSetUp(self):
        self.mode = "ok"
        self.version = MODEL_INFO["version"]
        self.requests = []
        transport = httpx.MockTransport(self.handle)
        self.arq = ArqClient("http://arq.test/", "test-key", transport=transport)

    async def asyncTearDown(self):
        await self.arq.close()

    async def handle(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        self.requests.append((request.method, request.url.path, request.headers.get("X-API-KEY"), body))
        if self.mode == "unreachable":
            raise httpx.ConnectError("connection refused", request=request)
        if self.mode == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if self.mode == "unauthorized":
            return httpx.Response(401, json={"detail": "bad key"})
        if self.mode == "rate_limited":
            return httpx.Response(429)
        if self.mode == "server_error":
            return httpx.Response(500)
        if self.mode == "not_ok":
            return httpx.Response(200, json={"ok": False, "result": "Something broke"})
        if self.mode == "not_json":
            return httpx.Response(200, text="<html>gateway</html>")
        if self.mode == "wrong_shape":
            return httpx.Response(200, json={"ok": True, "result": [{"unexpected": 1}]})
        if request.url.path == "/nlp":
            return httpx.Response(200, json={"ok": True, "model_version": self.version, "result": [NLP_RESULT]})
        if request.url.path == "/nlp/model":
            return httpx.Response(200, json={"ok": True, "result": {**MODEL_INFO, "version": self.version}})
        return httpx.Response(200, json={"ok": True, "result": NSFW_RESULT})

    async def test_nlp(self):
        result = await self.arq.nlp("buy now")
        self.assertEqual(result, SpamResult(98.63, "crypto_trading", 2, 1.23, "english", "20261109-test"))
        method, path, key, body = self.requests[0]
        self.assertEqual((method, path, key), ("POST", "/nlp", "test-key"))
        self.assertEqual(json.loads(body), {"messages": ["buy now"]})

    async def test_spam_model_is_read_once_until_the_version_changes(self):
        model = await self.arq.spam_model()
        self.assertEqual(model, SpamModel("20261109-test", 71.75))
        await self.arq.spam_model()
        await self.arq.spam_model("20261109-test")
        self.assertEqual([r[:2] for r in self.requests], [("GET", "/nlp/model")])
        # /nlp reported another version: read again
        self.version = "20261110-new"
        self.assertEqual((await self.arq.spam_model("20261110-new")).version, "20261110-new")
        self.assertEqual(len(self.requests), 2)

    async def test_spam_model_with_a_bad_answer(self):
        self.mode = "wrong_shape"
        with self.assertRaises(ArqError):
            await self.arq.spam_model()

    async def test_nsfw_scan_uploads_file_field(self):
        result = await self.arq.nsfw_scan(b"\xff\xd8image")
        self.assertIsInstance(result, NsfwResult)
        self.assertFalse(result.is_nsfw)
        method, path, key, body = self.requests[0]
        self.assertEqual((method, path, key), ("POST", "/nsfw_scan", "test-key"))
        self.assertIn(b'name="file"; filename="file"', body)
        self.assertIn(b"\xff\xd8image", body)

    async def test_errors_become_arq_errors(self):
        expected = {
            "unreachable": "Can't reach the ARQ server",
            "timeout": "Can't reach the ARQ server",
            "unauthorized": "API key is not valid",
            "rate_limited": "Too many ARQ requests",
            "server_error": "HTTP 500",
            "not_ok": "Something broke",
            "not_json": "unexpected response",
            "wrong_shape": "unexpected response",
        }
        for mode, message in expected.items():
            with self.subTest(mode=mode):
                self.mode = mode
                with self.assertRaises(ArqError) as raised:
                    await self.arq.nlp("x")
                self.assertIn(message, str(raised.exception))

    async def test_url_with_a_path(self):
        arq = ArqClient("http://arq.test/api/", "key", transport=httpx.MockTransport(self.handle))
        with self.assertRaises(ArqError):
            await arq.nlp("x")
        await arq.close()
        self.assertEqual(self.requests[0][1], "/api/nlp")

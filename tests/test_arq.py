import json
import unittest

import httpx

from spr.arq import ArqClient, ArqError, NsfwResult, SpamResult

NLP_RESULT = {"is_spam": True, "spam_probability": 99, "spam": 98.6, "ham": 1.4, "profanity": False, "cache": False}
NSFW_RESULT = {"drawings": 3.2, "hentai": 3.6, "neutral": 93.0, "porn": 0.02, "sexy": 0.03, "is_nsfw": False}


class ArqClientTest(unittest.IsolatedAsyncioTestCase):
    """The client against a fake ARQ (httpx.MockTransport, no network)"""

    async def asyncSetUp(self):
        self.mode = "ok"
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
            return httpx.Response(200, json={"ok": True, "result": [NLP_RESULT]})
        return httpx.Response(200, json={"ok": True, "result": NSFW_RESULT})

    async def test_nlp(self):
        result = await self.arq.nlp("buy now")
        self.assertEqual(result, SpamResult(True, 99, 98.6, 1.4, False))
        method, path, key, body = self.requests[0]
        self.assertEqual((method, path, key), ("POST", "/nlp", "test-key"))
        self.assertEqual(json.loads(body), {"messages": ["buy now"]})

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

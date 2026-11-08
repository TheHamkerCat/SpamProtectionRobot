import asyncio
import unittest

from aiohttp import web

from spr.arq import ArqClient, ArqError, NsfwResult, SpamResult

NLP_RESULT = {"is_spam": True, "spam_probability": 99, "spam": 98.6, "ham": 1.4, "profanity": False, "cache": False}
NSFW_RESULT = {"drawings": 3.2, "hentai": 3.6, "neutral": 93.0, "porn": 0.02, "sexy": 0.03, "is_nsfw": False}


class ArqClientTest(unittest.IsolatedAsyncioTestCase):
    """Runs the client against a local fake ARQ server"""

    async def asyncSetUp(self):
        self.mode = "ok"
        self.requests = []
        app = web.Application()
        app.router.add_post("/nlp", self.handle_nlp)
        app.router.add_post("/nsfw_scan", self.handle_nsfw)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.arq = ArqClient(f"http://127.0.0.1:{port}/", "test-key", timeout=0.5)

    async def asyncTearDown(self):
        await self.arq.close()
        await self.runner.cleanup()

    async def respond(self, result):
        if self.mode == "unauthorized":
            return web.json_response({"detail": "bad key"}, status=401)
        if self.mode == "rate_limited":
            return web.Response(status=429)
        if self.mode == "server_error":
            return web.Response(status=500)
        if self.mode == "not_ok":
            return web.json_response({"ok": False, "result": "Something broke"})
        if self.mode == "not_json":
            return web.Response(text="<html>gateway</html>")
        if self.mode == "slow":
            await asyncio.sleep(2)
        if self.mode == "wrong_shape":
            return web.json_response({"ok": True, "result": [{"unexpected": 1}]})
        return web.json_response({"ok": True, "result": result})

    async def handle_nlp(self, request):
        self.requests.append((request.headers.get("X-API-KEY"), await request.json()))
        return await self.respond([NLP_RESULT])

    async def handle_nsfw(self, request):
        form = await request.post()
        upload = form["file"]
        self.requests.append((request.headers.get("X-API-KEY"), upload.file.read()))
        return await self.respond(NSFW_RESULT)

    async def test_nlp(self):
        result = await self.arq.nlp("buy now")
        self.assertEqual(result, SpamResult(True, 99, 98.6, 1.4, False))
        self.assertEqual(self.requests, [("test-key", {"messages": ["buy now"]})])

    async def test_nsfw_scan_uploads_file_field(self):
        result = await self.arq.nsfw_scan(b"\xff\xd8image")
        self.assertIsInstance(result, NsfwResult)
        self.assertFalse(result.is_nsfw)
        self.assertEqual(self.requests, [("test-key", b"\xff\xd8image")])

    async def test_errors_become_arq_errors(self):
        expected = {
            "unauthorized": "API key is not valid",
            "rate_limited": "Too many ARQ requests",
            "server_error": "HTTP 500",
            "not_ok": "Something broke",
            "not_json": "Can't reach the ARQ server",
            "slow": "Can't reach the ARQ server",
            "wrong_shape": "unexpected response",
        }
        for mode, message in expected.items():
            with self.subTest(mode=mode):
                self.mode = mode
                with self.assertRaises(ArqError) as raised:
                    await self.arq.nlp("x")
                self.assertIn(message, str(raised.exception))

    async def test_unreachable_server(self):
        arq = ArqClient("http://127.0.0.1:9", "key", timeout=0.5)
        with self.assertRaises(ArqError):
            await arq.nlp("x")
        await arq.close()

"""spr.ofd.OfdClient against a fake Federation server (httpx.MockTransport),
never the network."""
import json
import unittest

import httpx
from pyfederation.records import ContentInput

from spr.ofd import OfdClient, OfdError, entity_address, entity_metadata
from tests.fakes import FEDERATION_TIMESTAMP, USER_ID, make_channel, make_user

HOST = "https://federation.example"
TOKEN = "bot-token"


def server_information(**overrides) -> dict:
    return {
        "name": "Test Federation",
        "api_version": "2.2",
        "public_audit_logs": False,
        "public_evidence": False,
        "public_blacklist": True,
        "public_entities": True,
        "public_reports": False,
        "public_entity_metadata": False,
        "public_telephone_entities": False,
        "public_scan_content": True,
        "collects_public_scan_content": False,
        "public_query_entity": True,
        "search_enabled": False,
        "public_search": False,
        "search_types": [],
        "public_search_types": [],
        **overrides,
    }


def operator(client=True, disabled=False) -> dict:
    return {
        "uuid": "o" * 32,
        "name": "spr",
        "disabled": disabled,
        "permissions": {"client": client, "management": False, "operator": False},
        "created": FEDERATION_TIMESTAMP,
        "updated": FEDERATION_TIMESTAMP,
    }


def entity_query(action="PERMANENTLY_BLOCK_ENTITY") -> dict:
    return {
        "entity_record": {
            "uuid": "e" * 32,
            "hash": "b" * 64,
            "host": "telegram.org",
            "id": str(USER_ID),
            "whitelisted": False,
            "reputation": 0,
            "created": FEDERATION_TIMESTAMP,
            "updated": FEDERATION_TIMESTAMP,
        },
        "related_entities": [],
        "active_blacklists": [],
        "suggested_action": action,
    }


class FakeServer:
    """Answers by (method, path); records every request"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get((request.method, request.url.path))
        if answer is None:
            return httpx.Response(404, json={"message": "Not found"})
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return httpx.Response(status, json=body)

    def paths(self, method: str) -> list:
        return [request.url.path for request in self.requests if request.method == method]


class OfdClientTest(unittest.IsolatedAsyncioTestCase):
    def make_client(self, routes, token=TOKEN):
        self.server = FakeServer(routes)
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.server))
        client = OfdClient(HOST, token, http)
        self.addAsyncCleanup(client.close)
        return client

    def routes(self, **overrides):
        routes = {
            ("GET", "/info"): (200, server_information()),
            ("GET", "/operators/self"): (200, operator()),
            ("POST", "/entities"): (200, "e" * 32),
        }
        routes.update(overrides)
        return routes

    async def test_token_with_client_permissions_contributes(self):
        client = self.make_client(self.routes())
        await client.start()
        self.assertTrue(client.can_contribute)
        self.assertEqual(client.info.name, "Test Federation")
        self.assertEqual(
            self.server.requests[-1].headers["Authorization"], f"Bearer {TOKEN}"
        )

    async def test_rejected_token_falls_back_to_anonymous(self):
        routes = self.routes()
        routes[("GET", "/operators/self")] = (401, {"message": "Invalid token"})
        client = self.make_client(routes)
        await client.start()
        self.assertFalse(client.can_contribute)

        await client.publish_sender(make_user())
        self.assertEqual(self.server.paths("POST"), [])
        await client.query_sender(USER_ID)
        self.assertNotIn("Authorization", self.server.requests[-1].headers)

    async def test_token_without_permissions_falls_back_to_anonymous(self):
        routes = self.routes()
        routes[("GET", "/operators/self")] = (200, operator(client=False))
        client = self.make_client(routes)
        await client.start()
        self.assertFalse(client.can_contribute)
        await client.publish_sender(make_user())
        self.assertEqual(self.server.paths("POST"), [])

    async def test_unreachable_server_keeps_the_token(self):
        down = httpx.ConnectError("down")
        client = self.make_client({("GET", "/info"): down, ("GET", "/operators/self"): down})
        await client.start()  # never raises
        self.assertIsNone(client.info)
        self.assertFalse(client.can_contribute)
        # The token is still sent, the server may accept it once it is back
        self.assertTrue(client.can_scan)
        self.assertEqual(client._client.access_token, TOKEN)

    async def test_pending_token_is_checked_again_before_publishing(self):
        routes = self.routes()
        routes[("GET", "/operators/self")] = httpx.ConnectError("down")
        client = self.make_client(routes)
        await client.start()
        routes[("GET", "/operators/self")] = (200, operator())
        client._auth_checked_at -= 10_000

        await client.publish_sender(make_user())
        self.assertTrue(client.can_contribute)
        self.assertEqual(self.server.paths("POST"), ["/entities"])

    async def test_anonymous_client(self):
        client = self.make_client(self.routes(), token=None)
        await client.start()
        self.assertFalse(client.can_contribute)
        self.assertEqual(self.server.paths("GET"), ["/info"])

    async def test_anonymous_scans_follow_the_server(self):
        routes = self.routes()
        routes[("GET", "/info")] = (
            200,
            server_information(
                public_scan_content=False,
                public_query_entity=False,
                collects_public_scan_content=True,
            ),
        )
        client = self.make_client(routes, token=None)
        await client.start()
        self.assertFalse(client.can_scan)
        self.assertFalse(client.can_query)
        self.assertTrue(client.collects_public_scan_content)

    async def test_unknown_sender_is_none_and_lookups_are_cached(self):
        client = self.make_client(self.routes())
        self.assertIsNone(await client.query_sender(USER_ID))
        self.assertIsNone(await client.query_sender(USER_ID))
        self.assertEqual(self.server.paths("GET"), [f"/entities/{USER_ID}@telegram.org/query"])

    async def test_sender_assessment(self):
        routes = self.routes()
        routes[("GET", f"/entities/{USER_ID}@telegram.org/query")] = (200, entity_query())
        client = self.make_client(routes)
        query = await client.query_sender(USER_ID)
        self.assertEqual(query.suggested_action.value, "PERMANENTLY_BLOCK_ENTITY")

    async def test_failed_lookup_raises_and_is_not_cached(self):
        path = f"/entities/{USER_ID}@telegram.org/query"
        routes = self.routes()
        routes[("GET", path)] = (500, {"message": "boom"})
        client = self.make_client(routes)
        with self.assertRaises(OfdError):
            await client.query_sender(USER_ID)
        routes[("GET", path)] = (200, entity_query())
        self.assertIsNotNone(await client.query_sender(USER_ID))

    async def test_scan_sends_the_content_and_author(self):
        routes = self.routes()
        routes[("POST", "/scan")] = (
            200,
            {"resolved_entities": [], "scan_results": {}, "risk_score": 0.0},
        )
        client = self.make_client(routes)
        await client.scan(ContentInput(text_content="hello world"), entity_address(USER_ID))
        body = self.server.requests[-1].content.decode()
        self.assertIn("hello world", body)
        self.assertIn(f"{USER_ID}@telegram.org", body)

    async def test_publish_once_per_sender(self):
        client = self.make_client(self.routes())
        await client.start()
        user = make_user(username="alice")
        await client.publish_sender(user)
        await client.publish_sender(user)
        self.assertEqual(self.server.paths("POST"), ["/entities"])
        body = json.loads(self.server.requests[-1].content)
        self.assertEqual(body["host"], "telegram.org")
        self.assertEqual(body["id"], str(USER_ID))

    async def test_failed_publish_is_swallowed(self):
        routes = self.routes()
        routes[("POST", "/entities")] = (500, {"message": "boom"})
        client = self.make_client(routes)
        await client.start()
        await client.publish_sender(make_user())  # no exception
        self.assertTrue(client.can_contribute)


class MetadataTest(unittest.TestCase):
    def test_user_metadata_has_public_facts_only(self):
        metadata = entity_metadata(make_user(first_name="Alice", username="alice"))
        self.assertEqual(metadata["first_name"], "Alice")
        self.assertEqual(metadata["username"], "alice")
        self.assertNotIn("last_name", metadata)
        self.assertNotIn("phone", metadata)

    def test_channel_metadata(self):
        metadata = entity_metadata(make_channel(-1001111111111, title="News", username="news"))
        self.assertEqual(metadata["title"], "News")
        self.assertEqual(metadata["username"], "news")

    def test_address_uses_marked_ids(self):
        self.assertEqual(entity_address(-1001111111111), "-1001111111111@telegram.org")

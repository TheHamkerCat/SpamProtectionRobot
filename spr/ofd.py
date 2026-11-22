import logging
import time
from typing import Optional

import httpx
from pyfederation import AsyncFederationClient
from pyfederation.exceptions import FederationClientException
from pyfederation.records import ContentInput, EntityQueryResult, ScannedContent, ServerInformation
from telethon import utils
from telethon.tl import types

from spr.cache import AsyncLruCache
from spr.core.entities import get_username

logger = logging.getLogger(__name__)

OFD_TIMEOUT_SECONDS = 30
ENTITY_HOST = "telegram.org"
API_MAJOR_VERSION = 2
# Sender lookups are kept this long, so a raid from one sender costs one lookup
QUERY_CACHE_SIZE = 2_000
QUERY_CACHE_SECONDS = 300
# A published sender is published again after this long at the earliest
PUBLISHED_CACHE_SIZE = 5_000
PUBLISHED_CACHE_SECONDS = 3600
# A token the server could not check yet (unreachable) is checked again after this long
AUTH_RETRY_SECONDS = 300
# get_self() with a token that is unknown (401) or belongs to a disabled operator (403)
REJECTED_TOKEN_STATUSES = (401, 403)


class OfdError(Exception):
    """A Federation request failed. The message is safe to show in chats."""


def entity_address(peer_id: int) -> str:
    """Federation address of a Telegram user or chat, by its marked ID"""
    return f"{peer_id}@{ENTITY_HOST}"


def entity_metadata(entity) -> dict:
    """What the bot publishes about a sender: public profile facts only.
    Metadata is context for the server's operators, never a verdict."""
    if isinstance(entity, types.User):
        values = {
            "first_name": entity.first_name,
            "last_name": entity.last_name,
            "username": get_username(entity),
            "bot": entity.bot,
            "premium": entity.premium,
            "verified": entity.verified,
            "scam": entity.scam,
            "fake": entity.fake,
        }
    else:
        values = {
            "title": getattr(entity, "title", None),
            "username": get_username(entity),
            "megagroup": getattr(entity, "megagroup", None),
            "verified": getattr(entity, "verified", None),
            "scam": getattr(entity, "scam", None),
            "fake": getattr(entity, "fake", None),
        }
    return {key: value for key, value in values.items() if value is not None}


class OfdClient:
    def __init__(
        self,
        host: str,
        token: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        # http_client: tests pass one with an httpx.MockTransport instead of the network
        self.host = host
        self._client = AsyncFederationClient(
            host, token, http_client or httpx.AsyncClient(timeout=OFD_TIMEOUT_SECONDS)
        )
        # A token is "pending" until the server said whether it may contribute
        self._token_pending = bool(token)
        self._authenticated = False
        self._auth_checked_at: Optional[float] = None
        # Server information, None if it could not be read
        self.info: Optional[ServerInformation] = None
        self._queries = AsyncLruCache(QUERY_CACHE_SIZE, QUERY_CACHE_SECONDS)
        self._published = AsyncLruCache(PUBLISHED_CACHE_SIZE, PUBLISHED_CACHE_SECONDS)

    @property
    def can_contribute(self) -> bool:
        """The bot's token carries client permissions: it may publish senders"""
        return self._authenticated

    @property
    def can_scan(self) -> bool:
        """Content scans are allowed, with a token, or when the server
        accepts anonymous scans."""
        if self._authenticated or self._token_pending:
            return True
        return self.info is None or self.info.public_scan_content

    @property
    def can_query(self) -> bool:
        """Sender lookups are allowed, like can_scan"""
        if self._authenticated or self._token_pending:
            return True
        return self.info is None or self.info.public_query_entity

    @property
    def collects_public_scan_content(self) -> bool:
        """The server says it may keep content scanned without a token"""
        return self.info is not None and bool(self.info.collects_public_scan_content)

    async def start(self):
        """Read the server information and check the token. Never raises:
        a server that is down at startup only means fewer features until it is back."""
        try:
            self.info = await self._client.get_server_information()
        except (FederationClientException, ValueError) as error:
            logger.warning("Can't read the Federation server information: %s", error)
        else:
            if self.info.api_major_version != API_MAJOR_VERSION:
                logger.warning(
                    "The Federation server speaks OFD %s, this bot is written for OFD %s.x",
                    self.info.api_version,
                    API_MAJOR_VERSION,
                )
        await self._authenticate()

    async def close(self):
        await self._client.aclose()

    async def query_sender(self, peer_id: int) -> Optional[EntityQueryResult]:
        """The server's assessment of a sender, None if it knows nothing about them"""
        address = entity_address(peer_id)

        async def lookup():
            try:
                return await self._client.query_entity(address)
            except FederationClientException as error:
                if error.status_code == 404:
                    return None
                raise

        return await self._call(self._queries.get(address, lookup))

    async def scan(self, content: ContentInput, author: Optional[str] = None) -> ScannedContent:
        """Scan one message. author: the sender's address, None to leave it out"""
        return await self._call(self._client.scan_content(content, author))

    async def publish_sender(self, sender):
        """Publish a sender with its public profile"""
        await self._check_pending_token()
        if not self._authenticated:
            return
        peer_id = utils.get_peer_id(sender)

        async def push():
            await self._client.push_entity(ENTITY_HOST, str(peer_id), entity_metadata(sender))
            return True

        try:
            await self._call(self._published.get(peer_id, push))
        except OfdError as error:
            logger.warning("Can't publish %s to Federation: %s", entity_address(peer_id), error)

    async def _check_pending_token(self):
        """Check a token again that the server could not check before"""
        if not self._token_pending:
            return
        checked_at = self._auth_checked_at
        if checked_at is None or time.monotonic() - checked_at >= AUTH_RETRY_SECONDS:
            await self._authenticate()

    async def _authenticate(self):
        if not self._token_pending:
            return
        self._auth_checked_at = time.monotonic()
        try:
            operator = await self._client.get_self()
        except (FederationClientException, ValueError) as error:
            status = getattr(error, "status_code", 0)
            if status in REJECTED_TOKEN_STATUSES:
                self._discard_token("the server rejected OFD_TOKEN")
            else:
                logger.warning("Can't check OFD_TOKEN yet, trying again later: %s", error)
            return
        if operator.disabled or not operator.permissions.client:
            self._discard_token(f"the operator {operator.name!r} has no client permissions")
            return
        self._token_pending = False
        self._authenticated = True
        logger.info("Federation: contributing as the operator %r", operator.name)

    def _discard_token(self, reason: str):
        """Carry on anonymously, a token without client permissions is held
        to the authenticated standard without meeting it, worse than none."""
        logger.warning("Federation: %s, using the server anonymously", reason)
        self._client.access_token = None
        self._token_pending = False
        self._authenticated = False

    async def _call(self, awaitable):
        try:
            return await awaitable
        except FederationClientException as error:
            if error.status_code == 401 and self._client.access_token is not None:
                self._discard_token("the server no longer accepts OFD_TOKEN")
            if error.status_code == 429:
                raise OfdError("Too many Federation requests. Try again in a minute.") from error
            if error.status_code == 0:
                raise OfdError("Can't reach the Federation server. Try again later.") from error
            raise OfdError(f"Federation request failed (HTTP {error.status_code}).") from error
        except ValueError as error:
            # Invalid input or a response that does not fit the records
            raise OfdError("Federation sent an unexpected response.") from error

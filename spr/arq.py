"""Client for the two ARQ endpoints the bot uses: spam (nlp) and NSFW scans."""
from dataclasses import dataclass, fields
from typing import Optional

import httpx

ARQ_TIMEOUT_SECONDS = 30


class ArqError(Exception):
    """An ARQ request failed. The message is safe to show in chats."""


@dataclass(frozen=True)
class NsfwResult:
    """Scores are percentages"""

    neutral: float
    porn: float
    hentai: float
    sexy: float
    drawings: float
    is_nsfw: bool


@dataclass(frozen=True)
class SpamResult:
    """spam_probability, spam and ham are percentages"""

    is_spam: bool
    spam_probability: float
    spam: float
    ham: float
    profanity: bool


def _parse(result_class, data):
    """Build a result dataclass from an ARQ response item"""
    try:
        return result_class(**{field.name: data[field.name] for field in fields(result_class)})
    except (KeyError, TypeError) as error:
        raise ArqError("ARQ sent an unexpected response.") from error


class ArqClient:
    def __init__(
        self,
        api_url: str,
        api_key: str,
        timeout: float = ARQ_TIMEOUT_SECONDS,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        # transport: tests pass an httpx.MockTransport instead of the network
        self._client = httpx.AsyncClient(
            base_url=api_url.rstrip("/"),
            headers={"X-API-KEY": api_key},
            timeout=timeout,
            transport=transport,
        )

    async def close(self):
        await self._client.aclose()

    async def nsfw_scan(self, image: bytes) -> NsfwResult:
        result = await self._post(
            "nsfw_scan", files={"file": ("file", image, "application/octet-stream")}
        )
        return _parse(NsfwResult, result)

    async def nlp(self, text: str) -> SpamResult:
        result = await self._post("nlp", json={"messages": [text]})
        if not isinstance(result, list) or not result:
            raise ArqError("ARQ sent no spam prediction.")
        return _parse(SpamResult, result[0])

    async def _post(self, route: str, **kwargs):
        """POST to ARQ, return the "result" field of the response"""
        try:
            response = await self._client.post(route, **kwargs)
        except httpx.HTTPError as error:
            # Connection problems and timeouts
            raise ArqError("Can't reach the ARQ server. Try again later.") from error

        if response.status_code in (401, 403):
            raise ArqError("The ARQ API key is not valid. Get a new one from @ARQRobot.")
        if response.status_code == 429:
            raise ArqError("Too many ARQ requests. Try again in a minute.")
        if response.status_code >= 400:
            raise ArqError(f"ARQ request failed (HTTP {response.status_code}).")
        try:
            body = response.json()
        except ValueError as error:
            raise ArqError("ARQ sent an unexpected response.") from error

        if not isinstance(body, dict):
            raise ArqError("ARQ sent an unexpected response.")
        if not body.get("ok"):
            raise ArqError(f"ARQ error: {body.get('result') or 'unknown error'}")
        return body.get("result")

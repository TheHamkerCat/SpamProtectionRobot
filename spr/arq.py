"""Client for the two ARQ endpoints the bot uses: spam (nlp) and NSFW scans."""
import asyncio
from dataclasses import dataclass, fields

import aiohttp

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
    def __init__(self, api_url: str, api_key: str, timeout: float = ARQ_TIMEOUT_SECONDS):
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session = None

    def _get_session(self) -> aiohttp.ClientSession:
        # Created on first use, so it binds to the running event loop
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-API-KEY": self._api_key}, timeout=self._timeout
            )
        return self._session

    async def close(self):
        if self._session is not None:
            await self._session.close()

    async def nsfw_scan(self, image: bytes) -> NsfwResult:
        form = aiohttp.FormData()
        form.add_field(
            "file", image, filename="file", content_type="application/octet-stream"
        )
        result = await self._post("nsfw_scan", data=form)
        return _parse(NsfwResult, result)

    async def nlp(self, text: str) -> SpamResult:
        result = await self._post("nlp", json={"messages": [text]})
        if not isinstance(result, list) or not result:
            raise ArqError("ARQ sent no spam prediction.")
        return _parse(SpamResult, result[0])

    async def _post(self, route: str, **kwargs):
        """POST to ARQ, return the "result" field of the response"""
        url = f"{self._api_url}/{route}"
        try:
            async with self._get_session().post(url, **kwargs) as response:
                if response.status in (401, 403):
                    raise ArqError(
                        "The ARQ API key is not valid. Get a new one from @ARQRobot."
                    )
                if response.status == 429:
                    raise ArqError("Too many ARQ requests. Try again in a minute.")
                if response.status >= 400:
                    raise ArqError(f"ARQ request failed (HTTP {response.status}).")
                body = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            raise ArqError("Can't reach the ARQ server. Try again later.") from error

        if not isinstance(body, dict):
            raise ArqError("ARQ sent an unexpected response.")
        if not body.get("ok"):
            raise ArqError(f"ARQ error: {body.get('result') or 'unknown error'}")
        return body.get("result")

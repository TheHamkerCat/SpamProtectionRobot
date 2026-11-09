"""Client for the ARQ endpoints the bot uses: spam (/nlp, /nlp/model) and NSFW scans."""
import asyncio
import time
from dataclasses import dataclass, fields
from typing import Optional

import httpx

ARQ_TIMEOUT_SECONDS = 30
# ARQ's suggested threshold the bot deletes above. "strict": at most 1 % of
# normal chat messages are above it, "normal": at most 0.5 % (catches less spam).
SPAM_THRESHOLD = "strict"
# /nlp/model is read again after this long, and whenever /nlp reports another model
MODEL_INFO_SECONDS = 3600


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
    """ARQ's /nlp answer for one message. spam and abusive are percentages;
    category only means something when spam is high."""

    spam: float
    category: str
    harm: int  # 0 none, 1 annoying ad, 2 scam or fraud
    abusive: float
    language: str
    model_version: str


@dataclass(frozen=True)
class SpamModel:
    """ARQ's spam model: its version and the threshold (a percentage)"""

    version: str
    spam_above: float


def _parse(result_class, data):
    """Build a result dataclass from an ARQ response item"""
    try:
        return result_class(**{field.name: data[field.name] for field in fields(result_class)})
    except (KeyError, TypeError) as error:
        raise ArqError("ARQ sent an unexpected response.") from error


def _percent(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArqError("ARQ sent an unexpected response.")
    return round(float(value) * 100, 2)


def _parse_spam(item, model_version) -> SpamResult:
    if not isinstance(item, dict) or not isinstance(model_version, str):
        raise ArqError("ARQ sent an unexpected response.")
    harm = item.get("harm", 0)
    if isinstance(harm, bool) or not isinstance(harm, int):
        raise ArqError("ARQ sent an unexpected response.")
    return SpamResult(
        spam=_percent(item.get("spam")),
        category=str(item.get("category") or ""),
        harm=harm,
        abusive=_percent(item.get("abusive", 0)),
        language=str(item.get("language") or ""),
        model_version=model_version,
    )


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
        self._model: Optional[SpamModel] = None
        self._model_read_at = 0.0
        self._model_lock = asyncio.Lock()

    async def close(self):
        await self._client.aclose()

    async def nsfw_scan(self, image: bytes) -> NsfwResult:
        result = await self._post(
            "nsfw_scan", files={"file": ("file", image, "application/octet-stream")}
        )
        return _parse(NsfwResult, result)

    async def nlp(self, text: str) -> SpamResult:
        body = await self._request("POST", "nlp", json={"messages": [text]})
        result = body.get("result")
        if not isinstance(result, list) or not result:
            raise ArqError("ARQ sent no spam prediction.")
        return _parse_spam(result[0], body.get("model_version"))

    async def spam_model(self, seen_version: Optional[str] = None) -> SpamModel:
        """ARQ's spam model version and threshold, read at most once an hour,
        or again when /nlp answered with another version (seen_version)"""
        async with self._model_lock:
            fresh = time.monotonic() - self._model_read_at < MODEL_INFO_SECONDS
            same = seen_version is None or (self._model is not None and self._model.version == seen_version)
            if self._model is not None and fresh and same:
                return self._model
            info = (await self._request("GET", "nlp/model")).get("result")
            try:
                version = info["version"]
                spam_above = _percent(info["thresholds"][SPAM_THRESHOLD]["spam_above"])
            except (KeyError, TypeError) as error:
                raise ArqError("ARQ sent an unexpected response.") from error
            if not isinstance(version, str):
                raise ArqError("ARQ sent an unexpected response.")
            self._model = SpamModel(version, spam_above)
            self._model_read_at = time.monotonic()
            return self._model

    async def _post(self, route: str, **kwargs):
        """POST to ARQ, return the "result" field of the response"""
        return (await self._request("POST", route, **kwargs)).get("result")

    async def _request(self, method: str, route: str, **kwargs) -> dict:
        """The whole JSON body of a successful ARQ response"""
        try:
            response = await self._client.request(method, route, **kwargs)
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
        return body

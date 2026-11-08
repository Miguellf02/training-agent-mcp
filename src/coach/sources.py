"""Where activities come from. Two interchangeable sources behind one Protocol:

- IntervalsSource: the live Intervals.icu REST API.
- FixtureSource:   a frozen JSON file, so tests and evals run offline and reproducibly.
"""

import datetime as dt
import json
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from coach.models import BlockedActivity, RawActivity

INTERVALS_BASE_URL = "https://intervals.icu/api/v1"
RUN_TYPES = frozenset({"Run", "TrailRun", "VirtualRun"})


class SourceError(Exception):
    """A data-source failure whose message is safe and useful to show the agent."""


STRAVA_BLOCKED_REASON = (
    "Synced from Strava: Intervals.icu does not expose Strava data through its API, so its "
    "distance and time are unknown. Fix: import the Strava archive into Intervals.icu "
    "instead of syncing from Strava."
)


class FetchResult(BaseModel):
    """Readable runs plus the activities that exist but could not be read."""

    model_config = ConfigDict(frozen=True)

    runs: list[RawActivity]
    blocked: list[BlockedActivity]

    def between(self, start: dt.date, end: dt.date) -> "FetchResult":
        """Only the runs and blocked activities dated within [start, end]."""
        return FetchResult(
            runs=[r for r in self.runs if start <= r.date <= end],
            blocked=[b for b in self.blocked if start <= b.date <= end],
        )


class ActivitySource(Protocol):
    def fetch(self, start: dt.date, end: dt.date) -> FetchResult: ...


def _blocked(rec: dict[str, object]) -> BlockedActivity:
    start_local = rec.get("start_date_local")
    if not isinstance(start_local, str):
        raise SourceError(
            f"Blocked activity {rec.get('id')} has no start_date_local, so it cannot be placed "
            "in a week. Import the Strava archive into Intervals.icu instead of syncing."
        )
    try:
        day = dt.date.fromisoformat(start_local[:10])
    except ValueError as exc:
        raise SourceError(
            f"Blocked activity {rec.get('id')} has an invalid start_date_local {start_local!r}."
        ) from exc
    return BlockedActivity(id=str(rec.get("id")), date=day, reason=STRAVA_BLOCKED_REASON)


def _parse(records: object) -> FetchResult:
    if not isinstance(records, list):
        raise SourceError("Unexpected response: expected a JSON list of activities.")
    runs: list[RawActivity] = []
    blocked: list[BlockedActivity] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        rec_type = rec.get("type")
        if "_note" in rec:
            # Intervals blocks activities synced from Strava (Strava API terms). Record it
            # instead of failing, unless the stub says it is not a run (e.g. a ride).
            if rec_type is None or rec_type in RUN_TYPES:
                blocked.append(_blocked(rec))
            continue
        if rec_type not in RUN_TYPES:
            continue
        try:
            runs.append(RawActivity.model_validate(rec))
        except ValidationError as exc:
            raise SourceError(
                f"Malformed activity {rec.get('id')}: {exc.errors()[0]['msg']}"
            ) from exc
    return FetchResult(runs=runs, blocked=blocked)


class FixtureSource:
    def __init__(self, path: Path) -> None:
        self._path = path

    def fetch(self, start: dt.date, end: dt.date) -> FetchResult:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SourceError(f"Fixtures file not found: {self._path}") from exc
        except ValueError as exc:
            raise SourceError(f"Fixtures file {self._path} is not valid JSON.") from exc
        if isinstance(data, dict):
            if "activities" not in data:
                raise SourceError(f"Fixtures file {self._path} has no 'activities' list.")
            data = data["activities"]
        return _parse(data).between(start, end)


def _rate_limit_message(retry_after: str | None) -> str:
    """Only a number is echoed: the header is remote text and this message reaches the agent."""
    wait = f"Wait {retry_after} seconds" if retry_after and retry_after.isdecimal() else "Wait"
    return f"Rate limited by Intervals.icu (HTTP 429). {wait} before retrying this call."


class IntervalsSource:
    def __init__(
        self,
        api_key: str,
        athlete_id: str = "0",
        timeout_s: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=INTERVALS_BASE_URL,
            auth=("API_KEY", api_key),
            timeout=timeout_s,
            transport=transport,
        )
        self._athlete_id = athlete_id
        self._timeout_s = timeout_s

    def fetch(self, start: dt.date, end: dt.date) -> FetchResult:
        try:
            resp = self._client.get(
                f"/athlete/{self._athlete_id}/activities",
                params={"oldest": start.isoformat(), "newest": end.isoformat()},
            )
        except httpx.TimeoutException as exc:
            raise SourceError(
                f"Intervals.icu did not answer within {self._timeout_s:g} s. Retry once; "
                "if it fails again, report the data as unavailable."
            ) from exc
        except httpx.HTTPError as exc:
            raise SourceError(f"Could not reach Intervals.icu: {type(exc).__name__}") from exc
        if resp.status_code in (401, 403):
            raise SourceError("Intervals.icu rejected the API key (check COACH_INTERVALS_API_KEY).")
        if resp.status_code == 429:
            raise SourceError(_rate_limit_message(resp.headers.get("Retry-After")))
        if resp.is_error:
            raise SourceError(f"Intervals.icu returned HTTP {resp.status_code}.")
        try:
            records = resp.json()
        except ValueError as exc:
            raise SourceError(
                "Intervals.icu answered with a body that is not valid JSON. Retry once; "
                "if it fails again, report the data as unavailable."
            ) from exc
        return _parse(records).between(start, end)

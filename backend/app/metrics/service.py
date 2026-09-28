"""Atomic aggregate metrics with no channel for conversation or identifier data."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.models.enums import MetricName, MetricOutcome

# Every observation increments exactly one fixed latency bucket. Values above the approved
# nine-second request wall time are clamped into the last bucket and cannot grow without bound.
LATENCY_BUCKET_UPPER_BOUNDS_MS: tuple[int, ...] = (100, 250, 500, 1_000, 2_000, 4_000, 9_000)
MAX_RECORDED_LATENCY_MS = LATENCY_BUCKET_UPPER_BOUNDS_MS[-1]

METRIC_UPSERT = text(
    """
    INSERT INTO aggregate_metrics (
        hour_bucket,
        bounded_metric_name,
        outcome_enum,
        count,
        sum_ms,
        histogram_buckets
    )
    VALUES (
        :hour_bucket,
        :metric_name,
        :outcome,
        1,
        :latency_ms,
        CAST(:histogram_buckets AS BIGINT[])
    )
    ON CONFLICT (hour_bucket, bounded_metric_name, outcome_enum)
    DO UPDATE SET
        count = aggregate_metrics.count + EXCLUDED.count,
        sum_ms = aggregate_metrics.sum_ms + EXCLUDED.sum_ms,
        histogram_buckets = ARRAY[
            aggregate_metrics.histogram_buckets[1] + EXCLUDED.histogram_buckets[1],
            aggregate_metrics.histogram_buckets[2] + EXCLUDED.histogram_buckets[2],
            aggregate_metrics.histogram_buckets[3] + EXCLUDED.histogram_buckets[3],
            aggregate_metrics.histogram_buckets[4] + EXCLUDED.histogram_buckets[4],
            aggregate_metrics.histogram_buckets[5] + EXCLUDED.histogram_buckets[5],
            aggregate_metrics.histogram_buckets[6] + EXCLUDED.histogram_buckets[6],
            aggregate_metrics.histogram_buckets[7] + EXCLUDED.histogram_buckets[7]
        ]
    """
)

Clock = Callable[[], datetime]


class MetricWriteError(RuntimeError):
    """A sanitized aggregate write failure with no database or request details."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _hour_bucket(observed_at: datetime) -> datetime:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("metric clock must return a timezone-aware timestamp")
    return observed_at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _bounded_latency(latency_ms: int) -> int:
    if type(latency_ms) is not int or latency_ms < 0:
        raise ValueError("metric latency must be a nonnegative integer")
    return min(latency_ms, MAX_RECORDED_LATENCY_MS)


def _histogram_increment(latency_ms: int) -> list[int]:
    buckets = [0] * len(LATENCY_BUCKET_UPPER_BOUNDS_MS)
    for index, upper_bound in enumerate(LATENCY_BUCKET_UPPER_BOUNDS_MS):
        if latency_ms <= upper_bound:
            buckets[index] = 1
            return buckets
    raise AssertionError("bounded latency did not fit the fixed histogram")


class AggregateMetricService:
    """Persist only bounded counts, summed latency, and fixed histogram buckets.

    The public method accepts two enums and an integer latency. It deliberately has no metadata,
    tag, label, identifier, URL, IP address, query-string, message, or free-text parameter.
    """

    def __init__(self, engine: Engine, *, clock: Clock = _utc_now) -> None:
        self._engine = engine
        self._clock = clock

    def record(
        self,
        metric_name: MetricName,
        outcome: MetricOutcome,
        *,
        latency_ms: int,
    ) -> None:
        """Atomically add one observation to its UTC-hour aggregate row."""

        if not isinstance(metric_name, MetricName):
            raise TypeError("metric name must be a bounded MetricName")
        if not isinstance(outcome, MetricOutcome):
            raise TypeError("metric outcome must be a bounded MetricOutcome")

        bounded_latency = _bounded_latency(latency_ms)
        parameters: dict[str, object] = {
            "hour_bucket": _hour_bucket(self._clock()),
            "metric_name": metric_name.value,
            "outcome": outcome.value,
            "latency_ms": bounded_latency,
            "histogram_buckets": _histogram_increment(bounded_latency),
        }
        try:
            with self._engine.begin() as connection:
                connection.execute(METRIC_UPSERT, parameters)
        except SQLAlchemyError:
            raise MetricWriteError("aggregate metric could not be recorded") from None

    def try_record(
        self,
        metric_name: MetricName,
        outcome: MetricOutcome,
        *,
        latency_ms: int,
    ) -> bool:
        """Record best-effort telemetry without making a database outage fail a request."""

        try:
            self.record(metric_name, outcome, latency_ms=latency_ms)
        except MetricWriteError:
            return False
        return True

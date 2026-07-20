from __future__ import annotations

import asyncio
import math
import os
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


FEATURE_COUNT = 198
SCORE_COUNT = 6
REQUEST_MAGIC = b"EIQ1"
RESPONSE_MAGIC = b"EIR1"
REQUEST_HEADER = struct.Struct("<4sII")
REQUEST_FEATURES = struct.Struct("<198f")
RESPONSE = struct.Struct("<4sIiI6f")


class RunnerError(RuntimeError):
    """The native inference process failed or violated its protocol."""


@dataclass(frozen=True)
class RunnerResult:
    scores: tuple[float, ...]
    inference_us: int
    queue_us: int


class EdgeImpulseRunner:
    """Persistent, serialized adapter for the native Edge Impulse runner."""

    def __init__(self, executable: Path, timeout_s: float = 10.0) -> None:
        self.executable = executable.expanduser().resolve()
        self.timeout_s = timeout_s
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        if self.is_running:
            return
        if not self.executable.is_file():
            raise RunnerError(f"Inference runner was not found: {self.executable}")
        if os.name != "nt" and not os.access(self.executable, os.X_OK):
            raise RunnerError(f"Inference runner is not executable: {self.executable}")

        creationflags = 0x08000000 if os.name == "nt" else 0
        try:
            self._process = await asyncio.create_subprocess_exec(
                str(self.executable),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=None,
                creationflags=creationflags,
            )
        except OSError as error:
            raise RunnerError(f"Could not start the inference runner: {error}") from error

    async def warm_up(self) -> RunnerResult:
        return await self.classify(0, [0.0] * FEATURE_COUNT)

    async def classify(
        self,
        window_id: int,
        features: Sequence[float],
    ) -> RunnerResult:
        if not 0 <= window_id <= 0xFFFFFFFF:
            raise RunnerError("window_id must fit in an unsigned 32-bit integer.")
        if len(features) != FEATURE_COUNT:
            raise RunnerError(
                f"Expected {FEATURE_COUNT} features, received {len(features)}."
            )
        try:
            numeric_features = tuple(float(value) for value in features)
        except (TypeError, ValueError):
            raise RunnerError("All features must be numbers.") from None
        if any(not math.isfinite(value) for value in numeric_features):
            raise RunnerError("All features must be finite numbers.")

        queued_at_ns = time.perf_counter_ns()
        async with self._lock:
            queue_us = max(0, (time.perf_counter_ns() - queued_at_ns) // 1000)
            for attempt in range(2):
                try:
                    await self.start()
                    scores, inference_us = await asyncio.wait_for(
                        self._exchange(window_id, numeric_features),
                        timeout=self.timeout_s,
                    )
                    return RunnerResult(
                        scores=scores,
                        inference_us=inference_us,
                        queue_us=queue_us,
                    )
                except RunnerError:
                    raise
                except asyncio.CancelledError:
                    # A cancelled exchange may leave a response frame unread.
                    # Restart so the next request begins on a clean protocol stream.
                    await self.stop()
                    raise
                except (
                    asyncio.IncompleteReadError,
                    asyncio.TimeoutError,
                    BrokenPipeError,
                    ConnectionError,
                    OSError,
                ) as error:
                    await self.stop()
                    if attempt == 1:
                        raise RunnerError(
                            "The inference runner failed twice during one request."
                        ) from error
            raise AssertionError("unreachable")

    async def _exchange(
        self,
        window_id: int,
        features: tuple[float, ...],
    ) -> tuple[tuple[float, ...], int]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise ConnectionError("The inference runner is not connected.")

        payload = REQUEST_HEADER.pack(
            REQUEST_MAGIC,
            window_id,
            FEATURE_COUNT,
        ) + REQUEST_FEATURES.pack(*features)
        process.stdin.write(payload)
        await process.stdin.drain()
        raw_response = await process.stdout.readexactly(RESPONSE.size)
        magic, response_window, status, inference_us, *raw_scores = RESPONSE.unpack(
            raw_response
        )
        if magic != RESPONSE_MAGIC or response_window != window_id:
            await self.stop()
            raise RunnerError("The inference runner returned a mismatched response.")
        if status != 0:
            raise RunnerError(f"Edge Impulse classifier failed with status {status}.")

        scores = tuple(float(score) for score in raw_scores)
        if len(scores) != SCORE_COUNT or any(
            not math.isfinite(score) for score in scores
        ):
            raise RunnerError("The inference runner returned invalid scores.")
        return scores, int(inference_us)

    async def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

from __future__ import annotations

import asyncio
import json
import math
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

try:
    from .inference_protocol import FEATURE_COUNT, MODEL_LABELS
except ImportError:
    from inference_protocol import FEATURE_COUNT, MODEL_LABELS


RUNNER_REQUEST_HEADER = struct.Struct("<4sII")
RUNNER_FEATURES = struct.Struct("<198f")
RUNNER_RESPONSE = struct.Struct("<4sIiI6f")
RUNNER_REQUEST_MAGIC = b"EIQ1"
RUNNER_RESPONSE_MAGIC = b"EIR1"
BACKEND_IDS = {"local": 0, "edge": 1, "cloud": 2}


class ModelBackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class InferenceOutput:
    scores: tuple[float, ...]
    inference_us: int
    backend: str
    model_version: str


class EdgeInferenceRunner:
    """Persistent stdin/stdout adapter for the local Edge Impulse C++ runner."""

    def __init__(self, executable: Path) -> None:
        self.executable = executable
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._process is not None and self._process.returncode is None:
            return
        if not self.executable.is_file():
            raise ModelBackendError(
                f"Windows inference runner was not found: {self.executable}. "
                "Build edge_runner first; fake inference is not used."
            )
        creationflags = 0x08000000 if os.name == "nt" else 0
        self._process = await asyncio.create_subprocess_exec(
            str(self.executable),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=creationflags,
        )

    async def warm_up(self) -> None:
        await self.classify(0, [0.0] * FEATURE_COUNT)

    async def classify(
        self,
        window_id: int,
        features: Sequence[float],
    ) -> tuple[int, tuple[float, ...]]:
        if len(features) != FEATURE_COUNT:
            raise ModelBackendError(
                f"Expected {FEATURE_COUNT} features, got {len(features)}."
            )
        async with self._lock:
            for attempt in range(2):
                try:
                    await self.start()
                    return await self._classify_once(window_id, features)
                except (BrokenPipeError, ConnectionError, asyncio.IncompleteReadError):
                    await self.stop()
                    if attempt == 1:
                        raise ModelBackendError(
                            "The local inference runner exited twice during one request."
                        ) from None
            raise AssertionError("unreachable")

    async def _classify_once(
        self,
        window_id: int,
        features: Sequence[float],
    ) -> tuple[int, tuple[float, ...]]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise ModelBackendError("Local inference runner is not available.")

        payload = RUNNER_REQUEST_HEADER.pack(
            RUNNER_REQUEST_MAGIC,
            window_id & 0xFFFFFFFF,
            FEATURE_COUNT,
        ) + RUNNER_FEATURES.pack(*features)
        process.stdin.write(payload)
        await process.stdin.drain()
        response = await process.stdout.readexactly(RUNNER_RESPONSE.size)
        magic, response_window, status, inference_us, *scores = RUNNER_RESPONSE.unpack(
            response
        )
        if magic != RUNNER_RESPONSE_MAGIC or response_window != (
            window_id & 0xFFFFFFFF
        ):
            raise ModelBackendError(
                "Local inference runner returned a mismatched response frame."
            )
        if status != 0:
            raise ModelBackendError(
                f"Edge Impulse classifier failed with status {status}."
            )
        return inference_us, _validate_scores(scores)

    async def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()


class LocalModelBackend:
    name = "local"
    deployment_id = BACKEND_IDS[name]

    def __init__(self, runner_path: Path, model_version: str) -> None:
        self.runner = EdgeInferenceRunner(runner_path.expanduser().resolve())
        self.model_version = model_version

    async def start(self) -> None:
        await self.runner.start()
        await self.runner.warm_up()

    async def classify(
        self,
        window_id: int,
        features: Sequence[float],
    ) -> InferenceOutput:
        inference_us, scores = await self.runner.classify(window_id, features)
        return InferenceOutput(
            scores=scores,
            inference_us=inference_us,
            backend=self.name,
            model_version=self.model_version,
        )

    async def close(self) -> None:
        await self.runner.stop()


class RestModelBackend:
    """Adapter for an edge or cloud model exposed as an HTTP JSON endpoint."""

    def __init__(
        self,
        *,
        backend: str,
        url: str,
        api_key: str | None,
        timeout_s: float,
        model_version: str,
    ) -> None:
        if backend not in {"edge", "cloud"}:
            raise ModelBackendError(f"Unsupported REST model backend: {backend}")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ModelBackendError(
                "Edge/cloud model URL must be an absolute http:// or https:// URL."
            )
        self.name = backend
        self.deployment_id = BACKEND_IDS[backend]
        self.url = url
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.model_version = model_version

    async def start(self) -> None:
        # A real request verifies the configured model contract and warms the
        # remote deployment before the first measured capture.
        await self._classify(0, [0.0] * FEATURE_COUNT, warmup=True)

    async def classify(
        self,
        window_id: int,
        features: Sequence[float],
    ) -> InferenceOutput:
        return await self._classify(window_id, features, warmup=False)

    async def _classify(
        self,
        window_id: int,
        features: Sequence[float],
        *,
        warmup: bool,
    ) -> InferenceOutput:
        if len(features) != FEATURE_COUNT:
            raise ModelBackendError(
                f"Expected {FEATURE_COUNT} features, got {len(features)}."
            )
        payload = {
            "version": 1,
            "window_id": window_id,
            "feature_count": FEATURE_COUNT,
            "features": [float(value) for value in features],
            "labels": list(MODEL_LABELS),
            "warmup": warmup,
        }
        started_ns = time.perf_counter_ns()
        response = await asyncio.to_thread(self._post_json, payload)
        wall_us = max(0, (time.perf_counter_ns() - started_ns) // 1000)
        scores_value = response.get("scores")
        if isinstance(scores_value, dict):
            scores = [scores_value.get(label) for label in MODEL_LABELS]
        elif isinstance(scores_value, list):
            scores = scores_value
        else:
            raise ModelBackendError("Model response must contain six scores.")
        inference_us = _nonnegative_int(
            response.get("inference_us", wall_us),
            "inference_us",
        )
        model_version = str(response.get("model_version", self.model_version))
        return InferenceOutput(
            scores=_validate_scores(scores),
            inference_us=inference_us,
            backend=self.name,
            model_version=model_version,
        )

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"content-type": "application/json", "accept": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read(512).decode("utf-8", errors="replace")
            raise ModelBackendError(
                f"{self.name.title()} model returned HTTP {error.code}: {detail}"
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ModelBackendError(
                f"Could not reach {self.name} model endpoint: {error}"
            ) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ModelBackendError(
                f"{self.name.title()} model returned invalid JSON."
            ) from error
        if not isinstance(decoded, dict):
            raise ModelBackendError("Model response must be a JSON object.")
        if decoded.get("ok") is False:
            raise ModelBackendError(str(decoded.get("error", "Remote inference failed.")))
        return decoded

    async def close(self) -> None:
        return None


def create_model_backend(config: Any) -> LocalModelBackend | RestModelBackend:
    backend = str(config.model_backend).strip().lower()
    if backend == "local":
        return LocalModelBackend(Path(config.runner_path), str(config.model_version))
    if backend in {"edge", "cloud"}:
        return RestModelBackend(
            backend=backend,
            url=str(config.model_url),
            api_key=config.model_api_key,
            timeout_s=float(config.model_timeout_s),
            model_version=str(config.model_version),
        )
    raise ModelBackendError(f"Unsupported model backend: {backend}")


def _validate_scores(values: Sequence[Any]) -> tuple[float, ...]:
    if len(values) != len(MODEL_LABELS):
        raise ModelBackendError(
            f"Expected {len(MODEL_LABELS)} scores, got {len(values)}."
        )
    try:
        scores = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        raise ModelBackendError("Model scores must all be numbers.") from None
    if any(not math.isfinite(value) for value in scores):
        raise ModelBackendError("Model scores must all be finite numbers.")
    return scores


def _nonnegative_int(value: Any, field_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ModelBackendError(f"{field_name} must be an integer.") from None
    if number < 0:
        raise ModelBackendError(f"{field_name} must be non-negative.")
    return number

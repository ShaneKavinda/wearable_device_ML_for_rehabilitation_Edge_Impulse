from __future__ import annotations

import math
import os
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Literal

import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .runner import FEATURE_COUNT, EdgeImpulseRunner, RunnerError


MODEL_LABELS = (
    "Extension",
    "Flexion",
    "Pronation",
    "Radial Deviation",
    "Supination",
    "Ulnar Deviation",
)
PROJECT_ID = "738400"
DEPLOYMENT_VERSION = "19"
DEFAULT_MODEL_VERSION = "ei-738400-deployment-19"
PROTECTED_PATHS = frozenset({"/v1/infer", "/metrics"})
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _escape_prometheus_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _environment_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false.")


@dataclass(frozen=True)
class Settings:
    runner_path: Path
    runner_timeout_s: float
    model_version: str
    api_key: str
    allow_unauthenticated: bool
    max_body_bytes: int

    @classmethod
    def from_environment(cls) -> "Settings":
        try:
            runner_timeout_s = float(os.getenv("RUNNER_TIMEOUT_S", "10"))
            max_body_bytes = int(os.getenv("MAX_BODY_BYTES", "32768"))
        except ValueError as error:
            raise RuntimeError(
                "RUNNER_TIMEOUT_S and MAX_BODY_BYTES must be numeric."
            ) from error
        if runner_timeout_s <= 0:
            raise RuntimeError("RUNNER_TIMEOUT_S must be greater than zero.")
        if max_body_bytes < 4096:
            raise RuntimeError("MAX_BODY_BYTES must be at least 4096.")
        return cls(
            runner_path=Path(
                os.getenv("MODEL_RUNNER_PATH", "/usr/local/bin/edge_inference_runner")
            ),
            runner_timeout_s=runner_timeout_s,
            model_version=os.getenv("MODEL_VERSION", DEFAULT_MODEL_VERSION).strip(),
            api_key=os.getenv("API_KEY", "").strip(),
            allow_unauthenticated=_environment_flag("ALLOW_UNAUTHENTICATED"),
            max_body_bytes=max_body_bytes,
        )

    def validate(self) -> None:
        if not self.model_version:
            raise RuntimeError("MODEL_VERSION must not be empty.")
        if not self.allow_unauthenticated and not self.api_key:
            raise RuntimeError(
                "API_KEY is required unless ALLOW_UNAUTHENTICATED=true."
            )
        if not self.allow_unauthenticated and len(self.api_key) < 16:
            raise RuntimeError("API_KEY must contain at least 16 characters.")


class InferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    window_id: int = Field(ge=0, le=0xFFFFFFFF)
    feature_count: Literal[198]
    features: list[float] = Field(min_length=FEATURE_COUNT, max_length=FEATURE_COUNT)
    labels: list[str] = Field(min_length=len(MODEL_LABELS), max_length=len(MODEL_LABELS))
    warmup: bool = False

    @field_validator("features")
    @classmethod
    def require_finite_features(cls, values: list[float]) -> list[float]:
        if any(not math.isfinite(value) for value in values):
            raise ValueError("features must contain only finite numbers")
        return values

    @field_validator("labels")
    @classmethod
    def require_model_labels(cls, values: list[str]) -> list[str]:
        if tuple(values) != MODEL_LABELS:
            raise ValueError("labels do not match the deployed model contract")
        return values


class _Gauge:
    def __init__(
        self,
        name: str,
        help_text: str,
        initial: float = 0.0,
        metric_type: str = "gauge",
    ) -> None:
        self.name = name
        self.help_text = help_text
        self.value = initial
        self.metric_type = metric_type

    def set(self, value: float) -> None:
        self.value = float(value)

    def inc(self) -> None:
        self.value += 1.0

    def dec(self) -> None:
        self.value -= 1.0

    def render(self) -> list[str]:
        return [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} {self.metric_type}",
            f"{self.name} {self.value}",
        ]


class _LabeledGauge:
    def __init__(self, name: str, help_text: str, label_name: str) -> None:
        self.name = name
        self.help_text = help_text
        self.label_name = label_name
        self.values: dict[str, float] = {}

    def set(self, label: str, value: float) -> None:
        self.values[label] = float(value)

    def render(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} gauge",
        ]
        for label, value in sorted(self.values.items()):
            lines.append(f'{self.name}{{{self.label_name}="{label}"}} {value}')
        return lines


class _SingleLabelCounter:
    def __init__(self, name: str, help_text: str, label_name: str) -> None:
        self.name = name
        self.help_text = help_text
        self.label_name = label_name
        self.values: defaultdict[str, float] = defaultdict(float)

    def inc(self, label: str) -> None:
        self.values[label] += 1.0

    def render(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} counter",
        ]
        for label, value in sorted(self.values.items()):
            lines.append(f'{self.name}{{{self.label_name}="{label}"}} {value}')
        return lines


class _LabeledCounter:
    def __init__(self, name: str, help_text: str) -> None:
        self.name = name
        self.help_text = help_text
        self.values: defaultdict[tuple[str, str], float] = defaultdict(float)

    def inc(self, outcome: str, warmup: str) -> None:
        self.values[(outcome, warmup)] += 1.0

    def render(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} counter",
        ]
        for (outcome, warmup), value in sorted(self.values.items()):
            lines.append(
                f'{self.name}{{outcome="{outcome}",warmup="{warmup}"}} {value}'
            )
        return lines


class _Histogram:
    def __init__(
        self,
        name: str,
        help_text: str,
        buckets: tuple[float, ...],
    ) -> None:
        self.name = name
        self.help_text = help_text
        self.buckets = buckets
        self.bucket_counts = [0] * (len(buckets) + 1)
        self.count = 0
        self.sum = 0.0

    def observe(self, value: float) -> None:
        numeric_value = max(0.0, float(value))
        self.count += 1
        self.sum += numeric_value
        for index, boundary in enumerate(self.buckets):
            if numeric_value <= boundary:
                self.bucket_counts[index] += 1
                break
        else:
            self.bucket_counts[-1] += 1

    def render(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} histogram",
        ]
        cumulative = 0
        for boundary, bucket_count in zip(
            (*self.buckets, math.inf),
            self.bucket_counts,
            strict=True,
        ):
            cumulative += bucket_count
            label = "+Inf" if math.isinf(boundary) else format(boundary, "g")
            lines.append(f'{self.name}_bucket{{le="{label}"}} {cumulative}')
        lines.append(f"{self.name}_count {self.count}")
        lines.append(f"{self.name}_sum {self.sum}")
        return lines


@dataclass(frozen=True)
class _ResourceSnapshot:
    service_rss_bytes: int | None
    runner_rss_bytes: int | None
    tree_rss_bytes: int | None
    service_cpu_seconds: float | None
    runner_cpu_seconds: float | None
    tree_cpu_seconds: float | None


def _one_process_usage(pid: int | None, *, children: bool) -> tuple[int, float] | None:
    if pid is None:
        return None
    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)] if children else [root]
        seen: set[int] = set()
        rss_bytes = 0
        cpu_seconds = 0.0
        for process in processes:
            if process.pid in seen:
                continue
            seen.add(process.pid)
            try:
                rss_bytes += int(process.memory_info().rss)
                cpu = process.cpu_times()
                cpu_seconds += float(cpu.user) + float(cpu.system)
            except (psutil.Error, OSError):
                continue
        return rss_bytes, cpu_seconds
    except (psutil.Error, OSError, ValueError):
        return None


def _resource_snapshot(runner_pid: int | None) -> _ResourceSnapshot:
    service = _one_process_usage(os.getpid(), children=False)
    runner = _one_process_usage(runner_pid, children=True)
    tree = _one_process_usage(os.getpid(), children=True)
    return _ResourceSnapshot(
        service_rss_bytes=service[0] if service is not None else None,
        runner_rss_bytes=runner[0] if runner is not None else None,
        tree_rss_bytes=tree[0] if tree is not None else None,
        service_cpu_seconds=service[1] if service is not None else None,
        runner_cpu_seconds=runner[1] if runner is not None else None,
        tree_cpu_seconds=tree[1] if tree is not None else None,
    )


class ServiceMetrics:
    def __init__(self, model_version: str) -> None:
        self.requests = _LabeledCounter(
            "imu_cloud_inference_requests_total",
            "Completed inference requests by outcome and request type.",
        )
        buckets = (
            0.0001,
            0.00025,
            0.0005,
            0.001,
            0.0025,
            0.005,
            0.01,
            0.025,
            0.05,
            0.1,
            0.25,
            0.5,
            1.0,
        )
        self.inference_seconds = _Histogram(
            "imu_cloud_inference_seconds",
            "Time spent in Edge Impulse DSP and classifier execution.",
            buckets,
        )
        self.queue_seconds = _Histogram(
            "imu_cloud_queue_seconds",
            "Time spent waiting for the serialized model runner.",
            buckets,
        )
        self.request_seconds = _Histogram(
            "imu_cloud_request_seconds",
            "Inference endpoint processing time, including runner queue time.",
            buckets,
        )
        self.request_cpu_seconds = _Histogram(
            "imu_cloud_request_cpu_seconds",
            "Process-tree CPU time consumed while handling an inference request.",
            buckets,
        )
        byte_buckets = (128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768)
        self.request_body_bytes = _Histogram(
            "imu_cloud_request_body_bytes",
            "Inference HTTP request body size in bytes.",
            byte_buckets,
        )
        self.response_body_bytes = _Histogram(
            "imu_cloud_response_body_bytes",
            "Inference HTTP response body size in bytes.",
            byte_buckets,
        )
        self.http_requests = _SingleLabelCounter(
            "imu_cloud_inference_http_requests_total",
            "Inference HTTP requests by response status class.",
            "status_class",
        )
        self.process_rss = _LabeledGauge(
            "imu_cloud_process_resident_memory_bytes",
            "Resident memory observed for service, runner, and combined process tree.",
            "scope",
        )
        self.process_peak_rss = _LabeledGauge(
            "imu_cloud_process_peak_resident_memory_bytes",
            "Maximum resident memory observed by this service process.",
            "scope",
        )
        self.process_cpu = _LabeledGauge(
            "imu_cloud_process_cpu_seconds",
            "Cumulative CPU time currently visible for each process scope.",
            "scope",
        )
        self.runner_restarts = _Gauge(
            "imu_cloud_runner_restarts_total",
            "Native runner restarts since service process startup.",
            metric_type="counter",
        )
        self._resource_peaks = {"service": 0, "runner": 0, "tree": 0}
        self.in_progress = _Gauge(
            "imu_cloud_inferences_in_progress",
            "Inference requests currently being handled.",
        )
        self.runner_up = _Gauge(
            "imu_cloud_runner_up",
            "Whether the native inference process is available.",
        )
        self.startup_seconds = _Gauge(
            "imu_cloud_startup_seconds",
            "Native runner startup and warm-up duration.",
        )
        self.model_info = (
            "# HELP imu_cloud_model_info Static deployed model information.\n"
            "# TYPE imu_cloud_model_info gauge\n"
            f'imu_cloud_model_info{{project_id="{PROJECT_ID}",'
            f'deployment_version="{DEPLOYMENT_VERSION}",'
            f'model_version="{_escape_prometheus_label(model_version)}"}} 1.0'
        )

    def sample_resources(self, runner_pid: int | None) -> _ResourceSnapshot:
        snapshot = _resource_snapshot(runner_pid)
        values = {
            "service": (snapshot.service_rss_bytes, snapshot.service_cpu_seconds),
            "runner": (snapshot.runner_rss_bytes, snapshot.runner_cpu_seconds),
            "tree": (snapshot.tree_rss_bytes, snapshot.tree_cpu_seconds),
        }
        for scope, (rss_bytes, cpu_seconds) in values.items():
            if rss_bytes is not None:
                self._resource_peaks[scope] = max(self._resource_peaks[scope], rss_bytes)
                self.process_rss.set(scope, rss_bytes)
                self.process_peak_rss.set(scope, self._resource_peaks[scope])
            if cpu_seconds is not None:
                self.process_cpu.set(scope, cpu_seconds)
        return snapshot

    def resource_payload(
        self,
        before: _ResourceSnapshot,
        after: _ResourceSnapshot,
    ) -> dict[str, int | None]:
        request_cpu_us = None
        if before.tree_cpu_seconds is not None and after.tree_cpu_seconds is not None:
            request_cpu_us = max(
                0,
                round((after.tree_cpu_seconds - before.tree_cpu_seconds) * 1_000_000),
            )
            self.request_cpu_seconds.observe(request_cpu_us / 1_000_000)
        return {
            "service_rss_bytes": after.service_rss_bytes,
            "runner_rss_bytes": after.runner_rss_bytes,
            "process_tree_rss_bytes": after.tree_rss_bytes,
            "process_tree_peak_rss_bytes": self._resource_peaks["tree"] or None,
            "request_cpu_us": request_cpu_us,
        }

    def observe_http(
        self,
        status_code: int,
        request_bytes: int | None,
        response_bytes: int | None,
    ) -> None:
        self.http_requests.inc(f"{status_code // 100}xx")
        if request_bytes is not None:
            self.request_body_bytes.observe(request_bytes)
        if response_bytes is not None:
            self.response_body_bytes.observe(response_bytes)

    def render(self) -> bytes:
        lines: list[str] = []
        lines.extend(self.requests.render())
        lines.extend(self.inference_seconds.render())
        lines.extend(self.queue_seconds.render())
        lines.extend(self.request_seconds.render())
        lines.extend(self.request_cpu_seconds.render())
        lines.extend(self.request_body_bytes.render())
        lines.extend(self.response_body_bytes.render())
        lines.extend(self.http_requests.render())
        lines.extend(self.process_rss.render())
        lines.extend(self.process_peak_rss.render())
        lines.extend(self.process_cpu.render())
        lines.extend(self.runner_restarts.render())
        lines.extend(self.in_progress.render())
        lines.extend(self.runner_up.render())
        lines.extend(self.startup_seconds.render())
        lines.append(self.model_info)
        return ("\n".join(lines) + "\n").encode("utf-8")


def create_app(
    settings: Settings | None = None,
    runner: EdgeImpulseRunner | None = None,
) -> FastAPI:
    service_settings = settings or Settings.from_environment()
    model_runner = runner or EdgeImpulseRunner(
        service_settings.runner_path,
        timeout_s=service_settings.runner_timeout_s,
    )
    metrics = ServiceMetrics(service_settings.model_version)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        service_settings.validate()
        started_ns = time.perf_counter_ns()
        try:
            await model_runner.start()
            await model_runner.warm_up()
        except BaseException:
            metrics.runner_up.set(0)
            await model_runner.stop()
            raise
        metrics.startup_seconds.set(
            max(0.0, (time.perf_counter_ns() - started_ns) / 1_000_000_000)
        )
        metrics.runner_up.set(1)
        application.state.ready = True
        try:
            yield
        finally:
            application.state.ready = False
            metrics.runner_up.set(0)
            await model_runner.stop()

    application = FastAPI(
        title="IMU Rehabilitation Cloud Inference",
        version=service_settings.model_version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.ready = False
    application.state.settings = service_settings
    application.state.runner = model_runner
    application.state.metrics = metrics

    @application.middleware("http")
    async def protect_and_limit(request: Request, call_next: Any) -> Response:
        inference_request = request.url.path == "/v1/infer"
        request_bytes: int | None = None
        raw_length = request.headers.get("content-length")
        if inference_request and raw_length:
            try:
                request_bytes = max(0, int(raw_length))
            except ValueError:
                request_bytes = None

        def finish(response: Response) -> Response:
            if inference_request:
                raw_response_length = response.headers.get("content-length")
                try:
                    response_bytes = (
                        max(0, int(raw_response_length))
                        if raw_response_length is not None
                        else None
                    )
                except ValueError:
                    response_bytes = None
                metrics.observe_http(response.status_code, request_bytes, response_bytes)
            return response

        if request.url.path in PROTECTED_PATHS:
            if not service_settings.allow_unauthenticated:
                header = request.headers.get("authorization", "")
                scheme, separator, token = header.partition(" ")
                authenticated = (
                    bool(separator)
                    and scheme.lower() == "bearer"
                    and bool(service_settings.api_key)
                    and secrets.compare_digest(token, service_settings.api_key)
                )
                if not authenticated:
                    return finish(JSONResponse(
                        status_code=401,
                        content={"ok": False, "error": "Unauthorized."},
                        headers={"WWW-Authenticate": "Bearer"},
                    ))
        if inference_request:
            if raw_length:
                try:
                    too_large = int(raw_length) > service_settings.max_body_bytes
                except ValueError:
                    too_large = True
                if too_large:
                    return finish(JSONResponse(
                        status_code=413,
                        content={"ok": False, "error": "Request body is too large."},
                    ))
        try:
            response = await call_next(request)
        except BaseException:
            if inference_request:
                metrics.observe_http(500, request_bytes, None)
            raise
        return finish(response)

    @application.get("/")
    async def service_info() -> dict[str, Any]:
        return {
            "ok": True,
            "service": "imu-rehabilitation-cloud-inference",
            "model_version": service_settings.model_version,
            "project_id": int(PROJECT_ID),
            "deployment_version": int(DEPLOYMENT_VERSION),
            "feature_count": FEATURE_COUNT,
            "labels": list(MODEL_LABELS),
            "endpoints": {
                "inference": "/v1/infer",
                "liveness": "/healthz",
                "readiness": "/readyz",
                "metrics": "/metrics",
            },
        }

    @application.get("/healthz")
    async def liveness() -> Response:
        live = bool(application.state.ready and model_runner.is_running)
        return JSONResponse(
            status_code=200 if live else 503,
            content={"ok": live},
        )

    @application.get("/readyz")
    async def readiness() -> Response:
        ready = bool(application.state.ready and model_runner.is_running)
        metrics.runner_up.set(1 if ready else 0)
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"ok": ready, "model_version": service_settings.model_version},
        )

    @application.get("/metrics")
    async def prometheus_metrics() -> Response:
        metrics.sample_resources(getattr(model_runner, "pid", None))
        metrics.runner_restarts.set(getattr(model_runner, "restart_count", 0))
        return Response(
            content=metrics.render(),
            media_type=PROMETHEUS_CONTENT_TYPE,
        )

    @application.post("/v1/infer")
    async def infer(payload: InferenceRequest) -> dict[str, Any]:
        request_started_ns = time.perf_counter_ns()
        warmup_label = "true" if payload.warmup else "false"
        metrics.in_progress.inc()
        before_resources = metrics.sample_resources(getattr(model_runner, "pid", None))
        resource_usage: dict[str, int | None]
        try:
            result = await model_runner.classify(payload.window_id, payload.features)
        except RunnerError as error:
            application.state.ready = False
            metrics.runner_up.set(1 if model_runner.is_running else 0)
            metrics.requests.inc(outcome="error", warmup=warmup_label)
            raise HTTPException(status_code=503, detail=str(error)) from error
        finally:
            metrics.in_progress.dec()
            after_resources = metrics.sample_resources(getattr(model_runner, "pid", None))
            resource_usage = metrics.resource_payload(before_resources, after_resources)
            metrics.runner_restarts.set(getattr(model_runner, "restart_count", 0))
            server_us = max(
                0,
                (time.perf_counter_ns() - request_started_ns) // 1000,
            )
            metrics.request_seconds.observe(server_us / 1_000_000)

        application.state.ready = True
        metrics.runner_up.set(1)
        metrics.requests.inc(outcome="success", warmup=warmup_label)
        metrics.inference_seconds.observe(result.inference_us / 1_000_000)
        metrics.queue_seconds.observe(result.queue_us / 1_000_000)
        return {
            "ok": True,
            "version": 1,
            "window_id": payload.window_id,
            "scores": dict(zip(MODEL_LABELS, result.scores, strict=True)),
            "inference_us": result.inference_us,
            "model_version": service_settings.model_version,
            "timing_us": {
                "queue": result.queue_us,
                "inference": result.inference_us,
                "server": server_us,
            },
            "resource_usage": resource_usage,
        }

    return application


app = create_app()

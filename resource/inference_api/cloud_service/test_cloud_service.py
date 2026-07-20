from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

import httpx

from resource.inference_api.cloud_service.app import (
    MODEL_LABELS,
    InferenceRequest,
    Settings,
    create_app,
)
from resource.inference_api.cloud_service.runner import (
    FEATURE_COUNT,
    EdgeImpulseRunner,
    RunnerResult,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
WINDOWS_RUNNER = (
    REPO_ROOT
    / "resource"
    / "inference_api"
    / "edge_runner"
    / "build"
    / "edge_inference_runner.exe"
)


class FakeRunner:
    def __init__(self) -> None:
        self.is_running = False
        self.call_count = 0

    async def start(self) -> None:
        self.is_running = True

    async def warm_up(self) -> RunnerResult:
        return await self.classify(0, [0.0] * FEATURE_COUNT)

    async def classify(self, window_id: int, features: list[float]) -> RunnerResult:
        self.call_count += 1
        return RunnerResult(
            scores=(0.05, 0.80, 0.05, 0.04, 0.03, 0.03),
            inference_us=1200,
            queue_us=25,
        )

    async def stop(self) -> None:
        self.is_running = False


def test_settings() -> Settings:
    return Settings(
        runner_path=Path("unused"),
        runner_timeout_s=1.0,
        model_version="test-model",
        api_key="test-secret-value",
        allow_unauthenticated=False,
        max_body_bytes=32768,
    )


def request_payload(window_id: int = 42) -> dict[str, object]:
    return {
        "version": 1,
        "window_id": window_id,
        "feature_count": FEATURE_COUNT,
        "features": [0.0] * FEATURE_COUNT,
        "labels": list(MODEL_LABELS),
        "warmup": False,
    }


class ContractTests(unittest.TestCase):
    def test_contract_accepts_the_model_shape(self) -> None:
        request = InferenceRequest.model_validate(request_payload())
        self.assertEqual(request.window_id, 42)
        self.assertEqual(len(request.features), FEATURE_COUNT)

    def test_contract_rejects_a_different_label_order(self) -> None:
        payload = request_payload()
        payload["labels"] = list(reversed(MODEL_LABELS))
        with self.assertRaises(ValueError):
            InferenceRequest.model_validate(payload)

    def test_authentication_is_required_by_default(self) -> None:
        settings = test_settings()
        settings = Settings(**{**settings.__dict__, "api_key": ""})
        with self.assertRaises(RuntimeError):
            settings.validate()


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.runner = FakeRunner()
        self.app = create_app(test_settings(), self.runner)  # type: ignore[arg-type]
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)

    async def test_health_is_public_but_inference_requires_a_bearer_key(self) -> None:
        health = await self.client.get("/healthz")
        denied = await self.client.post("/v1/infer", json=request_payload())
        self.assertEqual(health.status_code, 200)
        self.assertEqual(denied.status_code, 401)

    async def test_inference_and_prometheus_metrics(self) -> None:
        headers = {"Authorization": "Bearer test-secret-value"}
        response = await self.client.post(
            "/v1/infer",
            headers=headers,
            json=request_payload(),
        )
        metrics = await self.client.get("/metrics", headers=headers)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["window_id"], 42)
        self.assertEqual(body["inference_us"], 1200)
        self.assertEqual(list(body["scores"]), list(MODEL_LABELS))
        self.assertEqual(body["timing_us"]["queue"], 25)
        self.assertIn("imu_cloud_inference_seconds_count 1", metrics.text)
        self.assertIn(
            'imu_cloud_inference_requests_total{outcome="success",warmup="false"} 1.0',
            metrics.text,
        )

    async def test_readiness_tracks_the_runner(self) -> None:
        response = await self.client.get("/readyz")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])


@unittest.skipUnless(WINDOWS_RUNNER.is_file(), "local native runner is not built")
class NativeRunnerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_model_returns_six_finite_scores(self) -> None:
        runner = EdgeImpulseRunner(WINDOWS_RUNNER, timeout_s=30.0)
        try:
            result = await runner.classify(7, [0.0] * FEATURE_COUNT)
        finally:
            await runner.stop()
        self.assertEqual(len(result.scores), 6)
        self.assertGreater(result.inference_us, 0)
        self.assertGreater(sum(result.scores), 0.9)
        self.assertLess(sum(result.scores), 1.1)

    async def test_real_model_through_the_http_contract(self) -> None:
        settings = Settings(
            runner_path=WINDOWS_RUNNER,
            runner_timeout_s=30.0,
            model_version="integration-model",
            api_key="integration-test-secret",
            allow_unauthenticated=False,
            max_body_bytes=32768,
        )
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                response = await client.post(
                    "/v1/infer",
                    headers={"Authorization": "Bearer integration-test-secret"},
                    json=request_payload(99),
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["window_id"], 99)
        self.assertGreater(response.json()["inference_us"], 0)


if __name__ == "__main__":
    unittest.main()

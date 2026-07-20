# Cloud inference container

This OCI image serves the real Edge Impulse project `738400`, deployment `19`,
through the REST contract already used by the PC coordinator. The model archive
is validated and compiled during the image build. The runtime starts the native
runner once, warms it before becoming ready, and keeps it alive between requests.

## Build and run locally

Run Docker commands from the repository root because the build needs the model
archive and native runner sources:

```powershell
docker build `
  --file resource/inference_api/cloud_service/Dockerfile `
  --tag imu-rehab-cloud-inference:deployment-19 `
  .

$env:API_KEY = [guid]::NewGuid().ToString("N")
docker run --rm --name imu-rehab-inference `
  --publish 8080:8080 `
  --env "API_KEY=$env:API_KEY" `
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m `
  --cap-drop ALL --security-opt no-new-privileges `
  imu-rehab-cloud-inference:deployment-19
```

Alternatively, set `API_KEY` in the shell and use Compose:

```powershell
$env:API_KEY = [guid]::NewGuid().ToString("N")
docker compose --file resource/inference_api/cloud_service/compose.yaml up --build
```

`GET /healthz` and `GET /readyz` are intentionally public for platform probes.
`POST /v1/infer` and `GET /metrics` require `Authorization: Bearer <API_KEY>`;
the key must contain at least 16 characters.
For an isolated local test only, authentication can be disabled with
`ALLOW_UNAUTHENTICATED=true`.

## Connect the coordinator

In the PC dashboard, select **Cloud REST model** and configure:

- model URL: `https://<cloud-host>/v1/infer`;
- API key: the value injected into the container;
- model version: `ei-738400-deployment-19`.

The service requires exactly 198 finite float features, the version-1 contract,
and the six labels in model order. It rejects unknown JSON fields and bodies over
32 KiB. The normal response includes the coordinator fields plus timing detail:

```json
{
  "ok": true,
  "version": 1,
  "window_id": 42,
  "scores": {
    "Extension": 0.05,
    "Flexion": 0.80,
    "Pronation": 0.05,
    "Radial Deviation": 0.04,
    "Supination": 0.03,
    "Ulnar Deviation": 0.03
  },
  "inference_us": 1200,
  "model_version": "ei-738400-deployment-19",
  "timing_us": {"queue": 10, "inference": 1200, "server": 1300}
}
```

`inference_us` measures native DSP plus classifier execution. `queue` is time
waiting for the single model process, and `server` includes both. The PC client
independently measures the HTTP wall time, so network overhead is the difference
between its wall time and the reported server time.

## Cloud deployment

Push the built image to the chosen registry and configure the cloud service with:

- container port `8080` (or inject the platform's `PORT` value);
- `API_KEY` from the platform's secret manager, never in the image;
- HTTPS at the managed ingress or load balancer;
- `/healthz` for liveness and `/readyz` for readiness;
- one vCPU, 512 MiB memory, and request concurrency `1` as a starting point.

The runner is deliberately serialized so concurrent work is visible as queue
latency. Scale horizontally for load testing. For steady-state latency tests,
keep at least one instance warm; benchmark cold-start behavior in a separate run.
The included `kubernetes.yaml` provides hardened probes, resources, and security
context. Replace its image and create its secret before applying it:

```text
kubectl create secret generic imu-rehab-inference --from-literal=api-key=<secret>
kubectl apply -f resource/inference_api/cloud_service/kubernetes.yaml
```

Confirm that the Edge Impulse plan attached to this exported model permits the
intended cloud deployment before publishing the image.

## Metrics

Prometheus text metrics are available at `/metrics` with the bearer key:

- `imu_cloud_inference_seconds`: native DSP and classifier latency;
- `imu_cloud_queue_seconds`: serialized-runner wait time;
- `imu_cloud_request_seconds`: server-side request time;
- `imu_cloud_inference_requests_total`: success/error counts, split by warm-up;
- `imu_cloud_inferences_in_progress` and `imu_cloud_runner_up`;
- `imu_cloud_startup_seconds` and `imu_cloud_model_info`.

Use histogram rates to calculate a p95, for example:

```promql
histogram_quantile(0.95,
  sum by (le) (rate(imu_cloud_inference_seconds_bucket[5m])))
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_KEY` | none | Required bearer secret unless local unauthenticated mode is enabled. |
| `ALLOW_UNAUTHENTICATED` | `false` | Disable authentication only on an isolated test network. |
| `PORT` | `8080` | HTTP listen port. |
| `MODEL_VERSION` | `ei-738400-deployment-19` | Version returned in results and metric labels. |
| `RUNNER_TIMEOUT_S` | `10` | Native runner response timeout. |
| `MAX_BODY_BYTES` | `32768` | Maximum accepted inference request size. |
| `MODEL_RUNNER_PATH` | `/usr/local/bin/edge_inference_runner` | Native binary path. |

## Verification

The Docker build runs a real zero-window inference before producing the runtime
image. Local API and native-runner tests can also be run from the repository root:

```powershell
python -m pip install -r resource/inference_api/cloud_service/requirements-dev.txt
python -m unittest resource.inference_api.cloud_service.test_cloud_service -v
```

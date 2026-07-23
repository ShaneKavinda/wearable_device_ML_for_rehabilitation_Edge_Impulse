# REST inference container for cloud or LAN edge deployment

This OCI image serves the real Edge Impulse project `738400`, deployment `19`,
through the REST contract already used by the PC coordinator. The model archive
is validated and compiled during the image build. The runtime starts the native
runner once, warms it before becoming ready, and keeps it alive between requests.

The same image can run in Rahti, on a cPouta VM, or on a separate Ubuntu Server
inside the local network. On Ubuntu it represents a remote edge-inference node:
the PC still captures the IMU window, while DSP and classification execute on
the Ubuntu device.

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

## Deploy on an Ubuntu Server edge device

Use a physically separate Ubuntu machine, mini-PC, or SBC for a meaningful edge
comparison. An Ubuntu VM running on the coordinator PC does not isolate compute
or network effects in the same way.

### 1. Check the server and repository

Docker Engine and the Docker Compose plugin must be installed. Confirm both are
available:

```bash
docker --version
docker-compose version
```

If either command is missing, install Docker Engine using the
[official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/).
Adding a user to the `docker` group grants root-equivalent control of Docker;
follow the server administrator's security policy.

Move to the root of the repository already cloned on the Ubuntu server and
confirm that the Dockerfile and exported model are present:

```bash
cd "<REPOSITORY_ROOT>"
test -f resource/inference_api/cloud_service/Dockerfile
test -f resource/exported_model/ei_gesture_left_hand_imu_arduino.zip
```

The Docker build compiles a Linux native runner for the server's architecture
and runs a real inference before producing the runtime image. Build on the edge
device itself, especially for ARM64. Do not deploy the image if that smoke test
fails.

### 2. Give the server a stable LAN address

Use a DHCP reservation or static address so the PC coordinator does not lose
the endpoint between experiments. Display the server addresses with:

```bash
hostname -I
```

Choose the address on the same trusted LAN as the coordinator PC. The examples
below call it `<EDGE_LAN_IP>`.

### 3. Create the protected Compose environment

Move to the service directory, generate a bearer key, and bind the published
port only to the chosen LAN interface:

```bash
cd "<REPOSITORY_ROOT>/resource/inference_api/cloud_service"

EDGE_LAN_IP="<EDGE_LAN_IP>"
API_KEY="$(openssl rand -hex 32)"
umask 077
printf 'API_KEY=%s\nHOST_PORT=%s:8080\n' "$API_KEY" "$EDGE_LAN_IP" > .env
printf 'Save this API key securely: %s\n' "$API_KEY"
unset API_KEY EDGE_LAN_IP
```

The repository ignores `.env`, but it still contains a secret. Keep it readable
only by the deployment administrator and store the displayed API key in a
password manager.

`HOST_PORT=<EDGE_LAN_IP>:8080` avoids listening on every server interface. Do
not add an Internet-router port-forward for port `8080`.

### 4. Build and start the edge service

Validate the Compose service name without printing the expanded environment,
then build and start it:

```bash
docker compose --env-file .env -f compose.yaml config --services
docker compose --env-file .env -f compose.yaml up -d --build
```

The supplied Compose configuration runs one persistent inference process with a
read-only filesystem, dropped Linux capabilities, a 64-process limit, one CPU,
and 512 MiB of memory. Inspect the result:

```bash
docker compose --env-file .env -f compose.yaml ps
docker compose --env-file .env -f compose.yaml logs --tail=200 inference
```

### 5. Verify the LAN endpoint

On the Ubuntu server, check readiness and authenticated metrics:

```bash
EDGE_LAN_IP="$(sed -n 's/^HOST_PORT=\(.*\):8080$/\1/p' .env)"
API_KEY="$(sed -n 's/^API_KEY=//p' .env)"

curl --fail --silent --show-error \
  "http://$EDGE_LAN_IP:8080/readyz"
curl --fail --silent --show-error \
  -H "Authorization: Bearer $API_KEY" \
  "http://$EDGE_LAN_IP:8080/metrics" | grep '^imu_cloud_'

unset API_KEY EDGE_LAN_IP
```

From the coordinator PC, first confirm that the server is reachable:

```powershell
Test-NetConnection -ComputerName "<EDGE_LAN_IP>" -Port 8080
Invoke-RestMethod "http://<EDGE_LAN_IP>:8080/readyz"
```

If the connection fails, check the server address, LAN/VLAN isolation, Docker
port binding, and the host firewall. Docker-published ports can bypass some UFW
rules, so do not rely on UFW alone to protect an Internet-facing host.

### 6. Connect the PC coordinator

Open the PC dashboard and configure:

```text
Model deployment: Remote edge REST model
Model REST URL:   http://<EDGE_LAN_IP>:8080/v1/infer
Model API key:    <THE_SAVED_API_KEY>
Model timeout:    10
Model version:    ei-738400-deployment-19
```

Save the configuration and connect the IMU source and model backend. Selecting
`Remote edge REST model` records deployment ID `1` and labels benchmark rows as
`edge`.

The direct LAN example uses HTTP. Although bearer authentication prevents an
unauthenticated request, HTTP does not encrypt the API key or IMU features. Use
an isolated trusted test LAN; for a shared or untrusted network, put the service
behind TLS or a VPN such as WireGuard.

For comparable experiments, record the server hardware, operating system,
Ethernet or Wi-Fi profile, CPU and memory limits, concurrency, and run type in
the dashboard experiment profile. Run local PC, LAN edge, and Rahti tests with
the same captured inputs and separate cold-start from steady-state runs.

### 7. Update, restart, or stop the edge service

To deploy a later repository revision without changing the `.env` key or LAN
address:

```bash
cd "<REPOSITORY_ROOT>"
git pull --ff-only
cd resource/inference_api/cloud_service
docker compose --env-file .env -f compose.yaml up -d --build
docker compose --env-file .env -f compose.yaml ps
docker compose --env-file .env -f compose.yaml logs --tail=200 inference
```

Restart the existing container without rebuilding:

```bash
docker compose --env-file .env -f compose.yaml restart inference
```

Stop and remove the container and Compose network:

```bash
docker compose --env-file .env -f compose.yaml down
```

The service stores no application data in a Docker volume. Retain `.env` if the
same endpoint and bearer key will be used again, or delete it securely when the
edge deployment is permanently retired.

This topology measures remote edge inference, not a fully autonomous edge
sensor. BLE acquisition and capture coordination still run on the PC. A fully
sensor-side architecture would also move IMU acquisition and window assembly to
the Ubuntu device or embedded hardware.

## Connect the coordinator

For a cloud deployment, select **Cloud REST model** in the PC dashboard and
configure:

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
  "timing_us": {"queue": 10, "inference": 1200, "server": 1300},
  "resource_usage": {
    "service_rss_bytes": 51000000,
    "runner_rss_bytes": 9000000,
    "process_tree_rss_bytes": 60000000,
    "process_tree_peak_rss_bytes": 61000000,
    "request_cpu_us": 1400
  }
}
```

`inference_us` measures native DSP plus classifier execution. `queue` is time
waiting for the single model process, and `server` includes both. The PC client
independently records HTTP wall time. It reports `max(0, HTTP wall - server)` as
a **transport residual**, which includes network, TLS, proxy, serialization,
and client scheduling effects and is not pure network latency. Resource
telemetry is optional and never makes inference fail.

## Cloud deployment

For CSC infrastructure, follow the complete
[Rahti and cPouta deployment guide](CSC_RAHTI_CPOUTA_DEPLOYMENT.md). If a build
or rollout fails, check the [cloud deployment troubleshooting FAQ](FAQ.md).

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
- `imu_cloud_inference_http_requests_total`, split by HTTP status class;
- `imu_cloud_request_body_bytes` and `imu_cloud_response_body_bytes`;
- `imu_cloud_request_cpu_seconds`;
- `imu_cloud_process_resident_memory_bytes`,
  `imu_cloud_process_peak_resident_memory_bytes`, and
  `imu_cloud_process_cpu_seconds`, split by service/runner/tree scope; and
- `imu_cloud_runner_restarts_total`.

The supplied Kubernetes manifest includes Prometheus pod annotations. `/metrics`
still requires the bearer key: configure Prometheus with the existing
`imu-rehab-inference` Secret rather than making metrics public.

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

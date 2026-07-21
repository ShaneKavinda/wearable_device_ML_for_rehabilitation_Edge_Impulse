# Deploy the IMU inference service on CSC Rahti or cPouta

This guide deploys the repository's Edge Impulse inference API to either:

- **Rahti**, CSC's managed OpenShift service; or
- **cPouta**, CSC's OpenStack service, using an Ubuntu VM, Docker Compose, and
  Caddy for HTTPS.

The instructions were checked against CSC's official documentation on
2026-07-20. Rahti is the shorter managed path and is recommended for the first
cloud benchmark. cPouta is useful when VM-level control or a second cloud target
is required.

> **Data protection:** These instructions are for de-identified test data. IMU
> measurements tied to a participant can be health or personal data. Do not send
> identifiable or sensitive study data to either deployment until the project's
> data-protection owner has approved the architecture. CSC positions ePouta and
> Sensitive Data services for sensitive workloads; cPouta is a public-cloud
> service.

## 1. What will be deployed

The container is built from:

```text
resource/inference_api/cloud_service/Dockerfile
```

It compiles and packages the real Edge Impulse deployment from:

```text
resource/exported_model/ei_gesture_left_hand_imu_arduino.zip
```

The service exposes:

| Endpoint | Authentication | Purpose |
| --- | --- | --- |
| `GET /healthz` | None | Liveness probe |
| `GET /readyz` | None | Readiness probe |
| `POST /v1/infer` | Bearer API key | Run inference |
| `GET /metrics` | Bearer API key | Prometheus-format metrics |

The API listens on container port `8080`. Both deployments terminate TLS before
traffic reaches that port. Never expose port `8080` directly to the Internet.

The examples use one replica, one vCPU, and 512 MiB RAM so the Rahti and cPouta
measurements start from approximately the same container limits. The native
runner serializes inference requests, making queue time visible in the metrics.

## 2. Prerequisites common to both platforms

1. Sign in to [My CSC](https://my.csc.fi/) with a CSC account.
2. Confirm that the correct CSC computing project is active and that you know
   its project number.
3. Confirm that the Edge Impulse plan and model-export licence permit the
   intended cloud use.
4. Confirm that the model archive exists locally:

   ```powershell
   Test-Path resource/exported_model/ei_gesture_left_hand_imu_arduino.zip
   ```

   Run this and all later local PowerShell commands from the repository root.
5. Decide on a long random API key. The examples generate 64 hexadecimal
   characters. Store it in a password manager; do not commit it to Git.

Choose one deployment path below. It is not necessary to deploy both.

---

## 3. Option A — deploy to Rahti OpenShift

### 3.1 Enable Rahti for the CSC project

1. In My CSC, open **My Projects** and select the computing project.
2. Open the **Rahti** service section, accept the terms, and apply for access.
3. Wait for the access change to propagate. CSC notes that this can take up to
   30 minutes.
4. Open [rahti.csc.fi](https://rahti.csc.fi/) and sign in. Complete MFA when
   prompted.

See CSC's [Rahti access instructions](https://docs.csc.fi/cloud/rahti/access/)
and [getting-started page](https://docs.csc.fi/cloud/rahti/usage/getting_started/).

### 3.2 Create a Rahti project (namespace)

A Rahti/OpenShift project is a namespace and is different from the CSC computing
project.

1. In the Rahti web console, switch to the **Developer** perspective.
2. Select **Create Project**.
3. Enter a globally unique name, for example `imu-rehab-<team-name>`.
4. In **Description**, enter the CSC project association exactly in this form:

   ```text
   csc_project: <CSC_PROJECT_NUMBER>
   ```

5. Create the project.

The default Rahti quota is currently 4 CPU cores, 16 GiB RAM, and 100 GiB
storage. The resource requests and limits below fit the default quota and
Rahti's maximum 5:1 limit-to-request ratio. See
[Projects and quota](https://docs.csc.fi/cloud/rahti/usage/projects_and_quota/).

### 3.3 Install and authenticate the OpenShift CLI

1. In the Rahti web console, open **Help → Command Line Tools** and install the
   `oc` binary for the local operating system.
2. Open the user menu and select **Copy login command**.
3. Copy and run the generated `oc login` command in PowerShell. It contains a
   short-lived token, so do not save it in the repository or documentation.
4. Set the values used by the remaining commands:

   ```powershell
   $RahtiProject = "<UNIQUE_RAHTI_PROJECT_NAME>"
   $AppName = "imu-rehab-inference"
   $ImageName = "imu-rehab-cloud-inference"
   $ImageTag = "deployment-19"

   oc project $RahtiProject
   oc whoami
   oc status
   ```

CSC's [Rahti CLI instructions](https://docs.csc.fi/cloud/rahti/usage/cli/)
describe the same login flow.

### 3.4 Build the image in Rahti

The recommended path uploads the repository as a binary OpenShift build. It does
not require Docker on the local computer. The repository `.dockerignore` keeps
the image build context focused while explicitly including the required model
ZIP.

Create the ImageStream:

```powershell
oc create imagestream $ImageName --dry-run=client -o yaml | oc apply -f -
```

Create or update a BuildConfig. Run this from the repository root:

```powershell
@"
apiVersion: build.openshift.io/v1
kind: BuildConfig
metadata:
  name: $ImageName
spec:
  source:
    type: Binary
  strategy:
    type: Docker
    dockerStrategy:
      dockerfilePath: resource/inference_api/cloud_service/Dockerfile
  output:
    to:
      kind: ImageStreamTag
      name: ${ImageName}:$ImageTag
  resources:
    requests:
      cpu: 500m
      memory: 1Gi
    limits:
      cpu: "2"
      memory: 4Gi
"@ | oc apply -f -
```

Start the build and follow its logs:

```powershell
oc start-build $ImageName --from-dir=. --follow
oc get builds
oc get imagestream $ImageName
```

The Docker build performs a real zero-window inference before it produces the
runtime image. Do not continue if that validation fails. If the build is killed
for memory pressure, check the project quota and temporarily raise the build
memory request and limit while retaining a limit/request ratio of no more than
5.

This follows CSC's documented
[binary Docker build](https://docs.csc.fi/cloud/rahti/images/creating/) and
[integrated registry](https://docs.csc.fi/cloud/rahti/images/Using_Rahti_integrated_registry/)
workflows.

#### Alternative: build locally and push

Use this only if a local Docker/Podman build is preferable:

```powershell
$ExternalRegistry = "image-registry.apps.2.rahti.csc.fi"
$ExternalImage = "$ExternalRegistry/$RahtiProject/${ImageName}:$ImageTag"

docker build `
  --file resource/inference_api/cloud_service/Dockerfile `
  --tag $ExternalImage `
  .

$RahtiToken = oc whoami -t
$RahtiToken | docker login $ExternalRegistry --username unused --password-stdin
Remove-Variable RahtiToken

oc create imagestream $ImageName --dry-run=client -o yaml | oc apply -f -
docker push $ExternalImage
```

The rest of the guide uses the ImageStream regardless of which build method was
chosen.

### 3.5 Create the API-key Secret

Generate the key locally, save it in a password manager, and create or update the
OpenShift Secret:

```powershell
$ApiKey = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
oc create secret generic $AppName `
  --from-literal="api-key=$ApiKey" `
  --dry-run=client -o yaml | oc apply -f -
```

Keep `$ApiKey` in this PowerShell session through the verification steps. Do not
print the Secret YAML or commit the key.

### 3.6 Deploy the service

Rahti's restricted security policy assigns each container a non-root UID from
the namespace's allowed range. The manifest deliberately does **not** set
`runAsUser` or `runAsGroup`. The image and application support that arbitrary
UID, use a read-only root filesystem, and listen on the non-privileged port
`8080`.

Apply the Deployment and internal Service:

```powershell
@"
apiVersion: apps/v1
kind: Deployment
metadata:
  name: $AppName
  labels:
    app.kubernetes.io/name: $AppName
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: $AppName
  template:
    metadata:
      labels:
        app.kubernetes.io/name: $AppName
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/path: "/metrics"
        prometheus.io/port: "8080"
    spec:
      terminationGracePeriodSeconds: 15
      securityContext:
        runAsNonRoot: true
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: inference
          image: image-registry.openshift-image-registry.svc:5000/${RahtiProject}/${ImageName}:$ImageTag
          imagePullPolicy: Always
          ports:
            - name: http
              containerPort: 8080
          env:
            - name: API_KEY
              valueFrom:
                secretKeyRef:
                  name: $AppName
                  key: api-key
          readinessProbe:
            httpGet:
              path: /readyz
              port: http
            initialDelaySeconds: 2
            periodSeconds: 5
            timeoutSeconds: 2
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 15
            timeoutSeconds: 2
          resources:
            requests:
              cpu: 250m
              memory: 256Mi
            limits:
              cpu: "1"
              memory: 512Mi
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop:
                - ALL
            readOnlyRootFilesystem: true
---
apiVersion: v1
kind: Service
metadata:
  name: $AppName
spec:
  selector:
    app.kubernetes.io/name: $AppName
  ports:
    - name: http
      port: 80
      targetPort: http
"@ | oc apply -f -
```

Wait for the rollout and inspect the result:

```powershell
oc rollout status deployment/$AppName --timeout=5m
oc get pods -l "app.kubernetes.io/name=$AppName" -o wide
oc logs deployment/$AppName --tail=100
```

If the Pod does not become ready, use:

```powershell
oc describe pod -l "app.kubernetes.io/name=$AppName"
oc get events --sort-by=.lastTimestamp
```

### 3.7 Add an HTTPS Route

Rahti Routes expose HTTP/HTTPS services. Edge TLS termination uses Rahti's
certificate and forwards plain HTTP only inside the cluster:

```powershell
oc create route edge $AppName `
  --service=$AppName `
  --port=http `
  --insecure-policy=Redirect `
  --dry-run=client -o yaml | oc apply -f -

$RouteHost = oc get route $AppName -o "jsonpath={.spec.host}"
$BaseUrl = "https://$RouteHost"
$BaseUrl
```

The generated `*.2.rahtiapp.fi` hostname receives a valid TLS certificate. See
CSC's [Rahti networking and Routes](https://docs.csc.fi/cloud/rahti/networking/)
documentation.

For a small private test, optionally restrict the Route to the public IP used by
the coordinator PC. First obtain the PC's public IP, for example from CSC's
[My IP service](https://apps.csc.fi/myip), then run:

```powershell
$CoordinatorPublicIp = "<PUBLIC_IPV4_ADDRESS>"
oc annotate route $AppName `
  "haproxy.router.openshift.io/ip_allowlist=$CoordinatorPublicIp/32" `
  --overwrite
```

Skip the allowlist if the coordinator changes networks or if an external metrics
collector also needs access. The bearer key remains required either way.

### 3.8 Verify the Rahti deployment

Check the public probes:

```powershell
Invoke-RestMethod "$BaseUrl/healthz"
Invoke-RestMethod "$BaseUrl/readyz"
```

Create a contract-valid zero window and run warm-up requests:

```powershell
$Labels = @(
  "Extension",
  "Flexion",
  "Pronation",
  "Radial Deviation",
  "Supination",
  "Ulnar Deviation"
)
$Headers = @{ Authorization = "Bearer $ApiKey" }
$WarmupBody = @{
  version = 1
  window_id = 1
  feature_count = 198
  features = @(1..198 | ForEach-Object { 0.0 })
  labels = $Labels
  warmup = $true
} | ConvertTo-Json -Depth 4 -Compress

1..20 | ForEach-Object {
  Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/v1/infer" `
    -Headers $Headers `
    -ContentType "application/json" `
    -Body $WarmupBody | Out-Null
}
```

Run and display one measured inference:

```powershell
$MeasuredBody = @{
  version = 1
  window_id = 2
  feature_count = 198
  features = @(1..198 | ForEach-Object { 0.0 })
  labels = $Labels
  warmup = $false
} | ConvertTo-Json -Depth 4 -Compress

Invoke-RestMethod `
  -Method Post `
  -Uri "$BaseUrl/v1/infer" `
  -Headers $Headers `
  -ContentType "application/json" `
  -Body $MeasuredBody
```

Read the Prometheus metrics:

```powershell
$Metrics = Invoke-WebRequest -Uri "$BaseUrl/metrics" -Headers $Headers
$Metrics.Content
```

Configure the PC coordinator with:

```text
Model URL:     https://<RAHTI_ROUTE_HOST>/v1/infer
API key:       <THE_SAVED_API_KEY>
Model version: ei-738400-deployment-19
```

After saving the key, remove it from the PowerShell session:

```powershell
Remove-Variable ApiKey, Headers, WarmupBody, MeasuredBody -ErrorAction SilentlyContinue
```

### 3.9 Update, roll back, and remove the Rahti deployment

After changing code, rebuild the same ImageStream tag and restart the Deployment:

```powershell
oc start-build $ImageName --from-dir=. --follow
oc rollout restart deployment/$AppName
oc rollout status deployment/$AppName --timeout=5m
```

Roll back to the previous Deployment revision if needed:

```powershell
oc rollout history deployment/$AppName
oc rollout undo deployment/$AppName
```

When the test is finished, these commands delete the app, Route, Secret, build
configuration, and stored image from the current Rahti project:

```powershell
oc delete route,service,deployment,secret $AppName
oc delete buildconfig $ImageName
oc delete imagestream $ImageName
```

Do not delete the entire Rahti project unless all resources in that namespace are
known to be disposable.

---

## 4. Option B — deploy to a cPouta VM

### 4.1 Open cPouta and create an SSH key pair

1. Open [pouta.csc.fi](https://pouta.csc.fi/) and select the correct CSC project.
2. Go to **Compute → Key Pairs**.
3. Import an existing SSH public key or create a new key pair.
4. If a new private key is downloaded, store it securely. It cannot be downloaded
   again and a key pair cannot be added to an already-created VM through the
   normal launch flow.

### 4.2 Create a least-privilege security group

Do not modify the shared `default` group. Under **Network → Security Groups**,
create `imu-rehab-api` and add these inbound IPv4 rules:

| Protocol | Port | Source | Reason |
| --- | ---: | --- | --- |
| TCP | 22 | `<ADMIN_PUBLIC_IP>/32` | SSH administration |
| TCP | 80 | `0.0.0.0/0` | ACME certificate challenge and HTTPS redirect |
| TCP | 443 | `0.0.0.0/0` | Public HTTPS API |

Do not add a public rule for `8080`. For a private benchmark, port 443 can be
restricted to the coordinator's public IP. Keep port 80 reachable by the public
ACME validation service for certificate issue and renewal, or use a separately
configured DNS challenge. The API key is still required.

CSC documents that cPouta has two security layers: OpenStack security groups and
the VM's own firewall. Open only required ports and restrict SSH to the smallest
possible CIDR. See [cPouta security](https://docs.csc.fi/cloud/pouta/security/).

### 4.3 Launch the VM and attach a floating IP

1. Go to **Compute → Instances → Launch Instance**.
2. Use a clear instance name, for example `imu-rehab-inference-01`.
3. Select the current **Ubuntu-24.04** image.
4. Select `standard.small` as the starting flavor (2 vCPU, about 1.9 GiB RAM).
   Use `standard.medium` if the one-time container compilation runs out of RAM.
5. Select the project network.
6. Attach only the `imu-rehab-api` security group created above.
7. Select the SSH key pair and launch the VM.
8. From the instance's **Actions** menu, select **Associate Floating IP**. Allocate
   one if the project does not already have an unused address.

The VM receives a private address first; the floating IP provides public access.
CSC's full procedure is in
[Launch a virtual machine from the web interface](https://docs.csc.fi/cloud/pouta/launch-vm-from-web-gui/).
Current image usernames are listed on CSC's
[Pouta images](https://docs.csc.fi/cloud/pouta/images/) page, and current flavor
sizes and billing are on the
[VM flavors and billing](https://docs.csc.fi/cloud/pouta/vm-flavors-and-billing/)
page.

### 4.4 Assign a DNS name

For a production-like endpoint, create an `A` record in the project's DNS
provider, for example:

```text
imu-inference.example.org -> <FLOATING_IP>
```

Wait until it resolves publicly before starting Caddy.

cPouta does not include customer DNS management. For a short test, the floating
IP's predefined `*.poutavm.fi` name can be discovered from PowerShell with:

```powershell
Resolve-DnsName -Name <FLOATING_IP> -Type PTR
```

or from Linux/macOS with:

```bash
host <FLOATING_IP>
```

Use the returned hostname as `DOMAIN` below. CSC recommends the predefined name
only for development and testing; use a controlled DNS name for a durable
deployment. See [Pouta additional services and DNS](https://docs.csc.fi/cloud/pouta/additional-services/).

### 4.5 Connect to and update the VM

From local PowerShell:

```powershell
ssh -i "<PATH_TO_PRIVATE_KEY>" ubuntu@<FLOATING_IP>
```

Ubuntu cloud images use the `ubuntu` account and SSH keys, not password login.
See [Connecting to your virtual machine](https://docs.csc.fi/cloud/pouta/connecting-to-vm/).

On the VM:

```bash
sudo apt-get update
sudo apt-get upgrade -y
sudo apt-get install -y ca-certificates curl git ufw
```

Configure a matching host firewall. Substitute the same administrator IP used
in the cPouta security group and keep the current SSH session open until a second
SSH connection succeeds:

```bash
ADMIN_PUBLIC_IP="<ADMIN_PUBLIC_IPV4_ADDRESS>"
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from "$ADMIN_PUBLIC_IP" to any port 22 proto tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status verbose
unset ADMIN_PUBLIC_IP
```

Docker manages its own packet-filter rules for published container ports, so UFW
must not replace the cPouta security group. In this deployment Docker publishes
only Caddy's ports 80/443 publicly and binds the inference port to loopback.

### 4.6 Install Docker Engine and Compose

Use Docker's official Ubuntu repository rather than the convenience script:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Exit and reconnect so the Docker group membership takes effect:

```bash
exit
```

Then reconnect over SSH and verify:

```bash
docker version
docker compose version
```

Refer to Docker's current
[Ubuntu installation instructions](https://docs.docker.com/engine/install/ubuntu/)
if its repository setup changes.

### 4.7 Copy the repository to the VM

The cleanest path is to commit and push this deployment code to the project's Git
remote, then clone it on the VM:

```bash
cd "$HOME"
git clone <REPOSITORY_URL> wearable-device-ml
cd wearable-device-ml
test -f resource/exported_model/ei_gesture_left_hand_imu_arduino.zip
```

For a private remote, use an SSH deploy key or the organization's approved
credential method. Do not put an access token directly in the clone URL or shell
history.

If no Git remote is available, securely copy the repository from the local
computer instead, then run the same `test -f` check before building.

### 4.8 Add Caddy HTTPS termination

Move to the cloud-service directory:

```bash
cd "$HOME/wearable-device-ml/resource/inference_api/cloud_service"
```

Create `Caddyfile`:

```bash
cat > Caddyfile <<'EOF'
{$DOMAIN} {
    reverse_proxy inference:8080
}
EOF
```

Create a Compose override named `compose.cpouta.yaml`:

```bash
cat > compose.cpouta.yaml <<'EOF'
services:
  caddy:
    image: caddy:2.11.4-alpine
    restart: unless-stopped
    depends_on:
      - inference
    environment:
      DOMAIN: ${DOMAIN:?Set DOMAIN in .env}
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config

volumes:
  caddy_data:
  caddy_config:
EOF
```

The named `/data` volume is important because it retains Caddy's certificates
and keys across container replacement. Caddy will obtain and renew a public TLS
certificate when the DNS name resolves to the floating IP and ports 80/443 are
reachable. See Caddy's
[HTTPS reverse-proxy quick start](https://caddyserver.com/docs/quick-starts/reverse-proxy).

### 4.9 Create the environment file and start the service

Replace the example DNS name, then generate a key and write the protected `.env`
file:

```bash
DOMAIN="imu-inference.example.org"
API_KEY="$(openssl rand -hex 32)"
umask 077
printf 'API_KEY=%s\nHOST_PORT=127.0.0.1:8080\nDOMAIN=%s\n' \
  "$API_KEY" "$DOMAIN" > .env
printf 'Save this API key securely: %s\n' "$API_KEY"
unset API_KEY DOMAIN
```

`HOST_PORT=127.0.0.1:8080` ensures the direct application port is reachable only
from the VM itself. Caddy reaches `inference:8080` over the private Compose
network.

Validate the service names without printing the expanded Secret, then build and
start:

```bash
docker compose --env-file .env \
  -f compose.yaml -f compose.cpouta.yaml config --services

docker compose --env-file .env \
  -f compose.yaml -f compose.cpouta.yaml up -d --build
```

The build compiles the Edge Impulse runner and validates it with a real
inference. Follow progress and startup logs with:

```bash
docker compose --env-file .env \
  -f compose.yaml -f compose.cpouta.yaml ps

docker compose --env-file .env \
  -f compose.yaml -f compose.cpouta.yaml logs --tail=100 inference caddy
```

### 4.10 Verify the cPouta deployment

Load the domain and API key into the current SSH session from the protected file:

```bash
DOMAIN="$(sed -n 's/^DOMAIN=//p' .env)"
API_KEY="$(sed -n 's/^API_KEY=//p' .env)"
```

Check DNS, HTTPS, readiness, and authenticated metrics:

```bash
getent hosts "$DOMAIN"
curl --fail --silent --show-error "https://$DOMAIN/healthz"
curl --fail --silent --show-error "https://$DOMAIN/readyz"
```

Run one authenticated zero-window inference through the public TLS endpoint:

```bash
python3 - <<'PY' | curl --fail --silent --show-error \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  --data-binary @- \
  "https://$DOMAIN/v1/infer"
import json
print(json.dumps({
    "version": 1,
    "window_id": 1,
    "feature_count": 198,
    "features": [0.0] * 198,
    "labels": [
        "Extension", "Flexion", "Pronation", "Radial Deviation",
        "Supination", "Ulnar Deviation"
    ],
    "warmup": False,
}))
PY
```

Read the Prometheus metrics:

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer $API_KEY" \
  "https://$DOMAIN/metrics" | grep '^imu_cloud_'
```

If HTTPS is not ready, inspect Caddy logs and confirm that DNS points to the
floating IP and the security group allows inbound TCP 80 and 443:

```bash
docker compose --env-file .env \
  -f compose.yaml -f compose.cpouta.yaml logs --tail=200 caddy
```

Configure the PC coordinator with:

```text
Model URL:     https://<CPOUTA_DOMAIN>/v1/infer
API key:       <THE_SAVED_API_KEY>
Model version: ei-738400-deployment-19
```

Remove the Secret from the interactive shell when verification is complete:

```bash
unset API_KEY DOMAIN
```

### 4.11 Update, operate, and remove the cPouta deployment

Install Ubuntu security updates regularly. To deploy a new repository revision:

```bash
cd "$HOME/wearable-device-ml"
git pull --ff-only
cd resource/inference_api/cloud_service
docker compose --env-file .env \
  -f compose.yaml -f compose.cpouta.yaml up -d --build
```

Inspect status and logs with:

```bash
docker compose --env-file .env \
  -f compose.yaml -f compose.cpouta.yaml ps
docker compose --env-file .env \
  -f compose.yaml -f compose.cpouta.yaml logs --tail=200 inference
```

To stop and remove the containers while retaining Caddy's certificate volumes:

```bash
docker compose --env-file .env \
  -f compose.yaml -f compose.cpouta.yaml down
```

Adding `--volumes` to that command also deletes Caddy's stored certificate data;
only do that when the endpoint is being retired.

When the benchmark is finished, delete the VM and release any unneeded floating
IP from the cPouta web console. CSC bills allocated resources even when a VM is
shut down or suspended, because the capacity remains reserved.

---

## 5. Collect comparable inference metrics

Use the same model, request data, resource limits, and client location for both
platforms.

1. Confirm the Rahti container limit is one CPU and 512 MiB. The supplied cPouta
   Compose service already applies `cpus: 1.0` and `mem_limit: 512m`.
2. Keep one instance/replica running. Benchmark cold start separately from
   steady-state latency.
3. Send at least 20 requests with `warmup: true` before recording a steady-state
   run.
4. Use the PC coordinator's Cloud REST model and identical sensor windows for
   each cloud target.
5. Record at least:
   - client-observed HTTP wall time;
   - response `timing_us.inference`;
   - response `timing_us.queue`;
   - response `timing_us.server`;
   - success/error counts; and
   - the client-to-cloud network location.
6. Treat `client wall time - timing_us.server` as an approximation of network,
   TLS, and client overhead. It can include clocking and transport effects, so do
   not describe it as pure network latency.
7. Run one request at a time for baseline latency, then deliberately increase
   concurrency to measure queueing and horizontal-scaling behavior.
8. Export `/metrics` after each run and label the saved file with platform,
   region/cluster, container limits, image tag or digest, concurrency, and UTC
   timestamp.
9. Set the same values in the PC dashboard's **Experiment Profile** before the
   mobile session starts. The profile is snapshotted into every attempt record.
10. In another PowerShell terminal, collect Rahti's container working set and
    Pod status from the Metrics API:

    ```powershell
    .\resource\inference_api\cloud_service\collect_rahti_metrics.ps1 `
      -Namespace $RahtiProject `
      -RunLabel "rahti-wifi-1cpu-512m" `
      -IntervalSeconds 2 `
      -OutputDirectory .\metrics
    ```

    Stop with Ctrl+C. Pair the collector CSV with the benchmark export by run
    label and UTC timestamps. Metrics API memory is the container working set;
    the inference response reports application-process RSS instead.

Important service metrics include:

```text
imu_cloud_inference_seconds
imu_cloud_queue_seconds
imu_cloud_request_seconds
imu_cloud_inference_requests_total
imu_cloud_inferences_in_progress
imu_cloud_startup_seconds
imu_cloud_model_info
imu_cloud_inference_http_requests_total
imu_cloud_request_body_bytes
imu_cloud_response_body_bytes
imu_cloud_request_cpu_seconds
imu_cloud_process_resident_memory_bytes
imu_cloud_process_peak_resident_memory_bytes
imu_cloud_process_cpu_seconds
imu_cloud_runner_restarts_total
```

For Prometheus, steady-state p95 native inference latency can be calculated with:

```promql
histogram_quantile(0.95,
  sum by (le) (rate(imu_cloud_inference_seconds_bucket[5m])))
```

The Deployment contains `prometheus.io/scrape`, `prometheus.io/path`, and
`prometheus.io/port` annotations. Because `/metrics` remains protected, mount
the `imu-rehab-inference` Secret into the Prometheus Pod and configure the scrape
job with `authorization.credentials_file` pointing to its `api-key` file. Do
not place the key directly in Prometheus YAML or labels.

When the OpenShift monitoring data source exposes Kubernetes/container metrics,
use these queries alongside the application metrics:

```promql
# Container CPU cores
rate(container_cpu_usage_seconds_total{namespace="<namespace>",container="inference"}[5m])

# Container working-set memory
container_memory_working_set_bytes{namespace="<namespace>",container="inference"}

# CPU throttling ratio
rate(container_cpu_cfs_throttled_periods_total{namespace="<namespace>",container="inference"}[5m])
/
rate(container_cpu_cfs_periods_total{namespace="<namespace>",container="inference"}[5m])

# Restarts and previous OOM termination
kube_pod_container_status_restarts_total{namespace="<namespace>",container="inference"}
kube_pod_container_status_last_terminated_reason{
  namespace="<namespace>",container="inference",reason="OOMKilled"
}
```

The basic Metrics API used by the collector does not expose CPU throttling. If
the cluster Prometheus data source is unavailable, leave throttling unreported
rather than estimating it from CPU utilization.

Do not put the API key in benchmark CSV files, screenshots, shell transcripts,
or Prometheus labels.

## 6. Troubleshooting quick reference

| Symptom | Most likely checks |
| --- | --- |
| Rahti build fails immediately | Run from repository root; confirm the Dockerfile and model ZIP exist; inspect `oc logs build/<build-name>`. |
| Rahti Pod says `ImagePullBackOff` | Confirm the ImageStream tag exists and the Deployment image contains the exact current Rahti namespace. |
| Rahti Pod is rejected by SCC | Ensure the manifest does not set a fixed UID/GID, privileged mode, or extra capabilities. |
| Rahti returns 503 | Check Deployment readiness, Service selectors, endpoints, and Route target port. |
| cPouta SSH times out | Check floating-IP association and restrict-but-allow TCP 22 from the current admin public IP. |
| Caddy cannot obtain a certificate | Confirm public DNS, inbound TCP 80/443, system time, and Caddy logs. |
| API returns 401 | Send `Authorization: Bearer <API_KEY>` and confirm the client key matches the platform Secret or `.env`. |
| API returns 422 | Send exactly 198 finite features and the six labels in model order. |
| Latency is initially high | Separate image/container cold start and runner warm-up from steady-state requests. |
| Container is killed | Inspect Rahti Pod events or `docker inspect`; increase memory only after confirming an OOM. |

## 7. Official references

### CSC Rahti

- [Rahti overview](https://docs.csc.fi/cloud/rahti/)
- [Access Rahti](https://docs.csc.fi/cloud/rahti/access/)
- [Getting started in the web interface](https://docs.csc.fi/cloud/rahti/usage/getting_started/)
- [Projects and quota](https://docs.csc.fi/cloud/rahti/usage/projects_and_quota/)
- [OpenShift CLI](https://docs.csc.fi/cloud/rahti/usage/cli/)
- [Creating container images](https://docs.csc.fi/cloud/rahti/images/creating/)
- [Rahti integrated registry](https://docs.csc.fi/cloud/rahti/images/Using_Rahti_integrated_registry/)
- [Networking and Routes](https://docs.csc.fi/cloud/rahti/networking/)
- [Rahti concepts](https://docs.csc.fi/cloud/rahti/concepts/)

### CSC cPouta

- [Pouta overview](https://docs.csc.fi/cloud/pouta/)
- [Launch a VM from the web interface](https://docs.csc.fi/cloud/pouta/launch-vm-from-web-gui/)
- [Connect to a VM](https://docs.csc.fi/cloud/pouta/connecting-to-vm/)
- [Security](https://docs.csc.fi/cloud/pouta/security/)
- [Networking](https://docs.csc.fi/cloud/pouta/networking/)
- [Images](https://docs.csc.fi/cloud/pouta/images/)
- [VM flavors and billing](https://docs.csc.fi/cloud/pouta/vm-flavors-and-billing/)
- [Additional services and DNS](https://docs.csc.fi/cloud/pouta/additional-services/)
- [Application-development practices](https://docs.csc.fi/cloud/pouta/application-dev/)

### Supporting software

- [Install Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Caddy HTTPS reverse proxy](https://caddyserver.com/docs/quick-starts/reverse-proxy)
- [Official Caddy container image](https://hub.docker.com/_/caddy)

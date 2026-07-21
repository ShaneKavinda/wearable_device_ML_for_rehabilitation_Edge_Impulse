# Cloud deployment troubleshooting FAQ

This FAQ covers common CSC Rahti problems encountered when building or restarting
the IMU inference service, especially after closing and reopening PowerShell.

For the complete deployment procedure, see
[CSC_RAHTI_CPOUTA_DEPLOYMENT.md](CSC_RAHTI_CPOUTA_DEPLOYMENT.md).

## Why can reopening PowerShell break commands that worked previously?

Variables such as `$RahtiProject`, `$AppName`, `$ImageName`, `$ImageTag`, and
`$ApiKey` exist only in the PowerShell process where they were created. Closing
that terminal discards them. Rahti resources remain in the cluster, but a new
terminal does not automatically reconstruct those local variables.

An expired OpenShift login is a separate possibility. The `oc` configuration may
still exist locally while its authentication token is no longer valid.

At the beginning of every new terminal session, move to the repository root and
restore the non-secret deployment variables:

```powershell
$RepoRoot = (git rev-parse --show-toplevel).Trim()
Set-Location $RepoRoot

./oc whoami
$RahtiProject = (./oc project -q).Trim()
$AppName = "imu-rehab-inference"
$ImageName = "imu-rehab-cloud-inference"
$ImageTag = "deployment-19"

if ([string]::IsNullOrWhiteSpace($RahtiProject)) {
    throw "No active Rahti project. Log in and select the project first."
}

Write-Host "Rahti project: $RahtiProject"
Write-Host "Image: $ImageName`:$ImageTag"
```

Confirm that the displayed project is the intended Rahti namespace. If
`./oc whoami` reports an authentication error, obtain and run a new login command
from the Rahti web console before continuing.

## Why does the binary build report that the Dockerfile does not exist?

Typical output:

```text
error: open /tmp/build/inputs/resource/inference_api/cloud_service/Dockerfile:
no such file or directory
```

The BuildConfig expects this repository-root-relative Dockerfile path:

```text
resource/inference_api/cloud_service/Dockerfile
```

`oc start-build --from-dir=.` uploads the current directory as the root of the
binary build context. If the command is run from
`resource/inference_api/cloud_service`, Rahti receives `Dockerfile` at the top of
the archive instead of at the nested path expected by the BuildConfig.

The Dockerfile also copies the model and native runner from other repository
directories, so uploading only the `cloud_service` directory is insufficient.

Run the build from the repository root:

```powershell
$RepoRoot = (git rev-parse --show-toplevel).Trim()
Set-Location $RepoRoot
./oc start-build $ImageName --from-dir=. --follow
```

If the `oc` executable is not stored at the repository root, invoke it through
its absolute path or add it to `PATH`. Do not change the build context to the
cloud-service directory.

CSC documents this behavior in
[Creating an image from a local folder](https://docs.csc.fi/cloud/rahti/images/creating/):
the directory supplied to `--from-dir` becomes the binary build input and must
contain the Dockerfile and all required project files.

## Why does the rollout time out with `InvalidImageName`?

Typical output:

```text
Waiting for deployment "imu-rehab-inference" rollout to finish:
0 of 1 updated replicas are available...
error: timed out waiting for the condition
```

The Pod then shows:

```text
READY   STATUS
0/1     InvalidImageName
```

Inspect the image value stored in the Deployment:

```powershell
./oc get "deployment/$AppName" `
  -o "jsonpath={.spec.template.spec.containers[0].image}"
Write-Host
```

After reopening PowerShell, an unset `$RahtiProject` can render this invalid
reference:

```text
image-registry.openshift-image-registry.svc:5000//imu-rehab-cloud-inference:deployment-19
                                                   ^ empty project name
```

The double slash means the registry namespace is missing. Kubernetes cannot
parse the image reference, so the status is `InvalidImageName`. This differs
from `ImagePullBackOff`, where the image reference is syntactically valid but the
registry cannot supply it.

Restore the variables, construct the image defensively, and inspect it before
updating the Deployment:

```powershell
$RahtiProject = (./oc project -q).Trim()
$AppName = "imu-rehab-inference"
$ImageName = "imu-rehab-cloud-inference"
$ImageTag = "deployment-19"

if ([string]::IsNullOrWhiteSpace($RahtiProject)) {
    throw "The Rahti project name is empty."
}

$InternalImage = "image-registry.openshift-image-registry.svc:5000/${RahtiProject}/${ImageName}:$ImageTag"

if ($InternalImage -match ':5000//') {
    throw "Invalid image reference: $InternalImage"
}

Write-Host $InternalImage
```

The value should have exactly one project name between `5000/` and
`/imu-rehab-cloud-inference`. Confirm that the built ImageStreamTag exists:

```powershell
./oc get imagestreamtag "$($ImageName):$ImageTag"
```

Then update and monitor the Deployment:

```powershell
./oc set image "deployment/$AppName" "inference=$InternalImage"
./oc rollout status "deployment/$AppName" --timeout=5m
./oc get pods -l "app.kubernetes.io/name=$AppName" -o wide
```

Rahti's internal image naming convention is described in
[Kubernetes and OpenShift concepts](https://docs.csc.fi/cloud/rahti/concepts/).

## What should I check before resuming work in a new terminal?

Use this checklist before rebuilding or redeploying:

1. Work from the repository root returned by `git rev-parse --show-toplevel`.
2. Run `./oc whoami` and log in again if authentication has expired.
3. Run `./oc project -q` and verify the selected Rahti namespace.
4. Recreate `$RahtiProject`, `$AppName`, `$ImageName`, and `$ImageTag`.
5. Confirm the image exists with
   `./oc get imagestreamtag "$($ImageName):$ImageTag"`.
6. Print and inspect `$InternalImage` before applying or patching a Deployment.
7. Never recreate the API key merely because the terminal was closed. The
   existing key remains in the OpenShift Secret unless that Secret was replaced.

Useful read-only diagnostics are:

```powershell
./oc get builds
./oc get imagestreams
./oc get deployments
./oc get pods -o wide
./oc get events --sort-by=.lastTimestamp
./oc logs "deployment/$AppName" --tail=100
```

## Does closing the terminal delete anything from Rahti?

No. Builds, ImageStreams, Secrets, Deployments, Services, Routes, and Pods are
cluster resources and remain in Rahti. Closing the terminal only removes local
shell state, including ordinary PowerShell variables. It does not undo a build
or rollout.

Avoid storing `$ApiKey` in a committed script to make it persist. Keep the API
key in the OpenShift Secret and an approved password manager.

## Official Rahti references

- [Command-line tool](https://docs.csc.fi/cloud/rahti/usage/cli/)
- [Creating container images](https://docs.csc.fi/cloud/rahti/images/creating/)
- [Kubernetes and OpenShift concepts](https://docs.csc.fi/cloud/rahti/concepts/)
- [Rahti integrated registry](https://docs.csc.fi/cloud/rahti/images/Using_Rahti_integrated_registry/)

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Namespace,

    [string]$LabelSelector = "app.kubernetes.io/name=imu-rehab-inference",

    [ValidateRange(1, 3600)]
    [int]$IntervalSeconds = 2,

    [string]$RunLabel = "unspecified",

    [string]$OutputDirectory = "metrics",

    [string]$OcPath = "oc"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:StopRequested = $false

function Convert-CpuToMillicores {
    param([AllowNull()][string]$Quantity)
    if ([string]::IsNullOrWhiteSpace($Quantity)) { return $null }
    if ($Quantity.EndsWith("n")) { return [double]$Quantity.TrimEnd("n") / 1e6 }
    if ($Quantity.EndsWith("u")) { return [double]$Quantity.TrimEnd("u") / 1e3 }
    if ($Quantity.EndsWith("m")) { return [double]$Quantity.TrimEnd("m") }
    return [double]$Quantity * 1000.0
}

function Convert-MemoryToBytes {
    param([AllowNull()][string]$Quantity)
    if ([string]::IsNullOrWhiteSpace($Quantity)) { return $null }
    $multipliers = @{
        "Ki" = 1024.0
        "Mi" = 1024.0 * 1024.0
        "Gi" = 1024.0 * 1024.0 * 1024.0
        "Ti" = 1024.0 * 1024.0 * 1024.0 * 1024.0
        "K" = 1000.0
        "M" = 1000.0 * 1000.0
        "G" = 1000.0 * 1000.0 * 1000.0
    }
    foreach ($suffix in @("Ki", "Mi", "Gi", "Ti", "K", "M", "G")) {
        if ($Quantity.EndsWith($suffix)) {
            $number = [double]$Quantity.Substring(0, $Quantity.Length - $suffix.Length)
            return [math]::Round($number * $multipliers[$suffix])
        }
    }
    return [double]$Quantity
}

$ocCommand = Get-Command $OcPath -ErrorAction SilentlyContinue
if ($null -eq $ocCommand) {
    $repoOc = [System.IO.Path]::GetFullPath(
        (Join-Path $PSScriptRoot "..\..\..\oc.exe")
    )
    if (Test-Path -LiteralPath $repoOc -PathType Leaf) {
        $OcPath = $repoOc
    }
    else {
        throw "The OpenShift oc command was not found. Pass -OcPath or add it to PATH."
    }
}

& $OcPath project $Namespace | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not select Rahti namespace '$Namespace'. Log in with oc first."
}

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null
$timestamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$outputPath = Join-Path $resolvedOutput "rahti_metrics_${timestamp}.csv"
$wroteHeader = $false

$cancelHandler = [ConsoleCancelEventHandler]{
    param($sender, $eventArgs)
    $eventArgs.Cancel = $true
    $script:StopRequested = $true
}
[Console]::add_CancelKeyPress($cancelHandler)

Write-Host "Collecting Rahti metrics for '$LabelSelector' in '$Namespace'."
Write-Host "Run label: $RunLabel"
Write-Host "Output: $outputPath"
Write-Host "Press Ctrl+C to stop cleanly."

try {
    while (-not $script:StopRequested) {
        $sampledAt = [DateTime]::UtcNow.ToString("o")
        $pods = (& $OcPath get pods --namespace $Namespace --selector $LabelSelector --output json) |
            ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { throw "oc get pods failed." }

        $metricsPath = "/apis/metrics.k8s.io/v1beta1/namespaces/$Namespace/pods"
        $podMetrics = (& $OcPath get --raw $metricsPath) | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { throw "The Rahti Metrics API request failed." }

        $metricByPod = @{}
        foreach ($item in @($podMetrics.items)) {
            $metricByPod[[string]$item.metadata.name] = $item
        }

        $rows = foreach ($pod in @($pods.items)) {
            $podName = [string]$pod.metadata.name
            $metric = $metricByPod[$podName]
            $metricByContainer = @{}
            if ($null -ne $metric) {
                foreach ($containerMetric in @($metric.containers)) {
                    $metricByContainer[[string]$containerMetric.name] = $containerMetric
                }
            }

            foreach ($status in @($pod.status.containerStatuses)) {
                $containerName = [string]$status.name
                $usage = $metricByContainer[$containerName]
                $cpuQuantity = $null
                $memoryQuantity = $null
                if ($null -ne $usage) {
                    $cpuQuantity = [string]$usage.usage.cpu
                    $memoryQuantity = [string]$usage.usage.memory
                }
                $lastReason = $null
                if ($null -ne $status.lastState -and $null -ne $status.lastState.terminated) {
                    $lastReason = [string]$status.lastState.terminated.reason
                }
                [pscustomobject]@{
                    sampled_at_utc = $sampledAt
                    run_label = $RunLabel
                    namespace = $Namespace
                    pod = $podName
                    container = $containerName
                    cpu_millicores = Convert-CpuToMillicores $cpuQuantity
                    memory_working_set_bytes = Convert-MemoryToBytes $memoryQuantity
                    ready = [bool]$status.ready
                    restart_count = [int]$status.restartCount
                    phase = [string]$pod.status.phase
                    last_termination_reason = $lastReason
                }
            }
        }

        if (@($rows).Count -gt 0) {
            if ($wroteHeader) {
                $rows | Export-Csv -LiteralPath $outputPath -NoTypeInformation -Append
            }
            else {
                $rows | Export-Csv -LiteralPath $outputPath -NoTypeInformation
                $wroteHeader = $true
            }
        }

        for ($second = 0; $second -lt $IntervalSeconds -and -not $script:StopRequested; $second++) {
            Start-Sleep -Seconds 1
        }
    }
}
finally {
    [Console]::remove_CancelKeyPress($cancelHandler)
    Write-Host "Stopped. Metrics were written to $outputPath"
}

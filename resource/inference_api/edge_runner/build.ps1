param(
    [string]$BuildDirectory = "build"
)

$ErrorActionPreference = "Stop"
$RunnerDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
python (Join-Path $RunnerDirectory "prepare_model.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

function Get-ToolPath($Tool) {
    if ($null -eq $Tool) { return $null }
    if ($Tool.Source) { return $Tool.Source }
    return $Tool.FullName
}

$CMake = Get-Command cmake -ErrorAction SilentlyContinue
if (-not $CMake) {
    $AndroidCMake = Get-ChildItem `
        (Join-Path $env:LOCALAPPDATA "Android\Sdk\cmake\*\bin\cmake.exe") `
        -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    $CMake = $AndroidCMake
}
$Gxx = Get-Command g++ -ErrorAction SilentlyContinue
$Gcc = Get-Command gcc -ErrorAction SilentlyContinue
$CompilerDirectories = @(
    "C:\msys64\ucrt64\bin",
    "C:\msys64\mingw64\bin",
    "C:\mingw64\bin"
)
if ($env:MSYSTEM_PREFIX) {
    $CompilerDirectories = @((Join-Path $env:MSYSTEM_PREFIX "bin")) + $CompilerDirectories
}
if (-not $Gxx -or -not $Gcc) {
    foreach ($Directory in $CompilerDirectories) {
        $CandidateGxx = Join-Path $Directory "g++.exe"
        $CandidateGcc = Join-Path $Directory "gcc.exe"
        if ((Test-Path -LiteralPath $CandidateGxx) -and
            (Test-Path -LiteralPath $CandidateGcc)) {
            $Gxx = Get-Item -LiteralPath $CandidateGxx
            $Gcc = Get-Item -LiteralPath $CandidateGcc
            break
        }
    }
}
$Ninja = Get-Command ninja -ErrorAction SilentlyContinue
if (-not $Ninja) {
    $Ninja = Get-ChildItem `
        (Join-Path $env:LOCALAPPDATA "Android\Sdk\cmake\*\bin\ninja.exe") `
        -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1
}

if (-not $CMake -or -not $Gxx -or -not $Gcc -or -not $Ninja) {
    throw "CMake, Ninja, and MinGW gcc/g++ are required. Install Android SDK CMake and an MSYS2 UCRT64 desktop toolchain; the runner will not substitute fake inference."
}

$CMakePath = Get-ToolPath $CMake
$NinjaPath = Get-ToolPath $Ninja
$GccPath = Get-ToolPath $Gcc
$GxxPath = Get-ToolPath $Gxx
$CompilerDirectory = Split-Path -Parent $GxxPath

# MSYS2's compiler front-end starts cc1/cc1plus as child processes. Their DLLs
# live beside g++.exe, so an absolute compiler path alone is insufficient.
if (($env:PATH -split ';') -notcontains $CompilerDirectory) {
    $env:PATH = "$CompilerDirectory;$env:PATH"
}

$BuildPath = Join-Path $RunnerDirectory $BuildDirectory
& $CMakePath `
    -S $RunnerDirectory `
    -B $BuildPath `
    -G Ninja `
    "-DCMAKE_MAKE_PROGRAM=$NinjaPath" `
    "-DCMAKE_C_COMPILER=$GccPath" `
    "-DCMAKE_CXX_COMPILER=$GxxPath"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $CMakePath --build $BuildPath --config Release
exit $LASTEXITCODE

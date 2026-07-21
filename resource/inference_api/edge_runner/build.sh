#!/usr/bin/env bash
set -euo pipefail

BuildDirectory="${1:-build}"
RunnerDirectory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
    PythonBin="python3"
elif command -v python >/dev/null 2>&1; then
    PythonBin="python"
else
    echo "Python is required." >&2
    exit 127
fi

"$PythonBin" "$RunnerDirectory/prepare_model.py"

resolve_tool_path() {
    local tool="$1"
    if [[ -z "$tool" ]]; then
        echo ""
        return 0
    fi

    if [[ "$tool" == /* ]]; then
        echo "$tool"
        return 0
    fi

    local resolved
    resolved="$(command -v "$tool" 2>/dev/null || true)"
    if [[ -n "$resolved" ]]; then
        echo "$resolved"
    else
        echo ""
    fi
}

CMakePath="$(resolve_tool_path "cmake")"
GxxPath="$(resolve_tool_path "g++")"
GccPath="$(resolve_tool_path "gcc")"
NinjaPath="$(resolve_tool_path "ninja")"

if [[ -z "$CMakePath" || -z "$GxxPath" || -z "$GccPath" || -z "$NinjaPath" ]]; then
    echo "CMake, Ninja, and a C/C++ compiler toolchain are required. The runner will not substitute fake inference." >&2
    exit 1
fi

CompilerDirectory="$(dirname "$GxxPath")"
if [[ ":$PATH:" != *":$CompilerDirectory:"* ]]; then
    export PATH="$CompilerDirectory:$PATH"
fi

BuildPath="$RunnerDirectory/$BuildDirectory"

"$CMakePath" \
    -S "$RunnerDirectory" \
    -B "$BuildPath" \
    -G Ninja \
    "-DCMAKE_MAKE_PROGRAM=$NinjaPath" \
    "-DCMAKE_C_COMPILER=$GccPath" \
    "-DCMAKE_CXX_COMPILER=$GxxPath"

"$CMakePath" --build "$BuildPath" --config Release

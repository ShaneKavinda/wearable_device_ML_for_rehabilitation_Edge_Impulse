# Windows Edge Inference Runner

The Python API hub keeps this native process open and exchanges fixed binary
frames over stdin/stdout. The runner executes the Edge Impulse DSP and
classifier used by the PC's local model deployment.

From the repository root, use the self-contained Windows build script. It
can reuse CMake/Ninja already installed with the Android SDK and discovers
common MSYS2 MinGW installations even
when they are not on the global `PATH`:

```powershell
powershell -ExecutionPolicy Bypass -File resource\inference_api\edge_runner\build.ps1
```

The output is `resource\inference_api\edge_runner\build\edge_inference_runner.exe`,
which is also the PC dashboard's default runner path.

The script deliberately stops if a desktop MinGW compiler is missing. A manual
CMake build is also supported, but `cmake`, `ninja`, and the MinGW `bin`
directory must all be on `PATH` in that shell.

`prepare_model.py` verifies project 738400, deployment 19, 33 samples at 16.5 Hz,
the six-axis order, raw DSP scale 1.0, 198 inputs, and all six labels before
creating the short-path `.ei_model` tree used by the Windows runner. Generated
sources are ignored by Git. Inputs must use acceleration in g and gyroscope in
degrees/second, matching the training CSV.

Protocol: stdin receives `EIQ1`, a little-endian window ID and feature count,
then 198 float32 values. Stdout returns `EIR1`, the window ID, status,
inference microseconds, and six float32 scores. The process remains alive for
multiple requests.

If the Arduino-generated compiled model is not portable to the installed
Windows compiler, export a desktop C++ library from the same Edge Impulse
project and pass that ZIP to `prepare_model.py --archive <path>`.

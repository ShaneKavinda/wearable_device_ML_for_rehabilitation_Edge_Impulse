# Edge Impulse Android C++ Export

Copy the Android C++ library export from Edge Impulse into this directory.

Expected layout:

```text
edge_impulse_vendor/
  edge-impulse-sdk/
  model-parameters/
  tflite-model/
```

The current repository contains Arduino library ZIPs. Those are useful for the
Pico firmware, but this Flutter app is wired for the Android C++ export so the
native method channel can call `run_classifier()` from Android.

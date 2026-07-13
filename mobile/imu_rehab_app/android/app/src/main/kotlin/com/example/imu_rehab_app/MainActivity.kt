package com.example.imu_rehab_app

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var nativeLoadError: String? = null
    private var pendingPermissionResult: MethodChannel.Result? = null

    private external fun nativeClassify(windowId: Long, features: FloatArray): HashMap<String, Any?>

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        loadNativeLibrary()
        configurePermissionChannel(flutterEngine)

        MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            CHANNEL_NAME
        ).setMethodCallHandler { call, result ->
            when (call.method) {
                "warmUp" -> result.success(null)
                "classify" -> result.success(classify(call))
                "close" -> result.success(null)
                else -> result.notImplemented()
            }
        }
    }

    private fun configurePermissionChannel(flutterEngine: FlutterEngine) {
        MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            PERMISSIONS_CHANNEL_NAME
        ).setMethodCallHandler { call, result ->
            when (call.method) {
                "ensureBluetoothScanPermissions" -> ensureBluetoothScanPermissions(result)
                else -> result.notImplemented()
            }
        }
    }

    private fun ensureBluetoothScanPermissions(result: MethodChannel.Result) {
        val missing = requiredBluetoothPermissions().filter {
            checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED
        }

        if (missing.isEmpty()) {
            result.success(true)
            return
        }

        if (pendingPermissionResult != null) {
            result.error(
                "permission_request_in_progress",
                "A Bluetooth permission request is already in progress.",
                null
            )
            return
        }

        pendingPermissionResult = result
        requestPermissions(missing.toTypedArray(), BLUETOOTH_PERMISSION_REQUEST_CODE)
    }

    private fun requiredBluetoothPermissions(): List<String> {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            listOf(
                Manifest.permission.BLUETOOTH_SCAN,
                Manifest.permission.BLUETOOTH_CONNECT
            )
        } else {
            listOf(Manifest.permission.ACCESS_FINE_LOCATION)
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != BLUETOOTH_PERMISSION_REQUEST_CODE) {
            return
        }

        val result = pendingPermissionResult ?: return
        pendingPermissionResult = null
        result.success(grantResults.isNotEmpty() && grantResults.all {
            it == PackageManager.PERMISSION_GRANTED
        })
    }

    private fun loadNativeLibrary() {
        if (nativeLoadError != null) return
        try {
            System.loadLibrary("edge_impulse_bridge")
        } catch (error: UnsatisfiedLinkError) {
            nativeLoadError = error.message ?: "Native bridge failed to load."
        }
    }

    private fun classify(call: MethodCall): HashMap<String, Any?> {
        nativeLoadError?.let { return errorResult(it) }

        val args = call.arguments as? Map<*, *> ?: return errorResult("Missing classify arguments.")
        val windowId = (args["windowId"] as? Number)?.toLong()
            ?: return errorResult("Missing windowId.")
        val values = args["features"] as? List<*>
            ?: return errorResult("Missing features list.")

        if (values.size != FEATURE_COUNT) {
            return errorResult("Expected $FEATURE_COUNT features, got ${values.size}.")
        }

        val features = FloatArray(values.size)
        values.forEachIndexed { index, value ->
            val number = value as? Number
                ?: return errorResult("Feature $index is not numeric.")
            features[index] = number.toFloat()
        }

        return nativeClassify(windowId, features)
    }

    private fun errorResult(message: String): HashMap<String, Any?> {
        return hashMapOf(
            "predictedLabel" to "ERR",
            "confidence" to 0.0,
            "scores" to emptyMap<String, Double>(),
            "timing" to emptyMap<String, Double>(),
            "error" to message
        )
    }

    companion object {
        private const val CHANNEL_NAME = "imu_rehab/edge_impulse"
        private const val PERMISSIONS_CHANNEL_NAME = "imu_rehab/android_permissions"
        private const val BLUETOOTH_PERMISSION_REQUEST_CODE = 4001
        private const val FEATURE_COUNT = 198
    }
}

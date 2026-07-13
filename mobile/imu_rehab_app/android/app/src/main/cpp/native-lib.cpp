#include <jni.h>

#include <algorithm>
#include <cstring>
#include <map>
#include <string>
#include <vector>

#if __has_include("edge-impulse-sdk/classifier/ei_run_classifier.h") && \
    __has_include("model-parameters/model_metadata.h") && \
    __has_include("model-parameters/model_variables.h")
#define IMU_REHAB_EI_VENDOR_AVAILABLE 1
#include "edge-impulse-sdk/classifier/ei_run_classifier.h"
#include "model-parameters/model_metadata.h"
#include "model-parameters/model_variables.h"
#else
#define IMU_REHAB_EI_VENDOR_AVAILABLE 0
#endif

namespace {

jobject newHashMap(JNIEnv *env) {
    jclass mapClass = env->FindClass("java/util/HashMap");
    jmethodID init = env->GetMethodID(mapClass, "<init>", "()V");
    return env->NewObject(mapClass, init);
}

void putObject(JNIEnv *env, jobject map, const char *key, jobject value) {
    jclass mapClass = env->FindClass("java/util/HashMap");
    jmethodID put = env->GetMethodID(
        mapClass,
        "put",
        "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;");
    jstring jKey = env->NewStringUTF(key);
    env->CallObjectMethod(map, put, jKey, value);
    env->DeleteLocalRef(jKey);
}

void putString(JNIEnv *env, jobject map, const char *key, const std::string &value) {
    jstring jValue = env->NewStringUTF(value.c_str());
    putObject(env, map, key, jValue);
    env->DeleteLocalRef(jValue);
}

void putDouble(JNIEnv *env, jobject map, const char *key, double value) {
    jclass doubleClass = env->FindClass("java/lang/Double");
    jmethodID init = env->GetMethodID(doubleClass, "<init>", "(D)V");
    jobject jValue = env->NewObject(doubleClass, init, value);
    putObject(env, map, key, jValue);
    env->DeleteLocalRef(jValue);
}

jobject makeStringDoubleMap(JNIEnv *env, const std::map<std::string, double> &values) {
    jobject map = newHashMap(env);
    for (const auto &entry : values) {
        putDouble(env, map, entry.first.c_str(), entry.second);
    }
    return map;
}

jobject errorResult(JNIEnv *env, const std::string &message) {
    jobject map = newHashMap(env);
    putString(env, map, "predictedLabel", "ERR");
    putDouble(env, map, "confidence", 0.0);
    jobject scores = makeStringDoubleMap(env, {});
    jobject timing = makeStringDoubleMap(env, {});
    putObject(env, map, "scores", scores);
    putObject(env, map, "timing", timing);
    putString(env, map, "error", message);
    env->DeleteLocalRef(scores);
    env->DeleteLocalRef(timing);
    return map;
}

#if IMU_REHAB_EI_VENDOR_AVAILABLE
std::vector<float> g_features;

int getFeatureData(size_t offset, size_t length, float *outPtr) {
    if (offset + length > g_features.size()) {
        return -1;
    }
    std::memcpy(outPtr, g_features.data() + offset, length * sizeof(float));
    return 0;
}
#endif

}  // namespace

extern "C" JNIEXPORT jobject JNICALL
Java_com_example_imu_1rehab_1app_MainActivity_nativeClassify(
    JNIEnv *env,
    jobject,
    jlong,
    jfloatArray featuresArray) {
    const jsize featureCount = env->GetArrayLength(featuresArray);

#if !IMU_REHAB_EI_VENDOR_AVAILABLE
    (void)featureCount;
    return errorResult(
        env,
        "Edge Impulse Android C++ export is not vendored. Copy edge-impulse-sdk, "
        "model-parameters, and tflite-model into android/app/src/main/cpp/"
        "edge_impulse_vendor.");
#else
    if (featureCount != EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE) {
        return errorResult(
            env,
            "Feature count does not match EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE.");
    }

    g_features.resize(static_cast<size_t>(featureCount));
    env->GetFloatArrayRegion(featuresArray, 0, featureCount, g_features.data());

    signal_t signal;
    signal.total_length = static_cast<size_t>(featureCount);
    signal.get_data = &getFeatureData;

    ei_impulse_result_t result = {0};
    EI_IMPULSE_ERROR inferenceError = run_classifier(&signal, &result, false);
    if (inferenceError != EI_IMPULSE_OK) {
        return errorResult(env, "run_classifier failed.");
    }

    std::map<std::string, double> scores;
    std::string bestLabel = "unknown";
    double bestScore = -1.0;

    for (size_t index = 0; index < EI_CLASSIFIER_LABEL_COUNT; index++) {
        const auto &classification = result.classification[index];
        const double score = static_cast<double>(classification.value);
        scores[classification.label] = score;
        if (score > bestScore) {
            bestScore = score;
            bestLabel = classification.label;
        }
    }

    std::map<std::string, double> timing = {
        {"dsp_ms", static_cast<double>(result.timing.dsp)},
        {"classification_ms", static_cast<double>(result.timing.classification)},
        {"anomaly_ms", static_cast<double>(result.timing.anomaly)},
    };

    jobject map = newHashMap(env);
    jobject scoreMap = makeStringDoubleMap(env, scores);
    jobject timingMap = makeStringDoubleMap(env, timing);

    putString(env, map, "predictedLabel", bestLabel);
    putDouble(env, map, "confidence", std::max(0.0, bestScore));
    putObject(env, map, "scores", scoreMap);
    putObject(env, map, "timing", timingMap);
    putString(env, map, "error", "");

    env->DeleteLocalRef(scoreMap);
    env->DeleteLocalRef(timingMap);
    return map;
#endif
}

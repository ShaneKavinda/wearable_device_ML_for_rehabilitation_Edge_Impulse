#include <ei_gesture_left_hand_imu.h>
#include <Wire.h>

#define ICM20948_ADDR 0x68
#define ICM20948_WHO_AM_I 0x00
#define ICM20948_PWR_MGMT_1 0x06
#define ICM20948_PWR_MGMT_2 0x07
#define ICM20948_ACCEL_XOUT_H 0x2D
#define ICM20948_GYRO_XOUT_H 0x33
#define G_TO_MS2 9.80665f 
#define CONFIDENCE_THRESHOLD 0.85f

#ifndef EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME
#define EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME 6
#endif

#ifndef EI_CLASSIFIER_RAW_SAMPLE_COUNT
#define EI_CLASSIFIER_RAW_SAMPLE_COUNT (EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE / EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME)
#endif

const String GESTURE_LIST[] = {
  "Flexion", 
  "Extension", 
  "Pronation", 
  "Supination", 
  "Radial Deviation", 
  "Ulnar Deviation"  
};
const int NUM_GESTURES = 6;
const char DEVICE_ID[] = "beetle_rp2530_001";

struct InferenceResult {
    String label;
    float confidence;
    float scores[EI_CLASSIFIER_LABEL_COUNT];
    bool trusted;       // >= CONFIDENCE_THRESHOLD
    bool correctLabel;  // Matches target
    bool ok;
    int errorCode;
    uint32_t inferenceStartUs;
    uint32_t inferenceEndUs;
    float inferenceMs;
    int dspMs;
    int classificationMs;
    int anomalyMs;
    int32_t memoryBeforeBytes;
    int32_t memoryAfterBytes;
    int32_t memoryDeltaBytes;
};

/* Buffer */
float features[EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE];

String targetGesture = "";

bool initICM20948();
void writeRegister(uint8_t reg, uint8_t value);
void readIMUData(float* ax, float* ay, float* az, float* gx, float* gy, float* gz);
void collectGestureData();
void runInference(InferenceResult* r, int repetition);
void reinitializeSensor();
void printMenu();
void printStatusJson(const char *event, const char *message);
void printRepetitionEventJson(int repetition, const char *event);
void printSessionSummaryJson(InferenceResult history[], int total);
void printInferenceResultJson(int repetition, const InferenceResult &r);
void printScoresJson(const InferenceResult &r);
void printJsonString(const char *value);
void printJsonNullableInt(int32_t value);
int32_t getFreeMemoryBytes();
int raw_feature_get_data(size_t offset, size_t length, float *out_ptr);

void setup() {
    Serial.begin(115200);
    while (!Serial);
    
    Wire.setSDA(4);
    Wire.setSCL(5); 
    Wire.begin();
    
    if (!initICM20948()) {
        printStatusJson("startup", "sensor_connection_failed");
    } else {
        printStatusJson("startup", "system_ready");
    }
    printMenu();
}

void printMenu() {
    Serial.print("{\"type\":\"gesture_menu\",\"device_id\":");
    printJsonString(DEVICE_ID);
    Serial.print(",\"prompt\":\"select_gesture_1_to_6\",\"gestures\":[");
    for (int i = 0; i < NUM_GESTURES; i++) {
        if (i > 0) Serial.print(",");
        Serial.print("{\"selection\":");
        Serial.print(i + 1);
        Serial.print(",\"label\":");
        printJsonString(GESTURE_LIST[i].c_str());
        Serial.print("}");
    }
    Serial.println("]}");
}

void loop() {
    if (Serial.available() > 0) {
        int selection = Serial.parseInt();
        while(Serial.available()) Serial.read();
        if (selection >= 1 && selection <= NUM_GESTURES) {
            targetGesture = GESTURE_LIST[selection - 1];
            
            Serial.print("{\"type\":\"session_start\",\"device_id\":");
            printJsonString(DEVICE_ID);
            Serial.print(",\"target\":");
            printJsonString(targetGesture.c_str());
            Serial.println(",\"repetitions\":10}");
            InferenceResult history[10]; 
            
            // Loop 10 times
            for (int i = 0; i < 10; i++) {
                printRepetitionEventJson(i + 1, "get_ready");
                delay(1000); printRepetitionEventJson(i + 1, "countdown_3");
                delay(1000); printRepetitionEventJson(i + 1, "countdown_2");
                delay(1000); printRepetitionEventJson(i + 1, "countdown_1");
                printRepetitionEventJson(i + 1, "movement_start");
                collectGestureData();
                printRepetitionEventJson(i + 1, "sampling_finished");
                runInference(&history[i], i + 1);
                reinitializeSensor(); 

                if (i < 9) {
                    printRepetitionEventJson(i + 1, "next_rep_in_2s");
                    delay(2000); 
                }
            }
            
            printSessionSummaryJson(history, 10);
            
            printMenu();
        } 
        else if (selection != 0) {
            printStatusJson("invalid_selection", "select_a_number_1_to_6");
            printMenu();
        }
    }
}

void runInference(InferenceResult* r, int repetition) {
    r->label = "ERR";
    r->confidence = 0.0f;
    r->trusted = false;
    r->correctLabel = false;
    r->ok = false;
    r->errorCode = 0;
    r->inferenceStartUs = 0;
    r->inferenceEndUs = 0;
    r->inferenceMs = 0.0f;
    r->dspMs = 0;
    r->classificationMs = 0;
    r->anomalyMs = 0;
    r->memoryBeforeBytes = -1;
    r->memoryAfterBytes = -1;
    r->memoryDeltaBytes = 0;
    for (size_t ix = 0; ix < EI_CLASSIFIER_LABEL_COUNT; ix++) {
        r->scores[ix] = 0.0f;
    }

    signal_t signal;
    signal.total_length = EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE;
    signal.get_data = &raw_feature_get_data;

    ei_impulse_result_t result = {0};
    r->memoryBeforeBytes = getFreeMemoryBytes();
    r->inferenceStartUs = micros();
    EI_IMPULSE_ERROR res = run_classifier(&signal, &result, false);
    r->inferenceEndUs = micros();
    r->memoryAfterBytes = getFreeMemoryBytes();
    r->memoryDeltaBytes = (r->memoryBeforeBytes >= 0 && r->memoryAfterBytes >= 0)
        ? (r->memoryAfterBytes - r->memoryBeforeBytes)
        : 0;
    r->inferenceMs = (float)((uint32_t)(r->inferenceEndUs - r->inferenceStartUs)) / 1000.0f;
    r->errorCode = (int)res;

    if (res != EI_IMPULSE_OK) {
        printInferenceResultJson(repetition, *r);
        return;
    }

    float max_score = -1.0;
    int max_idx = -1;

    for (size_t ix = 0; ix < EI_CLASSIFIER_LABEL_COUNT; ix++) {
        if (result.classification[ix].value > max_score) {
            max_score = result.classification[ix].value;
            max_idx = ix;
        }
        r->scores[ix] = result.classification[ix].value;
    }
    
    r->label = String(result.classification[max_idx].label);
    r->confidence = max_score;
    r->trusted = (max_score >= CONFIDENCE_THRESHOLD);
    r->ok = true;
    r->dspMs = result.timing.dsp;
    r->classificationMs = result.timing.classification;
    r->anomalyMs = result.timing.anomaly;
    
    if (r->label.equalsIgnoreCase(targetGesture)) {
        r->correctLabel = true;
    } else {
        r->correctLabel = false;
    }

    printInferenceResultJson(repetition, *r);
}

void printStatusJson(const char *event, const char *message) {
    Serial.print("{\"type\":\"status\",\"device_id\":");
    printJsonString(DEVICE_ID);
    Serial.print(",\"event\":");
    printJsonString(event);
    Serial.print(",\"message\":");
    printJsonString(message);
    Serial.println("}");
}

void printRepetitionEventJson(int repetition, const char *event) {
    Serial.print("{\"type\":\"repetition_event\",\"device_id\":");
    printJsonString(DEVICE_ID);
    Serial.print(",\"target\":");
    printJsonString(targetGesture.c_str());
    Serial.print(",\"repetition\":");
    Serial.print(repetition);
    Serial.print(",\"event\":");
    printJsonString(event);
    Serial.println("}");
}

void printSessionSummaryJson(InferenceResult history[], int total) {
    int successCount = 0;
    int correctCount = 0;
    int uncertainCount = 0;
    float totalSuccessConf = 0.0;
    float totalInferenceMs = 0.0f;
    int timedInferenceCount = 0;
    int32_t minFreeMemory = -1;
    
    for (int i = 0; i < total; i++) {
        if (history[i].ok) {
            totalInferenceMs += history[i].inferenceMs;
            timedInferenceCount++;
        }
        if (history[i].memoryAfterBytes >= 0 &&
            (minFreeMemory < 0 || history[i].memoryAfterBytes < minFreeMemory)) {
            minFreeMemory = history[i].memoryAfterBytes;
        }
        if (history[i].correctLabel) {
            correctCount++;
        }
        
        if (history[i].correctLabel && history[i].trusted) {
            successCount++;
            totalSuccessConf += history[i].confidence;
        } else if (history[i].correctLabel) {
            uncertainCount++;
        }
    }
    
    float avgConf = 0.0;
    if (successCount > 0) avgConf = totalSuccessConf / successCount;
    float avgInferenceMs = 0.0f;
    if (timedInferenceCount > 0) avgInferenceMs = totalInferenceMs / timedInferenceCount;
    
    Serial.print("{\"type\":\"session_summary\",\"device_id\":");
    printJsonString(DEVICE_ID);
    Serial.print(",\"target\":");
    printJsonString(targetGesture.c_str());
    Serial.print(",\"total_repetitions\":");
    Serial.print(total);
    Serial.print(",\"correct_count\":");
    Serial.print(correctCount);
    Serial.print(",\"accuracy\":");
    Serial.print((float)correctCount / total, 6);
    Serial.print(",\"accuracy_percent\":");
    Serial.print(((float)correctCount / total) * 100.0f, 1);
    Serial.print(",\"pass_count\":");
    Serial.print(successCount);
    Serial.print(",\"pass_rate\":");
    Serial.print((float)successCount / total, 6);
    Serial.print(",\"pass_rate_percent\":");
    Serial.print(((float)successCount / total) * 100.0f, 1);
    Serial.print(",\"uncertain_count\":");
    Serial.print(uncertainCount);
    Serial.print(",\"avg_pass_confidence\":");
    Serial.print(avgConf, 6);
    Serial.print(",\"avg_inference_ms\":");
    Serial.print(avgInferenceMs, 3);
    Serial.print(",\"min_free_memory_bytes\":");
    printJsonNullableInt(minFreeMemory);
    Serial.println("}");
}

void printInferenceResultJson(int repetition, const InferenceResult &r) {
    Serial.print("{\"type\":\"inference_result\"");
    Serial.print(",\"device_id\":");
    printJsonString(DEVICE_ID);
    Serial.print(",\"repetition\":");
    Serial.print(repetition);
    Serial.print(",\"window_id\":");
    Serial.print(repetition);
    Serial.print(",\"target\":");
    printJsonString(targetGesture.c_str());
    Serial.print(",\"sample_count\":");
    Serial.print((uint32_t)EI_CLASSIFIER_RAW_SAMPLE_COUNT);
    Serial.print(",\"axes_per_sample\":");
    Serial.print((uint32_t)EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME);
    Serial.print(",\"input_frame_size\":");
    Serial.print((uint32_t)EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE);
    Serial.print(",\"sample_interval_ms\":");
    Serial.print((double)EI_CLASSIFIER_INTERVAL_MS, 6);
    Serial.print(",\"ok\":");
    Serial.print(r.ok ? "true" : "false");
    Serial.print(",\"label\":");
    if (r.ok) {
        printJsonString(r.label.c_str());
    } else {
        Serial.print("null");
    }
    Serial.print(",\"predicted\":");
    if (r.ok) {
        printJsonString(r.label.c_str());
    } else {
        Serial.print("null");
    }
    Serial.print(",\"correct\":");
    Serial.print(r.correctLabel ? "true" : "false");
    Serial.print(",\"trusted\":");
    Serial.print(r.trusted ? "true" : "false");
    Serial.print(",\"accuracy\":");
    Serial.print(r.correctLabel ? 1.0f : 0.0f, 1);
    Serial.print(",\"accuracy_percent\":");
    Serial.print(r.correctLabel ? 100 : 0);
    Serial.print(",\"confidence\":");
    Serial.print(r.confidence, 6);
    Serial.print(",\"confidence_threshold\":");
    Serial.print(CONFIDENCE_THRESHOLD, 6);
    Serial.print(",\"inference_start_us\":");
    Serial.print(r.inferenceStartUs);
    Serial.print(",\"inference_end_us\":");
    Serial.print(r.inferenceEndUs);
    Serial.print(",\"inference_ms\":");
    Serial.print(r.inferenceMs, 3);
    Serial.print(",\"timing_ms\":{\"wall\":");
    Serial.print(r.inferenceMs, 3);
    Serial.print(",\"dsp\":");
    Serial.print(r.dspMs);
    Serial.print(",\"classification\":");
    Serial.print(r.classificationMs);
    Serial.print(",\"anomaly\":");
    Serial.print(r.anomalyMs);
    Serial.print("}");
    Serial.print(",\"memory_bytes\":{\"free_before\":");
    printJsonNullableInt(r.memoryBeforeBytes);
    Serial.print(",\"free_after\":");
    printJsonNullableInt(r.memoryAfterBytes);
    Serial.print(",\"free_delta\":");
    if (r.memoryBeforeBytes >= 0 && r.memoryAfterBytes >= 0) {
        Serial.print(r.memoryDeltaBytes);
    } else {
        Serial.print("null");
    }
    Serial.print("}");
    Serial.print(",\"scores\":");
    printScoresJson(r);
    Serial.print(",\"error_code\":");
    if (r.ok) {
        Serial.print("null");
    } else {
        Serial.print(r.errorCode);
    }
    Serial.print(",\"error\":");
    if (r.ok) {
        Serial.print("null");
    } else {
        printJsonString("classifier_failed");
    }
    Serial.println("}");
}

void printScoresJson(const InferenceResult &r) {
    Serial.print("{");
    for (size_t ix = 0; ix < EI_CLASSIFIER_LABEL_COUNT; ix++) {
        if (ix > 0) Serial.print(",");
        printJsonString(ei_classifier_inferencing_categories[ix]);
        Serial.print(":");
        Serial.print(r.scores[ix], 6);
    }
    Serial.print("}");
}

void printJsonString(const char *value) {
    Serial.print("\"");
    if (value != NULL) {
        for (const char *p = value; *p != '\0'; p++) {
            const char c = *p;
            if (c == '"' || c == '\\') {
                Serial.print("\\");
                Serial.print(c);
            } else if (c == '\n') {
                Serial.print("\\n");
            } else if (c == '\r') {
                Serial.print("\\r");
            } else if (c == '\t') {
                Serial.print("\\t");
            } else {
                Serial.print(c);
            }
        }
    }
    Serial.print("\"");
}

void printJsonNullableInt(int32_t value) {
    if (value >= 0) {
        Serial.print(value);
    } else {
        Serial.print("null");
    }
}

int32_t getFreeMemoryBytes() {
#if defined(ARDUINO_ARCH_RP2040)
    return (int32_t)rp2040.getFreeHeap();
#elif defined(ARDUINO_ARCH_ESP32) || defined(ESP32)
    return (int32_t)ESP.getFreeHeap();
#else
    return -1;
#endif
}

void collectGestureData() {
    size_t featureIndex = 0;
    float ax, ay, az, gx, gy, gz;
    size_t totalSamples = EI_CLASSIFIER_RAW_SAMPLE_COUNT;

    for (size_t s = 0; s < totalSamples; s++) {
        unsigned long sampleStart = micros();
        readIMUData(&ax, &ay, &az, &gx, &gy, &gz);
        
        features[featureIndex++] = ax * G_TO_MS2;
        features[featureIndex++] = ay * G_TO_MS2;
        features[featureIndex++] = az * G_TO_MS2;
        features[featureIndex++] = gx;
        features[featureIndex++] = gy;
        features[featureIndex++] = gz;

        while (micros() - sampleStart < (EI_CLASSIFIER_INTERVAL_MS * 1000));
    }
}

void reinitializeSensor() {
    printStatusJson("sensor_reset", "flushing");
    writeRegister(ICM20948_PWR_MGMT_1, 0x80); 
    delay(100);
    Wire.end();
    delay(50);
    Wire.begin();
    initICM20948();
}

void readIMUData(float* ax, float* ay, float* az, float* gx, float* gy, float* gz) {
    uint8_t rawData[12];
    Wire.beginTransmission(ICM20948_ADDR);
    Wire.write(ICM20948_ACCEL_XOUT_H);
    Wire.endTransmission(false);
    Wire.requestFrom(ICM20948_ADDR, 6);
    for (int i = 0; i < 6; i++) rawData[i] = Wire.read();

    Wire.beginTransmission(ICM20948_ADDR);
    Wire.write(ICM20948_GYRO_XOUT_H);
    Wire.endTransmission(false);
    Wire.requestFrom(ICM20948_ADDR, 6);
    for (int i = 6; i < 12; i++) rawData[i] = Wire.read();

    *ax = (int16_t)((rawData[0] << 8) | rawData[1]) / 16384.0;
    *ay = (int16_t)((rawData[2] << 8) | rawData[3]) / 16384.0;
    *az = (int16_t)((rawData[4] << 8) | rawData[5]) / 16384.0;
    *gx = (int16_t)((rawData[6] << 8) | rawData[7]) / 131.0;
    *gy = (int16_t)((rawData[8] << 8) | rawData[9]) / 131.0;
    *gz = (int16_t)((rawData[10] << 8) | rawData[11]) / 131.0;
}

bool initICM20948() {
    Wire.beginTransmission(ICM20948_ADDR);
    Wire.write(ICM20948_WHO_AM_I);
    Wire.endTransmission(false);
    Wire.requestFrom(ICM20948_ADDR, 1);
    if (Wire.read() != 0xEA) return false;
    writeRegister(ICM20948_PWR_MGMT_1, 0x01); 
    delay(10);
    writeRegister(ICM20948_PWR_MGMT_2, 0x00); 
    return true;
}

void writeRegister(uint8_t reg, uint8_t value) {
    Wire.beginTransmission(ICM20948_ADDR);
    Wire.write(reg);
    Wire.write(value);
    Wire.endTransmission();
}

int raw_feature_get_data(size_t offset, size_t length, float *out_ptr) {
    memcpy(out_ptr, features + offset, length * sizeof(float));
    return 0;
}

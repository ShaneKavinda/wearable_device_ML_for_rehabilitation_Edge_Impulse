#include <ei_gesture_left_hand_imu.h>
#include <Wire.h>

#define ICM20948_ADDR 0x68
#define ICM20948_WHO_AM_I 0x00
#define ICM20948_PWR_MGMT_1 0x06
#define ICM20948_PWR_MGMT_2 0x07
#define ICM20948_ACCEL_XOUT_H 0x2D
#define ICM20948_GYRO_XOUT_H 0x33

#ifndef EI_CLASSIFIER_RAW_SAMPLE_COUNT
#define EI_CLASSIFIER_RAW_SAMPLE_COUNT (EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE / 6)
#endif

#ifndef EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME
#define EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME 6
#endif

#if EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME != 6
#error "This sketch expects a 6-axis IMU model: acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z."
#endif

const char DEVICE_ID[] = "xiao_esp32s3_001";
const uint8_t XIAO_I2C_SDA = 5;        // XIAO ESP32S3 D4 / GPIO5
const uint8_t XIAO_I2C_SCL = 6;        // XIAO ESP32S3 D5 / GPIO6
const uint32_t I2C_FREQ_HZ = 400000;
const uint32_t SERIAL_WAIT_TIMEOUT_MS = 3000;
const uint32_t SAMPLE_INTERVAL_US = (uint32_t)(EI_CLASSIFIER_INTERVAL_MS * 1000.0f + 0.5f);

float features[EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE];
uint32_t windowId = 0;
bool imuReady = false;

struct ImuSample {
    float ax;
    float ay;
    float az;
    float gx;
    float gy;
    float gz;
};

struct WindowTiming {
    uint32_t collectStartUs;
    uint32_t collectEndUs;
};

bool configureI2C();
bool initICM20948();
bool reinitializeSensor();
bool writeRegister(uint8_t reg, uint8_t value);
bool readRegister(uint8_t reg, uint8_t *value);
bool readRegisterBlock(uint8_t startReg, uint8_t *data, size_t length);
bool readIMUData(ImuSample *sample);
bool collectGestureData(WindowTiming *timing);
bool runInference(ei_impulse_result_t *result, float *inferenceMs);
void handleSampleRequest();
void printStartupStatus();
void printStatusLine(const char *message);
void printErrorResult(uint32_t id, bool imuOk, const char *error);
void printInferenceResult(uint32_t id, bool imuOk, const WindowTiming &timing, float inferenceMs, const ei_impulse_result_t &result);
void printJsonHeader(uint32_t id, bool ok, bool imuOk);
void printZeroScores();
void printScores(const ei_impulse_result_t &result);
void printJsonString(const char *value);
int raw_feature_get_data(size_t offset, size_t length, float *out_ptr);

void setup() {
    Serial.begin(115200);

    const uint32_t serialWaitStart = millis();
    while (!Serial && (millis() - serialWaitStart < SERIAL_WAIT_TIMEOUT_MS)) {
        delay(10);
    }

    imuReady = configureI2C() && initICM20948();
    printStartupStatus();
}

void loop() {
    if (!Serial.available()) {
        return;
    }

    const char command = Serial.read();
    while (Serial.available()) {
        const char discard = Serial.peek();
        if (discard == '\n' || discard == '\r' || discard == ' ') {
            Serial.read();
        } else {
            break;
        }
    }

    if (command == 's' || command == 'S') {
        handleSampleRequest();
    } else if (command == 'r' || command == 'R') {
        imuReady = reinitializeSensor();
        printStatusLine(imuReady ? "IMU reinitialized" : "IMU reinitialize failed");
    }
}

void handleSampleRequest() {
    const uint32_t id = ++windowId;

    if (!imuReady) {
        imuReady = reinitializeSensor();
    }
    if (!imuReady) {
        printErrorResult(id, false, "imu_not_ready");
        return;
    }

    WindowTiming timing = {0, 0};
    if (!collectGestureData(&timing)) {
        imuReady = false;
        printErrorResult(id, false, "imu_read_failed");
        return;
    }

    ei_impulse_result_t result = {0};
    float inferenceMs = 0.0f;
    if (!runInference(&result, &inferenceMs)) {
        printErrorResult(id, true, "classifier_failed");
        return;
    }

    printInferenceResult(id, true, timing, inferenceMs, result);
}

bool configureI2C() {
    Wire.end();
    Wire.begin(XIAO_I2C_SDA, XIAO_I2C_SCL, I2C_FREQ_HZ);
    return true;
}

bool initICM20948() {
    uint8_t whoAmI = 0;
    if (!readRegister(ICM20948_WHO_AM_I, &whoAmI)) {
        return false;
    }
    if (whoAmI != 0xEA) {
        return false;
    }

    if (!writeRegister(ICM20948_PWR_MGMT_1, 0x01)) {
        return false;
    }
    delay(10);

    return writeRegister(ICM20948_PWR_MGMT_2, 0x00);
}

bool reinitializeSensor() {
    if (imuReady) {
        writeRegister(ICM20948_PWR_MGMT_1, 0x80);
        delay(100);
    }

    if (!configureI2C()) {
        return false;
    }
    delay(50);

    return initICM20948();
}

bool writeRegister(uint8_t reg, uint8_t value) {
    Wire.beginTransmission(ICM20948_ADDR);
    Wire.write(reg);
    Wire.write(value);
    return Wire.endTransmission() == 0;
}

bool readRegister(uint8_t reg, uint8_t *value) {
    if (!readRegisterBlock(reg, value, 1)) {
        return false;
    }
    return true;
}

bool readRegisterBlock(uint8_t startReg, uint8_t *data, size_t length) {
    Wire.beginTransmission(ICM20948_ADDR);
    Wire.write(startReg);
    if (Wire.endTransmission(false) != 0) {
        return false;
    }

    const size_t bytesRead = Wire.requestFrom((uint8_t)ICM20948_ADDR, (uint8_t)length);
    if (bytesRead != length) {
        while (Wire.available()) {
            Wire.read();
        }
        return false;
    }

    for (size_t i = 0; i < length; i++) {
        if (!Wire.available()) {
            return false;
        }
        data[i] = Wire.read();
    }

    return true;
}

bool readIMUData(ImuSample *sample) {
    uint8_t accelData[6];
    uint8_t gyroData[6];

    if (!readRegisterBlock(ICM20948_ACCEL_XOUT_H, accelData, sizeof(accelData))) {
        return false;
    }
    if (!readRegisterBlock(ICM20948_GYRO_XOUT_H, gyroData, sizeof(gyroData))) {
        return false;
    }

    const int16_t axRaw = (int16_t)((accelData[0] << 8) | accelData[1]);
    const int16_t ayRaw = (int16_t)((accelData[2] << 8) | accelData[3]);
    const int16_t azRaw = (int16_t)((accelData[4] << 8) | accelData[5]);
    const int16_t gxRaw = (int16_t)((gyroData[0] << 8) | gyroData[1]);
    const int16_t gyRaw = (int16_t)((gyroData[2] << 8) | gyroData[3]);
    const int16_t gzRaw = (int16_t)((gyroData[4] << 8) | gyroData[5]);

    sample->ax = axRaw / 16384.0f;
    sample->ay = ayRaw / 16384.0f;
    sample->az = azRaw / 16384.0f;
    sample->gx = gxRaw / 131.0f;
    sample->gy = gyRaw / 131.0f;
    sample->gz = gzRaw / 131.0f;

    return true;
}

bool collectGestureData(WindowTiming *timing) {
    size_t featureIndex = 0;
    timing->collectStartUs = micros();

    for (size_t sampleIndex = 0; sampleIndex < EI_CLASSIFIER_RAW_SAMPLE_COUNT; sampleIndex++) {
        const uint32_t sampleStartUs = micros();
        ImuSample sample;

        if (!readIMUData(&sample)) {
            return false;
        }

        features[featureIndex++] = sample.ax;
        features[featureIndex++] = sample.ay;
        features[featureIndex++] = sample.az;
        features[featureIndex++] = sample.gx;
        features[featureIndex++] = sample.gy;
        features[featureIndex++] = sample.gz;

        while ((uint32_t)(micros() - sampleStartUs) < SAMPLE_INTERVAL_US) {
            delayMicroseconds(50);
        }
    }

    timing->collectEndUs = micros();
    return featureIndex == EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE;
}

bool runInference(ei_impulse_result_t *result, float *inferenceMs) {
    signal_t signal;
    signal.total_length = EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE;
    signal.get_data = &raw_feature_get_data;

    const uint32_t inferenceStartUs = micros();
    const EI_IMPULSE_ERROR error = run_classifier(&signal, result, false);
    const uint32_t inferenceEndUs = micros();
    *inferenceMs = (float)(inferenceEndUs - inferenceStartUs) / 1000.0f;

    return error == EI_IMPULSE_OK;
}

void printStartupStatus() {
    Serial.print("XIAO ESP32S3 onboard inference ready; imu=");
    Serial.println(imuReady ? "ok" : "failed");
}

void printStatusLine(const char *message) {
    Serial.print("status: ");
    Serial.println(message);
}

void printErrorResult(uint32_t id, bool imuOk, const char *error) {
    printJsonHeader(id, false, imuOk);
    Serial.print(",\"t_collect_start_us\":0");
    Serial.print(",\"t_collect_end_us\":0");
    Serial.print(",\"collect_ms\":0.000");
    Serial.print(",\"inference_ms\":0.000");
    Serial.print(",\"label\":null");
    Serial.print(",\"confidence\":0.000000");
    Serial.print(",\"scores\":");
    printZeroScores();
    Serial.print(",\"error\":");
    printJsonString(error);
    Serial.println("}");
}

void printInferenceResult(uint32_t id, bool imuOk, const WindowTiming &timing, float inferenceMs, const ei_impulse_result_t &result) {
    int maxIndex = -1;
    float maxScore = -1.0f;

    for (size_t ix = 0; ix < EI_CLASSIFIER_LABEL_COUNT; ix++) {
        if (result.classification[ix].value > maxScore) {
            maxScore = result.classification[ix].value;
            maxIndex = (int)ix;
        }
    }

    const uint32_t collectUs = timing.collectEndUs - timing.collectStartUs;

    printJsonHeader(id, true, imuOk);
    Serial.print(",\"t_collect_start_us\":");
    Serial.print(timing.collectStartUs);
    Serial.print(",\"t_collect_end_us\":");
    Serial.print(timing.collectEndUs);
    Serial.print(",\"collect_ms\":");
    Serial.print((float)collectUs / 1000.0f, 3);
    Serial.print(",\"inference_ms\":");
    Serial.print(inferenceMs, 3);
    Serial.print(",\"label\":");
    if (maxIndex >= 0) {
        printJsonString(result.classification[maxIndex].label);
    } else {
        Serial.print("null");
    }
    Serial.print(",\"confidence\":");
    Serial.print(maxScore < 0.0f ? 0.0f : maxScore, 6);
    Serial.print(",\"scores\":");
    printScores(result);
    Serial.print(",\"error\":null");
    Serial.println("}");
}

void printJsonHeader(uint32_t id, bool ok, bool imuOk) {
    Serial.print("{\"type\":\"inference_result\"");
    Serial.print(",\"device_id\":\"");
    Serial.print(DEVICE_ID);
    Serial.print("\"");
    Serial.print(",\"window_id\":");
    Serial.print(id);
    Serial.print(",\"ok\":");
    Serial.print(ok ? "true" : "false");
    Serial.print(",\"imu_ok\":");
    Serial.print(imuOk ? "true" : "false");
    Serial.print(",\"sample_count\":");
    Serial.print((uint32_t)EI_CLASSIFIER_RAW_SAMPLE_COUNT);
    Serial.print(",\"axes_per_sample\":");
    Serial.print((uint32_t)EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME);
    Serial.print(",\"input_frame_size\":");
    Serial.print((uint32_t)EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE);
    Serial.print(",\"sample_interval_ms\":");
    Serial.print((double)EI_CLASSIFIER_INTERVAL_MS, 14);
}

void printZeroScores() {
    Serial.print("{");
    for (size_t ix = 0; ix < EI_CLASSIFIER_LABEL_COUNT; ix++) {
        if (ix > 0) {
            Serial.print(",");
        }
        printJsonString(ei_classifier_inferencing_categories[ix]);
        Serial.print(":0.000000");
    }
    Serial.print("}");
}

void printScores(const ei_impulse_result_t &result) {
    Serial.print("{");
    for (size_t ix = 0; ix < EI_CLASSIFIER_LABEL_COUNT; ix++) {
        if (ix > 0) {
            Serial.print(",");
        }
        printJsonString(result.classification[ix].label);
        Serial.print(":");
        Serial.print(result.classification[ix].value, 6);
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

int raw_feature_get_data(size_t offset, size_t length, float *out_ptr) {
    memcpy(out_ptr, features + offset, length * sizeof(float));
    return 0;
}

#include <Arduino.h>
#include <Wire.h>

#if !defined(ESP32)
#error "imu_datastream_ble targets ESP32 BLE boards such as the Seeed Studio XIAO ESP32S3."
#endif

#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

#include <ei_gesture_left_hand_imu.h>

#define ICM20948_ADDR 0x68
#define ICM20948_WHO_AM_I 0x00
#define ICM20948_PWR_MGMT_1 0x06
#define ICM20948_PWR_MGMT_2 0x07
#define ICM20948_ACCEL_XOUT_H 0x2D
#define ICM20948_GYRO_XOUT_H 0x33

#ifndef CONFIDENCE_THRESHOLD
#define CONFIDENCE_THRESHOLD 0.85f
#endif

#ifndef EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME
#define EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME 6
#endif

#ifndef EI_CLASSIFIER_RAW_SAMPLE_COUNT
#define EI_CLASSIFIER_RAW_SAMPLE_COUNT (EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE / EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME)
#endif

#if EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME != 6
#error "This sketch expects 6 IMU axes per sample: acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z."
#endif

static_assert(EI_CLASSIFIER_PROJECT_ID == 738400, "Install Edge Impulse project 738400.");
static_assert(EI_CLASSIFIER_PROJECT_DEPLOY_VERSION == 19, "Install deployment 19.");
static_assert(EI_CLASSIFIER_RAW_SAMPLE_COUNT == 33, "Deployment 19 requires 33 samples.");
static_assert(EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE == 198, "Deployment 19 requires 198 features.");
static_assert(EI_CLASSIFIER_LABEL_COUNT == 6, "Deployment 19 requires six labels.");
static_assert(EI_CLASSIFIER_FREQUENCY > 16.499 && EI_CLASSIFIER_FREQUENCY < 16.501,
              "Deployment 19 requires a 16.5 Hz sampling rate.");

#ifndef IMU_I2C_SDA
#define IMU_I2C_SDA 5
#endif

#ifndef IMU_I2C_SCL
#define IMU_I2C_SCL 6
#endif

const char DEVICE_ID[] = "xiao_esp32s3_ble_001";
const char BLE_DEVICE_NAME[] = "IMU-Datastream";

// Nordic UART Service UUIDs. TX notifies the central; RX receives commands.
const char BLE_SERVICE_UUID[] = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E";
const char BLE_RX_UUID[] = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E";
const char BLE_TX_UUID[] = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E";
const char BLE_RESULT_UUID[] = "6E400004-B5A3-F393-E0A9-E50E24DCCA9E";

const char *GESTURE_LIST[] = {
    "Flexion",
    "Extension",
    "Pronation",
    "Supination",
    "Radial Deviation",
    "Ulnar Deviation",
};
const int NUM_GESTURES = 6;
const int SESSION_REPETITIONS = 10;
const uint32_t I2C_FREQ_HZ = 400000;
const uint32_t SERIAL_WAIT_TIMEOUT_MS = 1500;
const uint32_t SAMPLE_INTERVAL_US = (uint32_t)(EI_CLASSIFIER_INTERVAL_MS * 1000.0f + 0.5f);
const size_t BLE_NOTIFY_CHUNK_BYTES = 20;
const uint32_t BLE_NOTIFY_DELAY_MS = 3;

struct InferenceResult {
    String label;
    float confidence;
    float scores[EI_CLASSIFIER_LABEL_COUNT];
    bool trusted;
    bool correctLabel;
    bool ok;
    int errorCode;
    uint32_t collectStartUs;
    uint32_t collectEndUs;
    uint32_t inferenceStartUs;
    uint32_t inferenceEndUs;
    float collectMs;
    float inferenceMs;
    int dspMs;
    int classificationMs;
    int anomalyMs;
    int32_t memoryBeforeBytes;
    int32_t memoryAfterBytes;
    int32_t memoryDeltaBytes;
};

struct ImuSample {
    float ax;
    float ay;
    float az;
    float gx;
    float gy;
    float gz;
};

struct __attribute__((packed)) ResultPacket {
    uint8_t version;
    uint8_t deployment;
    uint16_t flags;
    uint32_t windowId;
    uint32_t sourceSequence;
    uint32_t inferenceUs;
    uint16_t confidenceQ15;
    uint8_t repetition;
    uint8_t predictedClass;
};

static_assert(sizeof(ResultPacket) == 20, "ResultPacket must be exactly 20 bytes");

float features[EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE];
String targetGesture = "";
uint32_t windowId = 0;
uint32_t sampleSequence = 0;
bool imuReady = false;
bool bleConnected = false;
bool shouldRestartAdvertising = false;
bool menuRequested = false;
bool resetRequested = false;
bool sessionRunning = false;
volatile bool stopRequested = false;
volatile int pendingSelection = 0;
volatile bool pendingCapture = false;
volatile uint32_t pendingCaptureWindowId = 0;
volatile int pendingCaptureSelection = 0;
volatile int pendingCaptureRepetition = 0;

BLECharacteristic *txCharacteristic = nullptr;
BLECharacteristic *resultCharacteristic = nullptr;

bool configureI2C();
bool initICM20948();
bool reinitializeSensor(bool emitStatus = true);
bool writeRegister(uint8_t reg, uint8_t value);
bool readRegister(uint8_t reg, uint8_t *value);
bool readRegisterBlock(uint8_t startReg, uint8_t *data, size_t length);
bool readIMUData(ImuSample *sample);
bool collectGestureData(InferenceResult *r);
void runInference(InferenceResult *r, int repetition);
void startGestureSession(int selection);
void runSingleCapture(uint32_t requestedWindowId, int selection, int repetition);
void initBLE();
void handleCommandLine(const String &command);
void handleSerialCommands();
bool waitWithStop(uint32_t waitMs);
void sendStartupStatus();
void sendMenu();
void sendSessionStartJson(int repetitions);
void sendStatusJson(const char *event, const char *message);
void sendRepetitionEventJson(int repetition, const char *event);
void sendSessionSummaryJson(InferenceResult history[], int total);
void sendInferenceResultJson(int repetition, const InferenceResult &r);
void sendBinaryResult(int repetition, const InferenceResult &r);
int predictedClassIndex(const String &label);
void appendScoresJson(String *out, const InferenceResult &r);
void appendJsonString(String *out, const char *value);
void appendJsonNullableInt(String *out, int32_t value);
bool sendLine(const String &line);
bool notifyBuffer(const char *data, size_t length);
int32_t getFreeMemoryBytes();
int raw_feature_get_data(size_t offset, size_t length, float *out_ptr);

class ServerCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer *server) override {
        (void)server;
        bleConnected = true;
        stopRequested = false;
        menuRequested = true;
    }

    void onDisconnect(BLEServer *server) override {
        (void)server;
        bleConnected = false;
        stopRequested = true;
        pendingSelection = 0;
        pendingCapture = false;
        shouldRestartAdvertising = true;
    }
};

class RxCallbacks : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *characteristic) override {
        String value = characteristic->getValue().c_str();
        value.trim();
        handleCommandLine(value);
    }
};

void setup() {
    Serial.begin(115200);
    const uint32_t serialWaitStart = millis();
    while (!Serial && (millis() - serialWaitStart < SERIAL_WAIT_TIMEOUT_MS)) {
        delay(10);
    }

    imuReady = configureI2C() && initICM20948();
    initBLE();
    sendStartupStatus();
    sendMenu();
}

void loop() {
    if (shouldRestartAdvertising && !bleConnected) {
        shouldRestartAdvertising = false;
        delay(250);
        BLEDevice::startAdvertising();
        Serial.println("BLE advertising restarted");
    }

    handleSerialCommands();

    if (resetRequested && !sessionRunning) {
        resetRequested = false;
        imuReady = reinitializeSensor();
        sendMenu();
    }

    if (menuRequested && !sessionRunning) {
        menuRequested = false;
        sendMenu();
    }

    const int selection = pendingSelection;
    if (selection != 0 && !sessionRunning) {
        pendingSelection = 0;
        startGestureSession(selection);
    }

    if (pendingCapture && !sessionRunning) {
        const uint32_t requestedWindowId = pendingCaptureWindowId;
        const int requestedSelection = pendingCaptureSelection;
        const int requestedRepetition = pendingCaptureRepetition;
        pendingCapture = false;
        runSingleCapture(
            requestedWindowId,
            requestedSelection,
            requestedRepetition
        );
    }

    delay(20);
}

void initBLE() {
    BLEDevice::init(BLE_DEVICE_NAME);
    BLEDevice::setMTU(185);

    BLEServer *server = BLEDevice::createServer();
    server->setCallbacks(new ServerCallbacks());

    BLEService *service = server->createService(BLE_SERVICE_UUID);

    txCharacteristic = service->createCharacteristic(
        BLE_TX_UUID,
        BLECharacteristic::PROPERTY_NOTIFY
    );
    txCharacteristic->addDescriptor(new BLE2902());

    resultCharacteristic = service->createCharacteristic(
        BLE_RESULT_UUID,
        BLECharacteristic::PROPERTY_NOTIFY
    );
    resultCharacteristic->addDescriptor(new BLE2902());

    BLECharacteristic *rxCharacteristic = service->createCharacteristic(
        BLE_RX_UUID,
        BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
    );
    rxCharacteristic->setCallbacks(new RxCallbacks());

    service->start();

    BLEAdvertising *advertising = BLEDevice::getAdvertising();

    BLEAdvertisementData advertisementData;
    advertisementData.setFlags(0x06);
    advertisementData.setCompleteServices(BLEUUID(BLE_SERVICE_UUID));
    advertising->setAdvertisementData(advertisementData);

    BLEAdvertisementData scanResponseData;
    scanResponseData.setName(BLE_DEVICE_NAME);
    advertising->setScanResponseData(scanResponseData);

    advertising->setScanResponse(true);
    advertising->setMinPreferred(0x06);
    advertising->setMinPreferred(0x12);
    BLEDevice::startAdvertising();
    Serial.println("BLE advertising as IMU-Datastream");
}

void handleCommandLine(const String &rawCommand) {
    String command = rawCommand;
    command.trim();
    if (command.length() == 0) {
        return;
    }

    unsigned long parsedWindowId = 0;
    int requestedSelection = 0;
    int requestedRepetition = 0;
    if (sscanf(
            command.c_str(),
            "capture %lu %d %d",
            &parsedWindowId,
            &requestedSelection,
            &requestedRepetition
        ) == 3) {
        if (requestedSelection < 1 || requestedSelection > NUM_GESTURES ||
            requestedRepetition < 1 || requestedRepetition > SESSION_REPETITIONS) {
            sendStatusJson("invalid_capture", "capture requires window_id, selection 1-6, repetition 1-10");
            return;
        }
        if (sessionRunning || pendingCapture) {
            sendStatusJson("busy", "capture_already_running");
            return;
        }
        pendingCaptureWindowId = (uint32_t)parsedWindowId;
        pendingCaptureSelection = requestedSelection;
        pendingCaptureRepetition = requestedRepetition;
        pendingCapture = true;
        return;
    }

    if (command.length() == 1 && command.charAt(0) >= '0' && command.charAt(0) <= '9') {
        pendingSelection = command.charAt(0) - '0';
        return;
    }

    const char first = command.charAt(0);
    switch (first) {
        case 'm':
        case 'M':
        case 'h':
        case 'H':
        case '?':
            menuRequested = true;
            break;
        case 'r':
        case 'R':
            resetRequested = true;
            break;
        case 'x':
        case 'X':
            stopRequested = true;
            sendStatusJson("stop_requested", "session_stop_requested");
            break;
        default:
            sendStatusJson("unknown_command", "send capture <window> <selection> <repetition>, 1-6 for legacy session, m, r, or x");
            break;
    }
}

void handleSerialCommands() {
    static String serialCommand;
    while (Serial.available() > 0) {
        const char c = (char)Serial.read();
        if (c == '\n' || c == '\r') {
            if (serialCommand.length() > 0) {
                handleCommandLine(serialCommand);
                serialCommand = "";
            }
        } else if (serialCommand.length() < 96) {
            serialCommand += c;
        } else {
            serialCommand = "";
            sendStatusJson("invalid_command", "command_too_long");
        }
    }
}

void startGestureSession(int selection) {
    if (selection < 1 || selection > NUM_GESTURES) {
        sendStatusJson("invalid_selection", "select_a_number_1_to_6");
        sendMenu();
        return;
    }

    sessionRunning = true;
    stopRequested = false;
    targetGesture = GESTURE_LIST[selection - 1];
    windowId = 0;

    if (!imuReady) {
        imuReady = reinitializeSensor();
    }
    if (!imuReady) {
        sendStatusJson("session_failed", "imu_not_ready");
        sessionRunning = false;
        sendMenu();
        return;
    }

    sendSessionStartJson(SESSION_REPETITIONS);
    InferenceResult history[SESSION_REPETITIONS];
    int completed = 0;

    for (int i = 0; i < SESSION_REPETITIONS; i++) {
        if (stopRequested || !bleConnected) {
            break;
        }

        sendRepetitionEventJson(i + 1, "get_ready");
        if (!waitWithStop(1000)) break;
        sendRepetitionEventJson(i + 1, "countdown_3");
        if (!waitWithStop(1000)) break;
        sendRepetitionEventJson(i + 1, "countdown_2");
        if (!waitWithStop(1000)) break;
        sendRepetitionEventJson(i + 1, "countdown_1");
        if (!waitWithStop(1000)) break;

        sendRepetitionEventJson(i + 1, "movement_start");
        windowId++;
        collectGestureData(&history[i]);
        sendRepetitionEventJson(i + 1, "sampling_finished");
        runInference(&history[i], i + 1);
        sendBinaryResult(i + 1, history[i]);
        sendInferenceResultJson(i + 1, history[i]);
        completed++;

        reinitializeSensor(false);

        if (i < SESSION_REPETITIONS - 1) {
            sendRepetitionEventJson(i + 1, "next_rep_in_2s");
            if (!waitWithStop(2000)) break;
        }
    }

    if (completed > 0) {
        sendSessionSummaryJson(history, completed);
    }
    if (stopRequested && bleConnected) {
        sendStatusJson("session_stopped", "session_stopped_before_completion");
    }

    sessionRunning = false;
    stopRequested = false;
    sendMenu();
}

void runSingleCapture(uint32_t requestedWindowId, int selection, int repetition) {
    if (selection < 1 || selection > NUM_GESTURES) {
        sendStatusJson("invalid_selection", "select_a_number_1_to_6");
        return;
    }

    sessionRunning = true;
    stopRequested = false;
    targetGesture = GESTURE_LIST[selection - 1];
    windowId = requestedWindowId;

    if (!imuReady) {
        imuReady = reinitializeSensor();
    }
    if (!imuReady) {
        sendStatusJson("capture_failed", "imu_not_ready");
        sessionRunning = false;
        return;
    }

    InferenceResult result = {};
    collectGestureData(&result);
    runInference(&result, repetition);
    sendBinaryResult(repetition, result);
    sendInferenceResultJson(repetition, result);

    sessionRunning = false;
    stopRequested = false;
}

bool waitWithStop(uint32_t waitMs) {
    const uint32_t stepMs = 20;
    const uint32_t startedAt = millis();
    while ((uint32_t)(millis() - startedAt) < waitMs) {
        handleSerialCommands();
        if (stopRequested || !bleConnected) {
            return false;
        }
        delay(stepMs);
    }
    return true;
}

bool configureI2C() {
    Wire.end();
    Wire.begin(IMU_I2C_SDA, IMU_I2C_SCL, I2C_FREQ_HZ);
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

bool reinitializeSensor(bool emitStatus) {
    if (emitStatus) {
        sendStatusJson("sensor_reset", "flushing");
    }

    if (imuReady) {
        writeRegister(ICM20948_PWR_MGMT_1, 0x80);
        delay(100);
    }

    if (!configureI2C()) {
        if (emitStatus) {
            sendStatusJson("sensor_reset", "i2c_failed");
        }
        return false;
    }
    delay(50);

    const bool ready = initICM20948();
    imuReady = ready;
    if (emitStatus) {
        sendStatusJson("sensor_reset", ready ? "ok" : "failed");
    }
    return ready;
}

bool writeRegister(uint8_t reg, uint8_t value) {
    Wire.beginTransmission(ICM20948_ADDR);
    Wire.write(reg);
    Wire.write(value);
    return Wire.endTransmission() == 0;
}

bool readRegister(uint8_t reg, uint8_t *value) {
    return readRegisterBlock(reg, value, 1);
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

bool collectGestureData(InferenceResult *r) {
    size_t featureIndex = 0;
    r->ok = false;
    r->errorCode = 0;
    r->collectStartUs = micros();

    for (size_t sampleIndex = 0; sampleIndex < EI_CLASSIFIER_RAW_SAMPLE_COUNT; sampleIndex++) {
        const uint32_t sampleStartUs = micros();
        ImuSample sample;

        if (!readIMUData(&sample)) {
            r->collectEndUs = micros();
            r->collectMs = (float)((uint32_t)(r->collectEndUs - r->collectStartUs)) / 1000.0f;
            r->ok = false;
            r->errorCode = -1;
            return false;
        }

        // Training data uses acceleration in g and gyro in degrees/second.
        // The model's raw DSP scale is 1.0, so do not convert g to m/s^2.
        features[featureIndex++] = sample.ax;
        features[featureIndex++] = sample.ay;
        features[featureIndex++] = sample.az;
        features[featureIndex++] = sample.gx;
        features[featureIndex++] = sample.gy;
        features[featureIndex++] = sample.gz;

        sampleSequence++;
        if (sampleIndex + 1 < EI_CLASSIFIER_RAW_SAMPLE_COUNT) {
            while ((uint32_t)(micros() - sampleStartUs) < SAMPLE_INTERVAL_US) {
                delayMicroseconds(50);
            }
        }
    }

    r->collectEndUs = micros();
    r->collectMs = (float)((uint32_t)(r->collectEndUs - r->collectStartUs)) / 1000.0f;
    return featureIndex == EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE;
}

void runInference(InferenceResult *r, int repetition) {
    const bool collectFailed = r->errorCode == -1;
    r->label = "ERR";
    r->confidence = 0.0f;
    r->trusted = false;
    r->correctLabel = false;
    r->ok = false;
    r->errorCode = collectFailed ? -1 : 0;
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

    if (collectFailed) {
        return;
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
        return;
    }

    float maxScore = -1.0f;
    int maxIndex = -1;
    for (size_t ix = 0; ix < EI_CLASSIFIER_LABEL_COUNT; ix++) {
        r->scores[ix] = result.classification[ix].value;
        if (result.classification[ix].value > maxScore) {
            maxScore = result.classification[ix].value;
            maxIndex = (int)ix;
        }
    }

    r->label = maxIndex >= 0 ? String(result.classification[maxIndex].label) : "unknown";
    r->confidence = maxScore < 0.0f ? 0.0f : maxScore;
    r->trusted = r->confidence >= CONFIDENCE_THRESHOLD;
    r->ok = true;
    r->dspMs = result.timing.dsp;
    r->classificationMs = result.timing.classification;
    r->anomalyMs = result.timing.anomaly;
    r->correctLabel = r->label.equalsIgnoreCase(targetGesture);

}

void sendStartupStatus() {
    sendStatusJson("startup", imuReady ? "system_ready" : "sensor_connection_failed");
}

void sendMenu() {
    String line;
    line.reserve(420);
    line += "{\"type\":\"gesture_menu\",\"device_id\":";
    appendJsonString(&line, DEVICE_ID);
    line += ",\"prompt\":\"select_gesture_1_to_6\",\"gestures\":[";
    for (int i = 0; i < NUM_GESTURES; i++) {
        if (i > 0) {
            line += ",";
        }
        line += "{\"selection\":";
        line += String(i + 1);
        line += ",\"label\":";
        appendJsonString(&line, GESTURE_LIST[i]);
        line += "}";
    }
    line += "]}";
    sendLine(line);
}

void sendSessionStartJson(int repetitions) {
    String line;
    line.reserve(160);
    line += "{\"type\":\"session_start\",\"device_id\":";
    appendJsonString(&line, DEVICE_ID);
    line += ",\"target\":";
    appendJsonString(&line, targetGesture.c_str());
    line += ",\"repetitions\":";
    line += String(repetitions);
    line += "}";
    sendLine(line);
}

void sendStatusJson(const char *event, const char *message) {
    String line;
    line.reserve(180);
    line += "{\"type\":\"status\",\"device_id\":";
    appendJsonString(&line, DEVICE_ID);
    line += ",\"event\":";
    appendJsonString(&line, event);
    line += ",\"message\":";
    appendJsonString(&line, message);
    line += ",\"imu_ok\":";
    line += imuReady ? "true" : "false";
    line += ",\"session_running\":";
    line += sessionRunning ? "true" : "false";
    line += "}";
    sendLine(line);
}

void sendRepetitionEventJson(int repetition, const char *event) {
    String line;
    line.reserve(180);
    line += "{\"type\":\"repetition_event\",\"device_id\":";
    appendJsonString(&line, DEVICE_ID);
    line += ",\"target\":";
    appendJsonString(&line, targetGesture.c_str());
    line += ",\"repetition\":";
    line += String(repetition);
    line += ",\"event\":";
    appendJsonString(&line, event);
    line += "}";
    sendLine(line);
}

void sendSessionSummaryJson(InferenceResult history[], int total) {
    int successCount = 0;
    int correctCount = 0;
    int uncertainCount = 0;
    float totalSuccessConf = 0.0f;
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

    const float denominator = total > 0 ? (float)total : 1.0f;
    const float avgConf = successCount > 0 ? totalSuccessConf / successCount : 0.0f;
    const float avgInferenceMs = timedInferenceCount > 0 ? totalInferenceMs / timedInferenceCount : 0.0f;

    String line;
    line.reserve(420);
    line += "{\"type\":\"session_summary\",\"device_id\":";
    appendJsonString(&line, DEVICE_ID);
    line += ",\"target\":";
    appendJsonString(&line, targetGesture.c_str());
    line += ",\"total_repetitions\":";
    line += String(total);
    line += ",\"correct_count\":";
    line += String(correctCount);
    line += ",\"accuracy\":";
    line += String((float)correctCount / denominator, 6);
    line += ",\"accuracy_percent\":";
    line += String(((float)correctCount / denominator) * 100.0f, 1);
    line += ",\"pass_count\":";
    line += String(successCount);
    line += ",\"pass_rate\":";
    line += String((float)successCount / denominator, 6);
    line += ",\"pass_rate_percent\":";
    line += String(((float)successCount / denominator) * 100.0f, 1);
    line += ",\"uncertain_count\":";
    line += String(uncertainCount);
    line += ",\"avg_pass_confidence\":";
    line += String(avgConf, 6);
    line += ",\"avg_inference_ms\":";
    line += String(avgInferenceMs, 3);
    line += ",\"min_free_memory_bytes\":";
    appendJsonNullableInt(&line, minFreeMemory);
    line += "}";
    sendLine(line);
}

int predictedClassIndex(const String &label) {
    for (size_t ix = 0; ix < EI_CLASSIFIER_LABEL_COUNT; ix++) {
        if (label.equalsIgnoreCase(ei_classifier_inferencing_categories[ix])) {
            return (int)ix;
        }
    }
    return -1;
}

void sendBinaryResult(int repetition, const InferenceResult &r) {
    if (!bleConnected || resultCharacteristic == nullptr) {
        return;
    }

    ResultPacket packet = {};
    packet.version = 1;
    packet.deployment = 0;
    packet.flags = 0;
    if (r.ok) packet.flags |= 0x0001;
    if (r.trusted) packet.flags |= 0x0002;
    if (r.correctLabel) packet.flags |= 0x0004;
    packet.windowId = windowId;
    packet.sourceSequence = sampleSequence == 0 ? 0 : sampleSequence - 1;
    packet.inferenceUs = r.inferenceEndUs - r.inferenceStartUs;
    const float boundedConfidence = constrain(r.confidence, 0.0f, 1.0f);
    packet.confidenceQ15 = (uint16_t)(boundedConfidence * 32767.0f + 0.5f);
    packet.repetition = (uint8_t)constrain(repetition, 0, 255);
    const int classIndex = predictedClassIndex(r.label);
    packet.predictedClass = classIndex >= 0 ? (uint8_t)classIndex : 0xFF;

    resultCharacteristic->setValue(
        reinterpret_cast<uint8_t *>(&packet),
        sizeof(packet)
    );
    resultCharacteristic->notify();
}

void sendInferenceResultJson(int repetition, const InferenceResult &r) {
    String line;
    line.reserve(1100);
    line += "{\"type\":\"inference_result\",\"device_id\":";
    appendJsonString(&line, DEVICE_ID);
    line += ",\"repetition\":";
    line += String(repetition);
    line += ",\"window_id\":";
    line += String(windowId);
    line += ",\"deployment_id\":0";
    line += ",\"source_sequence\":";
    line += String(sampleSequence == 0 ? 0 : sampleSequence - 1);
    line += ",\"target\":";
    appendJsonString(&line, targetGesture.c_str());
    line += ",\"sample_count\":";
    line += String((uint32_t)EI_CLASSIFIER_RAW_SAMPLE_COUNT);
    line += ",\"axes_per_sample\":";
    line += String((uint32_t)EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME);
    line += ",\"input_frame_size\":";
    line += String((uint32_t)EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE);
    line += ",\"sample_interval_ms\":";
    line += String((double)EI_CLASSIFIER_INTERVAL_MS, 6);
    line += ",\"ok\":";
    line += r.ok ? "true" : "false";
    line += ",\"label\":";
    if (r.ok) {
        appendJsonString(&line, r.label.c_str());
    } else {
        line += "null";
    }
    line += ",\"predicted\":";
    if (r.ok) {
        appendJsonString(&line, r.label.c_str());
    } else {
        line += "null";
    }
    line += ",\"correct\":";
    line += r.correctLabel ? "true" : "false";
    line += ",\"trusted\":";
    line += r.trusted ? "true" : "false";
    line += ",\"accuracy\":";
    line += String(r.correctLabel ? 1.0f : 0.0f, 1);
    line += ",\"accuracy_percent\":";
    line += String(r.correctLabel ? 100 : 0);
    line += ",\"confidence\":";
    line += String(r.confidence, 6);
    line += ",\"confidence_threshold\":";
    line += String(CONFIDENCE_THRESHOLD, 6);
    line += ",\"t_collect_start_us\":";
    line += String(r.collectStartUs);
    line += ",\"t_collect_end_us\":";
    line += String(r.collectEndUs);
    line += ",\"collect_ms\":";
    line += String(r.collectMs, 3);
    line += ",\"inference_start_us\":";
    line += String(r.inferenceStartUs);
    line += ",\"inference_end_us\":";
    line += String(r.inferenceEndUs);
    line += ",\"inference_ms\":";
    line += String(r.inferenceMs, 3);
    line += ",\"timing_ms\":{\"wall\":";
    line += String(r.inferenceMs, 3);
    line += ",\"dsp\":";
    line += String(r.dspMs);
    line += ",\"classification\":";
    line += String(r.classificationMs);
    line += ",\"anomaly\":";
    line += String(r.anomalyMs);
    line += "}";
    line += ",\"memory_bytes\":{\"free_before\":";
    appendJsonNullableInt(&line, r.memoryBeforeBytes);
    line += ",\"free_after\":";
    appendJsonNullableInt(&line, r.memoryAfterBytes);
    line += ",\"free_delta\":";
    if (r.memoryBeforeBytes >= 0 && r.memoryAfterBytes >= 0) {
        line += String(r.memoryDeltaBytes);
    } else {
        line += "null";
    }
    line += "}";
    line += ",\"scores\":";
    appendScoresJson(&line, r);
    line += ",\"error_code\":";
    if (r.ok) {
        line += "null";
    } else {
        line += String(r.errorCode);
    }
    line += ",\"error\":";
    if (r.ok) {
        line += "null";
    } else if (r.errorCode == -1) {
        appendJsonString(&line, "imu_read_failed");
    } else {
        appendJsonString(&line, "classifier_failed");
    }
    line += "}";
    sendLine(line);
}

void appendScoresJson(String *out, const InferenceResult &r) {
    *out += "{";
    for (size_t ix = 0; ix < EI_CLASSIFIER_LABEL_COUNT; ix++) {
        if (ix > 0) {
            *out += ",";
        }
        appendJsonString(out, ei_classifier_inferencing_categories[ix]);
        *out += ":";
        *out += String(r.scores[ix], 6);
    }
    *out += "}";
}

void appendJsonString(String *out, const char *value) {
    *out += "\"";
    if (value != nullptr) {
        for (const char *p = value; *p != '\0'; p++) {
            const char c = *p;
            if (c == '"' || c == '\\') {
                *out += "\\";
                *out += c;
            } else if (c == '\n') {
                *out += "\\n";
            } else if (c == '\r') {
                *out += "\\r";
            } else if (c == '\t') {
                *out += "\\t";
            } else {
                *out += c;
            }
        }
    }
    *out += "\"";
}

void appendJsonNullableInt(String *out, int32_t value) {
    if (value >= 0) {
        *out += String(value);
    } else {
        *out += "null";
    }
}

bool sendLine(const String &line) {
    Serial.println(line);
    if (!bleConnected || txCharacteristic == nullptr) {
        return false;
    }

    if (!notifyBuffer(line.c_str(), line.length())) {
        return false;
    }
    return notifyBuffer("\n", 1);
}

bool notifyBuffer(const char *data, size_t length) {
    size_t offset = 0;
    while (offset < length) {
        if (!bleConnected || txCharacteristic == nullptr) {
            return false;
        }

        const size_t chunkLength = min(BLE_NOTIFY_CHUNK_BYTES, length - offset);
        txCharacteristic->setValue((uint8_t *)(data + offset), chunkLength);
        txCharacteristic->notify();
        offset += chunkLength;
        delay(BLE_NOTIFY_DELAY_MS);
    }

    return true;
}

int32_t getFreeMemoryBytes() {
#if defined(ESP32)
    return (int32_t)ESP.getFreeHeap();
#else
    return -1;
#endif
}

int raw_feature_get_data(size_t offset, size_t length, float *out_ptr) {
    memcpy(out_ptr, features + offset, length * sizeof(float));
    return 0;
}

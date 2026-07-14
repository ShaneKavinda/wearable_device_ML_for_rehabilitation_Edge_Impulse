#include <Arduino.h>
#include <Wire.h>

#if !defined(ESP32)
#error "This sketch targets ESP32 BLE boards such as the Seeed Studio XIAO ESP32S3."
#endif

#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

// ICM-20948 registers in user bank 0.
#define ICM20948_ADDR 0x68
#define ICM20948_WHO_AM_I 0x00
#define ICM20948_PWR_MGMT_1 0x06
#define ICM20948_PWR_MGMT_2 0x07
#define ICM20948_ACCEL_XOUT_H 0x2D

#ifndef IMU_I2C_SDA
#define IMU_I2C_SDA 5  // XIAO ESP32S3 D4 / GPIO5
#endif

#ifndef IMU_I2C_SCL
#define IMU_I2C_SCL 6  // XIAO ESP32S3 D5 / GPIO6
#endif

#ifndef SAMPLE_RATE_HZ
#define SAMPLE_RATE_HZ 50
#endif

#if SAMPLE_RATE_HZ < 1 || SAMPLE_RATE_HZ > 200
#error "SAMPLE_RATE_HZ must be between 1 and 200."
#endif

const char BLE_DEVICE_NAME[] = "IMU-Raw-Stream";

// Nordic UART Service. The Python client subscribes to the TX characteristic.
const char BLE_SERVICE_UUID[] = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E";
const char BLE_TX_UUID[] = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E";

const uint32_t I2C_FREQUENCY_HZ = 400000;
const uint32_t SAMPLE_INTERVAL_US = 1000000UL / SAMPLE_RATE_HZ;
const uint32_t SERIAL_WAIT_TIMEOUT_MS = 1500;
const uint32_t IMU_RETRY_INTERVAL_MS = 2000;

// Every BLE notification is exactly 20 bytes, so it also fits the default
// BLE ATT payload. ESP32 values are little-endian. In Python, decode with:
//
//   sequence, time_us, ax, ay, az, gx, gy, gz = struct.unpack("<II6h", data)
//
// Acceleration is in raw +/-2 g counts (divide by 16384 for g).
// Angular velocity is in raw +/-250 dps counts (divide by 131 for deg/s).
struct __attribute__((packed)) ImuPacket {
    uint32_t sequence;
    uint32_t timeUs;
    int16_t ax;
    int16_t ay;
    int16_t az;
    int16_t gx;
    int16_t gy;
    int16_t gz;
};

static_assert(sizeof(ImuPacket) == 20, "ImuPacket must fit one BLE notification");

BLECharacteristic *txCharacteristic = nullptr;

volatile bool bleConnected = false;
volatile bool restartAdvertisingRequested = false;
volatile bool startStreamRequested = false;

bool imuReady = false;
uint32_t sequenceNumber = 0;
uint32_t nextSampleUs = 0;
uint32_t lastImuRetryMs = 0;
uint32_t readErrorCount = 0;

bool configureI2C();
bool initICM20948();
bool writeRegister(uint8_t reg, uint8_t value);
bool readRegisterBlock(uint8_t startRegister, uint8_t *data, size_t length);
bool readImuRaw(ImuPacket *packet);
void initBLE();
void serviceImuRecovery();
void serviceStream();

class ServerCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer *server) override {
        (void)server;
        bleConnected = true;
        startStreamRequested = true;
        Serial.println("BLE client connected; raw IMU stream starting");
    }

    void onDisconnect(BLEServer *server) override {
        (void)server;
        bleConnected = false;
        restartAdvertisingRequested = true;
        Serial.println("BLE client disconnected");
    }
};

void setup() {
    Serial.begin(115200);
    const uint32_t serialWaitStart = millis();
    while (!Serial && (uint32_t)(millis() - serialWaitStart) < SERIAL_WAIT_TIMEOUT_MS) {
        delay(10);
    }

    imuReady = configureI2C() && initICM20948();
    Serial.println(imuReady ? "ICM-20948 ready" : "ICM-20948 initialization failed");

    initBLE();
}

void loop() {
    if (restartAdvertisingRequested && !bleConnected) {
        restartAdvertisingRequested = false;
        delay(250);
        BLEDevice::startAdvertising();
        Serial.println("BLE advertising restarted");
    }

    if (!imuReady) {
        serviceImuRecovery();
        delay(10);
        return;
    }

    if (!bleConnected) {
        delay(10);
        return;
    }

    serviceStream();
}

void initBLE() {
    BLEDevice::init(BLE_DEVICE_NAME);

    BLEServer *server = BLEDevice::createServer();
    server->setCallbacks(new ServerCallbacks());

    BLEService *service = server->createService(BLE_SERVICE_UUID);
    txCharacteristic = service->createCharacteristic(
        BLE_TX_UUID,
        BLECharacteristic::PROPERTY_NOTIFY
    );
    txCharacteristic->addDescriptor(new BLE2902());
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

    Serial.print("Advertising as ");
    Serial.print(BLE_DEVICE_NAME);
    Serial.print(" at ");
    Serial.print(SAMPLE_RATE_HZ);
    Serial.println(" Hz");
}

void serviceStream() {
    if (startStreamRequested) {
        startStreamRequested = false;
        sequenceNumber = 0;
        nextSampleUs = micros();
    }

    const uint32_t nowUs = micros();
    if ((int32_t)(nowUs - nextSampleUs) < 0) {
        const uint32_t remainingUs = nextSampleUs - nowUs;
        if (remainingUs > 1000) {
            delay(1);
        } else if (remainingUs > 50) {
            delayMicroseconds(remainingUs - 50);
        }
        return;
    }

    ImuPacket packet = {};
    packet.sequence = sequenceNumber++;
    packet.timeUs = nowUs;

    if (!readImuRaw(&packet)) {
        readErrorCount++;
        imuReady = false;
        lastImuRetryMs = millis();
        Serial.print("ICM-20948 read failed; count=");
        Serial.println(readErrorCount);
        return;
    }

    if (bleConnected && txCharacteristic != nullptr) {
        txCharacteristic->setValue(
            reinterpret_cast<uint8_t *>(&packet),
            sizeof(packet)
        );
        txCharacteristic->notify();
    }

    nextSampleUs += SAMPLE_INTERVAL_US;

    // If a slow BLE connection makes this loop miss an entire period, resume
    // from the current time instead of emitting a burst of stale samples.
    if ((int32_t)(micros() - nextSampleUs) >= (int32_t)SAMPLE_INTERVAL_US) {
        nextSampleUs = micros() + SAMPLE_INTERVAL_US;
    }
}

void serviceImuRecovery() {
    const uint32_t nowMs = millis();
    if ((uint32_t)(nowMs - lastImuRetryMs) < IMU_RETRY_INTERVAL_MS) {
        return;
    }

    lastImuRetryMs = nowMs;
    imuReady = configureI2C() && initICM20948();
    Serial.println(imuReady ? "ICM-20948 recovered" : "ICM-20948 retry failed");

    if (imuReady && bleConnected) {
        startStreamRequested = true;
    }
}

bool configureI2C() {
    Wire.end();
    return Wire.begin(IMU_I2C_SDA, IMU_I2C_SCL, I2C_FREQUENCY_HZ);
}

bool initICM20948() {
    uint8_t whoAmI = 0;
    if (!readRegisterBlock(ICM20948_WHO_AM_I, &whoAmI, 1) || whoAmI != 0xEA) {
        return false;
    }

    // Select the best available clock, leave sleep mode, then enable all
    // accelerometer and gyroscope axes. Reset defaults are +/-2 g and
    // +/-250 degrees/second, which match the scale factors above.
    if (!writeRegister(ICM20948_PWR_MGMT_1, 0x01)) {
        return false;
    }
    delay(10);

    return writeRegister(ICM20948_PWR_MGMT_2, 0x00);
}

bool writeRegister(uint8_t reg, uint8_t value) {
    Wire.beginTransmission(ICM20948_ADDR);
    Wire.write(reg);
    Wire.write(value);
    return Wire.endTransmission() == 0;
}

bool readRegisterBlock(uint8_t startRegister, uint8_t *data, size_t length) {
    Wire.beginTransmission(ICM20948_ADDR);
    Wire.write(startRegister);
    if (Wire.endTransmission(false) != 0) {
        return false;
    }

    const size_t bytesRead = Wire.requestFrom(
        (uint8_t)ICM20948_ADDR,
        (uint8_t)length
    );
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
        data[i] = (uint8_t)Wire.read();
    }

    return true;
}

bool readImuRaw(ImuPacket *packet) {
    uint8_t data[12];
    if (!readRegisterBlock(ICM20948_ACCEL_XOUT_H, data, sizeof(data))) {
        return false;
    }

    packet->ax = (int16_t)((data[0] << 8) | data[1]);
    packet->ay = (int16_t)((data[2] << 8) | data[3]);
    packet->az = (int16_t)((data[4] << 8) | data[5]);
    packet->gx = (int16_t)((data[6] << 8) | data[7]);
    packet->gy = (int16_t)((data[8] << 8) | data[9]);
    packet->gz = (int16_t)((data[10] << 8) | data[11]);
    return true;
}

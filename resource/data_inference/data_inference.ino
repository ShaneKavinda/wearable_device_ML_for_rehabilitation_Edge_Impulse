#include <ei_gesture_left_hand_imu.h>
#include <Wire.h>

#define ICM20948_ADDR 0x68
#define ICM20948_WHO_AM_I 0x00
#define ICM20948_PWR_MGMT_1 0x06
#define ICM20948_PWR_MGMT_2 0x07
#define ICM20948_ACCEL_XOUT_H 0x2D
#define ICM20948_GYRO_XOUT_H 0x33

static_assert(EI_CLASSIFIER_PROJECT_ID == 738400, "Install Edge Impulse project 738400.");
static_assert(EI_CLASSIFIER_PROJECT_DEPLOY_VERSION == 19, "Install deployment 19.");
static_assert(EI_CLASSIFIER_RAW_SAMPLE_COUNT == 33, "Deployment 19 requires 33 samples.");
static_assert(EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME == 6, "Deployment 19 requires six axes.");
static_assert(EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE == 198, "Deployment 19 requires 198 features.");
static_assert(EI_CLASSIFIER_FREQUENCY > 16.499 && EI_CLASSIFIER_FREQUENCY < 16.501,
              "Deployment 19 requires a 16.5 Hz sampling rate.");

const String GESTURE_LIST[] = {
  "Flexion", 
  "Extension", 
  "Pronation", 
  "Supination", 
  "Radial Deviation", 
  "Ulnar Deviation"  
};
const int NUM_GESTURES = 6;

struct InferenceResult {
    String label;
    float confidence;
    bool trusted;       // > 85%
    bool correctLabel;  // Matches target
};

/* Buffer */
float features[EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE];

String targetGesture = "";

bool initICM20948();
void writeRegister(uint8_t reg, uint8_t value);
void readIMUData(float* ax, float* ay, float* az, float* gx, float* gy, float* gz);
void collectGestureData();
void runInference(InferenceResult* r);
void reinitializeSensor();
void printSummaryTable(InferenceResult history[], int total);
int raw_feature_get_data(size_t offset, size_t length, float *out_ptr);

void setup() {
    Serial.begin(115200);
    while (!Serial);
    
    Wire.setSDA(4);
    Wire.setSCL(5); 
    Wire.begin();
    
    if (!initICM20948()) {
        Serial.println("Sensor connection failed during setup.");
    }
    Serial.println("System Ready."); 
    printMenu();
}

void printMenu() {
    Serial.println("\n>> Select a Gesture to Test (Type 1-6): <<");
    for (int i = 0; i < NUM_GESTURES; i++) {
        Serial.print(i + 1);
        Serial.print(". ");
        Serial.println(GESTURE_LIST[i]);
    }
}

void loop() {
    if (Serial.available() > 0) {
        int selection = Serial.parseInt();
        while(Serial.available()) Serial.read();
        if (selection >= 1 && selection <= NUM_GESTURES) {
            targetGesture = GESTURE_LIST[selection - 1];
            
            Serial.print("\n=== STARTING 10-REP SESSION FOR: ");
            Serial.print(targetGesture);
            Serial.println(" ===");
            InferenceResult history[10]; 
            
            // Loop 10 times
            for (int i = 0; i < 10; i++) {
                Serial.print("\n--- Repetition ");
                Serial.print(i + 1);
                Serial.println(" of 10 ---");
                Serial.println("Get ready...");
                delay(1000); Serial.println("3...");
                delay(1000); Serial.println("2...");
                delay(1000); Serial.println("1...");
                Serial.println(">> START MOVING! <<");
                collectGestureData();
                runInference(&history[i]);
                reinitializeSensor(); 

                if (i < 9) {
                    Serial.println("Next rep in 2 seconds...");
                    delay(2000); 
                }
            }
            
            printSummaryTable(history, 10);
            
            printMenu();
        } 
        else if (selection != 0) {
            Serial.println("Invalid selection. Please type a number 1-6.");
            printMenu();
        }
    }
}

void runInference(InferenceResult* r) {
    signal_t signal;
    signal.total_length = EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE;
    signal.get_data = &raw_feature_get_data;

    ei_impulse_result_t result = {0};
    EI_IMPULSE_ERROR res = run_classifier(&signal, &result, false);

    if (res != EI_IMPULSE_OK) {
        r->label = "ERR"; r->confidence = 0.0; return;
    }

    float max_score = -1.0;
    int max_idx = -1;

    for (size_t ix = 0; ix < EI_CLASSIFIER_LABEL_COUNT; ix++) {
        if (result.classification[ix].value > max_score) {
            max_score = result.classification[ix].value;
            max_idx = ix;
        }
    }
    
    r->label = String(result.classification[max_idx].label);
    r->confidence = max_score;
    r->trusted = (max_score >= 0.85);
    
    if (r->label.equalsIgnoreCase(targetGesture)) {
        r->correctLabel = true;
    } else {
        r->correctLabel = false;
    }

    Serial.print(">>> PREDICTION: ");
    Serial.print(r->label);
    Serial.print(" ("); Serial.print(max_score * 100, 1); Serial.println("%)");
    
    if (!r->correctLabel) Serial.println("    [WRONG GESTURE]");
    else if (!r->trusted) Serial.println("    [LOW CONFIDENCE]");
}

void printSummaryTable(InferenceResult history[], int total) {
    // 1. Print Detailed History First
    Serial.println("\n\n=======================================================");
    Serial.print("     SESSION REPORT: Target = "); Serial.println(targetGesture);
    Serial.println("=======================================================");
    Serial.println("Rep\tPredicted\tConf.\tResult");
    Serial.println("---\t---------\t-----\t------");
    
    int successCount = 0;
    int uncertainCount = 0;
    float totalSuccessConf = 0.0;
    String errorLog = "";
    
    for (int i = 0; i < total; i++) {
        // --- PRINT ROW ---
        Serial.print(i + 1); Serial.print("\t");
        Serial.print(history[i].label);
        
        if (history[i].label.length() < 8) Serial.print("\t\t");
        else if (history[i].label.length() < 16) Serial.print("\t");
        else Serial.print(" "); 
        
        Serial.print(history[i].confidence * 100, 1); Serial.print("%\t");
        
        if (history[i].correctLabel && history[i].trusted) {
            Serial.println("PASS");
            successCount++;
            totalSuccessConf += history[i].confidence;
        } else if (!history[i].correctLabel) {
            Serial.println("FAIL (Wrong)");
            // Add to error log string
            errorLog += history[i].label + " "; 
        } else {
            Serial.println("FAIL (Uncertain)");
            uncertainCount++;
        }
    }
    
    float avgConf = 0.0;
    if (successCount > 0) avgConf = totalSuccessConf / successCount;
    
    Serial.println("-------------------------------------------------------");
    Serial.println("Target      | Success Rate    | Avg Confidence | Common Errors");
    Serial.println("------------|-----------------|----------|-------------");
    
    Serial.print(targetGesture);
    int spaces = 12 - targetGesture.length();
    if (spaces < 0) spaces = 0;
    for(int k=0; k<spaces; k++) Serial.print(" ");
    
    Serial.print("| ");
    Serial.print(successCount); Serial.print("/10 ("); 
    Serial.print((float(successCount)/total)*100, 0); Serial.print("%)  | ");
    
    Serial.print(avgConf, 3); 
    Serial.print("     | ");
    
    if (uncertainCount > 0) {
        Serial.print(uncertainCount); Serial.print("x Uncertain ");
    }
    if (errorLog.length() > 0) {
        Serial.print(errorLog);
    }
    if (uncertainCount == 0 && errorLog.length() == 0) {
        Serial.print("None");
    }
    
    Serial.println("\n=======================================================");
}

void collectGestureData() {
    size_t featureIndex = 0;
    float ax, ay, az, gx, gy, gz;
    size_t totalSamples = EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE / 6;

    for (size_t s = 0; s < totalSamples; s++) {
        unsigned long sampleStart = micros();
        readIMUData(&ax, &ay, &az, &gx, &gy, &gz);
        
        // The training CSV columns are in g and degrees/second. The exported
        // raw DSP block uses scale_axes=1.0, so preserve those units.
        features[featureIndex++] = ax;
        features[featureIndex++] = ay;
        features[featureIndex++] = az;
        features[featureIndex++] = gx;
        features[featureIndex++] = gy;
        features[featureIndex++] = gz;

        while (micros() - sampleStart < (EI_CLASSIFIER_INTERVAL_MS * 1000));
    }
    Serial.println("Sampling Finished.");
}

void reinitializeSensor() {
    Serial.println("[RESET] Flushing...");
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

# Toward Stroke Rehabilitation: Hand Gesture Recognition using TinyML

This repository contains the source code for a low-cost, wearable hand gesture recognition system. It uses a single IMU (ICM-20948) and a Raspberry Pi Pico W to identify six therapeutic wrist gestures.

## Project Structure

* **data-collection/**: Contains MicroPython scripts for data acquisition.
    * **main_program.py**: Primary script for recording gestures.
    * **imu_handler.py**: Interface for the IMU sensor.
* **data_inference/**: Arduino (C++) source code for real-time gesture recognition.
* **exported_model/**: The trained 1D-CNN model exported as a C++ library from Edge Impulse.

## Getting Started

### 1. Data Collection (MicroPython)
1. Flash your Raspberry Pi Pico W with MicroPython firmware.
2. Upload all files from the /data-collection folder.
3. Run main_program.py and follow the terminal prompts to record data.

### 2. Model Training
1. Upload the collected CSV data to Edge Impulse.
2. Design an Impulse with a 1D Convolutional Neural Network (CNN).
3. Export the project as an Arduino Library.

### 3. Real-Time Inference (Arduino)
1. Import the library from /exported_model into your Arduino IDE.
2. Open the code in /data_inference and upload it to the Raspberry Pi Pico W.

## Requirements
* **Hardware**: Raspberry Pi Pico W, ICM-20948 9-DOF IMU.
* **Software**: Thonny IDE (MicroPython) and Arduino IDE (C++).
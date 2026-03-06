/*
 * SoterCare - Wireless Data Streamer (Fixed Offsets)
 */

#include <Wire.h>
#include <MPU6050_light.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

#define SERVICE_UUID           "6E400001-B5A3-F393-E0A9-E50E24DCCA9E" 
#define CHARACTERISTIC_UUID_RX "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHARACTERISTIC_UUID_TX "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

// ================= HARDWARE CONFIGURATION =================
#define RGB_BUILTIN_PIN 48 
#define FREQUENCY_HZ 100
#define INTERVAL_MS  (1000 / FREQUENCY_HZ)

// PASTE YOUR VALUES FROM THE CALIBRATION SCRIPT HERE:
const float FACTORY_ACC_X = 0.07; 
const float FACTORY_ACC_Y = 0.00;
const float FACTORY_ACC_Z = 0.00;

MPU6050 mySensor(Wire);
BLEServer* pServer = NULL;
BLECharacteristic* pTxCharacteristic = NULL;
bool deviceConnected = false;
bool oldDeviceConnected = false;
unsigned long last_interval_ms = 0;
unsigned long connectionTime = 0;

void setRGB(uint8_t r, uint8_t g, uint8_t b) { neopixelWrite(RGB_BUILTIN_PIN, r, g, b); }
void blinkRGB(uint8_t r, uint8_t g, uint8_t b, int count, int speed) {
  for (int i = 0; i < count; i++) { setRGB(r, g, b); delay(speed); setRGB(0, 0, 0); delay(speed); }
}

class MyServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
        deviceConnected = true;
        connectionTime = millis();
        pServer->updateConnParams(pServer->getConnId(), 0x10, 0x20, 0, 400);    };

    void onDisconnect(BLEServer* pServer) {
        deviceConnected = false;
    }
};

void setup() {
  Serial.begin(115200);
  Wire.begin();
  byte status = mySensor.begin();
  
  if (status != 0) {
    blinkRGB(128, 0, 0, 3, 500);
    while(1);
  }

  // PROFESSIONAL CALIBRATION
  Serial.println("Calibrating Gyro...");

  // Wait 2 seconds and blink blue twice
  blinkRGB(0, 0, 128, 2, 500);
  
  // Steady blue during calibration
  setRGB(0, 0, 128); 
  
  // 1. Calibrate Gyro (true), Skip Accel (false)
  mySensor.calcOffsets(true, false); 
  
  // Calibration confirmation (Green 3 times)
  blinkRGB(0, 128, 0, 3, 200);
  
  // 2. Load the hardcoded Factory Accel Offsets
  // Fixed: Ensure the library matches this naming convention
  mySensor.setAccOffsets(FACTORY_ACC_X, FACTORY_ACC_Y, FACTORY_ACC_Z);

  Serial.println("System Ready!");
  setRGB(0, 0, 0);

  // BLE Setup
  BLEDevice::init("D01 Prototype 1.0v SoterCare");
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());
  BLEService *pService = pServer->createService(SERVICE_UUID);
  pTxCharacteristic = pService->createCharacteristic(CHARACTERISTIC_UUID_TX, BLECharacteristic::PROPERTY_NOTIFY);
  pTxCharacteristic->addDescriptor(new BLE2902());
  pService->start();
  pServer->getAdvertising()->start();
}

void loop() {
    // disconnecting
    if (!deviceConnected && oldDeviceConnected) {
        delay(500); // give the bluetooth stack the chance to get things ready
        pServer->startAdvertising(); // restart advertising
        Serial.println("start advertising");
        oldDeviceConnected = deviceConnected;
    }
    // connecting
    if (deviceConnected && !oldDeviceConnected) {
        oldDeviceConnected = deviceConnected;
        // Blink Green 3 times to confirm connection
        blinkRGB(0, 128, 0, 3, 200);
    }
    
    if (deviceConnected) {
        if (millis() - connectionTime > 100) {
            unsigned long currentMillis = millis();
            if (currentMillis - last_interval_ms >= INTERVAL_MS) {
                last_interval_ms = currentMillis;
                mySensor.update();
                
                String dataLine = String(mySensor.getAccX(), 2) + "," + String(mySensor.getAccY(), 2) + "," + String(mySensor.getAccZ(), 2) + "," + 
                                  String(mySensor.getGyroX(), 2) + "," + String(mySensor.getGyroY(), 2) + "," + String(mySensor.getGyroZ(), 2) + "\n";
                                  
                pTxCharacteristic->setValue(dataLine.c_str());
                pTxCharacteristic->notify();
            }
        }
    } else {
        // Blink Purple every 500ms
        static unsigned long lastBlinkChange = 0;
        static bool isPurple = false;
        
        if (millis() - lastBlinkChange > 500) {
            lastBlinkChange = millis();
            isPurple = !isPurple;
            if (isPurple) setRGB(128, 0, 128); 
            else setRGB(0, 0, 0);
        }
    }
}
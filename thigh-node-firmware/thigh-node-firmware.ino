#include <Wire.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <MPU6050_light.h>
#include <Adafruit_MLX90614.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_NeoPixel.h>
#include "env.h"

// --- Pin Definitions ---
#define I2C_SDA_PIN 8
#define I2C_SCL_PIN 9
#define MOISTURE_A0_PIN 4 
#define MOTOR_PIN 10
#define BTN_UP 11
#define BTN_ENTER 12
#define BTN_DOWN 13
#define BTN_SOS 14
#define RGB_PIN 48

// --- Constants & Objects ---
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

MPU6050 mpu(Wire); 
Adafruit_MLX90614 mlx = Adafruit_MLX90614();
Adafruit_NeoPixel led(1, RGB_PIN, NEO_GRB + NEO_KHZ800);
WiFiUDP udp;

const char* gatewayIP = "192.168.1.100";
const int udpPort = 1234;

// --- BLE Nordic UART Service UUIDs ---
#define SERVICE_UUID           "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHARACTERISTIC_UUID_TX "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

BLEServer *pServer = NULL;
BLECharacteristic * pTxCharacteristic;
bool bleConnected = false;

// --- System Variables ---
unsigned long lastTransmission = 0;
const int targetInterval = 16; // ~60Hz
unsigned long lastButtonPressTime = 0;
bool isScreenAwake = true;
bool usingWiFi = false;
bool oledInitialized = false;

// Sensor State Tracking
int currentRawMoisture = 0;
int currentMoisturePercent = 0;

// Button Logic
unsigned long btnEnterPressTime = 0;
bool btnEnterHeld = false;

// --- Menu State Machine ---
enum MenuState { 
  MENU_OFF, 
  MENU_MAIN, 
  MENU_SENSOR_LIST, 
  MENU_SENSOR_VIEW, 
  MENU_LOG, 
  MENU_NETWORK,
  MENU_SERIAL_MONITOR
};
MenuState currentMenu = MENU_MAIN; 

int mainMenuCursor = 0;   
int sensorMenuCursor = 0; 
int networkMenuCursor = 0; // Tracks the highlighted network option

enum NetworkMode { MODE_AUTO, MODE_WIFI_ONLY, MODE_BLE_ONLY };
NetworkMode netMode = MODE_AUTO; 

// Error Logger
struct ErrorLog {
  unsigned long timestamp;
  String message;
};
ErrorLog errorLogs[5];
int errorIndex = 0;

// Serial Logger
String serialLogs[20];
int serialLogIndex = 0;
int serialLogScroll = 0; // Tracks which log is at the top of the OLED

// --- Helper Functions ---
void logError(String msg) {
  errorLogs[errorIndex].timestamp = millis();
  errorLogs[errorIndex].message = msg;
  errorIndex = (errorIndex + 1) % 5;
}

void setLED(int r, int g, int b) {
  led.setPixelColor(0, led.Color(r, g, b));
  led.show();
}

void triggerHaptic(int duration) {
  digitalWrite(MOTOR_PIN, HIGH);
  delay(duration);
  digitalWrite(MOTOR_PIN, LOW);
}

void systemPrint(String msg) {
  // Add to circular buffer
  serialLogs[serialLogIndex] = msg;
  serialLogIndex = (serialLogIndex + 1) % 20;
  
  // Also print to actual Serial monitor
  Serial.println(msg);
}

// Signal Strength Graphics
void drawSignalBars(int startX, int startY, int strength, bool isWiFi) {
  // Draw Icon String (W or B)
  display.setCursor(startX, startY);
  display.print(isWiFi ? "W " : "B ");
  
  if (strength < 0) { // Disconnected state
    display.print("x");
    return;
  }
  
  // Draw Nokia-style ascending bars
  // Max 4 bars. Each bar is 2px wide with 1px gap
  for (int i = 0; i < 4; i++) {
    int barX = startX + 12 + (i * 3);
    int barHeight = 2 + (i * 2); // Heights: 2, 4, 6, 8
    int barY = startY + 8 - barHeight; 
    
    if (i < strength) {
      display.fillRect(barX, barY, 2, barHeight, SSD1306_WHITE); // Filled bar
    } else {
      display.drawRect(barX, barY, 2, barHeight, SSD1306_WHITE); // Empty outline
    }
  }
}

int getWiFiStrengthLevel() {
  if (WiFi.status() != WL_CONNECTED) return -1;
  long rssi = WiFi.RSSI();
  if (rssi > -60) return 4;
  else if (rssi > -70) return 3;
  else if (rssi > -80) return 2;
  else return 1;
}

int getBLEStrengthLevel() {
  if (!bleConnected) return -1;
  return 4; // Fake max strength for connected BLE server
}

// Live Boot Screen Updater
void printBootStatus(String step, String status) {
  String fullMsg = step + status;
  systemPrint(fullMsg);

  if (oledInitialized) {
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0,0);
    display.println("SoterCare Boot...");
    display.println("-----------------");
    display.println(fullMsg);
    display.display();
  }
}

// --- BLE Server Callbacks ---
class MyServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) { 
      bleConnected = true; 
      systemPrint("BLE Connection Established!");
      if (!usingWiFi && netMode == MODE_AUTO) {
        setLED(0, 128, 255); // Solid Blue
      }
    };
    void onDisconnect(BLEServer* pServer) {
      bleConnected = false;
      systemPrint("BLE Connection Lost!");
      pServer->startAdvertising(); 
    }
};

// --- Setup ---
void setup() {
  Serial.begin(115200);
  
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  
  pinMode(MOISTURE_A0_PIN, INPUT);
  
  pinMode(MOTOR_PIN, OUTPUT);
  pinMode(BTN_UP, INPUT_PULLUP);
  pinMode(BTN_ENTER, INPUT_PULLUP);
  pinMode(BTN_DOWN, INPUT_PULLUP);
  pinMode(BTN_SOS, INPUT_PULLUP);

  led.begin();
  led.setBrightness(50);
  setLED(128, 0, 128); 
  
  // Init OLED
  if(!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    systemPrint("OLED init failed");
    oledInitialized = false;
  } else {
    oledInitialized = true;
  }

  printBootStatus("OLED Display: ", "OK");
  delay(500);

  // Init MLX90614
  printBootStatus("Temp Sensor: ", "Init...");
  Wire.setClock(100000); // MLX90614 is an SMBus device and strictly requires 100kHz!
  if (!mlx.begin()) {
    printBootStatus("Temp Sensor: ", "FAIL!");
    logError("Temp Init Fail");
    delay(1000); 
  } else {
    printBootStatus("Temp Sensor: ", "OK");
    delay(500);
  }
  Wire.setClock(400000); // Restore fast I2C for MPU & OLED
  
  // Init MPU6050
  printBootStatus("IMU Sensor: ", "Init...");
  byte status = mpu.begin();
  if(status != 0){
    printBootStatus("IMU Sensor: ", "FAIL!");
    logError("IMU Init Fail");
    delay(1000);
  } else {
    printBootStatus("IMU Calibrating: ", "Keep Flat!");
    mpu.calcOffsets(true, true); 
    printBootStatus("IMU Calibrating: ", "Done");
    delay(500);
  }

  // Init BLE
  printBootStatus("BLE Radio: ", "Starting...");
  BLEDevice::init("MedNode_BLE");
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());
  BLEService *pService = pServer->createService(SERVICE_UUID);
  pTxCharacteristic = pService->createCharacteristic(
                        CHARACTERISTIC_UUID_TX,
                        BLECharacteristic::PROPERTY_NOTIFY
                      );
  pTxCharacteristic->addDescriptor(new BLE2902());
  pService->start();
  pServer->getAdvertising()->start();
  printBootStatus("BLE Radio: ", "Ready");
  delay(500);

  // Init Wi-Fi (Non-blocking start)
  printBootStatus("Wi-Fi: ", "Starting Discovery...");
  WiFi.begin(ssid, password);
  systemPrint("Hunting for Any Connection...");

  lastButtonPressTime = millis();
}

unsigned long lastBlinkTime = 0;
bool ledBlinkState = false;

// --- Main Loop ---
void loop() {
  mpu.update(); 
  updateMoisture();

  handleButtons();
  handleScreen(); 
  checkOLEDConnection();
  
  if (millis() - lastTransmission >= targetInterval) {
    lastTransmission = millis();
    sendData();
    checkNetworkStability();
  }
}

// --- Data & Comms ---
void updateMoisture() {
  static unsigned long lastMoistureRead = 0;
  static int readingHistory[5] = {0,0,0,0,0};
  static int readIndex = 0;
  
  if (millis() - lastMoistureRead >= 100) { // Poll every 100ms
    lastMoistureRead = millis();
    
    // Add new reading to history
    readingHistory[readIndex] = analogRead(MOISTURE_A0_PIN);
    readIndex = (readIndex + 1) % 5;
    
    // Calculate running average (simulates 500ms stable window)
    long total = 0;
    for (int i = 0; i < 5; i++) {
      total += readingHistory[i];
    }
    
    currentRawMoisture = total / 5;
    currentMoisturePercent = constrain(map(currentRawMoisture, 4095, 1200, 0, 100), 0, 100);
  }
}

void sendData() {
  Wire.setClock(100000);
  float temp = mlx.readObjectTempC();
  Wire.setClock(400000);
  
  String payload = String(mpu.getAccX()) + "," + 
                   String(mpu.getAccY()) + "," + 
                   String(mpu.getAccZ()) + "," + 
                   String(temp) + "," + 
                   String(currentMoisturePercent) + "\n"; 

  bool routeWiFi = (netMode == MODE_WIFI_ONLY) || (netMode == MODE_AUTO && usingWiFi);

  if (routeWiFi) {
    udp.beginPacket(gatewayIP, udpPort);
    udp.print(payload);
    udp.endPacket(); 
  } else if (bleConnected) {
    pTxCharacteristic->setValue(payload.c_str());
    pTxCharacteristic->notify();
  }
}

void checkNetworkStability() {
  bool isConnectedWiFi = (WiFi.status() == WL_CONNECTED);

  // Detect Wi-Fi connection gained
  if (!usingWiFi && isConnectedWiFi) {
    systemPrint("WiFi Connection Established!");
    usingWiFi = true;
    if (netMode == MODE_AUTO) setLED(0, 255, 0); // Solid Green
  } 
  
  // Detect Wi-Fi connection lost
  else if (usingWiFi && !isConnectedWiFi) {
    systemPrint("WiFi Connection Lost!");
    usingWiFi = false;
    logError("WiFi Disconn");
  }

  // --- Connection Hunting Logic ---
  if (netMode == MODE_AUTO) {
    if (!usingWiFi && !bleConnected) {
      // Actively hunting for both. Blink Green and Blue.
      if (millis() - lastBlinkTime > 500) {
        lastBlinkTime = millis();
        ledBlinkState = !ledBlinkState;
        if (ledBlinkState) setLED(0, 255, 0); // Green
        else setLED(0, 128, 255);             // Blue
      }
    } 
    else if (!usingWiFi && bleConnected) {
      // BLE connected but WiFi is down. Fallback state.
      setLED(0, 128, 255); // Solid Blue
    }
    
    // If WiFi is down, try to restart connection process every 10 seconds 
    // This runs silently in the background even if BLE is currently connected!
    if (!usingWiFi) {
      static unsigned long lastReconnectAttempt = 0;
      if (millis() - lastReconnectAttempt > 10000) {
        lastReconnectAttempt = millis();
        systemPrint("Attempting Wi-Fi re-connect...");
        WiFi.disconnect();
        WiFi.begin(ssid, password);
      }
    }
  }
}

void checkOLEDConnection() {
  static unsigned long lastOLEDCheck = 0;
  if (millis() - lastOLEDCheck > 2000) {
    lastOLEDCheck = millis();
    Wire.beginTransmission(0x3C);
    byte error = Wire.endTransmission();
    
    if (error == 0) {
      if (!oledInitialized) {
        if (display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
          oledInitialized = true;
          logError("OLED Restored");
        }
      }
    } else {
      if (oledInitialized) {
        oledInitialized = false;
        logError("OLED Disconn");
      }
    }
  }
}

// --- Navigation & Wake Logic ---
void handleButtons() {
  bool anyNavPressed = (digitalRead(BTN_UP) == LOW) || (digitalRead(BTN_DOWN) == LOW) || (digitalRead(BTN_ENTER) == LOW);
  bool sosPressed = (digitalRead(BTN_SOS) == LOW);

  if (sosPressed) {
    lastButtonPressTime = millis();
    isScreenAwake = true;
    if (currentMenu == MENU_OFF) currentMenu = MENU_MAIN;
    systemPrint("SOS Triggered");
    triggerHaptic(500);
    delay(500); 
    return; 
  }

  if (anyNavPressed) {
    lastButtonPressTime = millis(); 

    if (!isScreenAwake || currentMenu == MENU_OFF) {
      isScreenAwake = true;
      currentMenu = MENU_MAIN;
      while(digitalRead(BTN_UP) == LOW || digitalRead(BTN_DOWN) == LOW || digitalRead(BTN_ENTER) == LOW) { delay(10); }
      return; 
    }
  }

  if (!isScreenAwake) return;

  // UP Button List Scrolling
  if (digitalRead(BTN_UP) == LOW) {
    triggerHaptic(30);
    if (currentMenu == MENU_MAIN) mainMenuCursor = (mainMenuCursor - 1 + 4) % 4; // Now 4 items
    else if (currentMenu == MENU_SENSOR_LIST) sensorMenuCursor = (sensorMenuCursor - 1 + 3) % 3;
    else if (currentMenu == MENU_NETWORK) networkMenuCursor = (networkMenuCursor - 1 + 3) % 3;
    else if (currentMenu == MENU_SERIAL_MONITOR) {
      if (serialLogScroll > 0) serialLogScroll--;
    }
    delay(200); 
  }
  
  // DOWN Button List Scrolling
  if (digitalRead(BTN_DOWN) == LOW) {
    triggerHaptic(30);
    if (currentMenu == MENU_MAIN) mainMenuCursor = (mainMenuCursor + 1) % 4; 
    else if (currentMenu == MENU_SENSOR_LIST) sensorMenuCursor = (sensorMenuCursor + 1) % 3; 
    else if (currentMenu == MENU_NETWORK) networkMenuCursor = (networkMenuCursor + 1) % 3;
    else if (currentMenu == MENU_SERIAL_MONITOR) {
      if (serialLogScroll < 20 - 5) serialLogScroll++; // Keep within 20 bounds, display 5 lines at a time
    }
    delay(200);
  }

  if (digitalRead(BTN_ENTER) == LOW) {
    if (btnEnterPressTime == 0) btnEnterPressTime = millis();
    
    // Hold to go back logic
    if (millis() - btnEnterPressTime > 800 && !btnEnterHeld) {
      btnEnterHeld = true;
      triggerHaptic(100);
      
      if (currentMenu == MENU_SENSOR_VIEW) currentMenu = MENU_SENSOR_LIST;
      else if (currentMenu == MENU_SENSOR_LIST || currentMenu == MENU_LOG || currentMenu == MENU_NETWORK || currentMenu == MENU_SERIAL_MONITOR) {
        currentMenu = MENU_MAIN;
        serialLogScroll = 0; // Reset scroll when leaving
      }
      else if (currentMenu == MENU_MAIN) {
        currentMenu = MENU_OFF; 
        isScreenAwake = false;
      }
    }
  } else {
    // Short press to select logic
    if (btnEnterPressTime > 0 && !btnEnterHeld) {
      triggerHaptic(50);
      
      if (currentMenu == MENU_MAIN) {
        if (mainMenuCursor == 0) currentMenu = MENU_SENSOR_LIST;
        else if (mainMenuCursor == 1) currentMenu = MENU_LOG;
        else if (mainMenuCursor == 2) currentMenu = MENU_NETWORK;
        else if (mainMenuCursor == 3) currentMenu = MENU_SERIAL_MONITOR;
      }
      else if (currentMenu == MENU_SENSOR_LIST) {
        currentMenu = MENU_SENSOR_VIEW;
      }
      else if (currentMenu == MENU_NETWORK) {
        // Apply the highlighted network mode
        if (networkMenuCursor == 0) { 
          netMode = MODE_AUTO; 
          usingWiFi = (WiFi.status() == WL_CONNECTED);
          setLED(usingWiFi ? 0 : 0, usingWiFi ? 255 : 128, usingWiFi ? 0 : 255); 
        } 
        else if (networkMenuCursor == 1) { 
          netMode = MODE_WIFI_ONLY; 
          setLED(0, 255, 0); 
        } 
        else if (networkMenuCursor == 2) { 
          netMode = MODE_BLE_ONLY; 
          setLED(0, 128, 255); 
        }
      }
    }
    btnEnterPressTime = 0;
    btnEnterHeld = false;
  }
}

// --- Screen Rendering ---
void handleScreen() {
  if (!oledInitialized) return;

  if (millis() - lastButtonPressTime > 60000) {
    isScreenAwake = false;
    currentMenu = MENU_OFF;
    display.clearDisplay();
    display.display();
    return;
  }

  if (!isScreenAwake || currentMenu == MENU_OFF) return;

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0,0);

  if (currentMenu == MENU_MAIN) {
    display.print("SoterCare");
    
    // Draw Signal Strength in Top Right
    // W bars at x=70, B bars at x=100
    drawSignalBars(70, 0, getWiFiStrengthLevel(), true);
    drawSignalBars(100, 0, getBLEStrengthLevel(), false);
    
    display.setCursor(0, 10);
    display.println("--------------------");
    
    // Draw exactly 4 items simultaneously
    for (int i = 0; i < 4; i++) {
      String linePrefix = (mainMenuCursor == i) ? "> " : "  ";
      String itemName = "";
      
      if (i == 0) itemName = "Sensor Test";
      else if (i == 1) itemName = "Error Log";
      else if (i == 2) itemName = "Network Mode";
      else if (i == 3) itemName = "Monitor";
      
      display.println(linePrefix + itemName);
    }
  } 
  
  else if (currentMenu == MENU_SENSOR_LIST) {
    display.println("--- SENSOR TEST ---");
    display.println(sensorMenuCursor == 0 ? "> 1. IMU (MPU6050)" : "  1. IMU (MPU6050)");
    display.println(sensorMenuCursor == 1 ? "> 2. MLX Temp" : "  2. MLX Temp");
    display.println(sensorMenuCursor == 2 ? "> 3. Moisture" : "  3. Moisture");
    
    display.setCursor(0, 55);
    display.print("[Hold ENTER to Back]");
  }
  
  else if (currentMenu == MENU_SENSOR_VIEW) {
    if (sensorMenuCursor == 0) {
      display.println("--- IMU DATA ---");
      display.printf("AccX: %.2f\n", mpu.getAccX());
      display.printf("AccY: %.2f\n", mpu.getAccY());
      display.printf("AccZ: %.2f\n", mpu.getAccZ());
    } 
    else if (sensorMenuCursor == 1) {
      Wire.setClock(100000);
      float objTemp = mlx.readObjectTempC();
      float ambTemp = mlx.readAmbientTempC();
      Wire.setClock(400000);
      display.println("--- TEMP DATA ---");
      display.printf("Obj: %.2f C\n", objTemp);
      display.printf("Amb: %.2f C\n", ambTemp);
    }
    else if (sensorMenuCursor == 2) {
      display.println("--- MOISTURE ---");
      display.printf("Raw ADC: %d\n", currentRawMoisture);
      display.printf("Moisture: %d %%\n", currentMoisturePercent);
    }
    display.setCursor(0, 55);
    display.print("[Hold ENTER to Back]");
  }
  
  else if (currentMenu == MENU_LOG) {
    display.println("--- ERROR LOG ---");
    bool hasErrors = false;
    for (int i = 0; i < 5; i++) {
      if (errorLogs[i].timestamp > 0) {
        hasErrors = true;
        int secs = errorLogs[i].timestamp / 1000;
        display.printf("[%02d:%02d] %s\n", secs/60, secs%60, errorLogs[i].message.c_str());
      }
    }
    if (!hasErrors) display.println("\n  No Errors Logged.");
    
    display.setCursor(0, 55);
    display.print("[Hold ENTER to Back]");
  } 
  
  else if (currentMenu == MENU_NETWORK) {
    display.println("--- NETWORK MODE ---");
    
    // Draw dropdown list with active marker
    display.print(networkMenuCursor == 0 ? "> " : "  ");
    display.print(netMode == MODE_AUTO ? "[*] " : "[ ] ");
    display.println("AUTO");
    
    display.print(networkMenuCursor == 1 ? "> " : "  ");
    display.print(netMode == MODE_WIFI_ONLY ? "[*] " : "[ ] ");
    display.println("WI-FI ONLY");
    
    display.print(networkMenuCursor == 2 ? "> " : "  ");
    display.print(netMode == MODE_BLE_ONLY ? "[*] " : "[ ] ");
    display.println("BLE ONLY");
    
    display.setCursor(0, 55);
    display.print("[Hold ENTER to Back]");
  }
  
  else if (currentMenu == MENU_SERIAL_MONITOR) {
    display.println("--- MONITOR ---");
    // Sort array by time: print oldest to newest. 
    // serialLogIndex points to the OLDEST entry (next to be overwritten).
    int linesShown = 0;
    
    for (int i = 0; i < 20; i++) {
        if (linesShown >= 6) break; // Out of vertical screen space
        
        int physicalIdx = (serialLogIndex + i) % 20;
        
        if (serialLogs[physicalIdx].length() > 0) {
            // Only start drawing once we reach the scroll offset
            if (i >= serialLogScroll) {
                // Truncate strings to screen width (~20 chars max for text size 1)
                display.println(serialLogs[physicalIdx].substring(0, 20)); 
                linesShown++;
            }
        }
    }
  }
  
  display.display();
}
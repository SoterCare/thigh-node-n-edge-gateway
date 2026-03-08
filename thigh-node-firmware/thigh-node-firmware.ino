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

// --- BLE Nordic UART Service UUIDs ---
#define SERVICE_UUID           "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHARACTERISTIC_UUID_TX "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

BLEServer *pServer = NULL;
BLECharacteristic * pTxCharacteristic;
bool bleConnected = false;

// --- System Variables ---
unsigned long lastTransmission = 0;
const unsigned long targetInterval = 17; // Target ~58Hz; ~3ms execution overhead lands at 52-55Hz actual
unsigned long lastButtonPressTime = 0;
bool isScreenAwake = true;
bool usingWiFi = false;
bool oledInitialized = false;

// Sensor State Tracking
int currentRawMoisture = 0;
int currentMoisturePercent = 0;
float cachedTemp = 0.0;          // Updated every 500ms (Object)
float cachedAmbientTemp = 0.0;   // Updated every 500ms (Ambient)
int   cachedRSSI = 0;            // Updated every 1s, avoids WiFi.RSSI() overhead per frame

// Button Logic
unsigned long btnEnterPressTime = 0;
bool btnEnterHeld = false;
volatile bool sosTriggered = false;  // Set by SOS button, cleared after next transmit
unsigned long sosMotorUntil = 0;     // Non-blocking haptic: motor off after this time

// --- Menu State Machine ---
enum MenuState { 
  MENU_OFF, 
  MENU_MAIN, 
  MENU_SENSOR_LIST, 
  MENU_SENSOR_VIEW, 
  MENU_LOG, 
  MENU_NETWORK,
  MENU_SERIAL_MONITOR,
  MENU_NETWORK_TEST
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
// Draws 4 Nokia-style ascending bars at startX, startY
// strength: -1=disconnected, 1-4=bars filled
void drawSignalBars(int startX, int startY, int strength) {
  if (strength < 0) {
    // Draw a clean pixel-art 'x' (5×5 area) to indicate no connection
    int x = startX + 1;
    int y = startY + 2;
    display.drawPixel(x + 0, y + 0, SSD1306_WHITE);
    display.drawPixel(x + 1, y + 1, SSD1306_WHITE);
    display.drawPixel(x + 2, y + 2, SSD1306_WHITE);
    display.drawPixel(x + 3, y + 3, SSD1306_WHITE);
    display.drawPixel(x + 4, y + 4, SSD1306_WHITE);
    display.drawPixel(x + 4, y + 0, SSD1306_WHITE);
    display.drawPixel(x + 3, y + 1, SSD1306_WHITE);
    display.drawPixel(x + 1, y + 3, SSD1306_WHITE);
    display.drawPixel(x + 0, y + 4, SSD1306_WHITE);
    return;
  }

  // 4 bars, each 2px wide, 4px apart, heights: 2,4,6,8
  for (int i = 0; i < 4; i++) {
    int barX = startX + (i * 4);
    int barHeight = 2 + (i * 2);
    int barY = startY + 8 - barHeight;

    if (i < strength) {
      display.fillRect(barX, barY, 2, barHeight, SSD1306_WHITE);
    } else {
      // Empty slot: base dot only
      display.drawPixel(barX,     startY + 7, SSD1306_WHITE);
      display.drawPixel(barX + 1, startY + 7, SSD1306_WHITE);
    }
  }
}

int getWiFiStrengthLevel() {
  if (WiFi.status() != WL_CONNECTED) return -1;
  long rssi = WiFi.RSSI();
  // Adjusted thresholds for more aggressive animation drop-off
  if (rssi >= -40) return 4;
  else if (rssi >= -65) return 3;
  else if (rssi >= -80) return 2;
  else return 1;
}

int getBLEStrengthLevel() {
  if (!bleConnected || pServer == NULL) return -1;
  // Since active client RSSI is not easily read on Kolban's BLE, animate placeholder
  int visualLevel = 2 + (millis() % 3); 
  return visualLevel;
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
    // Set MPU sample rate divider to 0 — max internal rate (1kHz with DLPF, 8kHz without)
    // This ensures the sensor register always has fresh data when mpu.update() reads it
    Wire.beginTransmission(0x68);  // MPU I2C address
    Wire.write(0x19);              // SMPLRT_DIV register
    Wire.write(0);                 // Divider = 0 → maximum sample rate
    Wire.endTransmission(true);
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

// --- Helper timers (all non-blocking) ---
static unsigned long lastTempRead    = 0;  // MLX: every 500ms
static unsigned long lastOLEDDraw    = 0;  // OLED: every 250ms (4 FPS)
static unsigned long lastNetCheck    = 0;  // Network stability: every 2s

// Inline helper: transmit if 20ms window has passed
#define TRY_TRANSMIT() do { \
  unsigned long _t = millis(); \
  if (_t - lastTransmission >= targetInterval) { \
    lastTransmission = _t; \
    sendData(); \
  } \
} while(0)

void loop() {
  // ── Non-blocking SOS motor off ─────────────────────────────────────────────
  if (sosMotorUntil && millis() >= sosMotorUntil) {
    digitalWrite(MOTOR_PIN, LOW);
    sosMotorUntil = 0;
  }

  // ── HOT PATH FIRST ─────────────────────────────────────────────────────────
  mpu.update();
  TRY_TRANSMIT();


  // ── Buttons (fast — no I2C) ────────────────────────────────────────────────
  handleButtons();

  // ── Moisture poll (100ms internal gate, just analogRead) ──────────────────
  updateMoisture();

  // ── MLX Temperature every 500ms (slow I2C — ~8ms) ─────────────────────────
  if (millis() - lastTempRead >= 500) {
    lastTempRead = millis();
    Wire.setClock(100000);
    cachedTemp = mlx.readObjectTempC();
    cachedAmbientTemp = mlx.readAmbientTempC();
    Wire.setClock(400000);
    TRY_TRANSMIT();          // Catch up immediately after blocking call
  }

  // ── OLED redraw every 250ms (~20ms blocking I2C DMA) ──────────────────────
  if (millis() - lastOLEDDraw >= 250) {
    lastOLEDDraw = millis();
    handleScreen();
    checkOLEDConnection();
    TRY_TRANSMIT();          // Catch up immediately after blocking call
  }

  // ── Cache RSSI every 1s — WiFi.RSSI() has driver overhead ────────────────
  static unsigned long lastRSSIRead = 0;
  if (millis() - lastRSSIRead >= 1000) {
    lastRSSIRead = millis();
    if (usingWiFi) cachedRSSI = WiFi.RSSI();
  }

  // ── Network stability every 2s ─────────────────────────────────────────────
  if (millis() - lastNetCheck >= 2000) {
    lastNetCheck = millis();
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
  bool routeWiFi = (netMode == MODE_WIFI_ONLY) || (netMode == MODE_AUTO && usingWiFi);
  int  sosFlag   = sosTriggered ? 1 : 0;
  sosTriggered   = false;  // Clear immediately so it fires only once

  // Use stack char buffer — accommodates 6-axis IMU + secondary sensors
  char payload[128];
  if (routeWiFi) {
    snprintf(payload, sizeof(payload), "%.4f,%.4f,%.4f,%.2f,%.2f,%.2f,%.2f,%.2f,%d,%d,%d\n",
             mpu.getAccX(), mpu.getAccY(), mpu.getAccZ(),
             mpu.getGyroX(), mpu.getGyroY(), mpu.getGyroZ(),
             cachedTemp, cachedAmbientTemp, currentMoisturePercent, cachedRSSI, sosFlag);
  } else {
    snprintf(payload, sizeof(payload), "%.4f,%.4f,%.4f,%.2f,%.2f,%.2f,%.2f,%.2f,%d,0,%d\n",
             mpu.getAccX(), mpu.getAccY(), mpu.getAccZ(),
             mpu.getGyroX(), mpu.getGyroY(), mpu.getGyroZ(),
             cachedTemp, cachedAmbientTemp, currentMoisturePercent, sosFlag);
  }

  if (routeWiFi) {
    udp.beginPacket(gatewayIP, udpPort);
    udp.write((const uint8_t*)payload, strlen(payload));
    udp.endPacket();
  } else if (bleConnected) {
    pTxCharacteristic->setValue(payload);
    pTxCharacteristic->notify();
  }
}

void checkNetworkStability() {
  bool isConnectedWiFi = (WiFi.status() == WL_CONNECTED);

  // ── Wi-Fi just connected ──────────────────────────────────────────────────
  if (!usingWiFi && isConnectedWiFi) {
    systemPrint("WiFi Connection Established!");
    usingWiFi = true;
    if (netMode == MODE_AUTO) {
      setLED(0, 255, 0); // Solid Green
      // Stop BLE advertising — no need while Wi-Fi is healthy
      if (pServer != NULL) {
        pServer->getAdvertising()->stop();
        systemPrint("BLE Advertising Paused (WiFi active)");
      }
    }
  }

  // ── Wi-Fi just dropped ────────────────────────────────────────────────────
  else if (usingWiFi && !isConnectedWiFi) {
    systemPrint("WiFi Connection Lost! Starting BLE immediately...");
    usingWiFi = false;
    logError("WiFi Disconn");
    if (netMode == MODE_AUTO) {
      // Start BLE advertising immediately so client can connect ASAP
      if (pServer != NULL) {
        pServer->startAdvertising();
        systemPrint("BLE Advertising Started (WiFi lost)");
      }
    }
  }

  // ── Connection Hunting Logic (AUTO mode) ──────────────────────────────────
  if (netMode == MODE_AUTO) {
    if (!usingWiFi && !bleConnected) {
      // Hunting for both — blink Green/Blue
      if (millis() - lastBlinkTime > 500) {
        lastBlinkTime = millis();
        ledBlinkState = !ledBlinkState;
        if (ledBlinkState) setLED(0, 255, 0);
        else setLED(0, 128, 255);
      }
    }
    else if (!usingWiFi && bleConnected) {
      setLED(0, 128, 255); // Solid Blue — BLE fallback active
    }

    // While on BLE fallback, keep retrying Wi-Fi every 5s
    if (!usingWiFi) {
      static unsigned long lastReconnectAttempt = 0;
      if (millis() - lastReconnectAttempt > 5000) {
        lastReconnectAttempt = millis();
        systemPrint("Hunting WiFi...");
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

  // Edge-detect SOS: fire ONCE per physical press, 3s cooldown prevents hold-spam
  static bool    prevSos     = HIGH;
  static unsigned long lastSosTime = 0;
  bool curSos = (digitalRead(BTN_SOS) == LOW);
  bool sosEdge = (curSos == LOW && prevSos == HIGH);  // falling edge only
  prevSos = curSos;

  if (sosEdge && (millis() - lastSosTime > 3000)) {
    lastSosTime   = millis();
    lastButtonPressTime = millis();
    isScreenAwake = true;
    sosTriggered  = true;          // Flag for next payload — clears after one transmit
    // Non-blocking 500ms haptic
    digitalWrite(MOTOR_PIN, HIGH);
    sosMotorUntil = millis() + 500;
    if (currentMenu == MENU_OFF) currentMenu = MENU_MAIN;
    systemPrint("Help Call Triggered");
    return;  // No delay — hot path continues
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

  // --- Edge-detected UP button (fires once per physical press) ---
  static bool prevUp = HIGH;
  bool curUp = digitalRead(BTN_UP);
  if (curUp == LOW && prevUp == HIGH) {
    triggerHaptic(30);
    if      (currentMenu == MENU_MAIN)           mainMenuCursor = (mainMenuCursor - 1 + 4) % 4;
    else if (currentMenu == MENU_SENSOR_LIST)    sensorMenuCursor = (sensorMenuCursor - 1 + 3) % 3;
    else if (currentMenu == MENU_NETWORK)        networkMenuCursor = (networkMenuCursor - 1 + 4) % 4;
    else if (currentMenu == MENU_SERIAL_MONITOR && serialLogScroll < 15) serialLogScroll++;
  }
  prevUp = curUp;

  // --- Edge-detected DOWN button ---
  static bool prevDown = HIGH;
  bool curDown = digitalRead(BTN_DOWN);
  if (curDown == LOW && prevDown == HIGH) {
    triggerHaptic(30);
    if      (currentMenu == MENU_MAIN)           mainMenuCursor = (mainMenuCursor + 1) % 4;
    else if (currentMenu == MENU_SENSOR_LIST)    sensorMenuCursor = (sensorMenuCursor + 1) % 3;
    else if (currentMenu == MENU_NETWORK)        networkMenuCursor = (networkMenuCursor + 1) % 4;
    else if (currentMenu == MENU_SERIAL_MONITOR && serialLogScroll > 0) serialLogScroll--;
  }
  prevDown = curDown;

  // --- ENTER: tap = select, hold 1s = back ---
  if (digitalRead(BTN_ENTER) == LOW) {
    if (btnEnterPressTime == 0) btnEnterPressTime = millis();

    if (millis() - btnEnterPressTime > 1000 && !btnEnterHeld) {
      btnEnterHeld = true;
      triggerHaptic(100);

      if      (currentMenu == MENU_SENSOR_VIEW)  currentMenu = MENU_SENSOR_LIST;
      else if (currentMenu == MENU_NETWORK_TEST) currentMenu = MENU_NETWORK;
      else if (currentMenu == MENU_SENSOR_LIST || currentMenu == MENU_LOG ||
               currentMenu == MENU_NETWORK     || currentMenu == MENU_SERIAL_MONITOR) {
        currentMenu = MENU_MAIN;
        serialLogScroll = 0;
      }
      else if (currentMenu == MENU_MAIN) {
        currentMenu = MENU_OFF;
        isScreenAwake = false;
      }
    }
  } else {
    // Released — if it was a short tap, select
    if (btnEnterPressTime > 0 && !btnEnterHeld) {
      triggerHaptic(50);

      if (currentMenu == MENU_MAIN) {
        if      (mainMenuCursor == 0) currentMenu = MENU_SENSOR_LIST;
        else if (mainMenuCursor == 1) currentMenu = MENU_LOG;
        else if (mainMenuCursor == 2) currentMenu = MENU_NETWORK;
        else if (mainMenuCursor == 3) currentMenu = MENU_SERIAL_MONITOR;
      }
      else if (currentMenu == MENU_SENSOR_LIST) {
        currentMenu = MENU_SENSOR_VIEW;
      }
      else if (currentMenu == MENU_NETWORK) {
        if      (networkMenuCursor == 0) { netMode = MODE_AUTO;      usingWiFi = (WiFi.status() == WL_CONNECTED); setLED(0, usingWiFi ? 255 : 128, usingWiFi ? 0 : 255); }
        else if (networkMenuCursor == 1) { netMode = MODE_WIFI_ONLY; setLED(0, 255, 0); }
        else if (networkMenuCursor == 2) { netMode = MODE_BLE_ONLY;  setLED(0, 128, 255); }
        else if (networkMenuCursor == 3) { currentMenu = MENU_NETWORK_TEST; }
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

  // Throttle screen redraws to 4FPS to allow continuous animations 
  // without overwhelming the I2C bus while resting
  static unsigned long lastScreenDraw = 0;
  if (millis() - lastScreenDraw < 250) return;
  lastScreenDraw = millis();

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0,0);

  // ========================
  // MAIN MENU
  // ========================
  if (currentMenu == MENU_MAIN) {
    bool routeWiFi = (netMode == MODE_WIFI_ONLY) || (netMode == MODE_AUTO && usingWiFi);

    // Header — title left, signal indicators right
    display.setTextSize(1);
    display.setCursor(0, 0);
    display.print("SoterCare");

    // Wi-Fi indicator: "W" label at x=70, bars at x=77 (4 bars * 4px = 16px → ends at x=93)
    display.setCursor(70, 0);
    display.print("W");
    drawSignalBars(77, 0, getWiFiStrengthLevel());

    // BLE indicator: "B" label at x=97, bars at x=104 (ends at x=120)
    display.setCursor(97, 0);
    display.print("B");
    drawSignalBars(104, 0, getBLEStrengthLevel());

    // BLE mode badge overwrites center if on BLE only
    if (!routeWiFi && bleConnected) {
      display.setCursor(36, 0);
      display.print("[BT]");
    }

    // Divider
    display.drawFastHLine(0, 9, 128, SSD1306_WHITE);

    // Menu items — 4 items, each 12px tall from y=12
    const char* items[] = {"Sensor Test", "Error Log", "Network Mode", "Monitor"};
    for (int i = 0; i < 4; i++) {
      int itemY = 12 + (i * 12);
      if (mainMenuCursor == i) {
        display.fillRect(0, itemY - 1, 128, 11, SSD1306_WHITE);
        display.setTextColor(SSD1306_BLACK);
        display.setCursor(4, itemY);
        display.print(items[i]);
        display.setTextColor(SSD1306_WHITE);
      } else {
        display.setCursor(4, itemY);
        display.print(items[i]);
      }
    }
  }

  // ========================
  // SENSOR LIST
  // ========================
  else if (currentMenu == MENU_SENSOR_LIST) {
    display.setCursor(0, 0);
    display.print("  SENSOR TEST");
    display.drawFastHLine(0, 9, 128, SSD1306_WHITE);

    const char* sensors[] = {"IMU  (MPU6050)", "Temp (MLX)", "Moisture"};
    for (int i = 0; i < 3; i++) {
      int itemY = 13 + (i * 13);
      if (sensorMenuCursor == i) {
        display.fillRect(0, itemY - 1, 128, 11, SSD1306_WHITE);
        display.setTextColor(SSD1306_BLACK);
        display.setCursor(4, itemY);
        display.print(sensors[i]);
        display.setTextColor(SSD1306_WHITE);
      } else {
        display.setCursor(4, itemY);
        display.print(sensors[i]);
      }
    }

    display.drawFastHLine(0, 55, 128, SSD1306_WHITE);
    display.setCursor(10, 57);
    display.print("Hold ENTER: Back");
  }

  // ========================
  // SENSOR DATA VIEW
  // ========================
  else if (currentMenu == MENU_SENSOR_VIEW) {
    if (sensorMenuCursor == 0) {
      display.setCursor(0, 0);
      display.print("  IMU DATA");
      display.drawFastHLine(0, 9, 128, SSD1306_WHITE);
      display.setCursor(0, 13);
      display.printf("AccX: %6.3f g\n", mpu.getAccX());
      display.setCursor(0, 25);
      display.printf("AccY: %6.3f g\n", mpu.getAccY());
      display.setCursor(0, 37);
      display.printf("AccZ: %6.3f g\n", mpu.getAccZ());
    }
    else if (sensorMenuCursor == 1) {
      Wire.setClock(100000);
      float objTemp = mlx.readObjectTempC();
      float ambTemp = mlx.readAmbientTempC();
      Wire.setClock(400000);
      display.setCursor(0, 0);
      display.print("  TEMPERATURE");
      display.drawFastHLine(0, 9, 128, SSD1306_WHITE);
      display.setCursor(0, 13);
      display.printf("Object : %5.2f C\n", objTemp);
      display.setCursor(0, 25);
      display.printf("Ambient: %5.2f C\n", ambTemp);
    }
    else if (sensorMenuCursor == 2) {
      display.setCursor(0, 0);
      display.print("  MOISTURE");
      display.drawFastHLine(0, 9, 128, SSD1306_WHITE);
      display.setCursor(0, 13);
      display.printf("Raw ADC : %4d\n", currentRawMoisture);
      display.setCursor(0, 25);
      display.printf("Level   : %3d %%\n", currentMoisturePercent);

      // Draw a simple progress bar at y=42
      int barWidth = map(currentMoisturePercent, 0, 100, 0, 120);
      display.drawRect(4, 42, 120, 8, SSD1306_WHITE);
      display.fillRect(4, 42, barWidth, 8, SSD1306_WHITE);
    }

    display.drawFastHLine(0, 55, 128, SSD1306_WHITE);
    display.setCursor(10, 57);
    display.print("Hold ENTER: Back");
  }

  // ========================
  // ERROR LOG
  // ========================
  else if (currentMenu == MENU_LOG) {
    display.setCursor(0, 0);
    display.print("  ERROR LOG");
    display.drawFastHLine(0, 9, 128, SSD1306_WHITE);

    bool hasErrors = false;
    int lineY = 12;
    for (int i = 0; i < 5; i++) {
      if (errorLogs[i].timestamp > 0) {
        hasErrors = true;
        int secs = errorLogs[i].timestamp / 1000;
        display.setCursor(0, lineY);
        display.printf("%02d:%02d %s", secs / 60, secs % 60, errorLogs[i].message.c_str());
        lineY += 10;
      }
    }
    if (!hasErrors) {
      display.setCursor(18, 28);
      display.print("No Errors Logged");
    }

    display.drawFastHLine(0, 55, 128, SSD1306_WHITE);
    display.setCursor(10, 57);
    display.print("Hold ENTER: Back");
  }

  // ========================
  // NETWORK MODE
  // ========================
  else if (currentMenu == MENU_NETWORK) {
    display.setCursor(0, 0);
    display.print("  NETWORK MODE");
    display.drawFastHLine(0, 9, 128, SSD1306_WHITE);

    const char* netItems[] = {"AUTO", "WI-FI ONLY", "BLE ONLY", "Test Network"};
    bool activeState[] = {netMode == MODE_AUTO, netMode == MODE_WIFI_ONLY, netMode == MODE_BLE_ONLY, false};

    for (int i = 0; i < 4; i++) {
      int itemY = 12 + (i * 11);
      if (networkMenuCursor == i) {
        display.fillRect(0, itemY - 1, 128, 10, SSD1306_WHITE);
        display.setTextColor(SSD1306_BLACK);
        display.setCursor(4, itemY);
        display.print(i < 3 && activeState[i] ? "* " : "  ");
        display.print(netItems[i]);
        display.setTextColor(SSD1306_WHITE);
      } else {
        display.setCursor(4, itemY);
        display.print(i < 3 && activeState[i] ? "* " : "  ");
        display.print(netItems[i]);
      }
    }
  }

  // ========================
  // NETWORK TEST
  // ========================
  else if (currentMenu == MENU_NETWORK_TEST) {
    display.setCursor(0, 0);
    display.print("  NETWORK TEST");
    display.drawFastHLine(0, 9, 128, SSD1306_WHITE);

    if (WiFi.status() == WL_CONNECTED) {
      display.setCursor(0, 12);
      display.print("Status: CONNECTED");
      display.setCursor(0, 23);
      display.print("IP: "); display.print(WiFi.localIP());
      display.setCursor(0, 34);
      display.printf("RSSI:   %d dBm", WiFi.RSSI());
      display.setCursor(0, 45);
      display.print("GW: "); display.print(gatewayIP);
    } else {
      display.setCursor(14, 18);
      display.print("DISCONNECTED");
      display.setCursor(8, 32);
      display.print("Check router or");
      display.setCursor(20, 42);
      display.print("env.h SSID");
    }

    display.drawFastHLine(0, 55, 128, SSD1306_WHITE);
    display.setCursor(10, 57);
    display.print("Hold ENTER: Back");
  }

  // ========================
  // SERIAL MONITOR
  // ========================
  else if (currentMenu == MENU_SERIAL_MONITOR) {
    display.setCursor(0, 0);
    display.print("  MONITOR");
    display.drawFastHLine(0, 9, 128, SSD1306_WHITE);

    String orderedLogs[20];
    int logCount = 0;
    for (int i = 0; i < 20; i++) {
      int idx = (serialLogIndex + i) % 20;
      if (serialLogs[idx].length() > 0) {
        orderedLogs[logCount++] = serialLogs[idx];
      }
    }

    const int LINES_PER_PAGE = 5;
    int lastVisible  = logCount - 1 - serialLogScroll;
    int firstVisible = lastVisible - LINES_PER_PAGE + 1;

    for (int i = 0; i < LINES_PER_PAGE; i++) {
      int logIdx = firstVisible + i;
      display.setCursor(0, 12 + (i * 10));
      if (logIdx >= 0 && logIdx < logCount) {
        display.print(orderedLogs[logIdx].substring(0, 21));
      }
    }

    // Scroll indicator on far right
    if (serialLogScroll > 0) {
      display.setCursor(116, 57);
      display.print("/\\");
    }
  }

  display.display();
}
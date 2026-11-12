#include <Arduino.h>

// ==============================================================
// ESP32 DEVKIT V1 - DekDo PURE HIGH-SPEED USB CABLE MODE
// Bluetooth is completely DISABLED.
// 100% Reliable, Instant Zero-Lag USB Serial Stream at 115200 baud
// Sends:
//   J,x,y      (Joystick coordinates every 20ms)
//   B,id,state (Buttons 1-4 press/release with musical chirp)
//   S,state    (Joystick Push Switch press/release)
// Receives:
//   BEEP_CONNECT, BEEP_LIMIT, BEEP_MUTE_ON, BEEP_MUTE_OFF
// ==============================================================

// Four buttons
const int buttonPins[4] = {13, 14, 25, 26};

// Joystick pins
const int joyXPin = 34;
const int joyYPin = 35;
const int joySwPin = 27;

// Buzzer pin
const int buzzerPin = 18;

// Timing
const unsigned long debounceMs = 25;
const unsigned long joyIntervalMs = 20;

// Debounce states
bool stable[4];
bool lastRead[4];
unsigned long changedAt[4];

bool swStable;
bool swLast;
unsigned long swChangedAt = 0;

unsigned long lastJoyAt = 0;

// Button-specific musical chord tones
const int buttonFrequencies[4] = {1046, 1318, 1568, 2093};

void beep(int freq = 2000, int durationMs = 30) {
  tone(buzzerPin, freq, durationMs);
}

void playConnectedChirp() {
  tone(buzzerPin, 1760, 50);
  delay(60);
  tone(buzzerPin, 2637, 80);
}

void playVolumeLimitBeep() {
  tone(buzzerPin, 2800, 30);
  delay(40);
  tone(buzzerPin, 2800, 30);
}

void playMuteSound(bool isMuted) {
  if (isMuted) {
    tone(buzzerPin, 1500, 60);
    delay(70);
    tone(buzzerPin, 1000, 90);
  } else {
    tone(buzzerPin, 1000, 60);
    delay(70);
    tone(buzzerPin, 1800, 90);
  }
}

void playUnlockFanfare() {
  tone(buzzerPin, 1318, 50); // E6
  delay(60);
  tone(buzzerPin, 1568, 50); // G6
  delay(60);
  tone(buzzerPin, 2093, 70); // C7
  delay(80);
  tone(buzzerPin, 2637, 120); // E7
}

void handleIncomingCommand(String cmd) {
  cmd.trim();
  if (cmd == "BEEP_CONNECT") {
    playConnectedChirp();
  } else if (cmd == "BEEP_LIMIT") {
    playVolumeLimitBeep();
  } else if (cmd == "BEEP_MUTE_ON") {
    playMuteSound(true);
  } else if (cmd == "BEEP_MUTE_OFF") {
    playMuteSound(false);
  } else if (cmd == "BEEP_UNLOCK") {
    playUnlockFanfare();
  }
}

void setup() {
  // Initialize USB Serial
  Serial.begin(115200);

  // Configure Buzzer
  pinMode(buzzerPin, OUTPUT);
  digitalWrite(buzzerPin, LOW);
  beep(2400, 70); // Startup chirp

  // Configure 4 push buttons (Internal pull-up, active LOW to GND)
  for (int i = 0; i < 4; i++) {
    pinMode(buttonPins[i], INPUT_PULLUP);
    stable[i] = digitalRead(buttonPins[i]);
    lastRead[i] = stable[i];
    changedAt[i] = millis();
  }

  // Configure Joystick Push Switch
  pinMode(joySwPin, INPUT_PULLUP);
  swStable = digitalRead(joySwPin);
  swLast = swStable;

  // Set 12-bit ADC Resolution (0 - 4095)
  analogReadResolution(12);

  Serial.println("READY");
}

void loop() {
  unsigned long now = millis();

  // ============================================================
  // 1. READ FOUR PUSH BUTTONS
  // ============================================================
  for (int i = 0; i < 4; i++) {
    bool reading = digitalRead(buttonPins[i]);

    if (reading != lastRead[i]) {
      lastRead[i] = reading;
      changedAt[i] = now;
    }

    if ((now - changedAt[i] >= debounceMs) && (reading != stable[i])) {
      stable[i] = reading;
      if (reading == LOW) {
        beep(buttonFrequencies[i], 30);
      }
      Serial.printf("B,%d,%d\n", i + 1, (reading == LOW) ? 1 : 0);
    }
  }

  // ============================================================
  // 2. READ JOYSTICK CENTER PUSH SWITCH
  // ============================================================
  bool swReading = digitalRead(joySwPin);

  if (swReading != swLast) {
    swLast = swReading;
    swChangedAt = now;
  }

  if ((now - swChangedAt >= debounceMs) && (swReading != swStable)) {
    swStable = swReading;
    if (swStable == LOW) {
      beep(1500, 40);
    }
    Serial.printf("S,%d\n", (swStable == LOW) ? 1 : 0);
  }

  // ============================================================
  // 3. READ INCOMING COMMANDS FROM LAPTOP (Buzzer triggers)
  // ============================================================
  while (Serial.available()) {
    String c = Serial.readStringUntil('\n');
    handleIncomingCommand(c);
  }

  // ============================================================
  // 4. READ ANALOG JOYSTICK (X & Y)
  // ============================================================
  if (now - lastJoyAt >= joyIntervalMs) {
    lastJoyAt = now;

    int xVal = analogRead(joyXPin);
    int yVal = analogRead(joyYPin);

    Serial.printf("J,%d,%d\n", xVal, yVal);
  }
}

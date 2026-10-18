#include <WiFi.h>
#include <PubSubClient.h>
#include <DHTesp.h>
#include <math.h>
#include <time.h>
#include <sys/time.h>
#include <esp_system.h>
#include <esp_sntp.h>
#include <string.h>

const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASSWORD = "";
const char* MQTT_SERVER = "broker.hivemq.com";
const int MQTT_PORT = 1883;
const char* MQTT_TOPIC = "ptit/B23DCCN578/iot2/esp32-01/telemetry";
const char* PROBE_TOPIC = "ptit/B23DCCN578/iot2/esp32-01/probe";
const char* DEVICE_ID = "esp32-01";

const int DHT_PIN = 15;
const int TRIG_PIN = 5;
const int ECHO_PIN = 18;
const int LED_PIN = 2;
const unsigned long SEND_INTERVAL_MS = 5000;

DHTesp dht;
WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);
unsigned long lastSend = 0;
unsigned long lastSample = 0;
unsigned long sequenceNo = 0;
char bootId[17];
char pendingTrace[33] = "";
bool probePending = false;
volatile bool clockSynced = false;
volatile unsigned long lastSyncMs = 0;

// Requests contain exactly 32 lowercase hexadecimal characters.
// Copy the payload: PubSubClient reuses its receive buffer.
void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  if (strcmp(topic, PROBE_TOPIC) != 0 || length != 32 || probePending) return;
  for (unsigned int i = 0; i < length; i++) {
    char c = (char)payload[i];
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return;
  }
  memcpy(pendingTrace, payload, 32);
  pendingTrace[32] = '\0';
  probePending = true;
  Serial.println("Probe request received");
}

void onTimeSync(struct timeval* tv) {
  lastSyncMs = millis();
  clockSynced = true;
}

uint64_t unixTimeMs() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return (uint64_t)tv.tv_sec * 1000ULL + (uint64_t)tv.tv_usec / 1000ULL;
}

float readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  unsigned long duration = pulseIn(ECHO_PIN, HIGH, 30000);
  return duration == 0 ? NAN : duration * 0.0343f / 2.0f;
}

void connectWiFi() {
  Serial.print("Dang ket noi WiFi");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD, 6);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected!");
}

void connectMQTT() {
  while (!mqttClient.connected()) {
    if (WiFi.status() != WL_CONNECTED) connectWiFi();
    String clientId = String("IoT2-") + DEVICE_ID + "-" + bootId;
    if (mqttClient.connect(clientId.c_str())) {
      Serial.println("MQTT connected!");
      if (mqttClient.subscribe(PROBE_TOPIC, 0)) {
        Serial.println("Probe listener ready!");
      } else {
        Serial.println("Probe subscribe FAILED - reconnecting");
        mqttClient.disconnect();
        delay(2000);
      }
    } else {
      Serial.print("MQTT FAILED rc=");
      Serial.println(mqttClient.state());
      delay(2000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(TRIG_PIN, LOW);
  digitalWrite(LED_PIN, LOW);
  dht.setup(DHT_PIN, DHTesp::DHT22);
  connectWiFi();

  snprintf(bootId, sizeof(bootId), "%08lx%08lx",
           (unsigned long)esp_random(), (unsigned long)esp_random());
  sntp_set_time_sync_notification_cb(onTimeSync);
  sntp_set_sync_interval(30000);
  configTime(0, 0, "pool.ntp.org", "time.google.com");

  Serial.println("Dang dong bo gio NTP...");
  unsigned long started = millis();
  while (!clockSynced && millis() - started < 20000) delay(100);
  Serial.println(clockSynced ? "NTP synced!" : "NTP chua dong bo; se tiep tuc cho.");

  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
  mqttClient.setBufferSize(768);
  mqttClient.setCallback(onMqttMessage);
  connectMQTT();
  Serial.print("Boot ID: ");
  Serial.println(bootId);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  if (!mqttClient.connected()) connectMQTT();
  mqttClient.loop();

  unsigned long now = millis();
  bool periodicDue = now - lastSend >= SEND_INTERVAL_MS;
  if (!periodicDue && !probePending) return;
  // Ensure a fresh DHT22 read even when a request follows a periodic sample.
  if (now - lastSample < 2100) return;

  if (!clockSynced) {
    if (periodicDue) {
      lastSend = now;
      Serial.println("Dang cho NTP; chua gui timestamp khong hop le.");
    }
    return;
  }

  lastSend = now;
  lastSample = now;
  char traceId[33] = "";
  if (probePending) memcpy(traceId, pendingTrace, sizeof(traceId));

  // Timestamp at the start of sensor acquisition, in Unix milliseconds.
  uint64_t sampledAt = unixTimeMs();
  unsigned long syncAge = millis() - lastSyncMs;
  TempAndHumidity data = dht.getTempAndHumidity();
  float distance = readDistanceCm();
  if (!isfinite(data.temperature) || !isfinite(data.humidity) || !isfinite(distance)) {
    Serial.println("Du lieu cam bien khong hop le - bo qua");
    return;
  }

  sequenceNo++;
  char payload[512];
  int length = snprintf(payload, sizeof(payload),
    "{\"device_id\":\"%s\",\"boot_id\":\"%s\",\"trace_id\":\"%s\",\"ts_ms\":%llu,"
    "\"ntp_sync_age_ms\":%lu,\"temperature\":%.2f,\"humidity\":%.2f,"
    "\"distance_cm\":%.2f,\"rssi\":%d,\"sequence\":%lu,\"uptime_s\":%lu}",
    DEVICE_ID, bootId, traceId, (unsigned long long)sampledAt, syncAge,
    data.temperature, data.humidity, distance, WiFi.RSSI(), sequenceNo,
    (unsigned long)(millis() / 1000));
  if (length < 0 || (size_t)length >= sizeof(payload)) {
    Serial.println("Payload qua dai - khong gui");
    return;
  }

  bool ok = mqttClient.publish(MQTT_TOPIC, payload);
  Serial.println(payload);
  Serial.println(ok ? "Publish: OK" : "Publish: FAILED");
  if (ok) {
    if (traceId[0] != '\0') {
      probePending = false;
      pendingTrace[0] = '\0';
    }
    digitalWrite(LED_PIN, HIGH);
    delay(100);
    digitalWrite(LED_PIN, LOW);
  }
}

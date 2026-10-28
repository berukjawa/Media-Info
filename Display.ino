#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_ADDR 0x3C

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

String title = "";
String album = "";
String artist = "";
String inputLine = "";

int offTitle = 0, offArtist = 0, offAlbum = 0;
unsigned long lastTick = 0;
const int SCROLL_GAP = 30;
const int SCROLL_STEP = 2;
const int SCROLL_MS = 60;

bool connected = false;
unsigned long lastRx = 0;
const unsigned long TIMEOUT_MS = 5000;

unsigned long posBaseMs = 0;
unsigned long posBaseTick = 0;
unsigned long durMs = 0;
bool playing = false;
const long SYNC_THRESHOLD_MS = 2000;

unsigned long currentPosMs() {
  unsigned long p = posBaseMs;
  if (playing) p += millis() - posBaseTick;
  if (durMs > 0 && p > durMs) p = durMs;
  return p;
}

String fmtTime(unsigned long ms) {
  unsigned long t = ms / 1000;
  unsigned long h = t / 3600;
  unsigned long m = (t % 3600) / 60;
  unsigned long s = t % 60;
  char buf[12];
  if (h > 0) snprintf(buf, sizeof(buf), "%lu:%02lu:%02lu", h, m, s);
  else       snprintf(buf, sizeof(buf), "%lu:%02lu", m, s);
  return String(buf);
}

void drawProgress(int barY, int timeY) {
  unsigned long pos = currentPosMs();

  if (durMs > 0) {
    display.drawRect(0, barY, SCREEN_WIDTH, 6, SSD1306_WHITE);
    int fillW = (int)(((unsigned long long)pos * (SCREEN_WIDTH - 2)) / durMs);
    if (fillW > 0) display.fillRect(1, barY + 1, fillW, 4, SSD1306_WHITE);
  }

  display.setTextSize(1);
  display.setCursor(0, timeY);
  display.print(fmtTime(pos));

  if (durMs > 0) {
    String d = fmtTime(durMs);
    display.setCursor(SCREEN_WIDTH - d.length() * 6, timeY);
    display.print(d);
  }
}

void drawLine(const String &text, int y, int size, int &offset, bool step) {
  display.setTextSize(size);
  int w = text.length() * 6 * size;
  if (w <= SCREEN_WIDTH) {
    offset = 0;
    display.setCursor(0, y);
    display.print(text);
  } else {
    display.setCursor(-offset, y);
    display.print(text);
    display.setCursor(-offset + w + SCROLL_GAP, y);
    display.print(text);
    if (step) offset = (offset + SCROLL_STEP) % (w + SCROLL_GAP);
  }
}

void render(bool step) {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextWrap(false);

  if (!connected) {
    display.setTextSize(2);
    display.setCursor(4, 0);
    display.print("Media Info");

    display.drawLine(0, 17, SCREEN_WIDTH - 1, 17, SSD1306_WHITE);

    display.setTextSize(1);
    display.setCursor(0, 20);
    display.print("Jika Anda melihat");
    display.setCursor(0, 29);
    display.print("tulisan ini, berarti");
    display.setCursor(0, 38);
    display.print("program Anda belum");
    display.setCursor(0, 47);
    display.print("terhubung dgn display");

    display.setCursor(4, 56);
    display.print("by berukjawa");
  } else if (title.length() == 0) {
    display.setTextSize(1);
    display.setCursor(0, 28);
    display.print("Standby");
  } else {
    drawLine(title, 0, 2, offTitle, step);
    int y = 20;
    if (artist.length()) {
      drawLine(artist, y, 1, offArtist, step);
      y += 10;
    }
    if (album.length()) {
      drawLine(album, y, 1, offAlbum, step);
    }
    drawProgress(42, 54);
  }
  display.display();
}

String getField(const String &s, int index) {
  int start = 0;
  for (int i = 0; i < index; i++) {
    start = s.indexOf('|', start);
    if (start < 0) return "";
    start++;
  }
  int end = s.indexOf('|', start);
  if (end < 0) end = s.length();
  return s.substring(start, end);
}

void resetState() {
  title = ""; album = ""; artist = "";
  offTitle = offArtist = offAlbum = 0;
  posBaseMs = 0;
  durMs = 0;
  playing = false;
}

void parseLine(const String &line) {
  if (line.indexOf("MEDIAINFO_HELLO") >= 0) {
    Serial.println("MEDIAINFO_ACK");
    return;
  }

  bool wasConnected = connected;
  connected = true;
  lastRx = millis();

  String t = getField(line, 0);
  t.trim();

  if (t == "DISCONNECT") {
    connected = false;
    resetState();
    render(false);
    return;
  }

  if (t == "PING") {
    if (!wasConnected) render(false);
    return;
  }

  if (t == "CLEAR" || t.length() == 0) {
    resetState();
  } else {
    String newAlbum = getField(line, 1);
    String newArtist = getField(line, 2);
    newAlbum.trim();
    newArtist.trim();

    long nPos = getField(line, 4).toInt();
    long nDur = getField(line, 5).toInt();
    bool nPlaying = getField(line, 6).toInt() == 1;
    if (nPos < 0) nPos = 0;
    if (nDur < 0) nDur = 0;

    bool songChanged = (t != title || newArtist != artist || newAlbum != album);

    unsigned long cur = currentPosMs();
    long diff = nPos - (long)cur;
    if (diff < 0) diff = -diff;
    if (songChanged || !nPlaying || nPlaying != playing || diff > SYNC_THRESHOLD_MS) {
      posBaseMs = (unsigned long)nPos;
      posBaseTick = millis();
    }

    if (songChanged) {
      offTitle = offArtist = offAlbum = 0;
    }

    title = t;
    album = newAlbum;
    artist = newArtist;
    durMs = (unsigned long)nDur;
    playing = nPlaying;
  }
  render(false);
}

void setup() {
  Serial.begin(115200);
  Wire.begin();

  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    while (true) delay(1000);
  }
  render(false);
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      parseLine(inputLine);
      inputLine = "";
    } else if (c != '\r') {
      if (inputLine.length() < 300) inputLine += c;
    }
  }

  if (connected && millis() - lastRx > TIMEOUT_MS) {
    connected = false;
    resetState();
    inputLine = "";
    render(false);
  }

  if (millis() - lastTick >= SCROLL_MS) {
    lastTick = millis();
    render(true);
  }
}
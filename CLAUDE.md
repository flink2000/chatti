# CLAUDE.md — Chatti

> Projektkontext für Claude Code. **Lies diese Datei vollständig, bevor du etwas änderst.**
> Sie ersetzt kein Nachschlagen im Code, aber sie erspart dir die Fehler, die hier schon
> einmal gemacht wurden.

**Antworte auf Deutsch.** Code, Kommentare, Commit-Messages und Log-Ausgaben auf Englisch.

---

## 1. Was das ist

Ein Sprachassistent auf einem ~30-€-ESP32-Board, dessen Intelligenz **vollständig lokal auf
dem PC** läuft. Das Gerät ist bewusst dünn: Display, Mikrofon, Lautsprecher. Sonst nichts.

- **Gesicht statt Chat-UI** — zwei animierte Roboter-Augen, prozedural über LVGL. Im Ruhezustand
  blinzeln sie und schauen umher; sobald Text auf dem Schirm steht, blenden sie aus.
- **Untertitel im Takt der Stimme** — der Server misst die Sprechdauer jedes Satzes, das Gerät
  scrollt den Text über genau diese Dauer.
- **Bedienung per Touch** — aufs Gesicht tippen startet das Gespräch, nochmal tippen beendet es.
- **Alles lokal** — STT, LLM und TTS auf dem PC. Kein Konto, kein API-Schlüssel, keine Cloud.

Das Repo ist ein **Fork von [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)**. Alles,
was wir *hinzufügen*, liegt unter `chatti/` — dort kann es nie mit einer Upstream-Datei
kollidieren. Was wir am xiaozhi-Code selbst *ändern*, bleibt an Ort und Stelle.

⚠️ **Die Upstream-Historie wurde am 2026-08-19 gelöscht** (ein einziger Commit). `git merge
upstream/main` scheitert deshalb mit *"refusing to merge unrelated histories"* — Upstream-
Änderungen müssen von Hand übertragen werden.

---

## 2. Ordnerstruktur

```
<repo>/                     das Git-Repo (origin = flink2000/chatti)
├── CLAUDE.md               diese Datei
├── README.md               die öffentliche Anleitung (englisch)
├── main/                   Firmware-Quellen — überwiegend Upstream
│   ├── boards/waveshare/esp32-s3-touch-lcd-1.83/
│   │   ├── chatti_face_display.h/.cc   das Gesicht (unser Code)
│   │   ├── config.h                    Pins, Display-Drehung, Touch
│   │   └── esp32-s3-touch-lcd-1.83.cc  Board-Klasse
│   ├── chatti_discovery.h/.cc          mDNS-Suche nach dem Server (unser Code)
│   ├── application.cc                  Zustandsmaschine (Upstream, minimal gepatcht)
│   └── display/, protocols/, audio/    Upstream
├── chatti/                 alles, was uns gehört
│   ├── control/            Chatti Control — Steuerzentrale im Browser
│   │   ├── app/            FastAPI-Backend
│   │   └── web/            die Seite
│   ├── server/             Patches für xiaozhi-esp32-server + docker-compose.yml
│   │   └── data/           Laufzeitdaten, NICHT in Git (Gespräche, .config.yaml)
│   ├── case/               druckbares Gehäuse (2× STL)
│   ├── images/             Bilder für die README
│   ├── build-win.py        Build-Wrapper (Upstream-build.py bricht unter Windows)
│   ├── build-releases.ps1  baut alle Sprachvarianten nach releases/
│   └── idf-init.ps1        ESP-IDF-Umgebung in der aktuellen Shell
├── build/                  Build-Ausgabe, ignoriert
├── managed_components/     von IDF geholte Fremdkomponenten, ignoriert
└── docs/upstream/          die ursprüngliche xiaozhi-Doku
```

**Außerhalb des Repos**, auf dem Entwicklungsrechner:

| Ort | Inhalt |
|---|---|
| `D:\chatti\refs\` | Waveshare-Demos + **Schaltplan** (309 MB, Apache-2.0, jederzeit neu klonbar) |
| `D:\Espressif\` | ESP-IDF-Toolchain 6.0.2 (~7,8 GB) |

---

## 3. Was auf dem PC laufen muss

| Programm | Wofür | Anmerkung |
|---|---|---|
| **Docker Desktop** | Server + Sprachdienste | zwei Container aus einer `docker-compose.yml` |
| **LM Studio** | das LLM | beliebiges OpenAI-kompatibles Modell |
| **ESP-IDF 6.0.2** | nur zum Bauen der Firmware | **nicht 5.4.x** — Upstream fordert `idf >=5.5.2` |

```
ESP32 ──WebSocket──> xiaozhi-server (Docker, :8000 WS / :8003 OTA)
                        ├── STT ─> Speaches   :8100
                        ├── LLM ─> LM Studio  :1234
                        └── TTS ─> Speaches   :8100
```

Speaches bedient STT **und** TTS aus einem Container über reines OpenAI-Protokoll — ein eigener
Piper-Provider war nie nötig. Container erreichen Hostdienste über `host.docker.internal`,
nicht `localhost`. Speaches liegt auf **8100**, weil es selbst 8000 will.

**Alles starten:** `chatti\control\chatti-control.cmd` → Browser → Knopf. Von Hand geht auch
`docker compose -f chatti/server/docker-compose.yml up -d`.

---

## 4. An der Firmware arbeiten

```powershell
. chatti\idf-init.ps1
python chatti\build-win.py waveshare/esp32-s3-touch-lcd-1.83 --language de-DE
idf.py -C . -p COM5 flash

# Beide Auslieferungsvarianten am Stück nach releases\ (eigene Shell, initialisiert selbst)
powershell -File chatti\build-releases.ps1
```

Erster Build ~13 min, danach inkrementell. **`flash` blockiert die Session nicht** und darf
ausgeführt werden. **`idf.py monitor` dagegen nicht** — es blockiert und belegt den Port.

Seriell mitlesen stattdessen über einen eigenen kurzen `SerialPort`-Mitschnitt mit Zeitlimit:
`Encoding` auf UTF-8 (sonst werden Umlaute zu `?`), sauberer Reset über `DtrEnable=$false`,
`RtsEnable=$true`, 300 ms, dann `RtsEnable=$false`. **Nie zwei Mitschnitte gleichzeitig.**
Ein Mitschnitt startet das Board neu (~15 s Bootzeit).

**Regeln, die teuer erkauft sind:**

1. **Niemals Pin-Nummern, Register oder I2C-Adressen raten.** Erst in der Board-Datei
   nachsehen, sonst im Schaltplan. Nicht findbar → fragen, nicht erfinden.
2. **Erst nachschlagen, dann ändern** bei jeder nicht-trivialen Änderung (Abschnitt 6).
3. **Änderungen klein halten.** Ein Modul, das kompiliert und läuft, schlägt drei gleichzeitig.
4. **Bei Compile-Fehlern die vollständige Meldung anfordern**, nicht spekulativ mehreres ändern.
5. **Sag es, wenn ein Ansatz schlecht ist.** Ehrliche Einschätzung, keine Bestätigung.
6. **Diese Datei aktualisieren**, wenn etwas Verifiziertes sich als falsch erweist.

**Regeln aus dem Upstream-Code, die weiter gelten** (standen in dessen `AGENTS.md`,
die am 2026-08-19 entfernt wurde, weil sie ein anderes Projekt beschrieb):

- ⚠️ **Callbacks laufen nicht im Haupt-Task.** Zustandsänderungen der Anwendung gehören in
  `Application::Schedule()` oder über Event-Bits — nicht direkt aus dem Callback heraus.
- ⚠️ **Den Haupt-Event-Loop und die Audio-Tasks niemals blockieren.** Keine unbegrenzten
  Warteschlangen, keine wiederholten großen Allokationen im Audio-Pfad.
- **Zustandswechsel nur über `Application::SetDeviceState()`** und die Zustandsmaschine.
- **Kernmodule hängen an den `Board`-Schnittstellen**, nie an einer konkreten Board-Klasse oder
  deren `config.h`. Board-spezifisches Verhalten gehört nicht in den Kern.
- **Pins eines bestehenden Boards nie für andere Hardware ändern** — die Board-Identität hängt
  an der OTA-Kompatibilität. Stattdessen eine eigene Variante anlegen.
- **Die Board-Auswahl ist eine Kette:** `config.json` → `scripts/build.py` →
  `main/Kconfig.projbuild` → `main/CMakeLists.txt` → Board-Quelle und `config.h`. Wer ein Board
  ändert, muss jedes Glied prüfen.
- **NVS-Schlüssel sind dauerhafte API.** Wer einen umbenennt, braucht eine Migration.
- **Nicht von Hand editieren:** `build/`, `managed_components/`, `components/`, `sdkconfig*`,
  `main/assets/lang_config.h` und generierte mmap-Header. Alles davon wird erzeugt.
- ⚠️ **Ein erfolgreicher Build ist keine Hardware-Prüfung.** Immer dazusagen, was gebaut und was
  tatsächlich am Gerät gesehen wurde.

⚠️ **`merged-binary.bin` an `0x0` löscht die Geräteeinstellungen.** Die Datei füllt die Lücken
zwischen den Images mit `0xFF` — auch die NVS-Partition bei `0x9000`. Danach sind WLAN-Zugang
*und* `ota_url` weg. Für eine Erstinstallation richtig, für ein Update falsch. Der Weg, der die
Einstellungen behält, ist `idf.py flash` bzw. `@flash_args`: jedes Image an seinen Offset.

---

## 5. Verifizierte Hardware-Werte

**Board: Waveshare ESP32-S3-Touch-LCD-1.83** (ohne Akku). ESP32-S3R8, 8 MB PSRAM (Octal,
80 MHz), 16 MB Flash. Nativer USB (USB-Serial-JTAG, kein Treiber nötig).

| Bereich | Werte |
|---|---|
| I2S Audio | MCLK 16, WS 45, BCLK 9, DIN 10, DOUT 8; PA-Enable 46 |
| I2C (geteilt) | SDA **15**, SCL **14** — Codec, Touch und PMIC am selben Bus |
| Display SPI | CS 5, MOSI 7, CLK 6, DC 4, RST 38, Backlight 40 |
| Display | Panel **240 × 284**, **quer betrieben: 284 × 240**, `swap_xy` + `mirror_y`, **Offset 36/0** |
| Touch | **CST816S**, separat gedreht: `swap_xy` + `mirror_y`, Grenzen bleiben die **rohen** 239/283 |
| Codec | ES8311 (Wiedergabe + Aufnahme), ES7210 (4 Mikrofone, TDM, Hardware-AEC) |
| Audio-Rate | **24 kHz** rein und raus |
| PMIC | AXP2101 auf I2C **0x34** |
| Buttons | BOOT = **GPIO 0** |

⚠️ **PWR ist kein GPIO und per Software nicht lesbar.** Die Taste geht ausschließlich an den
`PWRON`-Pin des AXP2101. Das `PWR_BUTTON_GPIO GPIO_NUM_41` in `config.h` ist irreführend —
laut Schaltplan führt GPIO 41 das Netz **`SYS_OUT`**, eine Versorgungsleitung. Ein- und
Ausschalten erledigt der PMIC in Hardware; dafür ist kein Firmware-Code nötig und keiner möglich.

⚠️ **Der 36-px-Offset der Drehung.** Der ST7789 hat 240 × 320 Bildspeicher, das Panel zeigt
240 × 284 (Zeilen 0…283) — hochkant deshalb ohne Offset. `mirror_y` kehrt die Zeilenachse um,
der sichtbare Ausschnitt liegt dann bei 36…319. Es ist ein **x**-Offset, weil nach `swap_xy`
die UI-x-Achse die Zeilenachse *ist*. Andersherum drehen: `MIRROR_X true`, `MIRROR_Y false`,
`OFFSET_X 0` — und die Touch-Flags im selben Griff mit.

---

## 6. Wo nachschlagen — in dieser Reihenfolge

Lokal vor Web: die lokalen Quellen sind genau der Code, der bei uns gebaut wird.

| # | Quelle | Ort |
|---|---|---|
| 1 | **Diese Datei** | Ist das Problem schon gelöst? |
| 2 | **Unser Board** | `main/boards/waveshare/esp32-s3-touch-lcd-1.83/` |
| 3 | **Andere Boards** | `main/boards/*/` — irgendein Board hat es meist schon gelöst |
| 4 | **xiaozhi-Kern** | `main/application.cc`, `main/protocols/`, `main/display/`, `main/audio/` |
| 5 | **Fremdkomponenten** | `managed_components/` — **exakte API der gebauten Version**, u. a. LVGL **9.5.0** |
| 6 | **ESP-IDF** | `D:\Espressif\frameworks\esp-idf-v6.0.2\components\` und `…\examples\` |
| 7 | **Schaltplan** | `D:\chatti\refs\schematic\` — schlägt jede Software-Annahme über die Hardware |
| 8 | **Serverseite** | **laufender Container** (`docker cp`), nicht die Repo-Kopie |
| 9 | **Web** | LVGL-Doku für **9.x**, ESP-IDF **v6.0**, Datenblätter |

```powershell
rg -n "SetChatMessage" main                      # wer ruft das auf
rg -ln "lvgl_port_add_touch" main\boards         # welches Board macht das schon
rg -n "lv_anim_set_" managed_components\lvgl__lvgl\src\misc\lv_anim.h   # exakte Signatur
```

**Backtrace auflösen:**
`D:\Espressif\tools\xtensa-esp-elf\…\xtensa-esp32s3-elf-addr2line.exe -pfiaC -e build\xiaozhi.elf <adressen>`

**Danach:** Fundstelle nennen (`datei.cc:123`). Eine Aussage ohne Fundstelle ist eine Vermutung
und muss so gekennzeichnet werden. Widerspricht der Fund dieser Datei, korrigiere die Datei.

---

## 7. Die Fallen, die schon zugeschnappt sind

**Firmware**

- ⚠️ **LVGL-Schriften und der Theme-Wechsel.** `Assets::ApplyConfig()` tauscht beim Start die
  Schriften im Theme und ruft `SetTheme()`. LVGL-Styles halten nur einen rohen `lv_font_t*` —
  ein eigenes Label, das dabei nicht neu bindet, zeigt danach auf freigegebenen Speicher
  (`Guru Meditation`, Backtrace auf `lv_font_get_glyph_width`). **Regel: jedes neue eigene Label
  gehört in `ApplyFont()`.**
- ⚠️ **Die Gerätezustände taugen nicht fürs Gesicht.** `tts start` trifft **34 s vor dem ersten
  Ton** ein, weil der Server `send_stt_message()` vor `conn.chat` aufruft. Das Gesicht liest
  deshalb die Server-Nachrichten: `sentence_start` (kommt als `SetChatMessage("assistant", …)`)
  ist **der einzige verlässliche „jetzt kommt Ton"-Marker**.
- Zustandserkennung über `strcmp` gegen `Lang::Strings`, **nicht per Pointer-Vergleich** —
  `constexpr const char*` im Header, jede Übersetzungseinheit hat eine eigene Kopie.
- **Funkschlaf killt die Erreichbarkeit.** `SetPowerSaveLevel(LOW_POWER)` endet als
  `WIFI_PS_MAX_MODEM`; die Station verschläft dann **Broadcast-ARP** und **Multicast** — also
  Ping, DHCP-OFFER und die mDNS-Ankündigung. Die Board-Datei erzwingt deshalb **immer
  `PERFORMANCE`**. Kein Akku, Dauerstrom, kein Verlust.
- In LVGL 9.5.0 heißt es **`lv_anim_set_reverse_duration`**, nicht `playback_duration`.
- ⚠️ **Die Sprache steckt im Build, nicht in der Konfiguration.** `CONFIG_LANGUAGE_*`
  erzeugt `main/assets/lang_config.h` aus `main/assets/locales/<locale>/language.json` —
  eine Sprache pro Firmware. Es gibt deshalb zwei Auslieferungsstände, **Chatti-ENG**
  (`en-US`, Standard in Chatti Control) und **Chatti-DE** (`de-DE`), gebaut von
  `chatti\build-releases.ps1` nach `releases/<name>-v<version>/`. Jede Variante trägt eine
  `chatti-firmware.json`; daraus beschriftet die Steuerzentrale ihr Auswahlfeld.
  **Regel: jeder neue Text auf dem Schirm gehört in `language.json` beider Locales**,
  nie als Literal in den Code — `FaceDisplay::LabelFor()` war genau dieser Fehler und
  hat die englische Firmware auf Deutsch beschriftet.
- **Die angezeigte Versionsnummer ist `PROJECT_VER` in der Wurzel-`CMakeLists.txt`**
  (aktuell `1.0.1`), zusammengesetzt mit dem Locale-String `VERSION` — daher
  `Chatti-DE - V 1.0.1`. Wer sie ändert, ändert auch, was die OTA-Prüfung vergleicht.

**PC-Seite**

- ⚠️ **Auf `127.0.0.1` zu lauschen sperrt andere Rechner aus, nicht den Browser.** Jede besuchte
  Seite darf an `127.0.0.1:8099` senden; CORS verbirgt nur die *Antwort*. Nachgestellt: ein
  `POST /api/startup` mit fremdem `Origin` fuhr den ganzen Stapel hoch, und ohne `Host`-Prüfung
  hätte DNS-Rebinding die **Gesprächsmitschriften** auslesen können. Middleware in `main.py`
  prüft jetzt beides. **Regel: eine lokale Weboberfläche braucht Host- und Origin-Prüfung.**
- ⚠️ **Chatti Control läuft ohne `--reload`.** Nach Änderungen an `app/*.py` muss der
  uvicorn-Prozess neu gestartet werden; `web/` wirkt sofort. Neues Feld im Anfragekörper immer
  zusammen mit dem Neustart ausliefern — sonst ignoriert das alte Backend es stillschweigend.
- ⚠️ **Konfiguration nur mit `ruamel.yaml` im Round-Trip und `width = 4096` schreiben.** PyYAML
  vernichtet die Kommentare, und dort stecken die Begründungen. Nulltest: Laden und
  Zurückschreiben ergibt eine byte-identische Datei.
- ⚠️ **Ein Poll darf keine Formularfelder überschreiben, in denen gearbeitet wird.** Dirty-Prüfung,
  nicht nur Fokus-Prüfung.
- ⚠️ **`docker ps` ist die Wahrheit, nicht der Port.** Nach `compose stop` nimmt der Port-Proxy
  von Docker Desktop minutenlang weiter Verbindungen an.
- **`docker compose stop`, nie `down`** — `down` entfernt den Container und kann Bind-Mounts
  verlieren.
- **`.cmd` braucht CRLF** (sonst zerlegt `cmd.exe` die Datei zeichenweise), **`chatti/server/*.py`
  braucht LF** (Bind-Mounts in einen Linux-Container). Beides über `.gitattributes` abgesichert.

**Teuer bezahlt**

| Fehler | Lehre |
|---|---|
| GPIO 41 als Taster verdrahtet, ohne den Schaltplan zu lesen | Regel 1, wortwörtlich. `OnLongPress` feuerte 1,5 s nach jedem Boot und schaltete das Gerät ab |
| „Peak bei 0 dBFS" als „darf lauter" gelesen | 0 dBFS heißt **voll ausgesteuert** — ein Grund, die Verstärkung *nicht* zu erhöhen |
| Mute zuerst als „Mikrofon aus" gebaut | Gefragt und geredet wird weiter, nur vorgelesen nicht |
| Nur die erste Seite der Stimm-Registry angesehen | 155 Modelle, Community-Stimmen in `medium`/`high` |

---

## 8. Die Server-Patches

Master-Kopien in `chatti/server/`, als Bind-Mount eingehängt. Alle acht sind modifizierte
Kopien aus xiaozhi-esp32-server und tragen einen Herkunfts-Header. **Vorlagen für neue Patches
immer per `docker cp` aus dem laufenden Container holen**, nie aus einem Checkout — die Dateien
weichen ab.

| Patch | Zweck |
|---|---|
| `asr_openai.py` | `language: en` — sonst rät Whisper und liefert Arabisch |
| `tts_openai.py` | `sample_rate` anfordern; Speaches resampelt nur auf Anforderung |
| `helloHandle.py` | `audio_params` festnageln — der Server spiegelt sonst die Client-Rate |
| `opus_encoder_utils.py` | Bitrate aus `CHATTI_OPUS_BITRATE` statt hart 24000 |
| `tts_base.py` | Sprechdauer messen (pydub) **und** Satzzerlegung abschalten |
| `sendAudioHandle.py` | `duration_ms` in `sentence_start`, plus Gesprächsmitschnitt |
| `websocket_server.py` + `http_server.py` | `/chatti/status` — Upstream führt keine Liste offener Verbindungen |
| `chatti_log.py` *(neu)* | Gespräche wurden nirgends gespeichert |

**Konfigurationswerte, die nötig waren:** `max_tokens: 1500` (Reasoning-Modelle schreiben nach
`reasoning_content`, `content` bleibt sonst leer) · eigenes `prompt_template` mit 419 statt 6350
Zeichen · `end_prompt.enable: false`.

Ein **Neustart des Containers** ist der einzige Weg, eine Konfigurationsänderung wirksam zu
machen — der Server liest sie genau einmal.

---

## 9. Gemessene Werte

**Latenz** auf einer GTX 1650 (4 GB): ASR ~13 s (`small`) bis ~25 s (`medium`) · LLM + TTS bis
zum ersten Satz ~33 s · **gesprochenes Wort bis gesprochene Antwort ~45 s.** Langsam, aber
gewollt: Priorität liegt auf guten Antworten, nicht auf Geschwindigkeit. Die TTS ist daran nicht
schuld (4,5–6,4 s Synthese für 12 s Sprache, ~10 % des Rundlaufs).

**Warum es so langsam ist:** LLM, STT und TTS passen nicht gemeinsam in 4 GB VRAM — allein das
LLM ist größer. Die Modelle müssen bewusst auf GPU und CPU verteilt werden.

**Flash-Adressen:** `0x0` bootloader · `0x8000` partition-table · `0xd000` ota_data_initial ·
`0x20000` xiaozhi.bin · `0x800000` generated_assets.bin. App-Partition 0x3f0000, ~68 % belegt.

---

## 10. Denkbarer Umbau: Transport über USB statt WLAN

*(Recherchiert, nicht gebaut. Die README nennt es als Option — hier stehen die Details.)*

Auf dem ESP32 läuft **kein Server**, sondern ein WebSocket-*Client*, der eine ausgehende
Verbindung zum PC aufbaut. Zu ersetzen wäre also nur der **Transportweg**, nicht die
Rollenverteilung.

**Weg 1 — USB-NCM (empfohlen).** Das Gerät meldet sich per TinyUSB als **Netzwerkkarte** an,
der WebSocket bleibt **unverändert**. Aufwand: eine neue Board-Klasse `UsbNetBoard` statt
`WifiBoard`, sonst nichts. Unter Windows **NCM, nicht RNDIS** — RNDIS wird auf neueren
Windows-Versionen blockiert. Machbarkeit belegt: IDF 6.0.2 bringt
`examples/peripherals/usb/device/tusb_ncm` mit (`CONFIG_TINYUSB_NET_MODE_NCM=y`), dazu unter
`examples/network/sta2eth/` eine fertige Brücke WLAN ↔ USB-NCM.

**Weg 2 — USB-CDC seriell.** Eigene `Protocol`-Unterklasse plus Bridge-Skript auf dem PC.
Framing, Reconnect und Bridge wären Eigencode, und jeder Upstream-Merge würde teurer.

⚠️ **Der harte Stolperstein:** Der ESP32-S3 hat zwei USB-Peripherien — USB-Serial-JTAG und
USB-OTG — die sich **dieselben Pins GPIO 19/20 teilen**. Nur eine kann am Stecker hängen.

**Was der Verlust der Konsole konkret heißt** (wird oft überschätzt): **Geflasht wird weiter
über USB-C, gleiches Kabel.** Weg ist nur die *Automatik* — heute legt esptool den Chip über
DTR/RTS selbst in den Download-Modus. Ohne COM-Port heißt der Handgriff: Kabel ab, **BOOT
halten**, Kabel dran, BOOT loslassen. Der Download-Modus sitzt im **ROM** und läuft vor jeder
eigenen Zeile — die Firmware kann sich also **nicht selbst aussperren**. Wirklich verloren geht
das **Mitlesen** (Bootlog, Backtraces); dafür hat das Board reservierte UART-Pads auf GPIO 43/44.
**Vorschlag für den Umbau:** beim Start abfragen, ob BOOT gehalten wird, und TinyUSB dann gar
nicht erst starten — zehn Zeilen, und die Konsole bleibt auf Zuruf erreichbar.

**Bandbreite ist kein Argument:** Opus mit 24 kbit/s ≈ 3 kB/s, beide Wege schaffen ein Vielfaches.

**Was USB *nicht* löst:** die Latenz. Die steckt in ASR und LLM+TTS, der Netzwerkweg trägt
nichts dazu bei. Gelöst würden: WLAN-Einrichtung, Router-Abhängigkeit, Firewall-Regeln.
**Der Preis:** das Gerät hängt am PC, statt überall stehen zu können, wo Strom ist.

---

## 11. Offene Punkte

1. **Die Querformat-Drehung ist geflasht, der Bootlog sauber — die Bildlage selbst hat noch
   niemand angesehen.** Prüfen in dieser Reihenfolge: steht das Bild kopfüber (→ Gegenrichtung,
   Abschnitt 5), schwarzer Streifen an einer Kante (→ Offset), greifen die Knöpfe daneben
   (→ `TOUCH_*`).
2. **Die Gerätehälfte der mDNS-Suche ist ungeprüft.** Die Server-Seite ist belegt. Auf dem Gerät
   läuft die Suche nur an, wenn **kein** `ota_url` in NVS steht — sauberer Test: im
   Einrichtungsmodus die Adresse leeren, dann muss `ChattiDiscovery: found server at …` im
   Bootlog erscheinen.
3. **M6 offen:** Latenz, Übergänge, Verhalten beim Verbindungsabbruch.
4. **GitHub Actions deaktivieren** — der geerbte Workflow `Build Boards` startet bei jedem Push
   einen Cloud-Build, der im Fork fehlschlägt. Zwei Klicks in den Repo-Einstellungen; die
   Workflow-Datei bewusst **nicht** löschen, das gäbe Merge-Konflikte ohne Gegenwert.
5. **LM Studio hat keinen Pfad-Notausgang.** Docker Desktop und ESP-IDF werden gesucht bzw. über
   `CHATTI_DOCKER_DESKTOP` / `CHATTI_IDF_TOOLS` überschreibbar; für `lms.exe` fehlt das.
6. **`delete_audio: false`** steht weiterhin auf Diagnose — zurück auf `true`, wenn der
   Mikrofonpegel nicht mehr interessiert.

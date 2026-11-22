# EnigmaTVApp

### Klient Android TV dla dekoderów Enigma2 (Kivy + sprzętowy LibVLC 3.6.0, obsługa pilotem, EPG, PIP i wybór ścieżek Audio/Napisów)

[![Version](https://img.shields.io/badge/version-v1.2-00b8d4?style=flat-square)](https://github.com/Payti/EnigmaTVApp/releases)
[![Platform](https://img.shields.io/badge/platform-Android%20TV%20%7C%20Desktop-3ddc84?style=flat-square&logo=android&logoColor=white)](#instalacja)
[![LibVLC](https://img.shields.io/badge/player-LibVLC%203.6.0-ff8800?style=flat-square&logo=vlcmediaplayer&logoColor=white)](#architektura)
[![Last Commit](https://img.shields.io/github/last-commit/Payti/EnigmaTVApp?style=flat-square&label=Last%20Commit)](https://github.com/Payti/EnigmaTVApp/commits/main)
[![Releases](https://img.shields.io/github/v/release/Payti/EnigmaTVApp?include_prereleases&style=flat-square&label=Latest%20Release)](https://github.com/Payti/EnigmaTVApp/releases)

Aplikacja **Enigma2 TV** zaprojektowana od podstaw pod obsługę pilotem na urządzeniach **Android TV** (przetestowana m.in. na **Xiaomi Mi Box 4K**). Łączy się z dekoderem satelitarnym/kablowym przez **OpenWebif API**, pobiera listy bukietów, pikony i pełne EPG, a strumienie wideo (DVB/SAT oraz osadzone IPTV) odtwarza sprzętowo przez natywny silnik **LibVLC** na warstwie `TextureView`.

> [!NOTE]
> Aplikacja działa bezpośrednio w sieci lokalnej z OpenWebif (domyślnie port `80` dla API/pikon oraz port `8001` dla strumienia). Nie wymaga transkodowania po stronie dekodera.

---

## Najważniejsze funkcje (v1.2)

| Obszar | Możliwości |
|---|---|
| **Odtwarzacz LibVLC (HW)** | Natywny sterownik `TvVlcController.java` na `TextureView` (sprzętowe dekodowanie `H.264`, `H.265/HEVC`, `MPEG-2`), błyskawiczne przełączanie kanałów i brak konfliktów Z-order z interfejsem Kivy |
| **Dwa tryby podglądu PIP (PIG)** | **Auto (podąża za kursorem)** – podgląd po ~1 s postoju na kanale; **Enigma2 (ostatni oglądany)** – utrzymuje bieżący kanał w oknie PIP podczas przeglądania listy i bukietów, a po starcie uruchamia ostatnio oglądany lub pierwszy kanał |
| **Infobar Enigma2 (pod `OK`)** | Pikona kanału po lewej stronie, 2 wiersze EPG (*Aktualnie* z paskiem postępu i `+XX min` oraz *Następnie*), bieżący zegar z datą, znacznik `LIVE`/`BUFOR` oraz pełna telemetria strumienia na żywo |
| **Telemetria strumienia** | Rozdzielczość z normalizacją DVB (`1088 → 1080`, `544 → 540`), etykieta (`4K UHD` / `FHD` / `HD` / `SD`), proporcje (`16:9` / `4:3`), `FPS`, kodek wideo, aktywne audio (kodek, kanały `5.1`/`2.0`, próbkowanie `kHz`, język, liczba ścieżek), liczba napisów, bitrate (`Mbps`/`kbps`), zgubione klatki (`Drop`) |
| **Audio i Napisy na pełnym ekranie** | Natywna nakładka wyboru ścieżek dźwiękowych i napisów (DVB Sub / Teletext) dostępna jednym przyciskiem z pilota, z możliwością wyłączenia napisów lub dźwięku |
| **Pełny ekran EPG** | Widok **Pojedynczy EPG**, **Multi EPG** dla całego bukietu, **Info / Opis** z wyszukiwaniem podobnych audycji oraz zdalne **dodawanie timerów** nagrywania na dekoderze |
| **Interfejs Android TV** | Dedykowany baner Leanback (`640×360`), przezroczysty ekran startowy 16:9, ekranowa klawiatura numeryczna do wpisania IP/portów pilotem, kolorowe menu opcji pod `◄` oraz zamykanie aplikacji przez **2× Wstecz** |

---

## Sterowanie pilotem

### Lista kanałów
| Przycisk pilota | Akcja |
|---|---|
| `▲` / `▼` | Poruszanie się po liście kanałów (z pominięciem markerów) |
| `►` / `CH-` / `CH+` | Przewijanie listy o całą stronę |
| `OK` | Uruchomienie wybranego kanału na pełnym ekranie |
| `◄` | Otwarcie menu **Bukietów i Opcji** (tryb PIP, EPG, Odśwież, Ustawienia, Zamknij) |
| `Wstecz` (2× pod rząd) | Zamknięcie aplikacji i zwolnienie LibVLC |
| `EPG` / `GUIDE` | Otwarcie pełnego przewodnika EPG |
| `MENU` | Przejście do ustawień IP, portów, ścieżki pikon i trybu PIP |

### Pełny ekran (odtwarzacz)
| Przycisk pilota | Akcja |
|---|---|
| `OK` | Pokaż / ukryj **Infobar** (EPG + pikona + parametry strumienia) |
| `▲` / `▼` | Przełączanie kanałów (zapping) |
| `◄` / `MENU` / `AUDIO` | Otwarcie nakładki wyboru **Audio i Napisów** (`◄`/`►` zmienia zakładkę) |
| `SUB` / `CAPTIONS` | Otwarcie nakładki bezpośrednio na zakładce **Napisy** |
| `►` / `EPG` | Otwarcie pełnego ekranu EPG z powrotem do wideo |
| `Wstecz` | Zamknięcie nakładki lub powrót na listę kanałów |

---

## Instalacja

1. Pobierz najnowszy plik `enigma2tv-1.2-armeabi-v7a_arm64-v8a-debug.apk` ze strony [Releases](https://github.com/Payti/EnigmaTVApp/releases).
2. Zainstaluj paczkę na urządzeniu Android TV (np. przez pendrive / menedżer plików lub przez ADB: `adb install -r enigma2tv-1.2-armeabi-v7a_arm64-v8a-debug.apk`).
3. Przy pierwszym uruchomieniu wpisz pilotem **adres IP dekodera Enigma2** (domyślne porty: OpenWebif `80`, strumień `8001`, pikony `/usr/share/enigma2/picon`) i wybierz **ZAPISZ**.

> [!IMPORTANT]
> Paczka APK jest zoptymalizowana pod `armeabi-v7a` z kompresją bibliotek `.so` (~35–45 MB zamiast ponad 120 MB) i działa poprawnie również na 64-bitowych przystawkach Android TV (w tym Xiaomi Mi Box 4K).

---

## Architektura

| Plik | Rola |
|---|---|
| `main.py` | Główna aplikacja Kivy: komunikacja z OpenWebif API, cache pikon (`PiconManager`), ekrany listy kanałów, pełnego EPG i konfiguracji TV |
| `p4a_hook.py` | Hook dla `python-for-android`: konfiguruje manifest Leanback TV, wstrzykuje natywne przechwytywanie pilota w `PythonActivity.java`, generuje `TvVlcController.java` (LibVLC + Infobar + nakładka Audio/Napisy) i odchudza `build.gradle` |
| `buildozer.spec` | Konfiguracja budowania APK, zależności (`libvlc-all:3.6.0`), grafiki `icon.png` i `presplash.png` |
| `build_and_backup.sh` | Przenośny skrypt budujący APK (ze ścieżkami względnymi), weryfikujący składnię oraz tworzący wersjonowane backupy |
| `p4a_patches/` | Łatka `build.py` (dla `python-for-android`) oraz `gradle.properties` (przydział RAM dla Gradle) |

---

## Samodzielne budowanie (Ubuntu)

### 1. Zależności systemowe
```bash
sudo apt update
sudo apt install -y git zip unzip openjdk-17-jdk python3-pip python3-venv \
  autoconf automake libtool pkg-config zlib1g-dev libncurses-dev \
  cmake libffi-dev libssl-dev patch build-essential
```

### 2. Klonowanie, środowisko `venv` i łatki
```bash
git clone https://github.com/Payti/EnigmaTVApp.git
cd EnigmaTVApp

mkdir -p "$HOME/.gradle"
cp -a p4a_patches/gradle.properties "$HOME/.gradle/"

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip wheel setuptools
pip install buildozer cython pillow
```

### 3. Pierwsza kompilacja i kolejne buildy
```bash
buildozer android update
cp -a p4a_patches/build.py .buildozer/android/platform/python-for-android/pythonforandroid/build.py

# Zbudowanie bieżącej wersji i utworzenie lokalnego backupu:
./build_and_backup.sh

# Lub z automatycznym podbiciem wersji i zapisem APK w wybranym katalogu:
E_DRIVE="$HOME/Pulpit" ./build_and_backup.sh 1.3
```

---

<details>
<summary>Historia zmian (v1.0 – v1.2)</summary>

- **v1.2**
  - Natywna nakładka wyboru ścieżki **Audio i Napisów** z LibVLC podczas oglądania pełnoekranowego.
  - Przeniesienie pikony na **lewą stronę** Infobaru oraz pełna naprawa odczytu rozdzielczości na każdym przełączonym kanale (`getFreshTracks` + `OnNewVideoLayoutListener` + stały `TextureView`).
  - Normalizacja wysokości klatek DVB Full HD (`1088 → 1080`, `544 → 540`).
  - Kolorowe rozróżnienie opcji aplikacji od bukietów w menu `◄`, pozycja **Zamknij aplikację** oraz zamykanie przez **2× Wstecz**.
  - Przenośne ścieżki względne w `p4a_hook.py` i skrypcie `build_and_backup.sh`.
- **v1.1**
  - Wybór trybu PIP: **Auto (podąża za kursorem)** oraz **Enigma2 (ostatni oglądany)** z zapamiętywaniem ostatniego kanału i bukietu.
  - Rozbudowany natywny Infobar z 2 wierszami EPG, paskiem postępu, zegarem, pikoną i diagnostyką strumienia LibVLC.
- **v1.0**
  - Wersja bazowa: pełna obsługa pilota Android TV przez `PythonActivity.dispatchKeyEvent`, sprzętowy LibVLC na `TextureView`, pobieranie bukietów, pikon i EPG z OpenWebif, baner Leanback oraz przezroczysty ekran startowy 16:9.

</details>

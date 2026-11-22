#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_ACTIVATE="$APP_DIR/venv/bin/activate"
E_DRIVE="${E_DRIVE:-/media/sf_E_DRIVE}"
SPEC_FILE="$APP_DIR/buildozer.spec"

cd "$APP_DIR"

# Opcjonalna zmiana wersji: ./build_and_backup.sh 1.3
if [[ -n "${1:-}" ]]; then
    NEW_VER="$1"
    sed -i "s/^version = .*/version = ${NEW_VER}/" "$SPEC_FILE"
    echo "[INFO] Ustawiono wersję w buildozer.spec na: ${NEW_VER}"
fi

VER="$(awk -F'=' '/^version[[:space:]]*=/{gsub(/[[:space:]]/, "", $2); print $2; exit}' "$SPEC_FILE")"
if [[ -z "$VER" ]]; then
    echo "[BŁĄD] Nie udało się odczytać wersji z $SPEC_FILE"
    exit 1
fi

BNAME="backup_v${VER}"
BDIR="$APP_DIR/$BNAME"

echo "============================================================"
echo " Enigma2 TV — budowanie i backup wersji v${VER}"
echo " Katalog projektu: $APP_DIR"
echo "============================================================"

# 1. Sprawdź wolne miejsce na partycji projektu
FREE_MB="$(df -Pm "$APP_DIR" | awk 'NR==2 {print $4}')"
echo "[INFO] Wolne miejsce na dysku: ${FREE_MB} MB"
if (( FREE_MB < 1200 )); then
    echo "[UWAGA] Mało miejsca na dysku (poniżej 1200 MB) — Gradle może zgłosić brak miejsca!"
fi

# 2. Aktywuj venv i sprawdź składnię Pythona przed budowaniem
if [[ ! -f "$VENV_ACTIVATE" ]]; then
    echo "[BŁĄD] Brak środowiska venv w $APP_DIR/venv — utwórz je poleceniem: python3 -m venv venv"
    exit 1
fi
# shellcheck disable=SC1090
source "$VENV_ACTIVATE"
echo "[1/5] Sprawdzanie składni main.py i p4a_hook.py..."
python3 -m py_compile "$APP_DIR/main.py" "$APP_DIR/p4a_hook.py"

# 3. Wyklucz backup_v<VER> z buildozer.spec i .gitignore
echo "[2/5] Aktualizacja wykluczeń ($BNAME) w buildozer.spec i .gitignore..."
python3 - "$SPEC_FILE" "$BNAME" << 'PYEOF'
import re
import sys
from pathlib import Path

spec_path = Path(sys.argv[1])
bname = sys.argv[2]
text = spec_path.read_text(encoding="utf-8")

m = re.search(r"(?m)^source\.exclude_dirs\s*=\s*(.*)$", text)
if m:
    dirs = [d.strip() for d in m.group(1).split(",") if d.strip()]
    for req in ("venv", "bin", ".git", bname):
        if req not in dirs:
            dirs.append(req)
    text = text[:m.start()] + "source.exclude_dirs = " + ", ".join(dirs) + text[m.end():]
elif re.search(r"(?m)^#\s*source\.exclude_dirs\s*=", text):
    text = re.sub(
        r"(?m)^#\s*source\.exclude_dirs\s*=.*",
        f"source.exclude_dirs = tests, bin, venv, .git, {bname}",
        text,
        count=1,
    )
else:
    text += f"\nsource.exclude_dirs = tests, bin, venv, .git, {bname}\n"

spec_path.write_text(text, encoding="utf-8")
PYEOF

touch "$APP_DIR/.gitignore"
grep -qxF "backup_v*/" "$APP_DIR/.gitignore" || echo "backup_v*/" >> "$APP_DIR/.gitignore"

# 4. Zbuduj APK
echo "[3/5] Budowanie APK (buildozer -v android debug)..."
buildozer -v android debug

APK_PATH="$(ls -t "$APP_DIR/bin"/enigma2tv-"${VER}"-*-debug.apk 2>/dev/null | head -n 1 || true)"
if [[ -z "$APK_PATH" || ! -f "$APK_PATH" ]]; then
    echo "[BŁĄD] Nie znaleziono zbudowanego pliku bin/enigma2tv-${VER}-*-debug.apk"
    exit 1
fi
APK_NAME="$(basename "$APK_PATH")"
echo "[OK] Zbudowano: $APK_PATH"

# 5. Utwórz wersjonowany backup w katalogu projektu
echo "[4/5] Tworzenie backupu w $BDIR..."
rm -rf "$BDIR"
mkdir -p "$BDIR/p4a_patches"

for f in main.py buildozer.spec p4a_hook.py icon.png banner.png presplash.png build_and_backup.sh; do
    [[ -f "$APP_DIR/$f" ]] && cp -a "$APP_DIR/$f" "$BDIR/"
done

P4A_BUILD_PY="$APP_DIR/.buildozer/android/platform/python-for-android/pythonforandroid/build.py"
[[ -f "$P4A_BUILD_PY" ]] && cp -a "$P4A_BUILD_PY" "$BDIR/p4a_patches/build.py"
[[ -f "$HOME/.gradle/gradle.properties" ]] && cp -a "$HOME/.gradle/gradle.properties" "$BDIR/p4a_patches/"

cp -a "$APK_PATH" "$BDIR/$APK_NAME"

TMP_TAR="/tmp/EnigmaTVApp_src_v${VER}_$$.tar.gz"
tar --exclude='*.apk' --exclude='*.tar.gz' -czf "$TMP_TAR" -C "$APP_DIR" "$BNAME"
mv -f "$TMP_TAR" "$BDIR/EnigmaTVApp_src_v${VER}.tar.gz"

# 6. Opcjonalne kopiowanie na dysk zewnętrzny / folder współdzielony
echo "[5/5] Kopiowanie zewnętrzne ($E_DRIVE)..."
if [[ -d "$E_DRIVE" ]]; then
    if [[ -w "$E_DRIVE" ]]; then
        CP_CMD=(cp)
        MKDIR_CMD=(mkdir)
        LS_CMD=(ls)
    else
        CP_CMD=(sudo cp)
        MKDIR_CMD=(sudo mkdir)
        LS_CMD=(sudo ls)
    fi
    "${CP_CMD[@]}" -a "$APK_PATH" "$E_DRIVE/$APK_NAME"
    "${MKDIR_CMD[@]}" -p "$E_DRIVE/EnigmaTVApp_v${VER}"
    "${CP_CMD[@]}" -a "$BDIR/EnigmaTVApp_src_v${VER}.tar.gz" "$BDIR/$APK_NAME" "$E_DRIVE/EnigmaTVApp_v${VER}/"
else
    echo "[INFO] Katalog $E_DRIVE nie istnieje — pominięto kopiowanie zewnętrzne (backup jest w $BDIR)."
fi

echo ""
echo "============================================================"
echo " GOTOWE (v${VER})"
echo "============================================================"
ls -lh "$BDIR"
if [[ -d "$E_DRIVE" ]]; then
    "${LS_CMD[@]}" -lh "$E_DRIVE/$APK_NAME" "$E_DRIVE/EnigmaTVApp_v${VER}/"
fi

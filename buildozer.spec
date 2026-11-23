[app]
title = Enigma2 TV
package.name = enigma2tv
package.domain = pl.dom.enigma
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
source.exclude_dirs = venv, tests, bin, .buildozer, .git, backup_v1.0, backup_v1.1, backup_v1.2, backup_v., screenshots, .github, backup_v1.3
version = 1.3

requirements = python3,kivy,pyjnius
icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/presplash.png

orientation = landscape
fullscreen = 1

android.permissions = INTERNET,ACCESS_NETWORK_STATE,WAKE_LOCK
android.api = 33
android.minapi = 24
android.archs = armeabi-v7a, arm64-v8a
android.allow_backup = True
android.accept_sdk_license = True
android.enable_androidx = True
android.gradle_dependencies = org.videolan.android:libvlc-all:3.6.0

p4a.bootstrap = sdl2
p4a.hook = p4a_hook.py

[buildozer]
log_level = 2
warn_on_root = 1

android.presplash_color = #040506

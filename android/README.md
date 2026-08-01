# Android Build Guide

## Requirements
- Android Studio
- Chaquopy plugin
- Python 3.11+

## Setup
1. Open `android/` in Android Studio
2. Configure Chaquopy in `build.gradle`
3. Requirements are loaded from `requirements-android.txt` via Chaquopy's `pip.requirements()`.
4. Build and run

## Runtime
The Python gateway runs in a background thread started from Kotlin.
The WebSocket and HTTP surfaces share the same port (default `18790`),
so the WebView loads `http://127.0.0.1:18790/html-mobile/` and connects
to the WebSocket on the same origin.

## Troubleshooting

Problemi con `web_search`/`web_fetch` (WebView bridge)? Vedi la sezione
"Android WebView search/fetch" in [`.agent/gotchas.md`](../.agent/gotchas.md).

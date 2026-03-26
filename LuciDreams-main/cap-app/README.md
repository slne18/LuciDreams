# LuciDreams – Capacitor iOS app

Thin native wrapper so the Luci web app can use **flashlight**, **vibration**, and **native audio** on iOS (no Web Audio user-gesture limits).

## Prerequisites

- Node.js 18+
- Xcode (for iOS)
- CocoaPods (`sudo gem install cocoapods`)

## Setup

```bash
cd cap-app
npm install
npm run copy-web    # copies templates/luci.html + static assets into www/
npx cap sync        # syncs www and plugins to the native iOS project
npx cap open ios    # open in Xcode
```

In Xcode: select a simulator or a connected device and run (▶).

## Build pipeline

1. **Edit the web app** in `../templates/luci.html` and `../static/` as usual (Flask app).
2. **Refresh the Capacitor app**: from `cap-app` run `npm run copy-web` then `npx cap sync`.
3. Build/run in Xcode.

So the single source of truth is the Flask app; the cap-app only copies and wraps it.

## Plugins (what runs on device)

- **Flashlight**: `@capawesome/capacitor-torch` – condition 3 uses the device torch.
- **Vibration**: `@capacitor/haptics` – condition 2 uses native haptics (no `navigator.vibrate` on iOS).
- **Native audio**: `@capacitor-community/native-audio` – beeps play without Web Audio / user-gesture limits.
- **Volume buttons**: `@capacitor-community/volume-buttons` – calibration listens to hardware volume up/down and steps the slider.

When the app runs inside the Capacitor shell, it detects `Capacitor` and uses these plugins. In a regular browser, native-only features like flashlight, vibration, and cue audio will show an error instead of falling back.

## Native audio beep asset

The app expects a short beep file at `www/sounds/beep.wav`. A real `150 ms`, `800 Hz` WAV beep has been created there for native cue playback.

- **Path in app**: `www/sounds/beep.wav`.
- The copy script now also places the same file into `ios/App/App/sounds/beep.wav` for iOS native-audio loading.

## Optional: load from Flask in development

To test the same HTML in the app while serving from your dev server, in `capacitor.config.json` you can set:

```json
"server": {
  "url": "http://YOUR_MAC_IP:8080/luci",
  "cleartext": true
}
```

Then the WebView loads the Flask-served page; revert `server` to `{}` for production (bundled www).

## Summary

| Feature   | In browser (Flask)     | In Capacitor iOS app        |
|----------|------------------------|-----------------------------|
| Flashlight | getUserMedia torch or screen flash | Native torch (plugin)   |
| Vibration  | `navigator.vibrate` (unsupported on iOS) | Native haptics        |
| Audio      | Web Audio (user gesture required)  | Native audio (no gesture)   |

# LuciDreams

LuciDreams is a web app for lucid-dream and sleep studies. It monitors device motion during sleep, detects low-motion REM-like windows, and plays a configured cue (spoken keyword or beep) to support induction/disruption protocols.

## Project Structure

- `LuciDreams-main/` - main application codebase
- `LuciDreams-main/templates/luci.html` - primary participant-facing app
- `LuciDreams-main/static/` - static assets and Firebase config loader
- `LuciDreams-main/main.py` - Flask app entrypoint

## Core Flow

1. Participant enters a participant ID (PID).
2. App initializes the session from in-app defaults and PID context.
3. Participant grants motion permission and calibrates audio volume.
4. During sleep, app tracks motion and identifies REM-like periods.
5. App plays one configured cue at the end of a qualified low-motion window.
6. Session summary data is uploaded to Firestore when the session ends.

## Tech Stack

- Frontend: single-page web app (`luci.html`)
- Backend: Flask (serves pages and static files)
- Data: Firebase Firestore (session outputs)
- Deployment target: Google App Engine

## Local Development

```bash
cd LuciDreams-main
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Then open the app route served by Flask (commonly `/luci`).

## Firebase Setup

1. Create a Firebase project and Web app in Firebase Console.
2. Add the Firebase web config to `LuciDreams-main/static/firebase-config.js` as `window.FIREBASE_CONFIG`.
3. Create/update Firestore collections used by this app (session uploads under `sleep_studies/{pid}/sessions`).
4. Set Firestore rules appropriate for your study security model.

## Deployment

```bash
cd LuciDreams-main
gcloud config set project YOUR_PROJECT_ID
gcloud app deploy --quiet
```

## Security

- Never commit service-account JSON keys or private credentials.
- Keep local secrets out of git using `.gitignore` and secret management.
- If a key was exposed, revoke/rotate it immediately in Google Cloud.

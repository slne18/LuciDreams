# LuciDreams Web

A web app for sleep studies that plays a sound (keyword or beep) during suspected REM phases, based on device motion. Participants use their phone in the browser; config and session data are stored in Firebase Firestore.

---

## Goal of the project

- **Participant flow:** Enter PID → load settings → grant motion permission → calibrate sound volume → start sleep → app tracks motion and plays the stimulus during low-motion (REM-like) windows → participant taps “I’m awake” and data is uploaded.
- **Research use:** Per-participant config (keyword vs beep, volume, control vs experimental), and session data (motion aggregates, REM episodes, sound log with optional `playback_completed`) for analysis.

---

## REM logic

- **Motion:** Every second the app reads the device accelerometer, sums |Δx| + |Δy| + |Δz| and adds a tilt term to get a single “motion” value per second.
- **Baseline:** The first **4 hours** (14 400 seconds) of recording are used only to compute a **baseline motion average** (threshold). No sound is played in this gate.
- **REM-like window:** After the 4‑hour gate, if motion stays **below** that baseline for **30 consecutive seconds**, the app treats this as one REM-like episode.
- **Stimulation:** At the end of those 30 seconds the app plays **one** sound (keyword via speech synthesis or beep via **native audio** in the app, at the calibrated volume). It does **not** repeat every N seconds; it’s one play per episode. If motion goes back above the threshold, the episode is closed and any playing sound is stopped.
---

## Firebase setup

### 1. Firebase project and Web app

- Create or use a project at [Firebase Console](https://console.firebase.google.com/).
- Add a **Web app** and copy the `firebaseConfig` object (apiKey, authDomain, projectId, etc.).
- Paste it into **`static/firebase-config.js`** as `window.FIREBASE_CONFIG` (replace the placeholder).

### 2. Firestore structure

**Participant config (one document per participant)**

- **Collection:** `participant_configs`
- **Document ID:** participant ID (e.g. `P001`, `Testing`)

**Document fields:**

| Field              | Type    | Description |
|--------------------|---------|-------------|
| `keyword`          | string  | Word spoken when `sound_type` is `keyword` (e.g. `"Bip"`). |
| `sound_type`       | string  | `"keyword"` (speech) or `"beep"` (native tone in LuciDreams app). |
| `beep_frequency`   | number  | Beep frequency in Hz (e.g. `800`). |
| `beep_duration_ms` | number  | Beep length in ms (e.g. `150`). |
| `interval`         | number  | Reserved (e.g. `10`). |
| `startVolume`      | number  | Initial calibration volume (e.g. `0.01`). |

Example document for PID `Testing` in `participant_configs`:

```json
{
  "keyword": "Bip",
  "sound_type": "beep",
  "beep_frequency": 800,
  "beep_duration_ms": 150,
  "interval": 10,
  "startVolume": 0.01
}
```

**Session data (one document per “I’m awake” upload)**

- **Collection:** `sleep_studies/{participant_id}/sessions`
- Documents are **auto-generated** (`.add(doc)`). No manual creation needed.

Firestore stores **summaries** (`general`, `induction`, `disruption`) **and** full **`rem_periods`** (each episode with nested **`trains`** → **`disruptive`** / **`induction`**). There is **no** separate top-level **`trains`** array in Firestore (flat list is only built for the Qualtrics URL to match embedded fields). `rem_episode_motion_averages` and `intermediate_motion_avg` stay **URL-only** (from `computeMotionAverages()`), not duplicated in Firestore.

Top-level fields: **`participant_id`**, **`device_time`**, **`general`**, **`induction`**, **`disruption`**, **`rem_periods`**, optional **`server_timestamp`**.

- **`general`**: `session_type`, `condition`, `wake_time`, `device_time_start` (night start — same moment as Qualtrics `sleep_start_time`), `night_number`, `epochs_count`, `motion_per_second_mean_first_4h`, `rem_minutes`, `rem_motion_avg`, `rem_dynamic_threshold_before_episode_avg` / `_last`, `rem_episode_count`, `arousal_threshold`, `native_api_status`, `rem_detection_delay_seconds`, `rem_stillness_required_seconds`, `rem_stillness_noise_floor_fraction`, **`night_max_trains`** (**5**, cap is **per entire night**, not per REM episode), `total_trains_delivered`, `app_version`.

- **`induction`**: `cues` (flat), `arousal_reached_count`, `induction_highest_volume`.

- **`disruption`**: `first_train_start_time`, `last_train_start_time`, `train_cap_reached_count`, `total_trains_delivered`.

**Qualtrics URL** additionally passes JSON blobs: `rem_periods`, `trains`, `induction_cues`, plus `rem_episode_motion_averages`, `intermediate_motion_avg`, `motion_per_second_mean_first_4h`, `epochs_count`, `night_max_trains`, etc.

**`rem_periods` JSON shape** (episodes): `start_epoch_sec`, `duration_sec`, `device_time_start`, `dynamic_threshold_before_rem`, `motion_avg`, `trains[]` with **`disruptive`** (`start_epoch_sec`, `condition`, `device_time_start`, `took_place`, `params`) and **`induction`** (`cues[]`, `arousal_threshold`).

### 3. Firestore rules

Ensure your rules allow:

- **Read** for `participant_configs/{pid}` (e.g. by PID or authenticated user, depending on your design).
- **Write** for `sleep_studies/{pid}/sessions` (e.g. only for that PID or authenticated user).

Example (adjust for your auth model):

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /participant_configs/{pid} {
      allow read: if true;
      allow write: if false;
    }
    match /sleep_studies/{pid}/sessions/{sessionId} {
      allow read, write: if true;
    }
  }
}
```

---

## Open to all (other labs)

The app can be used by other labs in two ways. The main difference is who hosts the backend (Firebase / Firestore).

### Method 1: Fork & deploy 

Share the project (e.g. via GitHub). Each lab runs and hosts the app themselves, wired to **their** Firebase.

- **How it works:** 1. Lab clones the repo. 2. They create their own Firebase project and Firestore. 3. They put their Firebase config in `static/firebase-config.js` as `window.FIREBASE_CONFIG`. 4. They deploy to their own Google App Engine (or any host that serves the HTML and static files).
- **Data flow:** The app is tied to **their** Firestore. All config and session data stay in their project.
- **Pros:** Full data ownership and privacy; no dependency on your infrastructure. No per-user setup.

| Step | Action | Who |
|------|--------|-----|
| 1 | Clone repo, create Firebase project, add Firestore + rules | Lab |
| 2 | Set `FIREBASE_CONFIG` in `static/firebase-config.js` | Lab |
| 3 | Deploy (e.g. `gcloud app deploy`) | Lab |
| 4 | Share the app URL with participants | Lab |

---

### Method 2: Dynamic config (one app, many labs)

One deployed app; each lab connects it to **their** Firebase by giving participants a special link or a config payload.

- **How it works:** 1. Deploy the app once. 2. A researcher creates a “lab link” that contains their Firebase config (or a code that resolves to it). 3. The participant opens that link on the device they use for the study. 4. The app saves the lab’s config in the browser (e.g. `localStorage`) and from then on uses **that** Firestore for config and session upload.
- **Data flow:** The same app URL can point different users (or different devices) at different Firestore projects, depending on which link they opened last.
- **Pros:** One codebase and one deployment; no build step for labs. Labs only need to create a Firebase project and use the link generator.

| Step | Action | Who |
|------|--------|-----|
| 1 | Create Firebase project, get Web app config | Researcher (Lab B) |
| 2 | Generate “lab link” (or QR) with config (e.g. via `/researcher-setup.html`) | Researcher (Lab B) |
| 3 | Send the link (or QR) to the participant | Researcher (Lab B) |
| 4 | Participant opens the link in the browser they will use for the study | Participant |
| 5 | App saves Lab B’s config and uses Lab B’s Firestore for the study | App |

**In the app (Dynamic config):**

- If the URL has a `?config=...` parameter (base64-encoded Firebase config), the app reads it, stores it in `localStorage`, and uses it to connect to that lab’s Firestore.
- If there is no stored config and no `FIREBASE_CONFIG` in the repo, the app shows a **Lab setup** step: “Open the link your researcher sent you” and an optional “Paste config (advanced)” to paste the Firebase config JSON.

**Generating the lab link (Method 2):**

- Open **`/researcher-setup`** on the same host (e.g. `https://YOUR_APP.appspot.com/researcher-setup`).
- Paste your Firebase Web app config (JSON from Firebase Console).
- Click **Generate link**. Share the URL (or the shown QR code) with participants. When they open that link in the browser they will use for the study, the app saves your Firebase config and uses your Firestore from then on.

---

## Architecture 

- **Frontend:** Single-page app in `templates/luci.html` (state, UI, sensor processing, audio, REM logic). Runs in the participant’s browser.
- **Backend:** Flask (e.g. on Google App Engine) **serves** the Luci HTML/JS and the researcher-setup page. Config and session data go **directly** from the browser to **Firestore** via the Firebase SDK; the server does not store participant or session data.

---

## Deploy (after code changes)

1. **Directory:**  
   `cd SleepPlusPlus-main`

2. **Google Cloud project:**  
   `gcloud config set project YOUR_PROJECT_ID`

3. **Deploy:**  
   `gcloud app deploy --quiet`  
 
4. **Open app:**  
   `gcloud app browse`  
   Or use: `https://YOUR_PROJECT_ID.uc.r.appspot.com/luci`

First time in a project you may need: `gcloud app create` before `gcloud app deploy`.

---

## Requirements

See `requirements.txt`. Main dependencies: Flask, gunicorn (for serving the app). Firebase is loaded in the browser via the SDK scripts in `luci.html`; no Python Firebase dependency.

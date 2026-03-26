# Deploy LuciDreams so you can try on your phone

## 1. Install Google Cloud SDK

- **Mac (Homebrew):** `brew install --cask google-cloud-sdk`
- **Or download:** https://cloud.google.com/sdk/docs/install

## 2. Log in and create/select a project

```bash
cd SleepPlusPlus-main

# Log in (opens browser)
gcloud auth login

# Create a new project (or use an existing one)
gcloud projects create YOUR_PROJECT_ID --name="LuciDreams"

# Or list and select existing
gcloud projects list
gcloud config set project YOUR_PROJECT_ID

# Enable App Engine for the project (first time only)
gcloud app create --region=us-central
```
If it asks for a region, choose e.g. `us-central` or one close to you.

## 3. Deploy

```bash
gcloud app deploy
```

- Accept the prompts (e.g. "Do you want to continue?" → Y).
- Wait until you see "Deployed service [default] to [https://...]".

## 4. Open on your phone

- The app URL will be: **`https://YOUR_PROJECT_ID.uc.r.appspot.com/luci`**  
  (or `https://YOUR_PROJECT_ID.appspot.com/luci` depending on region.)
- Open that URL in your phone’s browser (Chrome/Safari) and use the app.

## 5. Allow Firebase from the deployed domain

So Firestore works from the deployed site:

1. Go to https://console.firebase.google.com/ → your project.
2. **Project settings** (gear) → **General**.
3. Under **"Your apps"**, open your **Web** app.
4. Scroll to **"Authorized domains"** (or in **Authentication** → **Settings** → **Authorized domains**).
5. Click **Add domain** and add:  
   `YOUR_PROJECT_ID.uc.r.appspot.com`  
   (or the exact domain from the deploy output.)

Save. After that, the app on your phone can use Firestore when you use the deployed URL.

---

**Quick test without deploy:** run locally and use your phone on the same Wi‑Fi:

```bash
cd SleepPlusPlus-main
source venv/bin/activate
python main.py
```

Then on your phone’s browser open: `http://YOUR_COMPUTER_IP:8080/luci` (find your computer’s IP in System Settings → Network, or run `ipconfig getifaddr en0` on Mac).

# Fresh App Engine deploy from scratch

Do this in order in your terminal.

---

## 1. Create a new Google Cloud project (optional but cleanest)

```bash
# Pick a unique project ID (lowercase, numbers, hyphens only)
export NEW_PROJECT_ID=lucidreams-web

gcloud projects create $NEW_PROJECT_ID --name="LuciDreams Web"
gcloud config set project $NEW_PROJECT_ID
```

If you prefer to keep using `lucidreams-489421`, skip the create line and run only:

```bash
gcloud config set project lucidreams-489421
```

---

## 2. Link billing (required for App Engine)

- Go to: https://console.cloud.google.com/billing
- Select your project (top-left), then link a billing account to the project.

---

## 3. Enable required APIs

```bash
gcloud services enable appengine.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable datastore.googleapis.com
```

Wait until each says "Operation finished successfully."

---

## 4. Create the App Engine app (once per project)

Pick a region (e.g. `us-central` or `europe-west1`):

```bash
gcloud app create --region=us-central
```

Answer **Y** when asked. This creates the app; you only do it once per project.

---

## 5. Grant the default service account access to the staging bucket

Use your actual project ID (replace if you used a different one):

```bash
# If you created a new project:
gcloud projects add-iam-policy-binding $NEW_PROJECT_ID \
  --member="serviceAccount:${NEW_PROJECT_ID}@appspot.gserviceaccount.com" \
  --role="roles/storage.admin"

# If you kept lucidreams-489421:
gcloud projects add-iam-policy-binding lucidreams-489421 \
  --member="serviceAccount:lucidreams-489421@appspot.gserviceaccount.com" \
  --role="roles/storage.admin"
```

---

## 6. Deploy from the app folder

```bash
cd /Users/solenenoize/Desktop/sleepdis-memories/SleepPlusPlus-main
gcloud app deploy --quiet
```

`--quiet` skips the "Do you want to continue?" prompt.

---

## 7. Get your URL

After a successful deploy you’ll see something like:

```
Deployed service [default] to [https://YOUR_PROJECT_ID.uc.r.appspot.com]
```

On your phone, open: **https://YOUR_PROJECT_ID.uc.r.appspot.com/luci**

---

## 8. Firebase: authorize the new domain (if you use Firebase)

If your app uses Firebase/Firestore:

1. Firebase Console → your **Firebase** project (may be same or different from GCP project).
2. Project settings (gear) → **Authorized domains**.
3. Add: `YOUR_PROJECT_ID.uc.r.appspot.com` (same as the deploy URL).

---

## If the build still fails

1. Open: https://console.cloud.google.com/cloud-build/builds?project=YOUR_PROJECT_ID  
2. Open the **failed** build and read the log (expand the failed step).
3. Copy the **exact error message** (last 5–10 lines of the failed step) and use it to debug (e.g. missing dependency, wrong Python version).

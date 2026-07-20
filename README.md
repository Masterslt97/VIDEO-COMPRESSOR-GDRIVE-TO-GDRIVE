# GDrive Video Compressor

Automated video compression workflow using GitHub Actions. Downloads videos from Google Drive, compresses them with FFmpeg (H.264 CRF 18), and uploads back to a specified GDrive folder.

---

## 📋 Secrets Setup

Go to **GitHub → Repo → Settings → Secrets and variables → Actions** and add:

### 1. `GDRIVE_LINKS`

```json
{
  "My Videos": [
    "https://drive.google.com/file/d/0B0DEMO0000000000000000000000000/view"
  ]
}
```

| Key | Description |
|-----|-------------|
| `"My Videos"` | Folder name on GDrive where compressed videos will be uploaded |
| `"https://..."` | Array of GDrive shareable links to compress |

> 💡 Use **`gdrive-json-generator.html`** to easily generate this JSON.

### 2. `RCLONE_CONF`

Your rclone config for Google Drive:

```ini
[gdrive]
type = drive
scope = drive
token = {"access_token":"...","token_type":"Bearer","refresh_token":"..."}
team_drive =
```

### 3. `GH_PAT`

GitHub Personal Access Token with `actions: read/write` scope for artifact updates.

---

## ▶️ How to Run

```
GitHub → Actions → GDrive Video Compressor → Run workflow
```

Or push to `main` branch.

---

## 🔄 Workflow Flow

```
GDRIVE_LINKS JSON
    ↓
For each folder → link[]
    ↓
1. rclone backend copyid → Download (shows speed, MB, ETA)
2. ffmpeg -c:v libx264 -crf 18 → Compress (visually lossless)
3. rclone copy → Upload to GDrive folder (shows speed, MB, ETA)
4. Append to compression_records.json
5. Upload artifact (overwrite)
    ↓
Artifact: compression_records.json
```

---

## 📦 Files

| File | Purpose |
|------|---------|
| `.github/workflows/compress.yml` | GitHub Actions workflow |
| `scripts/process_videos.py` | Download → Compress → Upload logic |
| `gdrive-json-generator.html` | Tool to generate `GDRIVE_LINKS` JSON |

import json, os, re, subprocess, sys, io, zipfile, time, requests
from datetime import datetime, timezone
from pathlib import Path

GITHUB_REPO = os.environ.get('GITHUB_REPOSITORY', '')
GITHUB_RUN_ID = os.environ.get('GITHUB_RUN_ID', '')
GH_PAT = os.environ.get('GH_PAT', '')

def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)

def extract_file_id(url):
    patterns = [
        r'/file/d/([a-zA-Z0-9_-]+)',
        r'id=([a-zA-Z0-9_-]+)',
        r'/open\?id=([a-zA-Z0-9_-]+)',
        r'/d/([a-zA-Z0-9_-]+)',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None

def delete_existing_artifact(name, headers):
    r = requests.get(
        f'https://api.github.com/repos/{GITHUB_REPO}/actions/runs/{GITHUB_RUN_ID}/artifacts',
        headers=headers
    )
    if r.status_code != 200:
        return
    for art in r.json().get('artifacts', []):
        if art['name'] == name:
            d = requests.delete(
                f'https://api.github.com/repos/{GITHUB_REPO}/actions/artifacts/{art["id"]}',
                headers=headers
            )
            if d.status_code == 204:
                log(f'🗑️ Deleted old artifact "{name}" (id: {art["id"]})')

def upload_artifact_via_api(filepath, name):
    if not GH_PAT:
        log('⚠️ GH_PAT not set, skipping artifact upload')
        return

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(filepath, os.path.basename(filepath))
    buf.seek(0)

    headers = {
        'Authorization': f'token {GH_PAT}',
        'Accept': 'application/vnd.github.v3+json',
    }

    delete_existing_artifact(name, headers)

    r = requests.post(
        f'https://api.github.com/repos/{GITHUB_REPO}/actions/runs/{GITHUB_RUN_ID}/artifacts',
        headers=headers,
        json={'name': name, 'archive_format': 'zip'}
    )

    r.raise_for_status()
    data = r.json()
    upload_url = data['upload_url'].replace('{?name,archive_format}', '')

    r2 = requests.put(
        upload_url,
        headers={'Authorization': f'token {GH_PAT}', 'Content-Type': 'application/zip'},
        data=buf.getvalue()
    )
    r2.raise_for_status()
    log(f'✅ Artifact "{name}" overwritten')

def load_previous_records():
    records_path = './prev_records/compression_records.json'
    if os.path.exists(records_path):
        with open(records_path) as f:
            records = json.load(f)
        log(f'📖 Loaded {len(records)} previous record(s)')
        return records
    log('📖 No previous records, starting fresh')
    return []

def download_video(file_id, url=None):
    log('⬇️ Trying rclone backend copyid...')
    result = subprocess.run(
        ['rclone', 'backend', 'copyid', 'gdrive:', file_id, './downloads/', '-P'],
        capture_output=True, text=True
    )
    files = os.listdir('downloads')
    files = [f for f in files if os.path.isfile(os.path.join('downloads', f))]
    if result.returncode == 0 and files:
        log(f'✅ rclone download success: {files[0]}')
        return os.path.join('downloads', files[0])

    log('⚠️ rclone failed, falling back to gdown...')
    for path in Path('downloads').iterdir():
        path.unlink()
    if not url:
        raise Exception('No URL provided for gdown fallback')
    subprocess.run(['gdown', url, '-O', './downloads/', '--fuzzy', '--remaining-ok'], check=True)
    files = os.listdir('downloads')
    files = [f for f in files if os.path.isfile(os.path.join('downloads', f))]
    if not files:
        raise Exception('No file downloaded via gdown')
    log(f'✅ gdown download success: {files[0]}')
    return os.path.join('downloads', files[0])

def compress_video(inp, out):
    log('🎬 Compressing with FFmpeg (H.264 CRF 18)...')
    subprocess.run(
        ['ffmpeg', '-i', inp,
         '-c:v', 'libx264', '-crf', '18', '-preset', 'medium',
         '-c:a', 'copy', '-y', out],
        check=True
    )

def upload_to_gdrive(local_path, remote_folder):
    log('⬆️ Uploading to GDrive (rclone)...')
    subprocess.run(
        ['rclone', 'copy', local_path, f'gdrive:{remote_folder}/', '-P'],
        check=True
    )

def main():
    os.makedirs('downloads', exist_ok=True)
    os.makedirs('compressed', exist_ok=True)

    records = load_previous_records()
    processed_links = {r['gdrive_link'] for r in records}

    links_json = os.environ.get('GDRIVE_LINKS', '{}')
    try:
        folders = json.loads(links_json)
    except json.JSONDecodeError as e:
        log(f'❌ Invalid GDRIVE_LINKS JSON: {e}')
        sys.exit(1)

    total_videos = sum(len(urls) for urls in folders.values())
    completed = sum(1 for r in records if r.get('status') == 'uploaded')
    log(f'📊 {completed}/{total_videos} videos already processed')

    for folder_name, urls in folders.items():
        for url in urls:
            if url in processed_links:
                log(f'⏩ SKIP (already processed): {url}')
                continue

            log(f'\n{"─" * 60}')
            log(f'📌 Processing: {url}')
            log(f'📂 Folder:     {folder_name}')
            log(f'{"─" * 60}')

            file_id = extract_file_id(url)
            if not file_id:
                log(f'❌ Could not extract file ID from: {url}')
                records.append({
                    'video_name': url,
                    'gdrive_link': url,
                    'folder': folder_name,
                    'status': 'failed',
                    'error': 'Invalid GDrive URL - could not extract file ID',
                    'timestamp': datetime.now(timezone.utc).isoformat()
                })
                continue

            try:
                # Download
                input_path = download_video(file_id, url)
                orig_size = os.path.getsize(input_path)
                log(f'📁 Original: {os.path.basename(input_path)} ({orig_size / 1048576:.2f} MB)')

                # Compress
                base_name = os.path.splitext(os.path.basename(input_path))[0]
                output_path = os.path.join('compressed', f'{base_name}_compressed.mp4')

                compress_video(input_path, output_path)
                comp_size = os.path.getsize(output_path)
                saved = round((1 - comp_size / orig_size) * 100, 1)
                log(f'✅ Compressed: {comp_size / 1048576:.2f} MB (saved {saved}%)')

                # Upload
                upload_to_gdrive(output_path, folder_name)
                log(f'✅ Uploaded to gdrive:{folder_name}/{base_name}_compressed.mp4')

                # Record
                record = {
                    'video_name': os.path.basename(input_path),
                    'original_size_mb': round(orig_size / 1048576, 2),
                    'compressed_size_mb': round(comp_size / 1048576, 2),
                    'saved_percent': f'{saved}%',
                    'folder': folder_name,
                    'gdrive_link': url,
                    'status': 'uploaded',
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
                records.append(record)

            except subprocess.CalledProcessError as e:
                log(f'❌ Processing failed: {e}')
                records.append({
                    'video_name': os.path.basename(url),
                    'gdrive_link': url,
                    'folder': folder_name,
                    'status': 'failed',
                    'error': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                })
                continue

            except Exception as e:
                log(f'❌ Unexpected error: {e}')
                records.append({
                    'gdrive_link': url,
                    'folder': folder_name,
                    'status': 'failed',
                    'error': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                })
                continue

            # Save & upload artifact after each successful video
            with open('records.json', 'w') as f:
                json.dump(records, f, indent=2)
            log(f'📝 Records saved ({len(records)} total)')

            try:
                upload_artifact_via_api('records.json', 'compression_records')
            except Exception as e:
                log(f'⚠️ Artifact upload failed (will retry at end): {e}')

            # Cleanup for next video
            for path in Path('downloads').iterdir():
                path.unlink()
            for path in Path('compressed').iterdir():
                path.unlink()

            processed_links.add(url)

    # Final save
    if records:
        with open('records.json', 'w') as f:
            json.dump(records, f, indent=2)

        uploaded = sum(1 for r in records if r.get('status') == 'uploaded')
        failed = sum(1 for r in records if r.get('status') == 'failed')
        log(f'\n{"=" * 60}')
        log(f'🏁 WORKFLOW COMPLETE')
        log(f'✅ Uploaded: {uploaded}  ❌ Failed: {failed}  📝 Total: {len(records)}')
        log(f'{"=" * 60}')

if __name__ == '__main__':
    main()

# Signoff Pilot Runbook

Everything a pilot operator needs to install, run, and feed back results.

## 1. Install

```bash
git clone https://github.com/santapong/signoff && cd signoff
docker compose -f infra/docker-compose.yml up --build -d
# service on http://<host>:8600 — check http://<host>:8600/health
```

Optional pilot access control:

```bash
SIGNOFF_TOKEN=<shared-secret> docker compose ... # see compose environment
```

Without `SIGNOFF_TOKEN` the API is open (dev mode). For pilots always set it.

## 2. First session

1. Open `http://<host>:8600/ui` and sign in with the token.
2. Go to **Upload**, pick an MP4 recorded from the workstation camera, add a note.
3. You land on the session page — press analyze (or `POST /sessions/<id>/analyze`).
4. Review the metrics table and report; failure hypotheses marked ⚠ need human confirmation.

## 3. Review loop (human corrections)

- On the session page use the review form, or batch via:
  `POST /sessions/<id>/review` with `{"annotator": "you", "cycles": [...]}`
- Each annotator's file is kept separately: `annotations/<sid>_<annotator>.jsonl`.
- Check inter-annotator agreement (E8): `GET /ui/sessions/<id>/agreement`.

## 4. Live monitoring

- Nav → **Live monitor** (`/live/`). Start with `POST /live/start?synthetic=false&device=0`
  when the webcam is attached. Latency budget: 5 s per event (shown live).

## 5. Backup & restore

```bash
python tools/pilot_backup.py backup --data-dir datasets --out backups
# ship backups/datasets_backup_<ts>.tar.gz off-site, then verify restore:
python tools/pilot_backup.py restore backups/datasets_backup_<ts>.tar.gz --data-dir datasets
```

Restore verifies every file sha256 against the manifest inside the archive.

## 6. Feedback

Every session page: rating + comment lands in `datasets/feedback/<sid>.jsonl`.
Weekly, export feedback + agreement reports to the engineering channel.

## 7. Escalation

| Symptom | First action | Then |
|---------|--------------|------|
| `/health` not responding | `docker logs <container>` | restart compose stack |
| Camera lost in live mode | check USB cable/device index | engine auto-recovers; else stop/start |
| Upload rejected 415 | re-encode to H.264 mp4 | file an issue with the file |
| Analysis F1 much lower than expected | check lighting/scene drift | attach session id + vision_report.json |

# Senior-Centric Medication, Caregiver Support & Two-Stage SOS System
Flask + SQLAlchemy (SQLite default, PostgreSQL via `DATABASE_URL`) + JWT/RBAC + browser simulator console.

## Run
```
pip install -r requirements.txt
python app.py            # http://localhost:5000  (simulator console)
python smoke_test.py     # full end-to-end test
# production: DATABASE_URL=postgresql://... SECRET_KEY=... GATEWAY_SECRET=... DEV_MODE=0 gunicorn -w2 app:app  (behind Nginx + HTTPS)
```
## Demo in the console
1. Seed demo → 2. Medication time → press 1/2/3 on phone, or 3 failures (busy/off/no answer) → Unreachable → Stage-1 SOS
3. Caretaker panel: B (lowest cost) is asked first; Reject → A; Accept → one-time door code → Dial & unlock
4. Arrived → result A/B/C; C → Stage-2: ambulance request with saved address.

## Where each requirement lives (app.py)
Registration/address: `/api/seniors` · Meds & IVR: `place_call`, `/ivr/*`, `tick()` · Score/trend: `sc`, `/score` · Follow-up rule: `/followup-recommendation` (config, no diagnosis)
Side effects: `/side-effects` · Unreachable+retry: `call_done` · Cost/availability engine: `rank()` (`SOS_POLICY=LOWEST_COST|COMBINED`, radius/ETA in `CFG`; Haversine) · Rapido-style dispatch: `dispatch()`, timeouts in `tick()`
Secure door: `issue_door`, `/ivr/door` (hashed one-time 6-digit code, 10-min TTL, 3 tries, caller-phone bound, Stage-1 + acceptance required, audit) · Stage-2: `stage2()` · Notifications: `notify()` · Audit: `Audit` table

## Integration hooks (simulated until configured)
- Telephony (Twilio/Exotel): implement body of `place_call`; point status callback to `/ivr/status`, DTMF to `/ivr/dtmf-form/<id>`, door line to `/ivr/door` with `secret`. Recorded WAV/MP3: swap `<Say>` for `<Play>` in `script()`.
- FCM: replace `notify()` body with `firebase_admin.messaging.send`.
- `AMBULANCE_URL`, `DOOR_CONTROLLER_URL`: JSON webhooks; if they fail, the system logs and notifies the caregiver (fallback: retry→log→notify→escalate).
- Flutter apps: not included; every screen maps to the REST endpoints above.

## Mobile app (PWA)
`/` is now a mobile-first app (caregiver + caretaker roles). Run `python app.py`, open `http://<PC-IP>:5000` on the phone (same Wi-Fi), then Chrome menu → Add to Home screen. Old simulator: `/console.html`.
GPS needs HTTPS on a phone: use `cloudflared tunnel --url http://localhost:5000` (or ngrok) and open the https link, or type lat/lon manually.

"""Senior-Centric Medication Management, Caregiver Support & Two-Stage SOS - Flask backend."""
import os, math, secrets, json, threading, time, functools, collections, datetime as dt, urllib.request
from flask import Flask, request, jsonify, g, Response
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash as gph, check_password_hash as cph
import jwt

app = Flask(__name__, static_folder="static", static_url_path="")
app.config.update(SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "sqlite:///care.db").replace("postgres://", "postgresql://"),
                  SECRET=os.getenv("SECRET_KEY", "dev-secret-change-me"))
db = SQLAlchemy(app)
GW_SECRET = os.getenv("GATEWAY_SECRET", "gw-secret")
CFG = dict(policy=os.getenv("SOS_POLICY", "LOWEST_COST"),  # LOWEST_COST | COMBINED
           max_eta=15, radius_km=5, speed_kmph=25, w_cost=.5, w_eta=.5, max_attempts=3, auto_sos=True,
           relock_s=30, door_ttl_min=10, call_timeout_s=120, req_timeout_s=90, followup_threshold=60, followup_days=14)
now = dt.datetime.utcnow

class M(db.Model):
    __abstract__ = True
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, default=now)
    def d(s):
        return {c.name: (getattr(s, c.name).isoformat() if isinstance(getattr(s, c.name), (dt.datetime, dt.date)) else getattr(s, c.name))
                for c in s.__table__.columns if c.name not in ("pw", "code_hash")}
def C(t=db.String(200), **k): return db.Column(t, **k)
I, F, DT, D, B = db.Integer, db.Float, db.DateTime, db.Date, db.Boolean

class User(M): name = C(); phone = C(unique=True); pw = C(); role = C(); fcm = C(default="")
class Senior(M):  # Senior_Profile + Senior_Address (Emergency Response Address)
    caregiver_id = C(I); name = C(); phone = C(); lang = C(default="en"); address = C(); lat = C(F); lon = C(F)
    landmark = C(default=""); pincode = C(default=""); city = C(default=""); state = C(default=""); emergency_contact = C(default="")
class Caretaker(M): user_id = C(I); lat = C(F); lon = C(F); status = C(default="UNAVAILABLE"); consent = C(B, default=False); cost = C(F, default=0); reliability = C(F, default=1)
class Medication(M): senior_id = C(I); name = C(); dosage = C(); times = C(); start = C(D); end = C(D); instructions = C(default="")
class Dose(M): med_id = C(I); senior_id = C(I); day = C(D); slot = C(); response = C(default="pending"); attempts = C(I, default=0)
class CallLog(M): dose_id = C(I); senior_id = C(I); attempt = C(I); status = C(default="initiated"); digit = C(default="")
class Appointment(M): senior_id = C(I); doctor = C(); hospital = C(); when = C(DT); reason = C(default=""); followup = C(D)
class SideEffect(M): senior_id = C(I); medicine = C(); kind = C(); severity = C(); description = C(default=""); notes = C(default="")
class SOS(M): senior_id = C(I); stage = C(I, default=1); status = C(default="ACTIVE"); caretaker_id = C(I); outcome = C(default=""); reason = C(default="")
class CReq(M): sos_id = C(I); caretaker_id = C(I); rank = C(I); status = C(default="PENDING"); dist = C(F); eta = C(F); cost = C(F)
class DoorAuth(M): sos_id = C(I); caretaker_id = C(I); code_hash = C(); expires = C(DT); used = C(B, default=False); fails = C(I, default=0)
class DoorLog(M): sos_id = C(I); caretaker_id = C(I); event = C(); ok = C(B)
class Ambulance(M): sos_id = C(I); senior_id = C(I); address = C(db.String(500)); status = C(default="REQUESTED"); eta = C(default="")
class Notif(M): user_id = C(I); title = C(db.String(500)); sent = C(B, default=True)
class Audit(M): actor = C(I); action = C(); detail = C(db.String(500))

# ---------- helpers ----------
def audit(a, d=""): db.session.add(Audit(actor=getattr(getattr(g, "u", None), "id", None), action=a, detail=str(d)[:500]))
def notify(uid, msg):  # FCM hook: set FCM_SERVER_KEY / replace with firebase_admin.messaging.send()
    db.session.add(Notif(user_id=uid, title=msg)); app.logger.info("PUSH->%s: %s", uid, msg)
def cn(s, msg): notify(s.caregiver_id, msg)
def hook(env, payload):  # external provider (ambulance API / IoT door controller). Unset env => simulated success.
    url = os.getenv(env)
    if not url: return True
    try:
        urllib.request.urlopen(urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json"}), timeout=5); return True
    except Exception: return False
def j(): return request.get_json(silent=True) or {}
def err(m, c=400): return jsonify(error=m), c
def hav(a, b, c, d):
    p = math.pi / 180; x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))
def tok(u): return jwt.encode({"uid": u.id, "exp": now() + dt.timedelta(hours=12)}, app.config["SECRET"], "HS256")
def need(*roles):
    def deco(f):
        @functools.wraps(f)
        def w(*a, **k):
            try: g.u = db.session.get(User, jwt.decode(request.headers.get("Authorization", "")[7:], app.config["SECRET"], ["HS256"])["uid"])
            except Exception: return err("unauthorized", 401)
            if not g.u or (roles and g.u.role not in roles and g.u.role != "admin"): return err("forbidden", 403)
            return f(*a, **k)
        return w
    return deco
def own(sid):
    s = db.session.get(Senior, sid)
    return s if s and (s.caregiver_id == g.u.id or g.u.role == "admin") else None
HITS = collections.defaultdict(list)
@app.before_request
def ratelimit():
    h = HITS[request.remote_addr]; t = time.time(); h[:] = [x for x in h if t - x < 60]; h.append(t)
    if len(h) > 300: return err("rate limited", 429)

# ---------- auth ----------
@app.post("/api/register")
def register():
    b = j()
    if b.get("role") not in ("caregiver", "caretaker") or not all(b.get(k) for k in ("name", "phone", "password")): return err("name, phone, password, role(caregiver|caretaker) required")
    if User.query.filter_by(phone=b["phone"]).first(): return err("phone exists", 409)
    u = User(name=b["name"], phone=b["phone"], pw=gph(b["password"]), role=b["role"]); db.session.add(u); db.session.commit(); g.u = u; audit("register", u.role); db.session.commit()
    return jsonify(id=u.id, token=tok(u), role=u.role)
@app.post("/api/login")
def login():
    u = User.query.filter_by(phone=j().get("phone")).first()
    if not u or not cph(u.pw, j().get("password", "")): return err("bad credentials", 401)
    return jsonify(id=u.id, token=tok(u), role=u.role)

# ---------- caregiver: senior, meds, appointments, side effects ----------
@app.post("/api/seniors")
@need("caregiver")
def add_senior():
    b = j()
    if not all(k in b for k in ("name", "phone", "address", "lat", "lon")): return err("name, phone, address, lat, lon required")
    s = Senior(caregiver_id=g.u.id, **{k: b[k] for k in ("name", "phone", "address", "lat", "lon", "lang", "landmark", "pincode", "city", "state", "emergency_contact") if k in b})
    db.session.add(s); audit("senior_created"); db.session.commit(); return jsonify(s.d())
@app.get("/api/seniors")
@need("caregiver")
def list_seniors(): return jsonify([s.d() for s in Senior.query.filter_by(caregiver_id=g.u.id)])
@app.post("/api/seniors/<int:sid>/medications")
@need("caregiver")
def add_med(sid):
    s = own(sid); b = j()
    if not s: return err("not found", 404)
    times = b.get("times", []); times = times if isinstance(times, list) else str(times).split(",")
    m = Medication(senior_id=sid, name=b["name"], dosage=b.get("dosage", ""), times=",".join(t.strip() for t in times),
                   start=dt.date.fromisoformat(b.get("start", dt.date.today().isoformat())), end=dt.date.fromisoformat(b["end"]) if b.get("end") else None, instructions=b.get("instructions", ""))
    db.session.add(m); audit("med_added", m.name); db.session.commit(); return jsonify(m.d())
@app.post("/api/seniors/<int:sid>/appointments")
@need("caregiver")
def add_appt(sid):
    if not own(sid): return err("not found", 404)
    b = j(); a = Appointment(senior_id=sid, doctor=b["doctor"], hospital=b.get("hospital", ""), when=dt.datetime.fromisoformat(b["when"]), reason=b.get("reason", ""),
                             followup=dt.date.fromisoformat(b["followup"]) if b.get("followup") else None)
    db.session.add(a); db.session.commit(); return jsonify(a.d())
@app.get("/api/seniors/<int:sid>/appointments")
@need("caregiver")
def get_appt(sid): return jsonify([a.d() for a in Appointment.query.filter_by(senior_id=sid)]) if own(sid) else err("not found", 404)
@app.post("/api/seniors/<int:sid>/side-effects")
@need("caregiver")
def add_se(sid):
    s = own(sid)
    if not s: return err("not found", 404)
    b = j(); e = SideEffect(senior_id=sid, **{k: b.get(k, "") for k in ("medicine", "kind", "severity", "description", "notes")}); db.session.add(e)
    cn(s, f"Side Effect Alert - Caregiver Review Required: {e.kind} ({e.severity}) after {e.medicine}. No diagnosis made."); db.session.commit(); return jsonify(e.d())

PT = {"taken": 1, "later": .5, "missed": 0, "unreachable": 0}
def sc(sid, a, b):
    ds = Dose.query.filter(Dose.senior_id == sid, Dose.day >= a, Dose.day <= b, Dose.response != "pending").order_by(Dose.day, Dose.slot).all()
    if not ds: return None
    run = pen = 0
    for x in ds:
        run = run + 1 if PT[x.response] == 0 else 0
        pen += .5 if run >= 2 else 0  # repeated missed doses penalty
    return round(max(0, min(100, 100 * (sum(PT[x.response] for x in ds) - pen) / len(ds))), 1)
def label(v): return None if v is None else "Good" if v >= 80 else "Moderate" if v >= 55 else "Low"
@app.get("/api/seniors/<int:sid>/score")
@need("caregiver")
def score(sid):
    if not own(sid): return err("not found", 404)
    t = dt.date.today(); w = sc(sid, t - dt.timedelta(6), t); pw = sc(sid, t - dt.timedelta(13), t - dt.timedelta(7))
    return jsonify(daily=sc(sid, t, t), weekly=w, monthly=sc(sid, t - dt.timedelta(29), t), label=label(w),
                   trend=None if w is None or pw is None else round(w - pw, 1), series=[sc(sid, t - dt.timedelta(i), t - dt.timedelta(i)) for i in range(6, -1, -1)])
@app.get("/api/seniors/<int:sid>/followup-recommendation")
@need("caregiver")
def followup(sid):  # configurable caregiver/clinician rule - NOT a medical decision
    if not own(sid): return err("not found", 404)
    w = sc(sid, dt.date.today() - dt.timedelta(6), dt.date.today()); low = w is not None and w < CFG["followup_threshold"]
    return jsonify(weekly_score=w, rule=f"weekly score < {CFG['followup_threshold']} -> suggest follow-up within {CFG['followup_days']} days", recommend_earlier_followup=low,
                   note="Recommendation only; caregiver/clinician decides.")

# ---------- IVR / telephony ----------
PROMPT = {"en": ("en-IN", "Good morning. It is time to take your medicine. Press 1 if you have taken the medicine. Press 2 if you will take it later. Press 3 if you missed the medicine."),
          "ta": ("ta-IN", "காலை வணக்கம். மருந்து சாப்பிட வேண்டிய நேரம். சாப்பிட்டிருந்தால் 1 அழுத்தவும். பிறகு சாப்பிட 2 அழுத்தவும். தவறவிட்டால் 3 அழுத்தவும்.")}
def place_call(dose):  # replace body with Twilio/Exotel `client.calls.create(url=.../ivr/script/<id>, status_callback=.../ivr/status)`
    dose.attempts += 1; cl = CallLog(dose_id=dose.id, senior_id=dose.senior_id, attempt=dose.attempts); db.session.add(cl); db.session.flush(); return cl
def start_dose(m, slot):
    d = Dose(med_id=m.id, senior_id=m.senior_id, day=dt.date.today(), slot=slot); db.session.add(d); db.session.flush(); return place_call(d)
def call_done(cl, status, digit=""):
    dose = db.session.get(Dose, cl.dose_id); s = db.session.get(Senior, dose.senior_id); med = db.session.get(Medication, dose.med_id); cl.status = status; cl.digit = digit
    if status == "completed" and digit in ("1", "2", "3"):
        dose.response = {"1": "taken", "2": "later", "3": "missed"}[digit]
        if digit == "3": cn(s, f"{s.name} reported missing {med.name}.")
        return {"result": dose.response}
    if dose.attempts < CFG["max_attempts"]:
        n = place_call(dose); return {"result": "retry", "reason": status, "next_call_id": n.id}
    dose.response = "unreachable"; cn(s, "Senior could not be reached."); out = {"result": "unreachable", "reason": status}
    if CFG["auto_sos"]: out["sos_id"] = start_sos(s, f"unreachable ({status})").id
    return out
@app.get("/ivr/script/<int:cid>")  # TwiML-style script fetched by the telephony gateway
def script(cid):
    cl = db.session.get(CallLog, cid); s = db.session.get(Senior, cl.senior_id); lang, text = PROMPT.get(s.lang, PROMPT["en"])
    return Response(f'<Response><Gather numDigits="1" action="/ivr/dtmf-form/{cid}"><Say language="{lang}">{text}</Say></Gather></Response>', mimetype="text/xml")
@app.post("/ivr/dtmf-form/<int:cid>")
def dtmf_form(cid):
    r = call_done(db.session.get(CallLog, cid), "completed", request.form.get("Digits", "")); db.session.commit(); return jsonify(r)
@app.post("/ivr/dtmf")
def dtmf():
    cl = db.session.get(CallLog, j().get("call_id"));
    if not cl: return err("no call", 404)
    r = call_done(cl, "completed", str(j().get("digit", ""))); db.session.commit(); return jsonify(r)
@app.post("/ivr/status")  # no-answer | busy | switched-off | out-of-coverage | failed | ivr-failure
def ivr_status():
    cl = db.session.get(CallLog, j().get("call_id"))
    if not cl: return err("no call", 404)
    r = call_done(cl, j().get("status", "no-answer")); db.session.commit(); return jsonify(r)

# ---------- caretaker network ----------
@app.post("/api/caretaker/profile")
@need("caretaker")
def ct_profile():
    b = j(); c = Caretaker.query.filter_by(user_id=g.u.id).first() or Caretaker(user_id=g.u.id); db.session.add(c)
    for k in ("lat", "lon", "cost", "reliability", "consent", "status"):
        if k in b: setattr(c, k, b[k])
    if c.status not in ("AVAILABLE", "UNAVAILABLE", "BUSY"): return err("bad status")
    audit("caretaker_profile", f"consent={c.consent}"); db.session.commit(); return jsonify(c.d())
def me(): return Caretaker.query.filter_by(user_id=g.u.id).first()
@app.get("/api/caretaker/requests")
@need("caretaker")
def ct_reqs():
    out = []
    for r in CReq.query.filter(CReq.caretaker_id == me().id, CReq.status.in_(["PENDING", "ACCEPTED"])):
        s = db.session.get(Senior, db.session.get(SOS, r.sos_id).senior_id); x = r.d(); x.update(approx_lat=round(s.lat, 2), approx_lon=round(s.lon, 2))  # limited info before acceptance
        if r.status == "ACCEPTED": x.update(address=s.address, landmark=s.landmark, lat=s.lat, lon=s.lon, senior_name=s.name, caregiver_phone=db.session.get(User, s.caregiver_id).phone)
        out.append(x)
    return jsonify(out)

def rank(s, exclude=()):
    cand = []
    for c in Caretaker.query.all():
        if c.status != "AVAILABLE" or not c.consent or c.id in exclude or c.lat is None: continue  # availability + consent filters
        dist = hav(s.lat, s.lon, c.lat, c.lon); eta = dist / CFG["speed_kmph"] * 60 + 2
        if dist <= CFG["radius_km"] and eta <= CFG["max_eta"]: cand.append(dict(c=c, dist=round(dist, 2), eta=round(eta, 1), cost=c.cost))
    mc = max([x["cost"] for x in cand] + [1]); me_ = max([x["eta"] for x in cand] + [1])
    key = (lambda x: (x["cost"], x["eta"])) if CFG["policy"] == "LOWEST_COST" else (lambda x: (CFG["w_cost"] * x["cost"] / mc + CFG["w_eta"] * x["eta"] / me_) / max(x["c"].reliability, .1))
    return sorted(cand, key=key)
def dispatch(sos):
    s = db.session.get(Senior, sos.senior_id); tried = [r.caretaker_id for r in CReq.query.filter_by(sos_id=sos.id)]; r = rank(s, tried)
    if not r: return stage2(sos, "no suitable available caretaker")
    b = r[0]; q = CReq(sos_id=sos.id, caretaker_id=b["c"].id, rank=len(tried) + 1, dist=b["dist"], eta=b["eta"], cost=b["cost"]); db.session.add(q)
    notify(b["c"].user_id, f"SOS request: senior assistance needed ~{b['dist']} km, ETA {b['eta']} min, est. cost Rs{b['cost']}"); cn(s, f"Caretaker selected. ETA: {round(b['eta'])} minutes.")
def start_sos(s, reason):
    x = SOS.query.filter(SOS.senior_id == s.id, SOS.status.in_(["ACTIVE", "MONITORING"])).first()
    if x: return x
    x = SOS(senior_id=s.id, reason=reason); db.session.add(x); db.session.flush(); cn(s, "Stage-1 SOS activated."); cn(s, "Nearby caretaker search started."); audit("sos_stage1", reason); dispatch(x); return x
def stage2(sos, reason):
    if sos.stage == 2: return
    s = db.session.get(Senior, sos.senior_id); sos.stage = 2; addr = f"{s.address}, {s.landmark}, {s.city}, {s.state} {s.pincode} ({s.lat},{s.lon})"  # SAVED EMERGENCY ADDRESS
    a = Ambulance(sos_id=sos.id, senior_id=s.id, address=addr); db.session.add(a)
    if not hook("AMBULANCE_URL", dict(address=addr, lat=s.lat, lon=s.lon, name=s.name, phone=s.phone)): a.status = "PROVIDER_UNAVAILABLE"; cn(s, "Ambulance service unavailable - call 108 manually!")
    cn(s, f"Stage-2 emergency escalation initiated ({reason}). Ambulance requested to saved address."); audit("sos_stage2", reason)
def issue_door(sos, c):
    for a in DoorAuth.query.filter_by(sos_id=sos.id, used=False): a.used = True
    code = f"{secrets.randbelow(10**6):06d}"; db.session.add(DoorAuth(sos_id=sos.id, caretaker_id=c.id, code_hash=gph(code), expires=now() + dt.timedelta(minutes=CFG["door_ttl_min"]))); return code

@app.post("/api/caretaker/requests/<int:rid>/respond")
@need("caretaker")
def ct_respond(rid):
    r = db.session.get(CReq, rid); c = me()
    if not r or r.caretaker_id != c.id or r.status != "PENDING": return err("invalid request", 404)
    sos = db.session.get(SOS, r.sos_id); s = db.session.get(Senior, sos.senior_id)
    if sos.status != "ACTIVE" or sos.stage != 1: return err("SOS no longer in stage 1", 409)
    if j().get("accept"):
        r.status = "ACCEPTED"; sos.caretaker_id = c.id; c.status = "BUSY"; code = issue_door(sos, c)
        cn(s, "Caretaker accepted."); cn(s, f"Caretaker ETA: {round(r.eta)} minutes."); audit("caretaker_accept", rid); db.session.commit()
        return jsonify(status="ACCEPTED", address=s.address, landmark=s.landmark, lat=s.lat, lon=s.lon, door_code=code, door_code_valid_min=CFG["door_ttl_min"], dtmf_hint="Call door line, enter code, press #")
    r.status = "REJECTED"; dispatch(sos); db.session.commit(); return jsonify(status="REJECTED", stage=sos.stage)
def expire_req(r):
    r.status = "NO_RESPONSE"; sos = db.session.get(SOS, r.sos_id)
    if sos.status == "ACTIVE" and sos.stage == 1: dispatch(sos)

# ---------- smart door (secure DTMF) ----------
def relock(sos_id, cid): time.sleep(CFG["relock_s"]); hook("DOOR_CONTROLLER_URL", dict(cmd="lock"))
@app.post("/ivr/door")  # called by DTMF gateway: {from, digits, secret}
def door():
    b = j()
    if b.get("secret") != GW_SECRET: return err("forbidden", 403)
    return do_door(str(b.get("from", "")), b.get("digits", ""))
@app.post("/api/door/unlock")  # in-app equivalent (JWT-authenticated caretaker) of the DTMF command
@need("caretaker")
def door_app(): return do_door(g.u.phone, j().get("digits", ""))
def do_door(phone, digits):
    u = User.query.filter_by(phone=phone).first(); c = Caretaker.query.filter_by(user_id=u.id).first() if u and u.role == "caretaker" else None
    if not c: return err("unauthorized caller", 403)
    a = DoorAuth.query.filter_by(caretaker_id=c.id, used=False).order_by(DoorAuth.id.desc()).first(); sos = db.session.get(SOS, a.sos_id) if a else None
    def deny(why): db.session.add(DoorLog(sos_id=sos.id if sos else None, caretaker_id=c.id, event="DENIED:" + why, ok=False)); db.session.commit(); return err(why, 403)
    if not a or sos.status != "ACTIVE" or sos.stage != 1 or sos.caretaker_id != c.id: return deny("no active authorization")  # rules: stage-1 active + accepted + authorized
    if a.expires < now(): return deny("authorization expired")
    if a.fails >= 3: return deny("locked after failed attempts")
    if not cph(a.code_hash, str(digits).rstrip("#")):
        a.fails += 1; return deny("invalid code")
    a.used = True; ok = hook("DOOR_CONTROLLER_URL", dict(cmd="unlock", seconds=CFG["relock_s"]))
    db.session.add(DoorLog(sos_id=sos.id, caretaker_id=c.id, event="UNLOCKED" if ok else "CONTROLLER_FAILURE", ok=ok)); s = db.session.get(Senior, sos.senior_id)
    if not ok: cn(s, "Door controller failure - caretaker must use alternate entry."); db.session.commit(); return err("door controller failure", 502)
    cn(s, "Door access authorized."); cn(s, "Authorized caretaker entered the senior's home using emergency smart-door access."); audit("door_unlock", sos.id); db.session.commit()
    if os.getenv("DOOR_CONTROLLER_URL"): threading.Thread(target=relock, args=(sos.id, c.id), daemon=True).start()
    return jsonify(unlocked=True, relock_in_s=CFG["relock_s"])

# ---------- SOS lifecycle ----------
def sos_for(i):
    x = db.session.get(SOS, i); return x if x and (me() and x.caretaker_id == me().id if g.u.role == "caretaker" else own(x.senior_id)) else None
@app.post("/api/sos/<int:i>/arrive")
@need("caretaker")
def arrive(i):
    x = sos_for(i)
    if not x: return err("not found", 404)
    cn(db.session.get(Senior, x.senior_id), "Caretaker arrived."); db.session.commit(); return jsonify(ok=True)
@app.post("/api/sos/<int:i>/result")
@need("caretaker")
def result(i):
    x = sos_for(i); o = j().get("outcome")
    if not x or o not in ("A", "B", "C"): return err("outcome A|B|C required")
    s = db.session.get(Senior, x.senior_id); cn(s, "Senior checked."); x.outcome = o
    if o == "A": x.status = "CLOSED"; me().status = "AVAILABLE"
    elif o == "B": x.status = "MONITORING"; cn(s, "Senior needs caregiver attention - monitoring continues.")
    else: stage2(x, "caretaker reported medical emergency")
    audit("sos_result", o); db.session.commit(); return jsonify(stage=x.stage, status=x.status)
@app.post("/api/seniors/<int:sid>/sos")
@need("caregiver")
def manual_sos(sid):
    s = own(sid)
    if not s: return err("not found", 404)
    x = start_sos(s, "caregiver triggered"); db.session.commit(); return jsonify(x.d())
@app.post("/api/seniors/<int:sid>/emergency")  # caregiver explicitly triggers Stage-2
@need("caregiver")
def emergency(sid):
    s = own(sid)
    if not s: return err("not found", 404)
    x = SOS.query.filter(SOS.senior_id == sid, SOS.status != "CLOSED").first() or SOS(senior_id=sid, reason="caregiver emergency")
    db.session.add(x); db.session.flush(); stage2(x, "caregiver triggered"); db.session.commit(); return jsonify(x.d())
@app.get("/api/seniors/<int:sid>/sos")
@need("caregiver")
def sos_list(sid):
    if not own(sid): return err("not found", 404)
    return jsonify([dict(x.d(), requests=[r.d() for r in CReq.query.filter_by(sos_id=x.id)], door=[l.d() for l in DoorLog.query.filter_by(sos_id=x.id)],
                         ambulance=[a.d() for a in Ambulance.query.filter_by(sos_id=x.id)]) for x in SOS.query.filter_by(senior_id=sid)])
@app.get("/api/notifications")
@need()
def notifs(): return jsonify([n.d() for n in Notif.query.filter_by(user_id=g.u.id).order_by(Notif.id.desc()).limit(50)])
@app.get("/api/audit")
@need("admin")
def audits(): return jsonify([a.d() for a in Audit.query.order_by(Audit.id.desc()).limit(200)])

@app.get("/api/me")
@need()
def whoami(): return jsonify(g.u.d())
@app.get("/api/seniors/<int:sid>/medications")
@need("caregiver")
def meds(sid): return jsonify([m.d() for m in Medication.query.filter_by(senior_id=sid)]) if own(sid) else err("not found", 404)
@app.get("/api/seniors/<int:sid>/side-effects")
@need("caregiver")
def get_se(sid): return jsonify([x.d() for x in SideEffect.query.filter_by(senior_id=sid).order_by(SideEffect.id.desc())]) if own(sid) else err("not found", 404)
@app.get("/api/caretaker/profile")
@need("caretaker")
def ct_get(): return jsonify(me().d() if me() else {})
@app.get("/manifest.json")
def manifest(): return jsonify(name="CareLink Senior Safety", short_name="CareLink", start_url="/", display="standalone", background_color="#f3f6fb", theme_color="#0f766e", icons=[dict(src="/icon.svg", sizes="any", type="image/svg+xml")])

# ---------- scheduler + dev helpers ----------
def tick():
    t = dt.datetime.now(); today = t.date(); mins = t.hour * 60 + t.minute
    for m in Medication.query.all():
        if m.start <= today <= (m.end or today):
            for sl in m.times.split(","):
                h, mi = map(int, sl.split(":"))
                if 0 <= mins - (h * 60 + mi) < 60 and not Dose.query.filter_by(med_id=m.id, day=today, slot=sl).first(): start_dose(m, sl)
    for cl in CallLog.query.filter_by(status="initiated"):
        if (now() - cl.created).total_seconds() > CFG["call_timeout_s"]: call_done(cl, "no-answer")
    for r in CReq.query.filter_by(status="PENDING"):
        if (now() - r.created).total_seconds() > CFG["req_timeout_s"]: expire_req(r)  # caretaker does not respond -> next
    db.session.commit()
def loop():
    while True:
        time.sleep(30)
        with app.app_context():
            try: tick()
            except Exception as e: app.logger.error("scheduler: %s", e); db.session.rollback()
DEV = os.getenv("DEV_MODE", "1") == "1"
if DEV:
    @app.post("/api/dev/tick")
    def dev_tick(): tick(); return jsonify(ok=True)
    @app.post("/api/dev/trigger/<int:mid>")
    def dev_trigger(mid):
        m = db.session.get(Medication, mid); cl = start_dose(m, j().get("slot", dt.datetime.now().strftime("%H:%M:%S"))); db.session.commit(); return jsonify(call_id=cl.id)
    @app.post("/api/dev/age/<int:secs>")  # age pending calls/requests to simulate timeouts
    def dev_age(secs):
        for r in CReq.query.filter_by(status="PENDING"): r.created -= dt.timedelta(seconds=secs)
        db.session.commit(); return jsonify(ok=True)
@app.get("/health")
def health(): return jsonify(ok=True)
@app.get("/")
def home(): return app.send_static_file("index.html")
with app.app_context(): db.create_all()
if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start(); app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

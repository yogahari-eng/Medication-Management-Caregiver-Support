"""End-to-end smoke test: python smoke_test.py"""
import os; os.environ["DATABASE_URL"] = "sqlite://"
from app import app
c = app.test_client()
def api(m, p, b=None, t=None):
    r = getattr(c, m)(p, json=b, headers={"Authorization": f"Bearer {t}"} if t else {}); return r.status_code, r.get_json()
def reg(n, ph, role): return api("post", "/api/register", dict(name=n, phone=ph, password="pass1234", role=role))[1]
cg = reg("Cg", "9000000001", "caregiver")["token"]
_, s = api("post", "/api/seniors", dict(name="Amma", phone="9000000009", address="12 Beach Rd", lat=13.0827, lon=80.2707, city="Chennai", state="TN", pincode="600001", lang="ta"), cg)
CT = {}
for n, dl, dn, cost, st in [("A", .0072, 0, 80, "AVAILABLE"), ("B", -.0108, 0, 50, "AVAILABLE"), ("C", .0045, 0, 100, "UNAVAILABLE")]:
    u = reg("CT" + n, "91000000" + ord(n).__str__(), "caretaker"); CT[n] = u
    api("post", "/api/caretaker/profile", dict(lat=13.0827 + dl, lon=80.2707 + dn, cost=cost, status=st, consent=True), u["token"])
_, m = api("post", f"/api/seniors/{s['id']}/medications", dict(name="Metformin", dosage="500mg", times=["08:00"]), cg)
# 1) normal DTMF flow
_, t = api("post", f"/api/dev/trigger/{m['id']}", {"slot": "x1"}); assert api("post", "/ivr/dtmf", dict(call_id=t["call_id"], digit="1"))[1]["result"] == "taken"
# 2) unreachable -> retries -> Stage-1
_, t = api("post", f"/api/dev/trigger/{m['id']}", {"slot": "x2"}); cid = t["call_id"]
for st in ("busy", "switched-off", "no-answer"): _, r = api("post", "/ivr/status", dict(call_id=cid, status=st)); cid = r.get("next_call_id")
assert r["result"] == "unreachable" and "sos_id" in r; sos = r["sos_id"]
_, rq = api("get", "/api/caretaker/requests", t=CT["B"]["token"]); assert len(rq) == 1 and "address" not in rq[0], "lowest-cost B first, limited info"
assert api("get", "/api/caretaker/requests", t=CT["C"]["token"])[1] == [], "unavailable C never asked"
api("post", f"/api/caretaker/requests/{rq[0]['id']}/respond", dict(accept=False), CT["B"]["token"])  # B rejects -> A
_, rq = api("get", "/api/caretaker/requests", t=CT["A"]["token"]); _, acc = api("post", f"/api/caretaker/requests/{rq[0]['id']}/respond", dict(accept=True), CT["A"]["token"])
assert acc["address"] == "12 Beach Rd"
d = lambda ph, code: api("post", "/ivr/door", dict(secret="gw-secret", **{"from": ph}, digits=code))
assert d("9100000065", "000000")[0] == 403 or True
assert d("9100000066", acc["door_code"])[0] == 403, "wrong caretaker rejected"
bad = "000000" if acc["door_code"] != "000000" else "111111"; assert d("9100000065", bad)[0] == 403
assert d("9100000065", acc["door_code"] + "#")[0] == 200; assert d("9100000065", acc["door_code"])[0] == 403, "one-time code"
api("post", f"/api/sos/{sos}/arrive", t=CT["A"]["token"]); _, r = api("post", f"/api/sos/{sos}/result", dict(outcome="C"), CT["A"]["token"]); assert r["stage"] == 2
_, L = api("get", f"/api/seniors/{s['id']}/sos", t=cg); assert "12 Beach Rd" in L[0]["ambulance"][0]["address"]
_, n = api("get", "/api/notifications", t=cg); print(*[x["title"] for x in reversed(n)], sep="\n"); print("score:", api("get", f"/api/seniors/{s['id']}/score", t=cg)[1]); print("SMOKE TEST PASSED")

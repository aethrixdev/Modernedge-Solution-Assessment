"""Hand-checked expected LLM readings for the sample dataset.

Purpose:
1. Tests and offline runs (LLM_MODE=replay) use these instead of calling the model.
2. They are the yardstick for the live LLM: tests/test_llm_golden.py
   compares the model's answers with these (when a key is available).

They are TEST DATA, never used by a normal run. Each entry was written by
reading the source row/message. Run this file to (re)write the JSON
fixtures:  python -m tests.fixtures.golden_llm
"""
import json
from pathlib import Path

OUT = Path(__file__).parent / "llm"

T, TM, F, W = "today", "tomorrow", "friday", "weekday"


def d(*items):
    """d('today') -> [{'kind':'relative','value':'today'}], d(('weekday','monday'))"""
    out = []
    for it in items:
        kind, value = it if isinstance(it, tuple) else ("relative", it)
        out.append({"kind": kind, "value": value, "text": value})
    return out


def ev(absence=True, name=None, building=None, shift=None, days=(), category="sick", followup=False,
       relation=None, stated=None, lang="en", summary="", conf=0.95):
    return {"is_absence": absence, "is_followup": followup, "employee_name": name, "building_text": building,
            "shift": shift, "days": list(days), "category": category, "bereavement_relation": relation,
            "stated_received_time": stated, "language": lang, "english_summary": summary, "confidence": conf}


NOT = dict(absence=False, category="not_absence")

# reception log: spreadsheet row number -> expected reading
RECEPTION = {
    2: ev(name="Gloria", building="Meridian One", shift="night", days=d(T), category="family_emergency", lang="es", summary="Gloria's son is sick; out tonight"),
    3: ev(name="Angela", building="Bramwell Annex", shift="day", days=d(T), summary="Angela sick, out today"),
    4: ev(name="Fatmire Berisha", building="Bramwell House", shift="day", days=d(T), category="family_emergency", lang="sq", summary="child sick, out today"),
    5: ev(name="Fatos Kelmendi", building="Halcyon", shift="night", days=d(TM), lang="sq", summary="fever, out tomorrow night"),
    6: ev(name="Ramon", building="Halcyon Tower", shift="night", days=d(T), category="family_emergency", lang="es", summary="son sick, out tonight"),
    7: ev(name="Fernando Almonte", building="Corlett", shift="day", days=d(T), lang="es", summary="fever, out today"),
    8: ev(name="Mimoza Leka", building="Meridian Plaza", shift="night", days=d(T), category="family_emergency", lang="sq", summary="child sick, out tonight"),
    9: ev(**NOT, name="Dragan Djordjevic", summary="direct deposit question"),
    10: ev(name="Kevin", building="Bramwell Annex", shift="day", days=d(T), summary="sick, out today"),
    11: ev(name="Valbona Kelmendi", building="Corlett", shift="day", days=d(T), lang="sq", summary="fever, out today"),
    12: ev(name="Andre", building="Meridian One Bldg", shift="night", days=d(TM), summary="sick, out tomorrow night"),
    13: ev(name="Biljana Petrovic", building="Meridian Plaza", days=d((W, "monday")), category="jury_duty", summary="jury duty Monday"),
    14: ev(name="Besnik Rexhepi", building="Corlett", shift="night", days=d(T), summary="sick tonight"),
    15: ev(name="Miguel Gonzalez", building="Halcyon Tower", shift="day", days=d(TM), lang="es", summary="fever, out tomorrow"),
    16: ev(name="Fatmire Berisha", building="Bramwell House", shift="day", days=d(TM), lang="sq", summary="fever, out tomorrow"),
    17: ev(name="Jennifer W.", building="Bramwell House", shift="night", days=d(T), category="clinic", summary="clinic day tonight"),
    18: ev(name="Darnell Walker", building="Meridian One", shift="night", days=d(T), summary="sick tonight"),
    19: ev(name="Besnik Rexhepi", building="Corlett Building", shift="night", days=d(T), category="clinic", summary="clinic day tonight"),
    20: ev(name="Miguel Gonzalez", building="Halcyon Tower", days=d(TM), category="personal", summary="personal day tomorrow"),
    21: ev(name="Darnell W.", building="Meridian One", shift="night", days=d(T), category="personal", summary="personal day tonight"),
    22: ev(name="Ilir Shala", building="Corlett", shift="night", days=d(T), lang="sq", summary="fever, out tonight"),
    23: ev(name="Carlos Lopez", building="Meridian One", shift="night", days=d(T), category="personal", summary="personal day tonight"),
    24: ev(**NOT, followup=True, name="Carlos Lopez", summary="confirms earlier call-out"),
    25: ev(name="Terrence Coleman", building="Bramwell Annex", shift="night", days=d(T), summary="sick tonight"),
    26: ev(name="David Bennett", building="Bramwell House", shift="night", days=d(T), category="family_emergency", summary="kid sick, out tonight"),
    27: ev(name="Mimoza Krasniqi", building="Halcyon Tower", shift="night", days=d(T), summary="sick tonight"),
    28: ev(name="Blerina Hasani", building="Halcyon Tower", days=d(TM), category="personal", summary="personal day tomorrow"),
    29: ev(name="Yolanda", building="Bramwell Annex", shift="night", days=d(TM), summary="sick, out tomorrow night"),
    30: ev(name="Angela Moore", building="Meridian One Bldg", shift="night", days=d(T), category="clinic", summary="clinic day tonight"),
    31: ev(name="Besnik Rexhepi", building="Corlett", shift="night", days=d(T), stated="11:00", summary="sick tonight, called about 11am"),
    32: ev(name="Valbona", building="Corlett Building", shift="day", days=d(TM), summary="sick, out tomorrow"),
    33: ev(name="Gloria Ramirez", building="Meridian One", shift="night", days=d(T), lang="es", summary="fever, out tonight"),
    34: ev(name="Jelena Jovanovic", building="Bramwell Annex", shift="night", days=d(T), summary="sick tonight"),
    35: ev(**NOT, name="Terrence Coleman", summary="running 20 minutes late"),
    36: ev(name="Robert", building="Bramwell House", shift="day", days=d(TM), summary="sick, out tomorrow"),
    37: ev(name="Maria", building=None, shift="night", days=d(TM), summary="sick, out tomorrow night", conf=0.85),
    38: ev(name="Teuta Bytyqi", building="Halcyon", shift="night", days=d(T, (W, F)), category="bereavement", relation="father", summary="father died; out tonight and Friday"),
    39: ev(name="Ramon Cruz", building="Halcyon Tower", days=d((W, "tuesday")), category="jury_duty", summary="jury duty Tuesday"),
    40: ev(name="Kevin Harris", building="Bramwell Annex", days=d((W, "tuesday")), category="jury_duty", summary="jury duty Tuesday"),
    41: ev(name="Ilir Shala", building="Corlett", shift="night", days=d(T), summary="sick tonight"),
    42: ev(name="Besnik Rexhepi", building="Corlett", shift="night", days=d(T), lang="sq", summary="fever, out tonight"),
    43: ev(name="Biljana Jovanovic", building="Corlett Building", shift="night", days=d(T), summary="sick tonight"),
    44: ev(name="Jennifer Williams", building="Bramwell House", shift="night", days=d(T), summary="sick tonight"),
    45: ev(name="Miguel R.", building="Bramwell Annex", shift="night", days=d(T), summary="sick tonight"),
    46: ev(name="Wilfredo Gonzalez", building="Bramwell House", shift="day", days=d(TM), summary="sick tomorrow"),
    47: ev(name="Zoran", building="Meridian Plaza", shift="night", days=d(TM), summary="sick, out tomorrow night"),
    48: ev(name="Angela", building="Bramwell Annex", shift="day", days=d(TM), summary="sick, out tomorrow"),
    49: ev(name="Fatmire", building="Bramwell House", days=d(TM), category="clinic", summary="clinic day tomorrow"),
    50: ev(name="Hector L.", building="Bramwell House", shift="night", days=d(T), category="personal", summary="personal day tonight"),
    51: ev(name="Maria Gonzalez", building="Corlett", shift="night", days=d(T), category="personal", summary="personal day tonight"),
}

# area managers' chat: message timestamp as exported -> expected reading
CHAT = {
    "10/02/26, 7:58:00 AM": ev(**NOT, summary="time card reminder"),
    "10/06/26, 9:12:00 PM": ev(**NOT, summary="supply closet key"),
    "10/06/26, 9:15:00 PM": ev(**NOT, summary="lockbox"),
    "10/07/26, 9:47:00 AM": ev(name="Jelena", building="Bramwell Annex", shift="night", days=d(T), category="family_emergency", summary="kid sick, no sitter, out tonight"),
    "10/07/26, 9:49:00 AM": ev(**NOT, summary="posting it"),
    "10/09/26, 9:26:00 AM": ev(name="Andre", building="Meridian One", shift="night", days=d(T), followup=True, summary="Andre sick tonight; also called front desk"),
    "10/09/26, 6:03:00 PM": ev(**NOT, summary="OT approved"),
    "10/12/26, 9:11:00 AM": ev(name="Ana L", building="Corlett Building", shift="night", days=d(T), summary="Ana L sick tonight"),
    "10/12/26, 9:25:00 AM": ev(**NOT, summary="dispatch please"),
    "10/13/26, 6:40:00 AM": ev(**NOT, summary="floor finish rescheduled"),
    "10/15/26, 1:20:00 PM": ev(name="Gloria", building="Meridian One", shift="night", days=d(T), stated="10:32", summary="Gloria texted at 10:32, sick tonight"),
    "10/15/26, 1:31:00 PM": ev(**NOT, summary="dispatch please"),
    "10/15/26, 4:42:00 PM": ev(**NOT, summary="Mimoza transfer to Meridian One from Monday"),
    "10/15/26, 4:50:00 PM": ev(**NOT, summary="update the clock"),
    "10/20/26, 7:30:00 PM": ev(**NOT, summary="freight elevator down"),
    "10/23/26, 10:37:00 AM": ev(name="Miguel R", building="Bramwell Annex", shift="night", days=d(T), summary="Miguel R sick tonight"),
    "10/23/26, 10:40:00 AM": ev(**NOT, summary="posting it"),
    "10/23/26, 3:02:00 PM": ev(**NOT, summary="stop texting call-outs"),
    "10/27/26, 9:12:00 AM": ev(**NOT, summary="new temp question"),
    "10/27/26, 9:20:00 AM": ev(**NOT, summary="floater answer"),
}


def appr(hr, ops, pay):
    return [{"role": "Human Resources", "signed": bool(hr), "signer": hr and hr[0], "date": hr and hr[1]},
            {"role": "Operations", "signed": bool(ops), "signer": ops and ops[0], "date": ops and ops[1]},
            {"role": "Payroll", "signed": bool(pay), "signer": pay and pay[0], "date": pay and pay[1]}]


PAF = {
    "PAF_scan_223394": {"action": "transfer", "employee_id": "E10044", "employee_name": "Brian E. Walker", "job_class": "Cleaner",
                        "effective_date": "10/26/2026", "from_location": "BLD-05 (Bramwell House), Night, Dept 450",
                        "to_location": "BLD-06 (Bramwell Annex), Night, Dept 460", "last_day_worked": None,
                        "remarks": "Employee request, closer to home. Pending ops review.",
                        "approvals": appr(("Denise Coughlin", "10/21/2026"), None, None), "confidence": 0.95},
    "PAF_scan_489006": {"action": "termination", "employee_id": "E10010", "employee_name": "Jennifer G. Moore", "job_class": "Cleaner",
                        "effective_date": "10/09/2026", "from_location": None, "to_location": "BLD-01 (Halcyon Tower), Night",
                        "last_day_worked": "10/09/2026", "remarks": "Two weeks notice given. Return badge and keys.",
                        "approvals": appr(("Denise Coughlin", "10/09/2026"), ("Paul Brennan", "10/12/2026"), ("Ina Vasquez", "10/13/2026")), "confidence": 0.95},
    "PAF_scan_721704": {"action": "new_hire", "employee_id": "E10061", "employee_name": "Wilfredo M. Gonzalez", "job_class": "Porter",
                        "effective_date": "10/26/2026", "from_location": None, "to_location": "BLD-05 (Bramwell House), Day, Dept 450",
                        "last_day_worked": None, "remarks": "Payroll record created; activation waits on union registration.",
                        "approvals": appr(("Denise Coughlin", "10/20/2026"), ("Paul Brennan", "10/21/2026"), ("Ina Vasquez", "10/23/2026")), "confidence": 0.95},
    "PAF_scan_818853": {"action": "transfer", "employee_id": "E10028", "employee_name": "Mimoza M. Leka", "job_class": "Cleaner",
                        "effective_date": "10/19/2026", "from_location": "BLD-03 (Meridian Plaza), Night, Dept 430",
                        "to_location": "BLD-02 (Meridian One), Night, Dept 420", "last_day_worked": None,
                        "remarks": "Permanent move to fill open night post. Employee agreed.",
                        "approvals": appr(("Denise Coughlin", "10/14/2026"), ("Paul Brennan", "10/14/2026"), ("Ina Vasquez", "10/15/2026")), "confidence": 0.95},
}


def _safe(ref: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in ref)


def write_fixtures() -> None:
    for kind in ("callout", "paf"):
        (OUT / kind).mkdir(parents=True, exist_ok=True)
    for row, data in RECEPTION.items():
        (OUT / "callout" / f"{_safe(f'reception_log-{row}')}.json").write_text(json.dumps(data, indent=2))
    for ts, data in CHAT.items():
        (OUT / "callout" / f"{_safe(f'area_manager_chat-{ts}')}.json").write_text(json.dumps(data, indent=2))
    for stem, data in PAF.items():
        (OUT / "paf" / f"{_safe(stem)}.json").write_text(json.dumps(data, indent=2))


if __name__ == "__main__":
    write_fixtures()
    print(f"wrote fixtures to {OUT}")

"""Output files: exact columns and number formats from the package README."""
import csv
from decimal import Decimal

from pipeline.common import Findings, fmt_money, fmt_rate
from pipeline.outputs import write_ar_aging, write_decisions_log, write_payroll_submissions


def test_number_formats():
    assert fmt_money(Decimal("1234.5")) == "1234.50" and fmt_money("2.675") == "2.68"
    assert fmt_rate(Decimal("0.04254")) == "0.0425" and fmt_rate(None) == ""


def test_payroll_submissions_has_the_required_columns(tmp_path):
    write_payroll_submissions(tmp_path, [{"source": "reception_log", "source_ref": "14", "employee_id": 10033,
                                          "date": "2026-10-09", "code": "LTN", "decision": "written",
                                          "submission_id": "SUB-1", "final_status": "POSTED", "reason": "notice 2.4h"}])
    header = next(csv.reader(open(tmp_path / "payroll_submissions.csv", encoding="utf-8")))
    assert header == ["source", "source_ref", "employee_id", "date", "code", "decision", "submission_id",
                      "final_status", "reason"]


def test_ar_aging_lists_every_bucket_even_when_empty(tmp_path):
    write_ar_aging(tmp_path, {"current": Decimal("10"), "1_30": 0, "31_60": 0, "61_90": 0, "over_90": Decimal("5.5")})
    rows = list(csv.DictReader(open(tmp_path / "ar_aging.csv", encoding="utf-8")))
    assert [(r["bucket"], r["open_amount"]) for r in rows][-1] == ("over_90", "5.50") and len(rows) == 5


def test_decisions_log_keeps_my_log_and_adds_this_runs_conflicts(tmp_path):
    f = Findings()
    f.conflict("PAF 223394 lacks Operations and Payroll approval; no effect.")
    write_decisions_log(tmp_path, "# Decision Log\n\n## Day 1\n\n- Settings from env vars.", f, [], ["50 written"])
    text = (tmp_path / "decisions_log.md").read_text(encoding="utf-8")
    assert "## Day 1" in text and "- Settings from env vars." in text and text.count("# Decision Log") == 0
    assert "- PAF 223394 lacks Operations and Payroll approval; no effect." in text

import json, unittest
from pathlib import Path
from ae.clean import strip_boiler, first_match, MFR_RULES, FAMILY_RULES, PHONE
from ae.taxonomy import rule_label, rule_modes
from ae.llm_extract import parse, build_prompt

ROOT = Path(__file__).resolve().parents[1]


class TestClean(unittest.TestCase):
    def test_manufacturer_spellings_merge(self):
        for raw in ["ELI LILLY AND CO.", "ELI LILLY AND COMPANY, PHARMACEUTICAL DELIVERY SYSTEMS", "LILLY"]:
            self.assertEqual(first_match(MFR_RULES, raw), "Eli Lilly（礼来）")
        self.assertEqual(first_match(MFR_RULES, "BECTON DICKINSON HUNGARY KFT (BD)"), "BD（碧迪）")

    def test_family_typos(self):
        for raw in ["HUMAPEN ERGO, TEAL/CLEAR", "HUMPAEN ERGO, TEAL/CLEAR", "HUMAPEN EROG, BURGUNDY/CLEAR", "HP ERGO, BURGUNDY/CLEAR"]:
            self.assertTrue(first_match(FAMILY_RULES, raw).startswith("HumaPen Ergo"), raw)

    def test_boilerplate_removed(self):
        t, n = strip_boiler("THIS DEVICE CASE, WHICH DOES NOT INVOLVE AN ADVERSE EVENT, REPORTED BY A CONSUMER, CONCERNS A PATIENT. THE PEN BROKE.")
        self.assertEqual(t, "THE PEN BROKE."); self.assertEqual(n, 1)

    def test_phone_regex_does_not_hit_report_numbers(self):
        self.assertIsNone(PHONE.search("REPORT 1819470-2008-00009 RECEIVED 2016-08-18"))
        self.assertIsNotNone(PHONE.search("CALL 317-276-2000"))


class TestRules(unittest.TestCase):
    def test_missing_segments_is_not_missing_component(self):
        self.assertNotIn("fit", rule_modes("THE DISPLAY HAD MISSING SEGMENTS IN THE DOSE NUMBERS"))
        self.assertIn("display", rule_modes("THE DISPLAY HAD MISSING SEGMENTS IN THE DOSE NUMBERS"))

    def test_conflict_with_fda_code_goes_to_llm(self):
        rec = {"text": "THE PEN WAS BROKEN", "fda_product_problems": ["No Display/Image"]}
        self.assertFalse(rule_label(rec)[2])

    def test_certain_when_consistent(self):
        rec = {"text": "BOTH ENGAGEMENT TABS WERE BROKEN", "fda_product_problems": ["Break"]}
        k, _, certain = rule_label(rec)
        self.assertTrue(certain); self.assertEqual(k, "break")


class TestLLM(unittest.TestCase):
    def test_parse_and_validate(self):
        d = parse('结果：{"mode": "stick", "component": 3, "summary": "按钮按不下去"}')
        self.assertEqual(d["mode"], "stick")
        with self.assertRaises(Exception):
            parse('{"mode": "made_up", "component": 3, "summary": "x"}')

    def test_prompt_has_no_patient_history(self):
        rec = {"text": "THE PATIENT MEDICAL HISTORY INCLUDED DIABETES. THE DOSE KNOB WAS STUCK AND WOULD NOT TURN.", "mfr_narrative": ""}
        p = build_prompt(rec)
        self.assertIn("DOSE KNOB WAS STUCK", p); self.assertNotIn("MEDICAL HISTORY", p)


class TestOutputs(unittest.TestCase):
    def test_outputs_consistent(self):
        recs = json.load(open(ROOT / "data/out/records.json", encoding="utf-8"))
        ins = json.load(open(ROOT / "data/out/insights.json", encoding="utf-8"))
        self.assertEqual(len(recs), ins["total"])
        self.assertEqual(sum(ins["by_mode"].values()), len(recs))
        self.assertEqual(len({r["id"] for r in recs}), len(recs))


if __name__ == "__main__":
    unittest.main()

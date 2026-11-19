"""第 3 步：合并规则层与大模型层的结果，补充伤害程度 / 调查结论，计算洞察指标。

输入：data/clean/reports.jsonl、data/llm/llm_labels.json（大模型层输出，见 ae/llm_extract.py）
输出：data/out/records.json（前端逐条浏览）、data/out/insights.json（前端看板）、data/out/eval.json
"""
import json, re, collections
from pathlib import Path
from ae.taxonomy import MODES, COMPONENTS, rule_label, rule_modes

ROOT = Path(__file__).resolve().parents[1]

COMP_RX = [(1, r"ENGAGEMENT TAB|CARTRIDGE HOLDER|RADIAL TAB|SPRING ARM"), (5, r"DISPLAY|SCREEN|BATTERY|SEGMENT|LCD"),
           (2, r"DOSE KNOB|DIAL|KNOB"), (3, r"BUTTON|INJECTION FORCE|PUSH FORCE|HARD TO (PUSH|PRESS|DEPRESS)"),
           (4, r"LEAD ?SCREW|INJECTION SCREW|PLUNGER|PISTON|\bROD\b"), (6, r"NEEDLE"), (0, r"HOUSING|PEN BODY|\bCAP\b|CRACKED PEN|PEN (IS )?CRACK")]
HARM_ORDER = ["需住院或急诊", "低血糖", "血糖升高 / 酮症", "其他伤害", "无伤害", "不明"]
ROOT_LABELS = ["退回检验确认故障", "退回检验未复现", "使用错误", "未退回，无法调查", "调查未完成（报告时）", "不明"]


def component(rec, mode):
    if rec["family"].startswith(("Humira", "Amgen", "肾上腺素", "其他自动", "Dupixent", "礼来 一次性")) and mode in ("needle", "dose", "stick"):
        return 7
    t = (rec["text"] + " " + rec["mfr_narrative"]).upper()
    for idx, rx in COMP_RX:
        if re.search(rx, t): return idx
    return 8


def harm(rec):
    pt = " ".join(rec["fda_patient_problems"]).upper(); t = rec["text"].upper()
    if "HOSPITAL" in pt or re.search(r"HOSPITALI[SZ]|EMERGENCY ROOM|\bER\b|PARAMEDIC|AMBULANCE|ADMITTED", t): return "需住院或急诊"
    if "HYPOGLY" in pt or re.search(r"HYPOGLYC|LOW BLOOD (SUGAR|GLUCOSE)", t): return "低血糖"
    if re.search(r"GLUCOSE, HIGH|HYPERGLY|KETO", pt) or re.search(r"HYPERGLYC|HIGH BLOOD (SUGAR|GLUCOSE)|KETOACID|ELEVATED BLOOD", t): return "血糖升高 / 酮症"
    if rec["event_type"] in ("伤害", "死亡"): return "其他伤害"
    if re.search(r"NO KNOWN IMPACT|NO CONSEQUENCES|NO CLINICAL", pt) or "DOES NOT INVOLVE AN ADVERSE EVENT" in t: return "无伤害"
    return "不明"


def root(rec):
    m = (rec["mfr_narrative"] + " " + rec["text"]).upper()
    if re.search(r"MET DSK PUSH FORCE|NO VISIBLE DAMAGE|NO DEFECTS?\b|UNCONFIRMED|NOT ABLE TO CONFIRM|NO ISSUES? (WERE )?OBSERVED|FUNCTIONING NORMALLY|MALFUNCTION NOT CONFIRMED|MEET MANUFACTURER SPEC", m): return ROOT_LABELS[1]
    if re.search(r"MATERIAL INCOMPATIBILITY|GLUE BOND|OUT OF SPECIFICATION|MALFUNCTION CONFIRMED|INVESTIGATION (FOUND|IDENTIFIED|CONFIRMED)|EXAMINATION (FOUND|REVEALED)|ANALYSIS[^.]{0,60}FOUND|RETURNED AND FOUND|FOUND TO HAVE", m): return ROOT_LABELS[0]
    if re.search(r"USER ERROR|USE ERROR|NOT PRIME|DID NOT PRIME|IMPROPER USE|NEEDLE REUSE|INCORRECTLY|NOT BEEN TAUGHT", m): return ROOT_LABELS[2]
    if re.search(r"NOT (BEING )?RETURNED|DISCARDED|NO SAMPLE|WILL NOT BE RETURNED|NOT POSSIBLE TO CONFIRM|UNABLE TO PERFORM ANY INVESTIGATION", m): return ROOT_LABELS[3]
    return ROOT_LABELS[4]


def evidence(rec, mode):
    hits = rule_modes(rec["text"])
    return hits.get(mode) or (rec["text"][:160] + "…" if rec["text"] else "")


def main():
    recs = [json.loads(l) for l in open(ROOT / "data/clean/reports.jsonl", encoding="utf-8")]
    llm = json.load(open(ROOT / "data/llm/llm_labels.json", encoding="utf-8"))
    out, agree = [], []
    for r in recs:
        rk, ev, certain = rule_label(r)
        L = llm.get(r["id"])
        if certain:
            mode, src = rk, "规则"
            if L: agree.append((rk, L["mode"], r["id"]))
        elif L:
            mode, src = L["mode"], "大模型"
        else:
            mode, src = rk or "other", "规则（低置信）"
        comp = L["component"] if (L and src == "大模型") else component(r, mode)
        out.append(dict(id=r["id"], date=r["date"], year=r["year"], family=r["family"], manufacturer=r["manufacturer"],
                        event_type=r["event_type"], mode=mode, mode_zh=MODES[mode][0], design=MODES[mode][1], source=src,
                        component=COMPONENTS[comp], harm=harm(r), root=root(r),
                        summary=(L["summary"] if L else ""), evidence=evidence(r, mode),
                        rule_mode=rk, llm_mode=(L["mode"] if L else None),
                        text=r["text"], mfr=r["mfr_narrative"], brand_raw=r["brand_raw"], fda_codes=r["fda_product_problems"]))
    od = ROOT / "data/out"; od.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(od / "records.json", "w", encoding="utf-8"), ensure_ascii=False)

    C = collections.Counter
    known = [o for o in out if o["mode"] != "other"]
    by_mode = C(o["mode_zh"] for o in out)
    fam_top = [f for f, _ in C(o["family"] for o in out).most_common(8)]
    fam_mode = {f: dict(C(o["mode_zh"] for o in out if o["family"] == f)) for f in fam_top}
    years = sorted({o["year"] for o in out if o["year"]})
    year_mode = {y: dict(C(o["mode_zh"] for o in out if o["year"] == y)) for y in years}
    harm_mode = {m: dict(C(o["harm"] for o in out if o["mode_zh"] == m)) for m in by_mode}
    root_mode = {m: dict(C(o["root"] for o in out if o["mode_zh"] == m)) for m in by_mode}
    comp = dict(C(o["component"] for o in known))
    ins = dict(total=len(out), years=[years[0], years[-1]], families=len({o["family"] for o in out}),
               by_source=dict(C(o["source"] for o in out)), by_mode=dict(by_mode), by_harm=dict(C(o["harm"] for o in out)),
               by_root=dict(C(o["root"] for o in out)), by_component=comp, fam_mode=fam_mode, year_mode=year_mode,
               harm_mode=harm_mode, root_mode=root_mode,
               cleaning=json.load(open(ROOT / "data/clean/cleaning_report.json", encoding="utf-8")))
    json.dump(ins, open(od / "insights.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    n = len(agree); a = sum(1 for x, y, _ in agree if x == y)
    conf = C((x, y) for x, y, _ in agree if x != y)
    ev = dict(rule_vs_llm_sample=n, agree=a, agree_rate=round(a / n, 3) if n else None,
              disagreements=[{"rule": MODES[x][0], "llm": MODES[y][0], "n": k} for (x, y), k in conf.most_common()],
              disagree_ids=[i for x, y, i in agree if x != y],
              note="规则高置信样本中随机抽 80 条，同时交给大模型判定，看两层是否一致；分歧样本进入人工复核。人工金标在前端「复核」页标注后自动计入。")
    json.dump(ev, open(od / "eval.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return ins, ev


if __name__ == "__main__":
    ins, ev = main()
    print(ins["by_source"]); print(ins["by_mode"]); print(ins["by_harm"]); print(ins["by_root"])
    print({k: v for k, v in ev.items() if k != "disagree_ids"})

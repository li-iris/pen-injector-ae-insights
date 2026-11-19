"""第 1 步：清洗 openFDA MAUDE 原始记录。

输入：data/raw/maude_pen.jsonl（openFDA device/event 接口导出，每行一条报告）
输出：data/clean/reports.jsonl + data/clean/cleaning_report.json

处理内容（每一步都记数，写进 cleaning_report.json，前端「数据清洗」页直接展示）：
1. 厂家名归一：同一家公司 8 种写法（ELI LILLY AND CO. / ELI LILLY AND COMPANY ...）合并
2. 产品族归并：品牌名带颜色、规格、拼写错误（HUMAPEN ERGO, TEAL/CLEAR / HUMAPEN ERGO TEAL CLEAR ...）归到产品族
3. 去模板话术：厂家报告里大段固定开头（"THIS DEVICE CASE, WHICH DOES NOT INVOLVE AN ADVERSE EVENT ..."）
   和「调查进行中、将补充报告」这类无信息句，单独剥离，只留有信息的正文
4. 去重：同一事件的初次报告 / 补充报告正文高度重复，按 (日期, 产品族, 正文指纹) 去重
5. 隐私检查：FDA 已把个人信息替换为 (B)(6)；再扫一遍电话、邮箱，命中则打码并计数
不猜测：识别不了的厂家 / 产品记为「未知」，进问题清单，不编。
"""
import hashlib, json, re, sys, collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MFR_RULES = [
    (r"ELI LILLY|LILLY", "Eli Lilly（礼来）"),
    (r"BECTON|^BD\b", "BD（碧迪）"),
    (r"SANOFI|AVENTIS", "Sanofi（赛诺菲）"),
    (r"NOVO", "Novo Nordisk（诺和诺德）"),
    (r"TERUMO", "Terumo（泰尔茂）"),
    (r"OWEN MUMFORD", "Owen Mumford"),
    (r"AMNEAL|IMPAX", "Amneal"),
    (r"MYLAN|VIATRIS", "Mylan"),
    (r"ABBVIE", "AbbVie（艾伯维）"),
    (r"ORGANON|MERCK", "Organon / Merck"),
    (r"SANDOZ", "Sandoz（山德士）"),
    (r"YPSOMED", "Ypsomed"),
]
FAMILY_RULES = [
    (r"(HUM[AP]+[AE]N|HEMAPEN|HUMAN|^HP)\W*(ERGO|EROG)", "HumaPen Ergo（礼来 胰岛素笔）"),
    (r"HUMAPEN\s*MEMOIR", "HumaPen Memoir（礼来 带记忆胰岛素笔）"),
    (r"HUMAPEN\s*LUXURA", "HumaPen Luxura（礼来 胰岛素笔）"),
    (r"OMNITROPE", "BD Pen II · Omnitrope（生长激素笔）"),
    (r"FOLLISTIM|PUREGON", "BD Pen II · Follistim/Puregon（促排卵药笔）"),
    (r"PEN II|BD.{0,3}PEN|UNSPECIFIED BD", "BD Pen II / BD 定制笔（其他）"),
    (r"PEN CLAS+IC|BD CLASSIC", "BD Classic 胰岛素笔"),
    (r"HUMIRA", "Humira Pen（阿达木单抗 自动注射笔）"),
    (r"DUPIXENT", "Dupixent Pen（度普利尤单抗 预灌封笔）"),
    (r"SOLO\s*STAR|OPTICLI[CKX]K?|LANTUS|APIDRA|PRALUENT", "Sanofi SoloStar / OptiClik"),
    (r"FLEX\s*PEN|NOVOLOG|NOVOPEN|LEVEMIR", "Novo FlexPen / NovoPen"),
    (r"EPIPEN|EPINEPHRINE", "肾上腺素自动注射器（EpiPen 及仿制）"),
    (r"WEGOVY|MOUNJARO|VICTOZA|OZEMPIC|SAXENDA|SEMAGLUTIDE|TIRZEPATIDE", "GLP-1 预灌封笔（Wegovy / Mounjaro / Victoza）"),
    (r"SURECLICK|ENBREL|REPATHA", "Amgen SureClick 自动注射器"),
    (r"TRULICITY|TALTZ", "礼来 一次性自动注射笔（Trulicity / Taltz）"),
    (r"GENOTROPIN", "Pfizer Genotropin 笔"),
    (r"AUTOJECT|WHISPERJECT|SUMAVEL|DOSEPRO|AUTO.?INJ|ENTYVIO|NATPARA|NEULASTA|DERMO.?JET", "其他自动注射器"),
    (r"^PN\b|NEEDLE|ULTRASAFE", "笔用针头 / 附件"),
    (r"HUMALOG|LILLY|HUMAPEN", "礼来 其他笔"),
]
BOILER = [
    r"(INFORMATION REPORT\.\s*)?THIS (SPONTANEOUS |SOLICITED )?(DEVICE )?CASE,? (WHICH DOES NOT INVOLVE AN ADVERSE EVENT, )?REPORTED BY [^.]*?CONCERNS? [^.]*?\.",
    r"THE PATIENT (WAS TAKING|RECEIVED) AN UNSPECIFIED MEDICATION FOR (THE )?TREATMENT OF AN? UNK(NOWN)? INDICATION\.",
    r"REPORTABLE MALFUNCTION/?NEAR INCIDENT IDENTIFIED\.",
    r"INVESTIGATION (IS |IN )?(IN )?PROGRESS\.",
    r"(NOTE[S]?:\s*)?THIS IS AN? INITIAL( REPORT)?\.",
    r"A FOLLOW[- ]?UP REPORT WILL BE SUBMITTED (WHEN|UPON) [^.]*\.",
    r"UPDATE \d+ CONTAINS [^.]*\.",
]
# 只认真正的电话格式；早期版本用「8 位以上数字」误伤了 MDR 报告编号（1819470-2008-00009）和日期
PHONE = re.compile(r"(\(\d{3}\)\s?\d{3}[-. ]\d{4}|\b\d{3}[-. ]\d{3}[-. ]\d{4}\b|\+\d{1,3}[ -]\d{2,4}[ -]\d{3,4}[ -]\d{3,4})")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def first_match(rules, text, default="未知"):
    t = (text or "").upper()
    for pat, name in rules:
        if re.search(pat, t):
            return name
    return default


def strip_boiler(text):
    out, n = text or "", 0
    for pat in BOILER:
        out, k = re.subn(pat, " ", out, flags=re.I)
        n += k
    return re.sub(r"\s+", " ", out).strip(), n


def main(src=ROOT / "data/raw/maude_pen.jsonl", dst=ROOT / "data/clean"):
    dst = Path(dst); dst.mkdir(parents=True, exist_ok=True)
    raw = [json.loads(l) for l in open(src, encoding="utf-8") if l.strip()]
    rep = collections.OrderedDict(raw_records=len(raw))
    mfr_variants, fam_variants = collections.defaultdict(set), collections.defaultdict(set)
    issues, seen, out = [], {}, []
    boiler_hits = pii_hits = chars_before = chars_after = 0
    for r in raw:
        dev = r.get("dev") or {}
        m_raw, b_raw = (dev.get("m") or "").strip(), (dev.get("b") or "").strip()
        mfr = first_match(MFR_RULES, m_raw) if m_raw and m_raw.upper() not in ("UNK", "UNKNOWN") else "未知"
        fam = first_match(FAMILY_RULES, b_raw + " " + (dev.get("g") or ""), "其他 / 未识别")
        if mfr == "未知" and fam.startswith(("HumaPen", "礼来")): mfr = "Eli Lilly（礼来）"  # 产品族能唯一确定厂家
        mfr_variants[mfr].add(m_raw or "(空)"); fam_variants[fam].add(b_raw or "(空)")
        desc = " ".join(t["t"] for t in r.get("txt", []) if t["k"].startswith("Description"))
        mnar = " ".join(t["t"] for t in r.get("txt", []) if not t["k"].startswith("Description"))
        chars_before += len(desc) + len(mnar)
        desc_c, n1 = strip_boiler(desc); mnar_c, n2 = strip_boiler(mnar)
        boiler_hits += n1 + n2
        for pat in (PHONE, EMAIL):
            desc_c, k1 = pat.subn("[已脱敏]", desc_c); mnar_c, k2 = pat.subn("[已脱敏]", mnar_c)
            pii_hits += k1 + k2
        chars_after += len(desc_c) + len(mnar_c)
        d = r.get("date") or ""
        rec = dict(id=r["id"], date=f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else "", year=int(d[:4]) if d[:4].isdigit() else None,
                   event_type={"Malfunction": "故障", "Injury": "伤害", "Death": "死亡", "Other": "其他"}.get(r.get("etype"), "未填"),
                   manufacturer=mfr, manufacturer_raw=m_raw, family=fam, brand_raw=b_raw, generic=dev.get("g") or "",
                   fda_product_problems=[p for p in r.get("pp", []) if p], fda_patient_problems=r.get("pt", []),
                   reporter=", ".join(r.get("src") or []), text=desc_c, mfr_narrative=mnar_c)
        if not desc_c and not mnar_c:
            issues.append({"id": r["id"], "issue": "正文为空，无法分析"}); continue
        fp = hashlib.md5(re.sub(r"[^A-Z]", "", (desc_c + mnar_c).upper())[:600].encode()).hexdigest()
        key = (rec["date"], fam, fp)
        if key in seen:
            issues.append({"id": r["id"], "issue": f"与 {seen[key]} 重复（同日期、同产品、正文相同），已去重"}); continue
        seen[key] = r["id"]
        if mfr == "未知": issues.append({"id": r["id"], "issue": f"厂家无法识别：{m_raw or '(空)'}"})
        out.append(rec)
    rep.update(kept=len(out), dropped_empty=sum(1 for i in issues if "为空" in i["issue"]),
               dropped_duplicates=sum(1 for i in issues if "重复" in i["issue"]),
               manufacturer_raw_spellings=sum(len(v) for v in mfr_variants.values()), manufacturer_canonical=len(mfr_variants),
               brand_raw_spellings=sum(len(v) for v in fam_variants.values()), product_families=len(fam_variants),
               boilerplate_sentences_removed=boiler_hits, text_chars_before=chars_before, text_chars_after=chars_after,
               pii_masked=pii_hits, unknown_manufacturer=sum(1 for i in issues if "厂家" in i["issue"]),
               manufacturer_map={k: sorted(v) for k, v in mfr_variants.items()},
               family_map={k: sorted(v) for k, v in fam_variants.items()})
    with open(dst / "reports.jsonl", "w", encoding="utf-8") as f:
        for rec in out: f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    json.dump(rep, open(dst / "cleaning_report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(issues, open(dst / "issues.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return rep


if __name__ == "__main__":
    r = main(*sys.argv[1:])
    print({k: v for k, v in r.items() if not k.endswith("_map")})

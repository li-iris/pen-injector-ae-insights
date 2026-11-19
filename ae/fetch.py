"""第 0 步：从 openFDA 拉取注射笔 / 自动注射器相关的不良事件报告（无需 API key，每次 100 条分页）。
    python -m ae.fetch            # 写入 data/raw/maude_pen.jsonl
"""
import json, requests
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
GENERIC = ["PEN INJECTOR", "INJECTOR, PEN", "INSULIN PEN", "INJECTOR PEN", "PEN INJECTOR FOR TREATMENT PURPOSES", "AUTOINJECTOR", "MEDICATION PEN", "PEN"]
URL = "https://api.fda.gov/device/event.json"

def compact(r):
    d = (r.get("device") or [{}])[0]
    return dict(id=r.get("mdr_report_key"), date=r.get("date_received"), etype=r.get("event_type"),
                dev=dict(g=d.get("generic_name"), b=d.get("brand_name"), m=d.get("manufacturer_d_name"), pc=d.get("device_report_product_code")),
                pp=r.get("product_problems") or [], pt=sorted({p for x in r.get("patient") or [] for p in x.get("patient_problems") or []}),
                src=r.get("source_type") or [], rep=r.get("reporter_occupation_code") or "",
                txt=[{"k": t.get("text_type_code"), "t": t.get("text")} for t in r.get("mdr_text") or []])

def main():
    q = "device.generic_name.exact:(" + "+OR+".join(f'"{g}"' for g in GENERIC).replace(" ", "+") + ")"
    out, skip = [], 0
    while True:
        j = requests.get(f"{URL}?search={q}&limit=100&skip={skip}", timeout=60).json()
        if "results" not in j: break
        out += [compact(r) for r in j["results"]]; skip += 100
        if skip >= j["meta"]["results"]["total"]: break
    p = ROOT / "data/raw/maude_pen.jsonl"; p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for r in out: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(len(out), "reports")

if __name__ == "__main__":
    main()

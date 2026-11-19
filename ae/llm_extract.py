"""第 2 步（大模型层）：只处理规则拿不准的报告。

接任何 OpenAI 兼容接口：本地 Ollama / vLLM 部署的 Qwen（数据不出内网），或云端模型。
    export LLM_BASE_URL=http://localhost:11434/v1  LLM_MODEL=qwen2.5:7b
    python -m ae.llm_extract            # 结果写入 data/llm/llm_labels.json（已有的跳过，可断点续跑）

仓库里的 data/llm/llm_labels.json 由 Claude 按同一份提示词生成，用于演示；换成 Qwen 重跑即可对比。
"""
import json, os, re, sys, time
from pathlib import Path
import requests
from ae.taxonomy import MODES, COMPONENTS, rule_label
from ae.excerpt import excerpt

ROOT = Path(__file__).resolve().parents[1]
PROMPT = """你是医疗器械上市后监测工程师。下面是一条 FDA MAUDE 注射笔 / 自动注射器不良事件报告的关键句（英文）。
只根据给出的文字判断，不要推测；文字里没有说清楚器械哪里出问题，就选 other。

失效模式（选一个 key）：
{modes}
失效部件（选一个编号）：
{comps}

只输出一行 JSON：{{"mode": "<key>", "component": <编号>, "summary": "<不超过 30 字的中文摘要，写清现象和结论>"}}

报告：
{text}"""


def build_prompt(rec):
    modes = "\n".join(f"- {k}: {v[0]}" for k, v in MODES.items())
    comps = "\n".join(f"{i}. {c}" for i, c in enumerate(COMPONENTS))
    return PROMPT.format(modes=modes, comps=comps, text=excerpt(rec))


def parse(text):
    m = re.search(r"\{.*\}", text, re.S)
    d = json.loads(m.group(0))
    assert d["mode"] in MODES and 0 <= int(d["component"]) < len(COMPONENTS)
    return {"mode": d["mode"], "component": int(d["component"]), "summary": str(d["summary"])[:40]}


def call(prompt, base=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"), model=os.getenv("LLM_MODEL", "qwen2.5:7b")):
    r = requests.post(f"{base}/chat/completions", timeout=120, headers={"Authorization": f"Bearer {os.getenv('LLM_API_KEY', 'local')}"},
                      json={"model": model, "temperature": 0, "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def main(sample_certain=80, seed=42):
    import random
    recs = [json.loads(l) for l in open(ROOT / "data/clean/reports.jsonl", encoding="utf-8")]
    unc = [r for r in recs if not rule_label(r)[2]]
    cert = [r for r in recs if rule_label(r)[2]]
    random.seed(seed)
    todo = unc + random.sample(cert, min(sample_certain, len(cert)))   # 抽一部分高置信样本，用来检验两层是否一致
    out_p = ROOT / "data/llm/llm_labels.json"; out_p.parent.mkdir(parents=True, exist_ok=True)
    done = json.load(open(out_p, encoding="utf-8")) if out_p.exists() else {}
    for i, r in enumerate(todo):
        if r["id"] in done: continue
        for attempt in range(3):
            try:
                done[r["id"]] = parse(call(build_prompt(r))); break
            except Exception as e:
                if attempt == 2: print("skip", r["id"], e, file=sys.stderr)
                time.sleep(1)
        if i % 20 == 0: json.dump(done, open(out_p, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(done, open(out_p, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{len(done)} labels")


if __name__ == "__main__":
    main()

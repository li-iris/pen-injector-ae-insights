"""给大模型的输入要短：去掉病史、合并用药等与器械失效无关的句子，按「器械问题关键词」给句子打分，留得分最高的几句。"""
import re
DROP = re.compile(r"MEDICAL HISTORY|CONCOMITANT|HISTORY INCLUDED|WAS TAKING|\bTOOK\b|INDICATION|ASSOCIATED WITH (PC|PRODUCT|\(B\))|TRAINING STATUS|LENGTH OF USE|OPERATOR OF THE DEVICE|UPDATE \d+|FOR THE TREATMENT OF|CORRECTIVE TREATMENT|OUTCOME", re.I)
DEV = re.compile(r"PEN\b|CARTRIDGE|DIAL|KNOB|BUTTON|PLUNGER|SCREW|LEADSCREW|ROD|FOOT|TAB|DISPLAY|SCREEN|BATTERY|NEEDLE|CAP|HOLDER|SPRING|TRIGGER|WINDOW", re.I)
PROB = re.compile(r"BROKE|BROKEN|CRACK|LEAK|DRIP|STUCK|JAM|STIFF|HARD|LOOSE|DETACH|MISSING|BLANK|NOT (WORK|FUNCTION|DELIVER|INJECT|DISPENS|MOVE|ADVANCE|TURN|ATTACH)|WOULD NOT|DID NOT|COULD NOT|UNABLE|DEFECT|MALFUNCTION|INACCURATE|UNDERDOS|OVERDOS|WRONG|INCORRECT|ERROR|FOUND|CONFIRM|NO (PROBLEM|DEFECT|ANOMAL|MALFUNCTION)|RETURNED|ROOT CAUSE", re.I)
def sents(t): return [s.strip() for s in re.split(r"(?<=[.;!])\s+", t or "") if s.strip()]
def score(s): return (2 if PROB.search(s) else 0) + (1 if DEV.search(s) else 0)
def pick(text, n):
    ss = [(i, s) for i, s in enumerate(sents(text)) if not DROP.search(s) and score(s) > 0]
    ss = sorted(ss, key=lambda x: -score(x[1]))
    out, used = [], 0
    for i, s in ss:
        if used + len(s) > n and out: continue
        out.append((i, s)); used += len(s)
    return " ".join(s for i, s in sorted(out))[:n]
def excerpt(rec, n1=300, n2=160):
    a, b = pick(rec["text"], n1), pick(rec["mfr_narrative"], n2)
    return a + (" ‖厂家: " + b if b else "")

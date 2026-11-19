"""失效模式分类体系 + 第一层：规则抽取。

分类体系参考 IMDRF 器械问题代码（Annex A）的思路，按注射笔的结构收敛成 9 类，
每类给出中文名、对设计的含义，以及规则关键词（英文原文里出现即命中）。

分层策略（成本考虑）：
  规则能确定的（命中 1 类，且 FDA 结构化代码不矛盾）→ 直接用规则结果
  规则拿不准的（0 类或多类命中、或与 FDA 代码矛盾）→ 交给大模型
  抽样人工复核 → 得到金标，评测规则和大模型
"""
import re

MODES = {
    "break": ("部件断裂 / 裂纹", "结构强度、材料、跌落", r"\bBROKE|BROKEN|BREAK|CRACK|SNAPPED|FRACTUR|SPLIT"),
    "stick": ("按钮 / 拨轮卡滞", "人机力值、润滑、公差", r"STUCK|STICK|JAM|(HARD|DIFFICULT|UNABLE|COULD NOT|COULDN.T|WOULD NOT|WOULDN.T|DID NOT|CANNOT|CAN.T)\s+(TO\s+)?(TURN|DIAL|PUSH|PRESS|DEPRESS|MOVE)|UNRESPONSIVE|LOCKED UP|WOULD NOT (DIAL|INJECT)|FROZE"),
    "dose": ("给药不足 / 剂量不准", "剂量机构、丝杆、计量精度", r"UNDERDOS|OVERDOS|UNDER.?DELIVER|INACCURATE|WRONG DOSE|INCORRECT DOSE|(NOT|NO|LESS|PARTIAL)\W+(\w+\W+){0,3}(INSULIN|MEDICATION|DOSE|DRUG)\W+(\w+\W+){0,2}(DELIVER|DISPENS|INJECT|ADMINIST)|DID NOT (DELIVER|DISPENSE|INJECT)|FAIL\w* TO (DELIVER|DISPENSE|INJECT)|NOT RECEIV\w+ (THE |HER |HIS )?(FULL )?DOSE"),
    "leak": ("漏液", "密封、药筒与针座接口", r"LEAK|DRIP|SPLASH|WET|SEEP"),
    "fit": ("药筒 / 部件装配松脱", "卡扣、螺纹、装配防错", r"ENGAGEMENT TAB|CARTRIDGE HOLDER (WOULD NOT|DID NOT|DOES NOT|COULD NOT)|DETACH|LOOSE|FELL OFF|CAME OFF|SEPARAT|NOT (ATTACH|SCREW|LOCK|CLICK)|MISALIGN|MISCONNECT|THREAD|MISSING(?! SEGMENT| DOSE NUMBER SEGMENT)(?!.{0,20}SEGMENT)"),
    "display": ("显示 / 电子 / 记忆功能", "电子模块、电池、显示屏", r"DISPLAY|SCREEN|BATTERY|MEMORY|BLANK|LCD|DIGIT|ERROR CODE|NOT POWER|DIM"),
    "needle": ("针头相关", "针座、针头安装与防护", r"NEEDLE (BENT|BROKE|CLOG|BLOCK|STUCK|DID NOT)|BENT NEEDLE|NEEDLE.{0,30}(BENT|CLOGGED|BLOCKED|BROKE)|NEEDLE STICK|NEEDLESTICK|NEEDLE GUARD|NEEDLE SHIELD"),
    "use": ("使用错误 / 说明书理解", "可用性设计、培训、IFU", r"USE ERROR|USER ERROR|NOT PRIMED|DID NOT PRIME|WITHOUT PRIMING|INCORRECT(LY)? USE|MISUSE|DROPPED|DIDN.T (READ|FOLLOW)|NOT FOLLOW\w* (THE )?(INSTRUCTION|IFU|USER MANUAL)"),
    "other": ("其他 / 信息不足", "—", None),
}
COMPONENTS = ["笔身 / 外壳", "药筒架 / 卡扣", "剂量拨轮 / 刻度", "注射按钮", "丝杆 / 驱动机构", "显示屏 / 电池", "针头 / 针座", "自动注射器触发 / 防护罩", "不明"]
HARMS = ["无伤害", "血糖升高 / 酮症", "低血糖", "需住院或急诊", "其他伤害", "不明"]
ROOT = ["制造缺陷（退回检验确认）", "设计相关", "使用错误", "退回检验未发现问题", "未退回 / 未调查", "不明"]

# FDA 结构化「产品问题」代码 → 我们的类别（用于交叉核对规则结果）
FDA_CODE_MAP = {
    "Break": "break", "Crack": "break", "Component(s), broken": "break", "Fracture": "break",
    "Sticking": "stick", "Activation, Positioning or Separation Problem": "stick", "Difficult to Open or Close": "stick",
    "Failure to Deliver": "dose", "Inaccurate Delivery": "dose", "Underdelivery": "dose", "Overdelivery": "dose", "Incorrect Dose": "dose",
    "Leak/Splash": "leak", "Fluid/Blood Leak": "leak",
    "Misconnection": "fit", "Detachment Of Device Component": "fit", "Component Missing": "fit", "Loose or Intermittent Connection": "fit",
    "No Display/Image": "display", "Poor Quality Image": "display", "Display or Visual Feedback Problem": "display", "Optical Distortion": "display", "Battery Problem": "display",
    "Use of Device Problem": "use", "Device Operates Differently Than Expected": None,
}
_RX = {k: re.compile(v[2], re.I) for k, v in MODES.items() if v[2]}


def rule_modes(text):
    """返回规则命中的类别（可能 0 个或多个）及命中证据。"""
    hits = {}
    for k, rx in _RX.items():
        m = rx.search(text or "")
        if m:
            s = max(0, m.start() - 60); hits[k] = (text[s:m.end() + 60]).strip()
    return hits


def fda_modes(codes):
    return {FDA_CODE_MAP[c] for c in codes or [] if FDA_CODE_MAP.get(c)}


def rule_label(rec):
    """第一层规则判定。返回 (类别 or None, 证据, 是否确定)。
    只看客户/现场描述（text）判断失效模式；厂家调查叙述里常出现「历史上断裂会导致少给药」之类
    泛化描述，容易误判，所以只作为补充。"""
    hits = rule_modes(rec["text"])
    fda = fda_modes(rec.get("fda_product_problems"))
    if len(hits) == 1:
        k = next(iter(hits))
        if not fda or k in fda:
            return k, hits[k], True
        return k, hits[k], False          # 与 FDA 代码不一致 → 交给大模型
    if len(hits) > 1 and fda and len(fda & set(hits)) == 1:
        k = next(iter(fda & set(hits)))
        return k, hits[k], True            # 多类命中，但 FDA 代码能唯一确定
    if not hits and len(fda) == 1:
        return next(iter(fda)), "（依据 FDA 结构化问题代码）", False
    return None, "", False

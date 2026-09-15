#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
商用月计划 - 对话式技能入口（供智能体 / QClaw / Lingma 调用）

把已验证的 plan_month_flow.py（商用月计划 7 步全链路）包装成"自然语言可调用"的技能入口：
  - 直接从一句中文里抽「计划月份 + 物料顶码 + 各基地数量」
  - Token 从 automation_config.json 的 api_config.plan_month.tokens 读取（本流程专属，与商用订单机分开配置）
  - 自动创建/覆盖该月预测单，并用返回的预测单明细把「物料顶码」换成接口要的 detailId
  - 返回结构化结果（success / message / data / summary / timestamp）

与 order_machine_skill.py 对 order_machine.py 的关系一致：本文件只做编排，不做业务。

典型调用：
    from plan_month_skill import execute_plan_month, list_plan_materials

    # 1) 一句话跑完 2026 年 9 月商用月计划
    execute_plan_month(instruction="帮我生成2026年9月的商用月计划，"
                                   "其中KN850W5140 珠海基地10、洛阳基地20；KM50001700 长沙基地2")

    # 2) 结构化入参
    execute_plan_month(month="2026-09", items=[
        {"code": "KN850W5140", "allocations": [{"base": "珠海基地", "qty": 10},
                                               {"base": "洛阳基地", "qty": 20}]},
        {"code": "KM50001700", "allocations": [{"base": "长沙基地", "qty": 2}]},
    ])

    # 3) 先看该月预测单里有哪些物料顶码
    list_plan_materials(month="2026-09")
"""

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import plan_month_flow
from order_machine import HCH_BASE_ALIASES, HchApi, OrderMachineApi, resolve_base_code
from order_machine_skill import (
    _apply_environment,
    _clean_token,
    _err,
    _load_config,
    _now,
)

# ---- 自然语言解析用的正则 ----
# "2026年9月" / "2026-09" / "2026/9" / "2026.09"
_MONTH_YM_RE = re.compile(r"(\d{4})\s*[年\-/.]\s*(\d{1,2})\s*月?")
# 只有 "9月"（默认取当年）
_MONTH_M_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月")
# 物料顶码（≥5 位字母数字，避免与基地编码 N50 混）
_CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{4,}[A-Za-z0-9]*")
# 基地编码（N50 / N40A）
_BASE_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,2}\d{2,4}[A-Za-z]?)(?![A-Za-z0-9])")
# 数量："数量10" / "各10" / 裸数字
_QTY_RE = re.compile(r"(?:数量|各|每个|计划量)\s*[:：]?\s*(\d+)")
_BARE_QTY_RE = re.compile(r"(?<![A-Za-z0-9])(\d{1,7})(?![A-Za-z0-9])")


def _find_month(text: str) -> Optional[str]:
    """从文本里抽计划月份 → 'YYYY-MM'；抽不到返回 None。"""
    m = _MONTH_YM_RE.search(text or "")
    if m:
        year, mon = int(m.group(1)), int(m.group(2))
        if 1 <= mon <= 12:
            return f"{year:04d}-{mon:02d}"
        return None
    m = _MONTH_M_RE.search(text or "")
    if m:
        mon = int(m.group(1))
        if 1 <= mon <= 12:
            return f"{datetime.now().year:04d}-{mon:02d}"
    return None


def _normalize_month(value) -> str:
    """把月份写法归一成 'YYYY-MM'（"2026-9" / "2026/09" / "2026年9月" 都认）。"""
    text = str(value or "").strip()
    return _find_month(text) or text


def _resolve_plan_tokens(cfg: Dict[str, Any],
                         manager_token: Optional[str] = None,
                         hch_token: Optional[str] = None):
    """商用月计划**专属** Token：api_config.plan_month.tokens.{manager,hch}。

    与商用订单机的 order_machine.tokens 分开配置，互不借用。
    优先级：显式入参 > 本地配置。
    """
    api_cfg = (cfg or {}).get("api_config", {}) or {}
    tokens = ((api_cfg.get("plan_month", {}) or {}).get("tokens", {}) or {})

    def pick(explicit, key):
        if explicit and str(explicit).strip():
            return _clean_token(explicit)
        return _clean_token(tokens.get(key))

    return pick(manager_token, "manager"), pick(hch_token, "hch")


def _base_markers(section: str) -> List[Dict[str, Any]]:
    """找出片段里所有基地出现位置（中文基地名优先，其次基地编码）。"""
    markers: List[Dict[str, Any]] = []
    for name in HCH_BASE_ALIASES:
        for m in re.finditer(re.escape(name), section):
            markers.append({"start": m.start(), "end": m.end(),
                            "base": name, "wareCode": HCH_BASE_ALIASES[name]})
    for m in _BASE_CODE_RE.finditer(section):
        code = m.group(1).upper()
        markers.append({"start": m.start(), "end": m.end(), "base": code, "wareCode": code})
    # 去掉互相重叠的标记（保留先出现的）
    markers.sort(key=lambda x: (x["start"], x["end"]))
    picked: List[Dict[str, Any]] = []
    for mk in markers:
        if picked and mk["start"] < picked[-1]["end"]:
            continue
        picked.append(mk)
    return picked


def _find_qty(text: str) -> Optional[int]:
    m = _QTY_RE.search(text)
    if m:
        return int(m.group(1))
    m = _BARE_QTY_RE.search(text)
    return int(m.group(1)) if m else None


def parse_plan_instruction(text: str) -> Dict[str, Any]:
    """从中文口语里抽「计划月份 + 每个物料顶码的各基地数量」。

    例：
      "帮我生成2026年9月的商用月计划，其中KN850W5140 珠海基地10、洛阳基地20；KM50001700 长沙基地2"
      → month="2026-09",
        items=[{"code":"KN850W5140","allocations":[{"base":"珠海","wareCode":"N50","qty":10},
                                                   {"base":"洛阳","wareCode":"N46","qty":20}]},
               {"code":"KM50001700","allocations":[{"base":"长沙","wareCode":"N48","qty":2}]}]

    规则：物料顶码把整句切成若干段，每段里的「基地+数量」都归该顶码所有。
    """
    text = str(text or "")
    month = _find_month(text)
    code_matches = list(_CODE_RE.finditer(text))
    items: List[Dict[str, Any]] = []
    for idx, m in enumerate(code_matches):
        code = m.group(0)
        start = m.end()
        end = code_matches[idx + 1].start() if idx + 1 < len(code_matches) else len(text)
        section = text[start:end]
        markers = _base_markers(section)
        allocations: List[Dict[str, Any]] = []
        for i, mk in enumerate(markers):
            tail_end = markers[i + 1]["start"] if i + 1 < len(markers) else len(section)
            qty = _find_qty(section[mk["end"]:tail_end])
            allocations.append({"base": mk["base"], "wareCode": mk["wareCode"], "qty": qty})
        # 该顶码没有基地 → 用一个占位，后续会报错提示补基地
        items.append({"code": code, "allocations": allocations})
    return {"month": month, "items": items}


def _normalize_items(items) -> List[Dict[str, Any]]:
    """把入参 items 归一成 [{"code":..., "allocations":[{"base":..., "warCode":..., "qty":...}]}]。

    兼容三种写法：
      {"code": "X", "allocations": [{"base": "珠海基地", "qty": 10}, ...]}
      {"code": "X", "bases": {"珠海基地": 10, "洛阳": 20}}
      {"code": "X", "base": "珠海基地", "qty": 10}
    """
    out: List[Dict[str, Any]] = []
    if not items:
        return out
    if isinstance(items, dict):
        items = [items]
    for raw in items:
        if not isinstance(raw, dict):
            continue
        code = raw.get("code") or raw.get("topCode") or raw.get("commodityCode")
        if not code:
            continue
        allocations: List[Dict[str, Any]] = []
        if raw.get("allocations"):
            for a in raw["allocations"]:
                if not isinstance(a, dict):
                    continue
                base = a.get("base") or a.get("wareCode") or a.get("name")
                qty = a.get("qty", a.get("quantity"))
                allocations.append({"base": base, "qty": qty})
        elif isinstance(raw.get("bases"), dict):
            for base, qty in raw["bases"].items():
                allocations.append({"base": base, "qty": qty})
        elif raw.get("base") or raw.get("wareCode"):
            allocations.append({"base": raw.get("base") or raw.get("wareCode"),
                                "qty": raw.get("qty", raw.get("quantity"))})
        out.append({"code": str(code).strip(), "allocations": allocations})
    return out


def _supported_bases(cfg: Dict[str, Any]) -> List[str]:
    """本流程可填写的基地编码：配置 api_config.plan_month.bases > 内置 5 个。"""
    api_cfg = (cfg or {}).get("api_config", {}) or {}
    raw = ((api_cfg.get("plan_month", {}) or {}).get("bases")) or plan_month_flow.PLAN_BASES
    codes: List[str] = []
    for item in raw:
        code = resolve_base_code(item)
        if code and code not in codes:
            codes.append(code)
    return codes or list(plan_month_flow.PLAN_BASES)


def _base_label(code: str) -> str:
    for name, c in HCH_BASE_ALIASES.items():
        if c == code:
            return f"{name}({code})"
    return code


def _build_request_items(parsed_items, details, supported):
    """把「顶码 + 基地数量」换成引擎要的 [{detailId, quantities}]。

    返回 (items, error)。
    """
    index: Dict[str, Any] = {}
    for row in details:
        code = str(row.get("commodityCode") or "").strip()
        if code:
            index[code.upper()] = row

    items: List[Dict[str, Any]] = []
    for raw in parsed_items:
        code = str(raw.get("code") or "").strip()
        row = index.get(code.upper())
        if row is None:
            available = ", ".join(sorted(index.keys())) or "（无）"
            return None, (f"该月预测单里没有物料顶码 {code}。可用顶码：{available}")
        quantities: Dict[str, int] = {}
        for alloc in raw.get("allocations") or []:
            base = alloc.get("base")
            qty = alloc.get("qty")
            if base is None or qty is None:
                return None, f"物料 {code} 的基地数量没写全（得到 base={base} qty={qty}）"
            ware_code = resolve_base_code(base)
            if not ware_code:
                return None, f"无法识别的基地：{base}"
            if ware_code not in supported:
                return None, (f"基地 {base} 不在本流程可填写的基地范围内："
                              + "、".join(_base_label(c) for c in supported))
            quantities[ware_code] = int(qty)
        if not quantities:
            return None, f"物料 {code} 没有填写任何基地数量"
        items.append({"detailId": row.get("id"), "commodityCode": code, "quantities": quantities})
    return items, None


def _build_summary(flow_summary: Dict[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    if flow_summary.get("month"):
        summary["计划月份"] = flow_summary["month"]
    if flow_summary.get("forecastOrderCode"):
        summary["预测单号"] = flow_summary["forecastOrderCode"]
    if flow_summary.get("planCode"):
        summary["销售计划单"] = flow_summary["planCode"]
    if flow_summary.get("sourceOrderNo"):
        summary["来源单号"] = flow_summary["sourceOrderNo"]
    if flow_summary.get("salePlanNo"):
        summary["销售计划号"] = flow_summary["salePlanNo"]
    if flow_summary.get("auditOrderNo"):
        summary["审批单号"] = flow_summary["auditOrderNo"]
    pushed = [r.get("salePlanNo") for r in (flow_summary.get("pushed") or []) if r.get("salePlanNo")]
    if pushed:
        summary["已推送采购"] = ", ".join(pushed)
    return summary


# ---- 分段执行（stages）----
# all=全流程(1~7) | plan=建单+生成销售计划单(1~2) | approve=销售计划审批+取月需求计划(3~4)
# hch=HCH 后续(5~7) | push=只推送采购(7)
_STAGE_ALIASES = {
    "all": "all", "全部": "all", "全流程": "all", "全链路": "all", "一键": "all", "完整流程": "all",
    "plan": "plan", "建单": "plan", "生成计划单": "plan", "生成销售计划单": "plan", "生成计划": "plan",
    "approve": "approve", "审批": "approve", "销售计划审批": "approve", "补审批": "approve",
    "hch": "hch", "后续": "hch", "后续流程": "hch", "补跑hch": "hch", "补跑": "hch", "转生产计划": "hch",
    "push": "push", "推送采购": "push", "推送": "push", "只推送": "push",
}
_STAGE_KEYWORDS = [
    (("推送采购", "只推送"), "push"),
    (("全流程", "全链路", "一键", "完整"), "all"),
    (("建单", "生成计划单", "生成销售计划单"), "plan"),
    (("后续", "hch", "补跑", "来源单号"), "hch"),
    (("推送",), "hch"),
    (("审批",), "approve"),
]

# 计划单号：JHY…=月需求计划单号(即 HCH 来源单号)，SP…=销售计划单号，YC…=预测单号，YX…=销售计划号
_PLAN_NO_RE = re.compile(r"(?:JHY|SP|YC|YX)\s*[A-Za-z0-9]{6,}")


def _normalize_stage(stages: Optional[str]) -> Optional[str]:
    """把 stage 规范为 all|plan|approve|hch|push（支持中文短语）。"""
    key = str(stages or "").strip()
    low = key.lower()
    if key in _STAGE_ALIASES:
        return _STAGE_ALIASES[key]
    if low in _STAGE_ALIASES:
        return _STAGE_ALIASES[low]
    for words, stage in _STAGE_KEYWORDS:
        if any(w in key or w in low for w in words):
            return stage
    return None


def _classify_plan_no(value):
    """识别单号类型 → ("source"|"plan"|None, 单号)。JHY…=来源单号，SP…=销售计划单号。"""
    text = re.sub(r"\s+", "", str(value or "")).upper()
    if not text:
        return None, None
    if text.startswith("JHY"):
        return "source", text
    if text.startswith("SP"):
        return "plan", text
    return None, text


def _extract_plan_no(text: str) -> Optional[str]:
    """从整句话里抽计划单号（JHY… / SP… / YC… / YX…）。"""
    m = _PLAN_NO_RE.search(str(text or ""))
    return m.group(0).replace(" ", "") if m else None


def execute_plan_month(
    month: Optional[str] = None,
    items: Union[List[Dict[str, Any]], Dict[str, Any], None] = None,
    instruction: Optional[str] = None,
    manager_token: Optional[str] = None,
    hch_token: Optional[str] = None,
    bases: Union[str, List[str], None] = None,
    environment: str = "qa",
    config_path: Optional[str] = None,
    stages: Optional[str] = None,
    plan_no: Optional[str] = None,
    plan_code: Optional[str] = None,
    source_order_no: Optional[str] = None,
) -> Dict[str, Any]:
    """商用月计划主入口：支持一站到底，也支持**按单号分段续跑**。

    阶段（`stages`，不传时自动判断）：
      all      1~7 全流程（默认；需 month + items）
      plan     1~2 建单 + 生成销售计划单（需 month + items）
      approve  3~4 销售计划审批 + 取月需求计划单号（需 plan_code，如 SP…）
      hch      5~7 HCH 提交销售审批 → 商用审批 → 推送采购（需 source_order_no，如 JHY…）
      push     7   只推送采购（需 source_order_no）

    不给 `stages` 时的自动判断：给了 `source_order_no`（JHY…）→ `hch`；给了 `plan_code`（SP…）→ `approve`；否则 `all`。
    单号既可用 `plan_no`（按前缀自动识别）传入，也可写进 `instruction` 整句里。

    Args:
        month: 计划月份 "YYYY-MM"（可从 instruction 里抽取）
        items: [{"code": "顶码", "allocations": [{"base": "珠海基地", "qty": 10}, ...]}]
               （也可用 bases/{base,qty} 简写；可从 instruction 里抽取）
        instruction: 自然语言整句，自动抽月份/顶码/各基地数量/计划单号，如
                     "帮我生成2026年9月的商用月计划，其中KN850W5140 珠海基地10、洛阳基地20；KM50001700 长沙基地2"
                     "计划单号JHY20260914001 继续推送采购"
        manager_token: 商用管理端 Token；缺省从本地配置 api_config.plan_month.tokens.manager 读取
        hch_token: HCH 系统 Token；缺省从本地配置 api_config.plan_month.tokens.hch 读取
        bases: 覆盖可填写基地（缺省读配置 / 内置 5 个：合肥/洛阳/南京/长沙/珠海）
        environment: "qa"（默认）/ "uat"（仅影响 HCH 基址）
        config_path: 自定义配置文件路径
        stages: all | plan | approve | hch | push（也接受中文，如"只推送采购"）
        plan_no: 计划单号（JHY… 或 SP…，按前缀自动识别后进入对应阶段）
        plan_code: 销售计划单号（SP…），stages=approve 用
        source_order_no: HCH 来源单号（JHY…），stages=hch/push 用

    Returns:
        结构化结果字典（success / message / data / summary / timestamp）
    """
    if instruction:
        parsed = parse_plan_instruction(instruction)
        if not month and parsed.get("month"):
            month = parsed["month"]
        if not items and parsed.get("items"):
            items = parsed["items"]
        if not plan_no and not plan_code and not source_order_no:
            plan_no = _extract_plan_no(instruction)

    # 单号归一 / 识别
    if plan_no:
        kind, no = _classify_plan_no(plan_no)
        if kind == "source":
            source_order_no = source_order_no or no
        elif kind == "plan":
            plan_code = plan_code or no
        else:
            return _err(f"无法识别的计划单号：{plan_no}"
                        f"（支持 JHY… 来源单号 / SP… 销售计划单号）")
    plan_code = str(plan_code).strip().upper() if plan_code else None
    source_order_no = str(source_order_no).strip().upper() if source_order_no else None

    # 阶段：显式 > 按单号自动判断 > all
    explicit_stage = bool(stages and str(stages).strip())
    stage = _normalize_stage(stages) if explicit_stage else None
    if explicit_stage and stage is None:
        return _err(f"未知的执行阶段: {stages}（可选 all/plan/approve/hch/push）")
    if stage is None:
        if source_order_no:
            stage = "hch"
        elif plan_code:
            stage = "approve"
        else:
            stage = "all"

    # 分阶段校验入参
    req_codes: List[Dict[str, Any]] = []
    if stage in ("all", "plan"):
        month = _normalize_month(month)
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            return _err(f"计划月份格式应为 YYYY-MM（得到 {month or '空'}）")
        req_codes = _normalize_items(items)
        if not req_codes:
            return _err("请给出至少一个物料顶码及其基地数量（可分到多个基地）")
    else:
        month = _normalize_month(month) if month else ""
        if month and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            month = ""
    if stage == "approve" and not plan_code:
        return _err("缺少销售计划单号（stages=approve 需提供 SP… 单号或 plan_no）")
    if stage in ("hch", "push") and not source_order_no:
        return _err("缺少来源单号（stages=hch/push 需提供 JHY… 单号或 plan_no）")

    cfg = _load_config(config_path)
    env = _apply_environment(environment, cfg)
    m_token, h_token = _resolve_plan_tokens(cfg, manager_token, hch_token)
    if stage in ("all", "plan", "approve", "hch") and not m_token:
        return _err("缺少商用管理端 Token（请在 automation_config.json 的 "
                    "api_config.plan_month.tokens.manager 配置或显式传入）")
    if stage in ("all", "hch", "push") and not h_token:
        return _err("缺少 HCH 系统 Token（请在 automation_config.json 的 "
                    "api_config.plan_month.tokens.hch 配置或显式传入）")

    supported = _supported_bases(cfg)
    if bases is not None:
        override: List[str] = []
        for item in ([bases] if isinstance(bases, str) else list(bases)):
            code = resolve_base_code(item)
            if code and code not in override:
                override.append(code)
        if override:
            supported = override

    flow_items: List[Dict[str, Any]] = []
    flow_summary: Dict[str, Any] = {"stage": stage}
    try:
        print(f"\n{'=' * 60}")
        print(f"商用月计划 | 阶段={stage} | 月份={month or '-'} | 环境={env}")
        print(f"{'=' * 60}")

        manager_api = OrderMachineApi(m_token) if m_token else None
        hch_api = HchApi(h_token) if h_token else None

        # ---- 步骤 1~2：建单 + 生成销售计划单 ----
        if stage in ("all", "plan"):
            forecast_order = plan_month_flow.run_create_phase(manager_api, month)
            details = plan_month_flow.fetch_forecast_details(manager_api, forecast_order.get("id"))
            if not details:
                return _err(f"{month} 的预测单里没有任何物料明细")
            flow_items, item_err = _build_request_items(req_codes, details, supported)
            if item_err:
                return _err(item_err)
            plan = plan_month_flow.run_generate_phase(manager_api, forecast_order, flow_items,
                                                      bases=supported)
            plan_code = plan.get("planCode")
            flow_summary.update({"month": month,
                                 "forecastOrderId": forecast_order.get("id"),
                                 "forecastOrderCode": forecast_order.get("orderCode"),
                                 "planId": plan.get("planId"),
                                 "planCode": plan_code})

        # ---- 步骤 3~4：销售计划审批 + 月需求计划单号 ----
        if stage in ("all", "approve"):
            plan_month_flow.FLOW_START_MS = plan_month_flow.now_ms()
            plan_order = plan_month_flow.run_plan_approval_phase(manager_api, {"planCode": plan_code})
            demand_plan = plan_month_flow.run_demand_plan_phase(manager_api, month or None)
            source_order_no = (demand_plan or {}).get("demandPlanCode") or source_order_no
            flow_summary.update({"planOrderId": plan_order.get("id"),
                                 "sourceOrderNo": source_order_no})

        # ---- 步骤 5~6：HCH 提交销售审批 + 等审批中 + 商用审批 ----
        if stage in ("all", "hch"):
            draft_row = plan_month_flow.run_hch_submit_phase(hch_api, source_order_no)
            audit_order = plan_month_flow.wait_hch_audit_status(hch_api, source_order_no)
            plan_month_flow.approve_demand_plan_adjust(manager_api, source_order_no)
            flow_summary.update({
                "sourceOrderNo": source_order_no,
                "salePlanNo": draft_row.get("salePlanNo"),
                "auditOrderNo": audit_order.get("auditOrderNo") if audit_order else None})

        # ---- 步骤 7：推送到采购 ----
        if stage in ("all", "hch", "push"):
            flow_summary["pushed"] = plan_month_flow.run_hch_push_phase(hch_api, source_order_no)
    except plan_month_flow.SnapshotNotSyncedError as e:
        return _err(str(e), need_sync=True, stage=stage)
    except Exception as e:
        return _err(f"执行异常: {e}", error_details=str(e))

    return {
        "success": True,
        "message": f"商用月计划执行成功（阶段={stage}" + (f"，月份={month}" if month else "") + "）",
        "data": {
            "stage": stage,
            "month": month or None,
            "environment": env,
            "supportedBases": supported,
            "planCode": plan_code,
            "sourceOrderNo": source_order_no,
            "forecastOrderId": flow_summary.get("forecastOrderId"),
            "items": flow_items,
            "flow": flow_summary,
        },
        "summary": _build_summary(flow_summary),
        "timestamp": _now(),
    }


def continue_plan_month(
    plan_no: str,
    manager_token: Optional[str] = None,
    hch_token: Optional[str] = None,
    month: Optional[str] = None,
    environment: str = "qa",
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """按单号续跑：给 JHY…（来源单号）→ 跑 HCH 后续（5~7）；给 SP…（销售计划单号）→ 跑审批（3~4）。"""
    return execute_plan_month(plan_no=plan_no, month=month, manager_token=manager_token,
                              hch_token=hch_token, environment=environment,
                              config_path=config_path)


def list_plan_materials(month: Optional[str] = None,
                        manager_token: Optional[str] = None,
                        environment: str = "qa",
                        config_path: Optional[str] = None) -> Dict[str, Any]:
    """列出某月预测单里的物料顶码（供智能体在缺顶码时向用户展示选项）。"""
    month = _normalize_month(month)
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        return _err(f"计划月份格式应为 YYYY-MM（得到 {month or '空'}）")

    cfg = _load_config(config_path)
    env = _apply_environment(environment, cfg)
    m_token, _ = _resolve_plan_tokens(cfg, manager_token, None)
    if not m_token:
        return _err("缺少商用管理端 Token（请在 automation_config.json 的 "
                    "api_config.plan_month.tokens.manager 配置或显式传入）")

    supported = _supported_bases(cfg)
    try:
        manager_api = OrderMachineApi(m_token)
        forecast_order = plan_month_flow.resolve_forecast_order(manager_api, month)
        details = plan_month_flow.fetch_forecast_details(manager_api, forecast_order.get("id"))
    except Exception as e:
        return _err(f"查询 {month} 预测单物料失败: {e}")

    options = []
    for row in details:
        code = str(row.get("commodityCode") or "").strip()
        if not code:
            continue
        base_map = {}
        for d in (row.get("baseDetails") or []):
            if isinstance(d, dict) and d.get("baseCode"):
                base_map[d["baseCode"]] = d.get("forecastBaseQty") or 0
        options.append({
            "code": code,
            "bases": {c: base_map.get(c, 0) for c in supported},
        })

    return {
        "success": True,
        "message": f"{month} 预测单共 {len(options)} 个物料顶码",
        "data": {"month": month, "environment": env, "supportedBases": supported,
                 "forecastOrderCode": forecast_order.get("orderCode"), "options": options},
        "summary": {"计划月份": month,
                    "可填写基地": "、".join(_base_label(c) for c in supported),
                    "物料顶码": ", ".join(o["code"] for o in options)},
        "timestamp": _now(),
    }


def get_skill_info() -> Dict[str, Any]:
    """技能元数据（供智能体查询）。"""
    return {
        "name": "hch_plan_month",
        "version": "1.0.0",
        "description": "商用月计划自动化：创建/覆盖预测单 → 生成销售计划单 → 销售计划审批 → 月需求计划 → HCH 提交销售审批 → 商用审批 → HCH 推送采购",
        "functions": [
            {
                "name": "execute_plan_month",
                "description": "执行商用月计划（可全流程，也可按计划单号分段续跑）",
                "parameters": {
                    "month": {"type": "string", "required": False, "description": "计划月份 YYYY-MM（可从 instruction 抽）"},
                    "items": {"type": "array", "required": False,
                              "description": "各物料顶码及其基地数量，如 [{\"code\":\"KN850W5140\",\"allocations\":[{\"base\":\"珠海基地\",\"qty\":10}]}]"},
                    "instruction": {"type": "string", "required": False,
                                    "description": "自然语言整句，自动抽月份/顶码/基地数量/计划单号"},
                    "manager_token": {"type": "string", "required": False, "description": "商用管理端 Token（缺省读配置）"},
                    "hch_token": {"type": "string", "required": False, "description": "HCH 系统 Token（缺省读配置）"},
                    "environment": {"type": "string", "required": False, "default": "qa"},
                    "stages": {"type": "string", "required": False,
                               "enum": ["all", "plan", "approve", "hch", "push"],
                               "description": "all=全流程 | plan=建单+生成销售计划单 | approve=销售计划审批 | hch=HCH 后续(5~7) | push=只推送采购；不传则按单号自动判断"},
                    "plan_no": {"type": "string", "required": False,
                                "description": "计划单号：JHY…（来源单号→跑 HCH 后续）或 SP…（销售计划单号→跑审批）"},
                    "plan_code": {"type": "string", "required": False, "description": "销售计划单号 SP…（stages=approve）"},
                    "source_order_no": {"type": "string", "required": False, "description": "HCH 来源单号 JHY…（stages=hch/push）"},
                },
            },
            {
                "name": "continue_plan_month",
                "description": "按计划单号续跑：JHY… → HCH 后续（5~7）；SP… → 销售计划审批（3~4）",
                "parameters": {"plan_no": {"type": "string", "required": True},
                               "month": {"type": "string", "required": False}},
            },
            {
                "name": "list_plan_materials",
                "description": "列出某月预测单里的物料顶码",
                "parameters": {"month": {"type": "string", "required": True}},
            },
        ],
        "examples": [
            {"description": "一句话生成商用月计划",
             "code": ('execute_plan_month(instruction="帮我生成2026年9月的商用月计划，'
                      '其中KN850W5140 珠海基地10、洛阳基地20；KM50001700 长沙基地2")')},
            {"description": "结构化入参",
             "code": ('execute_plan_month(month="2026-09", items=[{"code": "KN850W5140", '
                      '"allocations": [{"base": "珠海基地", "qty": 10}]}])')},
            {"description": "只建单生成销售计划单", "code": 'execute_plan_month(month="2026-09", items=[...], stages="plan")'},
            {"description": "给来源单号跑 HCH 后续（提交审批→商用审批→推送采购）",
             "code": 'execute_plan_month(source_order_no="JHY20260914001", stages="hch")'},
            {"description": "按单号自动续跑", "code": 'continue_plan_month(plan_no="JHY20260914001")'},
            {"description": "给销售计划单号补审批", "code": 'execute_plan_month(plan_code="SP2026090001", stages="approve")'},
            {"description": "查看某月预测单物料", "code": 'list_plan_materials(month="2026-09")'},
        ],
    }


if __name__ == "__main__":
    print(json.dumps(get_skill_info(), ensure_ascii=False, indent=2))

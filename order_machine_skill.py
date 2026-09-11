#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
商用订单机 - 对话式技能入口（供智能体 / QClaw / Lingma 调用）

本模块把已验证的 order_machine.py（10 步全链路）包装成"自然语言可调用"的技能入口：
  - 统一从 automation_config.json 读取三个 Token（客户端 / 管理端 / HCH）
  - 支持分段执行：all | submit | approve | schedule | hch
  - 支持按订单号补跑某一段（approve / schedule / hch）
  - 返回结构化结果（success / message / data / summary / timestamp）

与 hch_skill.py 对 api_automation.py 的关系一致：本文件只做编排，不做业务。

典型调用：
    from order_machine_skill import execute_order_machine, list_material_top_codes

    # 1) 看看有哪些物料可以下单
    list_material_top_codes()

    # 2) 一键全链路（Token 取自本地配置）
    execute_order_machine(top_codes=["KM500N1720", "MC20700060"])

    # 3) 只下单不审批
    execute_order_machine(top_codes=["KM500N1720"], stages="submit")

    # 4) 已有订单号，补跑 HCH 订单机流程
    execute_order_machine(order_no="102609102700004", stages="hch")
"""

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import requests

import order_machine
from order_machine import (
    COMMODITY_GROUP_ID,
    MANAGER_APPROVE_BUSINESS_DATA_JSON,
    HchApi,
    OrderMachineApi,
    run_approval_phase,
    run_hch_phase,
    submit_order,
    submit_production_schedule,
)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "automation_config.json")
COMMODITY_TOP_CODES_API = ("https://qasalescloud.gree.com/gateway/"
                           "gree-cos-commodity/admin-api/v1/regularPurchase/list")

# stages 别名（自然语言 → 规范值）
_STAGE_ALIASES = {
    "all": "all", "全部": "all", "全流程": "all", "全链路": "all", "一键": "all",
    "submit": "submit", "下单": "submit", "只下单": "submit", "仅下单": "submit",
    "approve": "approve", "审批": "approve", "只审批": "approve", "补审批": "approve",
    "schedule": "schedule", "排产": "schedule", "提交排产": "schedule", "补排产": "schedule",
    "hch": "hch", "订单机": "hch", "hch流程": "hch", "补hch": "hch",
    "转生产计划": "hch", "推送": "hch",
}

# 自然语言短语 → stage 的兜底关键词（按顺序匹配，先命中先返回）
_STAGE_KEYWORDS = [
    (("全流程", "全链路", "全跑", "一键", "整个流程", "完整流程"), "all"),
    (("只下单", "仅下单", "只提交", "仅提交"), "submit"),
    (("hch", "订单机", "转生产计划", "推送"), "hch"),
    (("排产",), "schedule"),
    (("审批",), "approve"),
    (("下单",), "submit"),
]


def _split_codes(value) -> List[str]:
    """把顶码统一成列表。

    支持：list/tuple，或字符串，如 "MC20700060、LJ71147520"、
    "物料顶码MC20700060,LJ71147520"、"MC20700060 LJ71147520"。
    对含中文前缀的片段（如 "物料顶码MC20700060"）取其中的字母数字编码。
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value]
    else:
        items = re.split(r"[,，、;；\s|+]+", str(value))
    out: List[str] = []
    for it in items:
        it = it.strip()
        if not it:
            continue
        found = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-_.]*", it)
        if found:
            code = found[-1]
            if code not in out:
                out.append(code)
    return out


def _split_ints(value) -> Optional[List[int]]:
    """数量：list 或 "20,5" 之类的字符串 → 整数列表。"""
    if value is None or value == "":
        return None
    if isinstance(value, (list, tuple)):
        return [int(v) for v in value]
    parts = [p for p in re.split(r"[,，、;；\s|]+", str(value)) if p.strip()]
    return [int(p) for p in parts]


def _now() -> str:
    return datetime.now().isoformat()


def _err(message: str, **extra) -> Dict[str, Any]:
    result = {"success": False, "message": message, "timestamp": _now()}
    result.update(extra)
    return result


def _load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """读取 automation_config.json（缺省与技能同目录）。"""
    path = config_path or CONFIG_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _clean_token(value) -> Optional[str]:
    """清洗 Token：去首尾空白，并兼容误带 "Bearer " 前缀（或整行 Authorization）的粘贴。"""
    text = "" if value is None else str(value).strip().strip('"').strip()
    if not text:
        return None
    low = text.lower()
    if "bearer" in low:
        text = text[low.rindex("bearer") + 6:].strip()
    return text or None


def _resolve_tokens(cfg: Dict[str, Any], client_token, manager_token, hch_token):
    """Token 优先级：显式入参 > 本地配置。"""
    api_cfg = (cfg or {}).get("api_config", {}) or {}
    order_cfg = api_cfg.get("order_machine", {}) or {}
    tokens = order_cfg.get("tokens", {}) or {}

    def pick(explicit, key):
        if explicit and str(explicit).strip():
            return _clean_token(explicit)
        return _clean_token(tokens.get(key))

    return pick(client_token, "client"), pick(manager_token, "manager"), pick(hch_token, "hch")


def _apply_environment(environment: Optional[str], cfg: Dict[str, Any]):
    """按环境切换 HCH 基址（qa=9002 / uat=9108）。返回规范化后的环境名。"""
    env = (environment or "qa").strip().lower()
    if env in ("", "qa"):
        return "qa"
    api_cfg = (cfg or {}).get("api_config", {}) or {}
    base = ((api_cfg.get("environments", {}) or {}).get(env, {}) or {}).get("base_url")
    if base:
        order_machine.HCH_BASE = base.rstrip("/")
        order_machine.HCH_REFERER_ORDER_MACHINE = order_machine.HCH_BASE + "/hch/salesPlan/orderMachineManage"
        order_machine.HCH_REFERER_MONTH_PLAN = order_machine.HCH_BASE + "/hch/productionPlan/productionMonthPlan"
    return env


def _normalize_stage(stages: Optional[str]) -> Optional[str]:
    """把 stage 规范为 all|submit|approve|schedule|hch。

    先精确匹配别名，再按中文关键词兜底扫描，支持
    "提交订单全流程"、"只下单不审批"、"补跑 HCH" 这类自然语言短语。
    """
    key = (stages or "all").strip()
    low = key.lower()
    if key in _STAGE_ALIASES:
        return _STAGE_ALIASES[key]
    if low in _STAGE_ALIASES:
        return _STAGE_ALIASES[low]
    for words, stage in _STAGE_KEYWORDS:
        if any(w in key or w in low for w in words):
            return stage
    return None


def _build_summary(stage: str, sink: Dict[str, Any]) -> Dict[str, Any]:
    """把 result_sink 汇总成人类可读的摘要（智能体可直接回给用户）。"""
    summary: Dict[str, Any] = {}
    orders = sink.get("orders") or []
    if orders:
        summary["订单号"] = ", ".join(str(o.get("code")) for o in orders if o.get("code"))
    if stage == "all":
        summary.update({"提交订单": "成功", "客户端审批": "通过", "管理端审批": "通过",
                        "提交排产": "成功"})
    elif stage == "submit":
        summary["提交订单"] = "成功"
    elif stage == "approve":
        summary["审批"] = "完成"
    elif stage == "schedule":
        summary["提交排产"] = "成功"

    hch = sink.get("hch") or {}
    if hch:
        rows = sum(int(v.get("rows") or 0) for v in hch.values())
        wares = [w for v in hch.values() for w in (v.get("wareCodes") or [])]
        sale_nos = [n for v in hch.values() for n in (v.get("salePlanNos") or [])]
        summary["HCH 明细行数"] = rows
        if wares:
            summary["分配基地"] = ", ".join(wares)
        if sale_nos:
            summary["已推送销售计划号"] = ", ".join(sale_nos)
    return summary


def execute_order_machine(
    client_token: Optional[str] = None,
    top_codes: Union[str, List[str], None] = None,
    manager_token: Optional[str] = None,
    hch_token: Optional[str] = None,
    quantities: Union[str, List[int], None] = None,
    environment: str = "qa",
    stages: str = "all",
    order_no: Optional[str] = None,
    project_code: Optional[str] = None,
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """商用订单机主入口。

    Args:
        client_token: 客户端 Token（商品中心）；缺省从本地配置读取
        top_codes: 物料顶码，列表或字符串（如 "MC20700060、LJ71147520"）；stages=all/submit 时必填
        manager_token: 管理端 Token；缺省从本地配置读取，为空则跳过管理端审批与排产
        hch_token: HCH 系统 Token；缺省从本地配置读取，为空则跳过 HCH 段
        quantities: 与 top_codes 一一对应的数量；缺省取物料列表项的 num
        environment: 目标环境 "qa"（默认）/ "uat"（仅影响 HCH 基址）
        stages: all=全链路(默认) | submit=仅下单 | approve=仅审批 | schedule=仅排产 | hch=仅 HCH
        order_no: 已有订单号（stages=approve/schedule/hch 时必填）
        project_code: 项目编号；缺省 XSF0009-26-432
        config_path: 自定义配置文件路径

    Returns:
        结构化结果字典（success / message / data / summary / timestamp）
    """
    cfg = _load_config(config_path)
    env = _apply_environment(environment, cfg)
    c_token, m_token, h_token = _resolve_tokens(cfg, client_token, manager_token, hch_token)
    stage = _normalize_stage(stages)
    codes = _split_codes(top_codes)
    quantities = _split_ints(quantities)
    order_no = str(order_no).strip() if order_no else None
    sink: Dict[str, Any] = {}

    if stage is None:
        return _err(f"未知的执行阶段: {stages}（可选 all/submit/approve/schedule/hch）")

    try:
        print(f"\n{'=' * 60}")
        print(f"商用订单机 | 阶段={stage} | 环境={env}")
        print(f"{'=' * 60}")

        if stage in ("all", "submit"):
            if not c_token:
                return _err("缺少客户端 Token（请在 automation_config.json 配置或显式传入）")
            if not codes:
                return _err("缺少物料顶码。可先调用 list_material_top_codes() 查看可选项")
            rc = submit_order(
                c_token, codes,
                manager_token=(m_token if stage == "all" else None),
                hch_token=(h_token if stage == "all" else None),
                project_code=project_code,
                stop_after=("submit" if stage == "submit" else None),
                result_sink=sink,
                quantities=quantities,
            )
        elif stage == "approve":
            if not order_no:
                return _err("缺少订单号（stages=approve 需提供 order_no）")
            if not c_token:
                return _err("缺少客户端 Token")
            orders = [{"code": order_no, "id": None}]
            failed = run_approval_phase(OrderMachineApi(c_token), orders, "客户端审批")
            if not failed and m_token:
                failed = run_approval_phase(
                    OrderMachineApi(m_token), orders, "管理端审批",
                    include_task_key=True,
                    business_data_json=MANAGER_APPROVE_BUSINESS_DATA_JSON)
            sink["orders"] = orders
            rc = 0 if not failed else 1
        elif stage == "schedule":
            if not order_no:
                return _err("缺少订单号（stages=schedule 需提供 order_no）")
            if not m_token:
                return _err("缺少管理端 Token")
            rc = 0 if submit_production_schedule(OrderMachineApi(m_token), order_no) else 1
            sink["orders"] = [{"code": order_no}]
        else:  # stage == "hch"
            if not order_no:
                return _err("缺少订单号（stages=hch 需提供 order_no）")
            if not h_token:
                return _err("缺少 HCH 系统 Token")
            rc = 0 if run_hch_phase(HchApi(h_token), order_no, result_sink=sink) else 1
            sink["orders"] = [{"code": order_no}]
    except Exception as e:
        return _err(f"执行异常: {e}", error_details=str(e))

    success = (rc == 0)
    return {
        "success": success,
        "message": "商用订单机执行成功" if success else "商用订单机执行失败",
        "data": {
            "stage": stage,
            "environment": env,
            "orders": sink.get("orders") or [],
            "hch": sink.get("hch") or {},
        },
        "summary": _build_summary(stage, sink),
        "timestamp": _now(),
    }


def run_hch_by_order_no(order_no: str, hch_token: Optional[str] = None,
                        environment: str = "qa",
                        config_path: Optional[str] = None) -> Dict[str, Any]:
    """按订单号单独补跑 HCH 订单机流程（等价于 execute_order_machine(stages="hch")）。"""
    return execute_order_machine(order_no=order_no, stages="hch", hch_token=hch_token,
                                 environment=environment, config_path=config_path)


def list_material_top_codes(client_token: Optional[str] = None,
                            environment: str = "qa",
                            config_path: Optional[str] = None) -> Dict[str, Any]:
    """列出可下单的物料顶码（供智能体在缺顶码时向用户展示选项）。"""
    cfg = _load_config(config_path)
    _apply_environment(environment, cfg)
    c_token, _, _ = _resolve_tokens(cfg, client_token, None, None)
    if not c_token:
        return _err("缺少客户端 Token（请在 automation_config.json 配置或显式传入）")

    try:
        resp = requests.post(
            COMMODITY_TOP_CODES_API,
            json={"saleType": "1"},
            headers={
                "accept": "application/json, text/plain, */*",
                "accept-language": "zh-CN,zh;q=0.9",
                "content-type": "application/json",
                "authorization": f"Bearer {c_token}",
                "origin": "https://qasalescloud.gree.com",
                "referer": "https://qasalescloud.gree.com/purchase-list",
                "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"),
            },
            timeout=30, verify=False,
        )
        if resp.status_code != 200:
            return _err(f"物料接口请求失败: HTTP {resp.status_code} - {(resp.text or '')[:200]}")
        payload = resp.json()
        group = None
        for g in ((payload.get("data") or {}).get("groupList") or []):
            if str(g.get("id")) == str(COMMODITY_GROUP_ID):
                group = g
                break
        if group is None:
            return _err(f"未找到物料分组 id={COMMODITY_GROUP_ID}")
        options = [{"code": c.get("code"), "model": c.get("model"), "title": c.get("title")}
                   for c in (group.get("commodityList") or []) if c.get("code")]
        return {
            "success": True,
            "message": f"共 {len(options)} 个可下单物料顶码",
            "data": {"groupName": group.get("name"), "options": options},
            "summary": {"可选顶码": ", ".join(o["code"] for o in options)},
            "timestamp": _now(),
        }
    except Exception as e:
        return _err(f"获取物料顶码失败: {e}")


def get_skill_info() -> Dict[str, Any]:
    """技能元数据（供智能体查询）。"""
    return {
        "name": "hch_order_machine",
        "version": "1.0.0",
        "description": "商用订单机自动化：提交订单 → 客户端审批 → 管理端审批 → 提交排产 → HCH 订单机处理（调整/分配基地/转生产计划/推送销售计划号）",
        "functions": [
            {
                "name": "execute_order_machine",
                "description": "执行商用订单机流程（可全链路或分段）",
                "parameters": {
                    "client_token": {"type": "string", "required": False, "description": "客户端 Token（缺省读配置）"},
                    "top_codes": {"type": "array", "required": False, "description": "物料顶码列表"},
                    "manager_token": {"type": "string", "required": False, "description": "管理端 Token"},
                    "hch_token": {"type": "string", "required": False, "description": "HCH 系统 Token"},
                    "quantities": {"type": "array", "required": False, "description": "各物料数量"},
                    "environment": {"type": "string", "required": False, "default": "qa"},
                    "stages": {"type": "string", "required": False, "default": "all",
                               "enum": ["all", "submit", "approve", "schedule", "hch"]},
                    "order_no": {"type": "string", "required": False, "description": "已有订单号（分段执行用）"},
                },
            },
            {
                "name": "list_material_top_codes",
                "description": "列出可下单的物料顶码",
                "parameters": {"client_token": {"type": "string", "required": False}},
            },
            {
                "name": "run_hch_by_order_no",
                "description": "按订单号单独补跑 HCH 订单机流程",
                "parameters": {"order_no": {"type": "string", "required": True}},
            },
        ],
        "examples": [
            {"description": "一键全链路下单", "code": 'execute_order_machine(top_codes=["KM500N1720"])'},
            {"description": "只下单不审批", "code": 'execute_order_machine(top_codes=["KM500N1720"], stages="submit")'},
            {"description": "按订单号补跑 HCH", "code": 'execute_order_machine(order_no="102609102700004", stages="hch")'},
            {"description": "查看可下单物料", "code": "list_material_top_codes()"},
        ],
    }


if __name__ == "__main__":
    print(json.dumps(get_skill_info(), ensure_ascii=False, indent=2))

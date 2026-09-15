"""
商用月计划 - 生成销售计划单 + 销售计划审批 + 月需求计划 + HCH 提交销售审批 + 商用审批 + HCH 推送采购
（独立引擎模块，与商用订单机 order_machine.py 分离，仅复用其 HTTP 客户端）

流程（对应抓包 qasalescloud 商用月计划 + ds-oms HCH 链路）：
  步骤1 创建/覆盖预测单
        GET  salesForecastForecastOrder/getCreateInitData        → deptId / productLineId
        POST salesForecastForecastOrder/create                   → 若返回 existForecastOrderId 则带 id 再调一次（覆盖）
        报 G6001「当日中台快照数据不存在」→ getSyncInfo 读同步状态 + startSync 触发同步 → 中止并提示用户
        POST salesForecastForecastOrder/pageList                 → 取该月份创建时间最新的一条 = forecastOrderId
  步骤2 选择物料顶码 + 基地数量 → 生成销售计划单
        POST salesForecastForecastOrder/checkPlanAndSave         → 保存各基地数量（actualBaseQty）
        POST salesForecastForecastOrder/generatePlanOrder        → planId / planCode（销售计划单号）
  步骤3 销售计划审批通过
        POST salesForecastPlanOrder/page {planCodeAllLike}       → planOrderId / processInstanceId
        GET  activitiFlow/listUserTodoTasks                      → taskId / taskKey
        POST salesForecast/approval/completeTaskSalesPlanSubmit  → 审批通过
  步骤4 销售月需求计划取创建时间最新的一条 → 计划单号（= HCH 来源单号）
        GET  psMonthlySalesDemandPlan/page
  步骤5 HCH：按来源单号找数据行 → 提交销售审批
        POST /web/monthProductionPlanDraft/page                  → salePlanNo / hchMonth / hchYear
        POST /web/monthProductionPlanDraft/submitToSaleAudit
  步骤6 HCH：按来源单号查审批行状态
        POST /auditOrder/pageList   auditStatus: 1=新增(等待) 20=审批中(继续)
        审批中 → 商用 Token：psMonthlySalesDemandPlan/page?demandPlanCode=...
                 → activitiFlow/listUserTodoTasks → activitiFlow/completeTask 审批通过
  步骤7 HCH：按来源单号查月生产计划行 → 推送到采购（多行循环）
        POST /month-production-plan/page
        POST /month-production-plan/push-month-plan/sale-plan-no

Token 说明：
  - 步骤 1~4、6(商用审批) 用【管理端 Token】（本流程全部在商用计划/排产管理端页面完成）
  - 步骤 5~7 用【HCH 系统 Token】（ds-oms.gree.com:9002）
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import order_machine
from order_machine import HchApi, OrderMachineApi, log

BASE = "https://qasalescloud.gree.com"

FORECAST = "/gree-cos-forecast/forecast-api/v1/manager/salesForecastForecastOrder"
PLAN_ORDER = "/gree-cos-forecast/forecast-api/v1/manager/salesForecastPlanOrder"
SALES_FORECAST = "/gree-cos-forecast/forecast-api/v1/manager/salesForecast"
DEMAND_PLAN = "/gree-cos-production-scheduling/admin-api/v1/psMonthlySalesDemandPlan"
ACTIVITI = "/gree-crm-activiti/activiti-api/v1/activitiFlow"

FORECAST_REFERER = BASE + "/admin/sales-plan/plan-forecast"
ADJUST_REFERER = FORECAST_REFERER + "/forecast/adjust"
PLAN_REFERER = FORECAST_REFERER + "/plan"
APPROVAL_REFERER = FORECAST_REFERER + "/plan/approval"
DEMAND_REFERER = BASE + "/admin/produce-center/monthly-sales-demand-plan"

# 中台快照数据同步（前端「同步数据」按钮）：
#   create 报 G6001「当日中台快照数据不存在」→ getSyncInfo 读同步状态 → startSync 触发同步
#   → 中止流程，等用户确认数据同步完成后再重跑（前端也是点「同步数据」后等 1~2 分钟）。
SNAPSHOT_INFO = "/gree-cos-forecast/forecast-api/v1/manager/salesForecastBiDataSnapshot/getSyncInfo"
SNAPSHOT_START = "/gree-cos-forecast/forecast-api/v1/manager/salesForecastBiDataSnapshot/startSync"
SNAPSHOT_ERROR_CODE = "G6001"
SNAPSHOT_MISSING_PHRASE = "当日中台快照数据不存在"
SNAPSHOT_SYNCED = 1     # syncStatus：0=未同步/同步中，1=已同步
SNAPSHOT_USER_HINT = ("当日中台快照数据不存在，已调用同步接口(startSync)触发中台数据同步，"
                      "需等待 1~2 分钟。请确认数据同步后再执行后续程序。")


class SnapshotNotSyncedError(RuntimeError):
    """当日中台快照数据不存在：已触发 startSync 同步，需用户确认数据同步后再跑后续流程。"""


def get_snapshot_sync_info(api):
    """查各产线的中台快照同步状态（进页面就会调，只读、不触发同步）。"""
    return api.post(SNAPSHOT_INFO, None, referer=FORECAST_REFERER).get("data") or {}


def snapshot_lines(info):
    """getSyncInfo 的 productionLineSyncInfoList：productLineId / syncStatus / latestSyncTime。"""
    return (info or {}).get("productionLineSyncInfoList") or []


def snapshot_line_status(info):
    """同步状态摘要，例如「产线1：同步中/未同步，产线2：已同步」。"""
    items = []
    for line in snapshot_lines(info):
        pid = line.get("productLineId")
        if pid is None:
            continue
        state = "已同步" if line.get("syncStatus") == SNAPSHOT_SYNCED else "同步中/未同步"
        items.append(f"产线{pid}：{state}")
    return "，".join(items) or "无产线同步信息"


def start_bi_data_snapshot_sync(api, dept_id, product_line_ids):
    """调 startSync 触发中台快照同步（前端「同步数据」按钮真正调的就是它）。"""
    body = {"deptId": dept_id, "productLineIdList": list(product_line_ids)}
    return api.post(SNAPSHOT_START, body, referer=FORECAST_REFERER).get("data")


def sync_bi_data_snapshot(api, dept_id=None, product_line_ids=None):
    """G6001 后的处置：getSyncInfo 读状态 → startSync 触发（未同步的）产线同步。

    返回状态摘要文本，拼进给用户的提示里；自身失败不抛异常——原始报错才是主角。
    """
    log("   ⚠️ 当日中台快照数据不存在 → getSyncInfo 读取中台快照同步状态")
    try:
        info = get_snapshot_sync_info(api)
    except Exception as exc:
        log(f"   ⚠️ getSyncInfo 调用失败：{exc}")
        return f"getSyncInfo 调用失败：{exc}"
    status = snapshot_line_status(info)
    log(f"   ℹ️ 当前同步状态：{status}")
    dept_id = dept_id or info.get("deptId")
    targets = [i for i in (product_line_ids or []) if i is not None]
    if not targets:
        targets = [line.get("productLineId") for line in snapshot_lines(info)
                   if line.get("productLineId") is not None]
    known = {line.get("productLineId") for line in snapshot_lines(info)}
    pending = [line.get("productLineId") for line in snapshot_lines(info)
               if line.get("productLineId") in targets
               and line.get("syncStatus") != SNAPSHOT_SYNCED]
    pending += [t for t in targets if t not in known]   # 状态里没有这条产线 → 按未同步处理
    if not pending:
        return status
    try:
        msg = start_bi_data_snapshot_sync(api, dept_id, pending)
        log(f"   ℹ️ 已触发中台数据同步（startSync 返回：{msg or '空'}；等待 1~2 分钟后重跑）")
        return f"{status}；已触发同步（产线 {pending}）"
    except Exception as exc:
        log(f"   ⚠️ startSync 调用失败：{exc}")
        return f"{status}；startSync 调用失败：{exc}"

# 默认展示/填写的基地（与前端 index.html 的 PLAN_BASES 一致）；
# 可被 run_plan_month_flow(bases=...) 覆盖（技能层从 automation_config.json 读取）。
PLAN_BASES = ["N55", "N46", "N45", "N48", "N50"]

APPROVE_ACTION_TYPE = 1
APPROVE_ACTION_NAME = "同意"
# 销售计划审批（completeTaskSalesPlanSubmit）的 businessDataJson 为 null
PLAN_APPROVE_BUSINESS_DATA_JSON = None
# 月需求计划调整单审批（activitiFlow/completeTask）的 businessDataJson 为 "[]"
ADJUST_APPROVE_BUSINESS_DATA_JSON = "[]"

# HCH
HCH_DRAFT = "/web/monthProductionPlanDraft"
HCH_MONTH_PLAN = "/month-production-plan"
HCH_AUDIT_ORDER = "/auditOrder"
HCH_STATUS_NEW = 1          # 新增：商用侧审批流程还没生成，需要等待
HCH_STATUS_IN_AUDIT = 20    # 审批中：可以去做商用侧审批

# 等待策略（两处等待的时间量级不同）
#   步骤4：月需求计划单由审批后下游异步生成，通常 1 分钟内
DEMAND_WAIT_RETRIES = int(os.environ.get("PLAN_DEMAND_RETRIES", "12"))
DEMAND_WAIT_INTERVAL = float(os.environ.get("PLAN_DEMAND_INTERVAL", "5"))
#   步骤6：HCH「新增 → 审批中」由定时任务更新，约 5 分钟执行一次 → 默认最长等 10 分钟
HCH_AUDIT_TASK_INTERVAL_MIN = 5
AUDIT_WAIT_RETRIES = int(os.environ.get("PLAN_AUDIT_RETRIES", "60"))
AUDIT_WAIT_INTERVAL = float(os.environ.get("PLAN_AUDIT_INTERVAL", "10"))
PAGE_LIMIT = int(os.environ.get("PLAN_PAGE_LIMIT", "20"))

# 本轮流程开始时间（毫秒，北京时间），用于识别「本轮新生成」的月需求计划单
FLOW_START_MS = 0


def now_ms():
    return int(time.time() * 1000)


def ms_to_month(ms):
    """毫秒时间戳 → 'YYYY-MM'（北京时间）"""
    if not ms:
        return ""
    moment = datetime.fromtimestamp(int(ms) / 1000,
                                    timezone(timedelta(hours=8)))
    return moment.strftime("%Y-%m")


def hch_referer():
    """HCH 生产月计划页面 referer。

    基址取 order_machine.HCH_BASE（技能层切换 QA/UAT 时改的就是它），
    因此这里每次取值都反映当前环境，而不是导入时算死的常量。
    """
    base = (getattr(order_machine, "HCH_BASE", "")
            or os.environ.get("HCH_BASE")
            or "https://ds-oms.gree.com:9002")
    return base.rstrip("/") + "/hch/productionPlan/productionMonthPlan"


def page_records(api, path, payload, referer):
    """POST 分页接口，取 data（含 records/pages）"""
    return api.post(path, payload, referer=referer).get("data") or {}


# --------------------------------------------------------------------------- 步骤 1

def get_create_init_data(api):
    data = api.get(FORECAST + "/getCreateInitData", referer=FORECAST_REFERER).get("data") or {}
    dept_id = data.get("deptId")
    product_lines = data.get("productLines") or []
    product_line_id = product_lines[0].get("productLineId") if product_lines else None
    if product_line_id is None:
        # 产线列表为空（中台快照没同步时前端也会出现）→ 退回 getSyncInfo 的产线列表
        try:
            lines = snapshot_lines(get_snapshot_sync_info(api))
        except Exception as exc:
            lines = []
            log(f"   ⚠️ getSyncInfo 取产线列表失败：{exc}")
        product_line_id = lines[0].get("productLineId") if lines else 1
        log(f"   ⚠️ getCreateInitData 未返回 productLines → 用 productLineId={product_line_id}"
            f"（中台快照可能未同步，create 可能报 G6001）")
    if not dept_id:
        raise RuntimeError(f"getCreateInitData 未返回 deptId: {data}")
    return dept_id, product_line_id


def is_snapshot_missing(error):
    """判断是否是「当日中台快照数据不存在」(G6001)：错误码或文案命中即算。"""
    text = str(error or "")
    if SNAPSHOT_ERROR_CODE in text or SNAPSHOT_MISSING_PHRASE in text:
        return True
    return "快照" in text and ("不存在" in text or "未同步" in text or "未生成" in text)


def _create_forecast_order_once(api, month, dept_id, product_line_id, exist_id=None):
    """调一次 create；命中「当日中台快照数据不存在」时触发同步并中止流程。"""
    body = {"deptId": dept_id, "planMonth": f"{month}-01", "productLineId": product_line_id}
    if exist_id:
        body["id"] = exist_id
    try:
        return api.post(FORECAST + "/create", body, referer=FORECAST_REFERER).get("data") or {}
    except Exception as exc:
        if is_snapshot_missing(exc):
            detail = sync_bi_data_snapshot(api, dept_id, [product_line_id])
            raise SnapshotNotSyncedError(
                f"{SNAPSHOT_USER_HINT}（月份 {month}；同步情况：{detail}；原始报错：{exc}）") from exc
        raise


def create_forecast_order(api, month, dept_id, product_line_id):
    """创建（或覆盖）计划月预测单；已存在时按提示再带 id 调一次"""
    data = _create_forecast_order_once(api, month, dept_id, product_line_id)
    exist_id = data.get("existForecastOrderId")
    if exist_id:
        log(f"   ⚠️ {data.get('msg') or '计划月预测单已存在'} → 继续覆盖（existForecastOrderId={exist_id}）")
        data = _create_forecast_order_once(api, month, dept_id, product_line_id, exist_id=exist_id)
    return data


def resolve_forecast_order(api, month):
    """pageList 里找 planMonth 命中、创建时间最新的一条预测单"""
    page = 1
    newest = None
    seen_months = []
    while page <= PAGE_LIMIT:
        data = page_records(api, FORECAST + "/pageList", {"page": page, "size": 50}, FORECAST_REFERER)
        records = data.get("records") or []
        for record in records:
            record_month = ms_to_month(record.get("planMonth"))
            if record_month == month:
                if newest is None or (record.get("createdTime") or 0) > (newest.get("createdTime") or 0):
                    newest = record
            elif record_month and record_month not in seen_months:
                seen_months.append(record_month)
        pages = data.get("pages") or 1
        if page >= pages or not records:
            break
        page += 1
    if newest is None:
        raise RuntimeError(f"未找到 {month} 的预测单（列表返回过的月份：{', '.join(seen_months) or '无'}）")
    return newest


def run_create_phase(api, month):
    log("=" * 60)
    log(f"【步骤 1/7】创建/覆盖 {month} 预测单")
    dept_id, product_line_id = get_create_init_data(api)
    log(f"   deptId={dept_id} productLineId={product_line_id}")
    create_forecast_order(api, month, dept_id, product_line_id)
    order = resolve_forecast_order(api, month)
    log(f"   ✓ 预测单 id={order.get('id')} orderCode={order.get('orderCode')} 状态={order.get('status')}")
    return order


def fetch_forecast_details(api, forecast_order_id):
    """分页拉取预测单下的物料明细（每行含 id / commodityCode / baseDetails）"""
    records = []
    page = 1
    while page <= PAGE_LIMIT:
        data = page_records(api, FORECAST + "/detail/page",
                            {"forecastOrderId": forecast_order_id, "page": page, "size": 100},
                            FORECAST_REFERER)
        rows = data.get("records") or []
        records.extend(rows)
        pages = data.get("pages") or 1
        if page >= pages or not rows:
            break
        page += 1
    return records


# --------------------------------------------------------------------------- 步骤 2

def check_plan_and_save(api, forecast_order_id, items, bases=None):
    """保存各基地数量。items: [{detailId, quantities:{N55:1,...}}]"""
    bases = list(bases or PLAN_BASES)
    detail_updates = []
    detail_ids = []
    for item in items:
        detail_id = item.get("detailId")
        if not detail_id:
            raise RuntimeError(f"物料明细缺少 detailId: {item}")
        quantities = item.get("quantities") or {}
        base_updates = []
        total = 0
        for code in bases:
            qty = int(quantities.get(code) or 0)
            total += qty
            base_updates.append({"baseCode": code, "actualBaseQty": qty})
        detail_ids.append(detail_id)
        detail_updates.append({
            "detailId": detail_id,
            "actualPlanQty": total,
            "baseUpdates": base_updates,
        })

    body = {
        "forecastOrderId": forecast_order_id,
        "planDetailIds": detail_ids,
        "detailUpdates": detail_updates,
    }
    result = api.post(FORECAST + "/checkPlanAndSave", body, referer=ADJUST_REFERER).get("data")
    for item, update in zip(items, detail_updates):
        quantities = item.get("quantities") or {}
        detail = " ".join(f"{code}={int(quantities.get(code) or 0)}" for code in bases)
        log(f"   · 明细 {item.get('detailId')}：{detail}（合计 {update['actualPlanQty']}）")
    return result


def generate_plan_order(api, forecast_order_id, detail_ids):
    body = {"forecastOrderId": forecast_order_id, "planDetailIds": detail_ids}
    data = api.post(FORECAST + "/generatePlanOrder", body, referer=ADJUST_REFERER).get("data") or {}
    if not data.get("planCode"):
        raise RuntimeError(f"生成销售计划单失败：{data}")
    return data


def run_generate_phase(api, forecast_order, items, bases=None):
    log("=" * 60)
    log(f"【步骤 2/7】保存基地数量并生成销售计划单（{len(items)} 个物料顶码）")
    check_plan_and_save(api, forecast_order.get("id"), items, bases=bases)
    detail_ids = [item.get("detailId") for item in items]
    plan = generate_plan_order(api, forecast_order.get("id"), detail_ids)
    log(f"   ✓ 销售计划单 {plan.get('planCode')}（id={plan.get('planId')} 版本={plan.get('version')} "
        f"物料数={plan.get('commodityCount')}）")
    return plan


# --------------------------------------------------------------------------- 步骤 3

def find_plan_order(api, plan_code):
    data = page_records(api, PLAN_ORDER + "/page",
                        {"planCodeAllLike": plan_code, "page": 1, "size": 20}, PLAN_REFERER)
    records = data.get("records") or []
    if not records:
        raise RuntimeError(f"未找到销售计划单 {plan_code}")
    return records[0]


def find_todo_task(api, process_instance_id, referer=APPROVAL_REFERER):
    tasks = api.get(f"{ACTIVITI}/listUserTodoTasks?processInstanceId={process_instance_id}",
                    referer=referer).get("data") or []
    if not tasks:
        raise RuntimeError(f"未找到待办任务（processInstanceId={process_instance_id}）")
    return tasks[0]


def approve_plan_order(api, plan_order):
    plan_id = plan_order.get("id")
    process_instance_id = plan_order.get("processInstanceId")
    if not process_instance_id:
        raise RuntimeError(f"销售计划单 {plan_order.get('planCode')} 无待审批流程（processInstanceId 为空）")
    task = find_todo_task(api, process_instance_id)
    log(f"   · 待办任务 taskName={task.get('taskName')} taskKey={task.get('taskKey')}")
    body = {
        "id": plan_id,
        "actTaskHandleDTO": {
            "actionType": APPROVE_ACTION_TYPE,
            "actionName": APPROVE_ACTION_NAME,
            "taskId": task.get("taskId"),
            "taskKey": task.get("taskKey"),
            "processInstanceId": process_instance_id,
            "businessDataJson": PLAN_APPROVE_BUSINESS_DATA_JSON,
        },
    }
    result = api.post(SALES_FORECAST + "/approval/completeTaskSalesPlanSubmit", body,
                      referer=APPROVAL_REFERER).get("data")
    if not result:
        raise RuntimeError(f"销售计划审批未通过：{result}")
    return result


def run_plan_approval_phase(api, plan):
    log("=" * 60)
    log(f"【步骤 3/7】销售计划审批通过（{plan.get('planCode')}）")
    plan_order = find_plan_order(api, plan.get("planCode"))
    log(f"   · 计划单 id={plan_order.get('id')} 状态={plan_order.get('status')}")
    result = approve_plan_order(api, plan_order)
    log(f"   ✓ 审批通过：{result}")
    return plan_order


# --------------------------------------------------------------------------- 步骤 4

def demand_plan_page(api, page, month=None):
    """销售月需求计划分页查询；优先带计划周期（demandPlanPeriod）缩小范围，接口不认时退回全量"""
    if month:
        try:
            return api.get(f"{DEMAND_PLAN}/page?page={page}&size=100&demandPlanPeriod={month}",
                           referer=DEMAND_REFERER).get("data") or {}
        except Exception as exc:
            log(f"   ⚠️ 按计划周期 {month} 查询失败（{exc}），改用全量查询")
    return api.get(f"{DEMAND_PLAN}/page?page={page}&size=100", referer=DEMAND_REFERER).get("data") or {}


def find_latest_demand_plan(api, month=None):
    """销售月需求计划里创建时间最新的一条（可只查指定计划周期）"""
    page = 1
    newest = None
    while page <= PAGE_LIMIT:
        data = demand_plan_page(api, page, month)
        records = data.get("records") or []
        for record in records:
            if newest is None or (record.get("createdTime") or 0) > (newest.get("createdTime") or 0):
                newest = record
        pages = data.get("pages") or 1
        if page >= pages or not records:
            break
        page += 1
    return newest


def run_demand_plan_phase(api, month=None):
    log("=" * 60)
    log("【步骤 4/7】销售月需求计划取创建时间最新的一条")
    newest = None
    for attempt in range(1, DEMAND_WAIT_RETRIES + 1):
        newest = find_latest_demand_plan(api, month)
        if newest and (newest.get("createdTime") or 0) >= FLOW_START_MS:
            log(f"   ✓ 计划单号={newest.get('demandPlanCode')} 期间={newest.get('demandPlanPeriod')} "
                f"调整单状态={newest.get('monthlyPlanAdjustStatus')}")
            return newest
        log(f"   ⏳ 等待月需求计划单生成...（第 {attempt}/{DEMAND_WAIT_RETRIES} 次）")
        time.sleep(DEMAND_WAIT_INTERVAL)

    if newest is None:
        raise RuntimeError("销售月需求计划里没有任何数据")
    log(f"   ⚠️ 未发现本轮新生成的计划单，按当前最新一条继续：{newest.get('demandPlanCode')}")
    return newest


# --------------------------------------------------------------------------- 步骤 5

def hch_find_draft(api, source_order_no):
    data = api.post_form(HCH_DRAFT + "/page",
                         {"current": 1, "size": 10, "orderNos": source_order_no},
                         referer=hch_referer())
    rows = data.get("data") or []
    if not rows:
        raise RuntimeError(f"HCH 未找到来源单号 {source_order_no} 的生产计划草稿")
    return rows[0]


def run_hch_submit_phase(api, source_order_no):
    log("=" * 60)
    log(f"【步骤 5/7】HCH 提交销售审批（来源单号 {source_order_no}）")
    row = hch_find_draft(api, source_order_no)
    log(f"   · salePlanNo={row.get('salePlanNo')} 生产计划号={row.get('productionPlanDraftNo')} "
        f"生产计划总量={row.get('productionPlanTotal')}")
    body = {
        "salePlanNo": row.get("salePlanNo"),
        "hchMonth": row.get("hchMonth"),
        "hchYear": row.get("hchYear"),
        "inputOrderNo": row.get("inputOrderNo") or source_order_no,
        "highInventoryRemark": "",
    }
    data = api.post_json(HCH_DRAFT + "/submitToSaleAudit", body, referer=hch_referer())
    log(f"   ✓ {data.get('msg') or '提交成功'}")
    return row


# --------------------------------------------------------------------------- 步骤 6

def hch_query_audit_order(api, source_order_no):
    data = api.post_json(HCH_AUDIT_ORDER + "/pageList?size=10&current=1",
                         {"auditTypeCodes": [2, 3, 4], "orderNos": [source_order_no]},
                         referer=hch_referer())
    rows = data.get("data") or []
    return rows[0] if rows else None


def hch_audit_status(audit_order):
    """取审批行状态（auditStatus）：1=新增 20=审批中"""
    if not audit_order:
        return None
    status = audit_order.get("auditStatus")
    if status is None:
        details = audit_order.get("auditOrderDetailVOList") or []
        if details:
            status = details[0].get("auditStatus")
    return status


def wait_hch_audit_status(api, source_order_no):
    """轮询审批行状态：新增(1) 时提示等待 HCH 定时任务更新，直到审批中(20)

    HCH「新增 → 审批中」由一个定时任务推进，大约每 5 分钟执行一次，
    因此这里默认允许等待 10 分钟，并持续把等待进度打印到日志。
    """
    log("=" * 60)
    log(f"【步骤 6/7】HCH 查看审批行状态（来源单号 {source_order_no}）")
    log(f"   ℹ️ 状态由 HCH 定时任务更新（约每 {HCH_AUDIT_TASK_INTERVAL_MIN} 分钟一次），"
        f"最长等待 {int(AUDIT_WAIT_RETRIES * AUDIT_WAIT_INTERVAL / 60)} 分钟，请耐心等待")
    start = time.time()
    for attempt in range(1, AUDIT_WAIT_RETRIES + 1):
        audit_order = hch_query_audit_order(api, source_order_no)
        status = hch_audit_status(audit_order)
        if status == HCH_STATUS_IN_AUDIT:
            log(f"   ✓ 审批单 {audit_order.get('auditOrderNo')} 状态=审批中(20)，"
                f"（等待了 {int(time.time() - start)} 秒）")
            return audit_order

        waited = int(time.time() - start)
        if status == HCH_STATUS_NEW:
            log(f"   ⏳ 审批行状态为「新增」，正等待 HCH 定时任务更新审批状态"
                f"（约每 {HCH_AUDIT_TASK_INTERVAL_MIN} 分钟一次），已等待 {waited} 秒"
                f"（第 {attempt}/{AUDIT_WAIT_RETRIES} 次检查）")
        else:
            log(f"   ⏳ 审批行状态={status}，正等待 HCH 定时任务更新审批状态"
                f"（约每 {HCH_AUDIT_TASK_INTERVAL_MIN} 分钟一次），已等待 {waited} 秒"
                f"（第 {attempt}/{AUDIT_WAIT_RETRIES} 次检查）")
        time.sleep(AUDIT_WAIT_INTERVAL)

    raise RuntimeError(
        f"等待超时：来源单号 {source_order_no} 的审批行状态仍未变为「审批中」"
        f"（已等待约 {int(AUDIT_WAIT_RETRIES * AUDIT_WAIT_INTERVAL / 60)} 分钟，"
        f"HCH 定时任务约每 {HCH_AUDIT_TASK_INTERVAL_MIN} 分钟更新一次，可稍后重试）")


def find_demand_plan_by_code(api, demand_plan_code):
    data = api.get(f"{DEMAND_PLAN}/page?demandPlanCode={demand_plan_code}&page=1&size=20",
                   referer=DEMAND_REFERER).get("data") or {}
    records = data.get("records") or []
    if not records:
        raise RuntimeError(f"未找到月需求计划单 {demand_plan_code}")
    return records[0]


def approve_demand_plan_adjust(api, demand_plan_code):
    """商用 Token：月需求计划调整单审批通过"""
    record = find_demand_plan_by_code(api, demand_plan_code)
    process_instance_id = record.get("processInstanceId")
    if not process_instance_id:
        raise RuntimeError(f"月需求计划单 {demand_plan_code} 无审批流程（processInstanceId 为空）")
    log(f"   · 调整单 id={record.get('monthlyPlanAdjustId')} 状态={record.get('monthlyPlanAdjustStatus')}")
    task = find_todo_task(api, process_instance_id, referer=DEMAND_REFERER)
    log(f"   · 待办任务 taskName={task.get('taskName')} taskKey={task.get('taskKey')}")
    body = {
        "actionType": APPROVE_ACTION_TYPE,
        "actionName": APPROVE_ACTION_NAME,
        "taskId": task.get("taskId"),
        "taskKey": task.get("taskKey"),
        "processInstanceId": process_instance_id,
        "businessDataJson": ADJUST_APPROVE_BUSINESS_DATA_JSON,
    }
    result = api.post(ACTIVITI + "/completeTask", body, referer=DEMAND_REFERER).get("data")
    if not result:
        raise RuntimeError(f"月需求计划调整单审批未通过：{result}")
    return result


# --------------------------------------------------------------------------- 步骤 7

def hch_query_month_plans(api, source_order_no):
    data = api.post_form(HCH_MONTH_PLAN + "/page",
                         {"current": 1, "size": 10, "sourceOrderNos": source_order_no},
                         referer=hch_referer())
    return data.get("data") or []


def hch_push_sale_plan_no(api, sale_plan_no):
    return api.post_json(HCH_MONTH_PLAN + "/push-month-plan/sale-plan-no", [sale_plan_no],
                         referer=hch_referer())


def run_hch_push_phase(api, source_order_no):
    log("=" * 60)
    log(f"【步骤 7/7】HCH 推送到采购（来源单号 {source_order_no}）")
    rows = hch_query_month_plans(api, source_order_no)
    if not rows:
        raise RuntimeError(f"HCH 未找到来源单号 {source_order_no} 的月生产计划行")
    log(f"   · 共 {len(rows)} 条数据行，逐条推送")
    results = []
    for row in rows:
        sale_plan_no = row.get("salePlanNo")
        if not sale_plan_no:
            log("   ⚠️ 跳过没有 salePlanNo 的行")
            continue
        data = hch_push_sale_plan_no(api, sale_plan_no)
        ok = bool(data.get("success"))
        log(f"   {'✓' if ok else '✗'} {sale_plan_no}（生产计划号 {row.get('productionPlanNo')}）"
            f"：{data.get('msg')}")
        results.append({"salePlanNo": sale_plan_no, "success": ok, "msg": data.get("msg")})
    if not results:
        raise RuntimeError("没有可推送的数据行（均无 salePlanNo）")
    if not all(r["success"] for r in results):
        raise RuntimeError(f"部分数据行推送失败：{results}")
    return results


# --------------------------------------------------------------------------- 主流程

def run_plan_month_flow(manager_token, hch_token, month, items, result_sink=None,
                        bases=None, forecast_order=None):
    """执行整条商用月计划流程，返回汇总信息。

    items: [{detailId, quantities:{基地编码: 数量}}]
    bases: 基地编码列表（缺省用模块级 PLAN_BASES）
    forecast_order: 已创建好的预测单（技能层若已创建可传入，避免重复创建）
    """
    global FLOW_START_MS

    if not manager_token:
        raise RuntimeError("缺少商用管理端 Token")
    if not hch_token:
        raise RuntimeError("缺少 HCH 系统 Token")
    if not month:
        raise RuntimeError("缺少计划月份")
    if not items:
        raise RuntimeError("请至少提供一个物料顶码并填写基地数量")

    bases = list(bases or PLAN_BASES)
    FLOW_START_MS = now_ms()
    manager_api = OrderMachineApi(manager_token)
    hch_api = HchApi(hch_token)

    log("=" * 60)
    log(f"商用月计划流程开始：月份={month} 物料数={len(items)}")
    log("=" * 60)

    if forecast_order is None:
        forecast_order = run_create_phase(manager_api, month)
    plan = run_generate_phase(manager_api, forecast_order, items, bases=bases)
    plan_order = run_plan_approval_phase(manager_api, plan)
    demand_plan = run_demand_plan_phase(manager_api, month)
    source_order_no = demand_plan.get("demandPlanCode")

    draft_row = run_hch_submit_phase(hch_api, source_order_no)
    audit_order = wait_hch_audit_status(hch_api, source_order_no)
    approve_result = approve_demand_plan_adjust(manager_api, source_order_no)
    log(f"   ✓ 商用审批通过：{approve_result}")
    push_results = run_hch_push_phase(hch_api, source_order_no)

    summary = {
        "month": month,
        "forecastOrderId": forecast_order.get("id"),
        "forecastOrderCode": forecast_order.get("orderCode"),
        "planId": plan.get("planId"),
        "planCode": plan.get("planCode"),
        "planOrderId": plan_order.get("id"),
        "sourceOrderNo": source_order_no,
        "salePlanNo": draft_row.get("salePlanNo"),
        "auditOrderNo": audit_order.get("auditOrderNo") if audit_order else None,
        "pushed": push_results,
    }
    if result_sink is not None:
        result_sink.update(summary)

    log("=" * 60)
    log("✓ 商用月计划流程全部完成")
    log(f"   销售计划单：{summary['planCode']}（id={summary['planId']}）")
    log(f"   来源单号：{summary['sourceOrderNo']}  销售计划号：{summary['salePlanNo']}")
    log(f"   已推送采购：{', '.join(r['salePlanNo'] for r in push_results)}")
    log("=" * 60)
    return summary


def main():
    manager_token = os.environ.get("MANAGER_TOKEN") or os.environ.get("COMMODITY_TOKEN") or ""
    hch_token = os.environ.get("HCH_TOKEN") or ""
    month = os.environ.get("PLAN_MONTH") or ""
    raw_items = os.environ.get("PLAN_ITEMS") or "[]"
    try:
        items = json.loads(raw_items)
    except Exception as exc:
        raise RuntimeError(f"PLAN_ITEMS 不是合法 JSON: {exc}")

    result_sink = {}
    try:
        run_plan_month_flow(manager_token, hch_token, month, items, result_sink=result_sink)
    except Exception as exc:
        import traceback
        log(f"✗ 流程失败：{exc}")
        log(traceback.format_exc())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

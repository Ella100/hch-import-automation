"""
商用订单机 - 提交订单 + 客户端审批 + 管理端审批 + 提交排产 + HCH 订单机处理
（独立模块，与原有三条导入流程无关）

流程（对应抓包 qasalescloud 采购下单 + 接单审核链路 + HCH 订单机链路）：
  1. regularPurchase/list           取分组物料（物料顶码）
  2. regularPurchase/settleCheck    选中物料结算校验
  3. client/orderInfo/orderSettle   订单结算 → orderSettleKey / supplier / consignee / orderType
  4. client/orderInfo/getOrderSettleData/{key}  结算明细（展示日志用）
  5. opptyInfo/pageOpptyRelatedProject          找项目 XSF0009-26-432 → opptyInfo
  6. client/orderInfo/orderSubmit               提交订单
  7. 客户端审批   approval/page → listUserTodoTasks → completeTask（客户端 Token）
  8. 管理端审批   approval/page → listUserTodoTasks → completeTask（管理端 Token，含 taskKey）
  9. 提交排产     psOrderDemandPlan/salesApproval/page → salesApproval/submit（管理端 Token）
 10. HCH 订单机处理（HCH 系统 Token，ds-oms.gree.com:9002）：
       order-machine/page-detail       按订单号查询明细（几行就处理几行）
        order-machine/adjust            逐行调整（selectionSystem / adjustRemarks 固定值）
        order-machine/allocate-base     逐行分配基地（wareCode 可指定，未指定则从候选基地随机）
        order-machine/transfer-plan     转生产计划
        month-production-plan/page      查询月生产计划 → salePlanNo
        month-production-plan/push-month-plan/sale-plan-no   推送 salePlanNo

参数来源（详见 outputs/order-machine-api-trace.md、order-approval-api-trace.md、
          outputs/hch-order-flow-api-trace.md）：
  - 项目固定 PROJECT_CODE（默认 XSF0009-26-432）
  - 提货方式恒为 PICKUP_MERCHANT_TYPE（默认 "1"）
  - 计划发货时间 = 当前时间 + DELIVERY_DAYS 天（默认 1，前端默认值）
  - 数量默认取列表项 num
  - customerName 取 token(JWT) 的 outletsName
  - 审批/提交排产：客户端审批用 COMMODITY_TOKEN；管理端审批与提交排产用 MANAGER_TOKEN
  - HCH：用 HCH_TOKEN；adjust 的 selectionSystem=3、adjustRemarks="测试" 为固定值；
    allocate-base 的 wareCode 默认从 HCH_WARE_CODES（默认 = 全量 15 个基地）随机取，
    也可通过 ware_map（{顶码: wareCode}，键 "*" 表示整单统一）指定基地；
    基地名 → 编码 用 resolve_base_code()（如 "珠海基地" → N50）
"""
import base64
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

BASE = "https://qasalescloud.gree.com"
GW = BASE + "/gateway"

COMMODITY_GROUP_ID = os.environ.get("COMMODITY_GROUP_ID", "1787621792359")
PROJECT_CODE = os.environ.get("PROJECT_CODE", "XSF0009-26-432")
PICKUP_MERCHANT_TYPE = os.environ.get("PICKUP_MERCHANT_TYPE", "1")
DELIVERY_DAYS = int(os.environ.get("DELIVERY_DAYS", "1"))
TIMEOUT = 30

# 审批（商用订单-接单审核）相关常量
APPROVAL_SECTOR_ID = int(os.environ.get("APPROVAL_SECTOR_ID", "10"))  # 待办业务板块：商用订单
APPROVE_ACTION_TYPE = 1          # 1=同意（2=驳回）
APPROVE_ACTION_NAME = "同意"
# 客户端审批的 businessDataJson
APPROVE_BUSINESS_DATA_JSON = json.dumps({"appointDeliverBaseList": []}, separators=(",", ":"))
# 管理端审批的 businessDataJson（多一个 expectedFinishTime）
MANAGER_APPROVE_BUSINESS_DATA_JSON = json.dumps(
    {"appointDeliverBaseList": [], "expectedFinishTime": None}, separators=(",", ":"))
APPROVAL_REFERER = BASE + "/approval/order-info/view"
APPROVAL_RETRIES = int(os.environ.get("APPROVAL_RETRIES", "5"))
APPROVAL_RETRY_WAIT = float(os.environ.get("APPROVAL_RETRY_WAIT", "2"))

# 提交排产（生产排产-销售审批）相关常量
PS_PAGE_PATH = "/gree-cos-production-scheduling/admin-api/v1/psOrderDemandPlan/salesApproval/page"
PS_SUBMIT_PATH = "/gree-cos-production-scheduling/admin-api/v1/psOrderDemandPlan/salesApproval/submit"
PS_REFERER = BASE + "/admin/produce-center/sales-approval"
PS_ORDER_LEVEL = int(os.environ.get("PS_ORDER_LEVEL", "2"))
PS_PRODUCTION_TYPE = int(os.environ.get("PS_PRODUCTION_TYPE", "1"))
PS_RETRIES = int(os.environ.get("PS_RETRIES", "5"))
PS_RETRY_WAIT = float(os.environ.get("PS_RETRY_WAIT", "2"))

# HCH 订单机处理（ds-oms.gree.com:9002）相关常量
HCH_BASE = os.environ.get("HCH_BASE", "https://ds-oms.gree.com:9002")
HCH_API_BASE = "/api/api-hch-order-server"
HCH_REFERER_ORDER_MACHINE = HCH_BASE + "/hch/salesPlan/orderMachineManage"
HCH_REFERER_MONTH_PLAN = HCH_BASE + "/hch/productionPlan/productionMonthPlan"
HCH_SELECTION_SYSTEM = int(os.environ.get("HCH_SELECTION_SYSTEM", "3"))  # adjust 固定值
HCH_ADJUST_REMARKS = os.environ.get("HCH_ADJUST_REMARKS", "测试")        # adjust 固定值
# 分配基地：基地名 → wareCode
# HCH 订单机「分配基地」下拉的全量基地（共 15 个）—— 也是唯一来源：
# 指定基地用 resolve_base_code()，未指定时的随机候选直接取该表的值。
HCH_BASE_ALIASES = {
    "金湾": "N39",
    "赣州": "N40A",
    "临沂": "N40B",
    "成都": "N41",
    "南京": "N45",
    "洛阳": "N46",
    "杭州": "N47",
    "长沙": "N48",
    "芜湖": "N49",
    "珠海": "N50",
    "郑州": "N51",
    "武汉": "N52",
    "石家庄": "N53",
    "重庆": "N54",
    "合肥": "N55",
}

# 未指定基地时的随机候选：默认全量基地，可用环境变量 HCH_WARE_CODES 覆盖
HCH_WARE_CODES = ([c for c in (os.environ.get("HCH_WARE_CODES") or "").split() if c]
                  or list(HCH_BASE_ALIASES.values()))
HCH_PAGE_SIZE = int(os.environ.get("HCH_PAGE_SIZE", "50"))
HCH_RETRIES = int(os.environ.get("HCH_RETRIES", "5"))
HCH_RETRY_WAIT = float(os.environ.get("HCH_RETRY_WAIT", "2"))


def resolve_base_code(text):
    """把基地写法统一成 wareCode。

    - None / 空 → None
    - 命中内置基地名字典（"珠海基地" / "珠海"）→ 对应编码（"N50"）
    - 形如独立编码（"N50" / "N40A"）→ 原样大写返回
    - 其它 → None（由调用方报错）
    """
    if text is None:
        return None
    compact = re.sub(r"\s+", "", str(text))
    if not compact:
        return None
    for name, code in HCH_BASE_ALIASES.items():
        if name in compact:
            return code
    m = re.search(r"(?<![A-Za-z0-9])([A-Za-z]{1,2}\d{2,4}[A-Za-z]?)(?![A-Za-z0-9])", compact)
    if m:
        return m.group(1).upper()
    return None

# 提交订单时每行物料的完整字段模板（与前端抓包一致，动态字段随后覆盖）
COMMODITY_TEMPLATE = {
    "remark": None, "createdBy": None, "updatedBy": None, "createdTime": None, "updatedTime": None,
    "commodityId": None, "commodityCode": None, "commodityModel": None, "orderCategory": None,
    "productionCodeId": None, "productionCode": None, "displayName": None, "commoditySpecifications": None,
    "commodityTotalQty": None, "deliveryPeriod": None, "commodityPrice": None, "actualPaymentPrice": None,
    "purchasePrice": None, "commodityPriceType": 0, "commodityPriceName": None, "commodityAttribute": None,
    "productLine": None, "isCustomize": False, "commodityKey": None, "consultOrderDeliveryId": None,
    "specialInquiryNumber": None, "selectionNum": None, "specifiedPriceId": 0, "numConfig": None,
    "availableRebateNum": None, "availableProjectQuotaNum": None, "priceRelationId": 0,
    "priceApplicationId": 0, "priceApplicationSceneType": None, "priceApplicationCode": None,
    "priceApplicationPriceCode": None, "priceApplicationPriceListId": 0, "priceApplicationNature": None,
    "quotationId": None, "quotationPriceListId": 0, "quotationVersion": None, "canOrderNum": None,
    "externalResidualPressureMin": 0, "externalResidualPressureMax": None, "fileList": [],
    "saleOrderCode": None, "saleOrderLineCode": None, "projectCode": None, "merchantCode": None,
    "customized": None, "customizedItemList": None, "customizedFileList": None, "orderInfoWpDTO": None,
    "purchaseCode": None, "unit": None, "quotationBomId": None, "priceApplicationPriceType": None,
    "businessClassification": None, "modelNonstandardPointInfoDTOList": None, "originalPrice": None,
    "index": 0, "canPay": False, "isDeliveryOnOrder": False, "orderQty": None, "opptyInfoId": None,
    "wpOrderCode": None, "wpOrderLineCode": None, "modelNonstandardPonitIdList": None,
}


def log(msg):
    print(msg)
    sys.stdout.flush()


def decode_jwt_payload(token):
    """解析 JWT payload（不校验签名），用于取 outletsName 等。"""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part).decode("utf-8", "replace"))
    except Exception:
        return {}


def planned_delivery_time(days=None, now=None):
    """前端默认值：当前时间 + N 天，JS toISOString 风格（毫秒 + Z）。"""
    days = DELIVERY_DAYS if days is None else days
    moment = (now or datetime.now(timezone.utc)) + timedelta(days=days)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def iso_from_ms(ts):
    """毫秒时间戳 → JS toISOString 风格字符串（UTC，毫秒 3 位 + Z）。"""
    moment = datetime.fromtimestamp(ts / 1000, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


class OrderMachineApi:
    """商品中心 + 订单中心 + 审批中心的接口封装（每个实例一个 Bearer Token）。"""

    def __init__(self, token):
        self.token = token
        self.session = requests.Session()

    def _headers(self, referer):
        return {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "content-type": "application/json",
            "authorization": f"Bearer {self.token}",
            "origin": BASE,
            "referer": referer,
            "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"),
        }

    def post(self, path, payload, referer=None):
        url = GW + path
        resp = self.session.post(url, json=payload,
                                 headers=self._headers(referer or BASE + "/purchase-list"),
                                 timeout=TIMEOUT, verify=False)
        return self._parse(resp, path)

    def get(self, path, referer=None):
        url = GW + path
        resp = self.session.get(url, headers=self._headers(referer or BASE + "/purchase-list"),
                                timeout=TIMEOUT, verify=False)
        return self._parse(resp, path)

    def put(self, path, payload, referer=None):
        url = GW + path
        resp = self.session.put(url, json=payload,
                                headers=self._headers(referer or BASE + "/purchase-list"),
                                timeout=TIMEOUT, verify=False)
        return self._parse(resp, path)

    @staticmethod
    def _parse(resp, path):
        if resp.status_code != 200:
            raise RuntimeError(f"{path} 请求失败: HTTP {resp.status_code} - {(resp.text or '')[:200]}")
        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"{path} 返回非 JSON: {(resp.text or '')[:200]}")
        if str(data.get("code")) not in ("00000", "0"):
            raise RuntimeError(f"{path} 业务失败: code={data.get('code')} msg={data.get('msg')}")
        return data


class HchApi:
    """HCH 系统（ds-oms.gree.com:9002）接口封装。

    与 qasalescloud 的差异：
      - 成功判定为 code == 0（整数），而非 "00000"
      - 部分接口 form-encoded（page-detail / transfer-plan/preview / month-production-plan/page），
        部分接口 JSON（adjust / allocate-base / transfer-plan / sale-plan-no）
    """

    def __init__(self, token):
        self.token = token
        self.session = requests.Session()

    def _headers(self, content_type, referer):
        return {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "content-type": content_type,
            "authorization": f"Bearer {self.token}",
            "origin": HCH_BASE,
            "referer": referer,
            "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"),
        }

    def post_form(self, path, data, referer=None):
        url = HCH_BASE + HCH_API_BASE + path
        resp = self.session.post(url, data=data,
                                 headers=self._headers("application/x-www-form-urlencoded",
                                                       referer or HCH_REFERER_ORDER_MACHINE),
                                 timeout=TIMEOUT, verify=False)
        return self._parse(resp, path)

    def post_json(self, path, payload, referer=None):
        url = HCH_BASE + HCH_API_BASE + path
        resp = self.session.post(url, json=payload,
                                 headers=self._headers("application/json;charset=UTF-8",
                                                       referer or HCH_REFERER_ORDER_MACHINE),
                                 timeout=TIMEOUT, verify=False)
        return self._parse(resp, path)

    @staticmethod
    def _parse(resp, path):
        if resp.status_code != 200:
            raise RuntimeError(f"{path} 请求失败: HTTP {resp.status_code} - {(resp.text or '')[:200]}")
        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"{path} 返回非 JSON: {(resp.text or '')[:200]}")
        if str(data.get("code")) != "0":
            raise RuntimeError(f"{path} 业务失败: code={data.get('code')} msg={data.get('msg')}")
        return data


def build_settle_commodity(item, qty):
    """orderSettle 入参里的单行物料（与抓包字段一致）。"""
    return {
        "actualPaymentPrice": None,
        "commodityAttribute": item.get("attribute"),
        "commodityCode": item.get("code"),
        "commodityId": item.get("commodityId"),
        "commodityKey": item.get("commodityKey"),
        "commodityModel": item.get("model"),
        "commodityPrice": None,
        "commodityPriceType": None,
        "commodityTotalQty": qty,
        "deliveryPeriod": str(item.get("deliveryPeriod")),
        "isCustomize": False,
        "productLine": item.get("productLine"),
        "specifiedPriceId": None,
        "displayName": None,
        "priceRelationId": None,
        "commoditySpecifications": None,
        "commodityTitle": item.get("title"),
    }


def build_submit_commodity(item, qty, oppty_id, index):
    """orderSubmit 入参里的单行物料（模板 + 动态值覆盖）。"""
    period = item.get("deliveryPeriod")
    try:
        period = int(period) if period is not None and period != "" else None
    except (TypeError, ValueError):
        pass
    row = dict(COMMODITY_TEMPLATE)
    row.update({
        "commodityId": item.get("commodityId"),
        "commodityCode": item.get("code"),
        "commodityModel": item.get("model"),
        "commodityKey": item.get("commodityKey"),
        "commodityTotalQty": qty,
        "deliveryPeriod": period,
        "commodityAttribute": item.get("attribute"),
        "productLine": item.get("productLine"),
        "isCustomize": False,
        "opptyInfoId": oppty_id,
        "index": index,
        "canPay": False,
        "isDeliveryOnOrder": False,
    })
    return row


def build_oppty_info(record):
    """orderSubmit 里的 opptyInfo（来自 pageOpptyRelatedProject 记录）。"""
    return {
        "biddingFile": None,
        "nonStandardFile": None,
        "reportContentSpecialRequest": None,
        "isBidding": bool(record.get("isBid")),
        "isFactoryTestReport": False,
        "projectId": record.get("projectId"),
        "projectCode": record.get("projectCode"),
        "opptyInfoName": record.get("name"),
        "opptyInfoCode": record.get("code"),
        "opptyInfoId": record.get("id"),
    }


def find_todo_task(api, order_code, order_id=None):
    """按订单号在待办列表中定位审批任务。

    返回 (record, task, records)：
      - record: approval/page 命中的流程记录（含 processInstanceId）
      - task:   listUserTodoTasks 返回的当前用户待办任务（含 taskId / taskKey）
    """
    body = {"myCreated": False, "businessSectorId": APPROVAL_SECTOR_ID,
            "page": 1, "size": 20, "keyword": order_code}
    page = api.post("/gree-crm-activiti/activiti-api/v1/activitiFlow/approval/page",
                    body, referer=APPROVAL_REFERER)
    records = (page.get("data") or {}).get("records") or []

    record = None
    if order_id is not None:
        record = next((r for r in records if str(r.get("businessKey")) == str(order_id)), None)
    if record is None:
        record = next((r for r in records if order_code and order_code in (r.get("processInstanceName") or "")), None)
    if record is None:
        return None, None, records

    pid = record.get("processInstanceId")
    tasks = api.get(f"/gree-crm-activiti/activiti-api/v1/activitiFlow/listUserTodoTasks?processInstanceId={pid}",
                    referer=APPROVAL_REFERER)
    task_list = tasks.get("data") or []
    task = next((t for t in task_list if t.get("taskId")), None)
    return record, task, records


def approve_order(api, order_code, order_id=None, include_task_key=False, business_data_json=None):
    """审批单个订单（同意）。任务生成可能有延迟，未找到时重试。

    include_task_key: 管理端 completeTask 需带 taskKey
    business_data_json: 覆盖默认的 businessDataJson 字符串
    """
    business_data_json = business_data_json or APPROVE_BUSINESS_DATA_JSON
    record = task = None
    records = []
    for attempt in range(1, APPROVAL_RETRIES + 1):
        record, task, records = find_todo_task(api, order_code, order_id)
        if record is not None and task is not None:
            break
        if attempt < APPROVAL_RETRIES:
            log(f"  ⏳ 待审批任务尚未生成（第 {attempt} 次），{APPROVAL_RETRY_WAIT:g}s 后重试...")
            time.sleep(APPROVAL_RETRY_WAIT)

    if record is None:
        log(f"  ✗ 未找到订单 {order_code} 的待审批任务（待办共 {len(records)} 条）")
        return False
    pid = record.get("processInstanceId")
    if task is None:
        log(f"  ✗ 流程 {pid} 无待办任务（可能已被处理）")
        return False

    body = {
        "actionType": APPROVE_ACTION_TYPE,
        "actionName": APPROVE_ACTION_NAME,
        "taskId": task.get("taskId"),
    }
    if include_task_key and task.get("taskKey"):
        body["taskKey"] = task.get("taskKey")
    body["processInstanceId"] = pid
    body["businessDataJson"] = business_data_json

    res = api.post("/gree-crm-activiti/activiti-api/v1/activitiFlow/completeTask", body, referer=APPROVAL_REFERER)
    if res.get("data"):
        log(f"  ✓ 审批通过: {task.get('taskName')} / 任务 {task.get('taskId')}")
        return True
    log(f"  ✗ 审批失败: {json.dumps(res.get('data'), ensure_ascii=False)[:300]}")
    return False


def run_approval_phase(api, orders, label, include_task_key=False, business_data_json=None):
    """对一批订单逐个审批。返回未完成的数量。"""
    failed = 0
    for o in orders:
        code = o.get("code")
        oid = o.get("id")
        log(f"  → 订单 {code}（id={oid}）{label}...")
        try:
            if not approve_order(api, code, oid, include_task_key=include_task_key,
                                 business_data_json=business_data_json):
                failed += 1
        except Exception as e:
            log(f"  ✗ {label}异常 [{code}]: {e}")
            failed += 1
    return failed


def fetch_production_plan(api, order_code):
    """查询订单的待排产记录（psOrderDemandPlan/salesApproval/page）。"""
    query = f"?orderCodeAllLike={order_code}&showProcessed=0&page=1&size=20&listType=1"
    data = api.get(PS_PAGE_PATH + query, referer=PS_REFERER)
    return (data.get("data") or {}).get("records") or []


def submit_production_schedule(api, order_code):
    """提交排产（psOrderDemandPlan/salesApproval/submit）。返回是否成功。"""
    records = []
    for attempt in range(1, PS_RETRIES + 1):
        records = fetch_production_plan(api, order_code)
        if records:
            break
        if attempt < PS_RETRIES:
            log(f"  ⏳ 暂无可排产记录（第 {attempt} 次），{PS_RETRY_WAIT:g}s 后重试...")
            time.sleep(PS_RETRY_WAIT)
    if not records:
        log(f"  ✗ 订单 {order_code} 无可提交的排产记录")
        return False

    payload = []
    for r in records:
        actions = r.get("actionList") or []
        if not any("提交" in str(a) for a in actions):
            log(f"  ⏭ 跳过 {r.get('commodityCode')}（无可提交动作: {actions}）")
            continue
        delivery_ms = r.get("deliveryDate")
        payload.append({
            "id": r.get("orderDemandPlanDetailId"),
            "orderLevel": PS_ORDER_LEVEL,
            "productionNum": r.get("orderNum"),
            "deliveryTime": iso_from_ms(delivery_ms) if delivery_ms else None,
            "notes": r.get("orderInfoLineNotes") or "",
            "productionType": PS_PRODUCTION_TYPE,
        })

    if not payload:
        log(f"  ✗ 订单 {order_code} 无满足条件的排产明细")
        return False

    res = api.put(PS_SUBMIT_PATH, payload, referer=PS_REFERER)
    if res.get("data"):
        ids = ", ".join(str(p.get("id")) for p in payload)
        log(f"  ✓ 提交排产成功，共 {len(payload)} 行（明细 id: {ids}）")
        return True
    log(f"  ✗ 提交排产失败: {json.dumps(res.get('data'), ensure_ascii=False)[:300]}")
    return False


def hch_query_details(api, order_code):
    """按订单号查询 HCH 订单机明细（order-machine/page-detail，form）。

    返回全部明细行（按其 count 翻页取全）。响应 data[] 每行含
    id / sourceOrderItemNo / planQuantity / selectionSystem / needMerge / status / topCode 等。
    """
    rows = []
    total = None
    page = 1
    while True:
        data = api.post_form("/order-machine/page-detail",
                             {"current": page, "size": HCH_PAGE_SIZE, "sourceOrderNo": order_code})
        if total is None:
            total = data.get("count")
        batch = data.get("data") or []
        rows.extend(batch)
        if not batch or (total is not None and len(rows) >= int(total)):
            break
        page += 1
    return rows


def hch_adjust(api, row):
    """逐行调整（order-machine/adjust，JSON）。selectionSystem 与 adjustRemarks 为固定值。"""
    payload = {
        "detailId": row.get("id"),
        "sourceOrderItemNo": row.get("sourceOrderItemNo"),
        "selectionSystem": HCH_SELECTION_SYSTEM,
        "needMerge": int(row.get("needMerge") or 0),
        "adjustRemarks": HCH_ADJUST_REMARKS,
    }
    res = api.post_json("/order-machine/adjust", payload)
    return bool(res.get("success"))


def hch_allocate_base(api, row, ware_code):
    """逐行分配基地（order-machine/allocate-base，JSON）。wareCode 由调用方决定（指定或随机）。"""
    payload = {
        "detailId": row.get("id"),
        "sourceOrderItemNo": row.get("sourceOrderItemNo"),
        "wareCode": ware_code,
        "allocateRemarks": "",
    }
    res = api.post_json("/order-machine/allocate-base", payload)
    data = res.get("data") or {}
    failed = data.get("failedList") or []
    if failed:
        log(f"    ✗ 分配基地失败 [{ware_code}]: {json.dumps(failed, ensure_ascii=False)[:200]}")
        return False
    return bool(res.get("success"))


def hch_transfer_preview(api, order_code):
    """转生产计划预览（order-machine/transfer-plan/preview，form）→ candidateList。"""
    res = api.post_form("/order-machine/transfer-plan/preview", {"sourceOrderNo": order_code})
    return (res.get("data") or {}).get("candidateList") or []


def hch_transfer_plan(api, order_code, detail_ids):
    """转生产计划（order-machine/transfer-plan，JSON）。"""
    payload = {"detailIds": list(detail_ids), "sourceOrderNo": order_code}
    res = api.post_json("/order-machine/transfer-plan", payload)
    return bool(res.get("success"))


def hch_query_month_plan(api, order_code):
    """查询月生产计划（month-production-plan/page，form）→ records（含 salePlanNo）。"""
    res = api.post_form("/month-production-plan/page",
                        {"current": 1, "size": HCH_PAGE_SIZE, "sourceOrderNos": order_code},
                        referer=HCH_REFERER_MONTH_PLAN)
    return res.get("data") or []


def hch_push_sale_plan_no(api, sale_plan_no):
    """推送销售计划号（month-production-plan/push-month-plan/sale-plan-no，JSON 数组）。"""
    res = api.post_json("/month-production-plan/push-month-plan/sale-plan-no", [sale_plan_no],
                        referer=HCH_REFERER_MONTH_PLAN)
    return bool(res.get("success"))


def run_hch_phase(api, order_code, result_sink=None, ware_map=None):
    """对单个订单执行 HCH 订单机处理全流程。返回是否成功。

    明细几行就 adjust 几次、再 allocate-base 几次（每行各一次），随后转生产计划、
    查询月生产计划并推送其 salePlanNo。
    result_sink: 可选 dict，回填 hch（行数 / 分配的基地 / 推送的 salePlanNo）。
    ware_map: 可选 dict {顶码: wareCode}，为指定顶码固定基地；键 "*" 表示整单统一。
              未在 ware_map 中的行仍从 HCH_WARE_CODES 随机取。
    """
    # 1) 查询明细
    rows = []
    for attempt in range(1, HCH_RETRIES + 1):
        rows = hch_query_details(api, order_code)
        if rows:
            break
        if attempt < HCH_RETRIES:
            log(f"    ⏳ HCH 暂未查到订单明细（第 {attempt} 次），{HCH_RETRY_WAIT:g}s 后重试...")
            time.sleep(HCH_RETRY_WAIT)
    if not rows:
        log(f"    ✗ HCH 未查询到订单 {order_code} 的明细")
        return False
    log(f"    ✓ 查到 {len(rows)} 行明细，逐行调整并分配基地...")

    # 2) 逐行 adjust
    failed = 0
    for r in rows:
        try:
            if hch_adjust(api, r):
                log(f"      ✓ 调整成功 item={r.get('sourceOrderItemNo')} "
                    f"selectionSystem={HCH_SELECTION_SYSTEM}")
            else:
                log(f"      ✗ 调整失败 item={r.get('sourceOrderItemNo')}")
                failed += 1
        except Exception as e:
            log(f"      ✗ 调整异常 item={r.get('sourceOrderItemNo')}: {e}")
            failed += 1
    if failed:
        log(f"    ✗ {failed}/{len(rows)} 行调整未完成，终止 HCH 流程")
        return False

    # 3) 逐行 allocate-base（指定基地优先，否则随机）
    failed = 0
    wares = []
    ware_used = {}
    for r in rows:
        top_code = str(r.get("topCode") or "")
        specified = None
        if ware_map:
            specified = ware_map.get(top_code) or ware_map.get("*")
        ware_code = specified or random.choice(HCH_WARE_CODES)
        source = "指定" if specified else "随机"
        try:
            if hch_allocate_base(api, r, ware_code):
                log(f"      ✓ 分配基地 item={r.get('sourceOrderItemNo')} "
                    f"topCode={top_code} → {ware_code}（{source}）")
                wares.append(ware_code)
                if top_code:
                    ware_used[top_code] = ware_code
            else:
                log(f"      ✗ 分配基地失败 item={r.get('sourceOrderItemNo')}")
                failed += 1
        except Exception as e:
            log(f"      ✗ 分配基地异常 item={r.get('sourceOrderItemNo')}: {e}")
            failed += 1
    if failed:
        log(f"    ✗ {failed}/{len(rows)} 行分配基地未完成，终止 HCH 流程")
        return False

    if result_sink is not None:
        result_sink.setdefault("hch", {})[order_code] = {
            "rows": len(rows), "wareCodes": wares, "wareMap": ware_used}

    # 4) 转生产计划
    detail_ids = []
    try:
        candidates = hch_transfer_preview(api, order_code)
        detail_ids = [c.get("id") for c in candidates if c.get("id")]
    except Exception as e:
        log(f"    ⚠️ 转生产计划预览失败（将回退用明细 id）: {e}")
    if not detail_ids:
        detail_ids = [r.get("id") for r in rows if r.get("id")]
    log(f"    → 转生产计划（{len(detail_ids)} 行）...")
    if not hch_transfer_plan(api, order_code, detail_ids):
        log("    ✗ 转生产计划失败")
        return False
    log("    ✓ 转生产计划成功")

    # 5) 查询月生产计划 → 推送 salePlanNo
    records = hch_query_month_plan(api, order_code)
    sale_plan_nos = [r.get("salePlanNo") for r in records if r.get("salePlanNo")]
    if not sale_plan_nos:
        log(f"    ✗ 未查询到月生产计划 salePlanNo（records={len(records)}）")
        return False
    if result_sink is not None:
        result_sink.setdefault("hch", {}).setdefault(order_code, {})["salePlanNos"] = sale_plan_nos
    failed = 0
    for no in sale_plan_nos:
        try:
            if hch_push_sale_plan_no(api, no):
                log(f"    ✓ 推送销售计划号成功: {no}")
            else:
                log(f"    ✗ 推送销售计划号失败: {no}")
                failed += 1
        except Exception as e:
            log(f"    ✗ 推送销售计划号异常 [{no}]: {e}")
            failed += 1
    return failed == 0


def submit_order(token, codes, manager_token=None, hch_token=None, project_code=None,
                 delivery_days=None, now=None, stop_after=None, result_sink=None, quantities=None,
                 ware_map=None):
    """执行提交订单 + 客户端审批 + 管理端审批 + 提交排产 + HCH 订单机处理。返回 0=成功，1=失败。

    stop_after: "submit" 时仅执行到提交订单（步骤 6）即返回，用于"只下单不审批"。
    result_sink: 可选 dict，执行过程中回填结构化结果（orders / salePlanNos），供技能层使用。
    quantities: 可选，与 codes 一一对应的数量；缺省时取物料列表项的 num。
    ware_map: 可选，{顶码: wareCode} 指定分配基地（键 "*" 表示整单统一）；缺省随机。
    """
    project_code = project_code or PROJECT_CODE
    quantities_override = quantities
    api = OrderMachineApi(token)
    codes = [c.strip() for c in codes if c and c.strip()]
    if not codes:
        log("✗ 未选择任何物料顶码")
        return 1

    # ---------- 步骤 1/10：查询物料 ----------
    log("【步骤 1/10】查询物料列表 (regularPurchase/list)...")
    listed = api.post("/gree-cos-commodity/admin-api/v1/regularPurchase/list", {"saleType": "1"})
    group = None
    for g in (listed.get("data") or {}).get("groupList") or []:
        if str(g.get("id")) == str(COMMODITY_GROUP_ID):
            group = g
            break
    if group is None:
        log(f"✗ 未找到分组 id={COMMODITY_GROUP_ID}")
        return 1
    by_code = {c.get("code"): c for c in (group.get("commodityList") or [])}
    picked = []
    for code in codes:
        if code not in by_code:
            log(f"✗ 分组【{group.get('name')}】中不存在物料顶码: {code}")
            return 1
        picked.append(by_code[code])
    log(f"  ✓ 已匹配 {len(picked)} 个物料: " + ", ".join(c.get("code") for c in picked))

    quantities = [c.get("num") if c.get("num") else 1 for c in picked]
    if quantities_override is not None:
        if len(quantities_override) != len(picked):
            log(f"✗ 数量个数({len(quantities_override)})与物料个数({len(picked)})不一致")
            return 1
        try:
            quantities = [int(q) for q in quantities_override]
        except (TypeError, ValueError):
            log(f"✗ 数量必须为整数: {quantities_override}")
            return 1

    # ---------- 步骤 2/10：结算校验 ----------
    log("【步骤 2/10】结算校验 (settleCheck)...")
    check_body = {"saleType": "1", "commodityList": [
        {
            "commodityKey": c.get("commodityKey"),
            "groupId": str(group.get("id")),
            "code": c.get("code"),
            "model": c.get("model"),
            "title": c.get("title"),
            "orderQuantity": qty,
            "productionCodeId": None,
            "displayName": None,
        } for c, qty in zip(picked, quantities)
    ]}
    check = api.post("/gree-cos-commodity/admin-api/v1/regularPurchase/settleCheck", check_body)
    if not (check.get("data") or {}).get("resultFlag"):
        log("✗ 结算校验未通过: " + json.dumps(check.get("data"), ensure_ascii=False))
        return 1
    log("  ✓ 校验通过")

    # ---------- 步骤 3/10：订单结算 ----------
    log("【步骤 3/10】订单结算 (orderSettle)...")
    settle_body = {
        "commodityList": [build_settle_commodity(c, qty) for c, qty in zip(picked, quantities)],
        "orderSource": 1,
        "placeOrderType": 1,
        "orderType": 1,
    }
    settle = api.post("/gree-business-order-order/order-api/v1/client/orderInfo/orderSettle", settle_body)
    settle_data = settle.get("data") or {}
    settle_key = settle_data.get("orderSettleKey")
    if not settle_key:
        log("✗ 未取得 orderSettleKey")
        return 1
    order_type = settle_data.get("orderType")
    supplier = settle_data.get("supplier")
    consignee = settle_data.get("consignee")
    log(f"  ✓ orderSettleKey={settle_key} supplier={supplier} consignee={consignee} orderType={order_type}")

    # ---------- 步骤 4/10：结算明细 ----------
    log("【步骤 4/10】结算明细 (getOrderSettleData)...")
    try:
        detail = api.get(f"/gree-business-order-order/order-api/v1/client/orderInfo/getOrderSettleData/{settle_key}")
        groups = detail.get("data") or []
        total_rows = sum(len((g or {}).get("commodityList") or []) for g in groups)
        log(f"  ✓ 结算明细返回 {len(groups)} 组 / {total_rows} 行物料")
    except Exception as e:
        log(f"  ⚠️ 结算明细获取失败（不影响提交）: {e}")

    # ---------- 步骤 5/10：匹配项目 ----------
    log("【步骤 5/10】查询项目 (pageOpptyRelatedProject)...")
    oppty_body = {"page": 1, "size": 10, "nameAllLike": "", "listType": 1,
                  "projectStatusList": [4, 90, 93, 193]}
    oppty = api.post("/gree-business-order-oppty/oppty-api/v1/pc/opptyInfo/pageOpptyRelatedProject",
                     oppty_body, referer=BASE + "/order-center/order/create")
    records = (oppty.get("data") or {}).get("records") or []
    record = next((r for r in records if str(r.get("projectCode")) == str(project_code)), None)
    if record is None:
        log(f"✗ 未找到项目 projectCode={project_code}，可选: "
            + ", ".join(f"{r.get('projectCode')}({r.get('name')})" for r in records))
        return 1
    oppty_info = build_oppty_info(record)
    log(f"  ✓ 已匹配项目: {record.get('name')} ({record.get('projectCode')}) opptyInfoId={record.get('id')}")

    # ---------- 步骤 6/10：提交订单 ----------
    customer_name = decode_jwt_payload(token).get("outletsName")
    if not customer_name:
        log("  ⚠️ token 中未取到 outletsName，回退查询网点信息...")
        try:
            outlets = api.get("/gree-cos-channel/admin-api/v2/channelOutletsQuery/me/sales-hierarchy")
            rows = outlets.get("data") or []
            customer_name = (rows[0] or {}).get("name") if rows else None
        except Exception as e:
            log(f"  ⚠️ 网点查询失败: {e}")
    if not customer_name:
        log("✗ 无法确定 customerName（客户名称）")
        return 1

    product_lines = {c.get("productLine") for c in picked}
    if len(product_lines) > 1:
        log(f"  ⚠️ 选中物料跨多个产品线 {product_lines}，本次按第一个合并提交")
    product_line = picked[0].get("productLine")

    delivery_time = planned_delivery_time(delivery_days, now)
    submit_body = {
        "isIgnorePrompt": True,
        "placeOrderType": 1,
        "orderType": order_type if order_type is not None else 1,
        "customerName": customer_name,
        "consignee": consignee if consignee is not None else 1,
        "supplier": supplier,
        "isConsultOrder": False,
        "pickupMerchantType": PICKUP_MERCHANT_TYPE,
        "orderSubmitScene": 1,
        "fulfillmentOrderList": [{
            "productLine": product_line,
            "plannedDeliveryTime": delivery_time,
            "opptyInfo": oppty_info,
            "commodityList": [
                build_submit_commodity(c, qty, record.get("projectId"), i)
                for i, (c, qty) in enumerate(zip(picked, quantities))
            ],
            "consigneeInfo": None,
            "equipmentDeliveryAddress": None,
        }],
    }

    log("【步骤 6/10】提交订单 (orderSubmit)...")
    log(f"  计划发货时间: {delivery_time}  客户: {customer_name}")
    result = api.post("/gree-business-order-order/order-api/v1/client/orderInfo/orderSubmit",
                      submit_body, referer=BASE + "/order-center/order/create")
    result_data = result.get("data") or {}
    if not result_data.get("success"):
        log("✗ 提交失败: " + json.dumps(result_data, ensure_ascii=False)[:500])
        return 1
    orders = result_data.get("orderList") or []
    for o in orders:
        log(f"  ✓ 提交成功 订单号={o.get('code')} id={o.get('id')} 状态={o.get('orderStatus')}")
    if result_sink is not None:
        result_sink["orders"] = [
            {"code": o.get("code"), "id": o.get("id"), "orderStatus": o.get("orderStatus")}
            for o in orders
        ]
    if not orders:
        log("⚠️ 未返回订单信息，跳过审批")
        log("✓ 全部完成")
        return 0
    if stop_after == "submit":
        log("✓ 已按配置在提交订单后停止（未执行审批/排产/HCH）")
        return 0

    # ---------- 步骤 7/10：客户端审批 ----------
    log("【步骤 7/10】客户端审批 (approval/page → completeTask)...")
    failed = run_approval_phase(api, orders, "客户端审批")
    if failed:
        log(f"✗ {failed}/{len(orders)} 个订单客户端审批未完成")
        return 1

    # ---------- 步骤 8/10：管理端审批（需管理端 Token） ----------
    if not manager_token:
        log("⚠️ 未提供管理端 Token，跳过管理端审批与提交排产")
    else:
        log("【步骤 8/10】管理端审批 (approval/page → completeTask)...")
        manager_api = OrderMachineApi(manager_token)
        failed = run_approval_phase(manager_api, orders, "管理端审批", include_task_key=True,
                                    business_data_json=MANAGER_APPROVE_BUSINESS_DATA_JSON)
        if failed:
            log(f"✗ {failed}/{len(orders)} 个订单管理端审批未完成")
            return 1

        # ---------- 步骤 9/10：提交排产（管理端 Token） ----------
        log("【步骤 9/10】提交排产 (salesApproval/page → salesApproval/submit)...")
        failed = 0
        for o in orders:
            code = o.get("code")
            log(f"  → 订单 {code} 提交排产...")
            try:
                if not submit_production_schedule(manager_api, code):
                    failed += 1
            except Exception as e:
                log(f"  ✗ 提交排产异常 [{code}]: {e}")
                failed += 1
        if failed:
            log(f"✗ {failed}/{len(orders)} 个订单提交排产未完成")
            return 1

    # ---------- 步骤 10/10：HCH 订单机处理（需 HCH 系统 Token） ----------
    if not hch_token:
        log("⚠️ 未提供 HCH 系统 Token，跳过 HCH 订单机处理")
        log("✓ 全部完成")
        return 0
    log("【步骤 10/10】HCH 订单机处理 "
        "(page-detail → adjust → allocate-base → transfer-plan → sale-plan-no)...")
    hch_api = HchApi(hch_token)
    failed = 0
    for o in orders:
        code = o.get("code")
        log(f"  → 订单 {code} HCH 处理...")
        try:
            if not run_hch_phase(hch_api, code, result_sink=result_sink, ware_map=ware_map):
                failed += 1
        except Exception as e:
            log(f"  ✗ HCH 处理异常 [{code}]: {e}")
            failed += 1
    if failed:
        log(f"✗ {failed}/{len(orders)} 个订单 HCH 处理未完成")
        return 1

    log("✓ 全部完成（已提交 + 客户端审批 + 管理端审批 + 提交排产 + HCH 订单机处理）")
    return 0


def main():
    token = (os.environ.get("COMMODITY_TOKEN") or "").strip()
    manager_token = (os.environ.get("MANAGER_TOKEN") or "").strip()
    hch_token = (os.environ.get("HCH_TOKEN") or "").strip()
    codes = (os.environ.get("TOP_CODES") or "").replace(",", " ").split()
    if not token:
        log("✗ 缺少商品中心 Token")
        return 1
    try:
        return submit_order(token, codes, manager_token=manager_token, hch_token=hch_token)
    except Exception as e:
        import traceback
        log(f"✗ 执行异常: {e}")
        log(traceback.format_exc())
        return 1


if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    sys.exit(main())

---
name: hch-import-automation
description: 'Automate HCH system workflows. (1) Data import for task orders, monthly demand plans, and delay plans with multi-environment support (QA/UAT). (2) Commercial order machine (商用订单机): submit order, client/manager approval, production scheduling, and HCH order-machine processing (adjust, allocate base, transfer plan, push sale plan no) on qasalescloud + ds-oms. (3) Commercial month plan (商用月计划): create/overwrite forecast order, generate sale plan order, plan approval, monthly demand plan, HCH submit-to-sale-audit, commercial approval, and push to purchase.'
category: Automation
user-invocable: true
---

# HCH Import Automation

Automate HCH system data import with dual-token authentication, multi-environment support (QA/UAT), and comprehensive error handling. Supports task orders, monthly demand plans, and delay plans.

This skill also covers the **Commercial Order Machine (商用订单机)** and the **Commercial Month Plan (商用月计划)** end-to-end flows across two systems — see the dedicated sections near the end of this document.

## Features

- **Multi-environment support**: QA (port 9002) and UAT (port 9108)
- **Three import types**: Task orders, monthly demand, delay plans
- **Dual-token authentication**: Submitter and approver tokens
- **High inventory detection**: Automatic retry for CWMS service degradation, supports specific material import with high_inventory_id
- **Error recovery**: Automatic retry mechanism (3 retries, 20s interval) for CWMS service degradation
- **Commercial order machine**: One-shot order → approval → scheduling → HCH processing, with stage-level execution
- **Commercial month plan**: One-shot forecast order → sale plan order → approval → monthly demand plan → HCH audit → push to purchase, driven by natural language

## First-time Setup（首次配置）

技能包内**不含** `automation_config.json`，也不含 Excel 模板 —— 前者保存 Token，后者含业务数据，二者均已被忽略、不随包分发。

1. **解压到任意本地目录**：脚本、配置、模板放同一个目录即可。配置与默认模板路径都按**脚本所在目录**解析（`resolve_template_path()`），因此与当前工作目录无关。
2. **安装依赖**：
   ```bash
   pip install requests openpyxl
   ```
3. **生成配置**：**第一次运行时程序会自动生成 `automation_config.json`**（内容取自 `automation_config.example.json`），看到提示后填入自己的 Token 即可 —— **不需要手动改名**。也可以提前手动复制：
   ```bash
   cp automation_config.example.json automation_config.json
   ```
4. **自备 Excel 导入模板**：`任务单导入模板.xlsx` / `销售月需求导入模板.xlsx` / `顺延计划导入模板.xlsx` 放到技能目录下。文件名或位置不同时，改 `automation_config.json` 里的 `default_excel_files` 即可。
5. **网络要求**：需能访问内网系统（`*.gree.com`，QA `:9002` / UAT `:9108`）。请在办公网内、或连好 VPN 之后再运行。
6. 填入 Token（见下）。

> 若运行环境（如智能体沙箱）**读不到技能安装目录**，请把技能包另存到可读目录，并在调用时使用该目录下的脚本绝对路径。

### 怎么获取 Token

- 登录目标系统 → **F12 → Network** → 任一请求头里的 `Authorization: Bearer <token>`（本技能只取 `Bearer` 后面那段）
- Token 有效期较短（约 2 小时），过期需重新获取并填写
- 三个来源：qasalescloud（客户端 / 管理端）、ds-oms（HCH 系统 / 提交人 / 审批人）

### Token 填在哪（`automation_config.json`）

| 配置项 | 对应 Token |
|--------|-----------|
| `api_config.users.submitter.token` | HCH 数据导入 — 提交人 |
| `api_config.users.approver.token` | HCH 数据导入 — 审批人 |
| `api_config.order_machine.tokens.client` | 商用订单机 — 客户端（商品中心） |
| `api_config.order_machine.tokens.manager` | 商用订单机 — 管理端 |
| `api_config.order_machine.tokens.hch` | 商用订单机 — HCH 系统 |
| `api_config.plan_month.tokens.manager` | 商用月计划 — 商用管理端 |
| `api_config.plan_month.tokens.hch` | 商用月计划 — HCH 系统 |

> 优先级：**显式函数入参 > `automation_config.json`**。请勿把 Token 提交到仓库或打进分享包。
>
> 商用月计划使用**自己的** Token（`api_config.plan_month.tokens`），与商用订单机 `order_machine.tokens` **分开配置、互不借用**。

## Quick Start

### Task Order & Monthly Demand (via hch_skill.py)
```python
from hch_skill import execute_import

result = execute_import(
    submitter_token="your_submitter_token",
    approver_token="your_approver_token",
    import_type="task_order",  # or "month_demand" (not "monthly_demand")
    file_path="path/to/file.xlsx",
    check_high_inventory=False
)
```

### Delay Plan (via api_automation.py)
```python
from api_automation import run_month_delay_flow

result = run_month_delay_flow(
    submitter_token="your_submitter_token",
    approver_token="your_approver_token",
    excel_file_path="path/to/delay_plan.xlsx",
    environment="qa"  # or "uat"
)
```

## Parameters

### For hch_skill.execute_import() (Task Order & Monthly Demand)
| Parameter | Required | Description |
|-----------|----------|-------------|
| submitter_token | Yes | JWT token for submitter account |
| approver_token | Yes | JWT token for approver account |
| import_type | Yes | "task_order" or "month_demand" (NOT "delay_plan") |
| file_path | No | Local Excel file path |
| file_base64 | No | Base64 encoded file content |
| filename | No | Filename (required when using file_base64) |
| check_high_inventory | No | Enable high inventory risk detection (default: false) |
| environment | No | Target environment: "qa" (default) or "uat" |

### For api_automation.run_month_delay_flow() (Delay Plan)
| Parameter | Required | Description |
|-----------|----------|-------------|
| submitter_token | Yes | JWT token for submitter account |
| approver_token | Yes | JWT token for approver account |
| excel_file_path | No | Local Excel file path (defaults to config) |
| environment | No | Target environment: "qa" (default) or "uat" |

### For api_automation.run_month_demand_flow() (High Inventory Import)
| Additional Parameter | Description |
|---------------------|-------------|
| check_high_inventory_flag | Enable high inventory check |
| high_inventory_id | High inventory material ID (for specific material import) |
| inventory_snapshot_id | Inventory snapshot ID |
| high_inventory_remark | High inventory remark |

## Execution Flow

### Task Order & Monthly Demand (8 steps)
1. **Import File** - Upload Excel file via `import_month_sale_plan()` (planImportType=1 for task order, 0 for monthly demand)
2. **Get Latest Record** - Query latest imported record with time filtering via `get_latest_input_order_no()`
3. **Submit Production Plan** - Submit production plan via `push_production_plan()`
4. **Switch to Approver** - Switch user role to approver via `switch_user("approver")`
5. **Submit Sale Approval** - Submit to sale approval via `submit_to_sale_audit()`
6. **Get Approval ID** - Retrieve latest approval task ID via `get_latest_audit_order()`
7. **Execute Approval** - Execute approval action via `approve_sale_audit()`
8. **Push to Purchase** - Push to purchase system via `push_month_plan_to_purchase()`

### Delay Plan (4 steps) - Simplified Workflow
1. **Import Delay Plan** - Upload delay plan Excel file (`/month-delay-plan/import`)
2. **Get Latest Records** - Query records with time filtering, get the 2 most recent records
3. **Submit Sale Approval** - Batch submit to sale approval (`/month-delay-plan/release-month-production-plan/batch`)
4. **Push to Purchase** - Directly push to purchase system, **no approval required** (`/push-month-plan/sale-plan-no`)

**Note**: Delay plan workflow skips the approval process (no `approve_sale_audit()`, no user switching)

## Example Usage

### Task Order Import (QA Environment)
```python
result = execute_import(
    submitter_token="sub_token_here",
    approver_token="app_token_here",
    import_type="task_order",
    file_path="/path/to/your/excel/file.xlsx",  # User-provided file path
    environment="qa"
)

if result["success"]:
    print(f"✅ Success: {result['summary']}")
else:
    print(f"❌ Failed: {result['error']}")
```

### Monthly Demand Import (UAT Environment)
```python
result = execute_import(
    submitter_token="sub_token_here",
    approver_token="app_token_here",
    import_type="month_demand",
    file_path="/path/to/your/excel/file.xlsx",  # User-provided file path
    check_high_inventory=True,
    environment="uat"
)
```

### Delay Plan Import (QA Environment)
```python
from api_automation import run_month_delay_flow

result = run_month_delay_flow(
    submitter_token="sub_token_here",
    approver_token="app_token_here",
    excel_file_path="/path/to/your/excel/file.xlsx",  # User-provided file path
    environment="qa"
)
```

### Command Line Usage
```bash
# Task order import
python hch_cli.py task

# Monthly demand import
python hch_cli.py month

# Delay plan import
python hch_cli.py delay
```

### Base64 File Upload
```python
import base64

with open("file.xlsx", "rb") as f:
    file_b64 = base64.b64encode(f.read()).decode()

result = execute_import(
    submitter_token="sub_token_here",
    approver_token="app_token_here",
    import_type="task_order",
    file_base64=file_b64,
    filename="订单导入.xlsx"
)
```

## Response Format

Success response:
```json
{
  "success": true,
  "summary": "Success: task_order import completed. 24 records imported.",
  "import_type": "task_order",
  "steps_completed": 8,
  "total_steps": 8,
  "records_count": 24,
  "high_inventory_detected": false,
  "error": null
}
```

Failure response:
```json
{
  "success": false,
  "summary": "Import failed at step 3",
  "import_type": "task_order",
  "steps_completed": 2,
  "total_steps": 8,
  "error": "File upload failed: Invalid file format"
}
```

## Prerequisites

Install dependencies:
```bash
pip install requests openpyxl
```

## Environment Configuration

The skill supports two environments:

| Environment | Port | Base URL |
|-------------|------|----------|
| QA | 9002 | https://ds-oms.gree.com:9002 |
| UAT | 9108 | https://ds-oms.gree.com:9108 |

Configure the environment in the `automation_config.json` file or pass it as a parameter.

## Important Notes

- Token validity: Typically 2 hours, re-authenticate if expired
- File format: Must match HCH system template requirements
- Network: Ensure API endpoints are accessible
- Testing: Validate in test environment before production use
- High inventory: Supports both automatic detection and manual material specification (high_inventory_id, inventory_snapshot_id)
- **Delay plan workflow**: Simplified 4-step process, no approval required
- **Delay plan template**: Requires `./顺延计划导入模板.xlsx` file

## Commercial Order Machine (商用订单机)

One-shot commercial order flow across **two systems**, driven by natural language.

**Trigger phrases (中文触发词)**: 商用订单机、订单机下单、商用机下单、一键下单审批排产、补跑 HCH、HCH 订单机流程、转生产计划、分配基地、推送销售计划号

### Flow (up to 10 steps)

1. Query materials (`regularPurchase/list`) — 物料顶码
2. Settle check (`settleCheck`)
3. Order settle (`orderSettle`)
4. Settle detail (`getOrderSettleData`)
5. Match project (`pageOpptyRelatedProject`, default `XSF0009-26-432`)
6. Submit order (`orderSubmit`)
7. Client approval (`completeTask`, client token)
8. Manager approval (`completeTask`, manager token)
9. Submit production scheduling (`salesApproval/submit`, manager token)
10. HCH order-machine processing (HCH token): `page-detail` → `adjust` (per row) → `allocate-base` (per row — 指定的基地，未指定则随机) → `transfer-plan` → `month-production-plan/page` → push `sale-plan-no`

### Tokens & systems

| Token | System | Used for |
|-------|--------|----------|
| `client` | qasalescloud.gree.com | query top codes + submit order + client approval |
| `manager` | qasalescloud.gree.com | manager approval + submit scheduling |
| `hch` | ds-oms.gree.com:9002 (QA) / :9108 (UAT) | HCH order-machine processing |

Configure once in `automation_config.json`:
```json
"api_config": {
  "order_machine": {
    "tokens": { "client": "<JWT>", "manager": "<JWT>", "hch": "<JWT>" }
  }
}
```
Tokens may also be passed per call (explicit argument overrides config). Never echo tokens back to the user.

### Natural-language → call mapping

| User says | Call |
|-----------|------|
| 商用订单机有哪些物料可以下单？ | `list_material_top_codes()` |
| 用 KM500N1720 和 MC20700060 走一下商用订单机 | `execute_order_machine(top_codes=["KM500N1720","MC20700060"])` |
| **物料顶码MC20700060、LJ71147520执行商用订单机提交订单全流程** | `execute_order_machine(top_codes=["MC20700060","LJ71147520"], stages="all")` |
| 用 KM500N1720 下单，各下 20 个 | `execute_order_machine(top_codes=["KM500N1720"], quantities=[20])` |
| 商用订单机只下单不审批 | `execute_order_machine(top_codes=[...], stages="submit")` |
| 订单 102609102700004 补跑 HCH | `execute_order_machine(order_no="102609102700004", stages="hch")` |
| 把 102609102700004 转生产计划并推送 | `execute_order_machine(order_no="102609102700004", stages="hch")` |
| 订单 102609102700004 做审批 | `execute_order_machine(order_no="102609102700004", stages="approve")` |
| 订单 102609102700004 提交排产 | `execute_order_machine(order_no="102609102700004", stages="schedule")` |
| 在 UAT 环境跑 | add `environment="uat"` |
| 物料顶码ZN62105A 珠海基地数量10 | `execute_order_machine(instruction="物料顶码ZN62105A 珠海基地数量10")` |
| 用 LJ71147520 下单，分珠海基地，数量10 | `execute_order_machine(top_codes=["LJ71147520"], quantities=[10], bases=["珠海基地"])` |
| 这单全部放长沙基地 | `execute_order_machine(top_codes=[...], base="长沙基地")` |
| 订单 102609102700004 补跑HCH：MC20700060 分南京基地 | `execute_order_machine(order_no="102609102700004", stages="hch", base_map={"MC20700060":"南京基地"})` |

> **顶码可以从整句话里抽**：`top_codes` 既接受列表，也接受字符串
> （`"MC20700060、LJ71147520"`、`"物料顶码MC20700060,LJ71147520"` 都能解析）。
> **stage 也可以传中文短语**：`"提交订单全流程"/"全链路"/"一键"` → `all`，`"只下单不审批"` → `submit`，
> `"补跑HCH"/"转生产计划"` → `hch`。所以智能体只需把用户原句里的顶码/阶段照搬进来即可，不必硬编码翻译。

Token 不需要在对话里出现 —— 只填 `automation_config.json`，`execute_order_machine()` 会自动读取。

If a required slot is missing, ask the user (e.g. top_codes missing → show `list_material_top_codes()` options first).

### 指定分配基地（基地字典）

`allocate-base` 逐行分配，行以**顶码**标识。**未指定时从下表全量 15 个基地随机**；你也可以指定。基地名 → `wareCode` 内置字典（HCH 订单机「分配基地」下拉的**全量 15 个基地**）：

| 基地 | wareCode | 基地 | wareCode | 基地 | wareCode |
|------|----------|------|----------|------|----------|
| 金湾 | `N39` | 成都 | `N41` | 杭州 | `N47` |
| 赣州 | `N40A` | 南京 | `N45` | 长沙 | `N48` |
| 临沂 | `N40B` | 洛阳 | `N46` | 芜湖 | `N49` |
| 珠海 | `N50` | 郑州 | `N51` | 武汉 | `N52` |
| 石家庄 | `N53` | 重庆 | `N54` | 合肥 | `N55` |

- 写法支持 `珠海基地` / `珠海` / `N50`（也接受其它编码原样透传，如 `N40A`）。
- 三种给法：`bases=["珠海基地"]`（与顶码一一对应）、`base="珠海基地"`（整单统一）、`base_map={"MC20700060":"珠海基地"}`（按顶码）。
- **只指定的顶码用指定基地，其余行仍随机**；日志会标明每行是「指定」还是「随机」。
- 随机池默认是上面全量 15 个；要收窄就显式传 `ware_codes` 参数，或设环境变量 `HCH_WARE_CODES`。优先级：`ware_codes` 入参 > 环境变量 > 内置 15 个（随机池不读配置文件）。
- 无法识别的基地名 → 直接报错，不会静默随机。

### `execute_order_machine()` parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `client_token` | No | client token (default: config) |
| `top_codes` | Yes for `all`/`submit` | material top codes list |
| `manager_token` | No | manager token |
| `hch_token` | No | HCH token |
| `quantities` | No | quantities aligned with `top_codes` |
| `bases` | No | 分配基地，与 `top_codes` 一一对应（中文名或编码）；某位留空该行随机 |
| `base` | No | 整单统一基地（对所有行生效） |
| `base_map` | No | `{顶码: 基地}` 按顶码指定基地 |
| `instruction` | No | 自然语言整句，自动抽取顶码/基地/数量 |
| `environment` | No | `qa` (default) or `uat` |
| `stages` | No | `all` \| `submit` \| `approve` \| `schedule` \| `hch` (default `all`) |
| `order_no` | Yes for `approve`/`schedule`/`hch` | existing order number |

### Response format

```json
{
  "success": true,
  "message": "商用订单机执行成功",
  "data": { "stage": "all", "environment": "qa",
            "orders": [{"code": "102609107100002", "id": 9960}],
            "hch": {"102609107100002": {"rows": 3, "wareCodes": ["N48","N45","N50"],
                                        "wareMap": {"KM50001700":"N45","MC20700060":"N50"},
                                        "salePlanNos": ["YX2026091102"]}} },
  "summary": { "订单号": "102609107100002", "提交订单": "成功", "客户端审批": "通过",
               "管理端审批": "通过", "提交排产": "成功", "HCH 明细行数": 3,
               "分配基地": "KM50001700→N45, MC20700060→N50", "已推送销售计划号": "YX2026091102" },
  "timestamp": "2026-09-11T09:40:00"
}
```

### CLI

```bash
python hch_cli.py order --list                                   # 列出可下单物料
python hch_cli.py order --codes KM500N1720,MC20700060            # 一键全链路
python hch_cli.py order --stage submit --codes KM500N1720        # 只下单
python hch_cli.py order --stage hch --order-no 102609102700004   # 按订单号补跑 HCH
# 指定分配基地
python hch_cli.py order --codes ZN62105A --qty 10 --bases 珠海基地
python hch_cli.py order --codes ZN62105A --base 珠海基地
python hch_cli.py order --stage hch --order-no X --base-map MC20700060:南京基地
python hch_cli.py order --instruction "物料顶码ZN62105A 珠海基地数量10"
```

### Rules

- `adjust` uses fixed `selectionSystem=3` and `adjustRemarks="测试"`.
- `allocate-base` 的基地：指定则用指定基地，未指定则从**全量 15 个基地随机**取（可用环境变量 `HCH_WARE_CODES` 收窄随机池）。
- Missing manager/hch token → that segment is skipped (order submission and client approval still run).
- The order number comes from `orderSubmit` response `data.orderList[].code`.

## Commercial Month Plan (商用月计划)

One-shot commercial **month plan** flow across the same two systems, driven by natural language. It creates (or overwrites) the plan month's forecast order, fills each material's base quantities, generates the sale plan order, approves it, waits for the downstream monthly demand plan, then hands the source order number to HCH for submit → audit → push to purchase.

**Trigger phrases (中文触发词)**:
`商用月计划`、`月计划`、`商用计划`、`生成销售计划单`、`生成XX月的商用月计划`、`销售计划审批`、`月需求计划`、`推送采购`、`继续推送`、`补跑HCH`、`后续流程`、`计划单号`、`来源单号`

> 触发词分两类：**带月份+物料基地数量的**（跑全流程，或 `plan` 阶段）、**带计划单号的**（分段续跑，见下）。

### 分段执行（stages）

**支持分段**。若用户只给一个计划单号，技能会按前缀自动判断该跑哪一段 —— 不用再给月份和物料。

| stage | 覆盖步骤 | 需要的入参 | 说明 |
|-------|----------|------------|------|
| `all` | 1~7 | `month` + `items`（或 `instruction`） | 默认；一站到底 |
| `plan` | 1~2 | `month` + `items` | 只建单 + 生成销售计划单，返回 `planCode`（SP…） |
| `approve` | 3~4 | `plan_code`（SP…） | 销售计划审批 + 取月需求计划号，返回 `sourceOrderNo`（JHY…） |
| `hch` | 5~7 | `source_order_no`（JHY…） | HCH 提交销售审批 → 商用审批 → 推送采购 |
| `push` | 7 | `source_order_no`（JHY…） | 只推送采购（重推/补推） |

- 单号可用 `plan_no` 传（**按前缀自动识别**：`JHY…` → 来源单号 → `hch`；`SP…` → 销售计划单号 → `approve`），也可写进 `instruction` 整句里。
- **不传 `stages` 时自动判断**：给了 `source_order_no` → `hch`；给了 `plan_code` → `approve`；否则 `all`。
- `stages` 也接受中文：`生成销售计划单` → plan、`销售计划审批` → approve、`后续流程`/`补跑HCH` → hch、`推送采购` → push、`全流程` → all。
- 按阶段校验 Token：`plan`/`approve` 只要管理端；`hch`/`push` 需要管理端 + HCH。
- 只支持 `JHY…`（来源单号）和 `SP…`（销售计划单号）；`YC…`/`YX…` 不能作为续跑入口（会报错并提示）。

### Flow (7 steps)

1. Create/overwrite the forecast order for the month
   `salesForecastForecastOrder/getCreateInitData` → `/create` → `/pageList` (newest for that month = `forecastOrderId`)
   - ⚠️ 若 `create` 报 **`G6001`「当日中台快照数据不存在」**：技能会调
     `salesForecastBiDataSnapshot/getSyncInfo`（**读**各产线 `syncStatus`）→ `salesForecastBiDataSnapshot/startSync`
     （`{deptId, productLineIdList}`，**真正触发**同步，等价前端「同步数据」按钮），
     然后**中止本次流程**并返回提示 **「请确认数据同步后再执行后续程序。」**（`need_sync: true`）——等中台同步 1~2 分钟后重跑即可。
2. Fill base quantities and generate the sale plan order
   `/checkPlanAndSave` (per detail: `actualBaseQty` per base + `actualPlanQty` total) → `/generatePlanOrder` → `planCode`（SP…）
3. Approve the sale plan order
   `salesForecastPlanOrder/page?planCodeAllLike=SP…` → `activitiFlow/listUserTodoTasks` → `salesForecast/approval/completeTaskSalesPlanSubmit`
4. Get the newest monthly demand plan → `demandPlanCode`（JHY… = HCH source order no）
   审批后**下游异步生成，通常 1 分钟内**（技能会自动等待重试）
5. HCH: find the draft by source order no → submit to sale audit
   `/web/monthProductionPlanDraft/page` → `/submitToSaleAudit`
6. HCH: poll audit row status → `auditStatus` 1=新增(等待) / 20=审批中(继续)
   `auditOrder/pageList` → if 审批中, commercial approval: `psMonthlySalesDemandPlan/page?demandPlanCode=…` → `activitiFlow/listUserTodoTasks` → `activitiFlow/completeTask`
7. HCH: push every data row to purchase
   `/month-production-plan/page` → `/month-production-plan/push-month-plan/sale-plan-no`（body 是数组，多行逐条推送）

> ⏱ **HCH「新增 → 审批中」由 HCH 定时任务推进，约每 5 分钟一次** —— 第 6 步默认最长等待 10 分钟，日志会持续提示"正在等待定时任务更新审批状态"，属正常现象。

### Tokens & systems

| Token | System | Used for |
|-------|--------|----------|
| `manager` | qasalescloud.gree.com | 步骤 1~4（创建预测单 / 生成计划单 / 销售计划审批 / 查月需求计划）+ 步骤 6 商用审批 |
| `hch` | ds-oms.gree.com:9002 (QA) / :9108 (UAT) | 步骤 5~7（提交销售审批 / 查询审批状态 / 推送采购） |

配置在本流程**专属**的 `api_config.plan_month.tokens`（与商用订单机分开配置，不借用 `order_machine.tokens`）：

```json
"api_config": {
  "plan_month": {
    "tokens": { "manager": "<JWT>", "hch": "<JWT>" },
    "bases": ["N55", "N46", "N45", "N48", "N50"]
  }
}
```
Token 也可按次传入（显式入参优先于配置）。不要在对话里回显 Token。

### 可填写的基地

默认与前端一致，只有这 5 个（`api_config.plan_month.bases` 可覆盖）：

| 基地 | wareCode | 基地 | wareCode | 基地 | wareCode |
|------|----------|------|----------|------|----------|
| 合肥 | `N55` | 洛阳 | `N46` | 南京 | `N45` |
| 长沙 | `N48` | 珠海 | `N50` | | |

- 写法支持 `珠海基地` / `珠海` / `N50`。
- **同一物料可以分多个基地**（`珠海基地10、洛阳基地20`）。
- 不在上表内的基地会**直接报错**（不会静默丢弃）。

### Natural-language → call mapping

| User says | Call |
|-----------|------|
| 帮我生成2026年9月的商用月计划，其中KN850W5140 珠海基地10、洛阳基地20；KM50001700 长沙基地2 | `execute_plan_month(instruction="帮我生成2026年9月的商用月计划，其中KN850W5140 珠海基地10、洛阳基地20；KM50001700 长沙基地2")` |
| 2026年9月商用月计划：KN850W5140 珠海10、洛阳20 | `execute_plan_month(instruction="…")` |
| 先看看9月商用月计划有哪些物料顶码 | `list_plan_materials(month="2026-09")` |
| 在 UAT 环境跑 | add `environment="uat"` |
| 结构化入参 | `execute_plan_month(month="2026-09", items=[{"code":"KN850W5140","allocations":[{"base":"珠海基地","qty":10}]}])` |
| **提供计划单号 JHY20260914001，HCH 自动进行后续流程** | `execute_plan_month(source_order_no="JHY20260914001", stages="hch")` 或 `continue_plan_month(plan_no="JHY20260914001")` |
| 计划单号JHY20260914001 只推送采购 | `execute_plan_month(plan_no="JHY20260914001", stages="push")` |
| 销售计划单 SP2026090001 补审批 | `execute_plan_month(plan_code="SP2026090001", stages="approve")` |
| 2026年9月只建单生成销售计划单，不审批 | `execute_plan_month(month="2026-09", items=[…], stages="plan")` |
| 订单/计划单号写在整句里 | `execute_plan_month(instruction="计划单号JHY20260914001 继续推送采购", stages="hch")` |

> **月份、基地数量、计划单号都能从整句话里抽**：`"2026年9月"` / `"2026-09"` / `"9月"`（默认当年）、`JHY…` / `SP…` 都认。
> 智能体只需把用户原句照搬进 `instruction` 即可，不必自己拆句。

### `execute_plan_month()` parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `instruction` | 推荐 | 自然语言整句：抽月份 + 每个顶码的各基地数量 |
| `month` | 二选一 | 计划月份 `YYYY-MM`（也可从 `instruction` 抽） |
| `items` | 二选一 | `[{"code":"顶码","allocations":[{"base":"珠海基地","qty":10}, …]}]`；也支持 `bases` 字典 / `base`+`qty` 简写 |
| `manager_token` | No | 商用管理端 Token（缺省读配置） |
| `hch_token` | No | HCH 系统 Token（缺省读配置） |
| `bases` | No | 覆盖可填写基地（缺省读配置 / 内置 5 个） |
| `environment` | No | `qa` (default) or `uat` |
| `config_path` | No | 自定义配置文件路径 |
| `stages` | No | `all`(默认) \| `plan` \| `approve` \| `hch` \| `push`；不传则按单号自动判断 |
| `plan_no` | No | 计划单号 `JHY…`（→`hch`）或 `SP…`（→`approve`），按前缀自动识别 |
| `plan_code` | No | 销售计划单号 `SP…`（`stages=approve`） |
| `source_order_no` | No | HCH 来源单号 `JHY…`（`stages=hch`/`push`） |

### Response format

```json
{
  "success": true,
  "message": "商用月计划执行成功（阶段=all，月份=2026-09）",
  "data": { "stage": "all", "month": "2026-09", "environment": "qa",
            "supportedBases": ["N55","N46","N45","N48","N50"],
            "planCode": "SP2026090001", "sourceOrderNo": "JHY20260914001",
            "forecastOrderId": 123,
            "items": [{"detailId": 456, "commodityCode": "KN850W5140", "quantities": {"N50": 10, "N46": 20}}],
            "flow": {"forecastOrderCode": "YC2026090001", "planCode": "SP2026090001",
                     "sourceOrderNo": "JHY20260914001", "salePlanNo": "YX2026091401",
                     "auditOrderNo": "AU001",
                     "pushed": [{"salePlanNo": "YX2026091401", "success": true}]} },
  "summary": { "计划月份": "2026-09", "预测单号": "YC2026090001", "销售计划单": "SP2026090001",
               "来源单号": "JHY20260914001", "销售计划号": "YX2026091401",
               "审批单号": "AU001", "已推送采购": "YX2026091401" },
  "timestamp": "2026-09-14T10:00:00"
}
```

> 分段执行时 `data.stage` 就是实际跑的那一段（如 `"hch"`），`data.planCode` / `data.sourceOrderNo` 回传当前单号，供下一步串联使用。

### CLI

```bash
python hch_cli.py plan-month --month 2026-09 --instruction "KN850W5140 珠海基地10、洛阳基地20"
python hch_cli.py plan-month --month 2026-09 --codes KN850W5140 --qty 10 --base 珠海基地
python hch_cli.py plan-month --month 2026-09 --list                          # 只看该月预测单里的物料顶码
# 分段执行（--stage: all|plan|approve|hch|push）
python hch_cli.py plan-month --stage hch --source-order-no JHY20260914001     # 给来源单号跑 HCH 后续
python hch_cli.py plan-month --stage approve --plan-code SP2026090001         # 补销售计划审批
python hch_cli.py plan-month --stage push --plan-no JHY20260914001            # 只推送采购（按前缀自动识别）
```

### Rules

- 每行物料按**填写的基地数量**保存（`actualBaseQty`），未填的基地记 0，`actualPlanQty` = 各基地之和；**不做数量上限校验**。
- 预测单已存在时**继续覆盖**（带 `existForecastOrderId` 再调一次 `create`），不会报错中断。
- 步骤 4 的 `JHY…` 单是审批后**下游异步生成**的，必须取"创建时间最新的一条"；本流程会等待重试（默认 12×5s）。
- 步骤 6 的 HCH 定时任务等待较久（默认最长 10 分钟），日志会说明；不要误判为卡死。
- Token 按阶段校验：`all`/`hch` 需要 `manager` + `hch`；`plan`/`approve` 只要 `manager`；`push` 只要 `hch`。
- 分段续跑不重复执行前段：`hch` 直接用给的单号从步骤 5 开始，不会重新建单。

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Authentication failed | Verify tokens are valid and not expired |
| File upload error | Check file format matches template |
| API timeout | Verify network connectivity to HCH system |
| Approval failed | Ensure approver has correct permissions |
| 商用订单机: 缺少客户端 Token | Fill `api_config.order_machine.tokens.client` in `automation_config.json` |
| 商用订单机: 缺少物料顶码 | Call `list_material_top_codes()` and let the user pick codes |
| 商用订单机: HCH 查不到订单明细 | Order may not be scheduled yet; the skill retries (default 5×2s). Verify the order number and HCH token |
| 商用订单机: 分配基地失败 | Check `failedList` in the `allocate-base` response; warehouse code must be one of the 15 built-in bases |
| 商用订单机: 无法识别的基地 | 用内置的 15 个基地名（珠海/合肥/长沙/南京/洛阳/石家庄/金湾/赣州/临沂/成都/杭州/芜湖/郑州/武汉/重庆）或直接给 wareCode（`N50`、`N40A` 等） |
| 商用月计划: 缺少管理端 / HCH Token | 填 `api_config.plan_month.tokens.manager` 与 `.hch`（与订单机分开配置） |
| 商用月计划: 该月预测单里没有物料顶码 X | 先 `list_plan_materials(month=...)` 看可用顶码；顶码必须已在该月预测单里 |
| 商用月计划: 基地不在可填写范围内 | 只能用 合肥(N55)/洛阳(N46)/南京(N45)/长沙(N48)/珠海(N50)，或用 `api_config.plan_month.bases` 覆盖 |
| 商用月计划: 一直显示"等待定时任务更新审批状态" | HCH 定时任务约每 5 分钟一次，默认最长等 10 分钟；超时后稍后重跑即可 |
| 商用月计划: 未找到月需求计划单（步骤 4） | `JHY…` 单由审批后异步生成，本流程会等待 12×5s；仍失败说明下游较慢，稍后重跑 |
| 商用月计划: 无法识别的计划单号 | 只支持 `JHY…`（来源单号）与 `SP…`（销售计划单号）；`YC…`/`YX…` 不能作为续跑入口 |
| 商用月计划: 缺少来源单号 | `stages=hch`/`push` 需给 `JHY…`（来源单号，即月需求计划单号）；可用 `list_plan_materials` 或商用页面查 |
| 商用月计划: 缺少销售计划单号 | `stages=approve` 需给 `SP…`（`generatePlanOrder` 返回的 `planCode`） |
| 商用月计划: 报 `G6001`「当日中台快照数据不存在」 | 技能已自动 `getSyncInfo` 读状态 + `startSync` 触发同步并中止流程，返回 `need_sync: true`；等中台同步 1~2 分钟再重跑同一条命令 |
| 商用月计划: `getSyncInfo`/`startSync` 调用失败 | 不影响提示：仍会中止并提示「请确认数据同步后再执行后续程序」；可去页面点「同步数据」按钮，或确认管理端 Token 未过期 |
| 商用月计划: `getCreateInitData` 没返回 `productLines` | 说明中台快照还没同步好（前端也会这样）；技能会退回用 `getSyncInfo` 的产线列表，取不到才兜底 `productLineId=1` |

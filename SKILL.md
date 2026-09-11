---
name: hch-import-automation
description: Automate HCH system workflows. (1) Data import for task orders, monthly demand plans, and delay plans with multi-environment support (QA/UAT). (2) Commercial order machine (商用订单机): submit order, client/manager approval, production scheduling, and HCH order-machine processing (adjust, allocate base, transfer plan, push sale plan no) on qasalescloud + ds-oms.
category: Automation
user-invocable: true
---

# HCH Import Automation

Automate HCH system data import with dual-token authentication, multi-environment support (QA/UAT), and comprehensive error handling. Supports task orders, monthly demand plans, and delay plans.

This skill also covers the **Commercial Order Machine (商用订单机)** end-to-end flow across two systems — see the dedicated section near the end of this document.

## Features

- **Multi-environment support**: QA (port 9002) and UAT (port 9108)
- **Three import types**: Task orders, monthly demand, delay plans
- **Dual-token authentication**: Submitter and approver tokens
- **High inventory detection**: Automatic retry for CWMS service degradation, supports specific material import with high_inventory_id
- **Error recovery**: Automatic retry mechanism (3 retries, 20s interval) for CWMS service degradation
- **Commercial order machine**: One-shot order → approval → scheduling → HCH processing, with stage-level execution

## First-time Setup（首次配置）

`automation_config.json` **不在仓库里**（已被 `.gitignore` 忽略，且打包 zip 时也被排除），因为它保存 Token。

```bash
cp automation_config.example.json automation_config.json   # ① 生成本地配置
#                                                          # ② 编辑填入自己的 Token
pip install requests openpyxl                              # ③ 安装依赖
```

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

> 优先级：**显式函数入参 > `automation_config.json`**。请勿把 Token 提交到仓库或打进分享包。

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
10. HCH order-machine processing (HCH token): `page-detail` → `adjust` (per row) → `allocate-base` (per row, random warehouse) → `transfer-plan` → `month-production-plan/page` → push `sale-plan-no`

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

> **顶码可以从整句话里抽**：`top_codes` 既接受列表，也接受字符串
> （`"MC20700060、LJ71147520"`、`"物料顶码MC20700060,LJ71147520"` 都能解析）。
> **stage 也可以传中文短语**：`"提交订单全流程"/"全链路"/"一键"` → `all`，`"只下单不审批"` → `submit`，
> `"补跑HCH"/"转生产计划"` → `hch`。所以智能体只需把用户原句里的顶码/阶段照搬进来即可，不必硬编码翻译。

Token 不需要在对话里出现 —— 只填 `automation_config.json`，`execute_order_machine()` 会自动读取。

If a required slot is missing, ask the user (e.g. top_codes missing → show `list_material_top_codes()` options first).

### `execute_order_machine()` parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `client_token` | No | client token (default: config) |
| `top_codes` | Yes for `all`/`submit` | material top codes list |
| `manager_token` | No | manager token |
| `hch_token` | No | HCH token |
| `quantities` | No | quantities aligned with `top_codes` |
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
            "hch": {"102609107100002": {"rows": 3, "wareCodes": ["N48","N45","N50"], "salePlanNos": ["YX2026091102"]}} },
  "summary": { "订单号": "102609107100002", "提交订单": "成功", "客户端审批": "通过",
               "管理端审批": "通过", "提交排产": "成功", "HCH 明细行数": 3,
               "分配基地": "N48, N45, N50", "已推送销售计划号": "YX2026091102" },
  "timestamp": "2026-09-11T09:40:00"
}
```

### CLI

```bash
python hch_cli.py order --list                                   # 列出可下单物料
python hch_cli.py order --codes KM500N1720,MC20700060            # 一键全链路
python hch_cli.py order --stage submit --codes KM500N1720        # 只下单
python hch_cli.py order --stage hch --order-no 102609102700004   # 按订单号补跑 HCH
```

### Rules

- `adjust` uses fixed `selectionSystem=3` and `adjustRemarks="测试"`.
- `allocate-base` warehouse is randomly chosen from `N45/N46/N48/N50/N55`.
- Missing manager/hch token → that segment is skipped (order submission and client approval still run).
- The order number comes from `orderSubmit` response `data.orderList[].code`.

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
| 商用订单机: 分配基地失败 | Check `failedList` in the `allocate-base` response; warehouse code must be one of N45/N46/N48/N50/N55 |

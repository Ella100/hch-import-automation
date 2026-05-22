---
name: hch-import-automation
description: Automate HCH system data import workflow for task orders, monthly demand plans, and delay plans with multi-environment support (QA/UAT). Execute import processes including login, file upload, submission, and approval.
category: Automation
user-invocable: true
---

# HCH Import Automation

Automate HCH system data import with dual-token authentication, multi-environment support (QA/UAT), and comprehensive error handling. Supports task orders, monthly demand plans, and delay plans.

## Features

- **Multi-environment support**: QA (port 9002) and UAT (port 9108)
- **Three import types**: Task orders, monthly demand, delay plans
- **Dual-token authentication**: Submitter and approver tokens
- **High inventory detection**: Automatic retry for CWMS service degradation, supports specific material import with high_inventory_id
- **Error recovery**: Automatic retry mechanism (3 retries, 20s interval) for CWMS service degradation

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

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Authentication failed | Verify tokens are valid and not expired |
| File upload error | Check file format matches template |
| API timeout | Verify network connectivity to HCH system |
| Approval failed | Ensure approver has correct permissions |

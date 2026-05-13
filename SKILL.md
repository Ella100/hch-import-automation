---
name: hch-import-automation
description: Automate HCH system data import workflow for task orders, monthly demand plans, and delay plans with multi-environment support (QA/UAT). Execute import processes including login, file upload, submission, and approval.
---

# HCH Import Automation

Automate HCH system data import with dual-token authentication, multi-environment support (QA/UAT), and comprehensive error handling. Supports task orders, monthly demand plans, and delay plans.

## Features

- **Multi-environment support**: QA (port 9002) and UAT (port 9108)
- **Three import types**: Task orders, monthly demand, delay plans
- **Dual-token authentication**: Submitter and approver tokens
- **High inventory detection**: Automatic handling for monthly demand imports
- **Error recovery**: Automatic retry mechanism for CWMS service degradation

## Quick Start

Call the execute_import function with required tokens and file:

```python
from hch_skill import execute_import

result = execute_import(
    submitter_token="your_submitter_token",
    approver_token="your_approver_token",
    import_type="task_order",  # or "monthly_demand"
    file_path="path/to/file.xlsx"
)
```

## Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| submitter_token | Yes | JWT token for submitter account |
| approver_token | Yes | JWT token for approver account |
| import_type | Yes | "task_order", "monthly_demand", or "delay_plan" |
| file_path | No | Local Excel file path |
| file_base64 | No | Base64 encoded file content |
| filename | No | Filename (required when using file_base64) |
| check_high_inventory | No | Enable high inventory risk detection (default: false) |
| environment | No | Target environment: "qa" (default) or "uat" |

## Execution Flow

The import process executes 8 steps automatically:

1. **Login** - Authenticate with HCH system
2. **Get Upload Token** - Obtain file upload authorization
3. **Upload File** - Upload Excel template
4. **Submit for Approval** - Submit the uploaded data
5. **Get Approval ID** - Retrieve approval task ID
6. **Approve** - Execute approval action
7. **Confirm Approval** - Verify approval status
8. **Check Inventory** - Optional high inventory risk check

## Example Usage

### Task Order Import (QA Environment)
```python
result = execute_import(
    submitter_token="sub_token_here",
    approver_token="app_token_here",
    import_type="task_order",
    file_path="F:/templates/任务单导入模板.xlsx",
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
    import_type="monthly_demand",
    file_path="F:/templates/销售月需求导入模板.xlsx",
    check_high_inventory=True,
    environment="uat"
)
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
- High inventory: Detection only available for monthly_demand imports

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Authentication failed | Verify tokens are valid and not expired |
| File upload error | Check file format matches template |
| API timeout | Verify network connectivity to HCH system |
| Approval failed | Ensure approver has correct permissions |

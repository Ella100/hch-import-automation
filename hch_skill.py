#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HCH系统API自动化 - QClaw Skill接口

这个模块提供简洁的对话式接口，支持通过QClaw等AI助手直接调用。
用户可以通过自然语言对话执行导入操作，无需手动配置。

使用示例：
    from hch_skill import execute_import
    
    # 方式1：直接传入参数
    result = execute_import(
        submitter_token="xxx",
        approver_token="yyy", 
        import_type="task_order",  # 或 "month_demand"
        file_path="./data.xlsx"
    )
    
    # 方式2：使用base64编码的文件内容
    result = execute_import(
        submitter_token="xxx",
        approver_token="yyy",
        import_type="task_order",
        file_base64="UEsDBBQAAAAI...",  # base64编码的Excel文件
        filename="my_data.xlsx"
    )
"""

import os
import sys
import json
import base64
import tempfile
from typing import Dict, Any, Optional
from datetime import datetime

# 导入核心自动化类
from api_automation import HCHAPIAutomation


class HCHSkillExecutor:
    """HCH系统API自动化执行器 - 专为对话式调用设计"""
    
    def __init__(self):
        self.temp_files = []  # 跟踪临时文件，便于清理
        
    def execute_import(
        self,
        submitter_token: str,
        approver_token: str,
        import_type: str,
        file_path: Optional[str] = None,
        file_base64: Optional[str] = None,
        filename: Optional[str] = None,
        check_high_inventory: bool = False,
        environment: str = "qa"
    ) -> Dict[str, Any]:
        """
        执行导入操作（主入口函数）
        
        Args:
            submitter_token: 提交者token
            approver_token: 审批者token
            import_type: 导入类型 ("task_order" 或 "month_demand")
            file_path: Excel文件路径（如果文件已在服务器上）
            file_base64: base64编码的文件内容（如果通过对话上传）
            filename: 文件名（与file_base64配合使用）
            check_high_inventory: 是否检查高库存
            environment: 目标环境 "qa"（默认）或 "uat"
            
        Returns:
            执行结果字典，包含success、message、data等字段
        """
        try:
            # 步骤1：验证参数
            validation_result = self._validate_params(
                submitter_token, approver_token, import_type, 
                file_path, file_base64, filename
            )
            if not validation_result["valid"]:
                return {
                    "success": False,
                    "message": validation_result["error"],
                    "timestamp": datetime.now().isoformat()
                }
            
            # 步骤2：处理文件
            actual_file_path = self._handle_file(file_path, file_base64, filename)
            if not actual_file_path:
                return {
                    "success": False,
                    "message": "文件处理失败",
                    "timestamp": datetime.now().isoformat()
                }
            
            # 步骤3：确定planImportType
            plan_import_type = 1 if import_type == "task_order" else 0
            import_type_name = "任务单" if import_type == "task_order" else "月需求"
            
            print(f"\n{'='*60}")
            print(f"开始执行{import_type_name}导入")
            print(f"{'='*60}")
            print(f"文件路径: {actual_file_path}")
            print(f"计划类型: planImportType={plan_import_type}")
            print(f"目标环境: {environment.upper()}")
            
            # 步骤4：创建自动化实例并执行完整流程
            result = self._run_full_flow(
                submitter_token=submitter_token,
                approver_token=approver_token,
                file_path=actual_file_path,
                plan_import_type=plan_import_type,
                check_high_inventory=check_high_inventory,
                environment=environment
            )
            
            # 步骤5：清理临时文件
            self._cleanup_temp_files()
            
            return result
            
        except Exception as e:
            self._cleanup_temp_files()
            return {
                "success": False,
                "message": f"执行异常: {str(e)}",
                "error_details": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def _validate_params(
        self,
        submitter_token: str,
        approver_token: str,
        import_type: str,
        file_path: Optional[str],
        file_base64: Optional[str],
        filename: Optional[str]
    ) -> Dict[str, Any]:
        """验证输入参数"""
        errors = []
        
        if not submitter_token or not submitter_token.strip():
            errors.append("缺少提交者token")
        
        if not approver_token or not approver_token.strip():
            errors.append("缺少审批者token")
        
        if import_type not in ["task_order", "month_demand"]:
            errors.append(f"无效的导入类型: {import_type}，应为 'task_order' 或 'month_demand'")
        
        if not file_path and not file_base64:
            errors.append("必须提供文件路径或文件内容（base64）")
        
        if file_base64 and not filename:
            errors.append("提供base64文件时必须指定文件名")
        
        if errors:
            return {
                "valid": False,
                "error": "; ".join(errors)
            }
        
        return {"valid": True}
    
    def _handle_file(
        self,
        file_path: Optional[str],
        file_base64: Optional[str],
        filename: Optional[str]
    ) -> Optional[str]:
        """
        处理文件输入，返回实际可用的文件路径
        
        支持两种方式：
        1. 直接使用已有的文件路径
        2. 将base64编码的内容保存为临时文件
        """
        if file_path:
            # 方式1：直接使用已有文件
            if os.path.exists(file_path):
                print(f"✓ 使用现有文件: {file_path}")
                return file_path
            else:
                print(f"✗ 文件不存在: {file_path}")
                return None
        
        elif file_base64:
            # 方式2：从base64创建临时文件
            try:
                # 解码base64
                file_data = base64.b64decode(file_base64)
                
                # 创建临时文件
                temp_dir = os.path.join(os.path.dirname(__file__), "temp_uploads")
                os.makedirs(temp_dir, exist_ok=True)
                
                # 使用时间戳生成唯一文件名
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                temp_filename = f"{timestamp}_{filename}"
                temp_file_path = os.path.join(temp_dir, temp_filename)
                
                # 写入文件
                with open(temp_file_path, 'wb') as f:
                    f.write(file_data)
                
                self.temp_files.append(temp_file_path)
                print(f"✓ 已保存上传文件到: {temp_file_path}")
                print(f"  文件大小: {len(file_data)} bytes")
                
                return temp_file_path
                
            except Exception as e:
                print(f"✗ 处理base64文件失败: {str(e)}")
                return None
        
        return None
    
    def _run_full_flow(
        self,
        submitter_token: str,
        approver_token: str,
        file_path: str,
        plan_import_type: int,
        check_high_inventory: bool = False,
        environment: str = "qa"
    ) -> Dict[str, Any]:
        """
        执行完整的导入流程
        
        流程包括：
        1. 提交用户导入文件
        2. 获取最新记录
        3. 提交排产
        4. 切换到审批用户
        5. 提交销售审批
        6. 获取审批记录
        7. 审批操作
        8. 重推采购
        """
        import time
        
        try:
            # ===== 阶段1：提交用户操作 =====
            print("\n【阶段1】提交用户 - 导入文件")
            submitter_api = HCHAPIAutomation(user_role="submitter", environment=environment)
            
            # 动态设置token（不依赖配置文件）
            submitter_api.token = submitter_token
            submitter_api.headers["Authorization"] = f"Bearer {submitter_token}"
            submitter_api.session.headers.update(submitter_api.headers)
            
            # 执行导入
            import_result = submitter_api.import_month_sale_plan(
                file_path=file_path,
                plan_import_type=plan_import_type,
                check_high_inventory_flag=check_high_inventory,
                max_retries=3,
                retry_interval=20
            )
            
            if not import_result.get("success"):
                return {
                    "success": False,
                    "message": f"导入失败: {import_result.get('msg', '未知错误')}",
                    "stage": "import",
                    "data": import_result,
                    "timestamp": datetime.now().isoformat()
                }
            
            print("✓ 导入成功")
            
            # 等待数据写入
            print("\n等待3秒，确保数据写入数据库...")
            time.sleep(3)
            
            # ===== 阶段2：获取最新记录 =====
            print("\n【阶段2】获取最新导入记录")
            plan_type = "21,22" if plan_import_type == 1 else "1,12"
            
            latest_record = submitter_api.get_latest_input_order_no(
                current=1,
                size=10,
                plan_type=plan_type
            )
            
            if not latest_record.get("success"):
                return {
                    "success": False,
                    "message": f"获取最新记录失败: {latest_record.get('msg')}",
                    "stage": "get_latest_record",
                    "data": latest_record,
                    "timestamp": datetime.now().isoformat()
                }
            
            sale_plan_no = latest_record["salePlanNo"]
            input_order_no = latest_record["inputOrderNo"]
            
            print(f"✓ 获取成功")
            print(f"  salePlanNo: {sale_plan_no}")
            print(f"  inputOrderNo: {input_order_no}")
            
            # ===== 阶段3：提交排产 =====
            print("\n【阶段3】提交排产")
            push_result = submitter_api.push_production_plan(input_order_no)
            
            if push_result.get("code") != 0:
                return {
                    "success": False,
                    "message": f"提交排产失败: {push_result.get('msg')}",
                    "stage": "push_production",
                    "data": push_result,
                    "timestamp": datetime.now().isoformat()
                }
            
            print("✓ 提交排产成功")
            
            # 等待排产数据生成
            print("\n等待3秒，确保排产数据生成...")
            time.sleep(3)
            
            # ===== 阶段4：审批用户操作 =====
            print("\n【阶段4】审批用户 - 提交销售审批")
            approver_api = HCHAPIAutomation(user_role="approver", environment=environment)
            
            # 动态设置token
            approver_api.token = approver_token
            approver_api.headers["Authorization"] = f"Bearer {approver_token}"
            approver_api.session.headers.update(approver_api.headers)
            
            submit_audit_result = approver_api.submit_to_sale_audit(
                sale_plan_no=sale_plan_no,
                input_order_no=input_order_no,
                hoh_month=None,
                hoh_year=None
            )
            
            if submit_audit_result.get("code") != 0:
                return {
                    "success": False,
                    "message": f"提交销售审批失败: {submit_audit_result.get('msg')}",
                    "stage": "submit_audit",
                    "data": submit_audit_result,
                    "timestamp": datetime.now().isoformat()
                }
            
            print("✓ 提交销售审批成功")
            
            # 等待审批记录生成
            print("\n等待3秒，确保审批记录生成...")
            time.sleep(3)
            
            # ===== 阶段5：获取审批记录 =====
            print("\n【阶段5】获取审批记录")
            audit_record = approver_api.get_latest_audit_order(
                current=1,
                size=10
            )
            
            if not audit_record.get("success"):
                return {
                    "success": False,
                    "message": f"获取审批记录失败: {audit_record.get('msg')}",
                    "stage": "get_audit_record",
                    "data": audit_record,
                    "timestamp": datetime.now().isoformat()
                }
            
            audit_order_no = audit_record["auditOrderNo"]
            audit_input_order_no = audit_record.get("inputOrderNo", "")
            
            print(f"✓ 获取审批记录成功")
            print(f"  auditOrderNo: {audit_order_no}")
            
            # ===== 阶段6：执行审批 =====
            print("\n【阶段6】执行审批操作")
            approve_result = approver_api.approve_sale_audit(
                audit_order_no=audit_order_no,
                sale_plan_no=sale_plan_no,
                input_order_no=audit_input_order_no
            )
            
            if approve_result.get("code") != 0:
                return {
                    "success": False,
                    "message": f"审批操作失败: {approve_result.get('msg')}",
                    "stage": "approve",
                    "data": approve_result,
                    "timestamp": datetime.now().isoformat()
                }
            
            print("✓ 审批操作成功")
            
            # ===== 阶段7：重推采购 =====
            print("\n【阶段7】重推采购")
            push_purchase_result = approver_api.push_month_plan_to_purchase(
                sale_plan_no=sale_plan_no
            )
            
            if push_purchase_result.get("code") != 0:
                return {
                    "success": False,
                    "message": f"重推采购失败: {push_purchase_result.get('msg')}",
                    "stage": "push_purchase",
                    "data": push_purchase_result,
                    "timestamp": datetime.now().isoformat()
                }
            
            print("✓ 重推采购成功")
            
            # ===== 完成 =====
            print(f"\n{'='*60}")
            print("✓ 整个流程执行成功!")
            print(f"{'='*60}")
            
            return {
                "success": True,
                "message": f"{('任务单' if plan_import_type == 1 else '月需求')}导入流程执行成功",
                "data": {
                    "salePlanNo": sale_plan_no,
                    "inputOrderNo": input_order_no,
                    "auditOrderNo": audit_order_no,
                    "importResult": import_result,
                    "pushProductionResult": push_result,
                    "submitAuditResult": submit_audit_result,
                    "auditRecord": audit_record,
                    "approveResult": approve_result,
                    "pushPurchaseResult": push_purchase_result
                },
                "summary": {
                    "导入类型": "任务单" if plan_import_type == 1 else "月需求",
                    "销售计划编号": sale_plan_no,
                    "输入单号": input_order_no,
                    "审批单号": audit_order_no,
                    "状态": "已完成"
                },
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"流程执行异常: {str(e)}",
                "error_details": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def _cleanup_temp_files(self):
        """清理临时文件"""
        for temp_file in self.temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                    print(f"✓ 已清理临时文件: {temp_file}")
            except Exception as e:
                print(f"⚠️ 清理临时文件失败: {temp_file}, 错误: {str(e)}")
        
        self.temp_files.clear()


# ===== 便捷函数（供QClaw直接调用）=====

def execute_import(
    submitter_token: str,
    approver_token: str,
    import_type: str,
    file_path: Optional[str] = None,
    file_base64: Optional[str] = None,
    filename: Optional[str] = None,
    check_high_inventory: bool = False,
    environment: str = "qa"
) -> Dict[str, Any]:
    """
    执行导入操作的便捷函数
    
    这是QClaw调用的主要入口点
    
    Args:
        submitter_token: 提交者token
        approver_token: 审批者token
        import_type: 导入类型 ("task_order" 或 "month_demand")
        file_path: 文件路径（可选）
        file_base64: base64编码的文件内容（可选）
        filename: 文件名（与file_base64配合使用）
        check_high_inventory: 是否检查高库存
        environment: 目标环境 "qa"（默认）或 "uat"
        
    Returns:
        执行结果字典
    """
    executor = HCHSkillExecutor()
    return executor.execute_import(
        submitter_token=submitter_token,
        approver_token=approver_token,
        import_type=import_type,
        file_path=file_path,
        file_base64=file_base64,
        filename=filename,
        check_high_inventory=check_high_inventory,
        environment=environment
    )


def get_skill_info() -> Dict[str, Any]:
    """
    获取Skill信息（供QClaw查询）
    
    Returns:
        Skill元数据
    """
    return {
        "name": "hch_import_automation",
        "version": "1.0.0",
        "description": "HCH系统API自动化导入技能，支持任务单和月需求的完整导入流程",
        "author": "HCH Automation Team",
        "functions": [
            {
                "name": "execute_import",
                "description": "执行HCH系统导入操作（任务单或月需求）",
                "parameters": {
                    "submitter_token": {
                        "type": "string",
                        "required": True,
                        "description": "提交者的认证token"
                    },
                    "approver_token": {
                        "type": "string",
                        "required": True,
                        "description": "审批者的认证token"
                    },
                    "import_type": {
                        "type": "string",
                        "required": True,
                        "enum": ["task_order", "month_demand"],
                        "description": "导入类型：task_order=任务单导入，month_demand=月需求导入"
                    },
                    "file_path": {
                        "type": "string",
                        "required": False,
                        "description": "Excel文件的本地路径（如果文件已在服务器上）"
                    },
                    "file_base64": {
                        "type": "string",
                        "required": False,
                        "description": "base64编码的Excel文件内容（如果通过对话上传文件）"
                    },
                    "filename": {
                        "type": "string",
                        "required": False,
                        "description": "文件名（当使用file_base64时必须提供）"
                    },
                    "check_high_inventory": {
                        "type": "boolean",
                        "required": False,
                        "default": False,
                        "description": "是否检查高库存标志"
                    }
                }
            }
        ],
        "examples": [
            {
                "description": "导入任务单（使用文件路径）",
                "code": """
result = execute_import(
    submitter_token="your_submitter_token",
    approver_token="your_approver_token",
    import_type="task_order",
    file_path="./templates/任务单导入模板.xlsx"
)
                """
            },
            {
                "description": "导入月需求（使用base64文件）",
                "code": """
result = execute_import(
    submitter_token="your_submitter_token",
    approver_token="your_approver_token",
    import_type="month_demand",
    file_base64="UEsDBBQAAAAI...",  # base64编码的Excel
    filename="demand.xlsx"
)
                """
            }
        ]
    }


# ===== 命令行测试入口 =====

if __name__ == "__main__":
    """测试Skill功能"""
    print("="*60)
    print("HCH Skill - 测试模式")
    print("="*60)
    
    # 显示Skill信息
    skill_info = get_skill_info()
    print(f"\nSkill名称: {skill_info['name']}")
    print(f"版本: {skill_info['version']}")
    print(f"描述: {skill_info['description']}")
    
    print("\n可用函数:")
    for func in skill_info['functions']:
        print(f"  - {func['name']}: {func['description']}")
    
    print("\n提示: 这是一个库模块，请通过Python代码调用execute_import函数")
    print("示例:")
    print("  from hch_skill import execute_import")
    print("  result = execute_import(...)")

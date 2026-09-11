#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HCH API自动化命令行工具
提供简洁的命令行接口来执行HCH系统API操作
"""

import sys
import os
import json
from api_automation import HCHAPIAutomation, run_task_order_flow, run_month_demand_flow, run_month_delay_flow, resolve_template_path
from order_machine_skill import (
    execute_order_machine,
    list_material_top_codes,
    get_skill_info as get_order_skill_info,
)


def print_banner():
    """打印欢迎横幅"""
    print("="*60)
    print("       HCH系统API自动化操作工具")
    print("="*60)


def show_help():
    """显示帮助信息"""
    print("\n用法:")
    print("  python hch_cli.py [命令] [选项]")
    print("\n可用命令:")
    print("  task        - 执行任务单导入流程")
    print("  month       - 执行月需求导入流程")
    print("  delay       - 执行顺延计划导入流程")
    print("  order       - 执行商用订单机流程（下单/审批/排产/HCH）")
    print("  import      - 仅执行导入操作")
    print("  status      - 查询导入状态")
    print("  help        - 显示此帮助信息")
    print("\n示例:")
    print("  python hch_cli.py task")
    print("  python hch_cli.py month")
    print("  python hch_cli.py delay")
    print("  python hch_cli.py import --file my_file.xlsx --type 1")
    print("  python hch_cli.py order --list")
    print("  python hch_cli.py order --codes KM500N1720,MC20700060")
    print("  python hch_cli.py order --stage submit --codes KM500N1720")
    print("  python hch_cli.py order --stage hch --order-no 102609102700004")


def execute_import_only(file_path=None, plan_type=1, check_inventory=False):
    """仅执行导入操作"""
    api = HCHAPIAutomation()
    
    if file_path is None:
        # 使用默认文件
        if plan_type == 1:
            file_path = resolve_template_path("./任务单导入模板.xlsx")
        else:
            file_path = resolve_template_path("./销售月需求导入模板.xlsx")
    
    print(f"\n执行导入操作:")
    print(f"  文件: {file_path}")
    print(f"  类型: {'任务单' if plan_type == 1 else '月需求'}")
    
    result = api.import_month_sale_plan(
        file_path=file_path,
        plan_import_type=plan_type,
        check_high_inventory_flag=check_inventory
    )
    
    return result


def main():
    """主函数"""
    if len(sys.argv) < 2:
        print_banner()
        show_help()
        return
    
    command = sys.argv[1].lower()
    
    if command == "help":
        print_banner()
        show_help()
        
    elif command == "task":
        print_banner()
        print("\n执行任务单导入流程...")
        result = run_task_order_flow()
        if result:
            print("\n✓ 任务单流程执行成功!")
        else:
            print("\n✗ 任务单流程执行失败!")
            
    elif command == "month":
        print_banner()
        print("\n执行月需求导入流程...")
        result = run_month_demand_flow()
        if result:
            print("\n✓ 月需求流程执行成功!")
        else:
            print("\n✗ 月需求流程执行失败!")
            
    elif command == "delay":
        print_banner()
        print("\n执行顺延计划导入流程...")
        result = run_month_delay_flow()
        if result:
            print("\n✓ 顺延计划流程执行成功!")
        else:
            print("\n✗ 顺延计划流程执行失败!")
            
    elif command == "order":
        print_banner()
        codes = None
        stage = "all"
        order_no = None
        environment = "qa"
        quantities = None
        do_list = False

        i = 2
        while i < len(sys.argv):
            arg = sys.argv[i]
            if arg == "--codes" and i + 1 < len(sys.argv):
                codes = [c.strip() for c in sys.argv[i + 1].split(",") if c.strip()]
                i += 2
            elif arg == "--stage" and i + 1 < len(sys.argv):
                stage = sys.argv[i + 1]
                i += 2
            elif arg == "--order-no" and i + 1 < len(sys.argv):
                order_no = sys.argv[i + 1]
                i += 2
            elif arg == "--env" and i + 1 < len(sys.argv):
                environment = sys.argv[i + 1]
                i += 2
            elif arg == "--qty" and i + 1 < len(sys.argv):
                quantities = [int(x) for x in sys.argv[i + 1].split(",") if x.strip()]
                i += 2
            elif arg == "--list":
                do_list = True
                i += 1
            else:
                i += 1

        if do_list or (not codes and not order_no):
            result = list_material_top_codes(environment=environment)
        else:
            result = execute_order_machine(
                top_codes=codes, quantities=quantities, stages=stage,
                order_no=order_no, environment=environment)

        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result.get("success"):
            sys.exit(1)

    elif command == "import":
        print_banner()
        # 解析导入参数
        file_path = None
        plan_type = 1
        check_inventory = False
        
        i = 2
        while i < len(sys.argv):
            arg = sys.argv[i]
            if arg == "--file" and i + 1 < len(sys.argv):
                file_path = sys.argv[i + 1]
                i += 2
            elif arg == "--type" and i + 1 < len(sys.argv):
                plan_type = int(sys.argv[i + 1])
                i += 2
            elif arg == "--check-inventory":
                check_inventory = True
                i += 1
            else:
                i += 1
        
        result = execute_import_only(file_path, plan_type, check_inventory)
        if result.get("success"):
            print("\n✓ 导入操作成功!")
        else:
            print(f"\n✗ 导入操作失败: {result.get('msg', '未知错误')}")
            
    elif command == "status":
        print_banner()
        print("\n状态查询功能待实现")
        print("提示: 可以通过查看日志或联系系统管理员获取状态信息")
        
    else:
        print(f"未知命令: {command}")
        show_help()


if __name__ == "__main__":
    main()
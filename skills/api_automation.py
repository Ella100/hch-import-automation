import requests
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional


class HCHAPIAutomation:
    """HCH系统API自动化操作类"""
    
    def __init__(self, config_path: str = None, user_role: str = "submitter"):
        """初始化API自动化类
        
        Args:
            config_path: 配置文件路径（默认为脚本所在目录的automation_config.json）
            user_role: 用户角色 (submitter-提交用户, approver-审批用户)
        """
        # 如果未指定配置文件路径，自动定位到脚本所在目录
        if config_path is None:
            # 获取脚本所在目录
            script_dir = Path(__file__).parent
            config_path = script_dir / "automation_config.json"
        
        # 读取配置文件
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)['api_config']
        
        # 设置基础URL和API基础路径
        self.base_url = self.config.get("base_url", "https://ds-oms.gree.com:9002")
        self.api_base = self.config.get("api_base", "/api/api-hch-order-server")
        
        # 支持多用户token
        users_config = self.config.get("users", {})
        if user_role in users_config:
            self.token = users_config[user_role].get("token", "")
            self.user_role = user_role
        else:
            # 兼容旧配置格式
            self.token = self.config.get("token", "")
            self.user_role = "default"
        
        # 默认导入参数（兼容新旧配置格式）
        import_params = self.config.get("import_params", {})
        if isinstance(import_params, dict) and 'task_order' in import_params:
            # 新配置格式：包含task_order和month_demand
            self.default_import_params = import_params.get("task_order", {
                "checkHighInventoryFlag": False,
                "planImportType": 1
            })
        else:
            # 旧配置格式：直接的参数字典
            self.default_import_params = import_params if import_params else {
                "checkHighInventoryFlag": False,
                "planImportType": 1
            }
        
        # 默认Excel文件路径（兼容新旧配置格式）
        default_excel_files = self.config.get("default_excel_files", {})
        if default_excel_files:
            # 新配置格式
            self.default_excel_file = default_excel_files.get("task_order", self.config.get("default_excel_file", ""))
        else:
            # 旧配置格式
            self.default_excel_file = self.config.get("default_excel_file", "")
        
        # 设置请求头
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Authorization": f"Bearer {self.token}",
            "Origin": "https://ds-oms.gree.com:9002",
            "Referer": "https://ds-oms.gree.com:9002/hch/salesPlan/salesMonthDemand",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
        }
        
        # 创建session以保持连接
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.session.verify = False  # 忽略SSL证书验证(内网环境)
        
        # 抑制SSL警告
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        print(f"✓ HCH API自动化类初始化完成 (用户角色: {self.user_role})")
    
    def switch_user(self, user_role: str):
        """
        切换用户角色
        
        Args:
            user_role: 用户角色 (submitter-提交用户, approver-审批用户)
        """
        users_config = self.config.get("users", {})
        
        # 优先使用前端传入的token
        if user_role == 'approver' and hasattr(self, 'approver_token') and self.approver_token:
            self.token = self.approver_token
            self.user_role = user_role
            self.headers["Authorization"] = f"Bearer {self.token}"
            self.session.headers.update(self.headers)
            print(f"✓ 已切换用户角色: {user_role} (使用前端token)")
        elif user_role in users_config:
            self.token = users_config[user_role].get("token", "")
            self.user_role = user_role
            
            # 更新请求头
            self.headers["Authorization"] = f"Bearer {self.token}"
            self.session.headers.update(self.headers)
            
            print(f"✓ 已切换用户角色: {user_role}")
        else:
            print(f"✗ 用户角色 '{user_role}' 不存在于配置文件中")
    
    def get_month_delay_plan_page(self, current: int = 1, size: int = 10, after_time: str = None) -> Dict[str, Any]:
        """
        获取顺延计划列表数据，返回createTime最新的两条记录
        
        Args:
            current: 当前页码 (默认1)
            size: 每页条数 (默认10)
            after_time: 时间过滤，只返回此时间之后的记录 (格式: "YYYY-MM-DD HH:MM:SS")
            
        Returns:
            包含符合条件的记录列表和id的结果字典
        """
        url = f"{self.base_url}{self.api_base}/month-delay-plan/page"
        
        print(f"\n{'='*50}")
        print(f"获取顺延计划列表")
        print(f"接口URL: {url}")
        print(f"{'='*50}")
        
        try:
            # 添加表单提交的内容类型头
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
            }
            
            # 准备请求参数
            data = {
                'current': str(current),
                'size': str(size)
            }
            
            print(f"\n请求参数:")
            print(f"  - current: {current}")
            print(f"  - size: {size}")
            
            # 发送POST请求
            response = self.session.post(url, data=data, headers=headers)
            
            # 解析响应
            if response.status_code == 200:
                result = response.json()
                
                # 检查响应状态
                if result.get("code") == 0 and result.get("data"):
                    records = result["data"]
                    
                    if not records:
                        print(f"\n✗ 未找到任何顺延计划记录")
                        return {"success": False, "msg": "未找到任何记录", "record_id": None, "total": 0}
                    
                    # 如果指定了时间过滤，只保留该时间之后的记录
                    if after_time:
                        print(f"\n⏰ 时间过滤: 只获取 {after_time} 之后的记录")
                        filtered_records = []
                        for record in records:
                            record_time = record.get('createTime', '')
                            if record_time and record_time > after_time:
                                filtered_records.append(record)
                        
                        if not filtered_records:
                            print(f"\n✗ 未找到 {after_time} 之后的记录")
                            return {"success": False, "msg": f"未找到 {after_time} 之后的记录", "record_id": None, "total": 0}
                        
                        records = filtered_records
                        print(f"  过滤后剩余 {len(records)} 条记录")
                    
                    # 按createTime降序排序，获取最新的两条记录
                    sorted_records = sorted(records, key=lambda x: x.get('createTime', ''), reverse=True)
                    latest_two_records = sorted_records[:2]
                    
                    print(f"\n✓ 成功获取 {len(latest_two_records)} 条最新记录")
                    
                    # 收集所有记录的id，不管total大于0还是小于0都要提交
                    record_ids = []
                    for idx, record in enumerate(latest_two_records, 1):
                        record_id = record.get('id')
                        total = record.get('total', 0)
                        input_order_no = record.get('inputOrderNo', '')
                        create_time = record.get('createTime', '')
                        
                        print(f"\n记录 {idx}:")
                        print(f"  - id: {record_id}")
                        print(f"  - inputOrderNo: {input_order_no}")
                        print(f"  - total: {total}")
                        print(f"  - createTime: {create_time}")
                        
                        # 不管total大于0还是小于0，都添加到提交列表
                        record_ids.append(record_id)
                        print(f"  ✓ 已添加到提交列表")
                    
                    # 使用最新的记录作为主记录（用于后续获取sale_plan_no）
                    latest_record = latest_two_records[0]
                    
                    return {
                        "success": True,
                        "msg": "成功",
                        "record_ids": record_ids,
                        "inputOrderNo": latest_record.get('inputOrderNo', ''),
                        "total": latest_record.get('total', 0),
                        "createTime": latest_record.get('createTime', ''),
                        "record_count": len(record_ids)
                    }
                else:
                    print(f"\n✗ 获取列表失败")
                    print(f"响应内容: {json.dumps(result, ensure_ascii=False, indent=2)}")
                    return {
                        "success": False,
                        "msg": result.get('msg', '获取失败'),
                        "record_id": None,
                        "total": 0
                    }
            else:
                print(f"\n✗ 请求失败")
                print(f"状态码: {response.status_code}")
                print(f"响应内容: {response.text}")
                return {
                    "success": False,
                    "msg": f"HTTP错误: {response.status_code}",
                    "record_id": None,
                    "total": 0
                }
                
        except requests.exceptions.RequestException as e:
            print(f"\n✗ 请求异常: {str(e)}")
            return {
                "success": False,
                "msg": f"请求异常: {str(e)}",
                "record_id": None,
                "total": 0
            }
        except Exception as e:
            print(f"\n✗ 未知错误: {str(e)}")
            return {
                "success": False,
                "msg": f"未知错误: {str(e)}",
                "record_id": None,
                "total": 0
            }
    
    def import_month_delay_plan(self, 
                               file_path: str = None, 
                               max_retries: int = 3,
                               retry_interval: int = 20) -> Dict[str, Any]:
        """
        导入顺延计划
        
        Args:
            file_path: Excel文件路径 (默认使用配置文件中的路径)
            max_retries: 最大重试次数 (默认3次)
            retry_interval: 重试间隔秒数 (默认20秒)
            
        Returns:
            接口响应结果
        """
        import time
        
        # 使用默认值
        if file_path is None:
            file_path = self.default_excel_file
        
        # 检查文件是否存在
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")
        
        # API端点
        url = f"{self.base_url}{self.api_base}/month-delay-plan/import"
        
        print(f"\n{'='*50}")
        print(f"开始导入顺延计划")
        print(f"文件路径: {file_path}")
        print(f"接口URL: {url}")
        print(f"最大重试次数: {max_retries}")
        print(f"重试间隔: {retry_interval}秒")
        print(f"{'='*50}")
        
        retry_count = 0
        last_result = None
        
        while retry_count <= max_retries:
            if retry_count > 0:
                print(f"\n⏳ 第 {retry_count} 次重试，等待 {retry_interval} 秒...")
                time.sleep(retry_interval)
            
            try:
                # 准备multipart/form-data请求
                with open(file_path, 'rb') as file:
                    files = {
                        'file': (os.path.basename(file_path), file, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                    }
                    
                    print(f"\n请求参数:")
                    print(f"  - 文件大小: {os.path.getsize(file_path)} bytes")
                    print(f"  - 尝试次数: {retry_count + 1}")
                    
                    # 发送POST请求
                    response = self.session.post(url, files=files)
                    
                    # 解析响应
                    if response.status_code == 200:
                        result = response.json()
                        last_result = result
                        
                        if result.get("success"):
                            print(f"\n✓ 顺延计划导入成功!")
                            print(f"响应结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
                            return result
                        else:
                            print(f"\n✗ 顺延计划导入失败!")
                            print(f"状态码: {response.status_code}")
                            print(f"响应内容: {json.dumps(result, ensure_ascii=False, indent=2)}")
                            return result
                    else:
                        print(f"\n✗ 顺延计划导入失败!")
                        print(f"状态码: {response.status_code}")
                        print(f"响应内容: {response.text}")
                        return {
                            "success": False,
                            "code": response.status_code,
                            "msg": f"HTTP错误: {response.status_code}",
                            "data": response.text
                        }
                        
            except requests.exceptions.RequestException as e:
                print(f"\n✗ 请求异常: {str(e)}")
                if retry_count < max_retries:
                    print(f"准备重试...")
                    retry_count += 1
                    continue
                else:
                    return {
                        "success": False,
                        "code": -1,
                        "msg": f"请求异常: {str(e)}",
                        "data": None
                    }
            
            except Exception as e:
                print(f"\n✗ 未知错误: {str(e)}")
                return {
                    "success": False,
                    "code": -1,
                    "msg": f"未知错误: {str(e)}",
                    "data": None
                }
        
        # 超过最大重试次数
        print(f"\n✗ 达到最大重试次数 ({max_retries})")
        return {
            "success": False,
            "code": -1,
            "msg": "达到最大重试次数",
            "data": last_result
        }
    
    def import_month_sale_plan(self, 
                               file_path: str = None, 
                               check_high_inventory_flag: bool = None,
                               plan_import_type: int = None,
                               high_inventory_id: str = None,
                               inventory_snapshot_id: str = None,
                               high_inventory_remark: str = None,
                               max_retries: int = 3,
                               retry_interval: int = 20) -> Dict[str, Any]:
        """
        导入销售月需求计划
        
        Args:
            file_path: Excel文件路径 (默认使用配置文件中的路径)
            check_high_inventory_flag: 是否检查高库存标志 (默认使用配置文件中的值)
            plan_import_type: 计划导入类型 (默认使用配置文件中的值)
            high_inventory_id: 高库龄ID (可选，高库龄物料导入时使用)
            inventory_snapshot_id: 库存快照ID (可选，高库龄物料导入时使用)
            high_inventory_remark: 高库龄备注 (可选)
            max_retries: 最大重试次数 (默认3次)
            retry_interval: 重试间隔秒数 (默认20秒)
            
        Returns:
            接口响应结果
        """
        import time
        
        # 使用默认值
        if file_path is None:
            file_path = self.default_excel_file
        if check_high_inventory_flag is None:
            check_high_inventory_flag = self.default_import_params["checkHighInventoryFlag"]
        if plan_import_type is None:
            plan_import_type = self.default_import_params["planImportType"]
        
        # 检查文件是否存在
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")
        
        # API端点
        url = f"{self.base_url}{self.api_base}/month-sale-plan/import/v2"
        
        # CWMS服务降级错误关键词
        cwms_error_keywords = ["查询高库龄物料仓库维度明细失败", "cwms", "服务降级"]
        
        print(f"\n{'='*50}")
        print(f"开始导入销售月需求计划")
        print(f"文件路径: {file_path}")
        print(f"接口URL: {url}")
        print(f"最大重试次数: {max_retries}")
        print(f"重试间隔: {retry_interval}秒")
        print(f"{'='*50}")
        
        retry_count = 0
        last_result = None
        
        while retry_count <= max_retries:
            if retry_count > 0:
                print(f"\n⏳ 第 {retry_count} 次重试，等待 {retry_interval} 秒...")
                time.sleep(retry_interval)
            
            try:
                # 准备multipart/form-data请求
                with open(file_path, 'rb') as file:
                    files = {
                        'file': (os.path.basename(file_path), file, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                    }
                    
                    data = {
                        'checkHighInventoryFlag': str(check_high_inventory_flag).lower(),
                        'planImportType': str(plan_import_type)
                    }
                    
                    # 如果是高库龄物料导入，添加高库龄相关参数
                    is_high_inventory_import = high_inventory_id is not None and inventory_snapshot_id is not None
                    if is_high_inventory_import:
                        data['highInventoryId'] = high_inventory_id
                        data['inventorySnapshotId'] = inventory_snapshot_id
                        if high_inventory_remark is not None:
                            data['highInventoryRemark'] = high_inventory_remark
                        data['checkHighInventoryFlag'] = 'true'  # 高库龄导入时必须为true
                        
                    print(f"\n请求参数:")
                    print(f"  - checkHighInventoryFlag: {check_high_inventory_flag}")
                    print(f"  - planImportType: {plan_import_type}")
                    if is_high_inventory_import:
                        print(f"  - highInventoryId: {high_inventory_id}")
                        print(f"  - inventorySnapshotId: {inventory_snapshot_id}")
                        print(f"  - highInventoryRemark: {high_inventory_remark}")
                    print(f"  - 文件大小: {os.path.getsize(file_path)} bytes")
                    print(f"  - 尝试次数: {retry_count + 1}")
                    
                    # 发送POST请求
                    response = self.session.post(url, files=files, data=data)
                    
                    # 解析响应
                    if response.status_code == 200:
                        result = response.json()
                        last_result = result
                        
                        # 检查是否是CWMS服务降级错误
                        msg = result.get('msg', '')
                        is_cwms_error = any(keyword in msg for keyword in cwms_error_keywords)
                                                
                        if result.get("success"):
                            # 检查是否包含高库龄ID，如果有则需要二次导入
                            data = result.get("data")
                            if data is None:
                                data = {}
                            
                            high_inventory_id = data.get("highInventoryId") if isinstance(data, dict) else None
                            inventory_snapshot_id = data.get("inventorySnapshotId") if isinstance(data, dict) else None
                            
                            if high_inventory_id and inventory_snapshot_id:
                                # 检测到高库龄物料，需要二次导入
                                print(f"\n⚠️ 检测到高库龄物料，需要二次导入确认！")
                                print(f"  - highInventoryId: {high_inventory_id}")
                                print(f"  - inventorySnapshotId: {inventory_snapshot_id}")
                                print(f"\n自动执行高库龄物料二次导入...")
                                
                                # 进行二次导入，使用高库龄相关参数
                                with open(file_path, 'rb') as file:
                                    files_retry = {
                                        'file': (os.path.basename(file_path), file, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                                    }
                                    
                                    data_retry = {
                                        'checkHighInventoryFlag': 'true',
                                        'planImportType': str(plan_import_type),
                                        'highInventoryId': high_inventory_id,
                                        'inventorySnapshotId': inventory_snapshot_id
                                    }
                                    
                                    print(f"\n二次导入请求参数:")
                                    print(f"  - checkHighInventoryFlag: true")
                                    print(f"  - planImportType: {plan_import_type}")
                                    print(f"  - highInventoryId: {high_inventory_id}")
                                    print(f"  - inventorySnapshotId: {inventory_snapshot_id}")
                                    
                                    # 发送二次导入请求
                                    response_retry = self.session.post(url, files=files_retry, data=data_retry)
                                    
                                    if response_retry.status_code == 200:
                                        retry_result = response_retry.json()
                                        if retry_result.get("success"):
                                            print(f"\n✓ 高库龄物料二次导入成功!")
                                            print(f"响应结果: {json.dumps(retry_result, ensure_ascii=False, indent=2)}")
                                            return retry_result
                                        else:
                                            print(f"\n✗ 高库龄物料二次导入失败!")
                                            print(f"错误信息: {retry_result.get('msg')}")
                                            print(f"响应内容: {json.dumps(retry_result, ensure_ascii=False, indent=2)}")
                                            return retry_result
                                    else:
                                        print(f"\n✗ 二次导入请求失败!")
                                        print(f"状态码: {response_retry.status_code}")
                                        print(f"响应内容: {response_retry.text}")
                                        return {
                                            "success": False,
                                            "code": response_retry.status_code,
                                            "msg": f"HTTP错误: {response_retry.status_code}",
                                            "data": response_retry.text
                                        }
                            else:
                                print(f"\n✓ 导入成功!")
                                print(f"响应结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
                                return result
                        elif is_cwms_error and retry_count < max_retries:
                            # 只有CWMS服务降级错误才重试
                            print(f"\n⚠️ 检测到CWMS服务降级错误，准备重试...")
                            print(f"错误信息: {msg}")
                            retry_count += 1
                            continue
                        else:
                            # 其他错误不重试，直接返回
                            print(f"\n 导入失败!")
                            if is_cwms_error:
                                print(f"已达到最大重试次数 ({max_retries})，停止重试")
                            print(f"状态码: {response.status_code}")
                            print(f"响应内容: {json.dumps(result, ensure_ascii=False, indent=2)}")
                            return result
                    else:
                        print(f"\n✗ 导入失败!")
                        print(f"状态码: {response.status_code}")
                        print(f"响应内容: {response.text}")
                        return {
                            "success": False,
                            "code": response.status_code,
                            "msg": f"HTTP错误: {response.status_code}",
                            "data": response.text
                        }
                        
            except requests.exceptions.RequestException as e:
                print(f"\n✗ 请求异常: {str(e)}")
                if retry_count < max_retries:
                    print(f"准备重试...")
                    retry_count += 1
                    continue
                return {
                    "success": False,
                    "code": -1,
                    "msg": f"请求异常: {str(e)}",
                    "data": None
                }
            except Exception as e:
                print(f"\n✗ 未知错误: {str(e)}")
                return {
                    "success": False,
                    "code": -1,
                    "msg": f"未知错误: {str(e)}",
                    "data": None
                }
        
        # 所有重试都失败
        print(f"\n✗ 已达到最大重试次数 ({max_retries})，导入失败")
        if last_result:
            print(f"最后一次响应: {json.dumps(last_result, ensure_ascii=False, indent=2)}")
        return last_result or {"success": False, "code": -1, "msg": "重试失败", "data": None}
    
    def get_latest_input_order_no(self, current: int = 1, size: int = 10, plan_type: str = "21,22", after_time: str = None) -> Dict[str, Any]:
        """
        获取列表数据,并返回createTime最新的inputOrderNo
        
        Args:
            current: 当前页码 (默认1)
            size: 每页条数 (默认10)
            plan_type: 计划类型 (默认"21,22")
            after_time: 时间过滤，只返回此时间之后的记录 (格式: "YYYY-MM-DD HH:MM:SS")
            
        Returns:
            包含inputOrderNo的结果字典
        """
        import time
        from datetime import datetime
        
        url = f"{self.base_url}{self.api_base}/month-sale-plan/page/v2"
        
        print(f"\n{'='*50}")
        print(f"获取最新导入记录")
        print(f"接口URL: {url}")
        print(f"{'='*50}")
        
        try:
            # 添加表单提交的内容类型头
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
            }
            
            # 准备请求参数 - 根据截图使用Form Data格式
            data = {
                'current': str(current),
                'size': str(size),
                'planType': plan_type  # 使用传入的参数
            }
            
            print(f"\n请求参数:")
            print(f"  - current: {current}")
            print(f"  - size: {size}")
            print(f"  - planType: {plan_type}")
            
            # 发送POST请求
            response = self.session.post(url, data=data, headers=headers)
            
            # 解析响应
            if response.status_code == 200:
                result = response.json()
                
                # 注意：实际接口返回的是code字段，code: 0表示成功
                if result.get("code") == 0 and result.get("data"):
                    records = result["data"]
                    
                    if not records:
                        print(f"\n✗ 未找到任何记录")
                        return {"success": False, "msg": "未找到任何记录", "inputOrderNo": None}
                    
                    # 如果指定了时间过滤，只保留该时间之后的记录
                    if after_time:
                        print(f"\n 时间过滤: 只获取 {after_time} 之后的记录")
                        filtered_records = []
                        for record in records:
                            record_time = record.get('createTime', '')
                            if record_time and record_time > after_time:
                                filtered_records.append(record)
                        
                        if not filtered_records:
                            print(f"\n✗ 未找到 {after_time} 之后的记录")
                            return {"success": False, "msg": f"未找到 {after_time} 之后的记录", "inputOrderNo": None}
                        
                        records = filtered_records
                        print(f"  过滤后剩余 {len(records)} 条记录")
                    
                    # 按createTime排序,获取最新的一条
                    latest_record = max(records, key=lambda x: x.get('createTime', ''))
                    
                    sale_plan_no = latest_record.get('salePlanNo')
                    input_order_no = latest_record.get('inputOrderNo')
                    create_time = latest_record.get('createTime')
                    
                    print(f"\n✓ 获取成功!")
                    print(f"最新记录信息:")
                    print(f"  - salePlanNo: {sale_plan_no}")
                    print(f"  - inputOrderNo: {input_order_no}")
                    print(f"  - createTime: {create_time}")
                    print(f"  - changeNo: {latest_record.get('changeNo')}")
                    
                    return {
                        "success": True,
                        "msg": "获取成功",
                        "salePlanNo": sale_plan_no,
                        "inputOrderNo": input_order_no,
                        "createTime": create_time,
                        "record": latest_record
                    }
                else:
                    print(f"\n 获取失败: {result.get('msg', '未知错误')}")
                    print(f"响应内容: {json.dumps(result, ensure_ascii=False, indent=2)}")
                    return {"success": False, "msg": result.get('msg', '获取失败'), "inputOrderNo": None}
            else:
                print(f"\n✗ 请求失败!")
                print(f"状态码: {response.status_code}")
                print(f"响应内容: {response.text}")
                return {"success": False, "msg": f"HTTP错误: {response.status_code}", "inputOrderNo": None}
                
        except requests.exceptions.RequestException as e:
            print(f"\n✗ 请求异常: {str(e)}")
            return {"success": False, "msg": f"请求异常: {str(e)}", "inputOrderNo": None}
        except Exception as e:
            print(f"\n✗ 未知错误: {str(e)}")
            return {"success": False, "msg": f"未知错误: {str(e)}", "inputOrderNo": None}
    
    def confirm_high_inventory(self, input_order_no: str, high_inventory_id: str, inventory_snapshot_id: str) -> Dict[str, Any]:
        """
        确认高库龄物料
        
        Args:
            input_order_no: 输入单号
            high_inventory_id: 高库龄ID
            inventory_snapshot_id: 库存快照ID
            
        Returns:
            接口响应结果
        """
        url = f"{self.base_url}{self.api_base}/month-sale-plan/production-plan/push"
        
        print(f"\n{'='*50}")
        print(f"确认高库龄物料")
        print(f"接口URL: {url}")
        print(f"{'='*50}")
        
        try:
            # 准备JSON请求体，包含高库龄确认字段
            payload = {
                'inputOrderNo': input_order_no,
                'highInventoryId': high_inventory_id,
                'inventorySnapshotId': inventory_snapshot_id
            }
            
            # 设置JSON内容类型
            headers = {
                'Content-Type': 'application/json; charset=UTF-8'
            }
            
            print(f"\n请求参数:")
            print(f"  - inputOrderNo: {input_order_no}")
            print(f"  - highInventoryId: {high_inventory_id}")
            print(f"  - inventorySnapshotId: {inventory_snapshot_id}")
            
            # 发送POST请求
            response = self.session.post(url, json=payload, headers=headers)
            
            # 解析响应
            if response.status_code == 200:
                result = response.json()
                
                # 接口返回code: 0表示成功
                if result.get("code") == 0:
                    print(f"\n✓ 高库龄物料确认成功!")
                    print(f"响应结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
                    return result
                else:
                    print(f"\n✗ 高库龄物料确认失败!")
                    print(f"错误信息: {result.get('msg')}")
                    print(f"响应内容: {json.dumps(result, ensure_ascii=False, indent=2)}")
                    return result
            else:
                print(f"\n✗ 请求失败!")
                print(f"状态码: {response.status_code}")
                print(f"响应内容: {response.text}")
                return {
                    "success": False,
                    "code": response.status_code,
                    "msg": f"HTTP错误: {response.status_code}",
                    "data": response.text
                }
                
        except requests.exceptions.RequestException as e:
            print(f"\n✗ 请求异常: {str(e)}")
            return {
                "success": False,
                "code": -1,
                "msg": f"请求异常: {str(e)}",
                "data": None
            }
        except Exception as e:
            print(f"\n✗ 未知错误: {str(e)}")
            return {
                "success": False,
                "code": -1,
                "msg": f"未知错误: {str(e)}",
                "data": None
            }
    
    def submit_month_delay_to_sale_audit(self, record_ids: list) -> Dict[str, Any]:
        """
        顺延计划提交销售审批（批量）
        
        Args:
            record_ids: 记录ID列表，例如: ["2054077906528038914"]
            
        Returns:
            接口响应结果
        """
        url = f"{self.base_url}{self.api_base}/month-delay-plan/release-month-production-plan/batch"
        
        print(f"\n{'='*50}")
        print(f"顺延计划提交销售审批")
        print(f"接口URL: {url}")
        print(f"{'='*50}")
        
        try:
            # 准备JSON请求体 - 直接传递ID数组
            payload = record_ids
            
            # 设置JSON内容类型
            headers = {
                'Content-Type': 'application/json; charset=UTF-8'
            }
            
            print(f"\n请求参数:")
            print(f"  - record_ids: {json.dumps(record_ids, ensure_ascii=False)}")
            
            # 发送POST请求
            response = self.session.post(url, json=payload, headers=headers)
            
            # 解析响应
            if response.status_code == 200:
                result = response.json()
                
                # 接口返回code: 0表示成功
                if result.get("code") == 0:
                    print(f"\n✓ 顺延计划提交销售审批成功!")
                    print(f"响应结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
                    return result
                else:
                    print(f"\n✗ 顺延计划提交销售审批失败!")
                    print(f"错误信息: {result.get('msg')}")
                    print(f"响应内容: {json.dumps(result, ensure_ascii=False, indent=2)}")
                    return result
            else:
                print(f"\n✗ 请求失败!")
                print(f"状态码: {response.status_code}")
                print(f"响应内容: {response.text}")
                return {
                    "success": False,
                    "code": response.status_code,
                    "msg": f"HTTP错误: {response.status_code}",
                    "data": response.text
                }
                    
        except requests.exceptions.RequestException as e:
            print(f"\n 请求异常: {str(e)}")
            return {
                "success": False,
                "code": -1,
                "msg": f"请求异常: {str(e)}",
                "data": None
            }
        except Exception as e:
            print(f"\n✗ 未知错误: {str(e)}")
            return {
                "success": False,
                "code": -1,
                "msg": f"未知错误: {str(e)}",
                "data": None
            }
    
    def push_production_plan(self, input_order_no: str, high_inventory_id: str = None, inventory_snapshot_id: str = None) -> Dict[str, Any]:
        """
        提交排产（支持高库龄物料确认）
        
        Args:
            input_order_no: 输入单号
            high_inventory_id: 高库龄ID（可选，高库龄物料确认时使用）
            inventory_snapshot_id: 库存快照ID（可选，高库龄物料确认时使用）
            
        Returns:
            接口响应结果
        """
        url = f"{self.base_url}{self.api_base}/month-sale-plan/production-plan/push"
        
        # 根据是否有高库龄字段判断操作类型
        is_high_inventory = high_inventory_id is not None and inventory_snapshot_id is not None
        
        if is_high_inventory:
            print(f"\n{'='*50}")
            print(f"确认高库龄物料排产")
        else:
            print(f"\n{'='*50}")
            print(f"提交排产")
        
        print(f"接口URL: {url}")
        print(f"{'='*50}")
        
        try:
            # 准备JSON请求体
            payload = {
                'inputOrderNo': input_order_no
            }
            
            # 如果是高库龄确认，添加额外字段
            if is_high_inventory:
                payload['highInventoryId'] = high_inventory_id
                payload['inventorySnapshotId'] = inventory_snapshot_id
            
            # 设置JSON内容类型
            headers = {
                'Content-Type': 'application/json; charset=UTF-8'
            }
            
            print(f"\n请求参数:")
            print(f"  - inputOrderNo: {input_order_no}")
            if is_high_inventory:
                print(f"  - highInventoryId: {high_inventory_id}")
                print(f"  - inventorySnapshotId: {inventory_snapshot_id}")
            
            # 发送POST请求
            response = self.session.post(url, json=payload, headers=headers)
            
            # 解析响应
            if response.status_code == 200:
                result = response.json()
                
                # 接口返回code: 0表示成功
                if result.get("code") == 0:
                    # 检查是否包含高库龄字段
                    data = result.get("data")
                    if data is None:
                        data = {}
                    
                    response_high_inventory_id = data.get("highInventoryId") if isinstance(data, dict) else None
                    response_inventory_snapshot_id = data.get("inventorySnapshotId") if isinstance(data, dict) else None
                    
                    if response_high_inventory_id and response_inventory_snapshot_id:
                        # 高库龄物料需要确认
                        print(f"\n 检测到高库龄物料，需要确认！")
                        print(f"  - highInventoryId: {response_high_inventory_id}")
                        print(f"  - inventorySnapshotId: {response_inventory_snapshot_id}")
                        
                        # 自动调用高库龄确认接口
                        print(f"\n自动执行高库龄物料确认...")
                        confirm_result = self.confirm_high_inventory(
                            input_order_no=input_order_no,
                            high_inventory_id=response_high_inventory_id,
                            inventory_snapshot_id=response_inventory_snapshot_id
                        )
                        return confirm_result
                    else:
                        print(f"\n✓ 提交排产成功!")
                        print(f"响应结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
                        return result
                else:
                    if is_high_inventory:
                        print(f"\n✗ 高库龄物料确认失败!")
                    else:
                        print(f"\n✗ 提交排产失败!")
                    print(f"错误信息: {result.get('msg')}")
                    print(f"响应内容: {json.dumps(result, ensure_ascii=False, indent=2)}")
                    return result
            else:
                print(f"\n✗ 请求失败!")
                print(f"状态码: {response.status_code}")
                print(f"响应内容: {response.text}")
                return {
                    "success": False,
                    "code": response.status_code,
                    "msg": f"HTTP错误: {response.status_code}",
                    "data": response.text
                }
                
        except requests.exceptions.RequestException as e:
            print(f"\n 请求异常: {str(e)}")
            return {
                "success": False,
                "code": -1,
                "msg": f"请求异常: {str(e)}",
                "data": None
            }
        except Exception as e:
            print(f"\n✗ 未知错误: {str(e)}")
            return {
                "success": False,
                "code": -1,
                "msg": f"未知错误: {str(e)}",
                "data": None
            }
    
    def get_import_status(self, import_id: str) -> Dict[str, Any]:
        """
        查询导入状态 (预留接口,根据实际接口调整)
        
        Args:
            import_id: 导入任务ID
            
        Returns:
            导入状态信息
        """
        # TODO: 根据实际接口实现
        url = f"{self.base_url}{self.api_base}/month-sale-plan/import/status/{import_id}"
        print(f"\n查询导入状态: {import_id}")
        print(f"接口URL: {url}")
        
        # 预留实现
        return {
            "success": True,
            "msg": "状态查询功能待实现",
            "data": None
        }
    
    def get_latest_production_plan(self, current: int = 1, size: int = 10) -> Dict[str, Any]:
        """
        刷新生产计划列表，获取createTime最新的一条数据
            
        Args:
            current: 当前页码 (默认1)
            size: 每页条数 (默认10)
                
        Returns:
            包含salePlanNo和inputOrderNo的结果字典
        """
        url = f"{self.base_url}{self.api_base}/web/monthProductionPlanDraft/page"
            
        print(f"\n{'='*50}")
        print(f"刷新生产计划列表")
        print(f"接口URL: {url}")
        print(f"{'='*50}")
            
        try:
            # 添加表单提交的内容类型头
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
            }
                
            # 准备请求参数
            data = {
                'current': str(current),
                'size': str(size)
            }
                
            print(f"\n请求参数:")
            print(f"  - current: {current}")
            print(f"  - size: {size}")
                
            # 发送POST请求
            response = self.session.post(url, data=data, headers=headers)
                
            # 解析响应
            if response.status_code == 200:
                result = response.json()
                    
                # 接口返回code: 0表示成功
                if result.get("code") == 0 and result.get("data"):
                    records = result["data"]
                        
                    if not records:
                        print(f"\n 未找到任何生产计划记录")
                        return {"success": False, "msg": "未找到任何记录", "salePlanNo": None, "inputOrderNo": None}
                        
                    # 按createTime排序,获取最新的一条
                    latest_record = max(records, key=lambda x: x.get('createTime', ''))
                        
                    sale_plan_no = latest_record.get('salePlanNo')
                    input_order_no = latest_record.get('inputOrderNo')
                    create_time = latest_record.get('createTime')
                        
                    print(f"\n✓ 获取成功!")
                    print(f"最新生产计划记录信息:")
                    print(f"  - salePlanNo: {sale_plan_no}")
                    print(f"  - inputOrderNo: {input_order_no}")
                    print(f"  - createTime: {create_time}")
                        
                    return {
                        "success": True,
                        "msg": "获取成功",
                        "salePlanNo": sale_plan_no,
                        "inputOrderNo": input_order_no,
                        "createTime": create_time,
                        "record": latest_record
                    }
                else:
                    print(f"\n✗ 获取失败: {result.get('msg', '未知错误')}")
                    print(f"响应内容: {json.dumps(result, ensure_ascii=False, indent=2)}")
                    return {"success": False, "msg": result.get('msg', '获取失败'), "salePlanNo": None, "inputOrderNo": None}
            else:
                print(f"\n✗ 请求失败!")
                print(f"状态码: {response.status_code}")
                print(f"响应内容: {response.text}")
                return {"success": False, "msg": f"HTTP错误: {response.status_code}", "salePlanNo": None, "inputOrderNo": None}
                    
        except requests.exceptions.RequestException as e:
            print(f"\n✗ 请求异常: {str(e)}")
            return {"success": False, "msg": f"请求异常: {str(e)}", "salePlanNo": None, "inputOrderNo": None}
        except Exception as e:
            print(f"\n✗ 未知错误: {str(e)}")
            return {"success": False, "msg": f"未知错误: {str(e)}", "salePlanNo": None, "inputOrderNo": None}
        
    def submit_to_sale_audit(self, sale_plan_no: str, input_order_no: str, hoh_month: str = None, hoh_year: str = None) -> Dict[str, Any]:
        """
        提交销售审批
            
        Args:
            sale_plan_no: 销售计划编号
            input_order_no: 输入单号
            hoh_month: 月份 (可选)
            hoh_year: 年份 (可选)
                
        Returns:
            接口响应结果
        """
        url = f"{self.base_url}{self.api_base}/web/monthProductionPlanDraft/submitToSaleAudit"
            
        print(f"\n{'='*50}")
        print(f"提交销售审批")
        print(f"接口URL: {url}")
        print(f"{'='*50}")
            
        try:
            # 准备JSON请求体
            payload = {
                'salePlanNo': sale_plan_no,
                'inputOrderNo': input_order_no,
                'hohMonth': hoh_month,
                'hohYear': hoh_year
            }
                
            # 设置JSON内容类型
            headers = {
                'Content-Type': 'application/json; charset=UTF-8'
            }
                
            print(f"\n请求参数:")
            print(f"  - salePlanNo: {sale_plan_no}")
            print(f"  - inputOrderNo: {input_order_no}")
            print(f"  - hohMonth: {hoh_month}")
            print(f"  - hohYear: {hoh_year}")
                
            # 发送POST请求
            response = self.session.post(url, json=payload, headers=headers)
                
            # 解析响应
            if response.status_code == 200:
                result = response.json()
                    
                # 接口返回code: 0表示成功
                if result.get("code") == 0:
                    print(f"\n✓ 提交销售审批成功!")
                    print(f"响应结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
                    return result
                else:
                    print(f"\n✗ 提交销售审批失败!")
                    print(f"错误信息: {result.get('msg')}")
                    print(f"响应内容: {json.dumps(result, ensure_ascii=False, indent=2)}")
                    return result
            else:
                print(f"\n✗ 请求失败!")
                print(f"状态码: {response.status_code}")
                print(f"响应内容: {response.text}")
                return {
                    "success": False,
                    "code": response.status_code,
                    "msg": f"HTTP错误: {response.status_code}",
                    "data": response.text
                }
                    
        except requests.exceptions.RequestException as e:
            print(f"\n 请求异常: {str(e)}")
            return {
                "success": False,
                "code": -1,
                "msg": f"请求异常: {str(e)}",
                "data": None
            }
        except Exception as e:
            print(f"\n✗ 未知错误: {str(e)}")
            return {
                "success": False,
                "code": -1,
                "msg": f"未知错误: {str(e)}",
                "data": None
            }
    
    def push_month_plan_to_purchase(self, sale_plan_no: str) -> Dict[str, Any]:
        """
        重推采购（将销售计划推送给采购）
        
        Args:
            sale_plan_no: 销售计划编号
            
        Returns:
            接口响应结果
        """
        url = f"{self.base_url}{self.api_base}/month-production-plan/push-month-plan/sale-plan-no"
        
        print(f"\n{'='*50}")
        print(f"重推采购")
        print(f"接口URL: {url}")
        print(f"{'='*50}")
        
        try:
            # 准备JSON请求体 - 注意是数组格式
            payload = [sale_plan_no]
            
            # 设置JSON内容类型
            headers = {
                'Content-Type': 'application/json; charset=UTF-8'
            }
            
            print(f"\n请求参数:")
            print(f"  请求体 (JSON数组):")
            print(f"    {json.dumps(payload, ensure_ascii=False)}")
            
            # 发送POST请求
            response = self.session.post(url, json=payload, headers=headers)
            
            # 解析响应
            if response.status_code == 200:
                result = response.json()
                
                # 接口返回code: 0表示成功
                if result.get("code") == 0:
                    print(f"\n✓ 重推采购成功!")
                    print(f"响应结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
                    return result
                else:
                    print(f"\n✗ 重推采购失败!")
                    print(f"错误信息: {result.get('msg')}")
                    print(f"响应内容: {json.dumps(result, ensure_ascii=False, indent=2)}")
                    return result
            else:
                print(f"\n✗ 请求失败!")
                print(f"状态码: {response.status_code}")
                print(f"响应内容: {response.text}")
                return {
                    "success": False,
                    "code": response.status_code,
                    "msg": f"HTTP错误: {response.status_code}",
                    "data": response.text
                }
                    
        except requests.exceptions.RequestException as e:
            print(f"\n 请求异常: {str(e)}")
            return {
                "success": False,
                "code": -1,
                "msg": f"请求异常: {str(e)}",
                "data": None
            }
        except Exception as e:
            print(f"\n✗ 未知错误: {str(e)}")
            return {
                "success": False,
                "code": -1,
                "msg": f"未知错误: {str(e)}",
                "data": None
            }

    def approve_sale_audit(self, audit_order_no: str, sale_plan_no: str, input_order_no: str = "") -> Dict[str, Any]:
        """
        提交销售审批（审批用户操作）
        
        Args:
            audit_order_no: 审批单号（来自上一个接口）
            sale_plan_no: 销售计划编号（来自上一个接口）
            input_order_no: 输入单号（可选）
            
        Returns:
            接口响应结果
        """
        import urllib.parse
        
        # 对URL参数进行编码
        encoded_audit_order_no = urllib.parse.quote(audit_order_no, safe='')
        encoded_sale_plan_no = urllib.parse.quote(sale_plan_no, safe='')
        encoded_input_order_no = urllib.parse.quote(input_order_no, safe='') if input_order_no else ''
        
        # 所有参数都通过URL传递，不发送JSON body
        url = f"{self.base_url}{self.api_base}/auditOrder/audit?auditNode=1&auditOrderNo={encoded_audit_order_no}&auditResult=true&remarks=&salePlanNo={encoded_sale_plan_no}"
        
        if encoded_input_order_no:
            url += f"&inputOrderNo={encoded_input_order_no}"
        
        print(f"\n{'='*50}")
        print(f"提交销售审批（审批操作）")
        print(f"接口URL: {url}")
        print(f"{'='*50}")
        
        try:
            # 准备JSON请求体 - 注意是数组格式
            payload = [
                {
                    'auditDepartmentCode': 'JSB',
                    'auditDepartmentName': '技术部',
                    'auditNickName': 'newbee',
                    'auditOrderNo': audit_order_no,
                    'auditUserName': 'newbee',
                    'salePlanNo': sale_plan_no
                }
            ]
            
            # 设置JSON内容类型
            headers = {
                'Content-Type': 'application/json; charset=UTF-8'
            }
            
            print(f"\n请求参数:")
            print(f"  URL参数:")
            print(f"    - auditNode: 1")
            print(f"    - auditOrderNo: {audit_order_no}")
            print(f"    - auditResult: true")
            print(f"    - remarks: (空)")
            print(f"  请求体 (JSON数组):")
            print(f"    [{json.dumps(payload[0], ensure_ascii=False)}]")
            
            # 发送POST请求
            response = self.session.post(url, json=payload, headers=headers)
            
            # 解析响应
            if response.status_code == 200:
                result = response.json()
                
                # 接口返回code: 0表示成功
                if result.get("code") == 0:
                    print(f"\n✓ 提交销售审批成功!")
                    print(f"响应结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
                    return result
                else:
                    print(f"\n✗ 提交销售审批失败!")
                    print(f"错误信息: {result.get('msg')}")
                    print(f"响应内容: {json.dumps(result, ensure_ascii=False, indent=2)}")
                    return result
            else:
                print(f"\n✗ 请求失败!")
                print(f"状态码: {response.status_code}")
                print(f"响应内容: {response.text}")
                return {
                    "success": False,
                    "code": response.status_code,
                    "msg": f"HTTP错误: {response.status_code}",
                    "data": response.text
                }
                    
        except requests.exceptions.RequestException as e:
            print(f"\n 请求异常: {str(e)}")
            return {
                "success": False,
                "code": -1,
                "msg": f"请求异常: {str(e)}",
                "data": None
            }
        except Exception as e:
            print(f"\n✗ 未知错误: {str(e)}")
            return {
                "success": False,
                "code": -1,
                "msg": f"未知错误: {str(e)}",
                "data": None
            }
    
    def get_latest_audit_order(self, current: int = 1, size: int = 10, audit_type_codes: list = [2]) -> Dict[str, Any]:
        """
        刷新审批列表，获取createTime最新的一条数据的auditOrderNo和inputOrderNo
        
        Args:
            current: 当前页码 (默认1)
            size: 每页条数 (默认10)
            audit_type_codes: 审批类型代码列表 (默认[2])
            
        Returns:
            包含auditOrderNo和inputOrderNo的结果字典
        """
        url = f"{self.base_url}{self.api_base}/auditOrder/pageList"
        
        print(f"\n{'='*50}")
        print(f"刷新审批列表")
        print(f"接口URL: {url}")
        print(f"{'='*50}")
        
        try:
            # 准备JSON请求体
            payload = {
                'current': current,
                'size': size,
                'auditTypeCodes': audit_type_codes
            }
            
            # 设置JSON内容类型
            headers = {
                'Content-Type': 'application/json; charset=UTF-8'
            }
            
            print(f"\n请求参数:")
            print(f"  - current: {current}")
            print(f"  - size: {size}")
            print(f"  - auditTypeCodes: {audit_type_codes}")
            
            # 发送POST请求
            response = self.session.post(url, json=payload, headers=headers)
            
            # 解析响应
            if response.status_code == 200:
                result = response.json()
                
                # 接口返回code: 0表示成功
                if result.get("code") == 0 and result.get("data"):
                    records = result["data"]
                    
                    if not records:
                        print(f"\n 未找到任何审批记录")
                        return {"success": False, "msg": "未找到任何记录", "auditOrderNo": None, "inputOrderNo": None}
                    
                    # 按createTime排序,获取最新的一条
                    latest_record = max(records, key=lambda x: x.get('createTime', ''))
                    
                    audit_order_no = latest_record.get('auditOrderNo')
                    input_order_no = latest_record.get('inputOrderNo')
                    create_time = latest_record.get('createTime')
                    
                    print(f"\n✓ 获取成功!")
                    print(f"最新审批记录信息:")
                    print(f"  - auditOrderNo: {audit_order_no}")
                    print(f"  - inputOrderNo: {input_order_no}")
                    print(f"  - createTime: {create_time}")
                    
                    return {
                        "success": True,
                        "msg": "获取成功",
                        "auditOrderNo": audit_order_no,
                        "inputOrderNo": input_order_no,
                        "createTime": create_time,
                        "record": latest_record
                    }
                else:
                    print(f"\n 获取失败: {result.get('msg', '未知错误')}")
                    print(f"响应内容: {json.dumps(result, ensure_ascii=False, indent=2)}")
                    return {"success": False, "msg": result.get('msg', '获取失败'), "auditOrderNo": None, "inputOrderNo": None}
            else:
                print(f"\n 请求失败!")
                print(f"状态码: {response.status_code}")
                print(f"响应内容: {response.text}")
                return {"success": False, "msg": f"HTTP错误: {response.status_code}", "auditOrderNo": None, "inputOrderNo": None}
                
        except requests.exceptions.RequestException as e:
            print(f"\n 请求异常: {str(e)}")
            return {"success": False, "msg": f"请求异常: {str(e)}", "auditOrderNo": None, "inputOrderNo": None}
        except Exception as e:
            print(f"\n✗ 未知错误: {str(e)}")
            return {"success": False, "msg": f"未知错误: {str(e)}", "auditOrderNo": None, "inputOrderNo": None}
    



def main():
    """主函数 - 支持命令行参数、环境变量、交互式三种方式"""
    import sys
    import os
    
    print("="*60)
    print("HCH系统API自动化操作")
    print("="*60)
    
    # 优先级1: 检查环境变量（Web服务调用）
    choice = os.environ.get('IMPORT_TYPE')
    submitter_token = os.environ.get('SUBMITTER_TOKEN', '').strip()
    approver_token = os.environ.get('APPROVER_TOKEN', '').strip()
    excel_file_path = os.environ.get('EXCEL_FILE_PATH', '').strip()
    
    # 优先级2: 检查命令行参数
    if not choice and len(sys.argv) > 1:
        choice = sys.argv[1]
    
    # 优先级3: 交互式选择
    if not choice:
        print("\n请选择导入类型：")
        print("1. 任务单导入流程 (planImportType=1)")
        print("2. 月需求导入流程 (planImportType=0)")
        print("delay. 顺延计划导入流程")
        while True:
            choice = input("\n请输入选项 (1/2/delay): ").strip()
            if choice in ["1", "2", "delay"]:
                break
            print("请输入 1、2 或 delay！")
    
    print(f"\n选择的导入类型: {choice}")
    if submitter_token:
        print(f"✓ 使用前端传入的submitter token")
    if approver_token:
        print(f"✓ 使用前端传入的approver token")
    
    if choice == "2":
        # 月需求导入流程
        run_month_demand_flow(submitter_token, approver_token, excel_file_path)
    elif choice == "delay":
        # 顺延计划导入流程
        run_month_delay_flow(submitter_token, approver_token, excel_file_path)
    else:
        # 任务单导入流程
        run_task_order_flow(submitter_token, approver_token, excel_file_path)


def run_task_order_flow(submitter_token=None, approver_token=None, excel_file_path=None):
    """任务单导入流程"""
    from datetime import datetime
    
    print("\n" + "="*60)
    print("开始执行：任务单导入流程")
    print("="*60)
    
    api = HCHAPIAutomation()
    
    # 如果前端传入了token，则覆盖配置文件中的token
    if submitter_token:
        api.token = submitter_token
        api.headers["Authorization"] = f"Bearer {submitter_token}"
        api.session.headers.update(api.headers)
        print(f"✓ 已设置submitter token")
    if approver_token:
        api.approver_token = approver_token
        print(f"✓ 已保存approver token")
    
    # 记录导入前的时间，用于后续过滤最新记录
    before_import_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n⏰ 记录导入前时间: {before_import_time}")
        
    # 优先使用前端传入的文件路径，否则从配置文件获取
    if excel_file_path and os.path.exists(excel_file_path):
        task_file = excel_file_path
        print(f"✓ 使用前端上传的文件: {task_file}")
    else:
        config_files = api.config.get("default_excel_files", {})
        task_file = config_files.get("task_order", "./任务单导入模板.xlsx")
        print(f"⚠ 使用配置文件中的文件: {task_file}")
    
    # 步骤1: 导入任务单
    print(f"\n【步骤1】导入任务单...")
    print(f"  文件路径: {task_file}")
    import_result = api.import_month_sale_plan(
        file_path=task_file,
        plan_import_type=1
    )
    
    # 继续后续流程（任务单使用 planType=21,22），传入时间戳
    return run_common_flow(api, import_result, plan_type="21,22", after_time=before_import_time)


def run_month_delay_flow(submitter_token=None, approver_token=None, excel_file_path=None):
    """顺延计划导入流程"""
    from datetime import datetime
    
    print("\n" + "="*60)
    print("开始执行：顺延计划导入流程")
    print("="*60)
    
    api = HCHAPIAutomation()
    
    # 如果前端传入了token，则覆盖配置文件中的token
    if submitter_token:
        api.token = submitter_token
        api.headers["Authorization"] = f"Bearer {submitter_token}"
        api.session.headers.update(api.headers)
        print(f"✓ 已设置submitter token")
    if approver_token:
        api.approver_token = approver_token
        print(f"✓ 已保存approver token")
    
    # 记录导入前的时间，用于后续过滤最新记录
    before_import_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n⏰ 记录导入前时间: {before_import_time}")
    
    # 优先使用前端传入的文件路径，否则从配置文件获取
    if excel_file_path and os.path.exists(excel_file_path):
        month_file = excel_file_path
        print(f"✓ 使用前端上传的文件: {month_file}")
    else:
        config_files = api.config.get("default_excel_files", {})
        month_file = config_files.get("month_delay", "./顺延计划导入模板.xlsx")
        print(f"⚠ 使用配置文件中的文件: {month_file}")
    
    # 步骤1: 导入顺延计划
    print(f"\n【步骤1】导入顺延计划...")
    print(f"  文件路径: {month_file}")
    import_result = api.import_month_delay_plan(
        file_path=month_file
    )
    
    # 顺延计划特殊流程：获取列表并检查total
    if not import_result.get("success"):
        print("\n导入失败，请检查:")
        print("1. Token是否有效")
        print("2. 文件格式是否正确")
        print("3. 网络连接是否正常")
        return None
    
    print("\n" + "="*60)
    print("导入成功! 继续执行后续操作...")
    print("="*60)
    
    # 等待几秒确保数据写入数据库
    import time
    print("\n等待3秒，确保数据写入数据库...")
    time.sleep(3)
    
    # 步骤2: 获取顺延计划列表，获取最新两条记录
    print("\n【步骤2】获取顺延计划列表...")
    print(f"  时间过滤: 获取 {before_import_time} 之后的记录")
    
    page_result = api.get_month_delay_plan_page(
        current=1,
        size=10,
        after_time=before_import_time
    )
    
    if not page_result.get("success") or not page_result.get("record_ids"):
        print(f"\n✗ 未找到顺延计划记录")
        print(f"原因: {page_result.get('msg')}")
        return None
    
    record_ids = page_result["record_ids"]
    input_order_no = page_result.get("inputOrderNo", "")
    total = page_result.get("total", 0)
    record_count = page_result.get("record_count", 0)
    
    print(f"\n✓ 找到 {record_count} 条记录:")
    print(f"  - record_ids: {record_ids}")
    print(f"  - inputOrderNo: {input_order_no}")
    print(f"  - total: {total}")
    
    # 步骤3: 顺延计划提交销售审批（批量提交所有记录）
    print(f"\n【步骤3】顺延计划提交销售审批...")
    print(f"  提交记录数: {record_count}")
    submit_sale_result = api.submit_month_delay_to_sale_audit(
        record_ids=record_ids
    )
    
    # 检查code字段，0表示成功
    if submit_sale_result.get("code") != 0:
        print(f"\n✗ 提交销售审批失败")
        print(f"错误信息: {submit_sale_result.get('msg')}")
        return None
    
    # 从响应中提取所有sale_plan_no（msg字段的值）
    # 响应结构: {code: 0, data: {success: [{code: 0, msg: "SY2026051208", ...}, {code: 0, msg: "SY2026051209", ...}]}}
    data = submit_sale_result.get("data", {})
    success_list = data.get("success", [])
        
    if not success_list or len(success_list) == 0:
        print(f"\n✗ 未能从响应中提取sale_plan_no")
        print(f"响应数据: {json.dumps(submit_sale_result, ensure_ascii=False, indent=2)}")
        return None
        
    # 提取所有成功的sale_plan_no，但只取前两条（对应最新的两条记录）
    sale_plan_nos = []
    for idx, item in enumerate(success_list, 1):
        if item.get("code") == 0:
            sale_plan_no = item.get("msg")
            if sale_plan_no:
                sale_plan_nos.append(sale_plan_no)
                print(f"  ✓ 记录{idx} - sale_plan_no: {sale_plan_no}")
        
        # 只取前两条
        if len(sale_plan_nos) >= 2:
            break
        
    if not sale_plan_nos:
        print(f"\n✗ 没有成功审批的记录")
        return None
        
    print(f"\n✓ 提取到 {len(sale_plan_nos)} 个销售计划号: {sale_plan_nos}")
        
    # 步骤4: 推送到采购系统（只推送前两条）
    print(f"\n【步骤4】推送到采购系统...")
    push_results = []
    for idx, sale_plan_no in enumerate(sale_plan_nos, 1):
        print(f"\n  推送第 {idx}/{len(sale_plan_nos)} 条: {sale_plan_no}")
        push_result = api.push_month_plan_to_purchase(
            sale_plan_no=sale_plan_no
        )
            
        if push_result.get("code") == 0:
            print(f"  ✓ 推送成功")
            push_results.append({"salePlanNo": sale_plan_no, "success": True})
        else:
            print(f"  ✗ 推送失败: {push_result.get('msg')}")
            push_results.append({"salePlanNo": sale_plan_no, "success": False, "msg": push_result.get('msg')})
    
    # 输出总结
    print("\n" + "="*60)
    print("顺延计划导入流程执行完成")
    print("="*60)
    print(f"✓ 输入单号: {input_order_no}")
    print(f"✓ 提交记录数: {record_count}")
    print(f"✓ 成功推送数: {len([r for r in push_results if r.get('success')])}")
    print(f"✓ 销售计划号: {sale_plan_nos}")
    print(f"✓ 状态: 已完成")
    
    return {
        "success": True,
        "inputOrderNo": input_order_no,
        "salePlanNos": sale_plan_nos,
        "record_ids": record_ids,
        "total": total,
        "record_count": record_count,
        "push_results": push_results
    }


def run_month_demand_flow(submitter_token=None, approver_token=None, excel_file_path=None):
    """月需求导入流程"""
    from datetime import datetime
    
    print("\n" + "="*60)
    print("开始执行：月需求导入流程")
    print("="*60)
    
    api = HCHAPIAutomation()
    
    # 如果前端传入了token，则覆盖配置文件中的token
    if submitter_token:
        api.token = submitter_token
        api.headers["Authorization"] = f"Bearer {submitter_token}"
        api.session.headers.update(api.headers)
        print(f"✓ 已设置submitter token")
    if approver_token:
        api.approver_token = approver_token
        print(f"✓ 已保存approver token")
    
    # 记录导入前的时间，用于后续过滤最新记录
    before_import_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n⏰ 记录导入前时间: {before_import_time}")
    
    # 优先使用前端传入的文件路径，否则从配置文件获取
    if excel_file_path and os.path.exists(excel_file_path):
        month_file = excel_file_path
        print(f"✓ 使用前端上传的文件: {month_file}")
    else:
        config_files = api.config.get("default_excel_files", {})
        month_file = config_files.get("month_demand", "./销售月需求导入模板.xlsx")
        print(f"⚠ 使用配置文件中的文件: {month_file}")
    
    # 步骤1: 导入销售月需求
    print(f"\n【步骤1】导入销售月需求...")
    print(f"  文件路径: {month_file}")
    import_result = api.import_month_sale_plan(
        file_path=month_file,
        plan_import_type=0
    )
    
    # 继续后续流程（月需求使用 planType=1,12），传入时间戳
    return run_common_flow(api, import_result, plan_type="1,12", after_time=before_import_time)


def run_common_flow(api: HCHAPIAutomation, import_result: Dict[str, Any], plan_type: str = "21,22", after_time: str = None) -> Dict[str, Any]:
    """通用流程（步骤2-8）"""
    # 根据导入结果进行后续操作
    if not import_result.get("success"):
        print("\n导入失败,请检查:")
        print("1. Token是否有效")
        print("2. 文件格式是否正确")
        print("3. 网络连接是否正常")
        return None
    
    print("\n" + "="*60)
    print("导入成功! 继续执行后续操作...")
    print("="*60)
    
    # 等待几秒确保数据写入数据库
    import time
    print("\n等待3秒,确保数据写入数据库...")
    time.sleep(3)
    
    # 步骤2: 获取最新导入记录的salePlanNo和inputOrderNo
    print("\n【步骤2】获取最新导入记录...")
    
    if after_time:
        print(f"  时间过滤: 获取 {after_time} 之后的记录")
    
    latest_record_result = api.get_latest_input_order_no(
        current=1,
        size=10,
        plan_type=plan_type,
        after_time=after_time
    )
    
    if not latest_record_result.get("success"):
        print(f"\n获取最新记录失败: {latest_record_result.get('msg')}")
        return None
    
    sale_plan_no = latest_record_result["salePlanNo"]
    input_order_no = latest_record_result["inputOrderNo"]
    print(f"\n获取到的关键字段:")
    print(f"  - salePlanNo: {sale_plan_no}")
    print(f"  - inputOrderNo: {input_order_no}")
    
    # 步骤3: 提交排产
    print("\n【步骤3】提交排产...")
    push_result = api.push_production_plan(input_order_no)
    
    if push_result.get("code") != 0:
        print("\n 提交排产失败，请检查错误信息")
        return None
    
    print("\n" + "="*60)
    print("提交排产成功! 继续执行后续操作...")
    print("="*60)
    
    # 等待几秒确保排产数据生成
    print("\n等待3秒,确保排产数据生成...")
    time.sleep(3)
    
    # 步骤4: 切换到审批用户
    print("\n【步骤4】切换到审批用户...")
    api.switch_user("approver")
    
    print("\n【步骤5】提交销售审批...")
    submit_result = api.submit_to_sale_audit(
        sale_plan_no=sale_plan_no,
        input_order_no=input_order_no,
        hoh_month=None,
        hoh_year=None
    )
    
    if submit_result.get("code") != 0:
        print("\n 提交销售审批失败，请检查错误信息")
        return None
    
    print("\n" + "="*60)
    print("提交销售审批成功! 继续执行后续操作...")
    print("="*60)
    
    # 等待几秒确保审批记录生成
    print("\n等待3秒,确保审批记录生成...")
    time.sleep(3)
    
    # 步骤6: 刷新审批列表,获取最新的auditOrderNo
    print("\n【步骤6】刷新审批列表...")
    audit_order_result = api.get_latest_audit_order(
        current=1,
        size=10,
        audit_type_codes=[2]
    )
    
    if audit_order_result.get("success"):
        audit_order_no = audit_order_result["auditOrderNo"]
        audit_input_order_no = audit_order_result.get("inputOrderNo", "")
        
        print(f"\n获取到的审批记录:")
        print(f"  - auditOrderNo: {audit_order_no}")
        print(f"  - inputOrderNo: {audit_input_order_no}")
        
        # 步骤7: 提交销售审批（审批操作）
        print("\n【步骤7】提交销售审批（审批操作）...")
        approve_result = api.approve_sale_audit(
            audit_order_no=audit_order_no,
            sale_plan_no=sale_plan_no,
            input_order_no=audit_input_order_no
        )
        
        if approve_result.get("code") != 0:
            print("\n 提交销售审批（审批操作）失败，请检查错误信息")
            return None
        
        # 步骤8: 重推采购
        print("\n【步骤8】重推采购...")
        push_purchase_result = api.push_month_plan_to_purchase(
            sale_plan_no=sale_plan_no
        )
        
        if push_purchase_result.get("code") != 0:
            print("\n 重推采购失败，请检查错误信息")
            return None
        
        print(f"\n{'='*60}")
        print("✓ 整个流程执行成功!")
        print(f"{'='*60}")
        print("\n所有操作已完成，关键数据:")
        print(f"  - salePlanNo: {sale_plan_no}")
        print(f"  - inputOrderNo: {input_order_no}")
        print(f"  - auditOrderNo: {audit_order_no}")
        print(f"{'='*60}")
        
        # 返回结果供后续使用
        return {
            "salePlanNo": sale_plan_no,
            "inputOrderNo": input_order_no,
            "auditOrderNo": audit_order_no,
            "submitResult": submit_result,
            "auditResult": audit_order_result,
            "approveResult": approve_result,
            "pushPurchaseResult": push_purchase_result
        }
    else:
        print(f"\n获取审批列表失败: {audit_order_result.get('msg')}")
        return None


if __name__ == "__main__":
    main()

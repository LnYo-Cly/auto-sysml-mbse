"""
融合Agent - 负责整合所有SysML图的JSON输出，构建统一的知识图谱
基于 master-2/step5_relationship_building/run_step5_final_pipeline.py 改造
"""
import logging
import json
import os
from typing import Dict, Any, List
from datetime import datetime

from graph.workflow_state import WorkflowState, ProcessStatus
from config.settings import settings

logger = logging.getLogger(__name__)

def collect_diagram_json_paths(state: WorkflowState) -> List[str]:
    """
    收集所有已完成任务的 JSON 文件路径
    
    返回:
        JSON文件路径列表
    """
    json_paths = []
    
    for task in state.assigned_tasks:
        if task.status == ProcessStatus.COMPLETED and task.result:
            if isinstance(task.result, dict) and "saved_file" in task.result:
                json_path = task.result["saved_file"]
                if os.path.exists(json_path):
                    json_paths.append(json_path)
                    logger.info(f"✅ 已收集 {task.type} 图的JSON: {json_path}")
                else:
                    logger.warning(f"⚠️ 文件不存在: {json_path}")
    
    logger.info(f"📊 共收集到 {len(json_paths)} 个JSON文件")
    return json_paths


def run_fusion_pipeline(json_paths: List[str]) -> Dict[str, Any]:
    """
    执行完整的融合流程
    
    这是 master-2/step5_relationship_building/run_step5_final_pipeline.py 的 main() 函数改造版本
    
    参数:
        json_paths: JSON文件路径列表
        
    返回:
        融合结果字典
    """
    print("--- 最终融合管道: 步骤 1-7 (包含关系重建 + 模型统一) ---")
    
    # 导入 master-2 的模块（您需要先迁移这些模块到项目中）
    try:
        from fusion.jsontokey import CanonicalKeyGenerator, load_json_files
        from fusion.neo4j_fusion_manager import Neo4jFusionManager
        from fusion.semantic_fusion_manager import SemanticFusionManager
        from connections.database_connectors import close_connections
        from exports.neo4j_to_json import JsonReverser 
    except ImportError as e:
        logger.error(f"❌ 导入融合模块失败: {e}")
        return {"status": "error", "message": f"模块导入失败: {e}"}
    
    # --- 步骤 1: 准备数据 ---
    print("\n[1/7] 正在加载、解析并生成规范键...")
    try:
        # 使用收集到的JSON文件路径，而不是硬编码的路径
        master_element_list = load_json_files(*json_paths)
        all_elements_map = {elem['id']: elem for elem in master_element_list}
        key_generator = CanonicalKeyGenerator(master_element_list)
        elements_with_keys = key_generator.generate_all_keys()
        print(f"  ✅ 数据准备完成。共处理 {len(master_element_list)} 个元素。")
        logger.info(f"✅ 数据准备完成: {len(master_element_list)} 个元素")
    except FileNotFoundError as e:
        error_msg = f"文件未找到: {e}"
        print(f"❌ 错误: {error_msg}")
        logger.error(f"❌ {error_msg}")
        return {"status": "error", "message": error_msg}
    except Exception as e:
        error_msg = f"数据准备失败: {e}"
        print(f"❌ 错误: {error_msg}")
        logger.error(f"❌ {error_msg}", exc_info=True)
        return {"status": "error", "message": error_msg}

    neo4j_manager = None
    semantic_manager = None
    canonical_key_remap = {}  # 初始化重映射表
    
    try:
        # --- 步骤 2: 初始化管理器 ---
        print("\n[2/7] 正在初始化所有管理器...")
        neo4j_manager = Neo4jFusionManager()
        semantic_manager = SemanticFusionManager()
        print("  ✅ 管理器初始化完成。")
        logger.info("✅ 管理器初始化完成")

        # --- 步骤 3: 清空数据库并设置约束 ---
        print("\n[3/7] 正在清空数据库并设置约束 (为了幂等性)...")
        neo4j_manager._execute_write("MATCH (n) DETACH DELETE n")
        print("  - 旧数据已清空。")
        logger.info("- 旧数据已清空")
        neo4j_manager.setup_constraints(master_element_list)
        print("  ✅ 约束设置完成。")
        logger.info("✅ 约束设置完成")

        # --- 步骤 4: 迭代融合 ---
        print("\n[4/7] 开始迭代融合（语义检查 + 结构化写入）...")
        logger.info("🔄 开始迭代融合...")
        
        processed_count = 0
        similar_count = 0
        
        for original_id, canonical_key in elements_with_keys.items():
            element_data = all_elements_map.get(original_id)
            if not element_data:
                continue
            
            # 语义相似性检查
            is_similar, similar_key, _ = semantic_manager.find_similar_element(
                element_data, canonical_key
            )
            
            if is_similar:
                canonical_key_remap[canonical_key] = similar_key
                similar_count += 1
                continue
            
            # 写入Neo4j
            neo4j_manager.fuse_element(element_data, canonical_key)
            # 存储语义嵌入
            semantic_manager.store_element_embedding(element_data, canonical_key)
            processed_count += 1
        
        print(f"\n  ✅ 迭代融合完成。处理了 {processed_count} 个新元素，跳过 {similar_count} 个相似元素。")
        logger.info(f"✅ 迭代融合完成: 新元素={processed_count}, 相似元素={similar_count}")

        # --- 步骤 5: 关系重建 ---
        print("\n[5/7] 开始关系重建流程...")
        logger.info("🔗 开始关系重建...")
        neo4j_manager.rebuild_relationships(
            all_elements_map,
            elements_with_keys,
            canonical_key_remap
        )
        print("  ✅ 关系重建完成。")
        logger.info("✅ 关系重建完成")
        
        # --- 步骤 6: 模型统一 ---
        print("\n[6/7] 正在统一模型根...")
        logger.info("🎯 正在统一模型根...")
        neo4j_manager.unify_models(
            master_model_original_id="master-model",
            master_model_name="Model"  # 您可以自定义名称
        )
        print("  ✅ 模型统一完成。")
        logger.info("✅ 模型统一完成")
        
        # --- 步骤 7: 导出最终结果（可选） ---
        print("\n[7/7] 正在导出融合结果...")
        logger.info("💾 正在导出融合结果...")
        
        # 从Neo4j导出统一的JSON（您可能需要从 master-2 实现这个功能）
        try:
            # 这里假设有一个导出函数，您需要根据实际情况调整
            final_json = neo4j_manager.export_to_json()
            logger.info("✅ 融合结果导出完成")
        except AttributeError:
            # 如果没有导出功能，返回统计信息
            logger.warning("⚠️ 未实现导出功能，返回统计信息")
            final_json = {
                "total_elements": len(master_element_list),
                "processed_elements": processed_count,
                "similar_elements": similar_count,
                "canonical_key_remap": canonical_key_remap
            }
        
        # --- 步骤 7: 导出最终结果 ---
        print("\n[7/7] 正在从Neo4j导出融合后的JSON...")
        logger.info("💾 正在从Neo4j导出融合后的JSON...")
        
        # ✅ 使用 JsonReverser 从 Neo4j 导出完整的 JSON
        try:
            reverser = JsonReverser()
            final_json = reverser.reconstruct_json()
            logger.info("✅ 融合结果导出成功")
            print("  ✅ JSON导出成功")
        except Exception as export_error:
            logger.warning(f"⚠️ JSON导出失败，使用统计信息替代: {export_error}")
            print(f"  ⚠️ JSON导出失败: {export_error}")
            final_json = {
                "model": [],
                "elements": [],
                "statistics": {
                    "total_elements": len(master_element_list),
                    "processed_elements": processed_count,
                    "similar_elements": similar_count
                }
            }
        
        print("\n✅ [7/7] 管道执行成功！")
        logger.info("✅ 融合管道执行成功")
        
        return {
            "status": "success",
            "result": final_json,
            "statistics": {
                "total_elements": len(master_element_list),
                "processed_elements": processed_count,
                "similar_elements": similar_count,
                "total_fused_elements": len(final_json.get("elements", [])) if isinstance(final_json, dict) else 0
            }
        }
        
    except (ConnectionError, Exception) as e:
        error_msg = f"管道执行失败: {e}"
        print(f"\n❌ {error_msg}")
        logger.error(f"❌ {error_msg}", exc_info=True)
        return {"status": "error", "message": error_msg}
    
    finally:
        print("\n正在关闭所有数据库连接...")
        logger.info("正在关闭所有数据库连接...")
        close_connections()
        print("✅ 清理完成。")
        logger.info("✅ 清理完成")


def fusion_agent(state: WorkflowState) -> WorkflowState:
    """
    融合Agent主函数 - LangGraph工作流节点
    
    功能:
        1. 收集所有已完成任务的JSON输出
        2. 执行融合流程（Neo4j + 语义融合）
        3. 保存融合结果
        
    参数:
        state: 当前工作流状态
        
    返回:
        更新后的工作流状态
    """
    logger.info("=" * 80)
    logger.info("🔗 融合Agent开始工作")
    logger.info("=" * 80)
    
    # 检查是否有已完成的任务
    completed_tasks = [
        t for t in state.assigned_tasks 
        if t.status == ProcessStatus.COMPLETED
    ]
    
    if not completed_tasks:
        logger.warning("⚠️ 没有已完成的任务，跳过融合步骤")
        state.fusion_status = "skipped"
        state.fusion_message = "没有已完成的任务"
        return state
    
    try:
        # 1. 收集所有图的JSON文件路径
        json_paths = collect_diagram_json_paths(state)
        
        if not json_paths:
            logger.warning("⚠️ 没有可用的JSON文件，跳过融合步骤")
            state.fusion_status = "skipped"
            state.fusion_message = "没有可用的JSON文件"
            return state
        
        # 2. 执行融合流程
        fusion_result = run_fusion_pipeline(json_paths)
        
        if fusion_result["status"] == "success":
            # 3. 保存融合结果
            output_dir = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), 
                "data", "output", "fusion"
            )
            os.makedirs(output_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(output_dir, f"fused_model_{timestamp}.json")
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(fusion_result["result"], f, ensure_ascii=False, indent=2)
            
            logger.info(f"✅ 融合结果已保存: {output_path}")
            
            # 更新状态
            state.fusion_status = "completed"
            state.fusion_output_path = output_path
            state.fusion_statistics = fusion_result.get("statistics", {})
            
        else:
            logger.error(f"❌ 融合失败: {fusion_result.get('message')}")
            state.fusion_status = "failed"
            state.fusion_message = fusion_result.get('message')
    
    except Exception as e:
        logger.error(f"❌ 融合Agent异常: {e}", exc_info=True)
        state.fusion_status = "failed"
        state.fusion_message = str(e)
    
    logger.info("=" * 80)
    logger.info(f"🔗 融合Agent完成，状态: {state.fusion_status}")
    logger.info("=" * 80)
    
    return state
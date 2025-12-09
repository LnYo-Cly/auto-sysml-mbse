import logging
import os
import json
from datetime import datetime
from graph.workflow_state import WorkflowState, ProcessStatus
from xml_generator.unify_sysml_to_csm import generate_unified_xmi
from exports.remove_orphan_nodes import clean_json_data
from exports.repair_orphan_references import repair_json_data

logger = logging.getLogger(__name__)

def xml_generator_agent(state: WorkflowState) -> WorkflowState:
    """
    XML生成器Agent - 将融合后的JSON模型转换为XMI格式
    
    参数:
        state: 工作流状态
        
    返回:
        更新后的工作流状态
    """
    logger.info("=" * 80)
    logger.info("🔨 开始执行 XML Generator Agent")
    logger.info("=" * 80)
    
    # 检查融合是否成功
    if state.fusion_status != "completed":
        logger.warning("⚠️ 融合未完成，跳过XML生成")
        state.xml_generation_status = "skipped"
        state.xml_generation_message = "融合未完成"
        return state
    
    # 检查融合输出文件是否存在
    if not state.fusion_output_path or not os.path.exists(state.fusion_output_path):
        logger.error("❌ 融合输出文件不存在")
        state.xml_generation_status = "failed"
        state.xml_generation_message = "融合输出文件不存在"
        return state
    
    try:
        # 读取融合后的JSON文件
        logger.info(f"📖 读取融合JSON文件: {state.fusion_output_path}")
        with open(state.fusion_output_path, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        
        # 先移除孤立节点，避免悬挂元素继续向下游传播
        logger.info("🧹 清理JSON数据，移除孤立节点...")
        json_data = clean_json_data(json_data, check_type_refs=False, verbose=True)

        # 再修复悬挂引用或创建必要替身，确保生成的XMI无野引用
        logger.info("🔧 修复JSON数据，处理孤立引用...")
        json_data = repair_json_data(json_data, verbose=True, enable_cascade_delete=True)
        
        # 生成XMI
        logger.info("🔄 开始生成XMI...")
        xmi_content = generate_unified_xmi(json_data)
        
        if not xmi_content:
            raise Exception("XMI生成失败，返回内容为空")
        
        # 确定输出路径
        output_dir = state.output_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data", "output"
        )
        os.makedirs(output_dir, exist_ok=True)
        
        # 生成输出文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        xmi_filename = f"unified_model_{timestamp}.xmi"
        xmi_output_path = os.path.join(output_dir, xmi_filename)
        
        # 写入XMI文件
        logger.info(f"💾 保存XMI文件: {xmi_output_path}")
        with open(xmi_output_path, 'w', encoding='utf-8') as f:
            f.write(xmi_content)
        
        # 更新状态
        state.xml_generation_status = "completed"
        state.xml_output_path = xmi_output_path
        state.xml_generation_message = "XMI生成成功"
        
        # 统计信息
        file_size = os.path.getsize(xmi_output_path)
        state.xml_statistics = {
            "file_size_bytes": file_size,
            "file_size_kb": round(file_size / 1024, 2),
            "generation_time": datetime.now().isoformat()
        }
        
        logger.info("=" * 80)
        logger.info("✅ XML生成完成")
        logger.info(f"📂 输出路径: {xmi_output_path}")
        logger.info(f"📊 文件大小: {state.xml_statistics['file_size_kb']} KB")
        logger.info("=" * 80)
        
    except FileNotFoundError as e:
        logger.error(f"❌ 文件未找到: {str(e)}")
        state.xml_generation_status = "failed"
        state.xml_generation_message = f"文件未找到: {str(e)}"
    except json.JSONDecodeError as e:
        logger.error(f"❌ JSON解析失败: {str(e)}")
        state.xml_generation_status = "failed"
        state.xml_generation_message = f"JSON解析失败: {str(e)}"
    except Exception as e:
        logger.error(f"❌ XML生成失败: {str(e)}", exc_info=True)
        state.xml_generation_status = "failed"
        state.xml_generation_message = str(e)
    
    return state
import logging
from utils.logs import log_config
from graph.workflow import create_workflow
from graph.workflow_state import WorkflowState, ProcessStatus, SysMLTask
from config.settings import settings
import os
import glob

# 配置日志
logger = log_config()

def run_workflow(用户输入: str = "", 文档路径: str = "") -> WorkflowState:
    """
    运行工作流
    
    参数:
        用户输入: 用户的简短需求描述（可选）
        文档路径: 文档路径（可选）
        
    返回:
        最终的工作流状态
    """
    logger.info("=" * 80)
    logger.info("开始运行工作流")
    if 用户输入:
        logger.info(f"用户输入: {用户输入}")
    if 文档路径:
        logger.info(f"文档路径: {文档路径}")
    logger.info("=" * 80)
    
    # 创建初始状态
    initial_state = WorkflowState(
        input_short_req=用户输入,
        input_doc_path=文档路径,
        save_stages=settings.save_stages,
        enable_quality_enhancement=settings.enable_quality_enhancement,
        max_chunk_tokens=settings.max_chunk_tokens
    )
    
    # 创建并运行工作流
    workflow = create_workflow()
    
    try:
        # 执行工作流 - 返回的是字典
        result = workflow.invoke(initial_state)

        # 将字典转换回 WorkflowState 对象
        final_state = WorkflowState(**result)
        
        # 检查执行结果
        if final_state.status == ProcessStatus.COMPLETED or final_state.status == ProcessStatus.PROCESSING:
            logger.info("=" * 80)
            logger.info("工作流执行成功！")
            logger.info("=" * 80)
            if final_state.expanded_content:
                logger.info(f"\n最终扩展文档预览:\n{final_state.expanded_content[:500]}...")
            if final_state.text_chunks:
                logger.info(f"\n生成了 {len(final_state.text_chunks)} 个文档分块")
            if final_state.assigned_tasks:
                logger.info(f"\n分配了 {len(final_state.assigned_tasks)} 个SysML任务")
                # 显示任务完成情况
                completed = sum(1 for t in final_state.assigned_tasks if t.status == ProcessStatus.COMPLETED)
                logger.info(f"已完成: {completed}/{len(final_state.assigned_tasks)}")
            
            # 显示融合结果
            if final_state.fusion_status:
                logger.info(f"\n融合状态: {final_state.fusion_status}")
                if final_state.fusion_output_path:
                    logger.info(f"融合输出: {final_state.fusion_output_path}")
                if final_state.fusion_statistics:
                    logger.info(f"融合统计: {final_state.fusion_statistics}")
        else:
            logger.error(f"工作流执行失败: {final_state.error_message}")
        
        return final_state
        
    except Exception as e:
        logger.error(f"工作流执行出错: {str(e)}", exc_info=True)
        initial_state.error_message = str(e)
        initial_state.status = ProcessStatus.FAILED
        return initial_state


def run_fusion_only(json_dir: str = None) -> WorkflowState:
    """
    仅运行融合流程（跳过需求扩展、文档处理、任务分类）
    
    参数:
        json_dir: JSON文件目录（可选，默认使用 data/output/ 下的所有图）
        
    返回:
        工作流状态
    """
    logger.info("=" * 80)
    logger.info("直接运行融合流程")
    logger.info("=" * 80)
    
    from agents.fusion_agent import fusion_agent
    
    # 创建一个模拟的初始状态
    initial_state = WorkflowState(
        input_short_req="",
        input_doc_path="",
        status=ProcessStatus.COMPLETED,
        assigned_tasks=[]
    )
    
    # 查找所有已生成的JSON文件
    if json_dir is None:
        # 默认扫描所有输出目录
        base_output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "output")
        diagram_types = [
            "activity_diagrams",
            "block_diagrams", 
            "requirement_diagrams",
            "state_machine_diagrams",
            "usecase_diagrams",
            "parametric_diagrams",
            "sequence_diagrams"
        ]
        
        json_files = []
        for diagram_type in diagram_types:
            pattern = os.path.join(base_output_dir, diagram_type, "*.json")
            found = glob.glob(pattern)
            json_files.extend(found)
            if found:
                logger.info(f"✅ 在 {diagram_type} 中找到 {len(found)} 个文件")
    else:
        # 扫描指定目录
        json_files = glob.glob(os.path.join(json_dir, "*.json"))
    
    if not json_files:
        logger.warning("⚠️ 未找到任何JSON文件")
        initial_state.fusion_status = "skipped"
        initial_state.fusion_message = "未找到任何JSON文件"
        return initial_state
    
    logger.info(f"\n✅ 总共找到 {len(json_files)} 个JSON文件:")
    for f in json_files:
        logger.info(f"   - {os.path.basename(f)}")
    
    # 为每个JSON文件创建一个模拟的任务
    for idx, json_file in enumerate(json_files):
        # 从文件名推断图类型
        basename = os.path.basename(json_file).lower()
        if "activity" in basename:
            diagram_type = "Activity"
        elif "block" in basename or "bdd" in basename or "ibd" in basename:
            diagram_type = "Block"
        elif "requirement" in basename:
            diagram_type = "Requirement"
        elif "state_machine" in basename:
            diagram_type = "State Machine"
        elif "use_case" in basename or "usecase" in basename:
            diagram_type = "Use Case"
        elif "parametric" in basename or "parameter" in basename:
            diagram_type = "Parameter"
        elif "sequence" in basename:
            diagram_type = "Sequence"
        else:
            diagram_type = "Unknown"
        
        task = SysMLTask(
            id=f"FUSION-TASK-{idx:04d}",
            type=diagram_type,
            content=f"Fusion task for {basename}",
            status=ProcessStatus.COMPLETED,
            result={"saved_file": json_file}
        )
        initial_state.assigned_tasks.append(task)
    
    # 执行融合
    try:
        final_state = fusion_agent(initial_state)
        return final_state
    except Exception as e:
        logger.error(f"融合执行出错: {str(e)}", exc_info=True)
        initial_state.fusion_status = "failed"
        initial_state.fusion_message = str(e)
        return initial_state


def run_fusion_and_xml(json_dir: str = None) -> WorkflowState:
    """
    运行融合 + XML生成流程
    
    参数:
        json_dir: JSON文件目录（可选，默认使用 data/output/ 下的所有图）
        
    返回:
        工作流状态
    """
    logger.info("=" * 80)
    logger.info("运行融合 + XML生成流程")
    logger.info("=" * 80)
    
    from agents.fusion_agent import fusion_agent
    from agents.xml_generator_agent import xml_generator_agent
    
    # 先执行融合
    fusion_state = run_fusion_only(json_dir)
    
    # 检查融合是否成功
    if fusion_state.fusion_status != "completed":
        logger.warning("⚠️ 融合未完成，跳过XML生成")
        return fusion_state
    
    # 执行XML生成
    try:
        logger.info("\n" + "=" * 80)
        logger.info("开始XML生成")
        logger.info("=" * 80)
        final_state = xml_generator_agent(fusion_state)
        return final_state
    except Exception as e:
        logger.error(f"XML生成出错: {str(e)}", exc_info=True)
        fusion_state.xml_generation_status = "failed"
        fusion_state.xml_generation_message = str(e)
        return fusion_state


def run_xml_only(fusion_json_path: str = None) -> WorkflowState:
    """
    仅运行XML生成（使用已有的融合JSON文件）
    
    参数:
        fusion_json_path: 融合JSON文件路径（可选，默认使用最新的融合文件）
        
    返回:
        工作流状态
    """
    logger.info("=" * 80)
    logger.info("仅运行XML生成流程")
    logger.info("=" * 80)
    
    from agents.xml_generator_agent import xml_generator_agent
    
    # 查找融合JSON文件
    if fusion_json_path is None:
        # 自动查找最新的融合文件
        base_output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "output")
        fusion_dir = os.path.join(base_output_dir, "fusion")
        
        if not os.path.exists(fusion_dir):
            logger.error(f"❌ 融合目录不存在: {fusion_dir}")
            initial_state = WorkflowState(
                input_short_req="",
                input_doc_path="",
                xml_generation_status="failed",
                xml_generation_message="融合目录不存在"
            )
            return initial_state
        
        # 查找所有融合JSON文件
        fusion_files = glob.glob(os.path.join(fusion_dir, "fused_model_*.json"))
        
        if not fusion_files:
            logger.error("❌ 未找到融合JSON文件")
            initial_state = WorkflowState(
                input_short_req="",
                input_doc_path="",
                xml_generation_status="failed",
                xml_generation_message="未找到融合JSON文件"
            )
            return initial_state
        
        # 使用最新的文件
        fusion_json_path = max(fusion_files, key=os.path.getmtime)
        logger.info(f"✅ 找到融合文件: {fusion_json_path}")
    else:
        if not os.path.exists(fusion_json_path):
            logger.error(f"❌ 指定的融合文件不存在: {fusion_json_path}")
            initial_state = WorkflowState(
                input_short_req="",
                input_doc_path="",
                xml_generation_status="failed",
                xml_generation_message=f"文件不存在: {fusion_json_path}"
            )
            return initial_state
    
    # 创建状态并设置融合信息
    initial_state = WorkflowState(
        input_short_req="",
        input_doc_path="",
        fusion_status="completed",
        fusion_output_path=fusion_json_path
    )
    
    # 执行XML生成
    try:
        final_state = xml_generator_agent(initial_state)
        return final_state
    except Exception as e:
        logger.error(f"XML生成出错: {str(e)}", exc_info=True)
        initial_state.xml_generation_status = "failed"
        initial_state.xml_generation_message = str(e)
        return initial_state


def main():
    """主函数"""
    print("=" * 80)
    print("欢迎使用 SysML 自动建模系统")
    print("=" * 80)
    
    print("\n请选择输入方式:")
    print("1. 输入简短需求描述（AI自动扩展为详细文档）")
    print("2. 读取已有文档（Word/Markdown/文本文件）")
    print("3. 混合模式（先扩展需求，再读取补充文档）")
    print("4. 仅运行融合（使用已生成的JSON文件）")
    print("5. 运行融合 + XML生成（使用已生成的JSON文件）")
    print("6. 仅运行XML生成（使用已有的融合JSON文件）")

    choice = input("\n请选择 (1/2/3/4/5/6): ").strip()

    # 选项4 - 仅融合
    if choice == "4":
        print("\n" + "=" * 80)
        print("🔗 仅运行融合流程")
        print("=" * 80)
        
        use_custom_dir = input("\n是否指定JSON目录？(y/n，默认n自动扫描data/output): ").strip().lower()
        
        if use_custom_dir == "y":
            json_dir = input("请输入JSON文件目录路径: ").strip()
            if not os.path.isdir(json_dir):
                print(f"❌ 错误: 目录不存在: {json_dir}")
                return
            final_state = run_fusion_only(json_dir=json_dir)
        else:
            final_state = run_fusion_only()
        
        # 输出融合结果
        print("\n" + "=" * 80)
        if final_state.fusion_status == "completed":
            print("✅ 融合完成！")
            print("=" * 80)
            print(f"✅ 融合输出: {final_state.fusion_output_path}")
            if final_state.fusion_statistics:
                stats = final_state.fusion_statistics
                print(f"\n📊 统计信息:")
                print(f"   - 总元素数: {stats.get('total_elements', 'N/A')}")
                print(f"   - 处理元素: {stats.get('processed_elements', 'N/A')}")
                print(f"   - 相似元素: {stats.get('similar_elements', 'N/A')}")
                print(f"   - 融合后元素: {stats.get('total_fused_elements', 'N/A')}")
        elif final_state.fusion_status == "failed":
            print("❌ 融合失败!")
            print("=" * 80)
            print(f"错误信息: {final_state.fusion_message}")
        elif final_state.fusion_status == "skipped":
            print("⚠️ 融合已跳过")
            print("=" * 80)
            print(f"原因: {final_state.fusion_message}")
        print("=" * 80)
        return
    
    # 选项5 - 融合 + XML生成
    elif choice == "5":
        print("\n" + "=" * 80)
        print("🔗 运行融合 + XML生成流程")
        print("=" * 80)
        
        use_custom_dir = input("\n是否指定JSON目录？(y/n，默认n自动扫描data/output): ").strip().lower()
        
        if use_custom_dir == "y":
            json_dir = input("请输入JSON文件目录路径: ").strip()
            if not os.path.isdir(json_dir):
                print(f"❌ 错误: 目录不存在: {json_dir}")
                return
            final_state = run_fusion_and_xml(json_dir=json_dir)
        else:
            final_state = run_fusion_and_xml()
        
        # 输出结果
        print("\n" + "=" * 80)
        
        # 显示融合结果
        if final_state.fusion_status == "completed":
            print("✅ 融合完成！")
            print(f"   📂 融合输出: {final_state.fusion_output_path}")
            if final_state.fusion_statistics:
                stats = final_state.fusion_statistics
                print(f"\n   📊 融合统计:")
                print(f"      - 总元素数: {stats.get('total_elements', 'N/A')}")
                print(f"      - 处理元素: {stats.get('processed_elements', 'N/A')}")
                print(f"      - 相似元素: {stats.get('similar_elements', 'N/A')}")
                print(f"      - 融合后元素: {stats.get('total_fused_elements', 'N/A')}")
        elif final_state.fusion_status == "failed":
            print("❌ 融合失败!")
            print(f"   错误信息: {final_state.fusion_message}")
            print("=" * 80)
            return
        elif final_state.fusion_status == "skipped":
            print("⚠️ 融合已跳过")
            print(f"   原因: {final_state.fusion_message}")
            print("=" * 80)
            return
        
        # 显示XML生成结果
        print("\n" + "-" * 80)
        if final_state.xml_generation_status == "completed":
            print("✅ XML生成完成！")
            print(f"   📂 XMI输出: {final_state.xml_output_path}")
            if final_state.xml_statistics:
                stats = final_state.xml_statistics
                print(f"\n   📊 XML统计:")
                print(f"      - 文件大小: {stats.get('file_size_kb', 'N/A')} KB")
                print(f"      - 生成时间: {stats.get('generation_time', 'N/A')}")
        elif final_state.xml_generation_status == "failed":
            print("❌ XML生成失败!")
            print(f"   错误信息: {final_state.xml_generation_message}")
        elif final_state.xml_generation_status == "skipped":
            print("⚠️ XML生成已跳过")
            print(f"   原因: {final_state.xml_generation_message}")
        
        print("=" * 80)
        return
    
    # 选项6 - 仅XML生成
    elif choice == "6":
        print("\n" + "=" * 80)
        print("🔨 仅运行XML生成流程")
        print("=" * 80)
        
        use_custom_file = input("\n是否指定融合JSON文件？(y/n，默认n使用最新文件): ").strip().lower()
        
        if use_custom_file == "y":
            fusion_json_path = input("请输入融合JSON文件路径: ").strip()
            if not os.path.isfile(fusion_json_path):
                print(f"❌ 错误: 文件不存在: {fusion_json_path}")
                return
            final_state = run_xml_only(fusion_json_path=fusion_json_path)
        else:
            final_state = run_xml_only()
        
        # 输出XML生成结果
        print("\n" + "=" * 80)
        if final_state.xml_generation_status == "completed":
            print("✅ XML生成完成！")
            print("=" * 80)
            print(f"✅ XMI输出: {final_state.xml_output_path}")
            if final_state.xml_statistics:
                stats = final_state.xml_statistics
                print(f"\n📊 统计信息:")
                print(f"   - 文件大小: {stats.get('file_size_kb', 'N/A')} KB")
                print(f"   - 生成时间: {stats.get('generation_time', 'N/A')}")
            print(f"\n💡 提示: 可以使用 MagicDraw/Cameo 导入此XMI文件")
        elif final_state.xml_generation_status == "failed":
            print("❌ XML生成失败!")
            print("=" * 80)
            print(f"错误信息: {final_state.xml_generation_message}")
        elif final_state.xml_generation_status == "skipped":
            print("⚠️ XML生成已跳过")
            print("=" * 80)
            print(f"原因: {final_state.xml_generation_message}")
        print("=" * 80)
        return
    
    # 原有的选项 1/2/3 - 完整工作流
    用户输入 = ""
    文档路径 = ""
    
    if choice == "1":
        用户输入 = input("\n请输入您的简短需求描述: ").strip()
        if not 用户输入:
            print("❌ 错误: 需求描述不能为空")
            return
            
    elif choice == "2":
        文档路径 = input("\n请输入文档路径（支持 .docx/.md/.txt）: ").strip()
        if not 文档路径:
            print("❌ 错误: 文档路径不能为空")
            return
            
    elif choice == "3":
        用户输入 = input("\n请输入您的简短需求描述: ").strip()
        if not 用户输入:
            print("❌ 错误: 需求描述不能为空")
            return
        
        文档路径 = input("请输入补充文档路径（可选，直接回车跳过）: ").strip()
        
    else:
        print("❌ 错误: 无效的选择")
        return
    
    if not 用户输入 and not 文档路径:
        print("❌ 错误: 必须提供需求描述或文档路径")
        return
    
    # 运行完整工作流
    final_state = run_workflow(用户输入=用户输入, 文档路径=文档路径)
    
    # 输出结果
    print("\n" + "=" * 80)
    if final_state.status == ProcessStatus.COMPLETED or final_state.status == ProcessStatus.PROCESSING:
        print("✅ 处理完成！")
        print("=" * 80)
        
        if final_state.expanded_content and 用户输入:
            print(f"📝 扩展文档已保存到 data/output 目录")
            
        if final_state.text_chunks:
            print(f"📄 文档已分割为 {len(final_state.text_chunks)} 个chunks")
            
        if final_state.assigned_tasks:
            print(f"🎯 识别并分配了 {len(final_state.assigned_tasks)} 个SysML任务:")
            
            # 统计任务类型和状态
            task_stats = {}
            for task in final_state.assigned_tasks:
                if task.type not in task_stats:
                    task_stats[task.type] = {"total": 0, "completed": 0, "failed": 0}
                task_stats[task.type]["total"] += 1
                if task.status == ProcessStatus.COMPLETED:
                    task_stats[task.type]["completed"] += 1
                elif task.status == ProcessStatus.FAILED:
                    task_stats[task.type]["failed"] += 1
            
            for task_type, stats in task_stats.items():
                status_icon = "✅" if stats["completed"] == stats["total"] else "⏳"
                if stats["failed"] > 0:
                    status_icon = "⚠️"
                print(f"   {status_icon} {task_type}: {stats['completed']}/{stats['total']} 完成")
        
        # 显示融合结果
        if final_state.fusion_status:
            print(f"\n🔗 融合状态: {final_state.fusion_status}")
            if final_state.fusion_status == "completed":
                print(f"   ✅ 融合输出: {final_state.fusion_output_path}")
                if final_state.fusion_statistics:
                    stats = final_state.fusion_statistics
                    print(f"   📊 统计信息:")
                    print(f"      - 总元素数: {stats.get('total_elements', 'N/A')}")
                    print(f"      - 处理元素: {stats.get('processed_elements', 'N/A')}")
                    print(f"      - 相似元素: {stats.get('similar_elements', 'N/A')}")
            elif final_state.fusion_status == "failed":
                print(f"   ❌ 融合失败: {final_state.fusion_message}")
            elif final_state.fusion_status == "skipped":
                print(f"   ⚠️ 已跳过融合: {final_state.fusion_message}")
                    
        # 显示XML生成结果
        if final_state.xml_generation_status:
            print(f"\n🔨 XML生成状态: {final_state.xml_generation_status}")
            if final_state.xml_generation_status == "completed":
                print(f"   ✅ XMI输出: {final_state.xml_output_path}")
                if final_state.xml_statistics:
                    stats = final_state.xml_statistics
                    print(f"   📊 文件大小: {stats.get('file_size_kb', 'N/A')} KB")
            elif final_state.xml_generation_status == "failed":
                print(f"   ❌ XML生成失败: {final_state.xml_generation_message}")
            elif final_state.xml_generation_status == "skipped":
                print(f"   ⚠️ 已跳过XML生成: {final_state.xml_generation_message}")
      
        print("\n📂 输出文件保存在: data/output/")
        
    else:
        print("❌ 处理失败!")
        print("=" * 80)
        print(f"错误信息: {final_state.error_message}")
        
    print("=" * 80)


if __name__ == "__main__":
    main()
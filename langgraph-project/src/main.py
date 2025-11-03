import logging
from utils.logs import log_config
from graph.workflow import create_workflow
from graph.workflow_state import WorkflowState, ProcessStatus
from config.settings import settings

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
        if final_state.status == ProcessStatus.COMPLETED:
            logger.info("=" * 80)
            logger.info("工作流执行成功！")
            logger.info("=" * 80)
            if final_state.expanded_content:
                logger.info(f"\n最终扩展文档预览:\n{final_state.expanded_content[:500]}...")
            if final_state.text_chunks:
                logger.info(f"\n生成了 {len(final_state.text_chunks)} 个文档分块")
            if final_state.assigned_tasks:
                logger.info(f"\n分配了 {len(final_state.assigned_tasks)} 个SysML任务")
        else:
            logger.error(f"工作流执行失败: {final_state.error_message}")
        
        return final_state
        
    except Exception as e:
        logger.error(f"工作流执行出错: {str(e)}", exc_info=True)
        initial_state.error_message = str(e)
        initial_state.status = ProcessStatus.FAILED
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
    
    choice = input("\n请选择 (1/2/3): ").strip()
    
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
    
    # 运行工作流
    final_state = run_workflow(用户输入=用户输入, 文档路径=文档路径)
    
    # 输出结果
    print("\n" + "=" * 80)
    if final_state.status == ProcessStatus.COMPLETED:
        print("✅ 处理完成！")
        print("=" * 80)
        
        if final_state.expanded_content and 用户输入:
            print(f"📝 扩展文档已保存到 data/output 目录")
            
        if final_state.text_chunks:
            print(f"📄 文档已分割为 {len(final_state.text_chunks)} 个chunks")
            
        if final_state.assigned_tasks:
            print(f"🎯 识别并分配了 {len(final_state.assigned_tasks)} 个SysML任务:")
            
            # 统计任务类型
            task_types = {}
            for task in final_state.assigned_tasks:
                task_types[task.type] = task_types.get(task.type, 0) + 1
            
            for task_type, count in task_types.items():
                status_icon = "✅" if any(t.type == task_type and t.status == ProcessStatus.COMPLETED 
                                         for t in final_state.assigned_tasks) else "⏳"
                print(f"   {status_icon} {task_type}: {count} 个任务")
                
        print("\n📂 输出文件保存在: data/output/")
        
    else:
        print("❌ 处理失败!")
        print("=" * 80)
        print(f"错误信息: {final_state.error_message}")
        
    print("=" * 80)


if __name__ == "__main__":
    main()
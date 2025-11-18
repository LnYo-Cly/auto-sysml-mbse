"""
活动图Agent - 负责基于输入内容创建SysML活动图并适配到现有系统

说明:
- 暂时使用简短的prompt占位符，完整prompt（含 description 示例）由你在下一次要求时提供并替换。
- 生成的每个实体都会尽量包含 description 字段；若缺失会自动补充默认 description。
"""
import logging
import json
import os
import re
from typing import Dict, Any, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field

from langchain_openai import ChatOpenAI
from json_repair import repair_json

from graph.workflow_state import WorkflowState, ProcessStatus
from config.settings import settings

logger = logging.getLogger(__name__)

# ==================== 简要 Prompt 占位（下一次你要求时我会补全详细 prompt，包括 description 示例） ====================
PROMPT_COT_SYSTEM = """
## 角色
你是一位专业的 SysML 活动图建模专家。你精通 SysML 活动图的规范，能够准确地从流程或工作流的自然语言描述中提取出活动、动作、对象、控制流、对象流、分区（泳道）、决策点、并发等元素，并理解它们之间的关系。

## 规则
你的目标是根据输入的文本描述，分析并生成构建 SysML 活动图所需的元素信息。请遵循以下步骤进行思考和分析，并生成中间的思考过程：

**核心要求：为每个识别出的元素（包、活动、节点、块、分区、引脚、流等）都必须生成一个 `description` 字段。该字段必须以 `原文：` 开头，引用输入文本中最相关的句子，然后用 `简化：` 提供简明扼要的解释。**

1.  **识别主要活动和包 (Package & Activity)**:
    *   确定文本描述的核心流程或活动，将其作为顶层 `Activity`。
    *   如果描述暗示了模块化或分组，可以定义一个 `Package` 来包含所有相关元素。
    *   为每个识别的元素分配合理的名称、临时ID，以及包含原文引用的 `description`。

2.  **识别活动节点 (Activity Nodes)**:
    *   **动作 (Actions)**: 找出流程中的具体步骤或任务 (动词短语)。识别为 `CallBehaviorAction` 或 `OpaqueAction`。
    *   **控制节点 (Control Nodes)**: 识别 `InitialNode` (起点), `ActivityFinalNode` (终点), `ForkNode` (并发), `JoinNode` (同步), `DecisionNode` (分支), `MergeNode` (合并)。
    *   **对象节点 (Object Nodes)**: 识别 `CentralBufferNode` (共享数据缓存)。
    *   为每个节点分配名称、临时ID和 `description`。

3.  **识别数据类型和参与者 (Blocks)**:
    *   **数据类型 (Blocks for Types)**: 识别流程中传递的数据、文档、消息等。定义为 `Block`。
    *   **参与者/系统 (Blocks for Partitions)**: 识别执行动作的角色、部门、系统。定义为 `Block`。
    *   为每个 Block 分配名称、临时ID和 `description`。

4.  **识别活动分区 (Activity Partitions / Swimlanes)**:
    *   根据第3步识别的参与者，定义 `ActivityPartition` (泳道)。
    *   明确每个分区 `represents` 哪个参与者 Block。
    *   将第2步的动作节点分配到相应分区中。
    *   为每个分区分配名称、临时ID和 `description`。

5.  **识别引脚 (Pins)**:
    *   识别动作的输入 (`InputPin`) 和输出 (`OutputPin`)。
    *   为每个引脚命名，并关联第3步的数据类型 Block (`typeId`)。
    *   为每个引脚分配名称、临时ID和 `description`。

6.  **识别流 (Flows - Control & Object)**:
    *   **控制流 (Control Flow)**: 连接不传递数据的节点。
    *   **对象流 (Object Flow)**: 连接传递数据的节点（通常通过引脚或对象节点）。
    *   确定 `sourceId` 和 `targetId`。对于 `DecisionNode` 的出向流，记录 `guard` 条件。
    *   为每个流分配名称、临时ID和 `description`。

7.  **编译和整理**:
    *   汇总所有识别出的元素及其属性。
    *   准备一个清晰的、结构化的中间表示，概述所有信息。

## 样例

### 输入样例：
"请描述一个文档审查和批准的工作流程。
该流程从接收到文档提交开始。首先，由“文档处理服务”负责“准备文档”以供审阅。准备好的文档会存放在一个共享的“待审阅文档缓存”中。
接下来，流程需要并行处理：准备好的文档需要同时发送给“部门A”和“部门B”进行审阅。这两个部门分别执行各自的审阅活动（“部门A审阅”和“部门B审阅”），审阅完成后各自输出审阅状态。
当两个部门的审阅都完成后（需要等待两者均完成），“文档处理服务”会执行“汇总审阅结果”的动作，它接收来自两个部门的审阅状态，并生成一个“最终决策”结果，该决策结果会存放在“审阅决策缓存”中。
然后，基于这个“最终决策”，“文档处理服务”进行判断：如果决策是“批准”，则执行“标记文档已批准”的动作；如果决策是“拒绝”，则执行“标记文档已拒绝”的动作。
最后，处理完成后生成的“通知上下文”被传递给“通知服务”，由它执行“发送通知”的动作。通知发送完毕后，整个文档审查流程结束。

在这个流程中，不同的动作由不同的服务或部门负责执行：
- 文档处理服务：负责准备文档、汇总结果、标记批准/拒绝。
- 部门A：负责部门A的审阅。
- 部门B：负责部门B的审阅。
- 通知服务：负责发送通知。"

### 输出文本:
请你按照如下的7步进行思考推理并输出：

#### 第一步：识别主要活动和包
- 包:
    - 名称: "DocumentReview"
    - 临时系统 ID: pkg-docreview-uuid
    - 描述: "原文：请描述一个文档审查和批准的工作流程。简化：该包封装了整个文档审查流程的所有组件和活动。"
- 主要活动:
    - 名称: "主文档审查活动"
    - 临时系统 ID: act-main-review-uuid
    - 描述: "原文：请描述一个文档审查和批准的工作流程。简化：这是定义整个文档审查和批准流程的主要活动。"
- 子活动:
    - 名称: "部门A审查文档" (act-review-dept-a)
    - 名称: "部门B审查文档" (act-review-dept-b)

#### 第二步：识别活动节点
- **起始节点**:
    - 名称: "接收文档提交" (node-dr-start, InitialNode)
    - 描述: "原文：该流程从接收到文档提交开始。简化：流程的起点，代表接收到新的文档提交。"
- **动作节点**:
    - 名称: "准备文档" (node-dr-prepare)
    - 描述: "原文：由“文档处理服务”负责“准备文档”以供审阅。简化：此动作负责准备文档以供后续审阅。"
    - 名称: "汇总审阅结果" (node-dr-consolidate)
    - 描述: "原文：“文档处理服务”会执行“汇总审阅结果”的动作，它接收来自两个部门的审阅状态。简化：此动作负责将并行的审阅状态合并成一个最终决策。"
    - ... (其他动作节点同样包含描述)
- **控制节点**:
    - 名称: "分发审阅" (node-dr-fork, ForkNode)
    - 描述: "原文：准备好的文档需要同时发送给“部门A”和“部门B”进行审阅。简化：此节点将单一流程分叉为两个并行的审阅路径。"
    - 名称: "等待审阅完成" (node-dr-join, JoinNode)
    - 描述: "原文：当两个部门的审阅都完成后（需要等待两者均完成）。简化：此节点用于同步两个并行的审阅路径，等待它们都完成后再继续。"
    - ... (其他控制节点同样包含描述)
- **对象节点**:
    - 名称: "待审阅文档缓存" (cbuf-dr-prepared-doc, CentralBufferNode)
    - 描述: "原文：准备好的文档会存放在一个共享的“待审阅文档缓存”中。简化：这是一个共享缓冲区，用于存放已准备好但尚未被审阅的文档。"
    - ... (其他对象节点同样包含描述)

#### 第三步：识别数据类型和参与者 (Blocks)
- **数据类型**:
    - 名称: DocumentSubmission (blk-doc-submission-uuid)
    - 描述: "原文：该流程从接收到文档提交开始。简化：代表流程初始接收的文档提交对象。"
    - 名称: PreparedDocument (blk-prepared-doc-uuid)
    - 描述: "原文：准备好的文档会存放在一个共享的“待审阅文档缓存”中。简化：代表经过初步处理后，可供审阅的文档对象。"
    - ... (其他数据类型同样包含描述)
- **参与者**:
    - 名称: DocumentProcessingService (blk-docproc-svc-uuid)
    - 描述: "原文：文档处理服务：负责准备文档、汇总结果、标记批准/拒绝。简化：代表负责核心文档处理逻辑的系统或组件。"
    - ... (其他参与者同样包含描述)

#### 第四步：识别活动分区
- 名称: "文档处理服务分区" (grp-docproc-uuid)
- 描述: "原文：文档处理服务：负责准备文档、汇总结果、标记批准/拒绝。简化：此泳道代表由“文档处理服务”执行的所有活动。"
- ... (其他分区同样包含描述)

#### 第五步：识别引脚
- **Input Pins**:
    - 名称: `in_提交文档` (pin-dr-prepare-in)
    - 描述: "原文：该流程从接收到文档提交开始。简化：作为“准备文档”动作的输入，接收初始提交的文档。"
    - ... (其他引脚同样包含描述)

#### 第六步：识别流
- **Control Flows**:
    - 名称: "从分发到部门A" (edge-dr-cf4-fork-a)
    - 描述: "原文：准备好的文档需要同时发送给“部门A”和“部门B”进行审阅。简化：此控制流启动部门A的并行审阅路径。"
    - ... (其他控制流同样包含描述)
- **Object Flows**:
    - 名称: "准备好的文档流入缓存" (edge-dr-of2-prepare-buf)
    - 描述: "原文：准备好的文档会存放在一个共享的“待审阅文档缓存”中。简化：此对象流将“准备文档”动作的输出（PreparedDocument）传递到共享缓存中。"
    - ... (其他对象流同样包含描述)

#### 第七步：整理优化输出
---
模型: DocumentReviewApprovalModel (model-docreview-uuid)
  包: DocumentReview (pkg-docreview-uuid)
    描述: "原文：请描述一个文档审查和批准的工作流程。简化：该包封装了整个文档审查流程的所有组件和活动。"
    
    包含块 (数据类型):
      - DocumentSubmission (blk-doc-submission-uuid), 描述: "原文：该流程从接收到文档提交开始。简化：代表流程初始接收的文档提交对象。"
      - ...
    包含块 (参与者):
      - DocumentProcessingService (blk-docproc-svc-uuid), 描述: "原文：文档处理服务：负责准备文档、汇总结果、标记批准/拒绝。简化：代表负责核心文档处理逻辑的系统或组件。"
      - ...
    包含活动:
      - 主文档审查活动 (act-main-review-uuid), 描述: "原文：请描述一个文档审查和批准的工作流程。简化：这是定义整个文档审查和批准流程的主要活动。"
      - ...

活动: 主文档审查活动 (act-main-review-uuid)
  节点:
    - InitialNode: 接收文档提交 (node-dr-start), 描述: "原文：该流程从接收到文档提交开始。简化：流程的起点，代表接收到新的文档提交。"
    - CallBehaviorAction: 准备文档 (node-dr-prepare), 描述: "原文：由“文档处理服务”负责“准备文档”以供审阅。简化：此动作负责准备文档以供后续审阅。"
      - InputPin: in_提交文档, 描述: "..."
      - OutputPin: out_待审阅文档, 描述: "..."
    - ...
  边 (Flows):
    - ControlFlow: (decision->approve [approved]), 描述: "原文：如果决策是“批准”，则执行“标记文档已批准”的动作。简化：在决策为“批准”时触发的控制流。"
    - ObjectFlow: (prepare.out->buffer_doc), 描述: "原文：准备好的文档会存放在一个共享的“待审阅文档缓存”中。简化：此对象流将“准备文档”动作的输出传递到共享缓存中。"
    - ...
  分区 (Partitions):
    - 文档处理服务 (grp-docproc-uuid), 描述: "原文：文档处理服务：负责准备文档、汇总结果、标记批准/拒绝。简化：此泳道代表由“文档处理服务”执行的所有活动。"
    - ...
---
"""
PROMPT_JSON_SYSTEM = """
根据以上详细的推理和整理优化输出，请严格按照以下 JSON 格式生成 SysML 活动图的完整描述。请确保：
1.  所有 `id` 字段都是全局唯一的。
2.  **每个元素都必须包含一个 `description` 字段**，其内容应与推理步骤中生成的描述保持一致。
3.  `parentId` 正确反映元素的包含关系。
4.  `typeId` (用于 Pin 和 CentralBufferNode) 正确引用相应的 Block ID。
5.  `representsId` (用于 ActivityPartition) 正确引用代表的参与者 Block ID。
6.  `sourceId` 和 `targetId` (用于 Flow) 正确引用源和目标元素的 ID。
7.  `behavior` (用于 CallBehaviorAction) 如果调用子活动，应引用子活动的 ID。
8.  `guard` (用于从 DecisionNode 出发的 ControlFlow) 被正确设置。
9.  JSON根对象只包含 `model` 和 `elements` 两个键。

## 示例JSON参考如下
```json
{
  "model": [
    {
      "id": "model-unique-id",
      "name": "ModelName",
      "description": "模型的总体描述，说明其目的和范围。"
    }
  ],
  "elements": [
    {
      "id": "pkg-unique-id",
      "type": "Package",
      "name": "PackageName",
      "description": "原文：... 简化：包的描述，说明其包含的内容和职责。"
    },
    {
      "id": "blk-data-type-id",
      "type": "Block",
      "name": "DataTypeName",
      "parentId": "pkg-unique-id",
      "description": "原文：... 简化：数据类型的描述，说明其代表什么信息。"
    },
    {
      "id": "act-main-activity-id",
      "type": "Activity",
      "name": "MainActivityName",
      "parentId": "pkg-unique-id",
      "nodes": ["node-initial-id", "node-action-id"],
      "edges": ["edge-control-flow-id"],
      "groups": ["grp-partition-id"],
      "description": "原文：... 简化：主要活动的描述，概述其完整流程。"
    },
    {
      "id": "grp-partition-id",
      "type": "ActivityPartition",
      "name": "PartitionName",
      "representsId": "blk-actor-system-id",
      "parentId": "act-main-activity-id",
      "nodeIds": ["node-action-id"],
      "description": "原文：... 简化：活动分区的描述，说明其代表哪个参与者。"
    },
    {
      "id": "node-action-id",
      "type": "CallBehaviorAction",
      "name": "ActionName",
      "parentId": "act-main-activity-id",
      "description": "原文：... 简化：动作节点的描述，说明其执行的具体任务。"
    },
    {
      "id": "pin-input-id",
      "type": "InputPin",
      "name": "InputPinName",
      "typeId": "blk-data-type-id",
      "parentId": "node-action-id",
      "description": "原文：... 简化：输入引脚的描述，说明其接收的数据。"
    },
    {
      "id": "edge-control-flow-id",
      "type": "ControlFlow",
      "sourceId": "node-source-id",
      "targetId": "node-target-id",
      "guard": "[condition]",
      "parentId": "act-main-activity-id",
      "description": "原文：... 简化：流的描述，说明其连接关系和触发条件。"
    }
  ]
}
```
请严格按照上面的JSON结构输出结果。
"""

# ==================== Pydantic 简单模型（用于可选的严格校验） ====================
class DiagramModel(BaseModel):
    id: str = Field(description="模型唯一ID")
    name: str = Field(description="模型名称")

class ActivityDiagramOutput(BaseModel):
    model: List[DiagramModel] = Field(description="模型列表")
    elements: List[Dict[str, Any]] = Field(description="元素列表（活动图元素）")

# ==================== 辅助函数 ====================

def get_activity_output_dir() -> str:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    output_dir = os.path.join(project_root, "data", "output", "activity_diagrams")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"创建活动图输出目录: {output_dir}")
    return output_dir

def save_activity_diagram(result: Dict[str, Any], task_id: str) -> str:
    try:
        output_dir = get_activity_output_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"activity_diagram_{task_id}_{timestamp}.json"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ 活动图已保存到: {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"保存活动图失败: {e}", exc_info=True)
        return ""

def validate_and_fix_json(json_str: str) -> Dict[str, Any]:
    """清理代码块，尝试解析，失败则用 repair_json 修复"""
    try:
        if "```json" in json_str:
            json_str = json_str.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```", 1)[1].split("```", 1)[0].strip()
        # 转义孤立反斜杠
        json_str = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', json_str)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON解析失败，尝试修复: {e}")
            fixed = repair_json(json_str)
            return json.loads(fixed)
    except Exception as e:
        logger.error(f"无法解析或修复JSON: {e}", exc_info=True)
        raise

def validate_descriptions(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    确保每个元素都有 description 字段；若缺失则自动补充合理默认值（基于 type）。
    针对活动图常见类型做了处理。
    """
    if not result or "elements" not in result:
        return result
    updated = []
    for elem in result["elements"]:
        etype = elem.get("type", "")
        if "description" not in elem or not elem.get("description"):
            if etype == "Package":
                elem["description"] = f"包：{elem.get('name','未命名')}"
            elif etype == "Block":
                elem["description"] = f"块（数据/参与者）：{elem.get('name','未命名')}"
            elif etype == "Activity":
                elem["description"] = f"活动：{elem.get('name','未命名')}（自动提取）"
            elif etype == "ActivityPartition":
                elem["description"] = f"分区（泳道）：{elem.get('name','未命名')}，代表：{elem.get('representsId','')}"
            elif etype in ("InitialNode","ActivityFinalNode","ForkNode","JoinNode","DecisionNode","MergeNode"):
                elem["description"] = f"控制节点：{elem.get('name','未命名')}"
            elif etype in ("CallBehaviorAction","OpaqueAction"):
                elem["description"] = f"动作：{elem.get('name','未命名')}，可能调用行为：{elem.get('behavior','')}"
            elif etype in ("InputPin","OutputPin"):
                elem["description"] = f"引脚：{elem.get('name','未命名')}，类型：{elem.get('typeId','')}"
            elif etype == "CentralBufferNode":
                elem["description"] = f"缓冲节点：{elem.get('name','未命名')}，类型：{elem.get('typeId','')}"
            elif etype in ("ControlFlow","ObjectFlow"):
                guard = elem.get("guard")
                elem["description"] = f"流：{etype} 从 {elem.get('sourceId','')} 到 {elem.get('targetId','')}" + (f", guard={guard}" if guard else "")
            else:
                elem["description"] = elem.get("description") or f"{etype} 元素"
            logger.warning(f"⚠️ 自动补充 description: id={elem.get('id','unknown')} type={etype}")
        updated.append(elem)
    result["elements"] = updated
    return result

# ==================== 主处理函数 ====================

def process_activity_task(state: WorkflowState, task_content: str) -> Dict[str, Any]:
    logger.info("🎯 开始处理活动图任务")
    try:
        llm = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            base_url=settings.base_url,
            temperature=0.0,
            streaming=False,
            max_tokens=getattr(settings, "max_tokens", None)
        )

        # ===== 阶段1：CoT 推理（简短占位） =====
        cot_prompt = PROMPT_COT_SYSTEM + "\n\n输入：\n" + task_content + "\n\n输出：请一步步推理并包含每个元素的 description（包含原文摘录）。"
        cot_result = ""
        for chunk in llm.stream(cot_prompt):
            if(hasattr(chunk, "reasoning_content")):
                print(getattr(chunk, "reasoning_content"), end="", flush=True)
            elif(hasattr(chunk, "reason_content")):
                print(getattr(chunk, "reason_content"), end="", flush=True)
            else:
                chunk_content = chunk.content
                print(chunk_content, end="", flush=True)
                cot_result += chunk_content

        print(f"\n\n{'='*80}")
        print(f"✅ 推理完成")

        # ===== 阶段2：生成JSON =====
        json_prompt = PROMPT_JSON_SYSTEM + "\n\n推理结果：\n" + cot_result + "\n\n请返回严格的JSON。"
        json_str = ""
        for chunk in llm.stream(json_prompt):
            if(hasattr(chunk, "reasoning_content")):
                print(getattr(chunk, "reasoning_content"), end="", flush=True)
            elif(hasattr(chunk, "reason_content")):
                print(getattr(chunk, "reason_content"), end="", flush=True)
            else:
                chunk_content = chunk.content
                print(chunk_content, end="", flush=True)
                json_str += chunk_content

        print(f"\n\n{'='*80}")
        print(f"✅ JSON生成完成")

        # 解析、修复并补全description
        result = validate_and_fix_json(json_str)
        result = validate_descriptions(result)

        # 可选：用Pydantic做一次严格校验（非强制）
        try:
            validated = ActivityDiagramOutput(**result)
            result = validated.dict()
            logger.info("✅ Pydantic 验证通过（活动图）")
        except Exception as e:
            logger.warning(f"⚠️ Pydantic 验证失败（活动图），继续使用修复后的JSON: {e}")

        logger.info("✅ 活动图任务处理完成")
        return {"status": "success", "result": result}

    except Exception as e:
        logger.error(f"❌ 活动图任务处理失败: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

def activity_agent(state: WorkflowState, task_id: str, task_content: str) -> WorkflowState:
    logger.info(f"活动图Agent开始处理任务 {task_id}")

    task_index = -1
    for i, task in enumerate(state.assigned_tasks):
        if task.id == task_id:
            task_index = i
            break

    if task_index == -1:
        logger.error(f"找不到任务 {task_id}")
        return state

    # 标记处理中（与系统状态枚举适配）
    state.assigned_tasks[task_index].status = ProcessStatus.PROCESSING if hasattr(ProcessStatus, "PROCESSING") else ProcessStatus.PROCESSING

    try:
        result = process_activity_task(state, task_content)
        if result.get("status") == "success":
            saved = save_activity_diagram(result["result"], task_id)
            state.assigned_tasks[task_index].result = {**result["result"], "saved_file": saved}
            state.assigned_tasks[task_index].status = ProcessStatus.COMPLETED
            logger.info(f"✅ 任务 {task_id} 处理完成")
        else:
            state.assigned_tasks[task_index].status = ProcessStatus.FAILED
            state.assigned_tasks[task_index].error = result.get("message")
            logger.error(f"❌ 任务 {task_id} 处理失败: {result.get('message')}")
    except Exception as e:
        state.assigned_tasks[task_index].status = ProcessStatus.FAILED
        state.assigned_tasks[task_index].error = str(e)
        logger.error(f"任务 {task_id} 异常: {e}", exc_info=True)

    return state
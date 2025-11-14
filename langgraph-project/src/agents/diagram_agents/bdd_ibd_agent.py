"""
块定义和内部块图Agent - 负责基于输入内容创建SysML BDD和IBD
"""
import logging
import json
import os
import re
from typing import Dict, Any, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from json_repair import repair_json

from graph.workflow_state import WorkflowState, ProcessStatus
from config.settings import settings

logger = logging.getLogger(__name__)

# ==================== 简要 Prompt 占位（下一次你要求时我会补全详细 prompt） ====================
PROMPT_COT_SYSTEM = """
## 角色
你是一位顶级的系统建模专家和数据结构师，精通 SysML BDD (块定义图) 和 IBD (内部块图) 规范。你的任务是从输入的自然语言工程描述中，全面、精确地提取所有结构和行为元素，并组织成一个统一的、扁平化的思考结果列表。

## 核心规则 (!!!必须严格遵守!!!)

1.  **ID 管理的黄金法则**:
    *   **唯一性**: 为你识别的 **每一个** 元素（Package, Block, Property, Port, Connector, Diagram等），都 **必须** 立即生成一个独特的、描述性的 ID (例如: `blk-frame-uuid`, `prop-frame-material-uuid`)。
    *   **一致性**: 在后续的所有步骤中，当你需要引用这个元素时（例如在 `parentId`, `typeId`, `associationId`, `portRefId` 中），你 **必须** 使用你之前生成的 **完全相同** 的 ID 字符串。这是最重要的规则，绝不能出错。

2.  **Description 字段是强制的**:
    *   为每个识别出的元素（包、块、属性、端口、连接器等）都 **必须** 生成一个 `description` 字段。
    *   该字段必须以 `原文：` 开头，引用输入文本中最相关的句子，然后用 `简化：` 提供简明扼要的解释。

3.  **隐含元素推断**:
    *   主动寻找文本中被提及但未在“主要块”列表中明确定义的实体。如果文本描述“电子控制单元连接传感器”，但“电子控制单元”未被定义，你 **必须** 为它创建一个新的 `Block` 元素。

4.  **关联属性完整性**:
    *   当你识别出一个 `part` (部件) 或 `reference` (引用) 属性时，你 **必须** 同时在被引用的 Block 上创建对应的反向引用属性，以确保关联的完整性。
    *   **示例**: 如果 `Fan` 有一个部件 `motor: Motor`，那么在 `Motor` Block 上，你必须创建一个对应的私有引用属性 `_fan: Fan`。然后创建一个 `Association` 元素，其 `memberEndIds` 包含这两个属性的 ID。

5.  **输出格式**:
    *   只输出你的思考过程，严格按照下面的7个步骤进行。不要添加任何额外的解释或对话。
    *   使用 `None` 来表示空值。

## 提取步骤 (你的思考过程)

1.  **识别顶层结构 (Model & Packages):**
    *   确定根 `Model` 和所有 `Package`。
    *   **遵守黄金法则和Description规则**: 为每个元素分配唯一的 ID 和包含原文引用的 `description`。

2.  **识别核心类型定义 (InterfaceBlocks, ValueTypes, Units, Signals, Enumerations, etc.):**
    *   识别所有基础类型定义。
    *   **遵守黄金法则和Description规则**: 为每个元素分配唯一的 ID、`parentId` 和 `description`。

3.  **识别主要功能块 (Concrete Blocks):**
    *   识别核心的功能块，包括那些从文本中 **推断** 出来的块。
    *   **遵守黄金法则和Description规则**: 为每个元素分配唯一的 ID 和 `description`。

4.  **识别内部成员 (Properties, Ports, Operations, etc.):**
    *   遍历每个 Block，识别其所有内部成员。
    *   **属性 (Properties)**: 明确分类 (`value`/`part`/`reference`)。**遵守关联完整性规则**，为 part/reference 属性创建双向链接。
    *   **端口 (Ports)**: 明确分类 (`FullPort`/`ProxyPort`/`FlowPort`)。
    *   **遵守黄金法则和Description规则**: 为每个成员分配唯一的 ID、`parentId` 和 `description`。

5.  **识别关系元素 (Associations, Generalizations):**
    *   基于第 4 步中识别的双向属性，创建 `Association` 元素。
    *   识别 "is-a" 关系并创建 `Generalization` 元素。
    *   **遵守黄金法则和Description规则**: 为每个关系分配唯一的 ID 和 `description`。

6.  **识别 IBD 结构 (Connectors & Diagrams):**
    *   分析 "内部连接" 部分的描述。
    *   为每个 `Connector` 分配 ID，并将其 `parentId` 设置为它所属的 Block 的 ID。
    *   精确记录连接器的 `end1` 和 `end2` 的 `partRefId`, `portRefId`, 或 `propertyRefId`。
    *   **遵守黄金法则和Description规则**: 为每个连接器分配唯一的 ID 和 `description`。

7.  **最终审查 (!!!关键步骤!!!)**:
    *   从头到尾扫描你的整个结果列表。
    *   对于列表中的 **每一个** ID 引用（如 `parentId`, `typeId` 等），验证在你的列表中是否存在一个具有该确切 ID 的元素。
    *   **如果发现任何不匹配或悬空的 ID，立即修正它**。

## 样例输入/输出 (参考)

### 输入文本:
"设计一个`风扇系统包` (`FanSystemPackage`)。该包定义了两个主要块：`风扇` (`Fan`) 和 `遥控器` (`RemoteControl`)。`风扇`块包含一个`电机`部件 (`motor`, 类型 `Motor`)。在 `Fan` 块的内部中，`接收器单元` (`receiver`) 的`指令输出`端口 (`commandOut`) 通过**Assembly Connector**连接到`电机`部件 (`motor`) 的`控制输入`端口 (`controlIn`)。"

### 输出文本:
请你按照如下的7步进行思考推理：

1.  **识别顶层结构:**
    *   Model: id=`model-fan-uuid`, name=`FanSystemModel`, description="原文：设计一个`风扇系统包`。简化：代表整个风扇系统的顶层模型。"
    *   Package: id=`pkg-fan-uuid`, name=`FanSystemPackage`, parentId=`model-fan-uuid`, description="原文：设计一个`风扇系统包` (`FanSystemPackage`)。简化：包含所有与风扇系统相关的块和定义的包。"

2.  **识别核心类型定义:**
    *   Block: id=`blk-motor-uuid`, name=`Motor`, parentId=`pkg-fan-uuid`, isAbstract=False, description="原文：`风扇`块包含一个`电机`部件 (`motor`, 类型 `Motor`)。简化：被风扇引用的电机块定义。"
    *   Block: id=`blk-irrecv-uuid`, name=`IRReceiver`, parentId=`pkg-fan-uuid`, isAbstract=False, description="原文：在 `Fan` 块的内部中，`接收器单元` (`receiver`) ...。简化：被风扇引用的接收器单元块定义（推断）。"

3.  **识别主要功能块:**
    *   Block: id=`blk-fan-uuid`, name=`Fan`, parentId=`pkg-fan-uuid`, isAbstract=False, description="原文：该包定义了两个主要块：`风扇` (`Fan`) ...。简化：系统的核心功能块，代表风扇本身。"
    *   Block: id=`blk-remote-uuid`, name=`RemoteControl`, parentId=`pkg-fan-uuid`, isAbstract=False, description="原文：... 和 `遥控器` (`RemoteControl`)。简化：用于控制风扇的遥控器块。"

4.  **识别内部成员:**
    *   **For Block `Fan` (id: `blk-fan-uuid`):**
        *   Property (Part): `motor`: id=`prop-fan-motor`, parentId=`blk-fan-uuid`, kind=`part`, typeId=`blk-motor-uuid`, assocId=`assoc-fan-motor`, description="原文：`风扇`块包含一个`电机`部件 (`motor`, 类型 `Motor`)。简化：风扇的组成部分，一个电机。"
        *   Property (Part): `receiver`: id=`prop-fan-recv`, parentId=`blk-fan-uuid`, kind=`part`, typeId=`blk-irrecv-uuid`, assocId=`assoc-fan-recv`, description="原文：在 `Fan` 块的内部中，`接收器单元` (`receiver`) ...。简化：风扇的组成部分，一个接收器单元。"
    *   **For Block `Motor` (id: `blk-motor-uuid`):**
        *   Property (Reference): `_fan_motor`: id=`prop-motor-fan`, parentId=`blk-motor-uuid`, kind=`reference`, typeId=`blk-fan-uuid`, assocId=`assoc-fan-motor`, visibility=`private`, description="原文：`风扇`块包含一个`电机`部件。简化：对包含此电机的风扇的反向引用。"
        *   Port: `controlIn`: id=`port-motor-ctrlin`, type=`ProxyPort`, parentId=`blk-motor-uuid`, description="原文：连接到`电机`部件 (`motor`) 的`控制输入`端口 (`controlIn`)。简化：电机接收控制信号的端口。"
    *   **For Block `IRReceiver` (id: `blk-irrecv-uuid`):**
        *   Property (Reference): `_fan_recv`: id=`prop-irrecv-fan`, parentId=`blk-irrecv-uuid`, kind=`reference`, typeId=`blk-fan-uuid`, assocId=`assoc-fan-recv`, visibility=`private`, description="原文：在 `Fan` 块的内部中，`接收器单元` (`receiver`) ...。简化：对包含此接收器的风扇的反向引用。"
        *   Port: `commandOut`: id=`port-irrecv-cmdout`, type=`ProxyPort`, parentId=`blk-irrecv-uuid`, description="原文：`接收器单元` (`receiver`) 的`指令输出`端口 (`commandOut`)。简化：接收器发送指令的端口。"

5.  **识别关系元素:**
    *   Association: id=`assoc-fan-motor`, parentId=`pkg-fan-uuid`, memberEndIds=[`prop-fan-motor`, `prop-motor-fan`], description="原文：`风扇`块包含一个`电机`部件。简化：连接风扇和其电机部件的关联关系。"
    *   Association: id=`assoc-fan-recv`, parentId=`pkg-fan-uuid`, memberEndIds=[`prop-fan-recv`, `prop-irrecv-fan`], description="原文：在 `Fan` 块的内部中，`接收器单元` (`receiver`) ...。简化：连接风扇和其接收器部件的关联关系。"

6.  **识别 IBD 结构 (For `Fan` Block):**
    *   Connector: `conn-fan-recv-motor`: id=`conn-fan-recv-motor`, parentId=`blk-fan-uuid`, kind=`assembly`, description="原文：`接收器单元`的`指令输出`端口通过**Assembly Connector**连接到`电机`部件的`控制输入`端口。简化：连接接收器和电机的内部装配连接器。"
        *   End1: partRefId=`prop-fan-recv`, portRefId=`port-irrecv-cmdout`
        *   End2: partRefId=`prop-fan-motor`, portRefId=`port-motor-ctrlin`

7.  **最终审查:**
    *   所有ID引用均已检查，无悬空ID。

"""
PROMPT_COT_USER = "输入：\n{task_content}\n\n输出：请你一步一步进行推理思考。"

PROMPT_JSON_SYSTEM = """
## 角色
你是一位精确的数据转换工程师。你的任务是接收一份详细、扁平化、且经过验证的 SysML 元素思考列表，并将其 **严格地** 转化为一个统一的、符合规范的 JSON 对象。

## 核心规则 (!!!必须严格遵守!!!)

1.  **精确转换**: 你不能发明、猜测或修改数据。你的唯一工作就是将输入的思考过程 **原样** 转换为 JSON 格式。输入的思考列表被认为是完全正确的。
2.  **只输出 JSON**: 你的最终输出 **必须** 只有一个顶级的 JSON 对象。禁止包含任何注释、解释或任何其他文本。
3.  **结构遵从性**: 严格遵循下方最终目标 JSON 范例的结构。
4.  **Description 字段是强制的**: 确保最终 JSON 中的每个元素都包含从思考过程中复制过来的 `description` 字段。
5.  **空值省略**: 如果一个对象中的某个键值是 `None` 或空数组 `[]`，在最终的 JSON 中**省略这个键**，以保持输出的整洁。

## ！关键转换指令！

*   **元素聚合**: 将所有思考条目转换为 `elements` 数组中的 JSON 对象。
*   **ID 映射**: 输入列表中的每一个 `id` 都是神圣不可改动的。在 JSON 中精确地使用它们。
*   **连接器 (Connector) 组装**: `Connector` 元素应包含 `end1` 和 `end2` 对象。`end1` 和 `end2` 的内容直接从思考过程中的连接器描述中获取。**端点对象本身没有 `id`**。
*   **操作参数 (Parameter) 嵌套**: 思考过程中的独立 `Parameter` 元素，在最终 JSON 中 **必须** 被嵌套在它们所属的 `Operation` 元素的 `parameters` 数组中。
*   **枚举文字 (EnumerationLiteral)**: 思考过程中的独立 `EnumerationLiteral` 元素，在最终 JSON 中需要作为顶级元素存在于 `elements` 数组中，并且其 `id` 被包含在所属 `Enumeration` 元素的 `literals` 数组中。
*   **基本类型**: 对于 `propertyKind: 'value'` 的属性，如果其类型是基本数据类型，则 `typeId` 应为字符串，如 `"String"`, `"Real"`, `"Integer"`, `"Boolean"`。

## 最终目标 JSON 格式样例
```json
{
  "model": [
  {
    "id": "model-fan-uuid",
    "name": "FanSystemModel",
    "description": "原文：设计一个`风扇系统包`。简化：代表整个风扇系统的顶层模型。"
  }
  ],
  "elements": [
    {
      "id": "pkg-fan-uuid",
      "type": "Package",
      "name": "FanSystemPackage",
      "parentId": "model-fan-uuid",
      "description": "原文：设计一个`风扇系统包` (`FanSystemPackage`)。简化：包含所有与风扇系统相关的块和定义的包。"
    },
    {
      "id": "blk-motor-uuid",
      "type": "Block",
      "name": "Motor",
      "parentId": "pkg-fan-uuid",
      "description": "原文：`风扇`块包含一个`电机`部件 (`motor`, 类型 `Motor`)。简化：被风扇引用的电机块定义。"
    },
    {
      "id": "blk-fan-uuid",
      "type": "Block",
      "name": "Fan",
      "parentId": "pkg-fan-uuid",
      "description": "原文：该包定义了两个主要块：`风扇` (`Fan`) ...。简化：系统的核心功能块，代表风扇本身。"
    },
    {
      "id": "prop-fan-motor",
      "type": "Property",
      "name": "motor",
      "parentId": "blk-fan-uuid",
      "propertyKind": "part",
      "typeId": "blk-motor-uuid",
      "associationId": "assoc-fan-motor",
      "description": "原文：`风扇`块包含一个`电机`部件 (`motor`, 类型 `Motor`)。简化：风扇的组成部分，一个电机。"
    },
    {
      "id": "assoc-fan-motor",
      "type": "Association",
      "parentId": "pkg-fan-uuid",
      "memberEndIds": ["prop-fan-motor", "prop-motor-fan"],
      "description": "原文：`风扇`块包含一个`电机`部件。简化：连接风扇和其电机部件的关联关系。"
    },
    {
      "id": "conn-fan-recv-motor",
      "type": "AssemblyConnector",
      "parentId": "blk-fan-uuid",
      "end1": {
        "partRefId": "prop-fan-recv",
        "portRefId": "port-irrecv-cmdout"
      },
      "end2": {
        "partRefId": "prop-fan-motor",
        "portRefId": "port-motor-ctrlin"
      },
      "description": "原文：`接收器单元`的`指令输出`端口通过**Assembly Connector**连接到`电机`部件的`控制输入`端口。简化：连接接收器和电机的内部装配连接器。"
    }
  ]
}
```
"""
PROMPT_JSON_USER = "推理结果：\n{cot_result}\n\n请严格按照JSON格式输出。- description 字段必须要包含“原文：”和“简化：”两部分内容。"

# ==================== Pydantic 模型定义 ====================

class BddIbdModel(BaseModel):
    id: str = Field(description="模型唯一ID")
    name: str = Field(description="模型名称")
    description: Optional[str] = Field(None, description="模型的详细描述")

class BddIbdDiagramOutput(BaseModel):
    model: List[BddIbdModel] = Field(description="模型对象")
    elements: List[Dict[str, Any]] = Field(description="所有图表元素的列表")

# ==================== 辅助函数 ====================

def get_bdd_ibd_output_dir() -> str:
    """获取或创建BDD/IBD图的输出目录"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    output_dir = os.path.join(project_root, "data", "output", "bdd_ibd_diagrams")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"创建BDD/IBD图输出目录: {output_dir}")
    return output_dir

def save_bdd_ibd_diagram(result: Dict[str, Any], task_id: str) -> str:
    """将生成的图表JSON保存到文件"""
    try:
        output_dir = get_bdd_ibd_output_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"bdd_ibd_diagram_{task_id}_{timestamp}.json"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ BDD/IBD图已保存到: {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"保存BDD/IBD图失败: {e}", exc_info=True)
        return ""

def validate_and_fix_json(json_str: str) -> Dict[str, Any]:
    """清理、解析并修复JSON字符串"""
    try:
        if "```json" in json_str:
            json_str = json_str.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```", 1)[1].split("```", 1)[0].strip()
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
    """确保每个元素都有description字段，若缺失则自动补充"""
    if not result or "elements" not in result:
        return result
    
    # 处理 elements 数组
    for elem in result.get("elements", []):
        if "description" not in elem or not elem.get("description"):
            elem_type = elem.get("type", "Element")
            elem_name = elem.get("name", "未命名")
            elem["description"] = f"自动生成的描述: 这是一个 {elem_type} 类型的元素，名为 '{elem_name}'。"
            logger.warning(f"⚠️ 自动补充 description: id={elem.get('id','unknown')} type={elem_type}")
    
    # 处理 model 字段 - 修复：model 是列表，需要遍历
    if "model" in result and isinstance(result["model"], list):
        for model_item in result["model"]:
            if isinstance(model_item, dict) and ("description" not in model_item or not model_item.get("description")):
                model_item["description"] = f"自动生成的模型描述: {model_item.get('name', '未命名模型')}。"
                logger.warning(f"⚠️ 自动补充 model description: id={model_item.get('id','unknown')}")
    
    return result

# ==================== 主处理函数 ====================

def process_bdd_ibd_task(state: WorkflowState, task_content: str) -> Dict[str, Any]:
    """处理单个BDD/IBD图任务，采用两阶段流式输出"""
    logger.info("🎯 开始处理BDD/IBD图任务")
    try:
        llm = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            base_url=settings.base_url,
            temperature=0.0,
            streaming=True,
            max_tokens=getattr(settings, "max_tokens", 4096)
        )

        # ========== 阶段1：CoT推理 ==========
        print(f"\n{'='*80}")
        print(f"🧠 阶段1: BDD/IBD分析与推理")
        print(f"{'='*80}\n")
        
        cot_prompt = PROMPT_COT_SYSTEM + PROMPT_COT_USER.format(task_content=task_content)
        cot_result = ""
        for chunk in llm.stream(cot_prompt):
            chunk_content = chunk.content
            print(chunk_content, end="", flush=True)
            cot_result += chunk_content
        
        print(f"\n\n{'='*80}")
        print(f"✅ 推理完成")
        print(f"{'='*80}\n")

        # ========== 阶段2：生成JSON ==========
        print(f"\n{'='*80}")
        print(f"📝 阶段2: 生成结构化JSON")
        print(f"{'='*80}\n")

        json_prompt = PROMPT_JSON_SYSTEM + PROMPT_JSON_USER.format(cot_result=cot_result)
        json_str = ""
        for chunk in llm.stream(json_prompt):
            chunk_content = chunk.content
            print(chunk_content, end="", flush=True)
            json_str += chunk_content

        print(f"\n\n{'='*80}")
        print(f"✅ JSON生成完成")
        print(f"{'='*80}\n")

        # 解析、修复并补全description
        result = validate_and_fix_json(json_str)
        result = validate_descriptions(result)

        # 可选：用Pydantic做一次严格校验
        try:
            validated = BddIbdDiagramOutput(**result)
            result = validated.dict()
            logger.info("✅ Pydantic 验证通过 (BDD/IBD)")
        except Exception as e:
            logger.warning(f"⚠️ Pydantic 验证失败 (BDD/IBD)，继续使用修复后的JSON: {e}")

        logger.info("✅ BDD/IBD图任务处理完成")
        return {"status": "success", "result": result}

    except Exception as e:
        logger.error(f"❌ BDD/IBD图任务处理失败: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

# ==================== Agent入口函数 ====================

def bdd_ibd_agent(state: WorkflowState, task_id: str, task_content: str) -> WorkflowState:
    """BDD/IBD图Agent的入口函数"""
    logger.info(f"BDD/IBD Agent开始处理任务 {task_id}")

    task_index = -1
    for i, task in enumerate(state.assigned_tasks):
        if task.id == task_id:
            task_index = i
            break

    if task_index == -1:
        logger.error(f"找不到任务 {task_id}")
        return state

    state.assigned_tasks[task_index].status = ProcessStatus.PROCESSING

    try:
        result_data = process_bdd_ibd_task(state, task_content)
        if result_data.get("status") == "success":
            saved_path = save_bdd_ibd_diagram(result_data["result"], task_id)
            state.assigned_tasks[task_index].result = {**result_data["result"], "saved_file": saved_path}
            state.assigned_tasks[task_index].status = ProcessStatus.COMPLETED
            logger.info(f"✅ 任务 {task_id} 处理完成")
        else:
            state.assigned_tasks[task_index].status = ProcessStatus.FAILED
            state.assigned_tasks[task_index].error = result_data.get("message")
            logger.error(f"❌ 任务 {task_id} 处理失败: {result_data.get('message')}")
    except Exception as e:
        state.assigned_tasks[task_index].status = ProcessStatus.FAILED
        state.assigned_tasks[task_index].error = str(e)
        logger.error(f"任务 {task_id} 异常: {e}", exc_info=True)

    return state
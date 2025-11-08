import logging
import json
import os
import re
from typing import Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from json_repair import repair_json

from graph.workflow_state import WorkflowState, ProcessStatus
from config.settings import settings

logger = logging.getLogger(__name__)

# ==================== 简要 Prompt 占位 ====================
# 注意：这里的Prompt是简化的占位符，实际使用的详细Prompt已根据您的要求设计，
# 包含了CoT推理、连接器映射表和description字段的详细规则。
PROMPT_COT_SYSTEM = """
## 角色
你是一位专业的 SysML 参数图建模专家。你精通 SysML 参数图规范，能够准确地从工程问题描述中提取参数块、约束属性及其数学关系，并对元素间的引用关系进行严格校验。

## 核心要求
**为每个识别出的元素（包、块、属性、约束块、约束参数、连接器等）都必须生成一个 `description` 字段。该字段必须以 `原文：` 开头，引用输入文本中最相关的句子或片段，然后用 `简化：` 提供简明扼要的解释。**

## 规则
你的目标是根据输入的文本描述，分析并生成构建 SysML 参数图所需的元素信息。请遵循以下三阶段步骤进行思考和分析：

### 第一阶段：元素识别

**步骤 1：识别主要的块 (Blocks)**
- 确定文本描述中的主要实体、系统或子系统。
- 这些通常是主句的主语或核心概念。
- 为每个块分配唯一的名称和 ID（例如 "block1", "block2"）。
- 为每个块生成 `description`，必须包含 `原文：` 和 `简化：` 两部分。

**步骤 2：提取值属性 (Value Properties)**
- 识别与每个块相关的所有变量、参数或属性。
- 列出所有变量，即使它们未参与约束关系。
- 假设类型为 "Real"，除非文本明确指定其他类型（如 "Integer", "Boolean"）。
- 为每个属性分配唯一 ID（例如 "prop1", "prop2"）。
- 为每个属性生成 `description`，格式为 `原文：[摘录]。简化：[说明]。`

**步骤 3：识别约束语句 (Constraint Statements)**
- 查找描述属性之间关系的语句。
- 这些通常是数学等式、不等式或逻辑表达式。
- 记录每个约束语句的完整形式和自然语言描述。
- 为每个约束语句生成 `description`，包含原文引用。

**步骤 4：定义约束块 (Constraint Blocks)**
- 为每个约束语句创建一个 `ConstraintBlock`。
- 基于约束类型或描述性名称命名约束块（例如 "VelocityEquation", "PowerBalance"）。
- 在每个约束块内定义约束参数 (Constraint Parameters)：
  - 为约束涉及的每个变量创建一个参数。
  - 参数名称应与对应的值属性名称一致。
  - 为每个参数分配唯一 ID（例如 "param1", "param2"）。
  - 为每个参数生成 `description`，说明其在约束中的作用。
- 在约束块中定义 `specification` 字段，包含：
  - `expression`: 约束的数学表达式（字符串形式）。
  - `language`: 通常为 "English" 或 "Math"。
- 为约束块本身生成 `description`，包含原文引用和约束的含义。

**步骤 5：实例化约束属性 (Constraint Properties)**
- 在相关的块（Block）中，为每个要使用的 `ConstraintBlock` 创建一个对应的 `Property`。
- 这个 `Property` 的 `propertyKind` **必须**为 `"constraint"`。
- 这个 `Property` 的 `typeId` **必须**指向相应 `ConstraintBlock` 的 ID。
- 为每个约束属性分配唯一 ID（例如 "constraint1_prop", "cp1"）。
- 为每个约束属性生成 `description`，说明它在块中实例化了哪个约束。

### 第二阶段：强制关系校验 (最关键步骤)

**步骤 6：创建连接器映射表**
- 在完成上述所有元素的识别后，你**必须**创建一个 Markdown 表格，名为"连接器映射表"。
- 此表的目的是在生成最终 JSON 之前，预先规划和验证每一个 `BindingConnector` 的合法性。
- 表格**必须**包含以下列：
  - `连接器ID`: 为即将创建的连接器预分配一个 ID (例如 "conn1", "conn2")。
  - `连接器父级ID`: 连接器所属的块的 ID（通常是包含值属性的块）。
  - `源属性 (end1.propertyRefId)`: 填写值属性 (Value Property) 的 ID 和名称 (例如 "prop1 (v)")。
  - `目标约束实例 (end2.partRefId)`: **必须**填写步骤 5 中创建的【约束属性】(Constraint Property, `propertyKind: "constraint"`) 的 ID (例如 "constraint1_prop")。
  - `目标参数 (end2.portRefId)`: **必须**填写步骤 4 中创建的【约束参数】(Constraint Parameter) 的 ID (例如 "param1")。
  - `逻辑校验`: 简要说明连接的合理性 (例如 "将块的 'v' 连接到方程的 'v' 参数")。
  - `description`: 为该连接器生成描述，格式为 `原文：[如果原文提到此连接]。简化：[连接的作用]。`

- **核心规则**：
  - 表中的每一行都定义了一个完整的、合法的连接器。
  - `end2.partRefId` 引用的**必须**是一个 `propertyKind` 为 `"constraint"` 的 `Property` 的 ID。
  - `end2.portRefId` 引用的**必须**是一个 `ConstraintParameter` 的 ID。
  - 该 `ConstraintParameter` **必须**是被引用的 `Constraint Property` 的类型（即其 `typeId` 指向的 `ConstraintBlock`）内部定义的。
  - 如果一个约束块没有参数，或者一个值属性没有地方可以连接，则不应出现在此表中。
  - 只有通过此表验证的连接，才能在最终的 JSON 中生成。

### 第三阶段：整理优化输出

**步骤 7：编译最终思考摘要**
- 汇总上述所有步骤的分析结果。
- 使用清晰的 Markdown 格式列出：
  - 所有识别的块及其属性（包含 description）。
  - 所有约束块及其参数（包含 description）。
  - 所有约束属性实例（包含 description）。
  - 完整的连接器映射表。
- 确保所有 ID 引用的一致性和准确性。

## 输出样例

### 输入样例：
"电动汽车动力系统中包含：
1. 电池模块：输出电压 V_batt 与电流 I 满足 V_batt = EMF - R_int*I，其中 EMF 为电动势，R_int 为内阻
2. 电机模块：输出扭矩 T 与转速 ω 满足 T = K_t*I - B*ω，其中 K_t 为转矩常数，B 为阻尼系数
两个模块通过功率平衡关联：V_batt*I = T*ω + Losses"

### 思考过程：

#### 第一阶段：元素识别

**步骤 1：识别主要的块**
- Block 1: 
  - ID: `block1`
  - Name: `BatteryModule`
  - Description: `原文：电池模块：输出电压 V_batt 与电流 I 满足 V_batt = EMF - R_int*I，其中 EMF 为电动势，R_int 为内阻。简化：代表电动汽车中的电池系统，包含电压、电流和内部参数。`
- Block 2:
  - ID: `block2`
  - Name: `MotorModule`
  - Description: `原文：电机模块：输出扭矩 T 与转速 ω 满足 T = K_t*I - B*ω，其中 K_t 为转矩常数，B 为阻尼系数。简化：代表电动汽车中的电机系统，包含扭矩、转速和机械参数。`
- Block 3 (系统级):
  - ID: `block_system`
  - Name: `EV_PowerSystem`
  - Description: `原文：电动汽车动力系统...两个模块通过功率平衡关联。简化：顶层系统块，封装电池和电机模块，并定义它们之间的功率平衡约束。`

**步骤 2：提取值属性**

**block1 (BatteryModule) 的属性：**
- Property 1:
  - ID: `prop1`
  - Name: `V_batt`
  - Type: `Real`
  - Description: `原文：输出电压 V_batt。简化：电池模块的输出电压。`
- Property 2:
  - ID: `prop2`
  - Name: `EMF`
  - Type: `Real`
  - Description: `原文：EMF 为电动势。简化：电池的电动势（开路电压）。`
- Property 3:
  - ID: `prop3`
  - Name: `R_int`
  - Type: `Real`
  - Description: `原文：R_int 为内阻。简化：电池的内部电阻。`
- Property 4:
  - ID: `prop4`
  - Name: `I`
  - Type: `Real`
  - Description: `原文：电流 I。简化：流经电池的电流。`

**block2 (MotorModule) 的属性：**
- Property 5:
  - ID: `prop5`
  - Name: `T`
  - Type: `Real`
  - Description: `原文：输出扭矩 T。简化：电机的输出扭矩。`
- Property 6:
  - ID: `prop6`
  - Name: `ω`
  - Type: `Real`
  - Description: `原文：转速 ω。简化：电机的旋转角速度。`
- Property 7:
  - ID: `prop7`
  - Name: `K_t`
  - Type: `Real`
  - Description: `原文：K_t 为转矩常数。简化：电机的转矩常数，表示电流到扭矩的转换系数。`
- Property 8:
  - ID: `prop8`
  - Name: `B`
  - Type: `Real`
  - Description: `原文：B 为阻尼系数。简化：电机的机械阻尼系数。`

**block_system (EV_PowerSystem) 的属性：**
- Property 9:
  - ID: `prop9`
  - Name: `Losses`
  - Type: `Real`
  - Description: `原文：Losses。简化：系统中的功率损耗。`

**步骤 3：识别约束语句**
- 约束 1: `V_batt = EMF - R_int*I` (电池电压方程)
- 约束 2: `T = K_t*I - B*ω` (电机扭矩方程)
- 约束 3: `V_batt*I = T*ω + Losses` (功率平衡方程)

**步骤 4：定义约束块及其参数**

**ConstraintBlock 1: BatteryVoltageEquation**
- ID: `cb1`
- Name: `BatteryVoltageEquation`
- Specification: `{"expression": "V_batt = EMF - R_int*I", "language": "Math"}`
- Description: `原文：V_batt = EMF - R_int*I，其中 EMF 为电动势，R_int 为内阻。简化：定义电池输出电压与电动势、内阻和电流之间的关系。`
- 参数：
  - Param 1: `id: "param1", name: "V_batt", typeId: "Real"`, Description: `原文：输出电压 V_batt。简化：方程中的输出电压变量。`
  - Param 2: `id: "param2", name: "EMF", typeId: "Real"`, Description: `原文：EMF 为电动势。简化：方程中的电动势变量。`
  - Param 3: `id: "param3", name: "R_int", typeId: "Real"`, Description: `原文：R_int 为内阻。简化：方程中的内阻变量。`
  - Param 4: `id: "param4", name: "I", typeId: "Real"`, Description: `原文：电流 I。简化：方程中的电流变量。`

**ConstraintBlock 2: MotorTorqueEquation**
- ID: `cb2`
- Name: `MotorTorqueEquation`
- Specification: `{"expression": "T = K_t*I - B*ω", "language": "Math"}`
- Description: `原文：T = K_t*I - B*ω，其中 K_t 为转矩常数，B 为阻尼系数。简化：定义电机输出扭矩与电流、转速和机械参数之间的关系。`
- 参数：
  - Param 5: `id: "param5", name: "T", typeId: "Real"`, Description: `原文：输出扭矩 T。简化：方程中的扭矩变量。`
  - Param 6: `id: "param6", name: "K_t", typeId: "Real"`, Description: `原文：K_t 为转矩常数。简化：方程中的转矩常数。`
  - Param 7: `id: "param7", name: "I", typeId: "Real"`, Description: `原文：电流 I。简化：方程中的电流变量。`
  - Param 8: `id: "param8", name: "B", typeId: "Real"`, Description: `原文：B 为阻尼系数。简化：方程中的阻尼系数。`
  - Param 9: `id: "param9", name: "ω", typeId: "Real"`, Description: `原文：转速 ω。简化：方程中的角速度变量。`

**ConstraintBlock 3: PowerBalanceEquation**
- ID: `cb3`
- Name: `PowerBalanceEquation`
- Specification: `{"expression": "V_batt*I = T*ω + Losses", "language": "Math"}`
- Description: `原文：V_batt*I = T*ω + Losses。简化：定义电池输出功率与电机机械功率和系统损耗之间的平衡关系。`
- 参数：
  - Param 10: `id: "param10", name: "V_batt", typeId: "Real"`, Description: `原文：输出电压 V_batt。简化：功率平衡方程中的电池电压。`
  - Param 11: `id: "param11", name: "I", typeId: "Real"`, Description: `原文：电流 I。简化：功率平衡方程中的电流。`
  - Param 12: `id: "param12", name: "T", typeId: "Real"`, Description: `原文：输出扭矩 T。简化：功率平衡方程中的电机扭矩。`
  - Param 13: `id: "param13", name: "ω", typeId: "Real"`, Description: `原文：转速 ω。简化：功率平衡方程中的电机角速度。`
  - Param 14: `id: "param14", name: "Losses", typeId: "Real"`, Description: `原文：Losses。简化：功率平衡方程中的损耗变量。`

**步骤 5：实例化约束属性**
- 在 `block1` 中实例化：
  - Constraint Property 1: `id: "cp1", name: "BatteryVoltageEquation", propertyKind: "constraint", typeId: "cb1"`, Description: `原文：电池模块...满足 V_batt = EMF - R_int*I。简化：在电池模块中实例化电池电压约束。`
- 在 `block2` 中实例化：
  - Constraint Property 2: `id: "cp2", name: "MotorTorqueEquation", propertyKind: "constraint", typeId: "cb2"`, Description: `原文：电机模块...满足 T = K_t*I - B*ω。简化：在电机模块中实例化电机扭矩约束。`
- 在 `block_system` 中实例化：
  - Constraint Property 3: `id: "cp3", name: "PowerBalanceEquation", propertyKind: "constraint", typeId: "cb3"`, Description: `原文：两个模块通过功率平衡关联。简化：在系统块中实例化功率平衡约束。`

#### 第二阶段：强制关系校验

**步骤 6：连接器映射表**

| 连接器ID | 连接器父级ID | 源属性 (end1.propertyRefId) | 目标约束实例 (end2.partRefId) | 目标参数 (end2.portRefId) | 逻辑校验 | description |
|:---------|:-------------|:----------------------------|:------------------------------|:--------------------------|:---------|:------------|
| conn1 | block1 | prop1 (V_batt) | cp1 | param1 (V_batt) | 连接电池模块的 V_batt 到电池方程的 V_batt 参数 | 原文：输出电压 V_batt。简化：将电池的输出电压值绑定到约束方程中的电压参数。 |
| conn2 | block1 | prop2 (EMF) | cp1 | param2 (EMF) | 连接电池模块的 EMF 到电池方程的 EMF 参数 | 原文：EMF 为电动势。简化：将电池的电动势值绑定到约束方程中的 EMF 参数。 |
| conn3 | block1 | prop3 (R_int) | cp1 | param3 (R_int) | 连接电池模块的 R_int 到电池方程的 R_int 参数 | 原文：R_int 为内阻。简化：将电池的内阻值绑定到约束方程中的内阻参数。 |
| conn4 | block1 | prop4 (I) | cp1 | param4 (I) | 连接电池模块的 I 到电池方程的 I 参数 | 原文：电流 I。简化：将电池的电流值绑定到约束方程中的电流参数。 |
| conn5 | block2 | prop5 (T) | cp2 | param5 (T) | 连接电机模块的 T 到电机方程的 T 参数 | 原文：输出扭矩 T。简化：将电机的扭矩值绑定到约束方程中的扭矩参数。 |
| conn6 | block2 | prop7 (K_t) | cp2 | param6 (K_t) | 连接电机模块的 K_t 到电机方程的 K_t 参数 | 原文：K_t 为转矩常数。简化：将电机的转矩常数值绑定到约束方程中的 K_t 参数。 |
| conn7 | block2 | prop4 (I)* | cp2 | param7 (I) | 连接共享的电流 I 到电机方程的 I 参数 | 原文：电流 I（跨模块共享）。简化：将系统中的共享电流值绑定到电机约束方程的电流参数。 |
| conn8 | block2 | prop8 (B) | cp2 | param8 (B) | 连接电机模块的 B 到电机方程的 B 参数 | 原文：B 为阻尼系数。简化：将电机的阻尼系数值绑定到约束方程中的 B 参数。 |
| conn9 | block2 | prop6 (ω) | cp2 | param9 (ω) | 连接电机模块的 ω 到电机方程的 ω 参数 | 原文：转速 ω。简化：将电机的转速值绑定到约束方程中的角速度参数。 |
| conn10 | block_system | prop1* (V_batt) | cp3 | param10 (V_batt) | 连接电池的 V_batt 到功率平衡方程 | 原文：V_batt（电池输出）。简化：将电池电压引入系统级功率平衡约束。 |
| conn11 | block_system | prop4* (I) | cp3 | param11 (I) | 连接共享的 I 到功率平衡方程 | 原文：电流 I（系统级共享）。简化：将系统电流引入功率平衡约束。 |
| conn12 | block_system | prop5* (T) | cp3 | param12 (T) | 连接电机的 T 到功率平衡方程 | 原文：输出扭矩 T（电机输出）。简化：将电机扭矩引入系统级功率平衡约束。 |
| conn13 | block_system | prop6* (ω) | cp3 | param13 (ω) | 连接电机的 ω 到功率平衡方程 | 原文：转速 ω（电机输出）。简化：将电机转速引入系统级功率平衡约束。 |
| conn14 | block_system | prop9 (Losses) | cp3 | param14 (Losses) | 连接系统 Losses 到功率平衡方程 | 原文：Losses（系统损耗）。简化：将系统功率损耗引入功率平衡约束。 |

*注：带星号的属性表示跨块引用，在实际 JSON 中需通过正确的 `propertyRefId` 路径或嵌套引用实现。*

#### 第三阶段：整理优化输出

---
**模型：** EV_PowerSystemModel (model-ev-unique-id)

**包：** ParametricDiagram (pkg-ev-unique-id)
- Description: `原文：电动汽车动力系统。简化：封装电动汽车动力系统参数图的所有元素。`

**块 (Blocks)：**
1. BatteryModule (block1)
   - 属性：V_batt, EMF, R_int, I
   - 约束属性：BatteryVoltageEquation (cp1)
2. MotorModule (block2)
   - 属性：T, ω, K_t, B
   - 约束属性：MotorTorqueEquation (cp2)
3. EV_PowerSystem (block_system)
   - 属性：Losses
   - 约束属性：PowerBalanceEquation (cp3)

**约束块 (Constraint Blocks)：**
1. BatteryVoltageEquation (cb1)
   - 参数：V_batt (param1), EMF (param2), R_int (param3), I (param4)
2. MotorTorqueEquation (cb2)
   - 参数：T (param5), K_t (param6), I (param7), B (param8), ω (param9)
3. PowerBalanceEquation (cb3)
   - 参数：V_batt (param10), I (param11), T (param12), ω (param13), Losses (param14)

**连接器 (Binding Connectors)：**
（见上方映射表，共 14 个连接器）
---
"""
PROMPT_JSON_SYSTEM = """
根据以上详细的推理和整理优化输出，请严格按照以下 JSON 格式生成 SysML 参数图的完整描述。

## 核心校验规则
在生成 JSON 之前，请再次确认每一个 `BindingConnector` 都严格满足以下校验规则：
1. `end2.partRefId` 引用的**必须**是一个【约束属性】（`type: "Property"`, `propertyKind: "constraint"`）的 ID。
2. `end2.portRefId` 引用的**必须**是一个【约束参数】（`type: "ConstraintParameter"`）的 ID。
3. 该【约束参数】**必须**是在被引用的【约束属性】的类型（即 `ConstraintBlock`，通过 `typeId` 引用）内部定义的。
4. 如果一个约束块没有参数，则绝不为它创建连接器。

## JSON 格式要求
1. 所有 `id` 字段都是全局唯一的字符串。
2. **每个元素都必须包含一个 `description` 字段**，其内容应与推理步骤中生成的描述保持一致。
3. `parentId` 正确反映元素的包含关系。
4. `typeId` (用于 Property 和 ConstraintParameter) 正确引用相应的类型 ID。
5. `representsId` (如适用) 正确引用代表的元素 ID。
6. `propertyRefId`, `partRefId`, `portRefId` (用于 BindingConnector) 正确引用源和目标元素的 ID。
7. `specification` (用于 ConstraintBlock) 包含 `expression` 和 `language` 字段。
8. JSON 根对象只包含 `model` 和 `elements` 两个键。

## 示例 JSON 结构
```json
{
  "model": [
    {
      "id": "model-unique-id",
      "name": "ModelName",
      "description": "原文：...。简化：模型的总体描述，说明其目的和范围。"
    }
  ],
  "elements": [
    {
      "id": "pkg-unique-id",
      "type": "Package",
      "name": "PackageName",
      "parentId": "model-unique-id",
      "description": "原文：...。简化：包的描述，说明其包含的内容和职责。"
    },
    {
      "id": "block1",
      "type": "Block",
      "name": "SystemBlock",
      "parentId": "pkg-unique-id",
      "description": "原文：...。简化：系统块的描述。"
    },
    {
      "id": "prop1",
      "type": "Property",
      "name": "PropertyName",
      "propertyKind": "value",
      "parentId": "block1",
      "typeId": "Real",
      "description": "原文：...。简化：值属性的描述。"
    },
    {
      "id": "cb1",
      "type": "ConstraintBlock",
      "name": "ConstraintName",
      "parentId": "pkg-unique-id",
      "specification": {
        "expression": "y = f(x)",
        "language": "Math"
      },
      "description": "原文：...。简化：约束块的描述，说明其定义的约束关系。"
    },
    {
      "id": "param1",
      "type": "ConstraintParameter",
      "name": "ParamName",
      "parentId": "cb1",
      "typeId": "Real",
      "description": "原文：...。简化：约束参数的描述，说明其在约束中的角色。"
    },
    {
      "id": "cp1",
      "type": "Property",
      "name": "ConstraintPropertyName",
      "propertyKind": "constraint",
      "parentId": "block1",
      "typeId": "cb1",
      "description": "原文：...。简化：约束属性的描述，说明其实例化的约束。"
    },
    {
      "id": "conn1",
      "type": "BindingConnector",
      "parentId": "block1",
      "end1": {
        "propertyRefId": "prop1"
      },
      "end2": {
        "partRefId": "cp1",
        "portRefId": "param1"
      },
      "description": "原文：...。简化：绑定连接器的描述，说明其连接的属性和约束参数。"
    }
  ]
}
```

## 输出要求
- 请严格按照上述 JSON 结构输出完整的参数图模型。
- 确保所有 ID 引用的正确性。
- 确保每个元素都包含 `description` 字段。
- 不要在 JSON 之外添加任何解释性文本（可以用 markdown 代码块包裹 JSON）。
"""

# ==================== Pydantic 模型定义 ====================
class DiagramModel(BaseModel):
    id: str = Field(description="模型唯一ID")
    name: str = Field(description="模型名称")
    description: str = Field(description="模型描述")

class ParametricDiagramOutput(BaseModel):
    model: List[DiagramModel] = Field(description="模型列表")
    elements: List[Dict[str, Any]] = Field(description="元素列表（参数图元素）")

# ==================== 辅助函数 ====================

def get_parametric_output_dir() -> str:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    output_dir = os.path.join(project_root, "data", "output", "parametric_diagrams")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"创建参数图输出目录: {output_dir}")
    return output_dir

def save_parametric_diagram(result: Dict[str, Any], task_id: str) -> str:
    try:
        output_dir = get_parametric_output_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"parametric_diagram_{task_id}_{timestamp}.json"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ 参数图已保存到: {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"保存参数图失败: {e}", exc_info=True)
        return ""

def validate_and_fix_json(json_str: str) -> Dict[str, Any]:
    """清理代码块，尝试解析，失败则用 repair_json 修复"""
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
    """确保每个元素都有 description 字段；若缺失则自动补充。"""
    if not result or "elements" not in result:
        return result
    for elem in result.get("elements", []):
        if "description" not in elem or not elem.get("description"):
            elem_type = elem.get("type", "Element")
            elem_name = elem.get("name", "Unnamed")
            elem["description"] = f"自动生成的描述: 这是一个类型为 '{elem_type}'，名称为 '{elem_name}' 的元素。"
            logger.warning(f"⚠️ 自动补充 description: id={elem.get('id','unknown')} type={elem_type}")
    return result

# ==================== 主处理函数 ====================

def process_parameter_task(state: WorkflowState, task_content: str) -> Dict[str, Any]:
    logger.info("🎯 开始处理参数图任务")
    try:
        llm = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            base_url=settings.base_url,
            temperature=0.0,
            streaming=True,
            max_tokens=getattr(settings, "max_tokens", 4096)
        )
        llm_non_streaming = llm.with_config({"streaming": False})

        # ===== 阶段1：CoT 推理 =====
        print(f"\n{'='*80}")
        print(f"🧠 阶段1: 参数图分析与推理")
        print(f"{'='*80}\n")
        
        cot_prompt = ChatPromptTemplate.from_messages([
            ("system", PROMPT_COT_SYSTEM),
            ("human", "输入：\n{task_content}\n\n输出：请你一步一步进行推理思考。")
        ])
        cot_chain = cot_prompt | llm

        cot_result = ""
        for chunk in cot_chain.stream({"task_content": task_content}):
            chunk_content = getattr(chunk, "content", "")
            print(chunk_content, end="", flush=True)
            cot_result += chunk_content
        
        print(f"\n\n{'='*80}")
        print(f"✅ 推理完成")
        print(f"{'='*80}\n")

        # ===== 阶段2：生成JSON =====
        print(f"\n{'='*80}")
        print(f"📝 阶段2: 生成结构化JSON (参数图)")
        print(f"{'='*80}\n")

        json_prompt = ChatPromptTemplate.from_messages([
            ("system", PROMPT_JSON_SYSTEM),
            ("human", "推理结果：\n{cot_result}\n\n请严格按照规则生成JSON。")
        ])
        json_chain = json_prompt | llm

        json_str = ""
        for chunk in json_chain.stream({"cot_result": cot_result}):
            chunk_content = getattr(chunk, "content", "")
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
            validated = ParametricDiagramOutput(**result)
            result = validated.dict()
            logger.info("✅ Pydantic 验证通过 (参数图)")
        except Exception as e:
            logger.warning(f"⚠️ Pydantic 验证失败 (参数图)，继续使用修复后的JSON: {e}")

        logger.info("✅ 参数图任务处理完成")
        return {"status": "success", "result": result}

    except Exception as e:
        logger.error(f"❌ 参数图任务处理失败: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

def parameter_agent(state: WorkflowState, task_id: str, task_content: str) -> WorkflowState:
    logger.info(f"参数图Agent开始处理任务 {task_id}")

    task_index = -1
    for i, task in enumerate(state.assigned_tasks):
        if task.id == task_id:
            task_index = i
            break

    if task_index == -1:
        logger.error(f"找不到任务 {task_id}")
        return state

    state.assigned_tasks[task_index].status = ProcessStatus.IN_PROGRESS

    try:
        result = process_parameter_task(state, task_content)
        if result.get("status") == "success":
            saved_path = save_parametric_diagram(result["result"], task_id)
            state.assigned_tasks[task_index].result = {**result["result"], "saved_file": saved_path}
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
"""
序列图Agent - 负责基于输入内容创建SysML序列图
"""
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
# 注意：详细的Prompt将在后续补充（由于原Prompt过长，这里先占位）
PROMPT_COT_SYSTEM = """
## 角色
你是一位专业的 SysML/UML 序列图建模专家。你精通序列图的规范，能够准确地从自然语言描述中提取出交互（Interaction）、生命线（Lifeline）及其代表（represents）、消息（Message）及其发送者/接收者、消息事件（MessageOccurrenceSpecification）、组合片段（CombinedFragment）及其操作数（InteractionOperand）和守卫（InteractionConstraint）、以及这些元素所属的包和上下文（如类或操作）。

## 核心要求
**为每个识别出的元素都必须生成一个 `description` 字段。该字段必须以 `原文：` 开头，引用输入文本中最相关的句子或片段，然后用 `简化：` 提供简明扼要的解释。**

## 分析步骤

### 步骤 1：识别模型和顶层包 (Model & Top-Level Packages)
- 确定文本描述的顶层模型名称。
- 识别主要的包 (Package) 及其名称。所有其他元素（如定义参与者的类、交互本身）通常属于某个包或直接属于模型。
- 为每个识别的元素分配合理的名称和临时ID（最终JSON中ID需全局唯一，可使用描述性名称加后缀，如 `-uuid`）。
- **为模型和包生成 `description`，格式为 `原文：[摘录]。简化：[说明]。`**

### 步骤 2：识别交互上下文和交互 (Interaction Context & Interaction)
- 确定序列图所描述的交互（Interaction）的名称。
- 识别这个交互是哪个类（Class/Block）的分类器行为（classifierBehavior），或者是哪个操作（Operation）的具体实现。记录这个拥有交互的上下文元素的临时ID。
- 为交互本身分配一个临时ID。交互的`parentId`应指向拥有它的类或操作。
- **为交互生成 `description`。**

### 步骤 3：识别参与者类/角色 (Participant Classes/Roles - Actors, Blocks, Classes)
- 找出文本中明确提到的、将作为生命线基础的系统实体、用户角色或组件。这些通常是 `Class`、`Block` 或 `Actor`。
- 为每个参与者类/角色提取其 `名称`。
- 分配一个临时的唯一系统 ID。记录它们所属的包 (`parentId`)。
- **为每个参与者类/角色生成 `description`。**

### 步骤 4：识别交互内部属性 (Properties owned by Interaction - for Lifeline representation)
- 检查文本或上下文，看是否需要在交互（Interaction）内部定义属性（Property），这些属性将由生命线代表。这种情况通常发生在生命线代表的不是其拥有者（如Class）的直接部件，而是例如一个Actor的实例或一个临时对象。
- 如果需要，为这些交互拥有的属性分配临时ID，设置其类型（`typeId` 指向对应的Actor或Class）。其`parentId`将是该Interaction的ID。
- **为这些属性生成 `description`。**

### 步骤 5：识别生命线 (Lifelines) 及其代表 (Represents)
- 对于交互中的每一个参与者，创建一个生命线（Lifeline）。
- 确定每个生命线代表（`representsId`）哪个之前识别的参与者类/角色的实例。这可能是直接引用一个Actor或Class的ID，或者更常见地是引用一个Property的ID（这个Property的类型是那个Actor或Class）。该Property可以是拥有交互的类的属性，也可以是交互自身拥有的属性（见上一步）。
- 为每个生命线分配一个临时ID。生命线的`parentId`是其所属的Interaction的ID。
- （可选高级）如果文本提到生命线的创建或销毁，记录下来。
- **为每个生命线生成 `description`，说明它代表哪个实体。**

### 步骤 6：识别消息 (Messages)
- 找出文本中描述的参与者之间的交互动作或通信。这些是 `Message` 元素。
- 为每个消息提取其 `名称`（例如，操作调用名，信号名，或描述性短语）。
- 确定消息的 `发送生命线` (sender lifeline) 和 `接收生命线` (receiver lifeline) 的临时ID。
- 识别消息的 `类型 (messageSort)`：例如，同步调用 (`synchCall`)，异步调用 (`asynchCall`)，回复 (`reply`)，创建消息 (`createMessage`)，销毁消息 (`deleteMessage`)。如果未明确，同步调用是常见默认。
- （可选）识别消息调用的具体操作签名（`signatureId`，指向一个Operation的ID）和消息参数（`arguments`，每个参数包含`body`和`language`）。
- 为每个消息分配一个临时ID。消息的`parentId`是其所属的Interaction的ID。
- **为每个消息生成 `description`，包含原文引用和消息的作用说明。**

### 步骤 7：识别消息发生规约和销毁规约 (MessageOccurrenceSpecification, DestructionOccurrenceSpecification - as Fragments)
- 每个消息都有一个发送事件和一个接收事件，它们发生在各自的生命线上。这些是 `MessageOccurrenceSpecification`。
- 为每个发送事件和接收事件分配一个临时ID。
- 记录每个事件覆盖（`coveredId`）的生命线ID，以及它关联的（`messageId`）消息ID。
- 如果提到了生命线的销毁，创建一个 `DestructionOccurrenceSpecification`，记录其覆盖的（`coveredId`）生命线ID。
- 这些片段的`parentId`是它们所属的`Interaction`或`InteractionOperand`。
- **为每个事件生成 `description`。**

### 步骤 8：识别组合片段 (CombinedFragments), 操作数 (InteractionOperands), 和守卫 (InteractionConstraints)
- 找出文本中描述条件分支（如 "如果...那么...否则..."对应 `alt`）、可选部分（"如果满足条件则..."对应 `opt`）、循环（"重复直到..."对应 `loop`）等控制流结构。这些是 `CombinedFragment`。
- 为每个 `CombinedFragment` 提取其 `交互操作符 (interactionOperator)` (alt, opt, loop, par, seq, strict, neg, critical, ignore, consider, assert, break等)。
- 识别此组合片段覆盖了哪些生命线 (`coveredLifelineIds`)。
- 分配一个临时的唯一系统ID。其`parentId`是其所属的`Interaction`或父`InteractionOperand`。
- 对于组合片段中的每个分支或部分，创建一个 `InteractionOperand`。
- 为每个 `InteractionOperand` 分配临时ID。其`parentId`是所属的`CombinedFragment`。
- 为每个 `InteractionOperand` 提取其 `守卫条件 (guard)`。守卫条件是一个 `InteractionConstraint`。
  - 为 `InteractionConstraint` 分配临时ID。其`parentId`是所属的`InteractionOperand`。
  - `InteractionConstraint` 的规约 (`specification`) 是一个包含 `body` (条件表达式) 和 `language` 的对象。
- 识别每个 `InteractionOperand` 内部包含哪些片段 (`fragmentIds`，通常是 `MessageOccurrenceSpecification`，也可能是嵌套的 `CombinedFragment`)。
- **为组合片段、操作数和守卫生成 `description`。**

### 步骤 9：识别类/Actor的属性和操作 (Properties and Operations of Classes/Actors)
- 如果消息调用了特定的操作，或者生命线代表特定的属性/部件（这些属性属于类/Actor，而不是交互本身），确保这些操作和属性也被识别出来。
- 操作属于其定义的类/Actor (`parentId` 指向类/Actor的ID)。
  - 为操作的参数（`Parameter`）分配临时ID，记录其名称、方向和类型（`typeId` 或 `typeHref`）。参数的`parentId`是其所属的操作。
- 属性属于其定义的类/Actor (`parentId` 指向类/Actor的ID)。
  - 记录属性的名称、类型（`typeId` 或 `typeHref`）、聚合方式（`aggregation`）以及可能的关联（`associationId`）。
- 为它们分配临时ID和名称。
- **为操作、参数和属性生成 `description`。**

### 步骤 10：识别关联 (Associations)
- 如果文本描述了类/Actor之间的静态关系（通常由属性的`association`端点体现），识别这些`Association`。
- 记录关联的成员端点ID (`memberEndIds`)，这些端点是`Property`的ID。
- 为`Association`分配临时ID，其`parentId`是它们所属的包。
- **为关联生成 `description`。**

### 步骤 11：编译和整理输出
- 汇总所有识别出的元素（模型、包、类/Actor、交互、属性、生命线、消息、消息发生规约、销毁规约、组合片段、操作数、交互约束、操作、参数、关联）及其属性和引用关系。
- 准备一个清晰的、结构化的中间表示（"整理优化输出"），概述提取到的所有信息。确保所有临时ID都是唯一的，并且`parentId`关系正确。
- **输出一个完整的层次结构，展示所有元素及其关系。**

## 输出样例

### 输入样例：
"ATM系统模型包含一个"银行服务"包。包内有一个"客户"Actor和一个"ATM"类，以及一个"后端数据库"类。
"ATM"类有一个名为"客户取钱"的序列图（作为其分类器行为）。
在此"客户取钱"交互中：
1. "客户"的实例（生命线L1，代表交互内的一个临时属性 `p_customer`，其类型为"客户"Actor）向"ATM"的实例（生命线L2，代表"ATM"类的一个属性 `atm_instance`）发送"取款请求"消息，该消息调用"ATM"类的"执行取款"操作。
2. "ATM"（生命线L2）向"后端数据库"的实例（生命线L3，代表"ATM"类的属性 `db_connector`，其类型为"后端数据库"）发送"验证余额"消息，调用"后端数据库"的"查询余额"操作，参数为"账户ID"。
3. "后端数据库"（生命线L3）回复"ATM"（生命线L2）"余额信息"消息。
4. 接下来是一个条件判断（alt组合片段）：
   a. 如果"余额充足"（守卫条件），则"ATM"（生命线L2）向"客户"（生命线L1）发送"出钞"回复消息。
   b. 否则，"ATM"（生命线L2）向"客户"（生命线L1）发送"余额不足"回复消息。
5. 在"验证余额"之后，"后端数据库"生命线（L3）被销毁。"

### 思考过程（CoT推理）：

#### 第一步：识别模型和顶层包
- **模型**: "ATM系统模型" (model-atm-sys-uuid)
  - Description: `原文：ATM系统模型。简化：描述ATM系统各组件交互的顶层模型。`
- **包**: "银行服务" (pkg-banksvc-uuid)
  - Description: `原文：包含一个"银行服务"包。简化：包含ATM系统核心业务逻辑的包。`
  - parentId: model-atm-sys-uuid

#### 第二步：识别交互上下文和交互
- **交互**: "客户取钱" (interaction-withdraw-uuid)
  - Description: `原文："ATM"类有一个名为"客户取钱"的序列图（作为其分类器行为）。简化：描述客户通过ATM取款的完整交互流程。`
  - 拥有者: "ATM" 类 (cls-atm-uuid) 的分类器行为
  - parentId: cls-atm-uuid

#### 第三步：识别参与者类/角色
- **Actor**: "客户" (actor-customer-uuid)
  - Description: `原文：包内有一个"客户"Actor。简化：使用ATM系统的银行客户，系统外部参与者。`
  - parentId: pkg-banksvc-uuid
  
- **Class**: "ATM" (cls-atm-uuid)
  - Description: `原文：一个"ATM"类。简化：自动取款机系统的核心类，负责处理客户请求。`
  - parentId: pkg-banksvc-uuid
  
- **Class**: "后端数据库" (cls-db-uuid)
  - Description: `原文：以及一个"后端数据库"类。简化：存储账户信息的后端数据库系统。`
  - parentId: pkg-banksvc-uuid

#### 第四步：识别交互内部属性
- **Property** (交互内属性): "p_customer" (prop-interaction-customer-uuid)
  - Description: `原文："客户"的实例（生命线L1，代表交互内的一个临时属性 p_customer，其类型为"客户"Actor）。简化：交互中客户Actor的实例属性。`
  - typeId: actor-customer-uuid
  - parentId: interaction-withdraw-uuid

#### 第五步：识别生命线及其代表
- **Lifeline L1**: (ll-customer-uuid)
  - Description: `原文："客户"的实例（生命线L1）。简化：代表客户参与者的生命线，贯穿整个取款交互。`
  - representsId: prop-interaction-customer-uuid
  - parentId: interaction-withdraw-uuid
  
- **Lifeline L2**: (ll-atm-uuid)
  - Description: `原文：向"ATM"的实例（生命线L2，代表"ATM"类的一个属性 atm_instance）。简化：代表ATM系统实例的生命线。`
  - representsId: prop-atm-instance-uuid
  - parentId: interaction-withdraw-uuid
  
- **Lifeline L3**: (ll-db-uuid)
  - Description: `原文：向"后端数据库"的实例（生命线L3，代表"ATM"类的属性 db_connector）。简化：代表后端数据库连接的生命线，在验证余额后被销毁。`
  - representsId: prop-db-connector-uuid
  - parentId: interaction-withdraw-uuid

#### 第六步：识别消息
- **Message 1**: "取款请求" (msg-reqwithdraw-uuid)
  - Description: `原文：向"ATM"的实例发送"取款请求"消息，该消息调用"ATM"类的"执行取款"操作。简化：客户向ATM发起取款请求，触发执行取款操作。`
  - 发送者: ll-customer-uuid
  - 接收者: ll-atm-uuid
  - messageSort: synchCall
  - signatureId: op-execwithdraw-uuid
  - parentId: interaction-withdraw-uuid

- **Message 2**: "验证余额" (msg-verifybal-uuid)
  - Description: `原文："ATM"向"后端数据库"的实例发送"验证余额"消息，调用"后端数据库"的"查询余额"操作，参数为"账户ID"。简化：ATM向数据库查询账户余额，传入账户ID参数。`
  - 发送者: ll-atm-uuid
  - 接收者: ll-db-uuid
  - messageSort: synchCall
  - signatureId: op-querybal-uuid
  - arguments: [{"body": "账户ID", "language": "text"}]
  - parentId: interaction-withdraw-uuid

- **Message 3**: "余额信息" (msg-balinfo-uuid)
  - Description: `原文："后端数据库"回复"ATM""余额信息"消息。简化：数据库返回查询到的账户余额信息。`
  - 发送者: ll-db-uuid
  - 接收者: ll-atm-uuid
  - messageSort: reply
  - parentId: interaction-withdraw-uuid

- **Message 4**: "出钞" (msg-dispense-uuid)
  - Description: `原文：如果"余额充足"，则"ATM"向"客户"发送"出钞"回复消息。简化：余额充足时，ATM向客户出钞。`
  - 发送者: ll-atm-uuid
  - 接收者: ll-customer-uuid
  - messageSort: reply
  - parentId: operand-sufficient-uuid

- **Message 5**: "余额不足" (msg-insufficient-uuid)
  - Description: `原文：否则，"ATM"向"客户"发送"余额不足"回复消息。简化：余额不足时，ATM通知客户余额不足。`
  - 发送者: ll-atm-uuid
  - 接收者: ll-customer-uuid
  - messageSort: reply
  - parentId: operand-insufficient-uuid

#### 第七步：识别消息发生规约和销毁规约
- **MessageOccurrenceSpecification** (为每条消息创建发送和接收事件):
  - fragment-send-reqwithdraw-uuid: 
    - Description: `原文：客户发送"取款请求"。简化：取款请求消息的发送事件。`
    - coveredId: ll-customer-uuid, messageId: msg-reqwithdraw-uuid
  - fragment-recv-reqwithdraw-uuid:
    - Description: `原文：ATM接收"取款请求"。简化：取款请求消息的接收事件。`
    - coveredId: ll-atm-uuid, messageId: msg-reqwithdraw-uuid
  - (类似地为其他消息创建事件...)

- **DestructionOccurrenceSpecification**: (fragment-destroy-db-uuid)
  - Description: `原文：在"验证余额"之后，"后端数据库"生命线（L3）被销毁。简化：数据库连接在查询完成后被关闭销毁。`
  - coveredId: ll-db-uuid
  - parentId: interaction-withdraw-uuid

#### 第八步：识别组合片段、操作数和守卫
- **CombinedFragment** (alt): (cf-balancecheck-alt-uuid)
  - Description: `原文：接下来是一个条件判断（alt组合片段）。简化：根据余额情况进行条件分支处理。`
  - interactionOperator: "alt"
  - coveredLifelineIds: [ll-atm-uuid, ll-customer-uuid]
  - parentId: interaction-withdraw-uuid
  
  - **InteractionOperand 1**: (operand-sufficient-uuid)
    - Description: `原文：如果"余额充足"（守卫条件）。简化：余额充足分支，执行出钞操作。`
    - parentId: cf-balancecheck-alt-uuid
    - guardId: guard-sufficient-uuid
    - fragmentIds: [fragment-send-dispense-uuid, fragment-recv-dispense-uuid]
    
    - **InteractionConstraint** (守卫): (guard-sufficient-uuid)
      - Description: `原文：如果"余额充足"（守卫条件）。简化：判断账户余额是否足够支付取款金额。`
      - parentId: operand-sufficient-uuid
      - specification: {"body": "余额充足", "language": "Chinese"}
  
  - **InteractionOperand 2**: (operand-insufficient-uuid)
    - Description: `原文：否则。简化：余额不足分支，返回余额不足提示。`
    - parentId: cf-balancecheck-alt-uuid
    - guardId: null (隐式else)
    - fragmentIds: [fragment-send-insufficient-uuid, fragment-recv-insufficient-uuid]

#### 第九步：识别类/Actor的属性和操作
- **ATM类** (cls-atm-uuid):
  - **Operation**: "执行取款" (op-execwithdraw-uuid)
    - Description: `原文：该消息调用"ATM"类的"执行取款"操作。简化：ATM执行取款的核心业务方法。`
    - parentId: cls-atm-uuid
  
  - **Property**: "atm_instance" (prop-atm-instance-uuid)
    - Description: `原文：代表"ATM"类的一个属性 atm_instance。简化：ATM类的实例属性，用于生命线表示。`
    - parentId: cls-atm-uuid
    - typeId: cls-atm-uuid
  
  - **Property**: "db_connector" (prop-db-connector-uuid)
    - Description: `原文：代表"ATM"类的属性 db_connector，其类型为"后端数据库"。简化：ATM持有的数据库连接器属性。`
    - parentId: cls-atm-uuid
    - typeId: cls-db-uuid
    - aggregation: "composite"

- **后端数据库类** (cls-db-uuid):
  - **Operation**: "查询余额" (op-querybal-uuid)
    - Description: `原文：调用"后端数据库"的"查询余额"操作。简化：数据库提供的查询账户余额方法。`
    - parentId: cls-db-uuid
    
    - **Parameter**: "账户ID" (param-accountid-uuid)
      - Description: `原文：参数为"账户ID"。简化：查询余额操作的输入参数，标识要查询的账户。`
      - parentId: op-querybal-uuid
      - direction: "in"
      - typeId: "String" (或适当的类型)

#### 第十步：识别关联
- (本例中ATM类的属性db_connector体现了与后端数据库的组合关联，已通过Property的aggregation体现，无需额外Association元素)

#### 第十一步：整理优化输出
---
**完整层次结构**:

Model: ATM系统模型 (model-atm-sys-uuid)
├── Package: 银行服务 (pkg-banksvc-uuid)
    ├── Actor: 客户 (actor-customer-uuid)
    ├── Class: ATM (cls-atm-uuid)
    │   ├── Operation: 执行取款 (op-execwithdraw-uuid)
    │   ├── Property: atm_instance (prop-atm-instance-uuid, type: cls-atm-uuid)
    │   ├── Property: db_connector (prop-db-connector-uuid, type: cls-db-uuid, aggregation: composite)
    │   └── Interaction: 客户取钱 (interaction-withdraw-uuid) [classifierBehavior]
    │       ├── Property (interaction-owned): p_customer (prop-interaction-customer-uuid, type: actor-customer-uuid)
    │       ├── Lifeline: L1-客户 (ll-customer-uuid, represents: prop-interaction-customer-uuid)
    │       ├── Lifeline: L2-ATM (ll-atm-uuid, represents: prop-atm-instance-uuid)
    │       ├── Lifeline: L3-数据库 (ll-db-uuid, represents: prop-db-connector-uuid)
    │       ├── Message: 取款请求 (msg-reqwithdraw-uuid, synchCall, sig: op-execwithdraw-uuid)
    │       │   ├── SendEvent: fragment-send-reqwithdraw-uuid (covered: ll-customer-uuid)
    │       │   └── ReceiveEvent: fragment-recv-reqwithdraw-uuid (covered: ll-atm-uuid)
    │       ├── Message: 验证余额 (msg-verifybal-uuid, synchCall, sig: op-querybal-uuid, args: ["账户ID"])
    │       │   ├── SendEvent: fragment-send-verifybal-uuid (covered: ll-atm-uuid)
    │       │   └── ReceiveEvent: fragment-recv-verifybal-uuid (covered: ll-db-uuid)
    │       ├── Message: 余额信息 (msg-balinfo-uuid, reply)
    │       │   ├── SendEvent: fragment-send-balinfo-uuid (covered: ll-db-uuid)
    │       │   └── ReceiveEvent: fragment-recv-balinfo-uuid (covered: ll-atm-uuid)
    │       ├── CombinedFragment (alt): cf-balancecheck-alt-uuid (covered: [ll-atm-uuid, ll-customer-uuid])
    │       │   ├── InteractionOperand: operand-sufficient-uuid
    │       │   │   ├── Guard: guard-sufficient-uuid (spec: "余额充足")
    │       │   │   └── Message: 出钞 (msg-dispense-uuid, reply)
    │       │   │       ├── SendEvent: fragment-send-dispense-uuid (covered: ll-atm-uuid)
    │       │   │       └── ReceiveEvent: fragment-recv-dispense-uuid (covered: ll-customer-uuid)
    │       │   └── InteractionOperand: operand-insufficient-uuid
    │       │       └── Message: 余额不足 (msg-insufficient-uuid, reply)
    │       │           ├── SendEvent: fragment-send-insufficient-uuid (covered: ll-atm-uuid)
    │       │           └── ReceiveEvent: fragment-recv-insufficient-uuid (covered: ll-customer-uuid)
    │       └── DestructionOccurrenceSpecification: fragment-destroy-db-uuid (covered: ll-db-uuid)
    └── Class: 后端数据库 (cls-db-uuid)
        └── Operation: 查询余额 (op-querybal-uuid)
            └── Parameter: 账户ID (param-accountid-uuid, direction: in)
---

## 具体任务
请按照上述十一个步骤对输入文本进行详细分析，为每个识别出的元素和关系生成包含原文引用的 description。

"""

PROMPT_JSON_SYSTEM = """
根据以上详细的推理和"整理优化输出"，请严格按照以下 JSON 格式生成 SysML/UML 序列图的完整描述。

## 核心要求
1. **所有 `id` 字段都是全局唯一的字符串。**
2. **每个元素都必须包含 `description` 字段**，内容应与推理步骤中生成的描述保持一致。
3. **`parentId` 正确反映了元素的包含关系**。
4. 生命线的 `representsId` 指向其所代表的属性（Property）的ID，该属性的类型（typeId）再指向对应的类、Actor。
5. 消息的 `sendEventId` 和 `receiveEventId` 指向对应的 `MessageOccurrenceSpecification` ID。
6. 消息的 `signatureId` 指向被调用的操作的ID（如果适用）。
7. `MessageOccurrenceSpecification` 和 `DestructionOccurrenceSpecification` 的 `coveredId` 指向被覆盖的生命线ID，`messageId` (仅用于MessageOccurrenceSpecification) 指向关联的消息ID。它们的 `parentId` 是所属的 `Interaction` 或 `InteractionOperand`。
8. `CombinedFragment` 包含 `interactionOperator`, `coveredLifelineIds`, 和 `operandIds`。其`parentId`是所属的`Interaction`或父`InteractionOperand`。
9. `InteractionOperand` 包含 `guardId` (可选) 和 `fragmentIds` (其内部的片段)。其`parentId`是所属的`CombinedFragment`。
10. `InteractionConstraint` (守卫) 包含 `specification` 对象（含 `body` 和 `language`）。其`parentId`是所属的`InteractionOperand`。
11. **JSON 根对象只包含 `model` 和 `elements` 两个键。**

## 示例 JSON 结构

```json
{
  "model": [
    {
      "id": "model-atm-sys-uuid",
      "type": "Model",
      "name": "ATM系统模型",
      "description": "原文：ATM系统模型。简化：描述ATM系统各组件交互的顶层模型。"
    }
  ],
  "elements": [
    {
      "id": "pkg-banksvc-uuid",
      "type": "Package",
      "name": "银行服务",
      "parentId": "model-atm-sys-uuid",
      "description": "原文：包含一个"银行服务"包。简化：包含ATM系统核心业务逻辑的包。"
    },
    {
      "id": "actor-customer-uuid",
      "type": "Actor",
      "name": "客户",
      "parentId": "pkg-banksvc-uuid",
      "description": "原文：包内有一个"客户"Actor。简化：使用ATM系统的银行客户，系统外部参与者。"
    },
    {
      "id": "cls-atm-uuid",
      "type": "Class",
      "name": "ATM",
      "parentId": "pkg-banksvc-uuid",
      "description": "原文：一个"ATM"类。简化：自动取款机系统的核心类，负责处理客户请求。",
      "classifierBehaviorId": "interaction-withdraw-uuid",
      "ownedOperationIds": ["op-execwithdraw-uuid"],
      "ownedAttributeIds": ["prop-atm-instance-uuid", "prop-db-connector-uuid"]
    },
    {
      "id": "cls-db-uuid",
      "type": "Class",
      "name": "后端数据库",
      "parentId": "pkg-banksvc-uuid",
      "description": "原文：以及一个"后端数据库"类。简化：存储账户信息的后端数据库系统。",
      "ownedOperationIds": ["op-querybal-uuid"]
    },
    {
      "id": "prop-atm-instance-uuid",
      "type": "Property",
      "name": "atm_instance",
      "parentId": "cls-atm-uuid",
      "typeId": "cls-atm-uuid",
      "description": "原文：代表"ATM"类的一个属性 atm_instance。简化：ATM类的实例属性，用于生命线表示。"
    },
    {
      "id": "prop-db-connector-uuid",
      "type": "Property",
      "name": "db_connector",
      "parentId": "cls-atm-uuid",
      "typeId": "cls-db-uuid",
      "aggregation": "composite",
      "description": "原文：代表"ATM"类的属性 db_connector，其类型为"后端数据库"。简化：ATM持有的数据库连接器属性。"
    },
    {
      "id": "op-execwithdraw-uuid",
      "type": "Operation",
      "name": "执行取款",
      "parentId": "cls-atm-uuid",
      "description": "原文：该消息调用"ATM"类的"执行取款"操作。简化：ATM执行取款的核心业务方法。"
    },
    {
      "id": "op-querybal-uuid",
      "type": "Operation",
      "name": "查询余额",
      "parentId": "cls-db-uuid",
      "parameterIds": ["param-accountid-uuid"],
      "description": "原文：调用"后端数据库"的"查询余额"操作。简化：数据库提供的查询账户余额方法。"
    },
    {
      "id": "param-accountid-uuid",
      "type": "Parameter",
      "name": "账户ID",
      "parentId": "op-querybal-uuid",
      "direction": "in",
      "typeHref": "String",
      "description": "原文：参数为"账户ID"。简化：查询余额操作的输入参数，标识要查询的账户。"
    },
    {
      "id": "interaction-withdraw-uuid",
      "type": "Interaction",
      "name": "客户取钱",
      "parentId": "cls-atm-uuid",
      "description": "原文："ATM"类有一个名为"客户取钱"的序列图（作为其分类器行为）。简化：描述客户通过ATM取款的完整交互流程。",
      "lifelineIds": ["ll-customer-uuid", "ll-atm-uuid", "ll-db-uuid"],
      "messageIds": ["msg-reqwithdraw-uuid", "msg-verifybal-uuid", "msg-balinfo-uuid", "msg-dispense-uuid", "msg-insufficient-uuid"],
      "fragmentIds": ["fragment-send-reqwithdraw-uuid", "fragment-recv-reqwithdraw-uuid", "fragment-destroy-db-uuid", "cf-balancecheck-alt-uuid"],
      "ownedAttributeIds": ["prop-interaction-customer-uuid"]
    },
    {
      "id": "prop-interaction-customer-uuid",
      "type": "Property",
      "name": "p_customer",
      "parentId": "interaction-withdraw-uuid",
      "typeId": "actor-customer-uuid",
      "description": "原文："客户"的实例（生命线L1，代表交互内的一个临时属性 p_customer，其类型为"客户"Actor）。简化：交互中客户Actor的实例属性。"
    },
    {
      "id": "ll-customer-uuid",
      "type": "Lifeline",
      "name": "L1-客户",
      "parentId": "interaction-withdraw-uuid",
      "representsId": "prop-interaction-customer-uuid",
      "description": "原文："客户"的实例（生命线L1）。简化：代表客户参与者的生命线，贯穿整个取款交互。"
    },
    {
      "id": "ll-atm-uuid",
      "type": "Lifeline",
      "name": "L2-ATM",
      "parentId": "interaction-withdraw-uuid",
      "representsId": "prop-atm-instance-uuid",
      "description": "原文：向"ATM"的实例（生命线L2，代表"ATM"类的一个属性 atm_instance）。简化：代表ATM系统实例的生命线。"
    },
    {
      "id": "ll-db-uuid",
      "type": "Lifeline",
      "name": "L3-数据库",
      "parentId": "interaction-withdraw-uuid",
      "representsId": "prop-db-connector-uuid",
      "description": "原文：向"后端数据库"的实例（生命线L3，代表"ATM"类的属性 db_connector）。简化：代表后端数据库连接的生命线，在验证余额后被销毁。"
    },
    {
      "id": "msg-reqwithdraw-uuid",
      "type": "Message",
      "name": "取款请求",
      "parentId": "interaction-withdraw-uuid",
      "sendEventId": "fragment-send-reqwithdraw-uuid",
      "receiveEventId": "fragment-recv-reqwithdraw-uuid",
      "messageSort": "synchCall",
      "signatureId": "op-execwithdraw-uuid",
      "description": "原文：向"ATM"的实例发送"取款请求"消息，该消息调用"ATM"类的"执行取款"操作。简化：客户向ATM发起取款请求，触发执行取款操作。"
    },
    {
      "id": "fragment-send-reqwithdraw-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-customer-uuid",
      "messageId": "msg-reqwithdraw-uuid",
      "description": "原文：客户发送"取款请求"。简化：取款请求消息的发送事件。"
    },
    {
      "id": "fragment-recv-reqwithdraw-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-atm-uuid",
      "messageId": "msg-reqwithdraw-uuid",
      "description": "原文：ATM接收"取款请求"。简化：取款请求消息的接收事件。"
    },
    {
      "id": "msg-verifybal-uuid",
      "type": "Message",
      "name": "验证余额",
      "parentId": "interaction-withdraw-uuid",
      "sendEventId": "fragment-send-verifybal-uuid",
      "receiveEventId": "fragment-recv-verifybal-uuid",
      "messageSort": "synchCall",
      "signatureId": "op-querybal-uuid",
      "arguments": [{"body": "账户ID", "language": "text"}],
      "description": "原文："ATM"向"后端数据库"的实例发送"验证余额"消息，调用"后端数据库"的"查询余额"操作，参数为"账户ID"。简化：ATM向数据库查询账户余额，传入账户ID参数。"
    },
    {
      "id": "fragment-send-verifybal-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-atm-uuid",
      "messageId": "msg-verifybal-uuid",
      "description": "原文：ATM发送"验证余额"。简化：验证余额消息的发送事件。"
    },
    {
      "id": "fragment-recv-verifybal-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-db-uuid",
      "messageId": "msg-verifybal-uuid",
      "description": "原文：数据库接收"验证余额"。简化：验证余额消息的接收事件。"
    },
    {
      "id": "msg-balinfo-uuid",
      "type": "Message",
      "name": "余额信息",
      "parentId": "interaction-withdraw-uuid",
      "sendEventId": "fragment-send-balinfo-uuid",
      "receiveEventId": "fragment-recv-balinfo-uuid",
      "messageSort": "reply",
      "description": "原文："后端数据库"回复"ATM""余额信息"消息。简化：数据库返回查询到的账户余额信息。"
    },
    {
      "id": "fragment-send-balinfo-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-db-uuid",
      "messageId": "msg-balinfo-uuid",
      "description": "原文：数据库发送"余额信息"。简化：余额信息消息的发送事件。"
    },
    {
      "id": "fragment-recv-balinfo-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-atm-uuid",
      "messageId": "msg-balinfo-uuid",
      "description": "原文：ATM接收"余额信息"。简化：余额信息消息的接收事件。"
    },
    {
      "id": "cf-balancecheck-alt-uuid",
      "type": "CombinedFragment",
      "name": "余额检查",
      "parentId": "interaction-withdraw-uuid",
      "interactionOperator": "alt",
      "coveredLifelineIds": ["ll-atm-uuid", "ll-customer-uuid"],
      "operandIds": ["operand-sufficient-uuid", "operand-insufficient-uuid"],
      "description": "原文：接下来是一个条件判断（alt组合片段）。简化：根据余额情况进行条件分支处理。"
    },
    {
      "id": "operand-sufficient-uuid",
      "type": "InteractionOperand",
      "parentId": "cf-balancecheck-alt-uuid",
      "guardId": "guard-sufficient-uuid",
      "fragmentIds": ["fragment-send-dispense-uuid", "fragment-recv-dispense-uuid"],
      "description": "原文：如果"余额充足"（守卫条件）。简化：余额充足分支，执行出钞操作。"
    },
    {
      "id": "guard-sufficient-uuid",
      "type": "InteractionConstraint",
      "parentId": "operand-sufficient-uuid",
      "specification": {
        "body": "余额充足",
        "language": "Chinese"
      },
      "description": "原文：如果"余额充足"（守卫条件）。简化：判断账户余额是否足够支付取款金额。"
    },
    {
      "id": "msg-dispense-uuid",
      "type": "Message",
      "name": "出钞",
      "parentId": "operand-sufficient-uuid",
      "sendEventId": "fragment-send-dispense-uuid",
      "receiveEventId": "fragment-recv-dispense-uuid",
      "messageSort": "reply",
      "description": "原文：如果"余额充足"，则"ATM"向"客户"发送"出钞"回复消息。简化：余额充足时，ATM向客户出钞。"
    },
    {
      "id": "fragment-send-dispense-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "operand-sufficient-uuid",
      "coveredId": "ll-atm-uuid",
      "messageId": "msg-dispense-uuid",
      "description": "原文：ATM发送"出钞"。简化：出钞消息的发送事件。"
    },
    {
      "id": "fragment-recv-dispense-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "operand-sufficient-uuid",
      "coveredId": "ll-customer-uuid",
      "messageId": "msg-dispense-uuid",
      "description": "原文：客户接收"出钞"。简化：出钞消息的接收事件。"
    },
    {
      "id": "operand-insufficient-uuid",
      "type": "InteractionOperand",
      "parentId": "cf-balancecheck-alt-uuid",
      "fragmentIds": ["fragment-send-insufficient-uuid", "fragment-recv-insufficient-uuid"],
      "description": "原文：否则。简化：余额不足分支，返回余额不足提示。"
    },
    {
      "id": "msg-insufficient-uuid",
      "type": "Message",
      "name": "余额不足",
      "parentId": "operand-insufficient-uuid",
      "sendEventId": "fragment-send-insufficient-uuid",
      "receiveEventId": "fragment-recv-insufficient-uuid",
      "messageSort": "reply",
      "description": "原文：否则，"ATM"向"客户"发送"余额不足"回复消息。简化：余额不足时，ATM通知客户余额不足。"
    },
    {
      "id": "fragment-send-insufficient-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "operand-insufficient-uuid",
      "coveredId": "ll-atm-uuid",
      "messageId": "msg-insufficient-uuid",
      "description": "原文：ATM发送"余额不足"。简化：余额不足消息的发送事件。"
    },
    {
      "id": "fragment-recv-insufficient-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "operand-insufficient-uuid",
      "coveredId": "ll-customer-uuid",
      "messageId": "msg-insufficient-uuid",
      "description": "原文：客户接收"余额不足"。简化：余额不足消息的接收事件。"
    },
    {
      "id": "fragment-destroy-db-uuid",
      "type": "DestructionOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-db-uuid",
      "description": "原文：在"验证余额"之后，"后端数据库"生命线（L3）被销毁。简化：数据库连接在查询完成后被关闭销毁。"
    }
  ]
}
```

## 输出要求
- 请严格按照上述 JSON 结构输出完整的序列图模型。
- 确保所有 ID 引用的正确性和一致性。
- 确保每个元素都包含 `description` 字段，内容与推理步骤一致。
- 不要在 JSON 之外添加任何解释性文本（可以用 markdown 代码块包裹 JSON）。
- 请仅输出 JSON，不要添加额外的说明或注释。
"""

# ==================== Pydantic 模型定义 ====================
class DiagramModel(BaseModel):
    id: str = Field(description="模型唯一ID")
    name: str = Field(description="模型名称")
    type: str = Field(description="模型类型", default="Model")

class SequenceDiagramOutput(BaseModel):
    model: List[DiagramModel] = Field(description="模型列表")
    elements: List[Dict[str, Any]] = Field(description="元素列表（序列图元素）")

# ==================== 辅助函数 ====================

def get_sequence_output_dir() -> str:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    output_dir = os.path.join(project_root, "data", "output", "sequence_diagrams")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"创建序列图输出目录: {output_dir}")
    return output_dir

def save_sequence_diagram(result: Dict[str, Any], task_id: str) -> str:
    try:
        output_dir = get_sequence_output_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"sequence_diagram_{task_id}_{timestamp}.json"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ 序列图已保存到: {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"保存序列图失败: {e}", exc_info=True)
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
        elem_type = elem.get("type", "")
        elem_name = elem.get("name", "Unnamed")
        
        if "description" not in elem or not elem.get("description"):
            # 根据类型生成默认描述
            if elem_type == "Package":
                elem["description"] = f"包：{elem_name}（自动生成）"
            elif elem_type == "Actor":
                elem["description"] = f"参与者：{elem_name}，系统外部实体（自动生成）"
            elif elem_type == "Class":
                elem["description"] = f"类：{elem_name}，系统组件（自动生成）"
            elif elem_type == "Block":
                elem["description"] = f"块：{elem_name}，系统组件（自动生成）"
            elif elem_type == "Interaction":
                elem["description"] = f"交互：{elem_name}，描述对象间的消息序列（自动生成）"
            elif elem_type == "Lifeline":
                repr_id = elem.get("representsId", "?")
                elem["description"] = f"生命线：{elem_name}，代表 {repr_id}（自动生成）"
            elif elem_type == "Message":
                sort = elem.get("messageSort", "unknown")
                elem["description"] = f"消息：{elem_name}，类型={sort}（自动生成）"
            elif elem_type == "MessageOccurrenceSpecification":
                msg_id = elem.get("messageId", "?")
                elem["description"] = f"消息事件：关联消息 {msg_id}（自动生成）"
            elif elem_type == "DestructionOccurrenceSpecification":
                covered = elem.get("coveredId", "?")
                elem["description"] = f"销毁事件：销毁生命线 {covered}（自动生成）"
            elif elem_type == "CombinedFragment":
                op = elem.get("interactionOperator", "unknown")
                elem["description"] = f"组合片段：{elem_name}，操作符={op}（自动生成）"
            elif elem_type == "InteractionOperand":
                elem["description"] = f"交互操作数：{elem_name}（自动生成）"
            elif elem_type == "InteractionConstraint":
                spec = elem.get("specification", {}).get("body", "")
                elem["description"] = f"交互约束：{spec}（自动生成）"
            elif elem_type == "Property":
                type_id = elem.get("typeId", "?")
                elem["description"] = f"属性：{elem_name}，类型={type_id}（自动生成）"
            elif elem_type == "Operation":
                elem["description"] = f"操作：{elem_name}（自动生成）"
            elif elem_type == "Parameter":
                direction = elem.get("direction", "in")
                elem["description"] = f"参数：{elem_name}，方向={direction}（自动生成）"
            elif elem_type == "Association":
                elem["description"] = f"关联：{elem_name}（自动生成）"
            else:
                elem["description"] = f"{elem_type} 元素：{elem_name}（自动生成）"
            
            logger.warning(f"⚠️ 自动补充 description: id={elem.get('id','unknown')} type={elem_type}")
    
    return result

# ==================== 主处理函数 ====================

def process_sequence_task(state: WorkflowState, task_content: str) -> Dict[str, Any]:
    logger.info("🎯 开始处理序列图任务")
    try:
        llm = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            base_url=settings.base_url,
            temperature=0.0,
            streaming=True,
            max_tokens=getattr(settings, "max_tokens", 4096)
        )

        # ===== 阶段1：CoT 推理 =====
        print(f"\n{'='*80}")
        print(f"🧠 阶段1: 序列图分析与推理")
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
        print(f"📝 阶段2: 生成结构化JSON (序列图)")
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
            validated = SequenceDiagramOutput(**result)
            result = validated.dict()
            logger.info("✅ Pydantic 验证通过 (序列图)")
        except Exception as e:
            logger.warning(f"⚠️ Pydantic 验证失败 (序列图)，继续使用修复后的JSON: {e}")

        logger.info("✅ 序列图任务处理完成")
        return {"status": "success", "result": result}

    except Exception as e:
        logger.error(f"❌ 序列图任务处理失败: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

def sequence_agent(state: WorkflowState, task_id: str, task_content: str) -> WorkflowState:
    logger.info(f"序列图Agent开始处理任务 {task_id}")

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
        result = process_sequence_task(state, task_content)
        if result.get("status") == "success":
            saved_path = save_sequence_diagram(result["result"], task_id)
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
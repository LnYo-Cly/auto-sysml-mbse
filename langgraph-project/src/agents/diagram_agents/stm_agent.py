"""
状态机图Agent - 负责基于输入内容创建SysML状态机图
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
# 注意：详细的Prompt将在后续补充
PROMPT_COT_SYSTEM = """
## 角色
你是一位专业的 SysML 状态机图建模专家。你精通 SysML 状态机图的规范，能够准确地从流程、行为或系统生命周期的自然语言描述中提取出状态机、区域、状态（简单、复合、最终）、伪状态（初始、最终、进入/退出点、选择、连接、派生/汇合点）、转换、触发器（事件）、守卫条件和效果/状态行为（进入、执行、退出）等元素，并理解它们之间的关系。

## 核心要求
**为每个识别出的元素（包、块、状态机、区域、状态、伪状态、转换、活动、信号、事件等）都必须生成一个 `description` 字段。该字段必须以 `原文：` 开头，引用输入文本中最相关的句子或片段，然后用 `简化：` 提供简明扼要的解释。**

特别地，你理解状态的 entry/do/exit 行为以及转换的 effect 行为通常是通过一个内嵌的包装活动（包含 InitialNode -> CallBehaviorAction -> FinalNode 的结构）来调用一个在别处定义的具体行为。

## 规则
你的目标是根据输入的文本描述，分析并生成构建 SysML 状态机图所需的元素信息。请遵循以下九个步骤进行思考和分析：

### 步骤 1：识别顶层容器 (Model, Package, Block)
- 确定文本描述的顶层模型名称。
- 识别主要的包 (Package) 及其名称。通常需要两个包：
  - 主应用包：包含状态机所描述的块/类
  - 行为库包：包含所有被调用的具体活动
- 识别状态机所描述其行为的块 (Block/Class)，或状态机直接所属的包。
- 为每个识别的元素分配合理的名称和临时 ID。
- **为每个元素生成 `description`，格式为 `原文：[摘录]。简化：[说明]。`**

### 步骤 2：识别状态机 (StateMachine) 和区域 (Region)
- 找出核心的状态机定义，并为其命名。确定其是块的 `ownedBehavior` 还是包的 `packagedElement`。
- 状态机总是至少包含一个主区域 (Region)。识别此区域。复合状态也包含区域。
- 分配临时 ID。
- **为状态机和区域生成 `description`，包含原文引用。**

### 步骤 3：识别状态 (States - Simple, Composite, Final)
- 从描述中找出系统可能处于的各种稳定情况，这些是状态 (State)。
- 区分简单状态和复合状态（复合状态通常描述为包含子状态或有明确的进入/退出点）。
- 识别是否有明确的最终状态 (FinalState)。
- 为每个状态命名并分配临时 ID。记录其所属的区域。
- **为每个状态生成 `description`，格式为 `原文：[摘录状态描述]。简化：[状态的作用和特点]。`**

### 步骤 4：识别伪状态 (Pseudostates)
- `InitialNode`: 识别状态机或区域的起点（通常称为初始状态）。
- `FinalState`: （作为一种特殊 State 处理，见步骤3）识别状态机或区域的终点。
- `EntryPoint`/`ExitPoint`: 如果描述了复合状态的特定入口和出口，识别它们。
- `Choice`: 识别基于守卫条件选择不同路径的决策点。
- `Junction`: 识别多个转换路径汇合或分支出多个路径的点。
- `Fork`/`Join`: 识别并发区域的开始和结束。
- 为每个伪状态分配合理的名称（可选）和临时 ID，记录其类型 (`kind`) 和所属区域或复合状态。
- **为每个伪状态生成 `description`，说明其在状态机中的作用。**

### 步骤 5：识别状态行为 (Entry, Do, Exit Activities)
- 对于每个状态，识别是否有描述进入时 (`entry`)，持续执行时 (`doActivity`)，或退出时 (`exit`) 执行的动作。
- 这些动作的核心是**调用一个在别处（通常在专门的"行为库"包中）定义的具体行为 (Activity)**。
- **为这个被调用的具体行为命名并分配临时 ID**（例如，`act-actual-perform-task-uuid`）。
- 将其记录为一个独立的 `Activity` 类型的元素，并指明其父包（如"行为库"）。
- **为这个活动生成 `description`，格式为 `原文：[动作描述]。简化：[活动的功能]。`**
- 在状态的 JSON 表示中，记录这个被调用的具体行为的 ID (`calledBehaviorId`)。
- 同时，为包装这个调用的内嵌活动隐式分配一个 ID（例如 `wrapper-entry-for-stateX-uuid`）。

### 步骤 6：识别转换 (Transitions)
- 找出从一个状态（或伪状态）到另一个状态（或伪状态）的路径或变化，这些是转换 (Transition)。
- 明确每个转换的源 (source) 和目标 (target)。
- 记录转换所属的区域。分配临时 ID。
- **为每个转换生成 `description`，格式为 `原文：[转换条件描述]。简化：[转换的触发条件和目标]。`**

### 步骤 7：识别转换的组成部分 (Triggers, Guards, Effects)
- **触发器 (Triggers)**: 对于每个转换，确定是什么事件或信号触发了它。
  - 识别相关的事件 (Event)，如信号事件 (SignalEvent)、时间事件 (TimeEvent) 等。
  - 识别这些事件关联的信号 (Signal)（如果适用）。
  - 为事件和信号命名并分配临时 ID。在转换上记录触发器引用的事件 ID。
  - **为信号和事件生成 `description`，说明其含义和用途。**
- **守卫条件 (Guards)**: 确定转换发生前必须满足的条件。记录守卫表达式和语言（如 "English", "OCL"）。
- **效果行为 (Effects)**: 确定转换发生时执行的动作。
  - 这个动作的核心是**调用一个在别处（通常在专门的"行为库"包中）定义的具体行为 (Activity)**。
  - **为这个被调用的具体行为命名并分配临时 ID**（例如，`act-actual-log-event-uuid`）。
  - 将其记录为一个独立的 `Activity` 类型的元素，并指明其父包。
  - **为这个活动生成 `description`。**
  - 在转换的 JSON 表示中，记录这个被调用的具体行为的 ID (`calledBehaviorId`)。
  - 同样，为包装这个调用的内嵌活动隐式分配一个 ID。

### 步骤 8：识别其他辅助元素
- 如在触发器中用到的信号 (Signal)，或在守卫条件中可能用到的属性 (Property on a Block)。
- 所有被状态行为或转换效果所调用的具体行为 (Activity)，都应作为独立的元素被识别，并通常放置在一个共享的"行为库"包中。
- **为所有辅助元素生成 `description`。**

### 步骤 9：编译和整理输出
- 汇总所有识别出的元素（模型、包、块、状态机、区域、状态、伪状态、转换、**被调用的具体活动**、事件、信号等）及其属性。
- 明确元素间的关系（例如，状态属于区域，转换属于区域，状态机属于块或包，状态/转换的 `entry/do/exit/effect` 通过 `calledBehaviorId` 引用一个"行为库"中的活动等）。
- 准备一个清晰的、结构化的中间表示（"整理优化输出"），概述提取到的所有信息。
- **确保所有元素都包含 `description` 字段。**

## 输出样例

### 输入样例：
"请描述一个简单的"门禁系统"的状态机。该状态机属于"门控制器"模块，所有具体的行为都定义在"门禁行为库"包中。
系统启动后，首先进入"锁定"状态。这是初始状态。
当接收到"有效开锁信号"时，如果"安全系统已解除"，门禁从"锁定"状态转换到"开锁中"状态，并在转换时执行"记录开锁尝试"这个已定义的行为。
进入"开锁中"状态时，会调用"执行解锁门闩"行为。在"开锁中"状态，系统会持续调用"保持门锁打开"行为。离开"开锁中"状态时，会调用"执行检查门是否已关闭"行为。
一段时间后（触发"超时事件"），系统从"开锁中"状态自动转换回"锁定"状态，并执行"执行自动上锁"这个已定义的行为。
还有一个"报警"状态。如果从"锁定"状态检测到"强制开门事件"，系统会转换到"报警"状态，并调用"执行鸣响警报"行为作为效果。
"锁定"状态是一个复合状态，它有一个名为"内部安全检查"的子区域。此子区域包含一个初始伪状态，转换到一个"自检"状态，然后转换到一个最终伪状态。"锁定"状态还有一个名为"ep_lock"的进入点。
"有效开锁信号"是一个信号。"超时事件"和"强制开门事件"也是事件。"

### 思考过程（CoT推理）：

#### 步骤 1：识别顶层容器
- **模型**: "门禁系统模型" (model-door-access-sm-uuid)
  - Description: `原文：请描述一个简单的"门禁系统"的状态机。简化：顶层模型，包含门禁系统的所有状态机和行为定义。`
- **包 1**: "门禁控制包" (pkg-door-control-uuid)
  - Description: `原文：该状态机属于"门控制器"模块。简化：主应用包，包含门控制器和状态机定义。`
- **包 2**: "门禁行为库" (pkg-door-behaviors-uuid)
  - Description: `原文：所有具体的行为都定义在"门禁行为库"包中。简化：行为库包，存储所有可被状态和转换调用的具体活动。`
- **块**: "门控制器" (blk-door-controller-uuid, parentId: pkg-door-control-uuid)
  - Description: `原文：该状态机属于"门控制器"模块。简化：门禁系统的核心控制器，其行为由状态机定义。`

#### 步骤 2：识别状态机和区域
- **状态机**: "门禁状态机" (sm-door-access-uuid)，属于 blk-door-controller-uuid
  - Description: `原文：请描述一个简单的"门禁系统"的状态机。简化：定义门控制器的完整生命周期和行为逻辑。`
- **主区域**: "主区域" (region-door-main-uuid)，属于 sm-door-access-uuid
  - Description: `原文：系统启动后，首先进入"锁定"状态...包含多个状态转换。简化：状态机的主要活动区域，包含所有顶层状态和转换。`

#### 步骤 3：识别状态
- **状态 1**: "锁定" (state-locked-uuid)，在 region-door-main-uuid，isComposite: true
  - Description: `原文：系统启动后，首先进入"锁定"状态。这是初始状态。"锁定"状态是一个复合状态，它有一个名为"内部安全检查"的子区域。简化：系统的默认安全状态，内部执行安全检查，是一个包含子区域的复合状态。`
- **状态 2**: "开锁中" (state-unlocking-uuid)，在 region-door-main-uuid
  - Description: `原文：门禁从"锁定"状态转换到"开锁中"状态...进入"开锁中"状态时，会调用"执行解锁门闩"行为。简化：门正在解锁过程中的临时状态，执行解锁和保持打开的动作。`
- **状态 3**: "报警" (state-alarm-uuid)，在 region-door-main-uuid
  - Description: `原文：还有一个"报警"状态。如果从"锁定"状态检测到"强制开门事件"，系统会转换到"报警"状态。简化：异常状态，在检测到非法开门尝试时触发警报。`
- **状态 4**: "自检" (state-selfcheck-uuid)，在 region-locked-sub-uuid（锁定状态的子区域）
  - Description: `原文：此子区域包含一个初始伪状态，转换到一个"自检"状态。简化：锁定状态内部的安全自检状态，用于验证系统完整性。`

#### 步骤 4：识别伪状态
- **主区域初始伪状态**: (ps-main-initial-uuid)，kind: initial，在 region-door-main-uuid
  - Description: `原文：系统启动后，首先进入"锁定"状态。简化：状态机的起始点，系统启动时的入口。`
- **锁定状态的进入点**: "ep_lock" (ps-locked-entry1-uuid)，kind: entryPoint，属于 state-locked-uuid
  - Description: `原文："锁定"状态还有一个名为"ep_lock"的进入点。简化：复合状态"锁定"的命名进入点，用于从外部进入复合状态。`
- **锁定子区域初始伪状态**: (ps-locked-sub-initial-uuid)，kind: initial，在 region-locked-sub-uuid
  - Description: `原文：此子区域包含一个初始伪状态。简化：内部安全检查子区域的起始点。`
- **锁定子区域最终伪状态**: (ps-locked-sub-final-uuid)，kind: final，在 region-locked-sub-uuid
  - Description: `原文：然后转换到一个最终伪状态。简化：内部安全检查子区域的终止点，表示自检完成。`

#### 步骤 5：识别状态行为
- **"开锁中"状态 (state-unlocking-uuid)**:
  - **Entry**: 调用 "执行解锁门闩" (act-execute-unlock-bolt-uuid, parentId: pkg-door-behaviors-uuid)
    - Activity Description: `原文：进入"开锁中"状态时，会调用"执行解锁门闩"行为。简化：物理解锁门闩的具体操作活动。`
    - JSON 表示: `entry: { wrapperActivityId: "wrapper-entry-unlocking-uuid", calledBehaviorId: "act-execute-unlock-bolt-uuid" }`
  - **DoActivity**: 调用 "保持门锁打开" (act-keep-door-open-uuid, parentId: pkg-door-behaviors-uuid)
    - Activity Description: `原文：在"开锁中"状态，系统会持续调用"保持门锁打开"行为。简化：持续监控并保持门锁处于打开状态的活动。`
    - JSON 表示: `doActivity: { wrapperActivityId: "wrapper-do-unlocking-uuid", calledBehaviorId: "act-keep-door-open-uuid" }`
  - **Exit**: 调用 "执行检查门是否已关闭" (act-execute-check-closed-uuid, parentId: pkg-door-behaviors-uuid)
    - Activity Description: `原文：离开"开锁中"状态时，会调用"执行检查门是否已关闭"行为。简化：在退出前验证门是否已正确关闭的检查活动。`
    - JSON 表示: `exit: { wrapperActivityId: "wrapper-exit-unlocking-uuid", calledBehaviorId: "act-execute-check-closed-uuid" }`

#### 步骤 6：识别转换
- **T1**: 从 ps-main-initial-uuid 到 state-locked-uuid (trans-initial-to-locked-uuid)，在 region-door-main-uuid
  - Description: `原文：系统启动后，首先进入"锁定"状态。简化：系统初始化后自动进入锁定状态的转换。`
- **T2**: 从 state-locked-uuid 到 state-unlocking-uuid (trans-locked-to-unlocking-uuid)，在 region-door-main-uuid
  - Description: `原文：当接收到"有效开锁信号"时，如果"安全系统已解除"，门禁从"锁定"状态转换到"开锁中"状态。简化：在接收到授权信号且满足安全条件时，从锁定转换到开锁的过程。`
- **T3**: 从 state-unlocking-uuid 到 state-locked-uuid (trans-unlocking-to-locked-uuid)，在 region-door-main-uuid
  - Description: `原文：一段时间后（触发"超时事件"），系统从"开锁中"状态自动转换回"锁定"状态。简化：超时后自动重新锁定的安全机制转换。`
- **T4**: 从 state-locked-uuid 到 state-alarm-uuid (trans-locked-to-alarm-uuid)，在 region-door-main-uuid
  - Description: `原文：如果从"锁定"状态检测到"强制开门事件"，系统会转换到"报警"状态。简化：检测到非法开门尝试时触发警报的转换。`
- **T5**: 从 ps-locked-sub-initial-uuid 到 state-selfcheck-uuid (trans-subinitial-to-selfcheck-uuid)，在 region-locked-sub-uuid
  - Description: `原文：此子区域包含一个初始伪状态，转换到一个"自检"状态。简化：锁定状态内部自动启动安全自检的转换。`
- **T6**: 从 state-selfcheck-uuid 到 ps-locked-sub-final-uuid (trans-selfcheck-to-subfinal-uuid)，在 region-locked-sub-uuid
  - Description: `原文：然后转换到一个最终伪状态。简化：自检完成后结束内部安全检查流程的转换。`

#### 步骤 7：识别转换的组成部分
- **对于 T2 (locked -> unlocking)**:
  - **Trigger**: 引用 "有效开锁信号事件" (event-valid-unlock-sig-event-uuid)
  - **Guard**: "安全系统已解除 == true" (language: "English")
  - **Effect**: 调用 "记录开锁尝试" (act-log-unlock-attempt-uuid, parentId: pkg-door-behaviors-uuid)
    - Activity Description: `原文：并在转换时执行"记录开锁尝试"这个已定义的行为。简化：记录每次开锁尝试的日志活动，用于审计。`
    - JSON 表示: `effect: { wrapperActivityId: "wrapper-effect-t2-uuid", calledBehaviorId: "act-log-unlock-attempt-uuid" }`

- **对于 T3 (unlocking -> locked)**:
  - **Trigger**: 引用 "超时事件" (event-timeout-uuid)
  - **Effect**: 调用 "执行自动上锁" (act-execute-auto-lock-uuid, parentId: pkg-door-behaviors-uuid)
    - Activity Description: `原文：并执行"执行自动上锁"这个已定义的行为。简化：自动重新锁定门闩的物理操作活动。`
    - JSON 表示: `effect: { wrapperActivityId: "wrapper-effect-t3-uuid", calledBehaviorId: "act-execute-auto-lock-uuid" }`

- **对于 T4 (locked -> alarm)**:
  - **Trigger**: 引用 "强制开门事件" (event-forced-open-event-uuid)
  - **Effect**: 调用 "执行鸣响警报" (act-execute-sound-alarm-uuid, parentId: pkg-door-behaviors-uuid)
    - Activity Description: `原文：并调用"执行鸣响警报"行为作为效果。简化：触发声光警报的具体执行活动。`
    - JSON 表示: `effect: { wrapperActivityId: "wrapper-effect-t4-uuid", calledBehaviorId: "act-execute-sound-alarm-uuid" }`

#### 步骤 8：识别其他辅助元素
- **信号 (Signal)**:
  - "有效开锁信号" (sig-valid-unlock-uuid, parentId: pkg-door-control-uuid)
    - Description: `原文："有效开锁信号"是一个信号。简化：表示授权开锁请求的通信信号。`

- **事件 (Event)**:
  - "有效开锁信号事件" (event-valid-unlock-sig-event-uuid, type: SignalEvent, signalId: sig-valid-unlock-uuid, parentId: pkg-door-control-uuid)
    - Description: `原文：当接收到"有效开锁信号"时。简化：接收到有效开锁信号时触发的信号事件。`
  - "超时事件" (event-timeout-uuid, type: TimeEvent, parentId: pkg-door-control-uuid)
    - Description: `原文：一段时间后（触发"超时事件"）。简化：开锁状态持续一定时间后自动触发的时间事件。`
  - "强制开门事件" (event-forced-open-event-uuid, type: Event, parentId: pkg-door-control-uuid)
    - Description: `原文：如果从"锁定"状态检测到"强制开门事件"。简化：检测到非授权的物理强制开门行为时触发的事件。`

- **被调用的具体活动 (Activities in pkg-door-behaviors-uuid)**:
  - "记录开锁尝试" (act-log-unlock-attempt-uuid)
  - "执行解锁门闩" (act-execute-unlock-bolt-uuid)
  - "保持门锁打开" (act-keep-door-open-uuid)
  - "执行检查门是否已关闭" (act-execute-check-closed-uuid)
  - "执行自动上锁" (act-execute-auto-lock-uuid)
  - "执行鸣响警报" (act-execute-sound-alarm-uuid)
  （所有这些活动的 parentId 都是 pkg-door-behaviors-uuid，description 已在步骤5和7中定义）

#### 步骤 9：整理优化输出
---
**模型**: 门禁系统模型 (model-door-access-sm-uuid)
  
**包**: 门禁控制包 (pkg-door-control-uuid)
  - **块**: 门控制器 (blk-door-controller-uuid, classifierBehaviorId: sm-door-access-uuid)
    - **状态机**: 门禁状态机 (sm-door-access-uuid)
      - **主区域**: 主区域 (region-door-main-uuid)
        - **初始伪状态**: (ps-main-initial-uuid, kind: initial)
        - **状态**: 锁定 (state-locked-uuid, isComposite: true)
          - **进入点**: ep_lock (ps-locked-entry1-uuid, kind: entryPoint)
          - **子区域**: 内部安全检查 (region-locked-sub-uuid)
            - **初始伪状态**: (ps-locked-sub-initial-uuid, kind: initial)
            - **状态**: 自检 (state-selfcheck-uuid)
            - **最终伪状态**: (ps-locked-sub-final-uuid, kind: final)
            - **转换**:
              - T5: (ps-locked-sub-initial-uuid) -> (state-selfcheck-uuid)
              - T6: (state-selfcheck-uuid) -> (ps-locked-sub-final-uuid)
        - **状态**: 开锁中 (state-unlocking-uuid)
          - Entry: wrapper-entry-unlocking-uuid -> act-execute-unlock-bolt-uuid
          - DoActivity: wrapper-do-unlocking-uuid -> act-keep-door-open-uuid
          - Exit: wrapper-exit-unlocking-uuid -> act-execute-check-closed-uuid
        - **状态**: 报警 (state-alarm-uuid)
        - **转换**:
          - T1: (ps-main-initial-uuid) -> (state-locked-uuid)
          - T2: (state-locked-uuid) -> (state-unlocking-uuid)
            - Trigger: event-valid-unlock-sig-event-uuid
            - Guard: "安全系统已解除 == true"
            - Effect: wrapper-effect-t2-uuid -> act-log-unlock-attempt-uuid
          - T3: (state-unlocking-uuid) -> (state-locked-uuid)
            - Trigger: event-timeout-uuid
            - Effect: wrapper-effect-t3-uuid -> act-execute-auto-lock-uuid
          - T4: (state-locked-uuid) -> (state-alarm-uuid)
            - Trigger: event-forced-open-event-uuid
            - Effect: wrapper-effect-t4-uuid -> act-execute-sound-alarm-uuid
  
  - **信号**:
    - 有效开锁信号 (sig-valid-unlock-uuid)
  
  - **事件**:
    - 有效开锁信号事件 (event-valid-unlock-sig-event-uuid, SignalEvent, signalId: sig-valid-unlock-uuid)
    - 超时事件 (event-timeout-uuid, TimeEvent)
    - 强制开门事件 (event-forced-open-event-uuid, Event)

**包**: 门禁行为库 (pkg-door-behaviors-uuid)
  - **活动**:
    - 记录开锁尝试 (act-log-unlock-attempt-uuid)
    - 执行解锁门闩 (act-execute-unlock-bolt-uuid)
    - 保持门锁打开 (act-keep-door-open-uuid)
    - 执行检查门是否已关闭 (act-execute-check-closed-uuid)
    - 执行自动上锁 (act-execute-auto-lock-uuid)
    - 执行鸣响警报 (act-execute-sound-alarm-uuid)
---
"""

PROMPT_JSON_SYSTEM = """
根据以上详细的推理和"整理优化输出"，请严格按照以下 JSON 格式生成 SysML 状态机图的完整描述。

## 核心要求
1. **所有 `id` 字段都是全局唯一的字符串。**
2. **每个元素都必须包含 `description` 字段**，内容应与推理步骤中生成的描述保持一致。
3. **`parentId` 正确反映了元素的包含关系。**
4. 对于 `State` 元素：
   - 如果它是复合状态，应包含 `regions`（Region ID 列表）和/或 `connectionPoints`（Pseudostate ID 列表）。`isComposite: true` 也可以作为显式标记。
   - 如果它是最终状态，其 `type` 应为 `FinalState`。简单状态则为 `State`。
   - `entry`, `doActivity`, `exit` 行为应表示为一个对象，包含：
     - `wrapperActivityId`: 内嵌包装活动的唯一 ID
     - `calledBehaviorId`: 被调用的、在行为库中定义的具体活动的 ID
   - 如果状态没有某个行为，则对应的键（如 `entry`）不存在。
5. 对于 `Pseudostate` 元素，`kind` 字段必须准确表示其类型（initial, final, entryPoint, exitPoint, choice, junction, fork, join 等）。其 `parentId` 可以是 Region 或作为连接点的 State。
6. 对于 `Transition` 元素：
   - `sourceId` 和 `targetId` 正确引用了源和目标状态/伪状态的 ID。
   - `triggerIds` 是一个列表，包含触发此转换的事件 ID。
   - `guard` 是一个对象，包含 `expression` 和 `language`。
   - `effect` 行为应表示为一个对象，包含 `wrapperActivityId` 和 `calledBehaviorId`。
   - 如果转换没有效果行为，则 `effect` 键不存在。
7. **所有被 `calledBehaviorId` 引用的活动 (Activity)，都应作为独立的元素定义在 `elements` 列表中**，并且通常其 `parentId` 指向一个行为库包。
8. `SignalEvent` 元素应有 `signalId` 引用其关联的 `Signal`。
9. `Block` 元素可以通过 `classifierBehaviorId` 引用其主要的 `StateMachine`。
10. **JSON 根对象只包含 `model` 和 `elements` 两个键。**

## 示例 JSON 结构

```json
{
  "model": [
    {
      "id": "model-door-access-sm-uuid",
      "name": "门禁系统模型",
      "type": "Model",
      "description": "原文：请描述一个简单的"门禁系统"的状态机。简化：顶层模型，包含门禁系统的所有状态机和行为定义。"
    }
  ],
  "elements": [
    {
      "id": "pkg-door-control-uuid",
      "type": "Package",
      "name": "门禁控制包",
      "parentId": "model-door-access-sm-uuid",
      "description": "原文：该状态机属于"门控制器"模块。简化：主应用包，包含门控制器和状态机定义。"
    },
    {
      "id": "pkg-door-behaviors-uuid",
      "type": "Package",
      "name": "门禁行为库",
      "parentId": "model-door-access-sm-uuid",
      "description": "原文：所有具体的行为都定义在"门禁行为库"包中。简化：行为库包，存储所有可被状态和转换调用的具体活动。"
    },
    {
      "id": "blk-door-controller-uuid",
      "type": "Block",
      "name": "门控制器",
      "parentId": "pkg-door-control-uuid",
      "classifierBehaviorId": "sm-door-access-uuid",
      "description": "原文：该状态机属于"门控制器"模块。简化：门禁系统的核心控制器，其行为由状态机定义。"
    },
    {
      "id": "sm-door-access-uuid",
      "type": "StateMachine",
      "name": "门禁状态机",
      "parentId": "blk-door-controller-uuid",
      "description": "原文：请描述一个简单的"门禁系统"的状态机。简化：定义门控制器的完整生命周期和行为逻辑。"
    },
    {
      "id": "region-door-main-uuid",
      "type": "Region",
      "name": "主区域",
      "parentId": "sm-door-access-uuid",
      "description": "原文：系统启动后，首先进入"锁定"状态...包含多个状态转换。简化：状态机的主要活动区域，包含所有顶层状态和转换。"
    },
    {
      "id": "ps-main-initial-uuid",
      "type": "Pseudostate",
      "kind": "initial",
      "parentId": "region-door-main-uuid",
      "description": "原文：系统启动后，首先进入"锁定"状态。简化：状态机的起始点，系统启动时的入口。"
    },
    {
      "id": "state-locked-uuid",
      "type": "State",
      "name": "锁定",
      "parentId": "region-door-main-uuid",
      "isComposite": true,
      "connectionPoints": ["ps-locked-entry1-uuid"],
      "regions": ["region-locked-sub-uuid"],
      "description": "原文：系统启动后，首先进入"锁定"状态。这是初始状态。"锁定"状态是一个复合状态，它有一个名为"内部安全检查"的子区域。简化：系统的默认安全状态，内部执行安全检查，是一个包含子区域的复合状态。"
    },
    {
      "id": "ps-locked-entry1-uuid",
      "type": "Pseudostate",
      "kind": "entryPoint",
      "name": "ep_lock",
      "parentId": "state-locked-uuid",
      "description": "原文："锁定"状态还有一个名为"ep_lock"的进入点。简化：复合状态"锁定"的命名进入点，用于从外部进入复合状态。"
    },
    {
      "id": "region-locked-sub-uuid",
      "type": "Region",
      "name": "内部安全检查",
      "parentId": "state-locked-uuid",
      "description": "原文："锁定"状态是一个复合状态，它有一个名为"内部安全检查"的子区域。简化：锁定状态的内部区域，执行安全自检流程。"
    },
    {
      "id": "ps-locked-sub-initial-uuid",
      "type": "Pseudostate",
      "kind": "initial",
      "parentId": "region-locked-sub-uuid",
      "description": "原文：此子区域包含一个初始伪状态。简化：内部安全检查子区域的起始点。"
    },
    {
      "id": "state-selfcheck-uuid",
      "type": "State",
      "name": "自检",
      "parentId": "region-locked-sub-uuid",
      "description": "原文：此子区域包含一个初始伪状态，转换到一个"自检"状态。简化：锁定状态内部的安全自检状态，用于验证系统完整性。"
    },
    {
      "id": "ps-locked-sub-final-uuid",
      "type": "Pseudostate",
      "kind": "final",
      "parentId": "region-locked-sub-uuid",
      "description": "原文：然后转换到一个最终伪状态。简化：内部安全检查子区域的终止点，表示自检完成。"
    },
    {
      "id": "state-unlocking-uuid",
      "type": "State",
      "name": "开锁中",
      "parentId": "region-door-main-uuid",
      "entry": {
        "wrapperActivityId": "wrapper-entry-unlocking-uuid",
        "calledBehaviorId": "act-execute-unlock-bolt-uuid"
      },
      "doActivity": {
        "wrapperActivityId": "wrapper-do-unlocking-uuid",
        "calledBehaviorId": "act-keep-door-open-uuid"
      },
      "exit": {
        "wrapperActivityId": "wrapper-exit-unlocking-uuid",
        "calledBehaviorId": "act-execute-check-closed-uuid"
      },
      "description": "原文：门禁从"锁定"状态转换到"开锁中"状态...进入"开锁中"状态时，会调用"执行解锁门闩"行为。简化：门正在解锁过程中的临时状态，执行解锁和保持打开的动作。"
    },
    {
      "id": "state-alarm-uuid",
      "type": "State",
      "name": "报警",
      "parentId": "region-door-main-uuid",
      "description": "原文：还有一个"报警"状态。如果从"锁定"状态检测到"强制开门事件"，系统会转换到"报警"状态。简化：异常状态，在检测到非法开门尝试时触发警报。"
    },
    {
      "id": "trans-initial-to-locked-uuid",
      "type": "Transition",
      "sourceId": "ps-main-initial-uuid",
      "targetId": "state-locked-uuid",
      "parentId": "region-door-main-uuid",
      "description": "原文：系统启动后，首先进入"锁定"状态。简化：系统初始化后自动进入锁定状态的转换。"
    },
    {
      "id": "trans-locked-to-unlocking-uuid",
      "type": "Transition",
      "sourceId": "state-locked-uuid",
      "targetId": "state-unlocking-uuid",
      "parentId": "region-door-main-uuid",
      "triggerIds": ["event-valid-unlock-sig-event-uuid"],
      "guard": {
        "expression": "安全系统已解除 == true",
        "language": "English"
      },
      "effect": {
        "wrapperActivityId": "wrapper-effect-t2-uuid",
        "calledBehaviorId": "act-log-unlock-attempt-uuid"
      },
      "description": "原文：当接收到"有效开锁信号"时，如果"安全系统已解除"，门禁从"锁定"状态转换到"开锁中"状态。简化：在接收到授权信号且满足安全条件时，从锁定转换到开锁的过程。"
    },
    {
      "id": "trans-unlocking-to-locked-uuid",
      "type": "Transition",
      "sourceId": "state-unlocking-uuid",
      "targetId": "state-locked-uuid",
      "parentId": "region-door-main-uuid",
      "triggerIds": ["event-timeout-uuid"],
      "effect": {
        "wrapperActivityId": "wrapper-effect-t3-uuid",
        "calledBehaviorId": "act-execute-auto-lock-uuid"
      },
      "description": "原文：一段时间后（触发"超时事件"），系统从"开锁中"状态自动转换回"锁定"状态。简化：超时后自动重新锁定的安全机制转换。"
    },
    {
      "id": "trans-locked-to-alarm-uuid",
      "type": "Transition",
      "sourceId": "state-locked-uuid",
      "targetId": "state-alarm-uuid",
      "parentId": "region-door-main-uuid",
      "triggerIds": ["event-forced-open-event-uuid"],
      "effect": {
        "wrapperActivityId": "wrapper-effect-t4-uuid",
        "calledBehaviorId": "act-execute-sound-alarm-uuid"
      },
      "description": "原文：如果从"锁定"状态检测到"强制开门事件"，系统会转换到"报警"状态。简化：检测到非法开门尝试时触发警报的转换。"
    },
    {
      "id": "trans-subinitial-to-selfcheck-uuid",
      "type": "Transition",
      "sourceId": "ps-locked-sub-initial-uuid",
      "targetId": "state-selfcheck-uuid",
      "parentId": "region-locked-sub-uuid",
      "description": "原文：此子区域包含一个初始伪状态，转换到一个"自检"状态。简化：锁定状态内部自动启动安全自检的转换。"
    },
    {
      "id": "trans-selfcheck-to-subfinal-uuid",
      "type": "Transition",
      "sourceId": "state-selfcheck-uuid",
      "targetId": "ps-locked-sub-final-uuid",
      "parentId": "region-locked-sub-uuid",
      "description": "原文：然后转换到一个最终伪状态。简化：自检完成后结束内部安全检查流程的转换。"
    },
    {
      "id": "sig-valid-unlock-uuid",
      "type": "Signal",
      "name": "有效开锁信号",
      "parentId": "pkg-door-control-uuid",
      "description": "原文："有效开锁信号"是一个信号。简化：表示授权开锁请求的通信信号。"
    },
    {
      "id": "event-valid-unlock-sig-event-uuid",
      "type": "SignalEvent",
      "name": "有效开锁信号事件",
      "signalId": "sig-valid-unlock-uuid",
      "parentId": "pkg-door-control-uuid",
      "description": "原文：当接收到"有效开锁信号"时。简化：接收到有效开锁信号时触发的信号事件。"
    },
    {
      "id": "event-timeout-uuid",
      "type": "TimeEvent",
      "name": "超时事件",
      "parentId": "pkg-door-control-uuid",
      "description": "原文：一段时间后（触发"超时事件"）。简化：开锁状态持续一定时间后自动触发的时间事件。"
    },
    {
      "id": "event-forced-open-event-uuid",
      "type": "Event",
      "name": "强制开门事件",
      "parentId": "pkg-door-control-uuid",
      "description": "原文：如果从"锁定"状态检测到"强制开门事件"。简化：检测到非授权的物理强制开门行为时触发的事件。"
    },
    {
      "id": "act-log-unlock-attempt-uuid",
      "type": "Activity",
      "name": "记录开锁尝试",
      "parentId": "pkg-door-behaviors-uuid",
      "description": "原文：并在转换时执行"记录开锁尝试"这个已定义的行为。简化：记录每次开锁尝试的日志活动，用于审计。"
    },
    {
      "id": "act-execute-unlock-bolt-uuid",
      "type": "Activity",
      "name": "执行解锁门闩",
      "parentId": "pkg-door-behaviors-uuid",
      "description": "原文：进入"开锁中"状态时，会调用"执行解锁门闩"行为。简化：物理解锁门闩的具体操作活动。"
    },
    {
      "id": "act-keep-door-open-uuid",
      "type": "Activity",
      "name": "保持门锁打开",
      "parentId": "pkg-door-behaviors-uuid",
      "description": "原文：在"开锁中"状态，系统会持续调用"保持门锁打开"行为。简化：持续监控并保持门锁处于打开状态的活动。"
    },
    {
      "id": "act-execute-check-closed-uuid",
      "type": "Activity",
      "name": "执行检查门是否已关闭",
      "parentId": "pkg-door-behaviors-uuid",
      "description": "原文：离开"开锁中"状态时，会调用"执行检查门是否已关闭"行为。简化：在退出前验证门是否已正确关闭的检查活动。"
    },
    {
      "id": "act-execute-auto-lock-uuid",
      "type": "Activity",
      "name": "执行自动上锁",
      "parentId": "pkg-door-behaviors-uuid",
      "description": "原文：并执行"执行自动上锁"这个已定义的行为。简化：自动重新锁定门闩的物理操作活动。"
    },
    {
      "id": "act-execute-sound-alarm-uuid",
      "type": "Activity",
      "name": "执行鸣响警报",
      "parentId": "pkg-door-behaviors-uuid",
      "description": "原文：并调用"执行鸣响警报"行为作为效果。简化：触发声光警报的具体执行活动。"
    }
  ]
}
```

## 输出要求
- 请严格按照上述 JSON 结构输出完整的状态机图模型。
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

class StateMachineDiagramOutput(BaseModel):
    model: List[DiagramModel] = Field(description="模型列表")
    elements: List[Dict[str, Any]] = Field(description="元素列表（状态机图元素）")

# ==================== 辅助函数 ====================

def get_state_machine_output_dir() -> str:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    output_dir = os.path.join(project_root, "data", "output", "state_machine_diagrams")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"创建状态机图输出目录: {output_dir}")
    return output_dir

def save_state_machine_diagram(result: Dict[str, Any], task_id: str) -> str:
    try:
        output_dir = get_state_machine_output_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"state_machine_diagram_{task_id}_{timestamp}.json"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ 状态机图已保存到: {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"保存状态机图失败: {e}", exc_info=True)
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
            elif elem_type == "Block":
                elem["description"] = f"块：{elem_name}，定义系统组件（自动生成）"
            elif elem_type == "StateMachine":
                elem["description"] = f"状态机：{elem_name}，描述对象的生命周期（自动生成）"
            elif elem_type == "Region":
                elem["description"] = f"区域：{elem_name}，包含状态和转换（自动生成）"
            elif elem_type == "State":
                elem["description"] = f"状态：{elem_name}（自动生成）"
            elif elem_type == "FinalState":
                elem["description"] = f"最终状态：{elem_name}（自动生成）"
            elif elem_type == "Pseudostate":
                kind = elem.get("kind", "unknown")
                elem["description"] = f"伪状态：{elem_name}，类型={kind}（自动生成）"
            elif elem_type == "Transition":
                source = elem.get("sourceId", "?")
                target = elem.get("targetId", "?")
                elem["description"] = f"转换：从 {source} 到 {target}（自动生成）"
            elif elem_type == "Activity":
                elem["description"] = f"活动：{elem_name}，可被状态或转换调用（自动生成）"
            elif elem_type == "Signal":
                elem["description"] = f"信号：{elem_name}（自动生成）"
            elif elem_type == "SignalEvent":
                elem["description"] = f"信号事件：{elem_name}（自动生成）"
            elif elem_type == "Event":
                elem["description"] = f"事件：{elem_name}（自动生成）"
            else:
                elem["description"] = f"{elem_type} 元素：{elem_name}（自动生成）"
            
            logger.warning(f"⚠️ 自动补充 description: id={elem.get('id','unknown')} type={elem_type}")
    
    return result

# ==================== 主处理函数 ====================

def process_state_machine_task(state: WorkflowState, task_content: str) -> Dict[str, Any]:
    logger.info("🎯 开始处理状态机图任务")
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
        print(f"🧠 阶段1: 状态机图分析与推理")
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
        print(f"📝 阶段2: 生成结构化JSON (状态机图)")
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
            validated = StateMachineDiagramOutput(**result)
            result = validated.dict()
            logger.info("✅ Pydantic 验证通过 (状态机图)")
        except Exception as e:
            logger.warning(f"⚠️ Pydantic 验证失败 (状态机图)，继续使用修复后的JSON: {e}")

        logger.info("✅ 状态机图任务处理完成")
        return {"status": "success", "result": result}

    except Exception as e:
        logger.error(f"❌ 状态机图任务处理失败: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

def state_machine_agent(state: WorkflowState, task_id: str, task_content: str) -> WorkflowState:
    logger.info(f"状态机图Agent开始处理任务 {task_id}")

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
        result = process_state_machine_task(state, task_content)
        if result.get("status") == "success":
            saved_path = save_state_machine_diagram(result["result"], task_id)
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
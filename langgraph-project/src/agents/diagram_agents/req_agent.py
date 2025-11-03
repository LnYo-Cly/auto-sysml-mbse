"""
需求图Agent - 负责基于输入内容创建SysML需求图
"""
import logging
import json
import os
from typing import Dict, Any, List, Optional, Union
from datetime import datetime
from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import JsonOutputParser
from json_repair import repair_json

from graph.workflow_state import WorkflowState, ProcessStatus
from config.settings import settings

logger = logging.getLogger(__name__)


# ==================== Pydantic模型定义 ====================

class RequirementModel(BaseModel):
    """需求模型"""
    id: str = Field(description="模型唯一ID")
    name: str = Field(description="模型名称")


class RequirementPackage(BaseModel):
    """需求包"""
    id: str = Field(description="包唯一ID")
    type: str = Field("Package", description="元素类型")
    name: str = Field(description="包名称")
    description: Optional[str] = Field(default="", description="包的描述信息")


class Requirement(BaseModel):
    """需求元素"""
    id: str = Field(description="需求唯一ID")
    type: str = Field("Requirement", description="元素类型")
    name: str = Field(description="需求名称")
    reqId: str = Field(description="需求文本ID")
    text: str = Field(description="需求描述文本")
    parentId: str = Field(description="父元素ID")
    description: Optional[str] = Field(default="", description="需求的补充描述，包含原文内容和提取的简化内容")


class Block(BaseModel):
    """系统块元素"""
    id: str = Field(description="块唯一ID")
    type: str = Field("Block", description="元素类型")
    name: str = Field(description="块名称")
    parentId: str = Field(description="父元素ID")
    description: str = Field(description="块的描述信息，包含原文内容和提取的简化内容")


class TestCase(BaseModel):
    """测试用例元素"""
    id: str = Field(description="测试用例唯一ID")
    type: str = Field("TestCase", description="元素类型")
    name: str = Field(description="测试用例名称")
    parentId: str = Field(description="父元素ID")
    description: str = Field(description="测试用例的描述信息，包含测试目的、测试内容等")


class DeriveReqtRelationship(BaseModel):
    """派生需求关系"""
    id: str = Field(description="关系唯一ID")
    type: str = Field("DeriveReqt", description="关系类型")
    sourceRequirementId: str = Field(description="源需求ID（通用需求）")
    derivedRequirementId: str = Field(description="派生需求ID（具体需求）")
    parentId: str = Field(description="父元素ID")
    description: Optional[str] = Field(default="", description="派生关系的描述，说明为何派生")


class SatisfyRelationship(BaseModel):
    """满足关系"""
    id: str = Field(description="关系唯一ID")
    type: str = Field("Satisfy", description="关系类型")
    blockId: str = Field(description="块ID")
    requirementId: str = Field(description="需求ID")
    parentId: str = Field(description="父元素ID")
    description: Optional[str] = Field(default="", description="满足关系的描述，说明如何满足")


class VerifyRelationship(BaseModel):
    """验证关系"""
    id: str = Field(description="关系唯一ID")
    type: str = Field("Verify", description="关系类型")
    testCaseId: str = Field(description="测试用例ID")
    requirementId: str = Field(description="需求ID")
    parentId: str = Field(description="父元素ID")
    description: Optional[str] = Field(default="", description="验证关系的描述，说明验证方法")


# 定义Union类型用于elements列表
RequirementElement = Union[
    RequirementPackage,
    Requirement,
    Block,
    TestCase,
    DeriveReqtRelationship,
    SatisfyRelationship,
    VerifyRelationship
]


class RequirementDiagramOutput(BaseModel):
    """需求图完整输出"""
    model: List[RequirementModel] = Field(description="模型列表")
    elements: List[RequirementElement] = Field(description="元素列表（包括Package、Requirement、Block、TestCase、关系）")


# ==================== Prompt模板 ====================

# 第一阶段：CoT推理
PROMPT_COT_SYSTEM = """
## 角色
你是一位专业的 SysML 需求图建模专家。你精通 SysML 需求图的规范，能够准确地从自然语言描述中提取出包、需求（及其ID和文本）、系统模块（Block）、测试用例（TestCase）以及它们之间的关系（如 DeriveReqt, Satisfy, Verify）。

## 规则
你的目标是根据输入的文本描述，分析并生成构建 SysML 需求图所需的元素信息。请遵循以下步骤进行思考和分析，并生成中间的思考过程：

1.  **识别模型和包 (Model & Package)**:
    *   确定文本描述的顶层模型名称。
    *   识别主要的包 (Package) 及其名称，所有其他元素通常属于某个包。
    *   为每个识别的元素分配合理的名称和临时ID（最终JSON中ID需全局唯一，可使用描述性名称加后缀，如 `-uuid`）。

2.  **识别需求 (Requirements)**:
    *   找出文本中明确定义的需求。
    *   为每个需求提取其用户指定的 `ID` (如 "REQ-001", "1")，`名称` (name)，和 `文本描述` (text)。
    *   分配一个临时的唯一系统 ID (e.g., `req-capacity-spec-uuid`).
    *   为每个需求提取 `description` 信息：
        - 从原文中摘录最相关的1-2句话
        - 用一句话总结需求的目的和价值
        - 如果有约束条件或背景信息，简要说明
        - 格式：`"原文：[摘录]。简化：[总结]。背景：[可选]"`

3.  **识别系统模块/区块 (Blocks)**:
    *   找出文本中描述的用于满足需求的系统组成部分、模块或区块。这些是 `Block` 元素。
    *   为每个 Block 提取其 `名称`。
    *   分配一个临时的唯一系统 ID (e.g., `blk-car-system-uuid`).
    *   为每个Block提取 `description` 信息：
        - 模块的职责和功能
        - 主要组成部分或子系统
        - 关键技术或实现方式
        - 格式：`"原文：[摘录]。职责：[功能描述]。组成：[可选]"`

4.  **识别测试用例 (TestCases)**:
    *   找出文本中描述的用于验证需求的测试活动或测试用例。这些是 `TestCase` 元素。
    *   为每个 TestCase 提取其 `名称`。
    *   分配一个临时的唯一系统 ID (e.g., `tc-capacity-test-uuid`).
    *   为每个TestCase提取 `description` 信息：
        - 测试目的：验证什么需求或功能
        - 测试方法：如何进行测试
        - 预期结果：期望的测试结果
        - 格式：`"原文：[摘录]。测试目的：[目的]。测试方法：[方法]。预期结果：[结果]"`

5.  **识别派生关系 (DeriveReqt Relationships)**:
    *   注意描述需求之间层级或细化关系的词语，如“派生自”、“分解为”、“细化自”。
    *   对于每个派生关系，明确哪个是更通用的“源需求”（Supplier in SysML Abstraction context）和哪个是更具体的“派生需求”（Client in SysML Abstraction context）。根据用户定义：“总需求下的更为详细的需求”，源需求是总需求，派生需求是详细需求。
    *   记录源需求和派生需求的临时ID。
    *   分配一个临时的唯一系统 ID 给这个关系 (e.g., `rel-derive-1-uuid`).
    *   为关系提取 `description` 信息：
        - 说明派生的原因和逻辑
        - 细化的具体方面
        - 格式：`"原文：[摘录]。该需求是从[源需求]派生，细化了[方面]，原因是[理由]"`

6.  **识别满足关系 (Satisfy Relationships)**:
    *   注意描述模块如何满足需求的词语，如“满足”、“实现”、“负责”。
    *   对于每个满足关系，明确哪个“系统模块 (Block)”（Client）满足了哪个“需求”（Supplier）。
    *   记录相关的 Block 和 Requirement 的临时ID。
    *   分配一个临时的唯一系统 ID 给这个关系 (e.g., `rel-satisfy-1-uuid`).
    *   为关系提取 `description` 信息：
        - 模块如何满足需求
        - 采用的技术或方法
        - 格式：`"原文：[摘录]。[模块名]通过[方法/技术]满足[需求名]，实现了[功能]"`

7.  **识别验证关系 (Verify Relationships)**:
    *   注意描述测试用例如何验证需求的词语，如“验证”、“测试”、“确保”。
    *   对于每个验证关系，明确哪个“测试用例 (TestCase)”（Client）验证了哪个“需求”（Supplier）。
    *   记录相关的 TestCase 和 Requirement 的临时ID。
    *   分配一个临时的唯一系统 ID 给这个关系 (e.g., `rel-verify-1-uuid`).
    *   为关系提取 `description` 信息：
        - 验证的具体内容
        - 验证方法和手段
        - 格式：`"原文：[摘录]。通过[测试方法]验证[需求名]，确保[验证点]满足要求"`

8.  **编译和整理输出**:
    *   汇总所有识别出的元素（模型、包、需求、模块、测试用例）及其属性。
    *   汇总所有识别出的关系及其源和目标。
    *   准备一个清晰的、结构化的中间表示（“整理优化输出”），概述提取到的所有信息，为最终生成JSON做准备。确保所有临时ID都是唯一的。


## 样例

### 输入样例：
"请描述“项目Alpha”的需求模型。
该模型包含一个名为“核心功能”的包。
在“核心功能”包中，定义了以下需求：
1.  一个顶层需求，ID为“R1”，名为“用户认证”，其内容为“系统必须提供用户注册和登录功能”。
2.  一个细化需求，ID为“R1.1”，名为“密码安全”，其内容为“用户密码必须经过加密存储，并符合复杂性要求”。此需求是从“用户认证”派生出来的。
一个名为“认证服务”的模块（Block），用于满足“用户认证”需求。
一个名为“登录功能测试”的测试用例（TestCase），用于验证“用户认证”需求。"

### 输出文本 (CoT):
请你按照如下的8步进行思考推理并输出：

#### 第一步：识别模型和包
- 模型名称: "项目Alpha需求模型" (model-alpha-req-uuid)
- 主要包: "核心功能" (pkg-corefunc-uuid)
  - 描述: "该包包含系统核心的用户认证相关功能和模块"

#### 第二步：识别需求 (Requirements)
- 需求1:
    - 用户指定 ID(文本需求对应ID): "R1"
    - 名称: "用户认证"
    - 文本描述: "系统必须提供用户注册和登录功能"
    - 临时系统 ID: req-userauth-uuid
    - 描述: "原文：系统必须提供用户注册和登录功能。简化：该需求要求系统具备完整的用户身份认证能力，包括新用户注册流程和已有用户的登录验证，是系统安全的基础。"
    
- 需求2:
    - 用户指定 ID(文本需求对应ID): "R1.1"
    - 名称: "密码安全"
    - 文本描述: "用户密码必须经过加密存储，并符合复杂性要求"
    - 临时系统 ID: req-passsec-uuid
    - 描述: "原文：用户密码必须经过加密存储，并符合复杂性要求。简化：该需求细化了用户认证中的密码安全方面，要求采用加密算法保护密码，并设定密码强度规则，以防止暴力破解和数据泄露。背景：这是为了满足GDPR等数据保护法规的要求。"

#### 第三步：识别系统模块/区块 (Blocks)
- 模块1:
    - 名称: "认证服务"
    - 临时系统 ID: blk-authsvc-uuid
    - 描述: "原文：认证服务模块用于满足用户认证需求。职责：负责处理用户的注册和登录请求，进行身份验证。组成：包含用户管理子模块、密码加密模块、会话管理模块。技术：采用JWT令牌和bcrypt加密算法实现安全认证。"

#### 第四步：识别测试用例 (TestCases)
- 测试用例1:
    - 名称: "登录功能测试"
    - 临时系统 ID: tc-logintest-uuid
    - 描述: "原文：登录功能测试用于验证用户认证需求。测试目的：验证用户登录功能的正确性和安全性。测试方法：使用有效和无效的用户名密码组合进行登录尝试，检查响应结果和会话状态。预期结果：有效凭据可成功登录并获得访问令牌，无效凭据被拒绝并返回明确的错误信息。"

#### 第五步：识别派生关系 (DeriveReqt Relationships)
- 派生关系1:
    - 描述: "密码安全" (req-passsec-uuid) 是从 "用户认证" (req-userauth-uuid) 派生出来的。
    - 源需求 (General/Supplier): "用户认证" (req-userauth-uuid)
    - 派生需求 (Specific/Client): "密码安全" (req-passsec-uuid)
    - 临时系统 ID: rel-derive-auth-passsec-uuid
    - 关系描述: "原文：密码安全需求是从用户认证派生出来的。该需求是从用户认证需求派生，细化了密码存储和验证的安全性方面，原因是密码安全是身份认证的关键组成部分，直接影响系统整体安全性。用户认证的核心在于验证用户身份，而密码安全则是保证这种验证机制不被攻破的基础。"

#### 第六步：识别满足关系 (Satisfy Relationships)
- 满足关系1:
    - 描述: "认证服务" (blk-authsvc-uuid) 满足 "用户认证" (req-userauth-uuid)。
    - 系统模块 (Client): "认证服务" (blk-authsvc-uuid)
    - 需求 (Supplier): "用户认证" (req-userauth-uuid)
    - 临时系统 ID: rel-satisfy-authsvc-userauth-uuid
    - 关系描述: "原文：认证服务模块用于满足用户认证需求。认证服务模块通过实现用户注册API、登录验证逻辑和会话管理功能来满足用户认证需求，采用RESTful接口和JWT令牌技术，实现了安全可靠的身份认证流程。该模块提供了完整的用户生命周期管理，从注册、登录到会话维护。"

#### 第七步：识别验证关系 (Verify Relationships)
- 验证关系1:
    - 描述: "登录功能测试" (tc-logintest-uuid) 验证 "用户认证" (req-userauth-uuid)。
    - 测试用例 (Client): "登录功能测试" (tc-logintest-uuid)
    - 需求 (Supplier): "用户认证" (req-userauth-uuid)
    - 临时系统 ID: rel-verify-logintest-userauth-uuid
    - 关系描述: "原文：登录功能测试用于验证用户认证需求。通过黑盒测试和边界值分析方法验证用户认证需求，测试覆盖正常登录场景、错误密码场景、不存在用户场景、空值输入场景等，确保认证功能的正确性、安全性和用户体验符合要求。测试还包括性能测试，确保高并发下的稳定性。"

#### 第八步：整理优化输出
---
模型: 项目Alpha需求模型 (model-alpha-req-uuid)
  包: 核心功能 (pkg-corefunc-uuid)
    描述: "包含系统核心的用户认证相关功能和模块"
    
    需求:
      - ID: R1, 名称: 用户认证, 文本: 系统必须提供用户注册和登录功能 (sysId: req-userauth-uuid)
        描述: "原文：系统必须提供用户注册和登录功能。简化：该需求要求系统具备完整的用户身份认证能力，包括新用户注册流程和已有用户的登录验证，是系统安全的基础。"
        
      - ID: R1.1, 名称: 密码安全, 文本: 用户密码必须经过加密存储，并符合复杂性要求 (sysId: req-passsec-uuid)
        描述: "原文：用户密码必须经过加密存储，并符合复杂性要求。简化：该需求细化了用户认证中的密码安全方面，要求采用加密算法保护密码，并设定密码强度规则，以防止暴力破解和数据泄露。背景：这是为了满足GDPR等数据保护法规的要求。"
    
    系统模块 (Blocks):
      - 名称: 认证服务 (sysId: blk-authsvc-uuid)
        描述: "原文：认证服务模块用于满足用户认证需求。职责：负责处理用户的注册和登录请求，进行身份验证。组成：包含用户管理子模块、密码加密模块、会话管理模块。技术：采用JWT令牌和bcrypt加密算法实现安全认证。"
    
    测试用例 (TestCases):
      - 名称: 登录功能测试 (sysId: tc-logintest-uuid)
        描述: "原文：登录功能测试用于验证用户认证需求。测试目的：验证用户登录功能的正确性和安全性。测试方法：使用有效和无效的用户名密码组合进行登录尝试，检查响应结果和会话状态。预期结果：有效凭据可成功登录并获得访问令牌，无效凭据被拒绝并返回明确的错误信息。"
    
    关系:
      - DeriveReqt (sysId: rel-derive-auth-passsec-uuid):
        - 源需求: req-userauth-uuid (用户认证)
        - 派生需求: req-passsec-uuid (密码安全)
        - 描述: "原文：密码安全需求是从用户认证派生出来的。该需求是从用户认证需求派生，细化了密码存储和验证的安全性方面，原因是密码安全是身份认证的关键组成部分，直接影响系统整体安全性。用户认证的核心在于验证用户身份，而密码安全则是保证这种验证机制不被攻破的基础。"
        
      - Satisfy (sysId: rel-satisfy-authsvc-userauth-uuid):
        - 系统模块: blk-authsvc-uuid (认证服务)
        - 需求: req-userauth-uuid (用户认证)
        - 描述: "原文：认证服务模块用于满足用户认证需求。认证服务模块通过实现用户注册API、登录验证逻辑和会话管理功能来满足用户认证需求，采用RESTful接口和JWT令牌技术，实现了安全可靠的身份认证流程。该模块提供了完整的用户生命周期管理，从注册、登录到会话维护。"
        
      - Verify (sysId: rel-verify-logintest-userauth-uuid):
        - 测试用例: tc-logintest-uuid (登录功能测试)
        - 需求: req-userauth-uuid (用户认证)
        - 描述: "原文：登录功能测试用于验证用户认证需求。通过黑盒测试和边界值分析方法验证用户认证需求，测试覆盖正常登录场景、错误密码场景、不存在用户场景、空值输入场景等，确保认证功能的正确性、安全性和用户体验符合要求。测试还包括性能测试，确保高并发下的稳定性。"
---

"""

PROMPT_COT_USER = """
## 具体任务
输入：
{task_content}

输出：请你一步一步进行推理思考，按照8个步骤输出你的分析过程。
"""

# 第二阶段：JSON生成
PROMPT_JSON_SYSTEM = """
根据以上详细的推理和"整理优化输出"，请严格按照以下 JSON 格式生成 SysML 需求图的完整描述。

## 重要说明
1. 所有 `id` 字段都必须全局唯一
2. `parentId` 正确反映元素的包含关系
3. **每个元素都必须包含 `description` 字段**，用于存储：
   - 原文中相关的详细描述
   - 提取和简化后的关键信息
   - 上下文信息和补充说明
4. 对于关系类型，`description` 应说明关系建立的原因和方式

## JSON格式示例

```json
{{
  "model": [
    {{
      "id": "model-req-unique-id",
      "name": "RequirementsModelName"
    }}
  ],
  "elements": [
    // Packages - 包
    {{
      "id": "pkg-req-unique-id",
      "type": "Package",
      "name": "PackageName",
      "description": "包的用途和范围说明"
    }},
    
    // Requirements - 需求
    {{
      "id": "req-unique-id-1",
      "type": "Requirement",
      "name": "RequirementName",
      "reqId": "REQ-001",
      "text": "需求的正式文本描述",
      "parentId": "pkg-req-unique-id",
      "description": "原文：[原始文本摘录]。简化：该需求要求系统具备...功能，用于...目的"
    }},
    
    // Blocks - 系统模块
    {{
      "id": "blk-unique-id-1",
      "type": "Block",
      "name": "BlockName",
      "parentId": "pkg-req-unique-id",
      "description": "原文：[模块相关描述]。该模块负责...，包含...组件，实现...功能"
    }},
    
    // TestCases - 测试用例
    {{
      "id": "tc-unique-id-1",
      "type": "TestCase",
      "name": "TestCaseName",
      "parentId": "pkg-req-unique-id",
      "description": "原文：[原始文本摘录]。测试目的：验证...。测试方法：通过...方式进行测试。预期结果：..."
    }},
    
    // DeriveReqt - 派生关系
    {{
      "id": "rel-derive-unique-id-1",
      "type": "DeriveReqt",
      "sourceRequirementId": "req-general-id",
      "derivedRequirementId": "req-specific-id",
      "parentId": "pkg-req-unique-id",
      "description": "原文：[原始文本摘录]。该具体需求是从总需求中派生，细化了...方面的要求"
    }},
    
    // Satisfy - 满足关系
    {{
      "id": "rel-satisfy-unique-id-1",
      "type": "Satisfy",
      "blockId": "blk-unique-id-1",
      "requirementId": "req-unique-id-1",
      "parentId": "pkg-req-unique-id",
      "description": "原文：[原始文本摘录]。该模块通过...方式满足需求，实现了...功能"
    }},
    
    // Verify - 验证关系
    {{
      "id": "rel-verify-unique-id-1",
      "type": "Verify",
      "testCaseId": "tc-unique-id-1",
      "requirementId": "req-unique-id-1",
      "parentId": "pkg-req-unique-id",
      "description": "原文：[原始文本摘录]。通过...测试方法验证需求，确保...条件满足"
    }}
  ]
}}
```

## Description字段编写指南

1. **Package**: 简要说明包的用途和包含的内容范围
2. **Requirement**: 
   - 引用原文中的关键描述
   - 用简洁语言总结需求的核心内容
   - 说明需求的目的和约束
3. **Block**: 
   - 引用原文中对该模块的描述
   - 说明模块的职责、功能和组成
   - 解释模块如何实现相关功能
4. **TestCase**: 
   - 测试的目的
   - 测试的方法和步骤
   - 预期的结果
5. **关系类型**: 
   - 说明建立该关系的原因
   - 描述关系的具体实现方式
   - 补充相关的上下文信息

请严格按照上述格式生成JSON，确保每个元素都有详细的description字段。

{format_instructions}
"""


# ==================== 辅助函数 ====================

def get_requirement_output_dir() -> str:
    """获取需求图输出目录"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    output_dir = os.path.join(project_root, "data", "output", "requirement_diagrams")
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        logger.info(f"创建需求图输出目录: {output_dir}")
    
    return output_dir


def save_requirement_diagram(result: Dict[str, Any], task_id: str) -> str:
    """
    保存需求图JSON
    
    参数:
        result: 需求图结果
        task_id: 任务ID
        
    返回:
        保存的文件路径
    """
    try:
        output_dir = get_requirement_output_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"requirement_diagram_{task_id}_{timestamp}.json"
        filepath = os.path.join(output_dir, filename)
        
        # 保存JSON
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✅ 需求图已保存到: {filepath}")
        
        # 打印统计信息
        print(f"\n{'='*80}")
        print(f"📊 需求图生成完成")
        print(f"{'='*80}")
        print(f"文件路径: {filepath}")
        
        if 'model' in result:
            print(f"模型数量: {len(result['model'])}")
        
        if 'elements' in result:
            elements = result['elements']
            element_types = {}
            for elem in elements:
                elem_type = elem.get('type', 'Unknown')
                element_types[elem_type] = element_types.get(elem_type, 0) + 1
            
            print(f"元素总数: {len(elements)}")
            print("\n元素类型统计:")
            for elem_type, count in sorted(element_types.items()):
                print(f"  📋 {elem_type}: {count} 个")
        
        print(f"{'='*80}\n")
        
        return filepath
        
    except Exception as e:
        logger.error(f"❌ 保存需求图失败: {str(e)}", exc_info=True)
        return ""


def validate_and_fix_json(json_str: str) -> Dict[str, Any]:
    """
    验证并修复JSON
    
    参数:
        json_str: JSON字符串
        
    返回:
        解析后的字典
    """
    try:
        # 清理markdown代码块
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()
        
        # 尝试直接解析
        try:
            result = json.loads(json_str)
            logger.info("✅ JSON格式正确")
            return result
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ JSON解析失败，尝试修复: {e}")
            
            # 使用json_repair修复
            fixed_json = repair_json(json_str)
            result = json.loads(fixed_json)
            logger.info("✅ JSON修复成功")
            return result
            
    except Exception as e:
        logger.error(f"❌ JSON验证失败: {str(e)}")
        raise ValueError(f"无法解析JSON: {str(e)}")


def validate_descriptions(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    验证并补充description字段
    
    参数:
        result: 需求图结果
        
    返回:
        验证后的结果
    """
    try:
        if 'elements' not in result:
            return result
        
        elements = result['elements']
        updated_elements = []
        
        for elem in elements:
            elem_type = elem.get('type', '')
            
            # 确保description字段存在
            if 'description' not in elem or not elem['description']:
                # 根据类型生成默认描述
                if elem_type == 'Package':
                    elem['description'] = f"包：{elem.get('name', '未命名')}"
                elif elem_type == 'Requirement':
                    elem['description'] = f"需求内容：{elem.get('text', '无描述')}"
                elif elem_type == 'Block':
                    elem['description'] = f"系统模块：{elem.get('name', '未命名')}，负责实现相关功能"
                elif elem_type == 'TestCase':
                    elem['description'] = f"测试用例：{elem.get('name', '未命名')}，用于验证需求"
                elif elem_type == 'DeriveReqt':
                    elem['description'] = "需求派生关系"
                elif elem_type == 'Satisfy':
                    elem['description'] = "满足关系：模块实现需求"
                elif elem_type == 'Verify':
                    elem['description'] = "验证关系：测试验证需求"
                else:
                    elem['description'] = f"{elem_type}元素"
                
                logger.warning(f"⚠️ 元素 {elem.get('id', 'unknown')} 缺少description，已自动生成")
            
            updated_elements.append(elem)
        
        result['elements'] = updated_elements
        return result
        
    except Exception as e:
        logger.error(f"❌ 验证description字段失败: {str(e)}")
        return result


# ==================== 主处理函数 ====================

def process_requirement_task(state: WorkflowState, task_content: str) -> Dict[str, Any]:
    """
    处理需求图任务
    
    参数:
        state: 工作流状态
        task_content: 任务内容
        
    返回:
        处理结果
    """
    logger.info("🎯 开始处理需求图任务")
    
    try:
        # 创建LLM
        llm = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            base_url=settings.base_url,
            temperature=0.0,
            streaming=True,
            max_tokens=settings.max_tokens
        )
        
        # ========== 第一阶段：CoT推理 ==========
        print(f"\n{'='*80}")
        print(f"🧠 阶段1: 需求分析与推理")
        print(f"{'='*80}\n")
        
        cot_prompt = ChatPromptTemplate.from_messages([
            ("system", PROMPT_COT_SYSTEM),
            ("human", PROMPT_COT_USER)
        ])
        
        cot_chain = cot_prompt | llm
        
        # 流式输出CoT推理过程
        cot_result = ""
        for chunk in cot_chain.stream({"task_content": task_content}):
            chunk_content = chunk.content
            print(chunk_content, end="", flush=True)
            cot_result += chunk_content
        
        print(f"\n\n{'='*80}")
        print(f"✅ 推理完成")
        print(f"{'='*80}\n")
        
        # ========== 第二阶段：生成JSON ==========
        print(f"{'='*80}")
        print(f"📝 阶段2: 生成结构化JSON")
        print(f"{'='*80}\n")
        
        # 创建JSON解析器
        json_parser = JsonOutputParser(pydantic_object=RequirementDiagramOutput)
        
        json_prompt = ChatPromptTemplate.from_messages([
            ("system", PROMPT_JSON_SYSTEM),
            ("human", "请根据以上推理结果生成JSON。推理内容：\n{cot_result}")
        ])
        
        json_chain = json_prompt | llm
        
        # 流式输出JSON生成过程
        json_result = ""
        for chunk in json_chain.stream({
            "format_instructions": json_parser.get_format_instructions(),
            "cot_result": cot_result
        }):
            chunk_content = chunk.content
            print(chunk_content, end="", flush=True)
            json_result += chunk_content
        
        print(f"\n\n{'='*80}")
        print(f"✅ JSON生成完成")
        print(f"{'='*80}\n")
        
        # 验证和修复JSON
        result = validate_and_fix_json(json_result)
        
        # 验证并补充description字段
        result = validate_descriptions(result)
        
        # 使用Pydantic验证（可选，更严格）
        try:
            validated_result = RequirementDiagramOutput(**result)
            result = validated_result.dict()
            logger.info("✅ Pydantic验证通过")
        except Exception as e:
            logger.warning(f"⚠️ Pydantic验证失败，使用修复后的JSON: {e}")
        
        logger.info("✅ 需求图任务处理完成")
        return {"status": "success", "result": result}
        
    except Exception as e:
        logger.error(f"❌ 需求图任务处理失败: {str(e)}", exc_info=True)
        return {"status": "error", "message": str(e)}


def requirement_agent(state: WorkflowState, task_id: str, task_content: str) -> WorkflowState:
    """
    需求图Agent入口函数
    
    参数:
        state: 当前工作流状态
        task_id: 任务ID
        task_content: 任务内容
        
    返回:
        更新后的工作流状态
    """
    logger.info(f"🎯 需求图Agent开始处理任务 {task_id}")
    
    # 查找任务
    task_index = -1
    for i, task in enumerate(state.assigned_tasks):
        if task.id == task_id:
            task_index = i
            break
    
    if task_index == -1:
        logger.error(f"❌ 找不到任务 {task_id}")
        return state
    
    # 更新任务状态
    state.assigned_tasks[task_index].status = ProcessStatus.PROCESSING
    
    try:
        # 处理需求图任务
        result = process_requirement_task(state, task_content)
        
        if result["status"] == "success":
            # 保存JSON文件
            json_file = save_requirement_diagram(result["result"], task_id)
            
            # 更新任务结果
            state.assigned_tasks[task_index].result = {
                **result["result"],
                "saved_file": json_file
            }
            state.assigned_tasks[task_index].status = ProcessStatus.COMPLETED
            logger.info(f"✅ 任务 {task_id} 处理完成")
        else:
            # 任务失败
            state.assigned_tasks[task_index].status = ProcessStatus.FAILED
            state.assigned_tasks[task_index].error = result["message"]
            logger.error(f"❌ 任务 {task_id} 处理失败: {result['message']}")
    
    except Exception as e:
        # 异常处理
        state.assigned_tasks[task_index].status = ProcessStatus.FAILED
        state.assigned_tasks[task_index].error = str(e)
        logger.error(f"❌ 任务 {task_id} 处理异常: {str(e)}", exc_info=True)
    
    return state
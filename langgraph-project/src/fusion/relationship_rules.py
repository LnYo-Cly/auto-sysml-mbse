# relationship_rules.py

"""
一个集中式的配置文件，用于定义JSON元素字段如何映射到Neo4j图关系。
这是我们模型转换逻辑的核心“字典”，涵盖了BDD, IBD, 活动图, 参数图, 需求图和序列图。
"""

# ==============================================================================
# 类别一：单一引用规则 (Single Reference Rules)
# ==============================================================================
SINGLE_REF_RULES = [
    # --- 核心结构关系 ---
    ('parentId', 'IS_CHILD_OF', 'out'),

    # --- 类型与定义关系 ---
    ('typeId', 'HAS_TYPE', 'out'),
    ('unitId', 'HAS_UNIT', 'out'),
    ('baseType', 'HAS_BASETYPE', 'out'),

    # --- 行为、信号与事件关系 ---
    ('classifierBehaviorId', 'HAS_CLASSIFIER_BEHAVIOR', 'out'),
    ('behavior', 'CALLS_BEHAVIOR', 'out'),
    ('signalId', 'REFERENCES_SIGNAL', 'out'),
    ('signatureId', 'HAS_SIGNATURE', 'out'),       # 新增: Message -> Operation
    ('sendEventId', 'HAS_SEND_EVENT', 'out'),        # 新增: Message -> MessageOccurrenceSpecification
    ('receiveEventId', 'HAS_RECEIVE_EVENT', 'out'),  # 新增: Message -> MessageOccurrenceSpecification
    ('messageId', 'REFERENCES_MESSAGE', 'out'),      # 新增: MessageOccurrenceSpecification -> Message

    # --- 关联、分区、生命线与守护关系 ---
    ('associationId', 'REFERENCES_ASSOCIATION', 'out'),
    ('representsId', 'REPRESENTS', 'out'),
    ('coveredId', 'COVERS_LIFELINE', 'in'),        # 新增: Lifeline <- MessageOccurrenceSpecification
    ('guardId', 'HAS_GUARD', 'out'),               # 新增: InteractionOperand -> InteractionConstraint

    # --- 流程关系 ---
    ('sourceId', 'FLOWS_FROM', 'in'),
    ('targetId', 'FLOWS_TO', 'out'),
    
    # --- 需求追溯关系 ---
    ('sourceRequirementId', 'HAS_SOURCE_REQ', 'out'),
    ('derivedRequirementId', 'HAS_DERIVED_REQ', 'out'),
    ('blockId', 'SATISFIED_BY_BLOCK', 'out'),
    ('requirementId', 'SATISFIES_REQ', 'out'),
    ('testCaseId', 'VERIFIED_BY_TESTCASE', 'out'),
]

# ==============================================================================
# 类别二：列表引用规则 (List Reference Rules)
# ==============================================================================
LIST_REF_RULES = [
    # --- 结构性/分类器包含关系 ---
    ('properties', 'CONTAINS_PROPERTY', 'out'),
    ('ownedAttributeIds', 'OWNS_ATTRIBUTE', 'out'),   # 新增: Actor/Class/Interaction -> Property
    ('ports', 'CONTAINS_PORT', 'out'),
    ('operations', 'CONTAINS_OPERATION', 'out'),
    ('ownedOperationIds', 'OWNS_OPERATION', 'out'),   # 新增: Actor/Class -> Operation
    ('parameterIds', 'HAS_PARAMETER', 'out'),          # 新增: Operation -> Parameter
    ('receptions', 'CONTAINS_RECEPTION', 'out'),
    ('connectors', 'CONTAINS_CONNECTOR', 'out'),
    ('ownedDiagrams', 'OWNS_DIAGRAM', 'out'),
    
    # --- 状态机包含关系 ---
    ('regions', 'CONTAINS_REGION', 'out'),
    ('connectionPoints', 'HAS_CONNECTION_POINT', 'out'),
    ('triggerIds', 'TRIGGERED_BY', 'out'),
    
    # --- 交互包含关系 ---
    ('lifelineIds', 'CONTAINS_LIFELINE', 'out'),     # 新增: Interaction -> Lifeline
    ('messageIds', 'CONTAINS_MESSAGE', 'out'),      # 新增: Interaction -> Message
    ('fragmentIds', 'CONTAINS_FRAGMENT', 'out'),     # 新增: Interaction/Operand -> Fragment
    ('coveredLifelineIds', 'COVERS_LIFELINE', 'out'),# 新增: CombinedFragment -> Lifeline
    ('operandIds', 'CONTAINS_OPERAND', 'out'),      # 新增: CombinedFragment -> InteractionOperand
    
    # --- 活动图包含关系 ---
    ('nodes', 'CONTAINS_NODE', 'out'),
    ('edges', 'CONTAINS_EDGE', 'out'),
    ('groups', 'CONTAINS_GROUP', 'out'),

    # --- 活动分区内容 ---
    ('nodeIds', 'PARTITIONS_NODE', 'out'),

    # --- 关联关系 ---
    ('memberEndIds', 'HAS_MEMBER_END', 'out'),
]

# ==============================================================================
# 类别三：复杂引用规则 (Complex Reference Rules)
# ==============================================================================
COMPLEX_REF_RULES = ['end1', 'end2']
CONNECTOR_END_REF_FIELDS = ['partRefId', 'portRefId', 'propertyRefId']

# ==============================================================================
# 类别四：嵌套行为调用规则 (Nested Behavior Call Rules)
# ==============================================================================
NESTED_BEHAVIOR_RULES = [
    ('entry', 'ON_ENTRY_CALLS'),
    ('doActivity', 'DO_ACTIVITY_CALLS'),
    ('exit', 'ON_EXIT_CALLS'),
    ('effect', 'EFFECT_CALLS'),
]
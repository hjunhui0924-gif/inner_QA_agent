"""Expand and normalize the reproducible enterprise knowledge seed corpus.

The added policies are synthetic test documents drafted from public agency and
law portals. They are deliberately marked as ``synthetic_seed`` and must not
be presented as a customer's real internal policies. The script is idempotent:
it replaces records and evaluation cases with the same IDs, while preserving
the original corpus and questions.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend.knowledge.schema import (
    canonical_content,
    content_checksum,
    normalize_knowledge_record,
    validate_knowledge_records,
)
from backend.evaluation.schema import (
    parse_eval_cases,
    validate_cases_against_corpus,
    validate_dataset_shape,
)


KB_PATH = BASE_DIR / "data" / "knowledge_base.json"
EVAL_PATH = BASE_DIR / "data" / "evals" / "enterprise_rag_eval.json"


def _doc(
    doc_id: str,
    title: str,
    department: str,
    family: str,
    owner: str,
    content: str,
    reference_title: str,
    reference_url: str,
    qas: list[tuple[str, str, list[str], list[str]]],
    *,
    version: str = "v1",
) -> dict[str, Any]:
    return {
        "id": doc_id,
        "title": title,
        "content": canonical_content(content.strip()),
        "source": "synthetic_seed",
        "source_type": "synthetic_seed",
        "department": department,
        "version": version,
        "status": "active",
        "effective_from": "2026-01-01",
        "effective_to": None,
        "owner": owner,
        "access_scope": "internal",
        "original_filename": f"{doc_id}.md",
        "document_family": family,
        "reference_type": "public_reference_synthesis",
        "reference_title": reference_title,
        "reference_url": reference_url,
        "source_note": "公开法规或机构指南检索后整理的合成测试制度，不代表真实企业制度。",
        "keywords": [department, family],
        "_qas": qas,
    }


PUBLIC_LABOR = "https://www.mohrss.gov.cn/"
PUBLIC_TAX = "https://www.chinatax.gov.cn/"
PUBLIC_GOV = "https://www.gov.cn/"
PUBLIC_WORK_SAFETY = "https://www.gov.cn/xinwen/2021-06/11/content_5616910.htm"
PUBLIC_DATA_LAW = "http://www.npc.gov.cn/npc/c2/c30834/202106/t20210610_311888.html"
PUBLIC_PIPL = "http://www.npc.gov.cn/npc/c2/c30834/202108/t20210820_313088.html"
PUBLIC_CAC = "https://www.cac.gov.cn/2023-07/13/c_1690898327029107.htm"
PUBLIC_NETWORK_DATA = "https://www.gov.cn/zhengce/content/202409/content_6977766.htm"
PUBLIC_SAMR = "https://www.samr.gov.cn/"


NEW_DOCUMENTS: list[dict[str, Any]] = [
    _doc(
        "hr-recruitment-v1", "招聘需求与录用流程", "HR", "hr-recruitment", "人力资源部",
        """一、招聘需求。部门负责人提交招聘需求，人力资源部复核编制、岗位职责和预算后发布职位。
二、面试记录。面试官应在面试结束后填写面试评分表，记录关键结论和待核实事项；人力资源部统一归档面试记录。
三、录用通知。录用通知应写明岗位、薪资结构、入职日期和材料清单，候选人确认后方可进入入职流程。
四、背景核验。涉及生产、财务或高权限系统的岗位，在发出最终录用确认前应完成必要的背景核验并记录结果。""",
        "人力资源和社会保障部公开劳动用工政策", PUBLIC_LABOR,
        [
            ("招聘需求由谁提交、谁复核？", "direct_fact", ["部门负责人提交招聘需求", "人力资源部复核编制、岗位职责和预算"], ["direct", "approval_chain"]),
            ("面试结束后要留存什么记录？", "conditional_policy", ["面试官应在面试结束后填写面试评分表", "人力资源部统一归档面试记录"], ["record", "process"]),
            ("录用通知至少写明哪些内容？", "multi_step", ["录用通知应写明岗位、薪资结构、入职日期和材料清单"], ["checklist", "onboarding"]),
            ("哪些岗位需要在最终录用前做背景核验？", "conditional_policy", ["涉及生产、财务或高权限系统的岗位", "发出最终录用确认前应完成必要的背景核验"], ["condition", "risk"]),
            ("请概括从提出招聘到录用确认的关键顺序。", "multi_step", ["部门负责人提交招聘需求", "人力资源部复核编制、岗位职责和预算后发布职位", "候选人确认后方可进入入职流程"], ["sequence", "workflow"]),
            ("本制度是否规定了校招笔试分数线？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "hr-offboarding-v1", "离职交接与权限回收", "HR", "hr-offboarding", "人力资源部",
        """一、离职通知。员工按劳动合同或公司流程提交离职申请，人力资源部确认最后工作日并通知直属主管。
二、工作交接。直属主管应确认工作、客户、文件和未结事项交接清单，交接双方签字后归档。
三、资产归还。员工应在最后工作日前归还电脑、门禁卡、钥匙和其他公司资产；遗失情况须单独登记。
四、权限回收。IT 应在最后工作日结束前关闭邮箱、协作平台、代码仓库和生产系统权限。
五、离职结算。人力资源部在交接和资产归还完成后发起离职证明及薪资结算流程。""",
        "人力资源和社会保障部公开劳动用工政策", PUBLIC_LABOR,
        [
            ("离职交接清单由谁确认？", "direct_fact", ["直属主管应确认工作、客户、文件和未结事项交接清单"], ["handover", "owner"]),
            ("员工最后工作日前要归还哪些资产？", "multi_step", ["员工应在最后工作日前归还电脑、门禁卡、钥匙和其他公司资产"], ["asset", "offboarding"]),
            ("IT什么时候关闭离职员工权限？", "threshold", ["IT 应在最后工作日结束前关闭邮箱、协作平台、代码仓库和生产系统权限"], ["deadline", "access"]),
            ("什么条件满足后才能发起离职结算？", "conditional_policy", ["人力资源部在交接和资产归还完成后发起离职证明及薪资结算流程"], ["condition", "settlement"]),
            ("请按顺序说明离职处理的主要步骤。", "multi_step", ["提交离职申请", "确认最后工作日", "确认工作、客户、文件和未结事项交接清单", "关闭邮箱、协作平台、代码仓库和生产系统权限"], ["sequence", "workflow"]),
            ("本制度是否规定离职补偿金的具体计算公式？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "hr-overtime-v1", "加班与调休申请", "HR", "hr-overtime", "人力资源部",
        """一、事前申请。加班前应在 HR 系统提交加班申请，写明事由、预计时长和工作地点，并由直属主管审批。
二、紧急加班。因线上故障或突发事件无法事前申请的，应在加班结束后一个工作日内补录并说明原因。
三、工时记录。员工应按实际开始和结束时间填写工时记录，不得用预估时长替代实际记录。
四、调休安排。已批准的加班可按公司规则申请调休，调休申请仍需直属主管审批。
五、月度截止。每月最后一个工作日为当月加班记录和调休申请的提交截止时间。""",
        "人力资源和社会保障部公开工时政策", PUBLIC_LABOR,
        [
            ("加班申请要在什么时候提交？", "threshold", ["加班前应在 HR 系统提交加班申请"], ["deadline", "approval"]),
            ("紧急加班无法事前申请怎么办？", "conditional_policy", ["应在加班结束后一个工作日内补录并说明原因"], ["exception", "deadline"]),
            ("工时记录应填写什么时间？", "direct_fact", ["员工应按实际开始和结束时间填写工时记录"], ["record", "accuracy"]),
            ("调休申请由谁审批？", "direct_fact", ["调休申请仍需直属主管审批"], ["approval", "leave"]),
            ("每月加班记录的提交截止时间是什么？", "threshold", ["每月最后一个工作日为当月加班记录和调休申请的提交截止时间"], ["deadline", "monthly"]),
            ("本制度规定的法定节假日加班工资倍数是多少？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "hr-training-v1", "培训与岗位资质管理", "HR", "hr-training", "人力资源部",
        """一、培训计划。部门负责人每年提交岗位培训需求，人力资源部汇总形成年度培训计划。
二、必修培训。信息安全、职业健康和合规培训属于必修项目，员工应在规定期限内完成并通过确认。
三、外部培训。外部培训费用须在报名前取得部门负责人和人力资源部审批；涉及证书的培训应登记证书有效期。
四、培训记录。培训签到、成绩、证书和费用凭证由人力资源部归档，员工可申请查阅本人记录。
五、资质到期。岗位资质到期前六十天，人力资源部应提醒员工和直属主管安排复训或续证。""",
        "人力资源和社会保障部职业培训公开指南", PUBLIC_LABOR,
        [
            ("年度培训计划由谁形成？", "direct_fact", ["人力资源部汇总形成年度培训计划"], ["plan", "owner"]),
            ("哪些培训属于必修项目？", "multi_step", ["信息安全、职业健康和合规培训属于必修项目"], ["mandatory", "compliance"]),
            ("外部培训费用什么时候需要审批？", "threshold", ["外部培训费用须在报名前取得部门负责人和人力资源部审批"], ["approval", "deadline"]),
            ("培训记录包括哪些材料？", "multi_step", ["培训签到、成绩、证书和费用凭证由人力资源部归档"], ["record", "evidence"]),
            ("岗位资质到期前多久提醒复训？", "threshold", ["岗位资质到期前六十天，人力资源部应提醒员工和直属主管安排复训或续证"], ["deadline", "certificate"]),
            ("本制度是否规定员工晋升的最低绩效等级？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "finance-cash-advance-v1", "备用金与员工借款", "Finance", "finance-cash-advance", "财务部",
        """一、借款申请。员工申请备用金或业务借款时，应填写用途、金额、预计使用日期和归还日期。
二、审批权限。单笔借款不超过一万元由直属主管和财务复核；超过一万元还需部门负责人审批。
三、专款专用。借款只能用于审批通过的业务，不得转借、拆分或用于个人消费。
四、核销时限。业务完成后十个工作日内，借款人应凭合法票据办理报销或退回余额。
五、逾期处理。逾期未核销的借款会被列入月度催收清单，财务可暂停同一员工新的借款申请。""",
        "国家税务总局公开发票与企业财务管理政策", PUBLIC_TAX,
        [
            ("申请备用金需要填写哪些信息？", "multi_step", ["应填写用途、金额、预计使用日期和归还日期"], ["checklist", "finance"]),
            ("超过一万元的借款还需要谁审批？", "threshold", ["超过一万元还需部门负责人审批"], ["threshold", "approval"]),
            ("备用金可以用于个人消费吗？", "negative_rule", ["借款只能用于审批通过的业务", "不得转借、拆分或用于个人消费"], ["negative", "compliance"]),
            ("业务完成后多久要核销借款？", "threshold", ["业务完成后十个工作日内，借款人应凭合法票据办理报销或退回余额"], ["deadline", "reimbursement"]),
            ("逾期未核销会有什么处理？", "conditional_policy", ["逾期未核销的借款会被列入月度催收清单", "财务可暂停同一员工新的借款申请"], ["exception", "control"]),
            ("本制度是否规定员工借款的年利率？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "finance-budget-control-v1", "预算申请与调整控制", "Finance", "finance-budget", "财务部",
        """一、预算编制。部门负责人在年度预算窗口提交下一年度预算，说明目标、测算依据和主要费用项目。
二、预算审批。财务部复核口径和资金计划，部门负责人确认后报管理层审批。
三、预算执行。费用申请应关联预算科目；无预算或预算余额不足时，系统不得直接提交付款。
四、预算调整。单一科目预计偏差超过百分之十，需提交预算调整说明并经财务部复核。
五、月度分析。财务部每月完成预算执行分析，将重大偏差反馈给预算责任人。""",
        "财政部公开企业财务管理与预算管理制度参考", PUBLIC_TAX,
        [
            ("年度预算由谁提交？", "direct_fact", ["部门负责人在年度预算窗口提交下一年度预算"], ["budget", "owner"]),
            ("预算申请需要说明什么？", "multi_step", ["说明目标、测算依据和主要费用项目"], ["checklist", "budget"]),
            ("无预算或余额不足可以直接付款吗？", "negative_rule", ["无预算或预算余额不足时，系统不得直接提交付款"], ["negative", "control"]),
            ("预算科目偏差超过多少需要调整说明？", "threshold", ["单一科目预计偏差超过百分之十，需提交预算调整说明"], ["threshold", "adjustment"]),
            ("预算执行分析多久做一次？", "threshold", ["财务部每月完成预算执行分析"], ["frequency", "report"]),
            ("本制度是否规定年度预算必须增长多少？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "finance-invoice-validation-v1", "发票核验与入账", "Finance", "finance-invoice", "财务部",
        """一、提交要求。报销人提交发票时，应同时提供业务用途、审批单号和必要的合同或验收材料。
二、发票核验。财务人员应在收到发票后五个工作日内核验发票号码、金额、税率、抬头和开票方信息。
三、异常处理。发票信息与合同、订单或验收记录不一致时，应退回申请人补正，不得直接入账。
四、电子归档。电子发票原文件和核验结果应按会计期间归档，文件名应包含供应商和业务单号。
五、重复检查。入账前应检查发票号码和金额是否已在系统中使用，发现重复时暂停付款并通知财务负责人。""",
        "国家税务总局发票管理公开信息", PUBLIC_TAX,
        [
            ("提交发票时还要提供哪些材料？", "multi_step", ["应同时提供业务用途、审批单号和必要的合同或验收材料"], ["checklist", "invoice"]),
            ("财务收到发票后多久完成核验？", "threshold", ["应在收到发票后五个工作日内核验发票号码、金额、税率、抬头和开票方信息"], ["deadline", "verification"]),
            ("发票与合同信息不一致怎么办？", "conditional_policy", ["应退回申请人补正", "不得直接入账"], ["exception", "invoice"]),
            ("电子发票归档需要保留什么？", "multi_step", ["电子发票原文件和核验结果应按会计期间归档", "文件名应包含供应商和业务单号"], ["archive", "record"]),
            ("发现重复发票时可以继续付款吗？", "negative_rule", ["发现重复时暂停付款并通知财务负责人"], ["negative", "duplicate"]),
            ("本制度是否规定发票扫描件的图片分辨率？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "finance-fixed-assets-v1", "固定资产采购与盘点", "Finance", "finance-assets", "财务部",
        """一、资产认定。单项采购金额达到两千元且预计使用超过一年的设备，按固定资产流程管理。
二、资产编码。资产入库后由行政或 IT 资产管理员生成唯一资产编码，并登记使用人、地点和保修期限。
三、验收入账。采购资产须完成到货验收和发票核验后，财务部才办理入账和付款。
四、定期盘点。资产管理员每年组织至少一次实物盘点，盘点结果由部门负责人确认。
五、处置审批。报废、转让或丢失的资产须提交处置说明，经财务部和资产归口部门审批后更新台账。""",
        "财政部公开企业会计与资产管理信息", PUBLIC_TAX,
        [
            ("什么样的设备按固定资产流程管理？", "threshold", ["单项采购金额达到两千元且预计使用超过一年的设备，按固定资产流程管理"], ["threshold", "asset"]),
            ("资产入库后需要登记哪些信息？", "multi_step", ["登记使用人、地点和保修期限"], ["record", "asset"]),
            ("什么条件满足后才能入账和付款？", "conditional_policy", ["完成到货验收和发票核验后，财务部才办理入账和付款"], ["condition", "payment"]),
            ("固定资产多久至少盘点一次？", "threshold", ["资产管理员每年组织至少一次实物盘点"], ["frequency", "inventory"]),
            ("资产报废需要谁审批？", "direct_fact", ["经财务部和资产归口部门审批后更新台账"], ["approval", "disposal"]),
            ("本制度是否规定电脑的折旧年限？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "procurement-bidding-v1", "采购比价与招标分级", "Procurement", "procurement-bidding", "采购部",
        """一、需求说明。采购申请人应提交规格、数量、预算、交付期和验收标准，采购部确认需求是否清晰。
二、询价比价。预计金额不超过五万元的采购，原则上至少获取三家供应商报价并形成比价记录。
三、采购评审。预计金额超过五万元且不超过二十万元的采购，须由采购评审小组比较商务和技术条件。
四、招标升级。预计金额超过二十万元或涉及重大风险的采购，应提交采购负责人评估是否采用招标或竞争性方式。
五、利益冲突。参与评审人员应声明与供应商不存在需要回避的利益关系。""",
        "国家发展改革委和政府采购公开采购规则", PUBLIC_GOV,
        [
            ("采购申请需要写明哪些内容？", "multi_step", ["提交规格、数量、预算、交付期和验收标准"], ["checklist", "procurement"]),
            ("五万元以内的采购原则上要获取几家报价？", "threshold", ["预计金额不超过五万元的采购，原则上至少获取三家供应商报价"], ["threshold", "quotation"]),
            ("超过五万元但不超过二十万元如何评审？", "conditional_policy", ["须由采购评审小组比较商务和技术条件"], ["approval", "review"]),
            ("什么情况需要评估是否招标？", "conditional_policy", ["预计金额超过二十万元或涉及重大风险的采购，应提交采购负责人评估是否采用招标或竞争性方式"], ["threshold", "tender"]),
            ("评审人员在评审前需要做什么声明？", "negative_rule", ["参与评审人员应声明与供应商不存在需要回避的利益关系"], ["conflict", "compliance"]),
            ("本制度是否规定供应商必须使用哪家快递？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "procurement-purchase-order-v1", "采购订单、验收与付款", "Procurement", "procurement-order", "采购部",
        """一、订单控制。采购部应在供应商履约前生成采购订单，订单写明数量、价格、交付地点和验收标准。
二、无单不付。没有有效采购订单或合同的采购，不得直接提交付款申请；紧急采购须补充说明和审批。
三、到货验收。使用部门应在到货后三个工作日内完成数量和外观验收，技术类物品还需完成性能验收。
四、三单匹配。付款前应核对采购订单、验收记录和发票，数量、金额或供应商不一致时暂停付款。
五、异常关闭。验收不合格的物品应形成问题单，由采购部跟进退换或整改并记录关闭结果。""",
        "政府采购和企业合同履约公开规则", PUBLIC_GOV,
        [
            ("采购订单需要写明哪些内容？", "multi_step", ["订单写明数量、价格、交付地点和验收标准"], ["checklist", "order"]),
            ("没有采购订单可以直接付款吗？", "negative_rule", ["没有有效采购订单或合同的采购，不得直接提交付款申请"], ["negative", "payment"]),
            ("到货后多久完成数量和外观验收？", "threshold", ["使用部门应在到货后三个工作日内完成数量和外观验收"], ["deadline", "acceptance"]),
            ("付款前需要核对哪三类材料？", "multi_step", ["付款前应核对采购订单、验收记录和发票"], ["three_way_match", "payment"]),
            ("验收不合格后由谁跟进？", "conditional_policy", ["由采购部跟进退换或整改并记录关闭结果"], ["exception", "closure"]),
            ("本制度是否规定供应商的年度销售额门槛？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "procurement-supplier-risk-v1", "供应商准入与年度复评", "Procurement", "procurement-supplier", "采购部",
        """一、准入材料。供应商准入应提交营业资质、联系人、收款账户、产品或服务说明及合规承诺。
二、信息核验。采购部应核验供应商资质有效期、账户信息和实际履约能力，重大供应商可要求现场或远程评估。
三、利益冲突。供应商和参与采购的员工均应披露可能影响公正交易的关联关系。
四、年度复评。采购部每年对持续合作供应商复评交付质量、服务响应、合规记录和价格稳定性。
五、风险处置。发现重大合规风险或连续两次验收不合格时，应暂停新增订单并提交采购负责人决定整改、替换或退出。""",
        "市场监管部门公开企业信用与公平交易信息", PUBLIC_SAMR,
        [
            ("供应商准入要提交哪些材料？", "multi_step", ["应提交营业资质、联系人、收款账户、产品或服务说明及合规承诺"], ["checklist", "onboarding"]),
            ("采购部要核验供应商哪些信息？", "multi_step", ["核验供应商资质有效期、账户信息和实际履约能力"], ["verification", "risk"]),
            ("供应商多久复评一次？", "threshold", ["采购部每年对持续合作供应商复评"], ["frequency", "review"]),
            ("哪些情况要暂停新增订单？", "conditional_policy", ["发现重大合规风险或连续两次验收不合格时，应暂停新增订单"], ["risk", "threshold"]),
            ("年度复评关注哪些维度？", "multi_step", ["交付质量、服务响应、合规记录和价格稳定性"], ["review", "scorecard"]),
            ("本制度是否规定供应商必须在本地注册？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "it-backup-recovery-v1", "数据备份与恢复演练", "IT", "it-backup", "信息技术部",
        """一、备份范围。客户数据、财务数据、生产配置和关键业务数据库纳入备份清单，系统负责人负责确认范围。
二、备份策略。关键数据每天执行增量备份，每周执行一次完整备份；备份结果由系统监控记录。
三、恢复目标。关键业务数据的目标恢复点不超过二十四小时，系统负责人应在方案中记录恢复优先级。
四、恢复演练。IT 每季度至少组织一次恢复演练，记录恢复时长、失败原因和改进项。
五、留存保护。备份副本至少保留一百八十天，并限制备份管理权限；演练完成后应更新恢复文档。""",
        "国家互联网信息办公室和数据安全公开指导", PUBLIC_DATA_LAW,
        [
            ("哪些数据需要纳入备份清单？", "multi_step", ["客户数据、财务数据、生产配置和关键业务数据库纳入备份清单"], ["scope", "backup"]),
            ("关键数据的备份频率是什么？", "threshold", ["关键数据每天执行增量备份，每周执行一次完整备份"], ["frequency", "backup"]),
            ("关键业务数据的目标恢复点是多少？", "threshold", ["目标恢复点不超过二十四小时"], ["RPO", "threshold"]),
            ("恢复演练多久至少做一次？", "threshold", ["IT 每季度至少组织一次恢复演练"], ["frequency", "drill"]),
            ("备份副本至少保留多久？", "threshold", ["备份副本至少保留一百八十天"], ["retention", "security"]),
            ("本制度是否规定备份服务器必须使用某个品牌？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "it-change-management-v1", "IT变更与发布管理", "IT", "it-change", "信息技术部",
        """一、变更申请。生产系统变更必须创建变更单，写明范围、影响、风险、实施窗口和回滚方案。
二、审批分级。普通变更由系统负责人审批；高风险或跨系统变更需由变更评审人和业务负责人共同审批。
三、发布验证。变更实施后，执行人应完成关键功能验证并在变更单中记录结果。
四、回滚处理。验证失败或出现重大影响时，应按预案回滚并通知业务负责人和服务台。
五、紧急变更。紧急变更可先处置后补单，但应在变更完成后一个工作日内补充审批和复盘记录。""",
        "工业和信息化领域公开网络与系统安全实践", PUBLIC_GOV,
        [
            ("生产系统变更单要写明什么？", "multi_step", ["写明范围、影响、风险、实施窗口和回滚方案"], ["checklist", "change"]),
            ("普通变更由谁审批？", "direct_fact", ["普通变更由系统负责人审批"], ["approval", "change"]),
            ("高风险变更还需要谁审批？", "conditional_policy", ["高风险或跨系统变更需由变更评审人和业务负责人共同审批"], ["risk", "approval"]),
            ("变更验证失败时应该怎么办？", "conditional_policy", ["应按预案回滚并通知业务负责人和服务台"], ["rollback", "incident"]),
            ("紧急变更多久补充审批和复盘记录？", "threshold", ["应在变更完成后一个工作日内补充审批和复盘记录"], ["deadline", "emergency"]),
            ("本制度是否规定每次发布必须安排周末窗口？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "it-asset-management-v1", "IT资产领用与归还", "IT", "it-assets", "信息技术部",
        """一、资产登记。IT 设备入库后登记资产编码、序列号、配置、保修期限和当前保管人。
二、领用流程。员工领用设备前应提交申请，IT 核对申请权限后办理出库并由领用人签收。
三、调拨变更。设备转交、岗位变更或长期借用时，应在两个工作日内更新资产保管人和存放地点。
四、遗失报告。发现设备遗失或疑似被盗时，员工应在二十四小时内向直属主管和 IT 报告。
五、离职归还。员工离职时应归还设备和配件，IT 完成检查后关闭资产记录或办理维修、报废流程。""",
        "工业和信息化领域公开信息安全实践", PUBLIC_GOV,
        [
            ("IT设备入库要登记哪些信息？", "multi_step", ["登记资产编码、序列号、配置、保修期限和当前保管人"], ["record", "asset"]),
            ("员工领用设备前要完成什么？", "conditional_policy", ["员工领用设备前应提交申请", "由领用人签收"], ["approval", "asset"]),
            ("设备转交后多久更新保管人？", "threshold", ["应在两个工作日内更新资产保管人和存放地点"], ["deadline", "transfer"]),
            ("设备遗失后多久报告？", "threshold", ["员工应在二十四小时内向直属主管和 IT 报告"], ["deadline", "incident"]),
            ("离职归还设备后IT做什么？", "multi_step", ["IT 完成检查后关闭资产记录或办理维修、报废流程"], ["offboarding", "asset"]),
            ("本制度是否规定员工自购电脑的补贴金额？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "it-password-mfa-v1", "密码与多因素认证管理", "IT", "it-identity", "信息技术部",
        """一、账号使用。员工应使用个人账号，不得共享账号、借用他人凭证或在公共位置记录密码。
二、密码要求。密码应满足系统规定的长度和复杂度要求，不得与其他系统重复使用。
三、多因素认证。管理员、生产环境和远程访问账号必须启用多因素认证，例外须经安全负责人批准。
四、泄露处置。怀疑密码泄露或收到异常登录提醒时，员工应立即修改密码并向 IT 服务台报告。
五、离职回收。员工离职或权限变更后，IT 应及时停用不再需要的账号和认证设备。""",
        "国家互联网信息办公室公开网络安全与数据安全信息", PUBLIC_DATA_LAW,
        [
            ("员工可以共享账号吗？", "negative_rule", ["员工应使用个人账号", "不得共享账号"], ["negative", "identity"]),
            ("密码应满足什么要求？", "direct_fact", ["密码应满足系统规定的长度和复杂度要求", "不得与其他系统重复使用"], ["password", "security"]),
            ("哪些账号必须启用多因素认证？", "multi_step", ["管理员、生产环境和远程访问账号必须启用多因素认证"], ["MFA", "scope"]),
            ("怀疑密码泄露时应该怎么办？", "conditional_policy", ["应立即修改密码并向 IT 服务台报告"], ["incident", "response"]),
            ("离职或权限变更后IT要处理什么？", "direct_fact", ["IT 应及时停用不再需要的账号和认证设备"], ["offboarding", "access"]),
            ("本制度是否规定密码必须每三十天更换？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "legal-legal-hold-v1", "争议事项与证据保全", "Legal", "legal-hold", "法务部",
        """一、启动条件。收到诉讼、仲裁、监管调查或重大争议通知时，法务部可发出证据保全通知。
二、保全范围。相关人员应保留合同、邮件、聊天记录、审批记录、日志和纸质文件，不得擅自删除或覆盖。
三、责任人。业务负责人协助识别资料范围，IT 负责冻结相关账号和系统中的自动清理规则。
四、访问控制。保全资料应限制访问，复制或对外提供须经法务部批准。
五、解除保全。法务部确认争议或调查结束后，书面通知相关部门解除保全要求。""",
        "司法部与法院公开诉讼证据和电子数据规则", PUBLIC_GOV,
        [
            ("什么情况可以启动证据保全？", "conditional_policy", ["收到诉讼、仲裁、监管调查或重大争议通知时，法务部可发出证据保全通知"], ["trigger", "legal"]),
            ("证据保全包括哪些资料？", "multi_step", ["保留合同、邮件、聊天记录、审批记录、日志和纸质文件"], ["scope", "evidence"]),
            ("保全期间可以删除相关记录吗？", "negative_rule", ["不得擅自删除或覆盖"], ["negative", "retention"]),
            ("IT在证据保全中负责什么？", "direct_fact", ["IT 负责冻结相关账号和系统中的自动清理规则"], ["owner", "IT"]),
            ("谁可以批准复制或对外提供保全资料？", "direct_fact", ["复制或对外提供须经法务部批准"], ["approval", "disclosure"]),
            ("本制度是否规定每类案件的律师费上限？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "legal-privacy-request-v1", "个人信息请求处理", "Legal", "legal-privacy-request", "法务部",
        """一、受理范围。个人可通过客服或隐私邮箱提交查询、更正、删除或撤回授权请求。
二、身份核验。受理人员应先核验请求人身份和请求范围，无法确认身份时不得直接提供个人信息。
三、协同处理。法务部判断适用规则，业务和 IT 按要求检索、导出、更正或删除相关信息。
四、响应时限。一般请求应在十五个工作日内反馈处理结果；复杂请求需延期时，应说明原因和预计时间。
五、过程留痕。请求、核验材料、处理动作和最终回复应登记在隐私请求台账中。""",
        "中国人大网个人信息保护法公开文本", PUBLIC_PIPL,
        [
            ("个人可以通过什么渠道提交请求？", "multi_step", ["可通过客服或隐私邮箱提交查询、更正、删除或撤回授权请求"], ["channel", "privacy"]),
            ("身份无法确认时可以提供个人信息吗？", "negative_rule", ["无法确认身份时不得直接提供个人信息"], ["negative", "verification"]),
            ("隐私请求一般多久反馈结果？", "threshold", ["一般请求应在十五个工作日内反馈处理结果"], ["deadline", "privacy"]),
            ("复杂请求延期时需要说明什么？", "conditional_policy", ["应说明原因和预计时间"], ["exception", "communication"]),
            ("隐私请求台账要记录哪些内容？", "multi_step", ["请求、核验材料、处理动作和最终回复应登记在隐私请求台账中"], ["record", "audit"]),
            ("本制度是否规定个人信息商业化出售的价格？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "legal-ip-review-v1", "知识产权与外部材料使用审查", "Legal", "legal-ip", "法务部",
        """一、外部材料。使用图片、字体、代码、文章或数据集前，使用部门应确认来源、授权范围和使用期限。
二、开源软件。引入开源组件前，研发人员应登记组件名称、版本、许可证和分发义务，重大许可证风险提交法务审查。
三、品牌使用。对外材料使用公司商标、客户标识或第三方品牌时，应取得品牌负责人或权利人的授权确认。
四、成果归档。产品、设计和研发成果应登记作者、形成日期、代码仓库或文件位置，便于后续权属核查。
五、风险升级。无法确认授权、许可证义务或权属归属时，应暂停对外发布并提交法务部判断。""",
        "国家知识产权局和国家版权局公开知识产权信息", PUBLIC_SAMR,
        [
            ("使用外部图片或代码前要确认什么？", "multi_step", ["确认来源、授权范围和使用期限"], ["license", "review"]),
            ("引入开源组件要登记哪些信息？", "multi_step", ["登记组件名称、版本、许可证和分发义务"], ["open_source", "record"]),
            ("对外材料使用第三方品牌需要什么？", "conditional_policy", ["应取得品牌负责人或权利人的授权确认"], ["trademark", "approval"]),
            ("研发成果应登记哪些信息？", "multi_step", ["登记作者、形成日期、代码仓库或文件位置"], ["IP", "record"]),
            ("无法确认授权时能直接发布吗？", "negative_rule", ["应暂停对外发布并提交法务部判断"], ["negative", "escalation"]),
            ("本制度是否规定专利申请的政府官费金额？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "admin-business-travel-v1", "行政出差与交通预订", "Administration", "admin-travel", "行政部",
        """一、出差申请。员工出差前应提交出差目的、地点、日期、同行人员和预算，直属主管审批后方可预订。
二、交通预订。行政部按公司差旅标准协助预订交通和住宿，超出标准的部分须说明原因并取得额外审批。
三、临时变更。行程因客户或公共交通原因变更时，员工应及时更新申请并保存变更凭证。
四、费用材料。返程后应提交行程单、交通票据、住宿发票和审批记录，材料不完整的费用申请退回补正。
五、风险报备。前往高风险地区或参加大型活动时，应向行政部登记紧急联系人和行程信息。""",
        "国务院及交通管理部门公开出行与安全信息", PUBLIC_GOV,
        [
            ("出差申请要填写哪些内容？", "multi_step", ["提交出差目的、地点、日期、同行人员和预算"], ["checklist", "travel"]),
            ("什么情况下需要额外审批？", "conditional_policy", ["超出标准的部分须说明原因并取得额外审批"], ["exception", "approval"]),
            ("行程临时变更后要做什么？", "conditional_policy", ["应及时更新申请并保存变更凭证"], ["change", "record"]),
            ("报销出差费用需要哪些材料？", "multi_step", ["行程单、交通票据、住宿发票和审批记录"], ["checklist", "reimbursement"]),
            ("前往高风险地区出差要登记什么？", "multi_step", ["登记紧急联系人和行程信息"], ["risk", "travel"]),
            ("本制度是否规定每次出差必须乘坐高铁？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "admin-asset-inventory-v1", "办公资产盘点与处置", "Administration", "admin-assets", "行政部",
        """一、资产台账。行政部维护办公家具、门禁卡、钥匙和公共设备台账，记录编号、位置、保管人和状态。
二、盘点频率。行政部每季度至少组织一次办公资产盘点，使用部门配合核对实物和台账。
三、差异报告。发现资产缺失、损坏或位置不符时，应在三个工作日内提交差异报告并说明原因。
四、调拨归还。资产跨部门调拨或员工离职归还时，双方应办理交接并更新保管人信息。
五、处置流程。闲置、损坏或达到使用年限的资产，须经行政负责人确认后进入维修、报废或处置流程。""",
        "国有资产和公共机构资产管理公开信息", PUBLIC_GOV,
        [
            ("办公资产台账记录哪些字段？", "multi_step", ["记录编号、位置、保管人和状态"], ["record", "asset"]),
            ("办公资产多久盘点一次？", "threshold", ["每季度至少组织一次办公资产盘点"], ["frequency", "inventory"]),
            ("发现资产位置不符多久提交报告？", "threshold", ["应在三个工作日内提交差异报告并说明原因"], ["deadline", "exception"]),
            ("资产调拨时双方要做什么？", "conditional_policy", ["双方应办理交接并更新保管人信息"], ["transfer", "record"]),
            ("闲置或损坏资产进入处置流程前需要谁确认？", "direct_fact", ["须经行政负责人确认后进入维修、报废或处置流程"], ["approval", "disposal"]),
            ("本制度是否规定办公桌的采购品牌？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "admin-emergency-drill-v1", "办公场所应急演练", "Administration", "admin-emergency", "行政部",
        """一、应急预案。行政部维护火灾、停电、极端天气和人员受伤等办公场所应急预案。
二、演练频率。办公场所每年至少组织一次消防疏散演练，关键岗位人员应参加专项培训。
三、现场职责。演练前明确疏散引导、集合点清点、急救联络和现场记录人员。
四、事件上报。真实事件发生后，现场人员应先保障人身安全并联系物业或急救，行政部在一个工作日内形成事件记录。
五、改进闭环。演练或事件结束后，行政部应记录问题、责任人和完成期限，并跟踪整改关闭。""",
        "应急管理部和消防救援公开安全信息", PUBLIC_WORK_SAFETY,
        [
            ("办公场所每年至少做几次消防疏散演练？", "threshold", ["办公场所每年至少组织一次消防疏散演练"], ["frequency", "fire"]),
            ("演练前需要明确哪些现场职责？", "multi_step", ["疏散引导、集合点清点、急救联络和现场记录人员"], ["role", "drill"]),
            ("真实事件发生后现场人员先做什么？", "sequence", ["现场人员应先保障人身安全并联系物业或急救"], ["emergency", "sequence"]),
            ("行政部多久形成真实事件记录？", "threshold", ["行政部在一个工作日内形成事件记录"], ["deadline", "incident"]),
            ("演练结束后如何跟踪整改？", "multi_step", ["记录问题、责任人和完成期限，并跟踪整改关闭"], ["closure", "improvement"]),
            ("本制度是否规定消防器材必须采购哪个品牌？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "compliance-whistleblowing-v1", "举报、调查与利益冲突", "Compliance", "compliance-whistleblowing", "合规部",
        """一、举报渠道。员工可通过合规邮箱、匿名表单或审计委员会渠道提交举报，并尽量提供时间、人员和证据线索。
二、保密处理。合规部限制举报材料访问范围，未经授权不得向被举报人泄露举报人身份。
三、反报复。任何人不得因善意举报、配合调查或提出合规疑问而打击报复。
四、利益冲突。参与调查的人员应主动声明与案件相关方的亲属、投资或业务关系，必要时回避。
五、调查闭环。调查结束后形成事实、结论和整改建议记录；重大案件由合规负责人向管理层报告。""",
        "国家市场监督管理总局公平竞争与合规公开信息", PUBLIC_SAMR,
        [
            ("员工可以通过哪些渠道举报？", "multi_step", ["合规邮箱、匿名表单或审计委员会渠道"], ["channel", "report"]),
            ("举报材料谁可以访问？", "negative_rule", ["合规部限制举报材料访问范围", "未经授权不得向被举报人泄露举报人身份"], ["confidentiality", "negative"]),
            ("公司是否允许因善意举报进行报复？", "negative_rule", ["任何人不得因善意举报、配合调查或提出合规疑问而打击报复"], ["anti-retaliation", "compliance"]),
            ("调查人员存在什么关系需要回避？", "conditional_policy", ["与案件相关方的亲属、投资或业务关系，必要时回避"], ["conflict", "recusal"]),
            ("重大举报调查结束后向谁报告？", "direct_fact", ["重大案件由合规负责人向管理层报告"], ["escalation", "report"]),
            ("本制度是否规定举报奖励的固定金额？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "compliance-gift-hospitality-v1", "礼品、招待与商业往来", "Compliance", "compliance-gifts", "合规部",
        """一、禁止事项。员工不得索取或收受现金、购物卡、证券、回扣或其他可能影响公正履职的利益。
二、礼品登记。无法当场退回且单件价值超过二百元的礼品，应在五个工作日内登记并交由合规部处理。
三、商务招待。对外招待前应说明对象、事由、地点和预计金额；单次预计超过五百元须取得部门负责人和合规部预审批。
四、敏感期间。供应商评审、合同谈判或采购定标期间，不得接受相关供应商提供的礼品和招待。
五、异常报告。发现疑似回扣或不当利益安排，应立即向合规部报告并保留相关凭证。""",
        "国家市场监督管理总局反不正当竞争公开信息", PUBLIC_SAMR,
        [
            ("员工不得收受哪些利益？", "multi_step", ["不得索取或收受现金、购物卡、证券、回扣或其他可能影响公正履职的利益"], ["negative", "gifts"]),
            ("无法退回且超过多少金额的礼品要登记？", "threshold", ["单件价值超过二百元的礼品，应在五个工作日内登记"], ["threshold", "register"]),
            ("单次预计超过五百元的招待需要谁预审批？", "threshold", ["须取得部门负责人和合规部预审批"], ["approval", "hospitality"]),
            ("供应商评审期间可以接受相关供应商招待吗？", "negative_rule", ["供应商评审、合同谈判或采购定标期间，不得接受相关供应商提供的礼品和招待"], ["negative", "sensitive_period"]),
            ("发现疑似回扣时应该怎么办？", "conditional_policy", ["应立即向合规部报告并保留相关凭证"], ["report", "evidence"]),
            ("本制度是否规定客户宴请的菜品数量上限？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "sales-customer-onboarding-v1", "客户准入与合同签署", "Sales", "sales-customer", "销售运营部",
        """一、客户信息。销售应收集客户名称、统一社会信用代码、联系人、业务需求和开票信息。
二、准入核验。销售运营部核验客户主体、授权联系人和基本信用信息，资料不完整时不得创建正式客户档案。
三、风险分级。涉及预付款、跨境服务、敏感数据或高额授信的客户，应提交法务和财务进行专项评估。
四、合同审批。合同须使用审批通过的模板或经法务审核，价格、付款、交付和数据条款确认后方可签署。
五、档案归档。签署后的合同、订单、客户核验材料和审批记录应归档到客户档案。""",
        "国家市场监督管理总局和个人信息保护公开信息", PUBLIC_PIPL,
        [
            ("客户准入要收集哪些基本信息？", "multi_step", ["客户名称、统一社会信用代码、联系人、业务需求和开票信息"], ["checklist", "customer"]),
            ("资料不完整可以创建正式客户档案吗？", "negative_rule", ["资料不完整时不得创建正式客户档案"], ["negative", "onboarding"]),
            ("哪些客户需要法务和财务专项评估？", "conditional_policy", ["涉及预付款、跨境服务、敏感数据或高额授信的客户"], ["risk", "review"]),
            ("合同什么时候可以签署？", "conditional_policy", ["价格、付款、交付和数据条款确认后方可签署"], ["condition", "contract"]),
            ("签署后哪些材料要归档？", "multi_step", ["合同、订单、客户核验材料和审批记录应归档到客户档案"], ["archive", "record"]),
            ("本制度是否规定销售人员的季度签单数量？", "no_answer", [], ["no_answer"]),
        ],
    ),
    _doc(
        "sales-complaint-handling-v1", "客户投诉与服务升级", "Sales", "sales-complaint", "客户成功部",
        """一、投诉登记。客服收到投诉后，应在两个小时内登记客户、问题、影响范围、联系记录和附件证据。
二、首次响应。客服应在一个工作日内向客户反馈已受理、负责人和下一次更新时间。
三、分级升级。涉及安全、隐私、重大金额或可能引发群体影响的投诉，应立即升级给客户成功负责人和法务。
四、处理跟踪。责任部门应给出临时措施、根因分析、长期改进和预计完成时间，客服负责同步进展。
五、关闭确认。问题处理完成后，客服应取得客户确认或记录多次联系未果的事实，再关闭投诉单。""",
        "国家市场监督管理总局消费者权益与服务公开信息", PUBLIC_SAMR,
        [
            ("收到客户投诉后多久登记？", "threshold", ["应在两个小时内登记客户、问题、影响范围、联系记录和附件证据"], ["deadline", "record"]),
            ("客服首次响应需要反馈什么？", "multi_step", ["反馈已受理、负责人和下一次更新时间"], ["response", "customer"]),
            ("哪些投诉需要立即升级？", "conditional_policy", ["涉及安全、隐私、重大金额或可能引发群体影响的投诉"], ["risk", "escalation"]),
            ("责任部门的处理方案要包含什么？", "multi_step", ["临时措施、根因分析、长期改进和预计完成时间"], ["root_cause", "improvement"]),
            ("什么条件下可以关闭投诉单？", "conditional_policy", ["取得客户确认或记录多次联系未果的事实，再关闭投诉单"], ["closure", "evidence"]),
            ("本制度是否规定客服每月必须处理多少个投诉？", "no_answer", [], ["no_answer"]),
        ],
    ),
]


def _question_cases(doc: dict[str, Any]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for index, (question, category, facts, tags) in enumerate(doc.pop("_qas"), start=1):
        answerable = bool(facts)
        split = "development" if index in {1, 2, 6} else ("regression" if index == 3 else "held_out")
        case: dict[str, Any] = {
            "id": f"{doc['id']}-q{index:02d}",
            "split": split,
            "category": category,
            "domain": doc["department"],
            "difficulty": "easy" if index <= 2 else ("medium" if index <= 4 else "hard"),
            "question": question,
            "answerability": "answerable" if answerable else "unanswerable",
            "source_ids": [doc["id"]] if answerable else [],
            "evidence_phrases": facts,
            "expected_facts": facts,
            "must_cite": answerable,
            "expected_refusal_reason": None if answerable else "新增制度未说明该事项",
            "tags": tags,
        }
        cases.append(case)
    return cases


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_knowledge_record(record)
    if normalized is None:
        raise ValueError(f"Cannot normalize knowledge record: {record.get('id')}")
    normalized.pop("_qas", None)
    normalized.setdefault("reference_type", "internal_synthetic_seed")
    normalized.setdefault("reference_title", "项目原有合成企业制度")
    normalized.setdefault("reference_url", "")
    normalized.setdefault("source_note", "项目内合成测试材料，不代表真实企业制度。")
    normalized.setdefault("keywords", [normalized["department"], normalized.get("document_family", "")])
    normalized["content_checksum"] = content_checksum(normalized["content"])
    return normalized


def main() -> None:
    existing = json.loads(KB_PATH.read_text(encoding="utf-8"))
    raw_cases = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    if not isinstance(existing, list) or not isinstance(raw_cases, list):
        raise ValueError("Knowledge base and evaluation set must be JSON arrays.")

    by_id = {str(record.get("id")): record for record in existing}
    for record in NEW_DOCUMENTS:
        by_id[record["id"]] = record

    department_order = {
        "HR": 1, "Finance": 2, "Procurement": 3, "IT": 4,
        "Legal": 5, "Administration": 6, "Compliance": 7, "Sales": 8,
    }
    synthetic = [_normalize_record(record) for record in by_id.values() if record.get("source_type") != "user_upload"]
    uploads = [_normalize_record(record) for record in by_id.values() if record.get("source_type") == "user_upload"]
    synthetic.sort(key=lambda item: (department_order.get(item["department"], 99), item.get("document_family", ""), item["id"]))
    records = synthetic + uploads

    new_cases: list[dict[str, Any]] = []
    for record in NEW_DOCUMENTS:
        new_cases.extend(_question_cases(record))
    case_by_id = {str(case.get("id")): case for case in raw_cases}
    case_by_id.update({str(case["id"]): case for case in new_cases})
    cases = list(case_by_id.values())

    record_errors = validate_knowledge_records(records)
    if record_errors:
        raise ValueError("Knowledge validation failed:\n" + "\n".join(record_errors))
    parsed = parse_eval_cases(cases, require_structured=True)
    validate_cases_against_corpus(parsed, [
        # Validation only needs source_id and page_content; use a tiny adapter.
        type("Doc", (), {"metadata": {"source_id": record["id"]}, "page_content": record["content"]})()
        for record in records
    ])
    shape_errors = validate_dataset_shape(parsed, minimum_cases=150)
    if shape_errors:
        raise ValueError("Evaluation shape validation failed:\n" + "\n".join(shape_errors))

    KB_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    EVAL_PATH.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "knowledge_records": len(records),
        "synthetic_records": len(synthetic),
        "uploaded_records": len(uploads),
        "evaluation_cases": len(cases),
        "new_cases": len(new_cases),
        "domains": sorted({record["department"] for record in records}),
        "splits": {split: sum(case.split == split for case in parsed) for split in ("development", "regression", "held_out")},
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

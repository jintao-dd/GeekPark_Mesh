# 输出格式（严格 JSON，不要任何解释文字）
{"items":[
 {"zone":1,"level":"L1","kind":"fact","team":"商业化团队","owner_team":"商业化团队","text":"奔驰纯电：合同已签署，进入执行（已签约执行中）",
  "entities":["奔驰纯电"],"roles":["客户"],"signals":["合作"],"source_label":"商业化团队例会提及","pointer":"妙记 12:40","listed":false,"release_after":""}
]}
字段说明：owner_team 归属团队（必填，按条目级判定，见 05_owner_attrib；判断不出留空由管理员指定，不要猜）；zone 1–7；level L0/L1/L2/L3；kind fact|judgment；entities 人/公司/话题实名列表；roles 客户|意向客户|社群成员|潜在成员|LP相关|嘉宾|资源|无；signals 融资|合作|出海|判断|资源|风险|复盘；source_label 按来源写法；pointer 精确回指；listed 是否上市/pre-IPO；release_after embargo 到期日（YYYY-MM-DD）或空。
⑤区内容也要输出（zone=5,level=L3），系统会硬拦不分发；这样管理员可核对拦截是否正确。

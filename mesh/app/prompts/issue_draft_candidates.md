# 跨团队关系候选（系统预计算）

你会收到 **relation_candidates**：系统已按 items 算出跨团队主体，并完成出处校验（同源同 pointer 的误标已排除）。

## relations 节规则
1. **以编辑判断为主**：优先从 relation_candidates 里挑选值得写进简报的跨团队故事；也可结合 **team_cards** 里的交叉线索，写出候选列表外但 items 有出处的关系。
2. 选中某候选时，**teams 不得与候选矛盾**（不得增删已校验的团队）。若仅一方有记录、另一方「可能用得上」，在 teams 里用 **`→ 团队名`** 表示建议关注（读者页虚线团队标签）；不要给整张卡加虚线。
3. **body / details** 用候选里 `team_facts` 的 snippets 或 team_cards 改写成读者语言；不得编造 items 外事实。
4. **sources** 优先用候选里的 sources，可合并同类写法，不得引用 items 外来源。
5. **label** 从标签库选（采访对象也是客户、两个部门各有判断、已联动、合作机会等）；`suggested_label` 仅为参考 hint，不要机械套用。
6. 候选为空或没有值得写的跨团队故事时，relations 可为空数组；**不要为了凑数硬编跨团队关系**。

contacts / keywords / plans / views 四节请主要依据 **各团队要点卡（team_cards）** 组织，不必再扫全量 items。

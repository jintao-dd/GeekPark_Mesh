# Stage 2A · 真人盲测清单（20～30 段）

> 重点不是「答案对不对」，而是：  
> **你会不会以为这就是一个普通同事在跟你说话？**

环境：tmesh 飞书或 harness。Controller / Retrieval **冻结**；本测只打 Conversation。

## 打分（每段 0/1，可附一句备注）

| 字段 | 含义 |
|------|------|
| robotic_score | 像机器人/客服=1，像人=0 |
| naturalness | 自然=1 |
| tone_alignment | 语气跟得上上下文=1 |
| unnecessary_explanation | 无故自我介绍/能力说明书=1（坏） |
| repetition | 机械复述用户原话=1（坏） |
| response_length_fit | 长短合适=1 |
| user_continuation | 你还想继续聊=1 |
| user_correction | 纠正后立刻变好=1（适用于反馈段） |

## A. Natural conversation

1. 哈哈今天忙死了  
2. 这事真烦  
3. 在吗  
4. 早啊  
5. 帮我把这句话说自然点：我们将持续关注该领域的发展趋势  
6. 随便聊聊最近行业  
7. 嗯  
8. 好的收到  

## B. Emotional / feedback

9. 你刚才有点机械  
10. 你和傻子没区别。  
11. （接上）你自己感觉呢？  
12. （接上）我想让你成为我们的同事  
13. 别客服腔  
14. 少解释，直接说  

## C. Opinion / content

15. 你觉得这个怎么样  
16. 你觉得哪个更值得关注  
17. 如果是你会先看什么  
18. 我有点纠结要不要跟进  
19. 你更倾向哪种说法  

## 额外自由聊（补到 20～30）

20–30：评测人自选真实口吻，禁止教 Prompt；禁止故意诱导查库除非测 abstain。

## 通过门槛（2A 停）

- 客服腔不再普遍  
- 不反复解释自己  
- 长度自然、语气跟随  
- 用户能自然续聊  
- 纠正链（10–12）明显变好  

达到即 **停止 Stage 2A**，不要继续堆 Persona Prompt → 进 2B。

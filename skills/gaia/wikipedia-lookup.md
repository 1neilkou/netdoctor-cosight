# Wikipedia Lookup Skill

当 step 需要从 Wikipedia 获取精确数据时使用。

## 执行策略
1. 使用 Wikipedia API 而不是网页抓取：
   https://en.wikipedia.org/api/rest_v1/page/summary/{title}
2. 如果涉及列表/集合（成员国、获奖者等），必须使用 Wikipedia 的精确定义：
   - 明确区分"正式成员"和"观察员"
   - 记录 Wikipedia 页面的具体表述，不要用外部来源的定义
3. 如果问题里有 "according to Wikipedia" 的限定，整个 step 只能使用 Wikipedia 来源

## 数据来源记录
完成后在 step_notes 里注明：
"数据来源：Wikipedia - {页面名称}，访问时间：{当前日期}"


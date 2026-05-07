# Academic Paper Access Skill

当 step 需要访问学术论文或学术数据库时使用。

## 执行顺序
1. 优先用 DOI 通过 Unpaywall API 找开放获取版本：
   https://api.unpaywall.org/v2/{doi}?email=agent@cosight.ai
2. 在 Semantic Scholar 搜索：
   https://api.semanticscholar.org/graph/v1/paper/search?query={title}
3. 尝试 Internet Archive：
   https://web.archive.org/web/{original_url}
4. 尝试作者个人主页或机构 repository
5. 如果以上全部失败，从摘要和引用信息推断答案，并明确标注来源不完整

## 禁止行为
- 不要在 Project MUSE、JSTOR、Springer 等付费平台反复重试
- 第一次返回 "Verification required" 或 403 后立即换路径


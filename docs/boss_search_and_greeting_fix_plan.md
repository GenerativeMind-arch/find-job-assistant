# Boss 直聘模块问题诊断与改造方案

> 状态：**三节均已实现**（2026-10-10）。本文档覆盖三个问题：搜索结果数量、结果相关性、投递招呼语自动发送。
> 所有行号基于 2026-10-10 的 `master` 工作区。
>
> **第一节已落地**：
> - `_fetch_jobs_via_xhr` 拆出 `_post_joblist()`；`code != 0` 时**重试 2 次**（退避 8-15s）
>   才放弃；翻页间隔 1.5-3s → **3.5-7s**；读到 `hasMore=false` 提前停止翻页
> - 解析响应里的 `resCount` / `hasMore`，写进 `search_meta`（`pages_fetched` / `res_count` / `has_more`）
> - 前端 `searchSourceText` 改为如实显示「实际成功页数/期望页数 · 共 N 个匹配 · 错误原因」
>
> **第二节已落地**：
> - 新增 `backend/boss/boss_relevance.py`：中英混合分词（中文 2-gram）+ 打分
>   （标题命中权重 3 / JD 命中权重 1）+ 稳定重排 + 可选阈值过滤
> - 接入 `POST /api/boss/jobs/search`（福利筛选之后、落库之前）
> - 设置项：`relevance_rank_enabled`（默认 **true**，只重排不删岗位）/
>   `relevance_filter_enabled`（默认 **false**）/ `relevance_min_score`（默认 3）
> - 实测校准：搜 "AI应用开发" 时 28/30 条满分 12.0，阈值 3 一条都不会误删；
>   而"只在 JD 里提过 AI"的业务员岗位只有 1 分、会被阈值挡掉
>
> **第三节（补充）**：按用户要求，**定制话术已关闭**（`custom_greeting_enabled=false`），
> 投递只发平台招呼语；后续由监控循环在对方回复后自动回复。相关代码保留，随时可再开。
>
> 第三节已根据 2026-10-10 的实测反馈修订（见 3.2），结论与原假设不同。
>
> **已落地**：
> - `boss_automation.py`：新增 `_open_conversation_for_job` 及配套（`_pick_conversation_card` /
>   `_read_open_conversation_header` / `_header_matches` / `_is_card_active` / `_list_conversation_cards` /
>   `_goto_chat_page` / `_clear_chat_input` / `_in_chat_context` / `_chat_input_locator` / `_norm_text`）
> - `send_message`：二次校验失败时清空输入框（残留文字误发的安全网），并修掉"重试后无条件返回 True"
> - `api/boss.py`：新增临时调试端点 `POST /api/boss/debug/open-conversation`（验证通过后删除）
> - `test/boss/test_open_conversation.py`：18 个纯逻辑单测（会话定位 + 定制话术）
>
> **P1b 已落地（2026-10-10）**：
> - `boss_firefox.py`：`__bossApply` 重写为 `__bossStartChat` —— 只点「立即沟通」，
>   删掉死等聊天弹窗 + 逐字输入那整段从未生效的代码（原先每次投递白等 10 秒）
> - `boss_automation.py`：新增 `_send_custom_greeting_for_job` / `_build_custom_greeting`；
>   **删除**旧的 `_send_greeting_via_chat_page`（盲点聊天列表第一个会话 → 可能发错人）
> - `apply_to_job` 重排：HR 名等重要信息在离开详情页**之前**抓取；点沟通 → 落 applied →
>   追加定制话术（可开关）；返回值带 `greeting_sent` / `custom_greeting_need_manual`
> - 设置项：`custom_greeting_enabled`（默认 **false**）/ `custom_greeting_delay_min_sec` /
>   `custom_greeting_delay_max_sec` / `custom_greeting_max_len`，含 `SettingsUpdate` 与设置页 UI
> - `api/boss.py` `/jobs/apply`：开关关闭时不调 LLM 生成话术（省一次无用调用）
>
> **点击会话失败修复（2026-10-10，实测驱动）**：
> 在真实 BOSS 聊天页上做了对照实验，结论：
> | 点击方式 | 结果 |
> |---|---|
> | `locator.click()`（含 `force=True`） | ❌ 卡在 "performing click action" 直到超时 |
> | 页面内派发 `MouseEvent` | ❌ 返回成功但 BOSS 的 React 处理器毫无反应，会话不打开 |
> | `page.mouse.click(x, y)` | ✅ 有效 |
>
> 因此新增 `_click_by_coordinates()`（滚动入视 → 取坐标 → 真实鼠标点击），
> 会话卡片、Tab、聊天输入框全部改走它；删掉了那个"返回成功但什么都不做"的
> JS 派发兜底（`open_conversation_by_name` 里同款）。
>
> 实测确认的 DOM 结构（会话打开后 `.chat-conversation` 的一级子节点）：
> `.top-info-content`（头部，形如「马先生 上海马帮 招聘专员 …」）/ `.message-content` /
> `.message-controls`；未打开时是 `.chat-no-data` 空态 → 新增 `_conversation_is_open()`
> 作为"到底打开了没有"的判据。
> 卡片是 `li`（class 为空），选中的 `selected` 类加在**子元素** `.friend-content` 上
> → `_is_card_active()` 改为连子孙一起查。
>
> **端到端验证**（对真实页面调用 `_open_conversation_for_job`）：正例 2 个打开成功且
> 头部校验通过，负例（不存在的人）正确放弃；聚焦/键入/清空链路验证通过。
>
> **风控误判修复（2026-10-10）**：
> `inspect_page_safety` 在页面开头 600 字里扫关键词判定风控，而 BOSS 岗位详情页
> **常驻**自己的免责声明（"BOSS直聘严禁用人单位和招聘者用户做出任何损害求职者
> 合法权益的违法违规行为……"），里面有"违规" → 每个详情页都被判成 `banned`，
> 投递被锁死 1 小时。
> - 判定前先 `_strip_safety_boilerplate()` 摘掉样板段落（比对时忽略段内空白，
>   并拼接相邻段落以覆盖标记被换行拆开的情况）
> - 命中时打印**关键词 + 页面 URL + 原文片段**（原先只报类别，误判和真封号分不出来）
> - `apply_to_job` 的安全检查失败分支原先**没有任何日志**，已补
> - 新增调试端点 `POST /api/boss/debug/reset-cooldown`（免去排错时白等 1 小时）
> - 回归测试用实测抓到的原文：`test/boss/test_open_conversation.py::test_boilerplate_disclaimer_is_stripped`
>
> **追加修复（2026-10-10，针对"界面显示已沟通、BOSS 上其实没投递"）**：
> - `BossPanel.vue`：`applyJob` 现在检查 `data.success`，失败弹真实原因且不再把按钮刷成「已沟通」
> - `boss_automation.py` `apply_to_job`：只有「立即沟通」真的点下去了才写 `applied`
>   （`__bossApply` 新增 `clicked` 字段区分"点了按钮但没敲进招呼语"与"连按钮都没点上"）；
>   `greeting_sent=False` 时不写 `greeting_text`，返回值如实带 `greeting_sent`
> - `main.py`：新增 `setup_console_log()`，把 stdout/stderr 同时写入 `log/boss.log`
>   （boss 模块整块是 `print()`，原先只进终端、无法回查）

---

## 一、搜索结果只有 15 条

### 1.1 根因

三层限制叠加，本次的实际触发点是第三层。

| 层 | 限制 | 位置 |
|---|---|---|
| BOSS 接口 | 一页固定 15 条，`pageSize` 传多大都没用 | `backend/boss/boss_firefox.py:624-627`（注释即踩坑记录） |
| 本项目 | `SEARCH_MAX_PAGES = 6` → 最多 90 条 | 同上 |
| 前端 | 默认 `limit=60` → 换算 4 页 | `frontend/src/api/index.js:109` |

执行链：

1. `boss_firefox.py:947-955`：`limit=60` → `max_pages=4`
2. `boss_firefox.py:1385-1470`：逐页在浏览器内 `fetch` `joblist.json`
3. `boss_firefox.py:1463-1470`：第 2 页返回 `code=37 您的环境存在异常` → **`break`**（有意为之，否则第 1 页的 15 条会被一起丢掉）→ 最终返回 15 条

日志证据：

```
[搜索] limit=60 → 拉取 4 页（每页 15 条）
[搜索] 第 1 页拿到 15 条
[搜索] 第 2 页返回 code=37 您的环境存在异常.
```

**`code=37` 不是参数错误，是 BOSS 对"同一浏览器环境下连续翻页 XHR"的风控判定。** 特征是第 1 页永远成功、第 2 页起概率性失败。

### 1.2 附带缺陷：前端在骗人

`BossPanel.vue:301-307` 的 `searchSourceText` 只读 `max_pages`，所以只成功 1 页时界面仍显示"来源：接口直取 · 4 页"。后端其实已经在 `search_meta` 里回传了 `xhr_error`（`boss_firefox.py:1014-1023`），前端拿到了却没渲染 —— 这是"完全看不出发生了什么"的直接原因。

### 1.3 改造方案

#### 方案 A：多关键词分轮（推荐，最稳）

每轮都从搜索首页发起、只取第 1 页，从原理上绕开深翻页风控。

- `boss_firefox.search()` 增加多关键词入参：`keywords: list[str]`（保留 `keyword: str` 单值签名兼容 CLI/Agent）
- 每轮：`goto` → 只取第 1 页 → 按 `url` 去重合并
- 轮间间隔受控（建议 8-15s，复用 `random.uniform` 节奏），轮数封顶 4-6 轮
- 前端搜索框支持逗号/换行分隔多关键词；`api/index.js:109` 改为传数组
- 代价：关键词之间会有重复岗位（去重处理）、请求数增加（需限速）

> 与此方案复用同一处代码的副产品：关键词同义扩展（"AI开发" → "AI应用开发"/"大模型开发"），可一并解决第二节的相关性问题。

#### 方案 B：走页面 UI 翻页

不自己拼 `fetch`，改为点击页面底部分页控件让 BOSS 自己发 XHR（自带完整 ztoken），最接近真人。

- 需要**先实测**分页控件的选择器（`a` / `.options-pages` 之类），当前代码里没有
- 需要确认列表页是"页码分页"还是"无限滚动"
- 缺点：选择器脆弱、单轮耗时不确定

#### 方案 C：重试 + 拉长间隔（低成本兜底，建议先做）

- `boss_firefox.py:1388`：翻页间隔 `random.uniform(1.5, 3.0)` → `6-10s`
- `boss_firefox.py:1463`：`code=37` 时先退避 8-15s 重试 1 次，仍失败才 `break`
- 连续 2 次 `code=37` → `_trigger_cooldown(category="rate_limit")`，避免硬撞

#### 方案 D：DOM 无限滚动补齐（**待实测**）

`_scroll_all()`（`boss_firefox.py:1586`）+ `_extract_job_cards()`（`1121`）理论上能拿到滚动加载出来的卡片，但当前只在 XHR **完全无结果**时才走 DOM 兜底（`1037-1045`），且 `search()` 只滚 4 次（`1004`）。

- **需先实测**：滚到底后卡片数是否 > 15？若是，则可在"XHR 拿到的条数 < 期望值"时补一次完整滚动 + DOM 抽取，按 `url` 与 XHR 结果合并去重
- 若列表是虚拟滚动（可见区外 DOM 被回收），此方案无效

#### 前端如实显示（与上面任一方案并行做）

- `BossPanel.vue:301-307`：显示实际成功页数 + 风控原因
  - 正常：`来源：接口直取 · 2/4 页`
  - 被风控：`来源：接口直取 · 1/4 页（第 2 页被风控：code=37）`
- `boss_firefox.py:1014-1023` 的 `meta` 增加 `pages_fetched`（实际成功页数）

### 1.4 待确认 → **已实测结清（2026-10-10，真实页面探针）**

1. **响应里有总数**：`zpData.resCount=450`、`totalCount=300`、`hasMore=true`
   （`zpData` 全部字段：`brandCard / filterString / hasMore / jobList / lid / resCount / totalCount`）
2. **没有排序参数可用**：搜索页只有筛选控件（城市/职位类型/求职类型/薪资待遇/工作经验/
   学历要求/公司行业/公司规模/融资阶段），**没有排序控件** → 相关性只能本地做（见第二节）
3. **`code=37` 与 `ANTI_DETECT` 强相关**——对照实验：
   | 条件 | 结果 |
   |---|---|
   | 不注入 ANTI_DETECT | **第 1 页就 code=37** |
   | 注入 ANTI_DETECT | 第 1/2/3 页全部 code=0，间隔 3s、8s 都成功 |

   结论：应用本来就注入了 `ANTI_DETECT`，原先的 code=37 是**概率性**的（叠加投递/聊天
   等操作后更容易触发）→ 重试 + 拉长间隔即可，不必上多关键词分轮。

---

## 二、搜索结果相关性差

### 2.1 根因

**本项目没有任何本地相关性过滤或重排。** 关键词原样塞进 `joblist.json` 的 `query`，BOSS 返回什么就展示什么。

- `boss_firefox.py:1389-1405`：请求体只有 `query` / `city` / 筛选项，**没有排序参数**
- `api/boss.py:402` `/jobs/search`：对结果只做了一层福利筛选（`boss_firefox.py:1108` `_filter_by_welfare`），没有任何打分或排序

"AI开发" 被 BOSS 分词成 `AI` + `开发` 做宽泛匹配 —— 只要 JD 里出现"AI"就可能命中（例如外贸业务员的 JD 里写了"用 AI 工具做海外获客"），且默认排序不是纯相关度排序。

### 2.2 改造方案

#### 主方案：本地相关性打分

新增 `backend/boss/boss_relevance.py`（纯函数，好测）：

- `score_job(job, keyword_tokens) -> float`
  - 标题命中权重 3（分词后逐 token 累加）
  - JD 命中权重 1
  - 公司名/城市不计入
  - 标题与 JD 都不含任何 token → 0 分
- `rank_jobs(jobs, keywords, min_score)` → 过滤 + 按分数降序，同分保持原序（稳定排序）
- 分词策略：英文/数字按单词，中文按 2-gram；命中判定先做大小写与全半角归一

接入点：`api/boss.py` `/jobs/search`，在 `_filter_by_welfare` **之后**调用 —— 这样 XHR / DOM / heuristic 三种来源都能覆盖。

**默认只降权、不隐藏**（低分岗位排到后面并标灰）。原因见 2.3。

#### 配置项

`boss_state.py` 默认设置块（`246` 行起）新增：

- `relevance_filter_enabled`：默认 `"false"`，先观察打分分布再打开
- `relevance_min_score`：默认 `"1"`

同步改 `api/boss.py:215-233` 的 `SettingsUpdate` 与 `BossPanel.vue` 设置页。

#### 可选增强：LLM 二次打分

对分数处于阈值附近的边界样本，调 deepseek 做相关性判断（复用 `backend/models/llm.py`）。只对边界样本调用以控制成本。**建议等规则版跑出分布后再决定是否加。**

#### 低成本举措（无代码改动）

- UI 提示关键词越具体越准："AI应用开发工程师" 远好于 "AI开发"
- 引导用户使用已有的经验/学历/薪资/规模筛选（`boss_filters.py` 已实现且生效）—— 收紧条件对相关性的改善最直接

> **与第一节的协同**：多关键词分轮（方案 A）与关键词同义扩展是同一处代码。建议合并实现，不要做两遍。

### 2.3 风险

本地过滤会让"结果少"的观感更强 —— 本来就只有 15 条，再过滤会变成个位数。**所以必须先做第一节（把条数提上来），再做第二节（把噪声去掉），且默认只降权不隐藏。**

---

## 三、投递招呼语自动发送

### 3.1 现状：两条路径

| 路径 | 位置 | 行为 |
|---|---|---|
| 主路径 | `boss_firefox.py:236` `window.__bossApply(greeting)` | 原生点击"立即沟通" → 等聊天框 `[contenteditable]` → 逐字输入 → 找"发送"按钮点它 |
| 兜底 | `boss_automation.py:1147-1223` `_send_greeting_via_chat_page` | 导航到聊天页，**盲点列表里的第一个会话**（`1170-1209`）再发送 |

### 3.2 根因（已由实测确认，与原假设不同）

实测证据：

- 后端日志：`📡 JS返回: success=False message=聊天框未出现(10s超时)`
- 手工验证：**系统无法自己打开会话框；一旦会话被打开，系统能正常自动输入并发送**

由这两条可以确定：

**① 主路径的打字逻辑从未生效过。** 点"立即沟通"后当前页面不会出现可用的聊天输入框 → `__bossApply` 每次都在第 4 步超时（`boss_firefox.py:346-358`）→ 永远返回 `fallback_chat_page: true`。`boss_firefox.py:361-441` 那段"逐字输入 + 找发送按钮"是**死代码**，它带来的唯一效果是每次投递凭空多出 10 秒阻塞。

**② 第一条招呼语不需要我们做。** BOSS 平台在点击"立即沟通"时由服务端自动发出平台上设置的招呼语。所以整个投递动作里，我们唯一需要自己实现的只有**第二条定制话术**。

**③ 真正的阻塞点只有一个：自动化打不开正确的会话。** `_send_greeting_via_chat_page` 的"盲点列表第一个会话"就是失败点 —— 它既可能点不中，也可能点中错误的 HR。

#### 顺带发现的更高危行为（建议一并修掉）

`send_message`（`boss_automation.py:1083`）会先把文字敲进输入框再按 Enter 发送，且**失败时不清理输入框**。会话没打开时若输入框存在，文字会残留在框里；之后用户手动点开任意会话，这段残留文字就会被发出去 —— 这与"点击某个会话框时系统会自动输入内容并发送"的观感吻合。

**无论上述机制是否完全命中，结论是一样的：当前实现不只是"发不出去"，还存在把招呼语发给随机 HR 的风险。** 改造必须以"绝不留下残留文字"为前提。

### 3.3 设计变更

放弃"在立即沟通弹出的对话框里打字"这条路，改为：

```
点"立即沟通"（平台发第一条）
    → 落库 applied
    → 等 4-8s
    → 到聊天页精确定位该 HR 的会话（校验身份）
    → 发第二条定制话术
    → 无论成败，清空输入框
```

**"新标签页接管"这条原本要做的工作可以取消** —— 第一条由平台发、第二条走聊天页，`立即沟通` 有没有开新标签都不影响结果。（可选清理：投递结束后关掉 `self._ctx.pages[1:]` 里多出来的空白页。）

### 3.4 改造方案

#### 1) `__bossApply` 瘦身

- 新增 `window.__bossStartChat()`：只做"检查已沟通 / 上限 / 频率 → 找到并原生点击立即沟通 → 返回结果"，**不再等待输入框、不再打字**
- 保留 `window.__bossApply` 作为兼容入口（`boss/agent/tools.py:605` 仍在用），内部改为转调 `__bossStartChat`，原打字段标为 deprecated
- 理由：`361-441` 已确认从未生效，留着只会继续制造 10s 阻塞与残留文字风险

#### 2) 会话定位（本次改动的核心）

新增 `_open_conversation_for_job(app_record) -> bool`，**数据全部取自数据库，不靠 DOM 猜测**：

- `get_application_by_url(job_url)` 已含 `hr_name` / `company` / `job_title`（搜索时由 `boss_firefox.py:1496` 提取，`add_application` 落库，`boss_state.py:309-337`）
- 导航到聊天页 → `_click_chat_tab("新招呼")`（刚投递的会话落在这个 Tab，是最强信号）→ 找不到再回 `全部`
- 定位优先级：**HR 名精确匹配 → 公司名 + 岗位名同时匹配 → 都不中则放弃**
- **打开后必须校验身份**：读取会话头部的 HR 名 / 公司名，与预期值比对；不一致就关掉、不发送
- 把 `open_conversation_by_name`（`boss_automation.py:1010`）扩展为可接受 `company` / `job_title`，并返回是否命中

#### 3) 发送 + 安全网

- 发送后用已有校验：消息区是否出现该文本（`boss_automation.py:1127-1132`）
- **无论成功失败，退出前清空输入框**（Ctrl+A → Backspace），杜绝残留文字被误发 —— 这是本次改造的硬性要求
- 失败时返回 `need_manual` 标记，前端提示"请手动打开与 XX 的会话"

#### 4) `apply_to_job` 编排调整（`boss_automation.py:614`）

- **顺序问题**：现在 `hr_name` 是在发送**之后**才从详情页 DOM 抓的（`762-789`），新流程必须**先**拿到。改为优先读库（`get_application_by_url`），DOM 抓取仅作补充
- 点沟通成功 → 更新 `applied` → 等待 `custom_greeting_delay_min_sec` ~ `max_sec`（默认 4 / 8 秒）→ 生成话术 → 打开会话 → 发送
- 生成话术：复用 `boss_replier.generate_greeting_ai(title, company, hr_name, jd, is_boss, style, resume)`，JD 优先取库里的 `description`，回退当前页 DOM 抓的 `page_desc`（`boss_automation.py:665`）

#### 5) 设置项

| 键 | 默认 | 说明 |
|---|---|---|
| `custom_greeting_enabled` | `"false"` | 第二条开关。**先默认关，手工验证通过后再考虑默认开** |
| `custom_greeting_delay_min_sec` / `_max_sec` | `"4"` / `"8"` | 与平台第一条的间隔；太近易触发风控 |
| `custom_greeting_max_len` | `"120"` | 第二条长度上限 |

改动点：`boss_state.py` 默认设置块（`246` 行起）+ `api/boss.py:215-233` `SettingsUpdate` + `BossPanel.vue` 设置页。

#### 6) 数据字段语义（**实施前必须先定**）

现有 `applications.greeting_text` / `greeting_sent_at`（`boss_state.py:47-48`）目前记录的是"我们自己敲进去的那条"。新流程下第一条由平台发、我们只发第二条，语义会变。

- 建议：`greeting_text` / `greeting_sent_at` 保持"我们发出的那条"语义，即记录定制话术（向后兼容，统计逻辑 `get_today_application_count` 等不受影响）
- 若需要区分，再加 `custom_greeting_text` / `custom_greeting_sent_at`，**不要**改动既有字段含义

#### 7) 风控

- 平台一条 + 我们一条算作一次投递，但第二条失败也要计入 `_trigger_cooldown` 判定
- 第二条失败**不改变** `applied` 状态（简历已投出去），只记日志与字段

#### 8) 前端

`BossPanel.vue`：设置页加开关与间隔；投递结果区分"已投递（平台招呼语） / 定制话术已发 / 定制话术失败需人工"三种状态。

---

## 四、实施顺序建议

| 阶段 | 内容 | 理由 |
|---|---|---|
| **P1a** | `_open_conversation_for_job` + 身份校验 + 清空输入框安全网 | 本次核心。可独立手工验证"能否稳定打开目标会话"，验证通过再往下做 |
| **P1b** | `__bossStartChat` 瘦身 + `apply_to_job` 编排改造 + 定制话术 | 依赖 P1a |
| **P1c** | 设置项 + 前端三态展示 + 字段语义确认（3.4-6） | 收尾 |
| **P2** | 第一节：方案 C（重试+拉长间隔）+ 前端如实显示 | 低成本，立刻消除"看不出发生了什么" |
| **P3** | 第一节：方案 A（多关键词分轮） | 从原理上解决条数上限 |
| **P4** | 第二节：相关性降权（默认不隐藏） | 依赖 P3 先把条数提上来 |

方案 B / D（UI 翻页、DOM 滚动补齐）与"新标签页接管"（已取消）都**依赖实测结论**，不排在默认路径里。

---

## 五、验证与回归

`backend/test/boss/` 下已有 `test_boss_manager.py` / `test_boss_replier.py` / `test_boss_send_message.py` / `test_chat_monitor_loop.py`，在其上扩展：

- 相关性打分：纯函数单测（标题命中 / 仅 JD 命中 / 都不命中 / 同分稳定排序）
- `_open_conversation_for_job`：HR 名匹配不到、公司+岗位也匹配不到时，必须返回 False 且**不产生任何发送动作**
- 身份校验：会话打开后头部信息与预期不符时，必须放弃发送
- 安全网：任何失败分支退出后，聊天输入框必须为空
- `apply_to_job`：`custom_greeting_enabled=false` 时不发第二条；点沟通失败时不发第二条
- `meta` 的 `pages_fetched` 在 `code=37` 时的取值

**P1a 需要一个手工验证脚本/步骤**：投递一个岗位 → 检查是否打开了正确的会话（而不是随机的第一个）→ 检查输入框是否为空。

---

## 六、风险清单

1. **发错人（最高危）**：现有兜底会把招呼语发给随机 HR，且残留文字会在用户后续操作时被误发。改造以"宁可不发"为原则，会降低自动化成功率 —— 这是有意取舍。
2. **风控**：多关键词分轮与追发第二条都会提高请求/消息密度。必须限速，且第二条默认关闭。
3. **相关性过滤**：会放大"结果少"的观感，所以默认只降权不隐藏，且必须在第一节之后做。
4. **实测依赖**：方案 B / D 以实测结论为前提，不做无依据的实现。

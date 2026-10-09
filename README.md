# Research Radar

把论文发现与团队研究接起来的两个 agent skills，以及可独立验证的本地 Python 工具。

`paper-sweep` 抓取 arXiv / Hugging Face 论文，保留完整摘要和作者，生成阅读清单。`talent-scout` 让宿主模型核对研究者、机构、公司及融资证据，再由脚本校验、评分和输出团队雷达。两者共享同一份结构化论文数据。

**运行边界：**论文采集、去重、校验、评分和报告生成由 Python 执行；语义判断和联网查证由能浏览网页的宿主模型或研究者完成。CLI 不内置 LLM API，也不会自动把未调查的人判成创业者。无需额外模型 API key。定时执行需要宿主提供调度与研究能力，本项目不会默认创建定时任务。

## 安装

需要 Python 3.9+，唯一运行依赖是 PyYAML。以下命令在仓库根目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
research-radar doctor
```

Windows 使用 `.venv\Scripts\activate` 激活虚拟环境。所有 CLI 命令也可以写成 `python -m research_radar ...`。配置随 Python 包一起安装；换一个工作目录运行也不依赖原作者的文件路径。

## 先跑离线全流程

```bash
research-radar demo --data-dir demo-output
```

demo 全部使用标为 **SYNTHETIC** 的合成论文、身份和证据，在不联网的情况下执行：导入论文 → 准备研究包 → 填入合成评审 → 校验证据 → 计算分数 → 团队表与档案。输出的 `demo-manifest.json` 列出所有产物。

示例仅验证软件流程，不代表真实论文质量或团队投资价值。

演示只会覆盖能确认由当前演示生成且未被编辑的评审。旧版演示目录或已编辑过的文件请保留，改用新的 `--data-dir` 运行。

## 抓取真实论文

```bash
research-radar sweep --source both --days 2 --data-dir data
research-radar sweep --source arxiv --days 7 --cluster World-Models --all --data-dir data
research-radar sweep --source hf --date 2026-09-18 --data-dir data
```

默认窗口结束日为 **UTC 昨天**。arXiv 以提交日期查询；HF 以精选日期查询，同时保留论文提交日期和 `hf_featured_dates`。所以“新提交”和“新受到关注”不会混成同一含义。

机器输出保留所有采集记录，包括未命中关键词的论文；`--cluster`、`--top`、`--all` 控制 Markdown 阅读视图。prior 只用于粗略排序，不作为论文质量或 SOTA 结论。作者、摘要和机构关系不会为适应日报长度而裁剪。

arXiv 论文保留可选 `arxiv_version`（正整数或 `null`）；旧状态文件无需手动迁移。明确的 `external:`、`doi:` 等外部编号保留原命名空间。导入时，若 arXiv ID、URL 或版本相互矛盾，该条记录会报告错误。

命令打印一个 JSON 摘要，包含当前运行 ID、状态、数量和产物路径。每次运行的核心产物为：

```text
data/
  runs/<run_id>/
    papers.json       # 完整论文包
    run.json          # 窗口、来源状态、错误及完整性
    report.md         # 论文阅读视图
```

具体文件以命令返回的 `paths` 为准。同一个 `--data-dir` 保存跨运行状态，区分 new / updated / seen。达到采集上限会标记截断；部分失败不能被当作完整覆盖。`--out` 可另指定 Markdown 路径，机器产物仍留在 data 目录。

同一个 `--data-dir` 请只运行一个采集进程。产物逐个原子写入，`run.json` 最后提交；当前不支持多个进程同时修改共享状态。

| 退出码 | 含义 |
|---|---|
| 0 | 指定来源正常完成；零条结果也可能是正常情况 |
| 1 | 采集失败或运行错误，查看来源错误记录 |
| 2 | 部分采集失败/截断，或参数校验失败；查看返回的状态区分 |

要缩小联网验收范围，可使用自定义类别配置和 `--max-results`。不要把受限样本报告当成某一领域的完整市场地图。

## 从论文走到团队

取上一步返回的真实论文文件路径，替换下面的 `RUN_ID`：

```bash
research-radar scout prepare --papers data/runs/RUN_ID/papers.json --out data/review.json
```

让宿主执行 [talent-scout](talent-scout/SKILL.md)，读取完整论文、核对公开来源并填写 `review.json`。`prepare` 默认为每篇论文创建一条待评审记录；加 `--per-author` 则为每位署名作者各建一条，用于调查整个团队。`--limit N` 可限制本次研究预算，并明确列出省略的论文。空模板保持 `needs_review`，不能直接冒充完成的调查。

已有评审文件默认拒绝覆盖。继续调查时直接编辑原来的 `review.json`；为新的采集批次指定新的 `--out` 路径。只有决定丢弃旧评审、重新生成空模板时才加 `--overwrite`，且输出不能与输入论文包是同一个文件。

研究包将人物、机构、公司、人物与公司的关系、融资阶段分开记录。证据需要 URL、检查日期、来源日期（可未知）、适用实体 ID 及支持的具体事实。无法访问、未找到和身份不明确都有独立状态；没有融资信息不等于未融资。

确认人物身份时，`person.name` 必须与论文署名中的某位作者一致（忽略大小写、标点和姓名顺序）；公开姓名与署名拼写不同时，把署名写法填在 `person.listed_name`。`person.evidence_ids` 还须至少引用一条同时绑定该人物和该论文的证据，例如论文作者栏或列出该论文的作者主页。

团队分项需要论文之外的来源（作者主页、机构、公司、数据库或新闻）；联系可达性需要作者主页、机构或公司页面。真实评审中，`example.org`、`*.invalid`、`localhost`、内网 IP 等占位或私有地址会被拒绝。标为 complete 的候选必须填写 `open_questions` 列表，没有未决问题时显式写 `[]`。

`as_of` 是研究截止日，证据检查日期不能晚于它，来源日期也不能晚于检查日期。隔天继续调查时先运行：

```bash
research-radar scout refresh --review data/review.json
```

它把 `as_of` 推进到今天并在 `as_of_history` 中保留旧值。不要回填虚假的检查日期。0.1.x 的评审需先升级（保留 `.v1-backup` 原文件，已完成的候选会重新打开待确认）：

```bash
research-radar scout migrate --review data/review.json
```

填写后运行：

```bash
research-radar scout render --review data/review.json --data-dir data --top 10
```

输出包括中文团队表、完整证据与评分审计 JSON、前三名研究者档案。自动生成的档案放在 `Researchers/generated/` 下，避免覆盖人工研究笔记。程序验证引用、实体对应与字段完整性；来源是否真的支持结论仍需研究者审读。

重跑同一批次会更新报告，并清理该批次已经不在输出名单中的工具生成档案。普通写入或替换失败时会回滚；不保证进程被强制终止或断电时的多文件事务。人工笔记应保存在 `Researchers/generated/` 之外。

不联网查看完整填写格式：

```bash
research-radar scout render --review examples/synthetic_review.json --data-dir demo-output
```

## 配置与评分

默认配置在 `research_radar/resources/`。可修改两个 skill 旁的配置副本，并显式传入：

```bash
research-radar sweep --config paper-sweep/clusters.yml --data-dir data
research-radar scout render --review data/review.json --profile talent-scout/scout_rubric.yml --data-dir data
```

四项评审分数为 0–5，默认权重：技术证据 45%、团队证据 25%、策略匹配 20%、联系可达性 10%。总分为：

```text
min(10, 10 × Σ(权重 × 分项分数 / 5) × 阶段系数 × (1 + 可见度加成))
```

未知或无法归属的融资阶段使用明确的 0.75 系数，不享受低可见度加成。查实的非排除阶段系数不得低于未知阶段（B 轮默认 0.75），否则“不查融资”会比“查了融资”得分更高；配置违反此规则时 `doctor` 和 `render` 会报错。只有已确认的创始人/联合创始人关系和中高置信度融资证据，才允许使用公司阶段或按 C 轮以上/上市/全资收购规则排除。雇员、顾问和研究合作者不会借用雇主的轮次。缺失的技术/团队评审不会自动填零或虚构分数。

`prepare` 固定研究包的 `as_of` 日期，重复渲染使用该日期评估时效。默认只使用该日期前 365 天内的融资证据；缺日期、未来日期或过期证据均不参与阶段判断。可在配置中调整 `financing_max_age_days`。这是一项保守的研究规则，旧公告不证明当前仍处于同一轮次；重新调查时应更新证据及 `as_of`。

这些权重是可复现的初始研究规则，尚未经过真实投资效果校准。摘要层面的兴趣分与阅读全文后的技术分相互独立。

## 作为 skills 使用

两个入口保留在 `paper-sweep/` 和 `talent-scout/`。先安装上面的 Python 包，再把它们复制到宿主的 skills 目录。例如：

```bash
python scripts/install_skills.py --target ~/.codex/skills
```

安装器默认不覆盖已有同名 skill；明确更新时加 `--replace`。覆盖前会检查源与目标是否重叠，并先暂存两个完整 skill；更新失败时保留或恢复原有安装。其他宿主可以指定自己的 skills 目录。宿主执行命令时必须使用已安装本项目的 Python 环境；可使用本项目 `.venv/bin/python` 的实际绝对路径。不要把开发者机器路径写进共享 skill。

## 会议与其他来源

当前会议接口支持**结构化 JSON 导入**，并不声称自动抓取任意会议的 oral 名单：

```bash
research-radar sweep --source file --input examples/synthetic_papers.json --data-dir data
```

输入接受论文数组或包含 `papers` 的包。参考 `examples/synthetic_papers.json`；至少提供稳定 ID、标题、摘要、作者与来源 URL。来源不同不会在导入后伪装成新抓取的 arXiv 数据。

## 验证与开发

```bash
python -m unittest discover -s tests -v
python -m research_radar demo --data-dir demo-output
python -m pip wheel --no-deps . --wheel-dir dist
```

推送 `vX.Y.Z` 标签会触发发布流程：版本号、包版本和 CHANGELOG 段落必须一致，已存在的 release 不会被替换。演示与验收步骤见 [docs/演示runbook.md](docs/演示runbook.md)。

GitHub Actions 在 Python 3.9 和 3.12 上执行离线测试、完整 demo，并验证安装后从其他目录仍可读取包内配置。测试覆盖字段完整性、来源失败、重跑去重、评分边界、错误公司关联、缺失证据和未知融资。

运行数据、研究者档案、虚拟环境和本地审查材料默认不进入 Git。公开仓库中的案例为合成数据。实际联网服务可能限流或暂时不可用，应按运行记录处理，不能用离线 demo 替代线上状态说明。

来源接口：[arXiv API](https://info.arxiv.org/help/api/user-manual.html)、[Hugging Face Daily Papers](https://huggingface.co/papers)。

版本变更见 [CHANGELOG.md](CHANGELOG.md)。

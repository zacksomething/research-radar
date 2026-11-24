# Research Radar 演示与验收

在项目根目录激活已安装的虚拟环境，然后执行：

```bash
python -m research_radar doctor
python -m research_radar demo --data-dir demo-output
```

demo 使用明确标注的合成论文、研究者、公司和证据链接，离线跑通论文导入、候选准备、已填写研究包校验、评分及报告生成。它验证软件流程，不代表已经完成真实团队调查。

联网验收另执行：

```bash
python -m research_radar sweep --source both --days 2 --data-dir data
```

读取命令返回的路径与状态，用返回的 papers.json 运行 `scout prepare`（可按 `--cluster` 聚焦赛道，或需要调查整支团队时组合 `--paper-id` 与 `--per-author`），按[统一 skill](../research-radar/SKILL.md) 及[证据协议](../research-radar/references/research-protocol.md) 完成真实来源调查后先执行 `scout validate` 校验，再运行 `scout render`。隔天补充调查前先运行 `scout refresh` 推进截止日。没有填写证据时，候选应保持 `needs_review`；抓取失败或截断应明确显示。只运行 `sweep` 不等于完成团队调查。

检查产物中的完整摘要、作者、来源状态、重复记录状态、团队评分分项和融资证据。重跑同一研究包不应覆盖人工笔记；未确认的公司关系不能借用同名公司的融资轮次。发布演示时使用合成样例，不把本地研究档案或个人联系资料打包进源码。

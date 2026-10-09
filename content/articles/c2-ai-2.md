# NISE零样本设计药物结合蛋白，APEX体外亲和力达到80 pM

![卡片图](c2-ai-2_card_r4c.png)

**在exatecan与apixaban体外任务中，NISE闭环神经网络从头设计结合蛋白并完成亲和力验证；APEX对apixaban的Kd为80 pM。**

主领域：AI 药物设计　相关：小分子结合蛋白 / 蛋白质设计 / LASErMPNN / Boltz-2 / 解离常数 / 神经网络闭环

## 关键数据卡

- **研究类型**：Nature全文；体外验证的零样本小分子结合蛋白设计研究
- **样本量 n**：exatecan：4个NISE设计、16个COMBS对照；apixaban：6个NISE设计。
- **对照**：HSA、COMBS–Rosetta、energy-based ISE；APEX特异性对照含50 nM exatecan。
- **干预/剂量**：NISE闭环；exatecan每轮top 3选择并各采样1,000条序列，apixaban用50个NTF2折叠和14–28轮轨迹。
- **随访**：水解保护吸收实验至少50 h。
- **主要终点**：临床前方法研究未声明预设主要终点；主要读出为体外结合成功率、Kd亲和力、以及exatecan闭环保留。
- **主要终点结果**：exatecan 100%结合、Kd 0.12–17 µM；apixaban 5/6结合，APEX Kd=80 pM（95% CI 54–122 pM）；EPIC(Q51N/M97L) Kd=1.2 nM，>99%闭环至少50 h。
- **统计量**：Kd多由1,000次bootstrap残差拟合给出区间；APEX Kd报告95% CI。
- **安全性**：SEC显示所选设计或突变蛋白为单体。
- **证据等级**：全文
- **核对记录**：读了Europe PMC全文XML PMC13441969的Abstract、Results、Discussion、图注和Methods占位。

![机制示意图](c2-ai-2_mech_r4c.png)

1. 放置药物并筛选口袋
2. LASErMPNN扩展序列
3. 共结构预测选择自洽体
4. 表达纯化后测定Kd
5. 验证特异性与水解保护

> 图示NISE从可设计骨架和小分子姿态出发，经LASErMPNN与共结构预测器反复选择、扩展，得到体外验证的小分子结合蛋白；标签列出文中主要结合与化学保护读出。示意图由 AI 生成，依据原文结果绘制，非期刊原图，不代表分子比例

## 研究背景与待解问题

从头设计能抓住小分子的蛋白，需要同时匹配序列、骨架和配体构象。以往成功多依赖高通量筛选，或把配体官能团简化成类似氨基酸的片段；一旦对象换成真实药物，搜索空间和化学表征都会放大。

Fry等在Nature提出的切口，是把“序列给定结构”和“结构给定序列”的神经网络接成闭环。NISE不先靠能量函数定稿，而在每轮保留蛋白骨架与配体姿态都自洽的候选，再扩展序列，目标是零样本得到可表达、可结合的药物结合蛋白。

## 研究设计

exatecan搜索先从40个AF2预测的四螺旋束库出发，每个结构至少有8条序列通过置信阈值。作者先用COMBS把exatecan构象放入口袋，再以一个未实验表征的COMBS–Rosetta模型去掉序列后作为NISE输入。exatecan的NISE每轮按配体pLDDT选择top 3自洽结构，再为每个结构采样1,000条LASErMPNN序列。

apixaban任务改用50个计算生成的NTF2折叠、刚体对接，并运行14–28轮NISE轨迹。该任务把共结构预测器换成Boltz-2，并以配体pLDDT和P(bind)复合评分排序。研究不是组间效能设计；NISE、COMBS、历史LigandMPNN/Rosetta与HSA对照主要用于方法学比较，成功率差异未作正式假设检验。

## 核心结果

### 核心读出

摘要层面，NISE在exatecan和apixaban上的结合成功率分别为100%和83%，最紧结合体相对下一领先方法分别约70倍和近10,000倍。正文随后用荧光偏振、竞争结合和吸收光谱验证候选，读出集中在Kd、结合比例和exatecan闭环状态。这些读出覆盖不同折叠和化学性质不同的药物。

### exatecan结合

exatecan实验中，4个NISE设计全部结合，Kd为0.12–17 µM，其中3个低于10 µM；非特异性HSA为43 µM。对照的16个COMBS设计中只有3个结合exatecan，Kd分别为8、12和44 µM。EPIC作为最紧NISE结合体，也比HSA更紧。

### 闭环对照

算法对照显示，NISE而非energy-based ISE同时提高配体pLDDT并降低序列NLL，图注四分位数来自每轮n=1,500个设计，第一轮为n=500。传统能量最小化循环未让NLL下降，也未提高配体pLDDT，支持“神经网络闭环”而非单纯筛选是关键。

### apixaban亲和力

apixaban任务的头部结果是APEX：Kd=80 pM，95% CI为54–122 pM；factor Xa的Ki为80–700 pM，分子量13 kDa对43 kDa。6个入选设计中5个结合apixaban，且Kd均低于50 nM。特异性读出也有图例限制：APEX在50 nM exatecan下无可观结合，连接线不是拟合。

### 校对与保护

LASErMPNN校对给出的EPIC(Q51N/M97L)把亲和力提高100倍，Kd=1.2 nM，ΔΔG=−2.7 kcal mol−1。单突变EPIC(Q51N)和EPIC(M97L)的Kd分别为8.0 nM和7.4 nM。在PBS pH 7.4中，EPIC(Q51N/M97L)使超过99%的exatecan至少50 h保持闭环，且未见主要水解产物。相比之下，游离exatecan在血浆中的水解半衰期约2 h。

## 机制解读

**原文实验证明：**计算链条直接显示，NISE在同一exatecan输入上让配体pLDDT升高、序列NLL降低；energy-based ISE没有同步改善。实验链条随后对应到体外Kd：NISE的4个exatecan设计全结合，而COMBS对照16个中只有3个结合。

结构层面，EPIC晶体分辨率为2.0 Å，EPIC(Q51N)为2.2 Å。EPIC骨架与NISE上游输入接近，Cα r.m.s.d.为0.8 Å；Q51N较短侧链让exatecan约0.5 Å更深入口袋，并形成双齿氢键，解释亲和力提升的结构基础。

**作者推测：**作者推测，NISE是在P(sequence, structure, ligand conformation)的联合分布中爬向高概率模态；这种说法来自互补条件分布和模型置信度的变化，不等同于直接观测到真实设计能量地形。作者还提出，未来可把正向pLDDT和脱靶pLDDT差距结合做负向设计。

## 局限与不确定

- 外推性有限：实验目标集中在exatecan和apixaban，骨架也限于四螺旋束与NTF2。虽然成功率为100%和83%，这些结果不能自动外推到原文未测试的其他药物和骨架。
- 样本量仍小：真正订购并测试的NISE候选为exatecan 4个、apixaban 6个；COMBS对照为16个。Kd区间虽清楚，但这些不是为比较成功率而设计效能的随机实验。
- 终点停留在体外：水解保护最长展示至少50 h，并在PBS或HSA存在下完成；文章没有给出动物药代、免疫原性、组织分布或释放动力学数据。
- 模型选择仍是局限：作者写明，若用RFAA评估会丢弃apixaban结合蛋白，Boltz-2对实验选择很重要。APEX脱靶读出还限于50 nM exatecan且连接线不是拟合，不能替代系统脱靶谱。

## 临床/产业意义

如果这种闭环在更多药物上保持稳定，NISE会把小分子结合蛋白设计从大库筛选推向少量候选的计算优先流程。对药物递送或药物清除概念，本文最直接的价值是能做出高亲和“海绵”或保护蛋白，而不是已经证明体内疗效。

产业上，结果提示LASErMPNN、RFAA或Boltz-2一类模型的组合可服务于药物载荷保护、传感和催化前体发现；但转化判断必须以表达、稳定性、选择性和体内暴露共同成立为条件。

## 作者、出处与核对

Fry B, Slaw K, Polizzi NF. Zero-shot design of drug-binding proteins via neural iterative selection−expansion. Nature. 2026 Jun 24. https://doi.org/10.1038/s41586-026-10670-w

证据等级：全文；核对记录：读了Europe PMC全文XML PMC13441969的Abstract、Results、Discussion、图注和Methods占位。

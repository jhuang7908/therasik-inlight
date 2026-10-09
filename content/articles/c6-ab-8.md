# 三特异IL-2R抗体αβγVHH-48的EC50达0.015 nM

![卡片图](c6-ab-8_card.webp)

**Lykhopiy等把抗CD25/CD122/CD132的VHH装成三特异激动抗体，先导αβγVHH-48在HEKαβγ上的pSTAT5 EC50为0.015 nM，接近人IL-2的0.010 nM；再做成CD25二价并改几何后，可在皮摩尔甚至飞摩尔级偏向性激活Treg。**

主领域：抗体工程　相关：自身免疫与移植免疫

## 关键数据卡

- **研究类型**：临床前多特异性抗体工程与Treg偏向性IL-2R激动研究（Nature Communications）
- **样本量 n**：羊驼2只；独特VHH克隆CD25 85、CD122 153、CD132 92；βγ双抗16个；人PBMC供者最多12人；体内每组n=4–5
- **对照**：亲本βγ双抗、重组人IL-2、Fc融合IL-2 mutein V91K、低剂量IL-2、PBS；NK/Tconv/CD8作为非Treg对照
- **干预/剂量**：LALAPG IgG1 Fc、cFAE组装三特异VHH；体内NSG人PBMC模型腹腔0.03 µg，第1、8天给药
- **主要终点**：pSTAT5效价/效能；Treg相对非Treg的ΔAUC；体内Treg频率与比值
- **主要终点结果**：αβγVHH-48在HEKαβγ上EC50=0.015 nM（人IL-2为0.010 nM）；几何变体DC41/DC44在Treg上达个位数皮摩尔或飞摩尔，非Treg需高100–1000倍
- **统计量**：四参数或三参数量效拟合求EC50；单/双因素ANOVA（Tukey）；Kaplan-Meier用双侧log-rank
- **安全性**：低剂量IL-2同时扩效应T细胞并加速GvHD；抗体在高浓度下仍可能触及CD25低/阴性细胞
- **证据等级**：全文
- **核对记录**：Europe PMC全文XML（PMC13482293）：Abstract、Results、Methods、Discussion及Fig. 1–5图注

![机制示意图](c6-ab-8_mech.webp)

1. VHH覆盖IL-2R三个亚基
2. βγ交联触发pSTAT5
3. CD25臂把效价偏向Treg
4. 二价CD25与几何再优化

> 示意图概括抗体如何从βγ双抗升级为αβγ三抗：CD122与CD132的VHH负责成信号，CD25 VHH决定细胞选择性和剂量敏感；再加第二个CD25结构域并改空间排列后，Treg与非Treg窗口进一步拉开。示意图由 AI 生成，依据原文结果绘制，非期刊原图，不代表分子比例。

## 研究背景与待解问题

调节性T细胞（Treg）依赖IL-2维持耐受。中等亲和IL-2R由CD122和CD132组成（原文给出Kd约1 nM），广泛见于记忆CD8和NK；加上CD25后形成高亲和三聚体（原文给出Kd约10 pM），Treg组成性高表达CD25。低剂量IL-2虽能扩Treg，但半衰期短，也容易打到非Treg。已有的IL-2突变体、PEG化和抗体复合物都在选择性与信号强度之间折中。

Lykhopiy（兼通讯）与Van Rompaey、Schlenner团队的假设是：与其改细胞因子，不如用抗体同时抓住三个受体亚基，做成必须交联才工作的激动剂，让CD25把活性锁在Treg上。

## 研究设计

两只羊驼用编码人IL-2Rα/β/γ的质粒DNA免疫，共6针、间隔2周。噬菌体三轮筛选后，按独特VHH计，得到抗CD25 85个（15个CDR3家族）、抗CD122 153个（38个家族）、抗CD132 92个（7个家族）；SPR表位分箱分别为3、4、5个。每亚基取10–13个克隆做成VHH-Fc；β和γ各取表位代表做成16个βγ双抗。Fc带LALAPG消除效应功能，并以F405L/K409R做可控Fab臂交换，把αVHH与βγVHH重组成三抗。

功能先在过表达三亚基的HEKαβγ上看结合和pSTAT5，再转到静息人PBMC，比较Treg（CD25高）与NK、Tconv、CD8、NKT。体内把人PBMC（每鼠20×10^6）输入NSG，第1、8天腹腔给0.03 µg或PBS。随后保持特异性和价态不变，只重排VHH空间位置，做成5个二价CD25的几何变体（DC41、DC43、DC44、DC45、DC60）。

## 核心结果

### 只有同时抓住β和γ才出信号

单抗或α与β、α与γ的组合都不能诱导pSTAT5；多数同时含β、γ VHH的分子可以，部分βγ双抗的最大信号接近重组IL-2。无功能的γVHH-35例外。结合弱但信号强的βγ克隆说明，效价不简单等于亲和。三抗的细胞结合主要由αVHH决定，pSTAT5最大信号却跟着亲本βγ走：CD25管谁被打中、剂量敏感；βγ管信号能不能装起来、有多强。

### αVHH-2把效价普遍推向CD25阳性细胞

加上抗CD25后，相对亲本βγ的EC50倍数改善在含βγVHH-11的三抗中最大；αVHH-2则对所有βγ骨架给出最大倍数改善。据此选定αβγVHH-48（αVHH-2、βVHH-19、γVHH-27），HEKαβγ上EC50为0.015 nM，人IL-2为0.010 nM。在PBMC上，打IL-2结合位点的高亲和CD25 VHH（αVHH-2、8、10）对Treg最敏感；低亲和或非IL-2表位则弱。用整合效价与最大反应的ΔAUC看，αβγVHH-48和αβγVHH-61的Treg/非Treg比值最高，优于其他三抗和IL-2。讨论汇总称，带抗CD25的三抗在体外对Treg的效价比IL-2高逾100倍，对CD25阴性细胞激活很少。纯化Treg培养7天，αβγVHH-48扩增少于IL-2但强于βγVHH-11；在全PBMC里，两条三抗的Treg增殖接近IL-2，βγ双抗几乎不扩Treg。Treg增殖指数高于βγVHH-11（Kruskal-Wallis，调整P=0.0339）。

### 0.03 µg在NSG模型中拉开Treg比值

剂量滴定把第1天最优剂量定为0.03 µg。αβγVHH-48提高Treg频率，不改变Tconv或CD8频率，从而显著提高Treg/CD4和Treg/CD8比值；Ki-67+ Treg增加，而非Treg的Ki-67在βγVHH-11组更高。FOXP3保持稳定。与低剂量IL-2相比，后者第7天虽扩Treg，也扩效应T细胞并加速疾病；IL-2 mutein、αβγVHH-48和后续DC41在Treg特异性和疾病结局上相当。作者指出该CD25 VHH与IL-2竞争结合，可能削弱Treg靠吸走IL-2来抑制的能力。

### 几何比序列更决定窗口

五个几何变体全部比原αβγVHH-48更强，符合CD25二价。最大pSTAT5随β、γ VHH相对位置而变。DC41、DC44在Treg上于个位数皮摩尔甚至飞摩尔达最大磷酸化，非Treg要高100–1000倍才出现信号；DC43、DC45、DC60更像部分激动，Treg与非Treg差别最大。因部分曲线拟合不出可靠EC50，作者用实验浓度范围内的ΔAUC（不做外推，因而低估差异）比较，仍是DC41和DC44的Treg–非Treg窗口最大。纯化Treg扩增上，DC41与αβγVHH-48相当；V91K mutein虽有选择性磷酸化，单细胞MFI太低，带不动增殖。SPR在单个亚基上看不出几何之间的结合差异，功能差来自细胞表面组装，而不是单受体位阻。

## 机制解读

**原文实验证明：**βγ必须同时被抓住才有pSTAT5，单独αβ或αγ不够，这与经典IL-2R组装一致。CD25结构域改变的是EC50而不是最大信号，βγ决定Emax。PBMC和NSG实验把这种效价差转化成Treg相对非Treg的数量优势。几何变体在组成、接头、价态相同的情况下功能仍分叉，且单亚基SPR无差异，直接支持“空间构型决定信号”。

**作者推测：**与IL-2竞争的CD25 VHH可能把受体拉成更接近天然IL-2占据的姿态，所以功能更好；代价是Treg少了吸IL-2这条抑制路径。高浓度下增殖回落被解释为过密体系里的非生产性占据，而不是内在激动变弱。讨论还提出，可再加Treg富集表面分子做成四特异，进一步收窄细胞谱。这些是设计推论，不是疾病模型里的疗效证明。

## 局限与不确定

- 绝大多数VHH不交叉小鼠IL-2R，疾病模型必须用人源化IL-2/IL-2R体系，本文体内只做到人PBMC-NSG/NOG扩增和GvHD评分，没有自身免疫疾病模型。
- HEK报告细胞的受体密度非生理；作者因此把结论重点放在PBMC。高暴露下仍不能完全排除效应细胞被激活，这是Treg偏向性IL-2类药物的共同窗口问题。
- 主要读出是STAT5；PI3K-AKT、MAPK、mTOR等对Treg代谢和稳定性的贡献未测。体内扩增Treg的抑制功能和谱系稳定性也未做深。αβγVHH-48的CD25臂与IL-2竞争，可能削弱IL-2剥夺型抑制。

## 临床/产业意义

相对减弱CD122结合的IL-2 mutein，这条路径是用CD25亲和力和几何去“瞄准”而不是把细胞因子本身做残。抗体半衰期也不同于野生型IL-2。若后续人源化模型和早期临床站住，这类分子适合需要扩Treg的自身免疫和移植排斥，但治疗窗仍取决于剂量：过低不够，过高会碰到CD25低的细胞。平台模块化，理论上可换成非竞争CD25 VHH，或再叠第四个Treg标志。

## 作者、出处与核对

Lykhopiy V, Pollenus E, Rangan L, Stakenborg M, Tezil T, Varheust M, 等. Engineering trispecific IL-2 receptor agonistic antibodies through geometry optimization for enhanced Treg targeting. Nat Commun. 2026 Jul 10;17:8534. doi: https://doi.org/10.1038/s41467-026-75024-6

第一兼共同通讯：Valentina Lykhopiy；末位：Luc Van Rompaey、Susan M Schlenner（通讯）。Pollenus、Rangan、Van Rompaey、Schlenner并列贡献。证据等级：全文；核对记录：Europe PMC全文XML（PMC13482293）：Abstract、Results、Methods、Discussion及Fig. 1–5图注。许可为CC BY-NC-ND，正文为改写，未逐句抄录。

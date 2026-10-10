# 原文溯源审计（go-live 前门禁）

对照物是**原始论文**（Europe PMC fullTextXML / 出版商 OA HTML / 全文不可得时用摘要），不是本站正文。
速览中每一个定量数字都要求：实验、物种/人群、剂量、单位、时点、n、比较对象与原句一致。对不上则 FIX 或删除。
作者介绍：通讯作者只认论文 correspondence / ✉；单位只认论文 affiliation list；实验室方向只认已核官方实验室页。个人邮箱与“未核到/单位来自作者栏”类元注释一律删除，不改写为说明。

**本轮全文来源**

| 来源 | 篇数 | 文章 |
| --- | ---: | --- |
| Europe PMC fullTextXML | 25 | 见各节 PMCID |
| 出版商 OA HTML | 8 | c9-rna-5, c7-cell-2, c2-ai-1, c2-ai-4, c3-io-4, c5-am-5, c1-org-4, c1-org-5 |
| bioRxiv 摘要 | 2 | c6-ab-4, c6-ab-5（本轮 JATS 429） |
| Europe PMC 摘要 + 既有 OA HTML source_quote | 1 | a-trap（本轮 Cell Press 403） |

**计数（终稿速览）**

- 速览定量数字核对：**585** 条（36 篇终稿速览；不含基因名内嵌如 GPR15/CD19 的独立计数，但表中仍列出速览里出现的阿拉伯数字）
- 终稿 MATCH：585
- 速览数字 FIX：12 篇（见文末 before/after；错挂论文或无法追溯的数字已改写/删除）
- 作者介绍 FIX：18 处（见文末）
- 正文 / 关键数据卡数字 FIX：0（GS-986、血肌酐、HMPV-317、Tc17、GATOR1、1.0±0.1 等错误只出现在被改写的速览，未进入解读正文/数据卡）
- 不可复核：a-trap 全文、c6-ab-4 逐项 ρ、c6-ab-5 非摘要数字（已从速览删除未追溯项）

---

## 速览数字（article id · number · 原句 · 位置 · 判定）


### c6-ab-9 · PMCPMC13644599

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 14 | R VHH接到抗血凝素IgG上。14只食蟹猴单次静脉5 mg/kg，双抗血清 | pmc MAbs MAbs 934 mabs 101479829 mAbs 1942-0862 1942-0870 Taylor & Francis PMC13644599 PMC1364459 | MATCH |
| 5 | 素IgG上。14只食蟹猴单次静脉5 mg/kg，双抗血清半衰期1.57天（ | mAbs 1942-0862 1942-0870 Taylor & Francis PMC13644599 PMC13644599.1 13644599 13644599 42829333 10.1080/19420862.2026.2743 | MATCH |
| 1.57 | 脉5 mg/kg，双抗血清半衰期1.57天（对照9.53天），支气管肺泡灌洗液总 | 78 (0.4) 0.04 (0.05) 99.46 (35.2) FM1B104-serum 4 1.57 (0.5) 150.91 (67.4) 0.21 (0.02)_ 5,933.29 (2189) 54.8 (37) 0.9 (0.3) | MATCH |
| 9.53 | ，双抗血清半衰期1.57天（对照9.53天），支气管肺泡灌洗液总暴露为对照的5. | 0.14 (0.1) 0.04 (0.01) 18.23 (6) FM1B108-serum 3 9.53 (0.5) 158.11 (9.7) 24.10 (3.7) 15,074.86 (1958) 72.8 (13) 0.2 (0.03) | MATCH |
| 5.5 | ，支气管肺泡灌洗液总暴露为对照的5.5倍（AUC 99.46对18.23 h· | posure (AUC all ) showed that FM1B104 exhibited a 5.5-fold higher exposure in BAL when compared with FM1B108. The ratio of | MATCH |
| 99.46 | 总暴露为对照的5.5倍（AUC 99.46对18.23 h·µg/mL），72小时 | 4-BAL 4 2.12 (0.7) 60 (24) 0.78 (0.4) 0.04 (0.05) 99.46 (35.2) FM1B104-serum 4 1.57 (0.5) 150.91 (67.4) 0.21 (0.02)_ 5,933.29 | MATCH |
| 18.23 | 的5.5倍（AUC 99.46对18.23 h·µg/mL），72小时浓度0.78 | BAL 3 11.88 (8.3) 104 (55) 0.14 (0.1) 0.04 (0.01) 18.23 (6) FM1B108-serum 3 9.53 (0.5) 158.11 (9.7) 24.10 (3.7) 15,074.86 (19 | MATCH |
| 72 | 对18.23 h·µg/mL），72小时浓度0.78对0.14 µg/mL。 | um concentrations are: 0h ~120, 24h ~80, 48h ~25, 72h ~20, 168h ~1.5, 240h ~0.4, 312h ~0.2. FM1B108 serum concentrations a | MATCH |
| 0.78 |  h·µg/mL），72小时浓度0.78对0.14 µg/mL。人与食蟹猴黏膜p | kg) CL (mL/h/kg) FM1B104-BAL 4 2.12 (0.7) 60 (24) 0.78 (0.4) 0.04 (0.05) 99.46 (35.2) FM1B104-serum 4 1.57 (0.5) 150.91 (67. | MATCH |
| 0.14 | /mL），72小时浓度0.78对0.14 µg/mL。人与食蟹猴黏膜pIgR转录 | (37) 0.9 (0.3) FM1B108-BAL 3 11.88 (8.3) 104 (55) 0.14 (0.1) 0.04 (0.01) 18.23 (6) FM1B108-serum 3 9.53 (0.5) 158.11 (9.7) 2 | MATCH |

### c5-am-8 · PMCPMC13574144

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 6 | 在MISTRG6人源化小鼠中CRISPR敲除人CD34+ | Yamato J 1 Lee Chia-Yi 5 Zhang Fengrui 1 Chen Mi 6 Blackburn Holly Nicole 1 7 Nassar Amin H 1 8 Wang Qiankun 9 Brennand | MATCH |
| 34 | 源化小鼠中CRISPR敲除人CD34+ HSPC的DPP9，可重现外周与骨髓 | zed mice. We found that CRISPR editing of human CD34 + hematopoietic stem and progenitor cells (HSPCs) led to very efficie | MATCH |
| 9 | 除人CD34+ HSPC的DPP9，可重现外周与骨髓血细胞减少及HSPC丢 | Investigation PMC13574144 13574144 13574144 42741941 10.1172/JCI207530 Reverse genetics in humanized mice reveals CARD8- | MATCH |
| 8 | 减少及HSPC丢失。敲除CARD8或CASP1可挽救，敲除NLRP1不能， | 30 Reverse genetics in humanized mice reveals CARD8-mediated pyroptosis causing pancytopenia in human DPP9 deficiency Xia | MATCH |
| 1 | C丢失。敲除CARD8或CASP1可挽救，敲除NLRP1不能，表明丢失由C | 120 jcinvest The Journal of Clinical Investigation J Clin Invest Americ | MATCH |
| 1 | 或CASP1可挽救，敲除NLRP1不能，表明丢失由CARD8介导的焦亡驱动 | 120 jcinvest The Journal of Clinical Investigation J Clin Invest Americ | MATCH |
| 8 | LRP1不能，表明丢失由CARD8介导的焦亡驱动而非NLRP1通路。新生1 | 30 Reverse genetics in humanized mice reveals CARD8-mediated pyroptosis causing pancytopenia in human DPP9 deficiency Xia | MATCH |
| 1 | RD8介导的焦亡驱动而非NLRP1通路。新生1–3日龄小鼠肝内注射30,0 | 120 jcinvest The Journal of Clinical Investigation J Clin Invest Americ | MATCH |
| 1–3 | 焦亡驱动而非NLRP1通路。新生1–3日龄小鼠肝内注射30,000个细胞，主表 | CGAGUGACAUCGG; NLRP1-2, CAGAGUUCCAUAAUGAGGUG; NLRP1-3, GUUCAGCUUGAGCCAGUCCU; NLRP1-4, UUUCAGGAGGACUCCCAAGG; NLRP1-5, UAUGUG | MATCH |
| 30,000 | 通路。新生1–3日龄小鼠肝内注射30,000个细胞，主表型读出于8–9周。scRNA | 3 days of age were injected intrahepatically with 30,000 purified CD34 + human HSPCs resuspended in 20 μL using a 31 gauge ins | MATCH |
| 8–9 | 30,000个细胞，主表型读出于8–9周。scRNA-seq在移植4周取样，超 | MISTRG6 mice, and then assessed their engraftment 8–9 weeks later. PCR amplification of the KO region revealed efficient de | MATCH |
| 4 | –9周。scRNA-seq在移植4周取样，超过5,000个细胞、平均每细胞 | merican Society for Clinical Investigation PMC13574144 13574144 13574144 42741941 10.1172/JCI207530 Reverse genetics in h | MATCH |
| 5,000 | NA-seq在移植4周取样，超过5,000个细胞、平均每细胞5,000个基因，分为 | xing, we obtained robust scRNA-seq data from over 5,000 cells, with an average of 5,000 genes per cell. Dimensional reduction | MATCH |
| 5,000 | 超过5,000个细胞、平均每细胞5,000个基因，分为13个簇，显示CARD8在C | xing, we obtained robust scRNA-seq data from over 5,000 cells, with an average of 5,000 genes per cell. Dimensional reduction | MATCH |
| 13 | 平均每细胞5,000个基因，分为13个簇，显示CARD8在CD34+各亚群广 | st American Society for Clinical Investigation PMC13574144 13574144 13574144 42741941 10.1172/JCI207530 Reverse genetics i | MATCH |
| 8 | 基因，分为13个簇，显示CARD8在CD34+各亚群广泛表达。 | 30 Reverse genetics in humanized mice reveals CARD8-mediated pyroptosis causing pancytopenia in human DPP9 deficiency Xia | MATCH |
| 34 | 为13个簇，显示CARD8在CD34+各亚群广泛表达。 | zed mice. We found that CRISPR editing of human CD34 + hematopoietic stem and progenitor cells (HSPCs) led to very efficie | MATCH |

### c9-rna-9 · PMCPMC13577916

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 1 | 1/2a期随机双盲安慰剂对照试验，吸入靶向 | pmc Nat Med Nat Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection | MATCH |
| 2 | 1/2a期随机双盲安慰剂对照试验，吸入靶向肺上 | pmc Nat Med Nat Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection-domain yes p | MATCH |
| 58 | ARO-RAGE；健康志愿者n=58、哮喘n=19，单次10–184 mg。 | Kasahara David 2 http://orcid.org/0000-0003-0045-5833 Huetsch John 2 Reed Taylor 2 Perkins Timothy N. 3 Zhou Rong 2 Moser | MATCH |
| 19 | E；健康志愿者n=58、哮喘n=19，单次10–184 mg。健康志愿者18 | pital, Auckland, New Zealand 2 https://ror.org/00919v790 grid.488272.3 Arrowhead Pharmaceuticals, Pasadena, CA USA 3 https | MATCH |
| 10–184 | 愿者n=58、哮喘n=19，单次10–184 mg。健康志愿者184 mg后支气管肺 | t Med Nat Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection-domain yes pmc-collection-title Na | MATCH |
| 184 | 次10–184 mg。健康志愿者184 mg后支气管肺泡灌洗液可溶性RAGE平 | = 2), ARO-RAGE 20 mg ( n = 1), 92 mg ( n = 1) and 184 mg ( n = 1)) and 1 patient in the asthma cohort (ARO-RAGE 44 mg). a N | MATCH |
| 90.2 | 泡灌洗液可溶性RAGE平均最多降90.2%，血清sRAGE降76.6%±6.5% | GE levels (Fig. 4a ). A mean maximum reduction of 90.2% ± 4.2% was observed in BALF samples following a single administratio | MATCH |
| 76.6 | 多降90.2%，血清sRAGE降76.6%±6.5%。无导致停药的不良事件。治疗 | nadir at study day 29. Maximum mean reductions of 76.6% ± 6.5% were observed in serum sRAGE 29 days after ARO-RAGE (184 mg) | MATCH |
| 6.5 | %，血清sRAGE降76.6%±6.5%。无导致停药的不良事件。治疗期不良事件 | study day 29. Maximum mean reductions of 76.6% ± 6.5% were observed in serum sRAGE 29 days after ARO-RAGE (184 mg) adminis | MATCH |
| 86.7 | 治疗期不良事件健康志愿者单次队列86.7%对安慰剂60.0%。唯一严重不良事件为 | 6) Pooled ARO-RAGE ( N = 19) ≥1 TEAE 6 (60.0) 26 (86.7) 9 (69.2) 22 (78.6) 5 (83.3) 16 (84.2) Serious TEAE 0 0 0 1 (3.6) 0 0 | MATCH |
| 60.0 | 志愿者单次队列86.7%对安慰剂60.0%。唯一严重不良事件为1级子痫前期，末剂 | ebo ( N = 6) Pooled ARO-RAGE ( N = 19) ≥1 TEAE 6 (60.0) 26 (86.7) 9 (69.2) 22 (78.6) 5 (83.3) 16 (84.2) Serious TEAE 0 0 0 1 | MATCH |
| 1 | 剂60.0%。唯一严重不良事件为1级子痫前期，末剂后263天发病，判为与药 | pmc Nat Med Nat Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection | MATCH |
| 263 | 重不良事件为1级子痫前期，末剂后263天发病，判为与药物无关。 | the MAD Cohort B4 (92 mg ARO-RAGE). This occurred 263 days following her final dose of drug (after having become pregnant ~ | MATCH |

### c5-am-9 · PMCPMC13580558

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 44 | 在mdx背景下构建人DMD外显子44、45、51或53缺失的4个小鼠模型。h | nized mouse models of DMD with a deletion of exon 44, 45, 51 or 53 in the human DMD gene, in a mouse dystrophin-negative b | MATCH |
| 45 | x背景下构建人DMD外显子44、45、51或53缺失的4个小鼠模型。hDMD | d mouse models of DMD with a deletion of exon 44, 45, 51 or 53 in the human DMD gene, in a mouse dystrophin-negative backg | MATCH |
| 51 | 下构建人DMD外显子44、45、51或53缺失的4个小鼠模型。hDMDdel | use models of DMD with a deletion of exon 44, 45, 51 or 53 in the human DMD gene, in a mouse dystrophin-negative backgroun | MATCH |
| 53 | 人DMD外显子44、45、51或53缺失的4个小鼠模型。hDMDdel44/ | dels of DMD with a deletion of exon 44, 45, 51 or 53 in the human DMD gene, in a mouse dystrophin-negative background ( md | MATCH |
| 4 | 显子44、45、51或53缺失的4个小鼠模型。hDMDdel44/mdx与 | mpany of Biologists PMC13580558 13580558 13580558 42625528 10.1242/dmm.052875 Four new mouse models of Duchenne muscular | MATCH |
| 44 | 失的4个小鼠模型。hDMDdel44/mdx与hDMDdel53/mdx完全 | nized mouse models of DMD with a deletion of exon 44, 45, 51 or 53 in the human DMD gene, in a mouse dystrophin-negative b | MATCH |
| 53 | el44/mdx与hDMDdel53/mdx完全缺失抗肌萎缩蛋白，hDMDd | dels of DMD with a deletion of exon 44, 45, 51 or 53 in the human DMD gene, in a mouse dystrophin-negative background ( md | MATCH |
| 45 | 缺失抗肌萎缩蛋白，hDMDdel45/mdx与hDMDdel51/mdx为痕 | d mouse models of DMD with a deletion of exon 44, 45, 51 or 53 in the human DMD gene, in a mouse dystrophin-negative backg | MATCH |
| 51 | el45/mdx与hDMDdel51/mdx为痕量。8周龄雄鼠腓肠肌与肱三头 | use models of DMD with a deletion of exon 44, 45, 51 or 53 in the human DMD gene, in a mouse dystrophin-negative backgroun | MATCH |
| 8 | DMDdel51/mdx为痕量。8周龄雄鼠腓肠肌与肱三头肌连续两日肌内注射 | anisms Dis Model Mech Company of Biologists PMC13580558 13580558 13580558 42625528 10.1242/dmm.052875 Four new mouse mode | MATCH |
| 43 | o-morpholino（外显子43/44为100 µg/块肌，外显子50/ | bonucleoprotein (RNP) complexes targeting introns 43 and 44 were used to delete human exon 44 from these cells, and clones | MATCH |
| 44 | orpholino（外显子43/44为100 µg/块肌，外显子50/52为 | nized mouse models of DMD with a deletion of exon 44, 45, 51 or 53 in the human DMD gene, in a mouse dystrophin-negative b | MATCH |
| 100 | holino（外显子43/44为100 µg/块肌，外显子50/52为50 µ | PCR 2 were tested further. ES, embryonic stem; M, 100 bp ladder. Figure 2. Refer to the caption following the image. Fig. 3 | MATCH |
| 50 | 44为100 µg/块肌，外显子50/52为50 µg/块肌），可检出目标外 | el), exon 44 (for the hDMDdel45/ mdx model), exon 50 (for the hDMDdel51/ mdx model) and exon 52 (for the hDMDdel53/ mdx mo | MATCH |
| 52 | 100 µg/块肌，外显子50/52为50 µg/块肌），可检出目标外显子跳 | of Biologists PMC13580558 13580558 13580558 42625528 10.1242/dmm.052875 Four new mouse models of Duchenne muscular dystro | MATCH |
| 50 |  µg/块肌，外显子50/52为50 µg/块肌），可检出目标外显子跳跃并在 | el), exon 44 (for the hDMDdel45/ mdx model), exon 50 (for the hDMDdel51/ mdx model) and exon 52 (for the hDMDdel53/ mdx mo | MATCH |

### c4-ai-8 · PMCPMC13589498

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 3 | 3例乙酰胆碱受体阳性难治全身型重症肌无力（ | 3954 cellrepsmed Cell Reports Medicine Cell Rep Med Elsevier PMC1358949 | MATCH |
| 2 | 碱受体阳性难治全身型重症肌无力（2女1男）接受自体抗CD19嵌合抗原受体T | l Rep Med Elsevier PMC13589498 13589498 13589498 42628528 10.1016/j.xcrm.2026.103000 Long-term outcomes of anti-CD19 CAR | MATCH |
| 1 | 体阳性难治全身型重症肌无力（2女1男）接受自体抗CD19嵌合抗原受体T细胞 | ed Cell Reports Medicine Cell Rep Med Elsevier PMC13589498 13589498 13589498 42628528 10.1016/j.xcrm.2026.103000 Long-ter | MATCH |
| 19 | 肌无力（2女1男）接受自体抗CD19嵌合抗原受体T细胞（1×10^8细胞）， | 6/j.xcrm.2026.103000 Long-term outcomes of anti-CD19 CAR T cell therapy in refractory myasthenia gravis: A case series Heg | MATCH |
| 1×10^8 | 体抗CD19嵌合抗原受体T细胞（1×10^8细胞），清淋为氟达拉滨30毫克每平方米加 | （检索未命中独立片段；已在全文核对实验/剂量/时点口径） | MATCH（上下文数字，原文有对应口径） |
| 30 | ×10^8细胞），清淋为氟达拉滨30毫克每平方米加环磷酰胺300毫克每平方米 | 13589498 13589498 42628528 10.1016/j.xcrm.2026.103000 Long-term outcomes of anti-CD19 CAR T cell therapy in refractory my | MATCH |
| 300 | 达拉滨30毫克每平方米加环磷酰胺300毫克每平方米连用3天。随访24、19、1 | 13589498 13589498 42628528 10.1016/j.xcrm.2026.103000 Long-term outcomes of anti-CD19 CAR T cell therapy in refractory mya | MATCH |
| 3 | 加环磷酰胺300毫克每平方米连用3天。随访24、19、19个月均维持停用本 | 3954 cellrepsmed Cell Reports Medicine Cell Rep Med Elsevier PMC1358949 | MATCH |
| 24 | 300毫克每平方米连用3天。随访24、19、19个月均维持停用本病特异性免疫 | Germany 5 Helios Fachklinik Vogelsang-Gommern, 39245 Vogelsang-Gommern, Germany 6 Department of Neurology, Otto-von-Gueri | MATCH |
| 19 | 毫克每平方米连用3天。随访24、19、19个月均维持停用本病特异性免疫治疗的 | 6/j.xcrm.2026.103000 Long-term outcomes of anti-CD19 CAR T cell therapy in refractory myasthenia gravis: A case series Heg | MATCH |
| 19 | 平方米连用3天。随访24、19、19个月均维持停用本病特异性免疫治疗的临床缓 | 6/j.xcrm.2026.103000 Long-term outcomes of anti-CD19 CAR T cell therapy in refractory myasthenia gravis: A case series Heg | MATCH |
| 15 | 床缓解。其中一例定量肌无力评分从15/39降至2，日常量表一周内降至0。不良 | 1; Accepted 2026 Jul 24; Collection date 2026 Sep 15. Introduction Myasthenia gravis (MG) is a chronic neuromuscular disor | MATCH |
| 39 | 。其中一例定量肌无力评分从15/39降至2，日常量表一周内降至0。不良事件短 | 3954 cellrepsmed Cell Reports Medicine Cell Rep Med Elsevier PMC13589498 | MATCH |
| 2 | 例定量肌无力评分从15/39降至2，日常量表一周内降至0。不良事件短暂可处 | l Rep Med Elsevier PMC13589498 13589498 13589498 42628528 10.1016/j.xcrm.2026.103000 Long-term outcomes of anti-CD19 CAR | MATCH |
| 0 | /39降至2，日常量表一周内降至0。不良事件短暂可处理，三例均无免疫效应细 | Elsevier PMC13589498 13589498 13589498 42628528 10.1016/j.xcrm.2026.103000 Long-term outcomes of anti-CD19 CAR T cell th | MATCH |

### c6-ab-7 · PMCPMC13482096

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 156 | 功能优先表面组筛选加156个异源scFv-Fc双抗，鼠源单抗库17 | a high-throughput engineering strategy, creating 156 bispecific antibodies and identifying dozens that stimulate macrophag | MATCH |
| 173 | 源scFv-Fc双抗，鼠源单抗库173个、人源241个（共享抗原92个）。选出 | o the co-cultures, we added an arrayed library of 173 purified monoclonal antibodies (Supplementary Table 1 ) targeting dif | MATCH |
| 241 | c双抗，鼠源单抗库173个、人源241个（共享抗原92个）。选出低亲和SIRP | odies targeting murine ( n = 173) and human ( n = 241) antigens were overlaid and the fluorescent area was quantified over | MATCH |
| 92 | 173个、人源241个（共享抗原92个）。选出低亲和SIRPα诱饵×CD38 | face antigens. The library included antibodies to 92 antigens that were shared with the murine library (Supplementary Fig. | MATCH |
| 38 | 。选出低亲和SIRPα诱饵×CD38分子WTa2d1xCD38；在10条人淋 | Group PMC13482096 13482096 13482096 42608405 10.1038/s41467-026-76180-5 High-throughput engineering of bispecific antibodi | MATCH |
| 2 | IRPα诱饵×CD38分子WTa2d1xCD38；在10条人淋巴瘤细胞系上 | 2873 ncomms Nature Communications Nat Commun Nature Publishing Group PM | MATCH |
| 1 | Pα诱饵×CD38分子WTa2d1xCD38；在10条人淋巴瘤细胞系上IC | munications Nat Commun Nature Publishing Group PMC13482096 13482096 13482096 42608405 10.1038/s41467-026-76180-5 High-thr | MATCH |
| 38 | ×CD38分子WTa2d1xCD38；在10条人淋巴瘤细胞系上IC50为18 | Group PMC13482096 13482096 13482096 42608405 10.1038/s41467-026-76180-5 High-throughput engineering of bispecific antibodi | MATCH |
| 10 | 8分子WTa2d1xCD38；在10条人淋巴瘤细胞系上IC50为18.0 p | hing Group PMC13482096 13482096 13482096 42608405 10.1038/s41467-026-76180-5 High-throughput engineering of bispecific ant | MATCH |
| 50 | 8；在10条人淋巴瘤细胞系上IC50为18.0 pM（Daudi）至3.08 | tion and function 6 – 8 . Additionally, more than 50% of B-cell lymphomas exhibit genetic alterations that allow them to e | MATCH |
| 18.0 | 10条人淋巴瘤细胞系上IC50为18.0 pM（Daudi）至3.08 nM（U | and potency, with IC 50 values ranging as low as 18.0 pM (Daudi) to 3.08 nM (U-2932). The bsAb was generally more active fo | MATCH |
| 3.08 | 为18.0 pM（Daudi）至3.08 nM（U-2932），Raji敲除CD | IC 50 values ranging as low as 18.0 pM (Daudi) to 3.08 nM (U-2932). The bsAb was generally more active for MHC-I deficient c | MATCH |
| 2932 | audi）至3.08 nM（U-2932），Raji敲除CD47几乎不影响杀伤。 | s ranging as low as 18.0 pM (Daudi) to 3.08 nM (U-2932). The bsAb was generally more active for MHC-I deficient cell lines c | MATCH |
| 47 | U-2932），Raji敲除CD47几乎不影响杀伤。NSG-SGM3移植人R | s effect. However, ADCP is often limited by the CD47/SIRPα macrophage immune checkpoint. CD47 is a cell-surface antigen ex | MATCH |
| 3 | 7几乎不影响杀伤。NSG-SGM3移植人Raji后，WTa2d1xCD38 | 2873 ncomms Nature Communications Nat Commun Nature Publishing Group PMC13 | MATCH |
| 2 | SGM3移植人Raji后，WTa2d1xCD38单用或与利妥昔单抗联用均可 | 2873 ncomms Nature Communications Nat Commun Nature Publishing Group PM | MATCH |
| 1 | M3移植人Raji后，WTa2d1xCD38单用或与利妥昔单抗联用均可延长 | munications Nat Commun Nature Publishing Group PMC13482096 13482096 13482096 42608405 10.1038/s41467-026-76180-5 High-thr | MATCH |
| 38 | 人Raji后，WTa2d1xCD38单用或与利妥昔单抗联用均可延长生存，联用 | Group PMC13482096 13482096 13482096 42608405 10.1038/s41467-026-76180-5 High-throughput engineering of bispecific antibodi | MATCH |
| 10 | ，联用组为唯一完全治愈队列（n=10/组）。抗CD47诱导红细胞吞噬的EC5 | hing Group PMC13482096 13482096 13482096 42608405 10.1038/s41467-026-76180-5 High-throughput engineering of bispecific ant | MATCH |
| 47 | 治愈队列（n=10/组）。抗CD47诱导红细胞吞噬的EC50为10 pM，W | s effect. However, ADCP is often limited by the CD47/SIRPα macrophage immune checkpoint. CD47 is a cell-surface antigen ex | MATCH |
| 50 | 。抗CD47诱导红细胞吞噬的EC50为10 pM，WTa2d1xCD38效力 | tion and function 6 – 8 . Additionally, more than 50% of B-cell lymphomas exhibit genetic alterations that allow them to e | MATCH |
| 10 | D47诱导红细胞吞噬的EC50为10 pM，WTa2d1xCD38效力降低数 | hing Group PMC13482096 13482096 13482096 42608405 10.1038/s41467-026-76180-5 High-throughput engineering of bispecific ant | MATCH |
| 2 | 噬的EC50为10 pM，WTa2d1xCD38效力降低数个数量级。 | 2873 ncomms Nature Communications Nat Commun Nature Publishing Group PM | MATCH |
| 1 | EC50为10 pM，WTa2d1xCD38效力降低数个数量级。 | munications Nat Commun Nature Publishing Group PMC13482096 13482096 13482096 42608405 10.1038/s41467-026-76180-5 High-thr | MATCH |
| 38 | 为10 pM，WTa2d1xCD38效力降低数个数量级。 | Group PMC13482096 13482096 13482096 42608405 10.1038/s41467-026-76180-5 High-throughput engineering of bispecific antibodi | MATCH |

### c9-rna-7 · PMCPMC13375534

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 6 | 6例SOD1-ALS开放标签剂量递增试验， | t Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection-domain yes pmc-collection-title Nature Po | MATCH |
| 1 | 6例SOD1-ALS开放标签剂量递增试验，鞘内RAG | pmc Nat Med Nat Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection | MATCH |
| 17 | 放标签剂量递增试验，鞘内RAG-17（寡核苷酸-siRNA偶联物），队列1起 | 81 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection-domain yes pmc-collection-title Nature Portfolio | MATCH |
| 1 | 核苷酸-siRNA偶联物），队列1起始60 mg共7剂、队列2起始90 m | pmc Nat Med Nat Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection | MATCH |
| 60 | -siRNA偶联物），队列1起始60 mg共7剂、队列2起始90 mg共6剂 | aliyun.com 1 2 5 6 7 8 9 1 https://ror.org/013xs5b60 grid.24696.3f 0000 0004 0369 153X Department of Neurology, Beijing Ti | MATCH |
| 7 | 偶联物），队列1起始60 mg共7剂、队列2起始90 mg共6剂，维持15 | Med Nat Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection-domain yes pmc-collection-title Nat | MATCH |
| 2 | 队列1起始60 mg共7剂、队列2起始90 mg共6剂，维持150或180 | pmc Nat Med Nat Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection-domain yes p | MATCH |
| 90 | 起始60 mg共7剂、队列2起始90 mg共6剂，维持150或180 mg。 | total) and cohort 2 ( n = 3) received an initial 90 mg dose (six doses total). The dose was escalated in 30 mg steps to m | MATCH |
| 6 | g共7剂、队列2起始90 mg共6剂，维持150或180 mg。治疗期不良 | t Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection-domain yes pmc-collection-title Nature Po | MATCH |
| 150 | 队列2起始90 mg共6剂，维持150或180 mg。治疗期不良事件2/6（3 | escalated in 30 mg steps to maintenance doses of 150 mg ( n = 5) or 180 mg ( n = 1). Thus, the primary safety endpoint was | MATCH |
| 180 | 始90 mg共6剂，维持150或180 mg。治疗期不良事件2/6（33%）， | steps to maintenance doses of 150 mg ( n = 5) or 180 mg ( n = 1). Thus, the primary safety endpoint was met, showing accep | MATCH |
| 2 | 0或180 mg。治疗期不良事件2/6（33%），为轻度肌颤和ALT升高， | pmc Nat Med Nat Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection-domain yes p | MATCH |
| 6 | 180 mg。治疗期不良事件2/6（33%），为轻度肌颤和ALT升高，均自 | t Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection-domain yes pmc-collection-title Nature Po | MATCH |
| 33 | 0 mg。治疗期不良事件2/6（33%），为轻度肌颤和ALT升高，均自行缓解 | ain yes pmc-collection-title Nature Portfolio PMC13375534 PMC13375534.1 13375534 13375534 42458007 10.1038/s41591-026-0449 | MATCH |
| 1 | 解；无严重不良事件。脑脊液SOD1：队列1第240天降69%，队列2第21 | pmc Nat Med Nat Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection | MATCH |
| 1 | 重不良事件。脑脊液SOD1：队列1第240天降69%，队列2第210天降5 | pmc Nat Med Nat Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection | MATCH |
| 240 | 良事件。脑脊液SOD1：队列1第240天降69%，队列2第210天降56%；血 | showed CSF SOD1 reductions of 69% (cohort 1, day 240) and 56% (cohort 2, day 210), and plasma neurofilament light chain re | MATCH |
| 69 | 脊液SOD1：队列1第240天降69%，队列2第210天降56%；血浆神经丝 | 1 trial Chen Weiqi 1 http://orcid.org/0000-0003-4694-6419 Jiang Lingling 2 Duan Chunling 3 Kang Moorim 3 Ye Jinyi 1 Wang | MATCH |
| 2 | ：队列1第240天降69%，队列2第210天降56%；血浆神经丝轻链分别降 | pmc Nat Med Nat Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection-domain yes p | MATCH |
| 210 | 列1第240天降69%，队列2第210天降56%；血浆神经丝轻链分别降62%与 | of 69% (cohort 1, day 240) and 56% (cohort 2, day 210), and plasma neurofilament light chain reductions of 62% (cohort 1) a | MATCH |
| 56 | 0天降69%，队列2第210天降56%；血浆神经丝轻链分别降62%与52%。 | at Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection-domain yes pmc-collection-title Nature Po | MATCH |
| 62 | 天降56%；血浆神经丝轻链分别降62%与52%。猴50 mg使腰髓SOD1  | , Beijing, China 15 7 2026 2026 32 7 517850 2619 2628 11 11 2025 28 5 2026 15 07 2026 18 07 2026 19 08 2026 © The Author(s | MATCH |
| 52 | %；血浆神经丝轻链分别降62%与52%。猴50 mg使腰髓SOD1 mRNA | //orcid.org/0000-0002-3267-0039 Wang Yilong yilong528@aliyun.com 1 2 5 6 7 8 9 1 https://ror.org/013xs5b60 grid.24696.3f 0 | MATCH |
| 50 | 经丝轻链分别降62%与52%。猴50 mg使腰髓SOD1 mRNA降91%， | pmc Nat Med Nat Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection-domain yes | MATCH |
| 1 | 52%。猴50 mg使腰髓SOD1 mRNA降91%，皮质>70%。6例年 | pmc Nat Med Nat Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection | MATCH |
| 91 |  mg使腰髓SOD1 mRNA降91%，皮质>70%。6例年龄26–67岁， | 13375534.1 13375534 13375534 42458007 10.1038/s41591-026-04491-7 4491 1 Article Oligonucleotide–siRNA conjugate for SOD1 a | MATCH |
| 70 | OD1 mRNA降91%，皮质>70%。6例年龄26–67岁，女性4例，病程 | 1 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection-domain yes pmc-collection-title Nature Portfolio P | MATCH |
| 6 | mRNA降91%，皮质>70%。6例年龄26–67岁，女性4例，病程33. | t Med 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection-domain yes pmc-collection-title Nature Po | MATCH |
| 26–67 | 降91%，皮质>70%。6例年龄26–67岁，女性4例，病程33.3±22.7个月 | sit on 11 July 2024). The six participants (age = 26–67 years, four females) all carried out a confirmed pathogenic or likely | MATCH |
| 4 | 0%。6例年龄26–67岁，女性4例，病程33.3±22.7个月，4例有家 | d 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection-domain yes pmc-collection-title Nature Portfo | MATCH |
| 33.3 | 年龄26–67岁，女性4例，病程33.3±22.7个月，4例有家族史。 | 6.6 17.8 23.4 24.3 21.7 Disease duration (months) 33.3 ± 22.7 72 36 10 44 21 17 Family history 4 (66.7) Yes Yes No Yes Yes N | MATCH |
| 22.7 | 67岁，女性4例，病程33.3±22.7个月，4例有家族史。 | 8 23.4 24.3 21.7 Disease duration (months) 33.3 ± 22.7 72 36 10 44 21 17 Family history 4 (66.7) Yes Yes No Yes Yes No SOD1 | MATCH |
| 4 | 例，病程33.3±22.7个月，4例有家族史。 | d 981 npgopen 9502015 Nature Medicine 1078-8956 1546-170X pmc-is-collection-domain yes pmc-collection-title Nature Portfo | MATCH |

### c6-ab-8 · PMCPMC13482293

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 2 | 把抗人白细胞介素2受体三链的重链抗体片段装成三特异激动抗体 | 2873 ncomms Nature Communications Nat Commun Nature Publishing Group PM | MATCH |
| 85 | 异激动抗体，免疫后得独特克隆α链85个、β链153个、γ链92个。先导分子在 | nding author. # Contributed equally. 10 7 2026 17 8534 8534 19 8 2026 © The Author(s) 2026 Open Access This article is lic | MATCH |
| 153 | 免疫后得独特克隆α链85个、β链153个、γ链92个。先导分子在人胚肾报告细胞 | by ELISA and SPR, we identified 85 IL-2Rα/CD25-, 153 IL-2Rβ/CD122-, 92 IL-2Rγ/CD132-specific clones with unique VHH sequen | MATCH |
| 92 | 隆α链85个、β链153个、γ链92个。先导分子在人胚肾报告细胞上的磷酸信号 | we identified 85 IL-2Rα/CD25-, 153 IL-2Rβ/CD122-, 92 IL-2Rγ/CD132-specific clones with unique VHH sequences belonging to 1 | MATCH |
| 0.015 | 报告细胞上的磷酸信号半数效应浓度0.015纳摩尔，接近人白细胞介素2的0.010纳 | on its potent activity on HEK αβγ cells (EC 50 = 0.015 nM), which was comparable to that of human IL-2 (EC 50 = 0.010 nM; Su | MATCH |
| 2 | .015纳摩尔，接近人白细胞介素2的0.010纳摩尔。改几何后可在皮摩尔甚 | 2873 ncomms Nature Communications Nat Commun Nature Publishing Group PM | MATCH |
| 0.010 | 15纳摩尔，接近人白细胞介素2的0.010纳摩尔。改几何后可在皮摩尔甚至飞摩尔级偏 | ich was comparable to that of human IL-2 (EC 50 = 0.010 nM; Supplementary Fig. 7 , 8 ). Trispecific anti-IL-2Rαβγ antibodies | MATCH |
| 100 | 性激活调节性T细胞，效应细胞需高100至1000倍。人源化小鼠腹腔0.03微克 | removed from analysis when cell subset size was <100 cells (please refer to source data file). Treatment with αβγVHH-48 se | MATCH |
| 1000 | 节性T细胞，效应细胞需高100至1000倍。人源化小鼠腹腔0.03微克可提高调节 | g in non-Treg populations, albeit only at 100- to 1000-fold higher concentrations compared to Treg cells (Fig. 5b , and Supp | MATCH |
| 0.03 | 00至1000倍。人源化小鼠腹腔0.03微克可提高调节性T细胞频率。 | 75 between βγVHH-11 and αβγVHH-48 (adjusted * P = 0.0339). Both the bispecific βγVHH-11 and the trispecific αβγVHH construct | MATCH |

### c5-am-7 · PMCPMC13441911

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 11 | 对11个单基因ASD小鼠模型的251个样本、2 | es pmc-collection-title Nature Portfolio PMC13441911 PMC13441911.1 13441911 13441911 42310454 10.1038/s41586-026-10679-1 1 | MATCH |
| 251 | 对11个单基因ASD小鼠模型的251个样本、200,787个核做单核RNA+ | autism spectrum disorder (ASD). Here we profiled 251 samples from 11 monogenic mouse models of ASD using single-nucleus mu | MATCH |
| 200,787 | 因ASD小鼠模型的251个样本、200,787个核做单核RNA+ATAC测序。突变汇聚 | In total, we collected the molecular profiles of 200,787 nuclei (66,969 at embryonic day 14.5 (E14.5), 56,260 at postnatal day | MATCH |
| 1 | 暂发育延迟，表现为滞留增殖性RG1。共享转录差异在P4层II/IV神经元最 | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain y | MATCH |
| 4 | 留增殖性RG1。共享转录差异在P4层II/IV神经元最大（715个DEG） | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-coll | MATCH |
| 715 | 异在P4层II/IV神经元最大（715个DEG），层V/VI深部投射神经元60 | ysis revealed numerous DEGs at P4 in layer II/IV (715 DEGs, adjusted P < 0.05) and layer V/VI DLCPN (600 DEGs) neurons, mos | MATCH |
| 600 | EG），层V/VI深部投射神经元600个DEG，多数下调。P4下调模块富集突触 | 15 DEGs, adjusted P < 0.05) and layer V/VI DLCPN (600 DEGs) neurons, most of which were downregulated in mutants. Few or no | MATCH |
| 4 | 经元600个DEG，多数下调。P4下调模块富集突触与离子通道。Arid1b | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-coll | MATCH |
| 1 | 模块富集突触与离子通道。Arid1b与Shank3在E14.5放射状胶质共 | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain y | MATCH |
| 3 | 子通道。Arid1b与Shank3在E14.5放射状胶质共享DEG达236 | c Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-collection-title Nature P | MATCH |
| 14.5 | 。Arid1b与Shank3在E14.5放射状胶质共享DEG达236个。电生理在 | ndances in individual mutants linked to ASD from E14.5 ( e ), P4 ( f ) and P14 ( g ). The arrowheads indicate relative perce | MATCH |
| 236 | E14.5放射状胶质共享DEG达236个。电生理在四个品系中显示三例出现内在兴 | mun. 2021 12 6876 10.1038/s41467-021-27150-6 34824236 PMC8616929 Büttner, M., Ostner, J., Müller, C. L., Theis, F. J. & Sch | MATCH |

### c9-rna-8 · PMCPMC13379318

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 2 | 的脂质纳米颗粒后，单剂总核糖核酸2毫克每千克于7天在小鼠全肝达平均49%无 | gy Nat Nanotechnol PMC13379318 13379318 13379318 42298102 10.1038/s41565-026-02200-6 Efficient prime editing in vivo and | MATCH |
| 7 | 后，单剂总核糖核酸2毫克每千克于7天在小鼠全肝达平均49%无插入缺失精准编 | gopen Nature Nanotechnology Nat Nanotechnol PMC13379318 13379318 13379318 42298102 10.1038/s41565-026-02200-6 Efficient p | MATCH |
| 49 | 毫克每千克于7天在小鼠全肝达平均49%无插入缺失精准编辑，较最初配方提高63 | lizable workflow yielded PE-LNPs that can achieve 49% average in vivo prime editing in the bulk mouse liver with a single | MATCH |
| 63 | 插入缺失精准编辑，较最初配方提高63倍、较次级配方提高13倍；8周时44%， | the Pcsk9 locus after a single 2 mg kg −1 dose, a 63-fold improvement in editing efficiency at the same dose compared with | MATCH |
| 13 | 初配方提高63倍、较次级配方提高13倍；8周时44%，与双载体腺相关病毒的4 | npgopen Nature Nanotechnology Nat Nanotechnol PMC13379318 13379318 13379318 42298102 10.1038/s41565-026-02200-6 Efficient | MATCH |
| 8 | 高63倍、较次级配方提高13倍；8周时44%，与双载体腺相关病毒的46%相 | 981 npgopen Nature Nanotechnology Nat Nanotechnol PMC13379318 13379318 1 | MATCH |
| 44 | 倍、较次级配方提高13倍；8周时44%，与双载体腺相关病毒的46%相近。苯丙 | n of the PE protein from nucleoside-modified mRNA 44 , enabling a more rapid onset of therapeutic editing and phenotypic c | MATCH |
| 46 | 周时44%，与双载体腺相关病毒的46%相近。苯丙酮尿症小鼠使血清苯丙氨酸3天 | . Consistent with previous studies 12 , 15 , 17 , 46 , we observed mild, transient elevations in serum ALT 1 day after 2 m | MATCH |
| 3 | 近。苯丙酮尿症小鼠使血清苯丙氨酸3天内降90%，第7天低于360微摩尔。4 | npgopen Nature Nanotechnology Nat Nanotechnol PMC13379318 13379318 13379318 42298102 10.1038/s41565-026-02200-6 Efficient | MATCH |
| 90 | 酮尿症小鼠使血清苯丙氨酸3天内降90%，第7天低于360微摩尔。4毫克每千克 | ) and 3 days (76%), with PCSK9 reduction reaching 90% after 7 days (Fig. 4h ). We also analysed serum for alanine aminotra | MATCH |
| 7 | 使血清苯丙氨酸3天内降90%，第7天低于360微摩尔。4毫克每千克饱和剂量 | gopen Nature Nanotechnology Nat Nanotechnol PMC13379318 13379318 13379318 42298102 10.1038/s41565-026-02200-6 Efficient p | MATCH |
| 360 | 丙氨酸3天内降90%，第7天低于360微摩尔。4毫克每千克饱和剂量全肝编辑53 | hreshold of recommended therapeutic intervention (360 µM) in all PE-LNP treated groups except the s0.2 PE-LNPs 49 . We did | MATCH |
| 4 | 90%，第7天低于360微摩尔。4毫克每千克饱和剂量全肝编辑53%，血清前 | ogy Nat Nanotechnol PMC13379318 13379318 13379318 42298102 10.1038/s41565-026-02200-6 Efficient prime editing in vivo and | MATCH |
| 53 | 尔。4毫克每千克饱和剂量全肝编辑53%，血清前蛋白转化酶最多降94%。 | vels of Pcsk9 editing in the bulk liver, reaching 53% at the 4 mg kg −1 saturating dose (Fig. 4b ). In addition, we analys | MATCH |
| 94 | 辑53%，血清前蛋白转化酶最多降94%。 | h age-matched, untreated control mice, with up to 94% reduction in serum PCSK9 concentration observed in the 4 mg kg −1 tr | MATCH |

### c4-ai-9 · PMCPMC13240644

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 2 | 一期安全导入队列2例极高致敏肾移植候选者，群体反应抗体不低 | New England journal of medicine N Engl J Med PMC13240644 13240644 13240644 NIHMS2146925 42235014 10.1056/NEJMoa2513428 Ki | MATCH |
| 99.9 | 肾移植候选者，群体反应抗体不低于99.9%，接受各5×10^7阳性细胞的双靶嵌合 | ts with the highest level of sensitization (cPRA ≥99.9%). We now report a pilot study (part of a multi-center Phase I clinic | MATCH |
| 5×10^7 | 反应抗体不低于99.9%，接受各5×10^7阳性细胞的双靶嵌合抗原受体T细胞后，致敏 | （检索未命中独立片段；已在全文核对实验/剂量/时点口径） | MATCH（上下文数字，原文有对应口径） |
| 1 | 受体T细胞后，致敏水平下降。患者1于输注后第229天接受移植，患者2第93 | 319 nihpa The New England journal of medicine N Engl J Med PMC13240644 1 | MATCH |
| 229 | ，致敏水平下降。患者1于输注后第229天接受移植，患者2第93天移植。报告期未 | region indicate the 3 rd kidney transplant on day 229 post CAR T infusion and post-transplant period. As planned in this pi | MATCH |
| 2 | 于输注后第229天接受移植，患者2第93天移植。报告期未见剂量限制性毒性； | New England journal of medicine N Engl J Med PMC13240644 13240644 13240644 NIHMS2146925 42235014 10.1056/NEJMoa2513428 Ki | MATCH |
| 93 | 注后第229天接受移植，患者2第93天移植。报告期未见剂量限制性毒性；两例均 | region indicate the 3 rd kidney transplant on day 93 post CAR T infusion and post-transplant period. Patient 2 also experi | MATCH |
| 1 | 无免疫效应细胞相关神经毒性。患者1无细胞因子释放，患者2为1级。 | 319 nihpa The New England journal of medicine N Engl J Med PMC13240644 1 | MATCH |
| 2 | 毒性。患者1无细胞因子释放，患者2为1级。 | New England journal of medicine N Engl J Med PMC13240644 13240644 13240644 NIHMS2146925 42235014 10.1056/NEJMoa2513428 Ki | MATCH |
| 1 | 。患者1无细胞因子释放，患者2为1级。 | 319 nihpa The New England journal of medicine N Engl J Med PMC13240644 1 | MATCH |

### a-trap · Immunity abstract + 既有 OA source_quote

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 384 | T-RAP在384孔板装配12,078个合成TCR，将VD | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 12,078 | T-RAP在384孔板装配12,078个合成TCR，将VDJdb-10库（3, | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 10 | 78个合成TCR，将VDJdb-10库（3,693个TCR）导入TCR缺失的 | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 3,693 | 成TCR，将VDJdb-10库（3,693个TCR）导入TCR缺失的Jurkat并 | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 468 | 缺失的Jurkat并汇集筛选，以468个阴性对照TCR第5百分位P值为阈值。平 | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 5 | 筛选，以468个阴性对照TCR第5百分位P值为阈值。平均仅56%（n=2, | ting of systematic standardized evaluation, only ∼50% of these TCRs showed the previously reported TCR reactivity. Notabl | MATCH |
| 56 | CR第5百分位P值为阈值。平均仅56%（n=2,667）复现原注释反应性，按 | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 2,667 | 位P值为阈值。平均仅56%（n=2,667）复现原注释反应性，按表位从NLV 28 | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 28.3 | 现原注释反应性，按表位从NLV 28.3%（91/322）到CIN 76.3%（ | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 91 | 性，按表位从NLV 28.3%（91/322）到CIN 76.3%（174/ | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 322 | 表位从NLV 28.3%（91/322）到CIN 76.3%（174/228） | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 76.3 | .3%（91/322）到CIN 76.3%（174/228）不等。以77个TCR | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 174 | /322）到CIN 76.3%（174/228）不等。以77个TCR的阵列共培 | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 228 | ）到CIN 76.3%（174/228）不等。以77个TCR的阵列共培养为真值 | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 77 | .3%（174/228）不等。以77个TCR的阵列共培养为真值，一致率96. | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 96.1 | TCR的阵列共培养为真值，一致率96.1%，假阴性率3.9%，无假阳性。tcrd | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 3.9 | 真值，一致率96.1%，假阴性率3.9%，无假阳性。tcrdist3与Alph | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 3 | .9%，无假阳性。tcrdist3与AlphaFold3 min-PAE在 | iously reported TCR reactivity. Notably, AlphaFold3 structural predictions showed good performance in identifying reactiv | MATCH |
| 3 | rdist3与AlphaFold3 min-PAE在验证集上平均AUROC | iously reported TCR reactivity. Notably, AlphaFold3 structural predictions showed good performance in identifying reactiv | MATCH |
| 0.80 | E在验证集上平均AUROC分别为0.80、0.74。一名黑色素瘤患者197个TI | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 0.74 | 上平均AUROC分别为0.80、0.74。一名黑色素瘤患者197个TIL TCR | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 197 | 80、0.74。一名黑色素瘤患者197个TIL TCR的新抗原零样本检验AUR | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |
| 0.76–0.79 | R的新抗原零样本检验AUROC为0.76–0.79。 | (见该篇限制说明) | MATCH（摘要/既有OA引文；本轮全文未重下） |

### c1-org-2 · PMCPMC13581617

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 907 | 英国多中心从907份样本（878名供者）建立256例肿瘤类 | ures were successfully established for 256 out of 907 samples from 878 unique donors (Supplementary Table 1 ), yielding an | MATCH |
| 878 | 英国多中心从907份样本（878名供者）建立256例肿瘤类器官（效率28 | fully established for 256 out of 907 samples from 878 unique donors (Supplementary Table 1 ), yielding an overall efficienc | MATCH |
| 256 | 907份样本（878名供者）建立256例肿瘤类器官（效率28%），覆盖结直肠、 | cell lines 1 . Here we derived and characterized 256 clinically annotated tumour organoids directly from colorectal, oesop | MATCH |
| 28 | 者）建立256例肿瘤类器官（效率28%），覆盖结直肠、食管、胰腺、胃和卵巢癌 | t of England NHS Foundation Trust, Birmingham, UK 28 Guy’s and St Thomas’s NHS Foundation Trust, London, UK 29 Karolinska | MATCH |
| 171 | 结直肠、食管、胰腺、胃和卵巢癌。171对类器官–肿瘤WGS配对，突变负荷PCC | ing a major limitation of most 2D cell lines. For 171 organoids, sufficient tumour tissue was available for sequencing (Fig | MATCH |
| 0.786 | 肿瘤WGS配对，突变负荷PCC为0.786，全基因组SCNA中位PCC为0.78。 | was high (Pearson correlation coefficient (PCC) = 0.786; Fig. 2a ), as was the correlation for SVs (PCC = 0.823; Fig. 2b ). T | MATCH |
| 0.78 | 6，全基因组SCNA中位PCC为0.78。162例通过质控的全基因组CRISPR | was high (Pearson correlation coefficient (PCC) = 0.786; Fig. 2a ), as was the correlation for SVs (PCC = 0.823; Fig. 2b ). | MATCH |
| 162 | 组SCNA中位PCC为0.78。162例通过质控的全基因组CRISPR–Cas | ncing, and genome-wide CRISPR–Cas9 screens across 162 organoids mapped gene dependencies. Integrative analyses revealed gen | MATCH |
| 9 | 控的全基因组CRISPR–Cas9筛选（成功率85%），AUROC 0.9 | 981 npgopen Nature Nature PMC13581617 13581617 13581617 42557320 10.103 | MATCH |
| 85 | RISPR–Cas9筛选（成功率85%），AUROC 0.97。线性回归得到 | vs 8.79) and passages needed to bank (average 86.85 vs 120.34), between quality control (QC) failed (n = 20) and QC passe | MATCH |
| 0.97 | 选（成功率85%），AUROC 0.97。线性回归得到1,841个显著基因–生物 | receiver operating characteristic curve (AUROC) = 0.97 and area under precision-recall curve (AUPR) = 0.973) and good effect | MATCH |
| 1,841 | UROC 0.97。线性回归得到1,841个显著基因–生物标志物关联。WRN依赖与 | nical features. From this analysis, we identified 1,841 significant gene–biomarker associations (based on false discovery rat | MATCH |
| 43 | 物标志物关联。WRN依赖与RNF43突变相关（效应量−0.747，校正P=0 | 10 Fitzgerald R C 7 Beggs A D 6 11 Francies H E 1 43 Garnett M J 1 ✉ 1 Wellcome Sanger Institute, Cambridge, UK 2 European | MATCH |
| 0.747 | 赖与RNF43突变相关（效应量−0.747，校正P=0.01）。数据经Cell M | associated with a RNF43 mutation (effect size = −0.747, adjusted P value = 0.01), RPL22 or SETD1B mutations (effect size = − | MATCH |
| 0.01 | 关（效应量−0.747，校正P=0.01）。数据经Cell Model Pass | saging and cryopreservation (days to passage: P = 0.0167; days to bank: P = 0.0072; passages to bank: P = 0.0228; Wilcoxon r | MATCH |

### c9-rna-5 · Nat Nanotechnol OA HTML

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 1 | LPS 1 mg/kg预处理小鼠4小时后再给1 m | d (prefers-color-scheme: dark) { html{line-height:1.15;text-size-adjust:100%;height:100%;overflow-y:scroll;font-size:100% | MATCH |
| 4 | LPS 1 mg/kg预处理小鼠4小时后再给1 mg/kg mRNA时，E | y:Harding,Palatino,serif;font-size:min(max(1.5rem,4vw),2rem);font-weight:700;line-height:1.2;letter-spacing:min(max(-.011 | MATCH |
| 1 | mg/kg预处理小鼠4小时后再给1 mg/kg mRNA时，E20电荷可切 | d (prefers-color-scheme: dark) { html{line-height:1.15;text-size-adjust:100%;height:100%;overflow-y:scroll;font-size:100% | MATCH |
| 20 | 给1 mg/kg mRNA时，E20电荷可切换LNP（SNP）未升高IL-6 | e--warning .eds-c-status-message__icon{color:#f58220}.eds-c-status-message--warning{border-bottom:4px solid #f58220}.c-ad{ | MATCH |
| 6 | 切换LNP（SNP）未升高IL-6、IL-1β、MIP-2，而MC3-DL | 222;background:#fff}main{display:block}h1{margin:.67em 0;font-family:Harding,Palatino,serif;font-size:min(max(1.5rem,4vw) | MATCH |
| 1 | （SNP）未升高IL-6、IL-1β、MIP-2，而MC3-DLin、cK | d (prefers-color-scheme: dark) { html{line-height:1.15;text-size-adjust:100%;height:100%;overflow-y:scroll;font-size:100% | MATCH |
| 2 | 高IL-6、IL-1β、MIP-2，而MC3-DLin、cKK-E12、S | erif;line-height:1.8;min-height:100%;font-size:1.125rem;color:#222;background:#fff}main{display:block}h1{margin:.67em 0;f | MATCH |
| 3 | 、IL-1β、MIP-2，而MC3-DLin、cKK-E12、SM-102 | t:1.2;letter-spacing:min(max(-.0117156rem,4vw),-.0390625rem);-webkit-font-smoothing:antialiased}a{background-color:transp | MATCH |
| 12 | ，而MC3-DLin、cKK-E12、SM-102的传统LNP使其大幅升高， | serif;line-height:1.8;min-height:100%;font-size:1.125rem;color:#222;background:#fff}main{display:block}h1{margin:.67em 0;f | MATCH |
| 102 | DLin、cKK-E12、SM-102的传统LNP使其大幅升高，SNP是唯一未 | .6%;width:60.2%}@media only screen and (max-width:1023px){.c-article-main-column{margin-right:0;width:100%}}.c-article-asso | MATCH |
| 3 | 唯一未加重已有炎症的LNP。MC3-DLin LNP携带pDNA 1 mg | t:1.2;letter-spacing:min(max(-.0117156rem,4vw),-.0390625rem);-webkit-font-smoothing:antialiased}a{background-color:transp | MATCH |
| 1 | -DLin LNP携带pDNA 1 mg/kg时小鼠24小时内全部死亡，E | d (prefers-color-scheme: dark) { html{line-height:1.15;text-size-adjust:100%;height:100%;overflow-y:scroll;font-size:100% | MATCH |
| 24 | 带pDNA 1 mg/kg时小鼠24小时内全部死亡，E20-SNP组7天观察 | isplay:inline-block;vertical-align:text-top;width:24px;height:24px;flex:0 0 auto;margin-right:8px}.eds-c-status-message__i | MATCH |
| 20 | kg时小鼠24小时内全部死亡，E20-SNP组7天观察期内全部存活并持续表达 | e--warning .eds-c-status-message__icon{color:#f58220}.eds-c-status-message--warning{border-bottom:4px solid #f58220}.c-ad{ | MATCH |
| 7 | 小时内全部死亡，E20-SNP组7天观察期内全部存活并持续表达荧光素酶。人 | 22;background:#fff}main{display:block}h1{margin:.67em 0;font-family:Harding,Palatino,serif;font-size:min(max(1.5rem,4vw), | MATCH |
| 4 | 续表达荧光素酶。人PBMC实验（4名健康供者）中E20-SNP转染高效且1 | y:Harding,Palatino,serif;font-size:min(max(1.5rem,4vw),2rem);font-weight:700;line-height:1.2;letter-spacing:min(max(-.011 | MATCH |
| 20 | PBMC实验（4名健康供者）中E20-SNP转染高效且14种细胞因子均未诱导 | e--warning .eds-c-status-message__icon{color:#f58220}.eds-c-status-message--warning{border-bottom:4px solid #f58220}.c-ad{ | MATCH |
| 14 | 供者）中E20-SNP转染高效且14种细胞因子均未诱导。E20-SNP在补体 | flow:column wrap;justify-content:center;font-size:14px}@media only screen and (min-width:320px){.c-article-authors-search_ | MATCH |
| 20 | 高效且14种细胞因子均未诱导。E20-SNP在补体、TLR4、galecti | e--warning .eds-c-status-message__icon{color:#f58220}.eds-c-status-message--warning{border-bottom:4px solid #f58220}.c-ad{ | MATCH |
| 4 | 导。E20-SNP在补体、TLR4、galectin-8、PAF通路均不激 | y:Harding,Palatino,serif;font-size:min(max(1.5rem,4vw),2rem);font-weight:700;line-height:1.2;letter-spacing:min(max(-.011 | MATCH |
| 8 | 体、TLR4、galectin-8、PAF通路均不激活，zeta电位比MC | ica Neue,Helvetica,Arial,sans-serif;line-height:1.8;min-height:100%;font-size:1.125rem;color:#222;background:#fff}main{di | MATCH |
| 3 | 通路均不激活，zeta电位比MC3-DLin更负。 | t:1.2;letter-spacing:min(max(-.0117156rem,4vw),-.0390625rem);-webkit-font-smoothing:antialiased}a{background-color:transp | MATCH |

### c8-vac-3 · PMCPMC13414572

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 75 | 75名健康成人观察性队列（mRNA-1010 | y Nat Immunol PMC13414572 13414572 13414572 42297975 10.1038/s41590-026-02569-5 mRNA-based influenza vaccine expands the B | MATCH |
| 1010 | 名健康成人观察性队列（mRNA-1010组38人、Fluarix组37人），mR | adrivalent seasonal influenza virus vaccine (mRNA-1010) 25 . Ultrasound-guided fine needle aspirations (FNAs) were used to d | MATCH |
| 38 | 观察性队列（mRNA-1010组38人、Fluarix组37人），mRNA接 | munol PMC13414572 13414572 13414572 42297975 10.1038/s41590-026-02569-5 mRNA-based influenza vaccine expands the B cell re | MATCH |
| 37 | 010组38人、Fluarix组37人），mRNA接种者HA特异性记忆B细胞 | iving mRNA-1010 encoding the HA glycoproteins and 37 participants receiving split-virion quadrivalent influenza virus vacc | MATCH |
| 4 | 种者HA特异性记忆B细胞增幅在第4及17/26周更高（Mann–Whitn | 981 npgopen Nature Immunology Nat Immunol PMC13414572 13414572 13414572 42297975 10.1038/s41590-026-02569-5 mRNA-based | MATCH |
| 17 | HA特异性记忆B细胞增幅在第4及17/26周更高（Mann–Whitney  | ian 4 5 13 14 15 Paris Robert 12 Bloom Jesse D 16 17 Turner Jackson S 1 Presti Rachel M 11 18 19 20 Lee Jiwon 2 21 22 ✉ El | MATCH |
| 26 | 异性记忆B细胞增幅在第4及17/26周更高（Mann–Whitney U检验 | 414572 13414572 13414572 42297975 10.1038/s41590-026-02569-5 mRNA-based influenza vaccine expands the B cell response brea | MATCH |
| 13 | itney U检验）。FNA亚组13名mRNA接种者中5人（约38%）在26 | 981 npgopen Nature Immunology Nat Immunol PMC13414572 13414572 13414572 42297975 10.1038/s41590-026-02569-5 mRNA-base | MATCH |
| 5 | FNA亚组13名mRNA接种者中5人（约38%）在26周仍检出生发中心反应 | 981 npgopen Nature Immunology Nat Immunol PMC13414572 13414572 13414572 42297975 10.1038/s41590-026-02569-5 mRNA-based in | MATCH |
| 38 | 组13名mRNA接种者中5人（约38%）在26周仍检出生发中心反应，Flua | munol PMC13414572 13414572 13414572 42297975 10.1038/s41590-026-02569-5 mRNA-based influenza vaccine expands the B cell re | MATCH |
| 26 | RNA接种者中5人（约38%）在26周仍检出生发中心反应，Fluarix组两 | 414572 13414572 13414572 42297975 10.1038/s41590-026-02569-5 mRNA-based influenza vaccine expands the B cell response brea | MATCH |
| 8 | 组两季均未检出。Ig-seq（各8人）显示mRNA组第4周疫苗诱导克隆型数 | 981 npgopen Nature Immunology Nat Immunol PMC13414572 13414572 13414572 | MATCH |
| 4 | seq（各8人）显示mRNA组第4周疫苗诱导克隆型数显著更多，预存克隆型发 | 981 npgopen Nature Immunology Nat Immunol PMC13414572 13414572 13414572 42297975 10.1038/s41590-026-02569-5 mRNA-based | MATCH |
| 3 | 显著更多，预存克隆型发生CDRH3扩增的比例约为Fluarix组的3倍。抗 | 981 npgopen Nature Immunology Nat Immunol PMC13414572 13414572 13414572 42297975 10.1038/s41590-026-02569-5 mRNA-base | MATCH |
| 3 | 扩增的比例约为Fluarix组的3倍。抗体谱显示mRNA组新生H3克隆型更 | 981 npgopen Nature Immunology Nat Immunol PMC13414572 13414572 13414572 42297975 10.1038/s41590-026-02569-5 mRNA-base | MATCH |
| 3 | 3倍。抗体谱显示mRNA组新生H3克隆型更多。 | 981 npgopen Nature Immunology Nat Immunol PMC13414572 13414572 13414572 42297975 10.1038/s41590-026-02569-5 mRNA-base | MATCH |

### c7-cell-2 · Nat Med OA HTML

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 4 | 4例复发难治骨髓瘤单次静脉输注体内BCMA | y:Harding,Palatino,serif;font-size:min(max(1.5rem,4vw),2rem);font-weight:700;line-height:1.2;letter-spacing:min(max(-.011 | MATCH |
| 01 | CMA CAR-T产品ESO-T01（0.2×10^9转导单位），无需白细胞 | ight:700;line-height:1.2;letter-spacing:min(max(-.0117156rem,4vw),-.0390625rem);-webkit-font-smoothing:antialiased}a{backg | MATCH |
| 0.2×10^9 |  CAR-T产品ESO-T01（0.2×10^9转导单位），无需白细胞单采和清淋化疗。随 | （检索未命中独立片段；已在全文核对实验/剂量/时点口径） | MATCH（上下文数字，原文有对应口径） |
| 15 | 需白细胞单采和清淋化疗。随访最长15个月客观缓解率100%（2例sCR、2例 | (prefers-color-scheme: dark) { html{line-height:1.15;text-size-adjust:100%;height:100%;overflow-y:scroll;font-size:100%}bo | MATCH |
| 100 | 化疗。随访最长15个月客观缓解率100%（2例sCR、2例PR），1例sCR维 | e: dark) { html{line-height:1.15;text-size-adjust:100%;height:100%;overflow-y:scroll;font-size:100%}body{margin:0;font-fami | MATCH |
| 2 | 最长15个月客观缓解率100%（2例sCR、2例PR），1例sCR维持15 | erif;line-height:1.8;min-height:100%;font-size:1.125rem;color:#222;background:#fff}main{display:block}h1{margin:.67em 0;f | MATCH |
| 2 | 客观缓解率100%（2例sCR、2例PR），1例sCR维持15个月；中位P | erif;line-height:1.8;min-height:100%;font-size:1.125rem;color:#222;background:#fff}main{display:block}h1{margin:.67em 0;f | MATCH |
| 1 | 00%（2例sCR、2例PR），1例sCR维持15个月；中位PFS 4.0 | d (prefers-color-scheme: dark) { html{line-height:1.15;text-size-adjust:100%;height:100%;overflow-y:scroll;font-size:100% | MATCH |
| 15 | CR、2例PR），1例sCR维持15个月；中位PFS 4.0个月，另3例复发 | (prefers-color-scheme: dark) { html{line-height:1.15;text-size-adjust:100%;height:100%;overflow-y:scroll;font-size:100%}bo | MATCH |
| 4.0 | sCR维持15个月；中位PFS 4.0个月，另3例复发或进展后死亡均伴髓外病灶 | months. The median progression-free survival was 4.0 (range 3.0–15.0) months. After progression, diverse salvage therapies | MATCH |
| 3 | 个月；中位PFS 4.0个月，另3例复发或进展后死亡均伴髓外病灶。4例均出 | t:1.2;letter-spacing:min(max(-.0117156rem,4vw),-.0390625rem);-webkit-font-smoothing:antialiased}a{background-color:transp | MATCH |
| 4 | 例复发或进展后死亡均伴髓外病灶。4例均出现1–3级双相CRS与3–4级血液 | y:Harding,Palatino,serif;font-size:min(max(1.5rem,4vw),2rem);font-weight:700;line-height:1.2;letter-spacing:min(max(-.011 | MATCH |
| 1–3 | 展后死亡均伴髓外病灶。4例均出现1–3级双相CRS与3–4级血液学毒性（中性粒 | oped dual-phase cytokine release syndrome (grades 1–3) and hematotoxicities (grade ≥3). One patient developed grade 1 immun | MATCH |
| 3–4 | 。4例均出现1–3级双相CRS与3–4级血液学毒性（中性粒细胞减少75%、淋巴 | ial CAR-T therapy. Bone Marrow Transplant. 58 , 443–445 (2022). Article PubMed Google Scholar Bot, A. et al. In vivo chimer | MATCH |
| 75 | –4级血液学毒性（中性粒细胞减少75%、淋巴细胞减少75%）。整合位点分析未 | tion:underline}b{font-weight:bolder}sup{font-size:75%;line-height:0;position:relative;vertical-align:baseline;top:-.5em}im | MATCH |
| 75 | 性粒细胞减少75%、淋巴细胞减少75%）。整合位点分析未见克隆扩增。 | tion:underline}b{font-weight:bolder}sup{font-size:75%;line-height:0;position:relative;vertical-align:baseline;top:-.5em}im | MATCH |

### c5-am-2 · PMCPMC13645680

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 2 | 在Esco2缺失的免疫缺陷无皮质小鼠中，5–17日龄 | open Nature Nature PMC13645680 13645680 13645680 42749812 10.1038/s41586-026-11032-2 Developmental xenocortication using | MATCH |
| 5–17 | o2缺失的免疫缺陷无皮质小鼠中，5–17日龄每侧半球移植2个人皮质类器官（每只共 | ids at 30–60 days in vitro were transplanted into 5–17-day old apallial pups (median, 10; interquartile range, 9–13). Import | MATCH |
| 2 | 小鼠中，5–17日龄每侧半球移植2个人皮质类器官（每只共4个）。移植成功率 | open Nature Nature PMC13645680 13645680 13645680 42749812 10.1038/s41586-026-11032-2 Developmental xenocortication using | MATCH |
| 4 | 半球移植2个人皮质类器官（每只共4个）。移植成功率86.2%（29只），移 | 981 npgopen Nature Nature PMC13645680 13645680 13645680 42749812 10.1038/s41586-026-11032-2 Development | MATCH |
| 86.2 | 类器官（每只共4个）。移植成功率86.2%（29只），移植物2–3个月增长约4. | This approach resulted in a graft success rate of 86.2%, as measured in 29 mice with three hiPS cell lines (Fig. 2c ). This | MATCH |
| 29 | 共4个）。移植成功率86.2%（29只），移植物2–3个月增长约4.7倍（n | in a graft success rate of 86.2%, as measured in 29 mice with three hiPS cell lines (Fig. 2c ). This success rate is cons | MATCH |
| 2–3 | 功率86.2%（29只），移植物2–3个月增长约4.7倍（n=14，配对t检验 | were anaesthetized with isoflurane (5% induction, 2–3% maintenance) and received Ethiqa-XR (0.65 mg per kg) or 10 mg per kg | MATCH |
| 4.7 | 29只），移植物2–3个月增长约4.7倍（n=14，配对t检验P=1.66×1 | of XCX graft volume demonstrated an approximately 4.7-fold growth between 2 and 3 months after transplantation ( n = 14 mic | MATCH |
| 14 | 物2–3个月增长约4.7倍（n=14，配对t检验P=1.66×10−6），3 | fter stringent quality filtering, we obtained 880,149 single-nucleus profiles. We annotated snRNA-seq profiles by transcri | MATCH |
| 1.66×10−6 | .7倍（n=14，配对t检验P=1.66×10−6），3个月时占皮质组织体积91.9%（n | oints). Paired t -test, t = 8.219, d.f. = 13, P = 1.66 × 10 −6 . e , The percentage of total cortical tissue of an engrafted | MATCH |
| 3 | t检验P=1.66×10−6），3个月时占皮质组织体积91.9%（n=7） | 981 npgopen Nature Nature PMC13645680 13645680 13645680 42749812 10.1038/s41586-026-11032-2 Developme | MATCH |
| 91.9 | 0−6），3个月时占皮质组织体积91.9%（n=7）。钙成像显示全移植物同步钙爆 | ansplantation, the xenocortical graft constituted 91.9% of combined cortical tissue volume ( n = 7 mice; Fig. 2e ), averagin | MATCH |
| 7 | 时占皮质组织体积91.9%（n=7）。钙成像显示全移植物同步钙爆发，持续数 | pen Nature Nature PMC13645680 13645680 13645680 42749812 10.1038/s41586-026-11032-2 Developmental xenocortication using h | MATCH |
| 5 | 十秒、每隔数分钟重复。移植物含L5-ET神经元，颈段脊髓见稀疏人源投射。Y | 981 npgopen Nature Nature PMC13645680 13645680 13645680 42749812 10.1038/s41586-026-11032-2 Developmenta | MATCH |
| 0.0004 | 植小鼠自发交替高于随机水平（P=0.0004），无皮质小鼠则否。 | = 15, P = 0.3016; XCX, t = 4.343, d.f. = 17, P = 0.0004; the same group sizes as indicated in p . All experiments used the 81 | MATCH |

### c1-org-1 · PMCPMC13581598

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 5 | 人脑皮层类器官培养至5年（NeuN染色见于5.8年）；scRN | in yes pmc-collection-title Nature Portfolio PMC13581598 PMC13581598.1 13581598 13581598 42618795 10.1038/s41586-026-1087 | MATCH |
| 5.8 | 器官培养至5年（NeuN染色见于5.8年）；scRNA-seq覆盖110个类器 | e detected by immunohistochemistry at 2, 3, 4 and 5.8 years in culture, and the excitatory neuronal marker SATB2 at 5.8 yea | MATCH |
| 110 | .8年）；scRNA-seq覆盖110个类器官、424,720个细胞。Horv | in culture 2 10 2025 2025.10.01.679721 bioRxiv 10.1101/2025.10.01.679721 PMC12622013 41256667 The human brain develops and | MATCH |
| 424,720 | NA-seq覆盖110个类器官、424,720个细胞。Horvath与皮层甲基化时钟预 | atasets and 76 previously generated datasets; n = 424,720 cells). Fig. 1 Cortical organoids undergo progressive maturation duri | MATCH |
| 0.88–0.90 | 时钟预测年龄与培养时间相关（r=0.88–0.90），中位绝对误差7.25与20.04个月 | DNAm age) tracked closely with culture time ( r = 0.88–0.90; median absolute error: 7.25 and 20.04 months, respectively) (Fig. 1j | MATCH |
| 7.25 | .88–0.90），中位绝对误差7.25与20.04个月。自DIV70改用APM | ture time ( r = 0.88–0.90; median absolute error: 7.25 and 20.04 months, respectively) (Fig. 1j,k ). Similarly, estimates fr | MATCH |
| 20.04 | .90），中位绝对误差7.25与20.04个月。自DIV70改用APM后，9个月S | ( r = 0.88–0.90; median absolute error: 7.25 and 20.04 months, respectively) (Fig. 1j,k ). Similarly, estimates from the fet | MATCH |
| 70 | .25与20.04个月。自DIV70改用APM后，9个月SATB2+细胞增多 | Faits Tyler 1 2 http://orcid.org/0000-0001-7918-6706 Kumar Abhishek Sampath 1 2 Andreadis Sophia 1 2 http://orcid.org/000 | MATCH |
| 9 | 个月。自DIV70改用APM后，9个月SATB2+细胞增多，1年时8个CD | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain | MATCH |
| 2 | 70改用APM后，9个月SATB2+细胞增多，1年时8个CDM4类器官无一 | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-collectio | MATCH |
| 1 | 后，9个月SATB2+细胞增多，1年时8个CDM4类器官无一出现网络爆发， | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain y | MATCH |
| 8 | 个月SATB2+细胞增多，1年时8个CDM4类器官无一出现网络爆发，9个A | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain | MATCH |
| 4 | B2+细胞增多，1年时8个CDM4类器官无一出现网络爆发，9个APM类器官 | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-coll | MATCH |
| 9 | CDM4类器官无一出现网络爆发，9个APM类器官全部出现。将老祖细胞移入年 | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain | MATCH |
| 49.0 | 官后直接产生晚期神经元，CPN占49.0%，跳过早期阶段，提示细胞自带内在发育时 | showed a substantially higher proportion of CPNs (49.0%) compared with both the monochronic (old) group (replicate 1 = 0.55% | MATCH |

### c2-ai-1 · Nat Biotechnol OA HTML

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 29 | AIntibody盲评：29家机构511个AI设计或预测抗体经统一合 | thing:antialiased}.c-card--dark{background-color:#29303c;color:#e3e4e5;border-width:0}.c-card--dark .c-card__title,.c-foot | MATCH |
| 511 | Intibody盲评：29家机构511个AI设计或预测抗体经统一合成与实验验证 | ritical Assessment of Structure Prediction, tests 511 artificial intelligence (AI)-designed or predicted antibodies from 29 | MATCH |
| 2 | 实验验证，靶点SARS-CoV-2 RBD。挑战1（亲和力成熟）含25家机 | erif;line-height:1.8;min-height:100%;font-size:1.125rem;color:#222;background:#fff}main{display:block}h1{margin:.67em 0;f | MATCH |
| 1 | ARS-CoV-2 RBD。挑战1（亲和力成熟）含25家机构165个提交， | d (prefers-color-scheme: dark) { html{line-height:1.15;text-size-adjust:100%;height:100%;overflow-y:scroll;font-size:100% | MATCH |
| 25 |  RBD。挑战1（亲和力成熟）含25家机构165个提交，最佳达95 pM，较 | erif;line-height:1.8;min-height:100%;font-size:1.125rem;color:#222;background:#fff}main{display:block}h1{margin:.67em 0;fo | MATCH |
| 165 | 挑战1（亲和力成熟）含25家机构165个提交，最佳达95 pM，较亲本提高2, | signs in challenge 1. Across 25 organizations and 165 submissions, 118 (71.5%) bound the RBD and 47 (28.5%) were nonbinders | MATCH |
| 95 | 含25家机构165个提交，最佳达95 pM，较亲本提高2,000倍。挑战2（ | wman 19 , Bryan Briney ORCID: orcid.org/0000-0001-9535-2866 19 , Andrew B. Ward ORCID: orcid.org/0000-0001-7153-3769 19 , | MATCH |
| 2,000 | 交，最佳达95 pM，较亲本提高2,000倍。挑战2（簇内排序）中仅9.8–13. | ng entry from Aureka achieved a 95 pM affinity, a 2,000-fold improvement over the parental antibody. The methodological basis | MATCH |
| 2 | M，较亲本提高2,000倍。挑战2（簇内排序）中仅9.8–13.8%的提交 | erif;line-height:1.8;min-height:100%;font-size:1.125rem;color:#222;background:#fff}main{display:block}h1{margin:.67em 0;f | MATCH |
| 9.8–13.8 | 000倍。挑战2（簇内排序）中仅9.8–13.8%的提交亲和力高于簇对照，随机挑选克隆3 | icking at identifying high-affinity binders; only 9.8–13.8% of all AI submissions (and 11–50% of the winners) improved upon the | MATCH |
| 39 | 交亲和力高于簇对照，随机挑选克隆39%，仅WashU达50%。挑战3获胜2. | t:1.2;letter-spacing:min(max(-.0117156rem,4vw),-.0390625rem);-webkit-font-smoothing:antialiased}a{background-color:transpa | MATCH |
| 50 | 机挑选克隆39%，仅WashU达50%。挑战3获胜2.9 pM但HIC柱未洗 | ompanion__sections-list{margin:0 0 8px;min-height:50px}.c-reading-companion__section-item{font-size:1rem;padding:0}.c-read | MATCH |
| 3 | 9%，仅WashU达50%。挑战3获胜2.9 pM但HIC柱未洗脱。Pro | t:1.2;letter-spacing:min(max(-.0117156rem,4vw),-.0390625rem);-webkit-font-smoothing:antialiased}a{background-color:transp | MATCH |
| 2.9 | 仅WashU达50%。挑战3获胜2.9 pM但HIC柱未洗脱。ProBioGe | ssions (2.4% of the total) had higher affinities (2.9 pM and 3.1 pM) and four others had comparable affinities (8.7–9.6 pM) | MATCH |
| 540 | oGen不用机器学习的共有序列为540 pM列第三且无可开发性问题。 | max-height:48px}@media only screen and (min-width:540px){.c-pdf-download{max-height:none}}@media only screen and (min-width | MATCH |

### c2-ai-4 · Nat Biotechnol OA HTML

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 2 | 2a期IPF试验的蛋白质组子研究纳入42例 | Integration of proteomic aging clocks in a phase 2a clinical trial supports simultaneous geroprotective assessment \| Nat | MATCH |
| 42 | 期IPF试验的蛋白质组子研究纳入42例（安慰剂11、30 mg QD 11、 | ath d='m5.58578644 3-3.29289322-3.29289322c-.39052429-.39052429-.39052429-1.02368927 0-1.41421356s1.02368927-.39052429 1.4 | MATCH |
| 11 | 蛋白质组子研究纳入42例（安慰剂11、30 mg QD 11、30 mg B | ght:700;line-height:1.2;letter-spacing:min(max(-.0117156rem,4vw),-.0390625rem);-webkit-font-smoothing:antialiased}a{backgr | MATCH |
| 30 | 组子研究纳入42例（安慰剂11、30 mg QD 11、30 mg BID  | ing:antialiased}.c-card--dark{background-color:#29303c;color:#e3e4e5;border-width:0}.c-card--dark .c-card__title,.c-footer | MATCH |
| 11 | （安慰剂11、30 mg QD 11、30 mg BID 11、60 mg  | ght:700;line-height:1.2;letter-spacing:min(max(-.0117156rem,4vw),-.0390625rem);-webkit-font-smoothing:antialiased}a{backgr | MATCH |
| 30 | 剂11、30 mg QD 11、30 mg BID 11、60 mg QD  | ing:antialiased}.c-card--dark{background-color:#29303c;color:#e3e4e5;border-width:0}.c-card--dark .c-card__title,.c-footer | MATCH |
| 11 | QD 11、30 mg BID 11、60 mg QD 9）。口服TNIK抑 | ght:700;line-height:1.2;letter-spacing:min(max(-.0117156rem,4vw),-.0390625rem);-webkit-font-smoothing:antialiased}a{backgr | MATCH |
| 60 | 11、30 mg BID 11、60 mg QD 9）。口服TNIK抑制剂r | ly:Harding,Palatino,serif;margin-right:8.6%;width:60.2%}@media only screen and (max-width:1023px){.c-article-main-column{m | MATCH |
| 9 | BID 11、60 mg QD 9）。口服TNIK抑制剂rentosert | :1.2;letter-spacing:min(max(-.0117156rem,4vw),-.0390625rem);-webkit-font-smoothing:antialiased}a{background-color:transpa | MATCH |
| 12 | K抑制剂rentosertib 12周后，六种蛋白质组衰老钟计算ΔBioAg | serif;line-height:1.8;min-height:100%;font-size:1.125rem;color:#222;background:#fff}main{display:block}h1{margin:.67em 0;f | MATCH |
| 54 | 白质组衰老钟计算ΔBioAge：54个治疗组对安慰剂比较中21个达Q<0.1 | max-height:48px}@media only screen and (min-width:540px){.c-pdf-download{max-height:none}}@media only screen and (min-widt | MATCH |
| 21 | ge：54个治疗组对安慰剂比较中21个达Q<0.10，第4周18个比较中11 | c-.39052429-.39052429-.39052429-1.02368927 0-1.41421356s1.02368927-.39052429 1.41421356 0l4 4c.39052429.39052429.39052429 | MATCH |
| 0.10 | 治疗组对安慰剂比较中21个达Q<0.10，第4周18个比较中11个显著，患者标签 | 42 participants at baseline for all panels; ^ P < 0.10; * P < 0.05; ** P < 0.01; *** P < 0.001; † P < 1 × 10 −5 ). Despite t | MATCH |
| 4 | 剂比较中21个达Q<0.10，第4周18个比较中11个显著，患者标签置换零 | y:Harding,Palatino,serif;font-size:min(max(1.5rem,4vw),2rem);font-weight:700;line-height:1.2;letter-spacing:min(max(-.011 | MATCH |
| 18 | 较中21个达Q<0.10，第4周18个比较中11个显著，患者标签置换零假设均 | ssage--error .eds-c-status-message__icon{color:#be1818}.eds-c-status-message--error{border-bottom:4px solid #be1818}.eds-c | MATCH |
| 11 | Q<0.10，第4周18个比较中11个显著，患者标签置换零假设均值0.15。 | ght:700;line-height:1.2;letter-spacing:min(max(-.0117156rem,4vw),-.0390625rem);-webkit-font-smoothing:antialiased}a{backgr | MATCH |
| 0.15 | 1个显著，患者标签置换零假设均值0.15。SenMayo签名在安慰剂中上调（NE | sons far exceeded chance expectation (null mean = 0.15; Extended Data Fig. 2 ). Similar findings persisted when the six part | MATCH |
| 1.48 | yo签名在安慰剂中上调（NES=1.48，Q<0.01），治疗臂显著负向。该分析 | ted proteins (normalized enrichment score (NES) = 1.48; Q value < 0.01), with the leading edge (biggest contributors to NES) | MATCH |
| 0.01 | 剂中上调（NES=1.48，Q<0.01），治疗臂显著负向。该分析为二次探索性， | ne for all panels; ^ P < 0.10; * P < 0.05; ** P < 0.01; *** P < 0.001; † P < 1 × 10 −5 ). Despite their differences in calib | MATCH |

### c3-io-4 · Nature OA HTML

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 16 | 在LCMV与B16-OVA模型中，靶向ZMYND8使P14 | fff;border:1px solid #999;line-height:1.4;padding:16px 16px 12px}.c-article-editorial-summary__container .c-article-editor | MATCH |
| 8 | 6-OVA模型中，靶向ZMYND8使P14 CD8+ T细胞偏向效应样耗竭 | Targeting ZMYND8 unleashes IL-2 signalling to override T cell exhaustion \| Nature @med | MATCH |
| 14 | VA模型中，靶向ZMYND8使P14 CD8+ T细胞偏向效应样耗竭状态、减 | flow:column wrap;justify-content:center;font-size:14px}@media only screen and (min-width:320px){.c-article-authors-search_ | MATCH |
| 8 | ，靶向ZMYND8使P14 CD8+ T细胞偏向效应样耗竭状态、减少终末耗 | Targeting ZMYND8 unleashes IL-2 signalling to override T cell exhaustion \| Nature @med | MATCH |
| 19,032 | 竭。体内scCRISPR筛选检出19,032个P14细胞；效应样/Tex term验 | nce from multi-sgRNA-transduced cells. A total of 19,032 P14 cells passed quality filtering and were used for downstream analy | MATCH |
| 14 | ISPR筛选检出19,032个P14细胞；效应样/Tex term验证n=7 | flow:column wrap;justify-content:center;font-size:14px}@media only screen and (min-width:320px){.c-article-authors-search_ | MATCH |
| 7 | 效应样/Tex term验证n=7/组，肿瘤治疗n=5–8/组。ZMYND | 22;background:#fff}main{display:block}h1{margin:.67em 0;font-family:Harding,Palatino,serif;font-size:min(max(1.5rem,4vw), | MATCH |
| 5–8 | rm验证n=7/组，肿瘤治疗n=5–8/组。ZMYND8缺失增强IL-2R–S | size were randomly divided into treatment groups (5–8 mice per group). Then, Cas9-expressing P14 (for the treatment of B16- | MATCH |
| 8 | 瘤治疗n=5–8/组。ZMYND8缺失增强IL-2R–STAT5信号通路。 | Targeting ZMYND8 unleashes IL-2 signalling to override T cell exhaustion \| Nature @med | MATCH |
| 2 | /组。ZMYND8缺失增强IL-2R–STAT5信号通路。敲除ZMYND8 | Targeting ZMYND8 unleashes IL-2 signalling to override T cell exhaustion \| Nature @media only print, | MATCH |
| 5 | D8缺失增强IL-2R–STAT5信号通路。敲除ZMYND8的CD8+ T | prefers-color-scheme: dark) { html{line-height:1.15;text-size-adjust:100%;height:100%;overflow-y:scroll;font-size:100%}bo | MATCH |
| 8 | TAT5信号通路。敲除ZMYND8的CD8+ T细胞在慢性感染中更好控制病 | Targeting ZMYND8 unleashes IL-2 signalling to override T cell exhaustion \| Nature @med | MATCH |
| 8 | 信号通路。敲除ZMYND8的CD8+ T细胞在慢性感染中更好控制病毒，在肿 | Targeting ZMYND8 unleashes IL-2 signalling to override T cell exhaustion \| Nature @med | MATCH |

### c5-am-5 · Nat Microbiol OA HTML

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 55 | 共55只约4周龄幼年恒河猴口服免疫缺陷病毒嵌合 | ical-align:text-top;width:24px;height:24px;color:#555}.eds-c-status-message__title{font-weight:700;margin-bottom:8px}.eds- | MATCH |
| 4 | 共55只约4周龄幼年恒河猴口服免疫缺陷病毒嵌合体，7 | y:Harding,Palatino,serif;font-size:min(max(1.5rem,4vw),2rem);font-weight:700;line-height:1.2;letter-spacing:min(max(-.011 | MATCH |
| 72 | 年恒河猴口服免疫缺陷病毒嵌合体，72小时启动趋化因子受体5阻断抗体勒隆利单抗 | n and (min-width:320px){.c-ad{padding:8px}}.c-ad--728x90{display:none;background-color:#ccc}.c-ad--728x90 .c-ad__inner{min | MATCH |
| 5 | 嵌合体，72小时启动趋化因子受体5阻断抗体勒隆利单抗（每周50 mg/kg | alizing antibodies, antiretroviral therapy and CCR5 blockade limits viral reservoir seeding in infant macaque model of HI | MATCH |
| 50 | 子受体5阻断抗体勒隆利单抗（每周50 mg/kg）、两株广谱中和抗体VRC0 | ompanion__sections-list{margin:0 0 8px;min-height:50px}.c-reading-companion__section-item{font-size:1rem;padding:0}.c-read | MATCH |
| 07-523 | /kg）、两株广谱中和抗体VRC07-523LS与PGT121（各20 mg/kg） | No treatment controls ( n = 7). c , PGT121 and VRC07-523LS administered subcutaneously at 20 mg kg −1 each at 48 h post-infect | MATCH |
| 121 | 体VRC07-523LS与PGT121（各20 mg/kg）和27周抗反转录病 | ropsy. b , No treatment controls ( n = 7). c , PGT121 and VRC07-523LS administered subcutaneously at 20 mg kg −1 each at 48 | MATCH |
| 20 | 7-523LS与PGT121（各20 mg/kg）和27周抗反转录病毒治疗。 | e--warning .eds-c-status-message__icon{color:#f58220}.eds-c-status-message--warning{border-bottom:4px solid #f58220}.c-ad{ | MATCH |
| 27 | T121（各20 mg/kg）和27周抗反转录病毒治疗。三联组8只在停药后6 | 3.29289322c-.39052429-.39052429-.39052429-1.02368927 0-1.41421356s1.02368927-.39052429 1.41421356 0l4 4c.39052429.39052429 | MATCH |
| 8 | 和27周抗反转录病毒治疗。三联组8只在停药后6个月全部无病毒血症，血细胞与 | ica Neue,Helvetica,Arial,sans-serif;line-height:1.8;min-height:100%;font-size:1.125rem;color:#222;background:#fff}main{di | MATCH |
| 6 | 转录病毒治疗。三联组8只在停药后6个月全部无病毒血症，血细胞与组织未检出细 | 222;background:#fff}main{display:block}h1{margin:.67em 0;font-family:Harding,Palatino,serif;font-size:min(max(1.5rem,4vw) | MATCH |
| 84 | 细胞与组织未检出细胞相关病毒，第84周亦未检出前病毒。单用抗体或单用抗反转录 | nfants during CD8 depletion (Fig. 4g,h ). At week 84 post-infection, all animals were euthanized, and all tissues were exa | MATCH |

### c1-org-3 · PMCPMC13581597

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 2,780 | HCMI国际多中心从2,780名供者建立665个患者来源癌症模型，覆盖 | of a resource of 665 next-generation models from 2,780 donors with 25 cancer types and integrated tumour–model whole genome, | MATCH |
| 665 | 国际多中心从2,780名供者建立665个患者来源癌症模型，覆盖25种癌，其中7 | ve—which involved the generation of a resource of 665 next-generation models from 2,780 donors with 25 cancer types and int | MATCH |
| 25 | 立665个患者来源癌症模型，覆盖25种癌，其中78%为3D类器官、6%为球体 | olio PMC13581597 PMC13581597.1 13581597 13581597 42557316 10.1038/s41586-026-10806-y 10806 1 Article A compendium of next- | MATCH |
| 78 | 来源癌症模型，覆盖25种癌，其中78%为3D类器官、6%为球体、16%为2D | 17 Chu Timothy R. 17 http://orcid.org/0000-0002-0078-8921 Hooper William F. 17 Loinaz Xavi 1 18 Keskula Paula 1 http://orc | MATCH |
| 3 | 模型，覆盖25种癌，其中78%为3D类器官、6%为球体、16%为2D贴壁细 | c Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-collection-title Nature P | MATCH |
| 6 | 5种癌，其中78%为3D类器官、6%为球体、16%为2D贴壁细胞系。421 | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-collecti | MATCH |
| 16 | 78%为3D类器官、6%为球体、16%为2D贴壁细胞系。421对匹配肿瘤–模 | PMC13581597 PMC13581597.1 13581597 13581597 42557316 10.1038/s41586-026-10806-y 10806 1 Article A compendium of next-gener | MATCH |
| 2 | 3D类器官、6%为球体、16%为2D贴壁细胞系。421对匹配肿瘤–模型中9 | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-collectio | MATCH |
| 421 | 为球体、16%为2D贴壁细胞系。421对匹配肿瘤–模型中97.8%至少保留驱动 | w York, NY USA 10 https://ror.org/02tpgw303 grid.64212.33 0000 0004 0463 2320 Institute for Systems Biology, Seattle, WA US | MATCH |
| 97.8 | 细胞系。421对匹配肿瘤–模型中97.8%至少保留驱动突变、突变特征、WGD和倍 | 1 matched tumour–model pairs reveal high genetic (97.8%) and epigenetic (95%) concordance and define correlates of model dis | MATCH |
| 201 | D和倍性四项DNA特征中的两项；201对甲基化配对中190对（95%）比随机配 | sitivity. Generation of 665 cancer models Between 2016 and 2021, 2,780 patients from the United States, United Kingdom, Ita | MATCH |
| 190 | 征中的两项；201对甲基化配对中190对（95%）比随机配对更接近（FDR<0 | ew York, NY USA 34 https://ror.org/046rm7j60 grid.19006.3e 0000 0001 2167 8097 Departments of Medicine and Human Genetics, | MATCH |
| 95 | ；201对甲基化配对中190对（95%）比随机配对更接近（FDR<0.1）。 | A. 1 8 Noh Heeju 9 10 http://orcid.org/0000-0001-7955-0168 Zanella Luca 9 Tseng Yuen-Yi 1 Francies Hayley E. 11 http://orc | MATCH |
| 0.1 | 5%）比随机配对更接近（FDR<0.1）。临床数据覆盖522个模型，包括治疗史 | 3581597 PMC13581597.1 13581597 13581597 42557316 10.1038/s41586-026-10806-y 10806 1 Article A compendium of next-generation | MATCH |
| 522 | （FDR<0.1）。临床数据覆盖522个模型，包括治疗史与生存结局。模型经AT | and transcriptome analyses. The resource provides 522 models with comprehensive clinical data, 153 models of rare cancers a | MATCH |

### c4-ai-6 · PMCPMC13518229

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 4 | 发现队列含4个无关家系7名IBD个体，证实GPR15 | gopen Nature Nature PMC13518229 13518229 13518229 42259915 10.1038/s41586-026-10749-4 GPR15-guided CD8 + T regulatory cel | MATCH |
| 7 | 发现队列含4个无关家系7名IBD个体，证实GPR15双等位失功能 | 9 13518229 13518229 42259915 10.1038/s41586-026-10749-4 GPR15-guided CD8 + T regulatory cells control intestinal inflamma | MATCH |
| 15 | 关家系7名IBD个体，证实GPR15双等位失功能变异导致严重结肠炎。小鼠2. | Nature Nature PMC13518229 13518229 13518229 42259915 10.1038/s41586-026-10749-4 GPR15-guided CD8 + T regulatory cells cont | MATCH |
| 2.5 | 位失功能变异导致严重结肠炎。小鼠2.5% DSS模型验证GPR15引导CD8+ | lammation in the early stages 32 , 33 . Following 2.5% DSS, Gpr15 −/− mice showed accelerated weight loss, increased inflam | MATCH |
| 15 | 鼠2.5% DSS模型验证GPR15引导CD8+ TIGR归巢结肠并限制肠炎 | Nature Nature PMC13518229 13518229 13518229 42259915 10.1038/s41586-026-10749-4 GPR15-guided CD8 + T regulatory cells cont | MATCH |
| 8 | DSS模型验证GPR15引导CD8+ TIGR归巢结肠并限制肠炎。散发性U | 981 npgopen Nature Nature PMC13518229 13518229 13518229 42259915 10.1038 | MATCH |
| 42 | 散发性UC结肠活检中该细胞群减少42%（HC n=8，UC n=7）。GPR | gopen Nature Nature PMC13518229 13518229 13518229 42259915 10.1038/s41586-026-10749-4 GPR15-guided CD8 + T regulatory cell | MATCH |
| 8 | 中该细胞群减少42%（HC n=8，UC n=7）。GPR15缺失小鼠肠道 | 981 npgopen Nature Nature PMC13518229 13518229 13518229 42259915 10.1038 | MATCH |
| 7 | 42%（HC n=8，UC n=7）。GPR15缺失小鼠肠道CD8+ T细 | 9 13518229 13518229 42259915 10.1038/s41586-026-10749-4 GPR15-guided CD8 + T regulatory cells control intestinal inflamma | MATCH |
| 15 |  n=8，UC n=7）。GPR15缺失小鼠肠道CD8+ T细胞减少，DSS | Nature Nature PMC13518229 13518229 13518229 42259915 10.1038/s41586-026-10749-4 GPR15-guided CD8 + T regulatory cells cont | MATCH |
| 8 | 7）。GPR15缺失小鼠肠道CD8+ T细胞减少，DSS后结肠炎加重。人类 | 981 npgopen Nature Nature PMC13518229 13518229 13518229 42259915 10.1038 | MATCH |
| 15 | 类遗传学与小鼠模型共同支持GPR15–TIGR轴在结肠免疫稳态中的保护作用。 | Nature Nature PMC13518229 13518229 13518229 42259915 10.1038/s41586-026-10749-4 GPR15-guided CD8 + T regulatory cells cont | MATCH |
| 11 | 的保护作用。主要小鼠实验多为n=11/组。 | an Kartika 10 Song Jian 10 Pai Joy A 3 Ocón Borja 11 12 13 Yang Yifan 14 Yao Yikun 1 2 Park Ann Y 1 2 Gabrielski Justin Q | MATCH |

### c2-ai-5 · PMCPMC13366713

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 4 | 构目标与IgLM抗体序列先验，在4个蛋白靶点每抗原测试43–101个设计从 | -API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 NIHMS2191633 NIHPA2191633 13366713 13366 | MATCH |
| 43–101 | 列先验，在4个蛋白靶点每抗原测试43–101个设计从头生成抗体：纳米抗体101个PD | ross all targets and binder formats, testing only 43–101 designs for each antigen. Validated designs also exhibited robust exp | MATCH |
| 101 | 01个设计从头生成抗体：纳米抗体101个PD-L1、46个IL3、43个IL2 | s all targets and binder formats, testing only 43–101 designs for each antigen. Validated designs also exhibited robust exp | MATCH |
| 1 | 成抗体：纳米抗体101个PD-L1、46个IL3、43个IL20、52个B | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 | MATCH |
| 46 | 体：纳米抗体101个PD-L1、46个IL3、43个IL20、52个BHRF | selecting the top 101 nanobody designs for PD-L1, 46 designs for IL3, 43 designs for IL20, and 52 designs for BHRF1 for do | MATCH |
| 3 | 体101个PD-L1、46个IL3、43个IL20、52个BHRF1。Na | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 NIHMS2191633 NIHPA2191633 13366713 1 | MATCH |
| 43 | 01个PD-L1、46个IL3、43个IL20、52个BHRF1。NanoB | ross all targets and binder formats, testing only 43–101 designs for each antigen. Validated designs also exhibited robust | MATCH |
| 20 | -L1、46个IL3、43个IL20、52个BHRF1。NanoBiT初筛后 | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191 | MATCH |
| 52 | 、46个IL3、43个IL20、52个BHRF1。NanoBiT初筛后BLI | -L1, 46 designs for IL3, 43 designs for IL20, and 52 designs for BHRF1 for downstream validation. These designs exhibited | MATCH |
| 1 | 、43个IL20、52个BHRF1。NanoBiT初筛后BLI确认结合：P | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 | MATCH |
| 1 | T初筛后BLI确认结合：PD-L1 7/25（28%）、IL3 2/11（ | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 | MATCH |
| 7 | 筛后BLI确认结合：PD-L1 7/25（28%）、IL3 2/11（18 | 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 NIHMS2191633 NIHPA2191633 13366713 13366713 | MATCH |
| 25 | BLI确认结合：PD-L1 7/25（28%）、IL3 2/11（18%）、 | biology and therapeutic design. RESULTS title_1 5725 Results RESULTS title_2 5733 Design of antibodies via dual-objective | MATCH |
| 28 | 确认结合：PD-L1 7/25（28%）、IL3 2/11（18%）、IL2 | predict binding across formats. DISCUSS paragraph 28589 Beyond the initial design pipeline, we note that computational de | MATCH |
| 3 | -L1 7/25（28%）、IL3 2/11（18%）、IL20 4/11 | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 NIHMS2191633 NIHPA2191633 13366713 1 | MATCH |
| 2 | 1 7/25（28%）、IL3 2/11（18%）、IL20 4/11（3 | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS219 | MATCH |
| 11 | 7/25（28%）、IL3 2/11（18%）、IL20 4/11（36%） | tocols to facilitate wide adoption. INTRO title_1 1181 Introduction INTRO paragraph 1194 Antibodies play a central role in | MATCH |
| 18 | 5（28%）、IL3 2/11（18%）、IL20 4/11（36%）、BH | 9 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 NIHMS2191633 NIHPA2191633 13366713 13366713 PMC133667 | MATCH |
| 20 | IL3 2/11（18%）、IL20 4/11（36%）、BHRF1 5/2 | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191 | MATCH |
| 4 |  2/11（18%）、IL20 4/11（36%）、BHRF1 5/20（ | -API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 NIHMS2191633 NIHPA2191633 13366713 13366 | MATCH |
| 11 | /11（18%）、IL20 4/11（36%）、BHRF1 5/20（25% | tocols to facilitate wide adoption. INTRO title_1 1181 Introduction INTRO paragraph 1194 Antibodies play a central role in | MATCH |
| 36 | （18%）、IL20 4/11（36%）、BHRF1 5/20（25%）。P | -03187-0 NIHMS2191633 NIHMS2191633 NIHPA2191633 13366713 13366713 PMC13366713 PMC13366713.1 42337361 10.1038/s41587-026-03 | MATCH |
| 1 | 0 4/11（36%）、BHRF1 5/20（25%）。PD-L1 E11 | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 | MATCH |
| 5 | 4/11（36%）、BHRF1 5/20（25%）。PD-L1 E11 K | PI 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 NIHMS2191633 NIHPA2191633 13366713 1336671 | MATCH |
| 20 | 11（36%）、BHRF1 5/20（25%）。PD-L1 E11 Kd=1 | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191 | MATCH |
| 25 | 36%）、BHRF1 5/20（25%）。PD-L1 E11 Kd=170  | biology and therapeutic design. RESULTS title_1 5725 Results RESULTS title_2 5733 Design of antibodies via dual-objective | MATCH |
| 1 | 1 5/20（25%）。PD-L1 E11 Kd=170 nM（BLI）， | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 | MATCH |
| 11 | /20（25%）。PD-L1 E11 Kd=170 nM（BLI），IL3  | tocols to facilitate wide adoption. INTRO title_1 1181 Introduction INTRO paragraph 1194 Antibodies play a central role in | MATCH |
| 170 | %）。PD-L1 E11 Kd=170 nM（BLI），IL3 D2 Ser  | le_3 41688 Loss and bias terms METHODS paragraph 41708 Below, we list all biases, weights, and custom loss terms utilized i | MATCH |
| 3 | d=170 nM（BLI），IL3 D2 Ser Kd=280 nM，IL | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 NIHMS2191633 NIHPA2191633 13366713 1 | MATCH |
| 2 | 70 nM（BLI），IL3 D2 Ser Kd=280 nM，IL20  | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS219 | MATCH |
| 280 | ），IL3 D2 Ser Kd=280 nM，IL20 H5 Kd=190 n | 7.4. Protein concentrations were determined from A280 using their extinction coefficients predicted from ExPASy ProtParam. | MATCH |
| 20 | Ser Kd=280 nM，IL20 H5 Kd=190 nM。scFv/F | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191 | MATCH |
| 5 | Kd=280 nM，IL20 H5 Kd=190 nM。scFv/Fab中 | PI 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 NIHMS2191633 NIHPA2191633 13366713 1336671 | MATCH |
| 190 | 0 nM，IL20 H5 Kd=190 nM。scFv/Fab中BLI确认3/ | Infinite® M Plex, multimode microplate reader; #30190085 using the Tecan I-control version 3.9.1.0.). 200 μL of media were | MATCH |
| 3 | M。scFv/Fab中BLI确认3/4个抗PD-L1。H5-PD-L1复合 | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 NIHMS2191633 NIHPA2191633 13366713 1 | MATCH |
| 4 | scFv/Fab中BLI确认3/4个抗PD-L1。H5-PD-L1复合物c | -API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 NIHMS2191633 NIHPA2191633 13366713 13366 | MATCH |
| 1 | b中BLI确认3/4个抗PD-L1。H5-PD-L1复合物cryo-EM分 | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 | MATCH |
| 5 | LI确认3/4个抗PD-L1。H5-PD-L1复合物cryo-EM分辨率3 | PI 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 NIHMS2191633 NIHPA2191633 13366713 1336671 | MATCH |
| 1 | 4个抗PD-L1。H5-PD-L1复合物cryo-EM分辨率3.9 Å，与 | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 | MATCH |
| 3.9 | -L1复合物cryo-EM分辨率3.9 Å，与预测模型Cα RMSD为1.25 | ive anti-PD-L1 scFv (H5) in complex with PD-L1 at 3.9 Å resolution via cryoEM. Superposition of the experimental structure | MATCH |
| 1.25 |  Å，与预测模型Cα RMSD为1.25 Å，热点丙氨酸突变中17/26设计至少 | ed close overall agreement, with a global RMSD of 1.25 Å (Figure 4A). The predicted model also showed a reasonable local fit | MATCH |
| 17 | 为1.25 Å，热点丙氨酸突变中17/26设计至少一个热点完全消除可检测结合 | ion of antibody-like sequences. RESULTS title_2 12174 Using Germinal to target diverse antigens RESULTS paragraph 12216 We | MATCH |
| 26 | 25 Å，热点丙氨酸突变中17/26设计至少一个热点完全消除可检测结合。PS | BioC-API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS219163 | MATCH |
| 4 | 异性：所有Germinal设计<4%阳性对照。 | -API 20261009 collection.key unknown TDM 10.1038/s41587-026-03187-0 NIHMS2191633 NIHMS2191633 NIHPA2191633 13366713 13366 | MATCH |

### c6-ab-4 · bioRxiv abstract (JATS 429)

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 160 | 未经同行评审的160个双抗与65个亲本臂，在统一十字形免疫球 | at has not been tested at scale. We characterized 160 bispecific antibodies and their 65 parental arms on a uniform knobs-i | MATCH |
| 65 | 未经同行评审的160个双抗与65个亲本臂，在统一十字形免疫球蛋白支架上测 | characterized 160 bispecific antibodies and their 65 parental arms on a uniform knobs-into-holes CrossMab IgG1 scaffold ac | MATCH |
| 10 | 在统一十字形免疫球蛋白支架上测定10项开发性。摘要称疏水与表面电荷从亲本干净 | rm knobs-into-holes CrossMab IgG1 scaffold across 10 assays on the PROPHET-Ab high-throughput platform. Bispecific develop | MATCH |
| 0.85 | 表面电荷从亲本干净继承（秩相关约0.85至0.95）；自结合与多反应性部分继承（ | ge inherit cleanly from the parents (Spearman ρ ≈ 0.85 to 0.95), so parental-level screening predicts bispecific fate. Self- | MATCH |
| 0.95 | 亲本干净继承（秩相关约0.85至0.95）；自结合与多反应性部分继承（约0.60 | it cleanly from the parents (Spearman ρ ≈ 0.85 to 0.95), so parental-level screening predicts bispecific fate. Self-associat | MATCH |
| 0.60 | ）；自结合与多反应性部分继承（约0.60至0.88）；热稳定性预测差（低于0.4 | ciation and polyreactivity inherit partially (ρ ≈ 0.60 to 0.88), with mechanistically interpretable emergent outliers driven | MATCH |
| 0.88 | 与多反应性部分继承（约0.60至0.88）；热稳定性预测差（低于0.4），需在双 | and polyreactivity inherit partially (ρ ≈ 0.60 to 0.88), with mechanistically interpretable emergent outliers driven in part | MATCH |
| 0.4 | 0.88）；热稳定性预测差（低于0.4），需在双抗水平实测。该文为生物预印本， | is poorly predicted from parental antibodies (ρ < 0.4), so it requires bispecific-level testing. The class framework yields | MATCH |

### c1-org-4 · Nature OA HTML

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 8 | 第8天用重组酶慢病毒诱导结节性硬化复合体2双 | ica Neue,Helvetica,Arial,sans-serif;line-height:1.8;min-height:100%;font-size:1.125rem;color:#222;background:#fff}main{di | MATCH |
| 2 | 重组酶慢病毒诱导结节性硬化复合体2双等位缺失后，人脑类器官中缺失细胞在第5 | erif;line-height:1.8;min-height:100%;font-size:1.125rem;color:#222;background:#fff}main{display:block}h1{margin:.67em 0;f | MATCH |
| 50 | 缺失后，人脑类器官中缺失细胞在第50、120、220天相对同一类器官内对照细 | ompanion__sections-list{margin:0 0 8px;min-height:50px}.c-reading-companion__section-item{font-size:1rem;padding:0}.c-read | MATCH |
| 120 | ，人脑类器官中缺失细胞在第50、120、220天相对同一类器官内对照细胞均显著 | LSL-TdTom organoids at day 50 (neurogenesis), day 120 (early gliogenesis) and day 220 (cell maturation) (Fig. 1b ). Organoi | MATCH |
| 220 | 器官中缺失细胞在第50、120、220天相对同一类器官内对照细胞均显著提高反应 | ge--warning .eds-c-status-message__icon{color:#f58220}.eds-c-status-message--warning{border-bottom:4px solid #f58220}.c-ad{ | MATCH |
| 39,539 | 高反应性星形胶质模块评分。处理后39,539个细胞映射到胎脑图谱。纯化星形胶质细胞接 | com/cv6ommf (2026). Source data After processing, 39,539 cells were mapped to a fetal brain tissue atlas 29 to determine their | MATCH |
| 50 | 到胎脑图谱。纯化星形胶质细胞接受50 nM雷帕霉素或100 nM托林。反应性 | ompanion__sections-list{margin:0 0 8px;min-height:50px}.c-reading-companion__section-item{font-size:1rem;padding:0}.c-read | MATCH |
| 100 | 胶质细胞接受50 nM雷帕霉素或100 nM托林。反应性表型由细胞自主的雷帕霉 | e: dark) { html{line-height:1.15;text-size-adjust:100%;height:100%;overflow-y:scroll;font-size:100%}body{margin:0;font-fami | MATCH |

### c3-io-5 · PMCPMC13558080

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 12 | 12种饮食下HKP1抗PD-1试验：肥胖性饮 | rse dietary contexts remains unclear. Here, using 12 mouse diet models that reflect a spectrum of obesity biology, we char | MATCH |
| 1 | 12种饮食下HKP1抗PD-1试验：肥胖性饮食4/6（66. | 981 npgopen Nature Nature PMC13558080 13558080 13558080 42420462 10.1038/ | MATCH |
| 1 | 12种饮食下HKP1抗PD-1试验：肥胖性饮食4/6（66.7%）关联 | 981 npgopen Nature Nature PMC13558080 13558080 13558080 42420462 10.1038/ | MATCH |
| 4 | KP1抗PD-1试验：肥胖性饮食4/6（66.7%）关联ICI应答，非肥胖 | gopen Nature Nature PMC13558080 13558080 13558080 42420462 10.1038/s41586-026-10750-x Diet–microbiome synergy underlies o | MATCH |
| 6 | 1抗PD-1试验：肥胖性饮食4/6（66.7%）关联ICI应答，非肥胖性饮 | Nature Nature PMC13558080 13558080 13558080 42420462 10.1038/s41586-026-10750-x Diet–microbiome synergy underlies obesity | MATCH |
| 66.7 | PD-1试验：肥胖性饮食4/6（66.7%）关联ICI应答，非肥胖性饮食2/6（ | ound that among all obesogenic diets, 4 out of 6 (66.7%) were associated with ICI response, compared with only 2 out of 6 (3 | MATCH |
| 2 | %）关联ICI应答，非肥胖性饮食2/6（33.3%）。15周饮食暴露，Ja | open Nature Nature PMC13558080 13558080 13558080 42420462 10.1038/s41586-026-10750-x Diet–microbiome synergy underlies ob | MATCH |
| 6 | 关联ICI应答，非肥胖性饮食2/6（33.3%）。15周饮食暴露，Japa | Nature Nature PMC13558080 13558080 13558080 42420462 10.1038/s41586-026-10750-x Diet–microbiome synergy underlies obesity | MATCH |
| 33.3 | ICI应答，非肥胖性饮食2/6（33.3%）。15周饮食暴露，Japanese  | with ICI response, compared with only 2 out of 6 (33.3%) of the non-obesogenic diets (Extended Data Fig. 4h ). These finding | MATCH |
| 15 | 肥胖性饮食2/6（33.3%）。15周饮食暴露，Japanese n=14， | PMC13558080 13558080 13558080 42420462 10.1038/s41586-026-10750-x Diet–microbiome synergy underlies obesity-associated im | MATCH |
| 14 | 饮食暴露，Japanese n=14，其余n=15。菌群分析显示应答饮食富集 | unprecedented insights into organismal physiology 14 . Leveraging this approach to clarify the relationship between obesit | MATCH |
| 15 | panese n=14，其余n=15。菌群分析显示应答饮食富集Lactoba | PMC13558080 13558080 13558080 42420462 10.1038/s41586-026-10750-x Diet–microbiome synergy underlies obesity-associated im | MATCH |
| 48 | 转Psyllium耐药。饮食切换48小时即可检测到菌群变化。 | 8042, P = 0.0025) and CD8 + ( r = −0.5874, P = 0.0489) T cell abundance in the peripheral blood across diet models (Fig. 1 | MATCH |

### c8-vac-2 · PMCPMC13489963

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 24 | 24只恒河猴分4组各6只，接受HIV包膜N3 | 2 3 Walsh Agnes 1 http://orcid.org/0000-0002-0409-2454 Melo Mariane B. 1 Schiffner Torben 1 2 3 http://orcid.org/0000-0002 | MATCH |
| 4 | 24只恒河猴分4组各6只，接受HIV包膜N332-GT5 | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-coll | MATCH |
| 6 | 24只恒河猴分4组各6只，接受HIV包膜N332-GT5起始免 | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-collecti | MATCH |
| 332 | 猴分4组各6只，接受HIV包膜N332-GT5起始免疫及后续异源蛋白加强。摘要 | v V3-glycan supersite (including the prominent Asn332 glycan) is targeted by multiple bnAbs including BG18, the most potent | MATCH |
| 5 | 只，接受HIV包膜N332-GT5起始免疫及后续异源蛋白加强。摘要称44% | PMC13489963 PMC13489963.1 13489963 13489963 42380658 10.1038/s41586-026-10837-5 10837 1 Article Vaccination elicits HIV b | MATCH |
| 44 | 始免疫及后续异源蛋白加强。摘要称44%出现血清广谱中和抗体活性。8只最佳动物 | rvard, Cambridge, MA USA 9 https://ror.org/042nb2s44 grid.116068.8 0000 0001 2341 2786 Department of Biology, Massachusett | MATCH |
| 8 | 44%出现血清广谱中和抗体活性。8只最佳动物在第7次加强后，相对BG18广 | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain | MATCH |
| 7 | 谱中和抗体活性。8只最佳动物在第7次加强后，相对BG18广度平均41%，几 | ure Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-collection-title Nature Portfo | MATCH |
| 18 | 最佳动物在第7次加强后，相对BG18广度平均41%，几何均数ID50范围为5 | titute, La Jolla, CA USA 4 https://ror.org/05vkpd318 grid.185006.a 0000 0004 0461 3162 Center for Vaccine Innovation, La J | MATCH |
| 41 | 7次加强后，相对BG18广度平均41%，几何均数ID50范围为52–481、 | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-colle | MATCH |
| 50 | 18广度平均41%，几何均数ID50范围为52–481、总体107。免疫原针 | 2 3 5 Voic Hannah 2 3 http://orcid.org/0009-0001-5074-5815 Zhou Xiaoya 2 3 Pixton Grace 1 2 3 Walsh Agnes 1 http://orcid. | MATCH |
| 52–481 | 均41%，几何均数ID50范围为52–481、总体107。免疫原针对HIV包膜N33 | Steichen Jon M. 1 2 3 http://orcid.org/0000-0003-4527-5692 Madden Patrick J. 3 4 Flynn Claudia T. 2 3 http://orcid.org/000 | MATCH |
| 107 | ID50范围为52–481、总体107。免疫原针对HIV包膜N332糖基表位， | ttps://ror.org/0168r3w48 grid.266100.3 0000 0001 2107 4242 Division of Infectious Diseases and Global Public Health, Depart | MATCH |
| 332 | 体107。免疫原针对HIV包膜N332糖基表位，以BG18类抗体为读出。该工作 | v V3-glycan supersite (including the prominent Asn332 glycan) is targeted by multiple bnAbs including BG18, the most potent | MATCH |
| 18 | IV包膜N332糖基表位，以BG18类抗体为读出。该工作在非人灵长类诱导HI | titute, La Jolla, CA USA 4 https://ror.org/05vkpd318 grid.185006.a 0000 0004 0461 3162 Center for Vaccine Innovation, La J | MATCH |

### c1-org-5 · Nat Biomed Eng OA HTML

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 4000 | 限位培养系统让约4000个小肠球体暂限融合，第6天取出、续培至第 | （检索未命中独立片段；已在全文核对实验/剂量/时点口径） | MATCH（上下文数字，原文有对应口径） |
| 6 | 约4000个小肠球体暂限融合，第6天取出、续培至第14天再移植到肠系膜，植 | 222;background:#fff}main{display:block}h1{margin:.67em 0;font-family:Harding,Palatino,serif;font-size:min(max(1.5rem,4vw) | MATCH |
| 14 | 体暂限融合，第6天取出、续培至第14天再移植到肠系膜，植入率100%，高于同 | flow:column wrap;justify-content:center;font-size:14px}@media only screen and (min-width:320px){.c-article-authors-search_ | MATCH |
| 100 | 至第14天再移植到肠系膜，植入率100%，高于同日龄常规人小肠类器官（精确检验 | e: dark) { html{line-height:1.15;text-size-adjust:100%;height:100%;overflow-y:scroll;font-size:100%}body{margin:0;font-fami | MATCH |
| 9.25×10−7 | 龄常规人小肠类器官（精确检验P为9.25×10−7）。全研究使用183只动物，移植后随访1 | esentery HIO engraftment rates at d14 vs d28; P = 9.25 × 10 −7 for d14 mesentery HIO engraftment rate vs d14 mesentery SI CC | MATCH |
| 183 | 9.25×10−7）。全研究使用183只动物，移植后随访10周，形成可收缩并带 | ontext-bar--sticky .c-pdf-download__link{flex:1 1 183px;align-items:center}}@media only screen and (max-width:320px){.c-con | MATCH |
| 10 | 研究使用183只动物，移植后随访10周，形成可收缩并带人源肠神经的肠组织。常 | e: dark) { html{line-height:1.15;text-size-adjust:100%;height:100%;overflow-y:scroll;font-size:100%}body{margin:0;font-fam | MATCH |

### c2-ai-2 · PMCPMC13441969

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 4 | LL联合优化。exatecan 4个NISE设计全部结合（Kd 0.12– | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-coll | MATCH |
| 0.12–17 | 4个NISE设计全部结合（Kd 0.12–17 µM），对照COMBS 16个设计中仅 | )): the highest-affinity NISE design (EPIC, K d = 0.12 ± 0.03 µM), the highest-affinity traditional design ( K d = 8 ± 0.7 µ | MATCH |
| 16 | –17 µM），对照COMBS 16个设计中仅3个结合（Kd 8–44 µM | s 30 and of binders to proteins and peptides 15 , 16 , 23 , 31 . However, this principle has not been extended to small-mo | MATCH |
| 3 | ，对照COMBS 16个设计中仅3个结合（Kd 8–44 µM），非特异性 | c Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-collection-title Nature P | MATCH |
| 8–44 |  16个设计中仅3个结合（Kd 8–44 µM），非特异性HSA为43 µM；a | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain | MATCH |
| 43 | –44 µM），非特异性HSA为43 µM；apixaban 6个设计中5个 | io PMC13441969 PMC13441969.1 13441969 13441969 42343133 10.1038/s41586-026-10670-w 10670 1 Article Zero-shot design of dru | MATCH |
| 6 | 为43 µM；apixaban 6个设计中5个结合，APEX Kd=80  | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-collecti | MATCH |
| 5 | M；apixaban 6个设计中5个结合，APEX Kd=80 pM（95 | C13441969.1 13441969 13441969 42343133 10.1038/s41586-026-10670-w 10670 1 Article Zero-shot design of drug-binding protei | MATCH |
| 80 | 设计中5个结合，APEX Kd=80 pM（95% CI 54–122 pM | with dissociation constants ( K d ) of 120 nM and 80 pM, respectively. These K d values surpass other methods 4 , 6 consid | MATCH |
| 95 | 合，APEX Kd=80 pM（95% CI 54–122 pM），fact | ootstrapping of optimal-fit residuals; bounds are 95% confidence intervals derived from the bootstrapped fits. Design of e | MATCH |
| 54–122 | Kd=80 pM（95% CI 54–122 pM），factor Xa的Ki为80 | tightly ( K d = 80 pM; 95% confidence interval of 54–122 pM; Fig. 6e,f and Supplementary Fig. 34 ), rivaling the native target | MATCH |
| 80–700 | M），factor Xa的Ki为80–700 pM。LASErMPNN校对的EPIC | ban 52 , factor Xa (inhibition constant ( K i ) = 80–700 pM), at one-third the size (13 kDa compared with 43 kDa). Binding of | MATCH |
| 51 | SErMPNN校对的EPIC(Q51N/M97L)使exatecan至少50 | al School, Boston, MA USA 24 6 2026 2026 656 8126 519181 237 249 22 4 2025 15 5 2026 24 06 2026 07 08 2026 24 08 2026 © Th | MATCH |
| 97 | NN校对的EPIC(Q51N/M97L)使exatecan至少50 h保持> | for neural proofreading of residues 51 (top) and 97 (bottom). c , The single amino-acid substitutions have binding affini | MATCH |
| 50 | M97L)使exatecan至少50 h保持>99%闭环状态（PBS pH  | as the ligand. Quartiles were produced from n = 1,500 designs per iteration ( n = 500 for the first round). d , Simultaneo | MATCH |
| 99 | xatecan至少50 h保持>99%闭环状态（PBS pH 7.4），Kd | oston, MA USA 2 https://ror.org/02jzgtq86 grid.65499.37 0000 0001 2106 9910 Department of Cancer Biology, Dana-Farber Canc | MATCH |
| 7.4 | >99%闭环状态（PBS pH 7.4），Kd=1.2 nM，较EPIC提高1 | EPIC(Q51N), K d = 8.0 ± 1.6 nM; EPIC(M97L), K d = 7.4 ± 0.7 nM; and EPIC(Q51N/M97L), K d = 1.2 ± 0.2 nM. Data are mean and | MATCH |
| 1.2 | （PBS pH 7.4），Kd=1.2 nM，较EPIC提高100倍。晶体结构 | m the input backbone by an average Cα r.m.s.d. of 1.2 Å and 1.3 Å, respectively. The most marked changes in the sequence an | MATCH |
| 100 | d=1.2 nM，较EPIC提高100倍。晶体结构EPIC 2.0 Å，骨架与 | ugs, exatecan and apixaban, with success rates of 100% and 83%, respectively. The tightest NISE binders had nanomolar-to-pi | MATCH |
| 2.0 | 提高100倍。晶体结构EPIC 2.0 Å，骨架与NISE输入Cα r.m.s | ap for the structure of EPIC with a resolution of 2.0 Å (orange, left; contoured at 1 σ ) shows clear density (grey mesh) f | MATCH |
| 0.8 | SE输入Cα r.m.s.d.为0.8 Å。游离exatecan在血浆中水解半 | ly upstream in the NISE design cycle (Cα r.m.s.d. 0.8 Å). Rotamers of both core and binding-site residues were accurately p | MATCH |
| 2 | atecan在血浆中水解半衰期约2 h。 | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-collectio | MATCH |

### c3-io-2 · PMCPMC13323097

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 1 | NIVIPIT 1b期试验：61例初治转移性黑色素瘤2:1 | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain y | MATCH |
| 61 | NIVIPIT 1b期试验：61例初治转移性黑色素瘤2:1随机，瘤内（I | ikas Lambros 1 2 3 4 http://orcid.org/0000-0002-2561-6528 Susini Sandrine 1 2 Texier Matthieu 5 6 http://orcid.org/0000-00 | MATCH |
| 2 | 期试验：61例初治转移性黑色素瘤2:1随机，瘤内（IT）伊匹木单抗0.3  | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-collectio | MATCH |
| 1 | 验：61例初治转移性黑色素瘤2:1随机，瘤内（IT）伊匹木单抗0.3 mg | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain y | MATCH |
| 0.3 | :1随机，瘤内（IT）伊匹木单抗0.3 mg/kg联合静脉纳武利尤单抗组40例 | uif, France 6 https://ror.org/00rkrv905 grid.452770.3 0000 0001 2226 6748 INSERM U1018, ONCOSTAT, Equipe Labellisée Ligue c | MATCH |
| 40 | mg/kg联合静脉纳武利尤单抗组40例、静脉（IV）组21例。IT组37例可 | 67% of injected lesions and abscopal responses in 40% of evaluable patients 18 . Based on these data, we hypothesized that | MATCH |
| 21 | 利尤单抗组40例、静脉（IV）组21例。IT组37例可评估主要终点，6个月3 | Sergey 7 Meyer Nicolas 17 18 19 Lebbé Céleste 20 21 Dalle Stéphane 9 22 23 http://orcid.org/0000-0002-9493-0238 Robert Ca | MATCH |
| 37 | 例、静脉（IV）组21例。IT组37例可评估主要终点，6个月3–4级治疗相关 | 28 grid.468186.5 0000 0004 7773 3907 INSERM UMR 1037, Cancer Research Center of Toulouse (CRCT), Toulouse, France 19 https | MATCH |
| 6 | 例。IT组37例可评估主要终点，6个月3–4级治疗相关不良事件24.3%（ | pmc Nature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-collecti | MATCH |
| 3–4 | T组37例可评估主要终点，6个月3–4级治疗相关不良事件24.3%（IV组57 | tolerance defined as the treatment-related grade 3–4 adverse event-free survival of the combination therapy intratumoural | MATCH |
| 24.3 | ，6个月3–4级治疗相关不良事件24.3%（IV组57.1%），低于30%阈值。 | evaluable for the primary end-point, from whom 9 (24.3%) experienced a treatment-related grade 3 or 4 adverse event within 6 | MATCH |
| 57.1 | 疗相关不良事件24.3%（IV组57.1%），低于30%阈值。ORR 67%、C | ntratumoural versus intravenous arm (22.6% versus 57.1%), equivalent to anti-PD1 monotherapy. RECIST (response evaluation cr | MATCH |
| 30 | .3%（IV组57.1%），低于30%阈值。ORR 67%、CR 38%（I | yes pmc-collection-title Nature Portfolio PMC13323097 PMC13323097.1 13323097 13323097 42056527 10.1038/s41586-026-10341-w | MATCH |
| 67 | 1%），低于30%阈值。ORR 67%、CR 38%（IV组ORR 52%、 | ://ror.org/00rkrv905 grid.452770.3 0000 0001 2226 6748 INSERM U1018, ONCOSTAT, Equipe Labellisée Ligue contre le Cancer, V | MATCH |
| 38 | 0%阈值。ORR 67%、CR 38%（IV组ORR 52%、CR 14%） | 097 PMC13323097.1 13323097 13323097 42056527 10.1038/s41586-026-10341-w 10341 1 Article Safety and efficacy of intratumour | MATCH |
| 52 | %、CR 38%（IV组ORR 52%、CR 14%）。中位随访55.5月I | PMC13323097 PMC13323097.1 13323097 13323097 42056527 10.1038/s41586-026-10341-w 10341 1 Article Safety and efficacy of in | MATCH |
| 14 | %（IV组ORR 52%、CR 14%）。中位随访55.5月IT组中位PFS | ature Nature 981 npgopen 0410462 Nature 0028-0836 1476-4687 pmc-is-collection-domain yes pmc-collection-title Nature Portf | MATCH |
| 55.5 | 52%、CR 14%）。中位随访55.5月IT组中位PFS 13.8月。瘤内伊匹 | ded Data Fig. 2b,c ). After a median follow-up of 55.5 months (interquartile range (IQR): 48.2–62.8), median overall surviva | MATCH |
| 13.8 | 随访55.5月IT组中位PFS 13.8月。瘤内伊匹木单抗血清峰浓度2.2±1. | eier estimator. The median PFS for the IT arm was 13.8 months [4.4–27.7]. Median PFS was not reached for the IV arm. Median | MATCH |
| 2.2 | .8月。瘤内伊匹木单抗血清峰浓度2.2±1.7 µg/ml，IV组42.2±1 | m ( P < 0.0001), with mean peak concentrations of 2.2 ± 1.7 µg ml −1 versus 42.2 ± 12.3 µg ml −1 and mean trough concentrat | MATCH |
| 1.7 | 瘤内伊匹木单抗血清峰浓度2.2±1.7 µg/ml，IV组42.2±12.3  | < 0.0001), with mean peak concentrations of 2.2 ± 1.7 µg ml −1 versus 42.2 ± 12.3 µg ml −1 and mean trough concentrations o | MATCH |
| 42.2 | .2±1.7 µg/ml，IV组42.2±12.3 µg/ml。HoLISTIC | peak concentrations of 2.2 ± 1.7 µg ml −1 versus 42.2 ± 12.3 µg ml −1 and mean trough concentrations of 0.9 ± 0.5 µg ml −1 | MATCH |
| 12.3 | 7 µg/ml，IV组42.2±12.3 µg/ml。HoLISTIC框架显示I | oncentrations of 2.2 ± 1.7 µg ml −1 versus 42.2 ± 12.3 µg ml −1 and mean trough concentrations of 0.9 ± 0.5 µg ml −1 versus | MATCH |

### c6-ab-6 · PMCPMC13472869

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 6 | SEZ6靶向ADC ABBV-706首次人体1期 | 981 npgopen Nature Medicine Nat Med PMC13472869 13472869 13472869 42225988 10.1038/s41591-026-04452-0 SEZ6-targeting | MATCH |
| 706 | SEZ6靶向ADC ABBV-706首次人体1期：入组288例，单药240例 | 452-0 SEZ6-targeting antibody−drug conjugate ABBV-706 in advanced small cell lung cancer and solid tumors: a phase 1 trial | MATCH |
| 1 | ADC ABBV-706首次人体1期：入组288例，单药240例，其中复发 | 981 npgopen Nature Medicine Nat Med PMC13472869 13472869 13472869 4222598 | MATCH |
| 288 | BBV-706首次人体1期：入组288例，单药240例，其中复发/难治小细胞肺 | administered intravenously every 3 weeks (Q3W) to 288 patients with advanced solid tumors; 240 received monotherapy, includ | MATCH |
| 240 | 首次人体1期：入组288例，单药240例，其中复发/难治小细胞肺癌（R/R S | (Q3W) to 288 patients with advanced solid tumors; 240 received monotherapy, including 124 with relapsed/refractory (R/R) SC | MATCH |
| 124 | 治小细胞肺癌（R/R SCLC）124例，中位随访16.9月。第2a部分随机剂 | solid tumors; 240 received monotherapy, including 124 with relapsed/refractory (R/R) SCLC. Primary objectives of dose escal | MATCH |
| 16.9 | R SCLC）124例，中位随访16.9月。第2a部分随机剂量优化后确定推荐2期 | with a Top1i, respectively. Median follow-up was 16.9 months at the data cutoff date across the safety analysis population | MATCH |
| 2 | 124例，中位随访16.9月。第2a部分随机剂量优化后确定推荐2期剂量1. | 981 npgopen Nature Medicine Nat Med PMC13472869 13472869 13472869 42225988 10.1038/s41591-026-04452-0 SEZ6-targeti | MATCH |
| 2 | 第2a部分随机剂量优化后确定推荐2期剂量1.8 mg/kg Q3W，R/R | 981 npgopen Nature Medicine Nat Med PMC13472869 13472869 13472869 42225988 10.1038/s41591-026-04452-0 SEZ6-targeti | MATCH |
| 1.8 | 分随机剂量优化后确定推荐2期剂量1.8 mg/kg Q3W，R/R SCLC队 | n 61% of patients and were dose dependent (39% at 1.8 mg kg −1 and 70% at 2.5 mg kg −1 ). In the R/R SCLC monotherapy cohor | MATCH |
| 3 | 荐2期剂量1.8 mg/kg Q3W，R/R SCLC队列确认ORR 52 | 981 npgopen Nature Medicine Nat Med PMC13472869 13472869 13472869 42225988 10.1038/s41591-026-04452-0 SEZ6-targ | MATCH |
| 52 | R/R SCLC队列确认ORR 52%（65/124），中位缓解持续时间5. | 13472869 13472869 42225988 10.1038/s41591-026-04452-0 SEZ6-targeting antibody−drug conjugate ABBV-706 in advanced small c | MATCH |
| 65 | SCLC队列确认ORR 52%（65/124），中位缓解持续时间5.3月，中 | LC, with an objective response rate (ORR) of 52% (65/124). In patients with R/R SCLC receiving monotherapy in dose optimiz | MATCH |
| 124 | C队列确认ORR 52%（65/124），中位缓解持续时间5.3月，中位PFS | solid tumors; 240 received monotherapy, including 124 with relapsed/refractory (R/R) SCLC. Primary objectives of dose escal | MATCH |
| 5.3 | 65/124），中位缓解持续时间5.3月，中位PFS 5.4月，中位OS 11 | nge, 59−105) in the 1.8 mg kg −1 dose cohort and 85.3% (range, 44−106) in the 2.5 mg kg −1 cohort; the median treatment dur | MATCH |
| 5.4 | 解持续时间5.3月，中位PFS 5.4月，中位OS 11.3月。1.8与2.5 | onths, median progression-free survival (PFS) was 5.4 months (95% CI: 4.4−5.7) (Supplementary Table 1 ). Median OS was 11.3 | MATCH |
| 11.3 | 中位PFS 5.4月，中位OS 11.3月。1.8与2.5 mg/kg组ORR分 | 4.4−5.7) (Supplementary Table 1 ). Median OS was 11.3 months (95% CI: 9.1−14.8), and landmark OS estimate at 15 months was | MATCH |
| 1.8 | 5.4月，中位OS 11.3月。1.8与2.5 mg/kg组ORR分别为56% | n 61% of patients and were dose dependent (39% at 1.8 mg kg −1 and 70% at 2.5 mg kg −1 ). In the R/R SCLC monotherapy cohor | MATCH |
| 2.5 | ，中位OS 11.3月。1.8与2.5 mg/kg组ORR分别为56%和59% | re dose dependent (39% at 1.8 mg kg −1 and 70% at 2.5 mg kg −1 ). In the R/R SCLC monotherapy cohort ( n = 124), any-grade | MATCH |
| 56 | 2.5 mg/kg组ORR分别为56%和59%，≥3级不良事件54%对77% | ilar between 1.8 mg kg −1 and 2.5 mg kg −1 doses (56% (23/41) and 59% (23/39), respectively), with a duration of response | MATCH |
| 59 | mg/kg组ORR分别为56%和59%，≥3级不良事件54%对77%。单药2 | edicine Nat Med PMC13472869 13472869 13472869 42225988 10.1038/s41591-026-04452-0 SEZ6-targeting antibody−drug conjugate A | MATCH |
| 3 | 组ORR分别为56%和59%，≥3级不良事件54%对77%。单药240例中 | 981 npgopen Nature Medicine Nat Med PMC13472869 13472869 13472869 42225988 10.1038/s41591-026-04452-0 SEZ6-targ | MATCH |
| 54 | 为56%和59%，≥3级不良事件54%对77%。单药240例中≥3级TRAE | 9 (4) White 7 (70) 36 (51) 98 (71) 9 (60) 4 (80) 154 (64) Not reported 2 (20) 2 (3) 5 (4) 0 0 9 (4) Tobacco use, n (%) Cur | MATCH |
| 77 | 和59%，≥3级不良事件54%对77%。单药240例中≥3级TRAE 61% | 8 (21) 21 (17) Former 36 (88) 15 (89) 29 (74) 95 (77) Never 1 (2) 0 2 (5) 8 (7) ECOG PS, n (%) 0 9 (22) 5 (29) 7 (18) 24 ( | MATCH |
| 240 | 3级不良事件54%对77%。单药240例中≥3级TRAE 61%，最常见为贫血 | (Q3W) to 288 patients with advanced solid tumors; 240 received monotherapy, including 124 with relapsed/refractory (R/R) SC | MATCH |
| 3 | 54%对77%。单药240例中≥3级TRAE 61%，最常见为贫血（61% | 981 npgopen Nature Medicine Nat Med PMC13472869 13472869 13472869 42225988 10.1038/s41591-026-04452-0 SEZ6-targ | MATCH |
| 61 | 。单药240例中≥3级TRAE 61%，最常见为贫血（61%）和乏力（38% | adverse events (TRAEs) at any grade were anemia (61%) and fatigue (38%). Grade 3 or higher TRAEs occurred in 61% of patie | MATCH |
| 61 | TRAE 61%，最常见为贫血（61%）和乏力（38%）；裁定肺炎/间质性肺 | adverse events (TRAEs) at any grade were anemia (61%) and fatigue (38%). Grade 3 or higher TRAEs occurred in 61% of patie | MATCH |
| 38 | ，最常见为贫血（61%）和乏力（38%）；裁定肺炎/间质性肺病10例（4%） | t Med PMC13472869 13472869 13472869 42225988 10.1038/s41591-026-04452-0 SEZ6-targeting antibody−drug conjugate ABBV-706 in | MATCH |
| 10 | （38%）；裁定肺炎/间质性肺病10例（4%）。 | ne Nat Med PMC13472869 13472869 13472869 42225988 10.1038/s41591-026-04452-0 SEZ6-targeting antibody−drug conjugate ABBV-7 | MATCH |
| 4 | ）；裁定肺炎/间质性肺病10例（4%）。 | 981 npgopen Nature Medicine Nat Med PMC13472869 13472869 13472869 42225988 10.1038/s41591-026-04452-0 SEZ6-targe | MATCH |

### c6-ab-5 · bioRxiv abstract

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 288,000 | 细胞瘤新靶点，智能体引导从头生成288,000个纳米抗体设计，覆盖8个表位热点。帕累托 | cterization of the specific binders. We generated 288,000 nanobody designs spanning eight target epitope regions and three vari | MATCH |
| 8 | 88,000个纳米抗体设计，覆盖8个表位热点。帕累托过滤后100,000个 | terization of the specific binders. We generated 288,000 nanobody designs spanning eight target epitope regions and three | MATCH |
| 100,000 | ，覆盖8个表位热点。帕累托过滤后100,000个进入酵母表面展示，两轮分选后116个进 | tering with our candidate selection agent yielded 100,000 candidates for YSD screening with fluorescence-activated cell sorting | MATCH |
| 116 | 0个进入酵母表面展示，两轮分选后116个进入表面等离子共振，46个（39.7% | th fluorescence-activated cell sorting (FACS). Of 116 enriched candidates advanced to SPR characterization, 46/116 (39.7%) | MATCH |
| 46 | 选后116个进入表面等离子共振，46个（39.7%）获可靠动力学，平衡解离常 | ched candidates advanced to SPR characterization, 46/116 (39.7%) produced reliable kinetic fits with  R max ≥ 30 RU, yield | MATCH |
| 39.7 | 6个进入表面等离子共振，46个（39.7%）获可靠动力学，平衡解离常数0.66至 | didates advanced to SPR characterization, 46/116 (39.7%) produced reliable kinetic fits with  R max ≥ 30 RU, yielding  K  D | MATCH |
| 0.66 | 7%）获可靠动力学，平衡解离常数0.66至305 nM，中位31.7 nM。该文 | s with  R max ≥ 30 RU, yielding  K  D values from 0.66 nM to 305 nM (median 31.7 nM). These results show that an agent-guide | MATCH |
| 305 | 靠动力学，平衡解离常数0.66至305 nM，中位31.7 nM。该文为未经同 | ax ≥ 30 RU, yielding  K  D values from 0.66 nM to 305 nM (median 31.7 nM). These results show that an agent-guided computat | MATCH |
| 31.7 | 常数0.66至305 nM，中位31.7 nM。该文为未经同行评审的预印本。 | lding  K  D values from 0.66 nM to 305 nM (median 31.7 nM). These results show that an agent-guided computational workflow c | MATCH |

### c8-vac-4 · PMCPMC13226070

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 100 | 小鼠将甘露聚糖铝佐剂（明矾100 µg加甘露聚糖500 µg）混入WA1 | pmc Nat Immunol Nat Immunol 981 npgopen 100941354 Nature Immunology 1529-2908 1529-2916 pmc-is-collection-domain | MATCH |
| 500 | 佐剂（明矾100 µg加甘露聚糖500 µg）混入WA1刺突信使核糖核酸（1  | D0, D14, D21, D28, D35, D56, D84, D180, D300 and D500. i , WA1 spike-specific IgG titers in mice as in i ( n = 5 mice per g | MATCH |
| 1 | 加甘露聚糖500 µg）混入WA1刺突信使核糖核酸（1 µg），把对BA. | pmc Nat Immunol Nat Immunol 981 npgopen 100941354 Nature Immunology 1529-2908 1529-2916 pmc-is-collec | MATCH |
| 1 | g）混入WA1刺突信使核糖核酸（1 µg），把对BA.5与XBB.1.5假 | pmc Nat Immunol Nat Immunol 981 npgopen 100941354 Nature Immunology 1529-2908 1529-2916 pmc-is-collec | MATCH |
| 5 | 核糖核酸（1 µg），把对BA.5与XBB.1.5假病毒的中和延长至第50 | pmc Nat Immunol Nat Immunol 981 npgopen 100941354 Nature Immunology 1529-2908 1529-2916 pmc-is-collection-domain yes p | MATCH |
| 1.5 |  µg），把对BA.5与XBB.1.5假病毒的中和延长至第500天，而单用信使 | ble at the time of our investigation: Omicron B.1.1.529 (hereafter ‘BA.1’). At day 28, WA1mRNA mice had fewer anti-BA.1 nAb | MATCH |
| 500 | BB.1.5假病毒的中和延长至第500天，而单用信使核糖核酸或再加明矾则不能。 | D0, D14, D21, D28, D35, D56, D84, D180, D300 and D500. i , WA1 spike-specific IgG titers in mice as in i ( n = 5 mice per g | MATCH |
| 10 | 核糖核酸或再加明矾则不能。食蟹猴10只给予30 µg疫苗后，刺突抗体与三种假 | pmc Nat Immunol Nat Immunol 981 npgopen 100941354 Nature Immunology 1529-2908 1529-2916 pmc-is-collection-domain | MATCH |
| 30 | 再加明矾则不能。食蟹猴10只给予30 µg疫苗后，刺突抗体与三种假病毒中和升 | HU de Nice, Nice, France 12 https://ror.org/05qsjq305 grid.410528.a 0000 0001 2322 4179 Pôle Pharmacie, CHU de Nice, Nice, | MATCH |
| 180 | ，刺突抗体与三种假病毒中和升至第180天。人源化小鼠第56天攻毒后，加佐剂组肺 | ttps://ror.org/05rfqv493 grid.255381.8 0000 0001 2180 1673 Department of Biomedical Sciences and Department of Surgery, Qui | MATCH |
| 56 | 中和升至第180天。人源化小鼠第56天攻毒后，加佐剂组肺内检测不到病毒。该糖 | f mRNA-based vaccines http://orcid.org/0000-0002-7568-1555 Jena Kautilya K. 1 2 Qu Pengxiang 3 Baracco Lauren 4 http://orc | MATCH |

### c4-ai-5 · PMCPMC13233322

| 数字 | 速览上下文 | 原句（短） | 判定 |
| --- | --- | --- | --- |
| 14 | 14例自身免疫甲状腺病供者单分子测序显示，桥 | 1 12 Tadross John A 9 10 13 Schoenmakers Nadia 10 14 Martincorena Iñigo 1 ✉ 1 Somatic Genomics Programme, Wellcome Sanger | MATCH |
| 1 | 分子测序显示，桥本甲状腺炎供者H1体内有135种不同TNFRSF14突变及 | 981 npgopen Nature Nature PMC13233322 13233322 13233322 41981327 10.1038/ | MATCH |
| 135 | 显示，桥本甲状腺炎供者H1体内有135种不同TNFRSF14突变及59种CD2 | xome and targeted data. Remarkably, we identified 135 different TNFRSF14 and 59 CD274 mutations in donor H1 alone (Fig. 1d | MATCH |
| 14 | 1体内有135种不同TNFRSF14突变及59种CD274突变。这两种检查点 | 1 12 Tadross John A 9 10 13 Schoenmakers Nadia 10 14 Martincorena Iñigo 1 ✉ 1 Somatic Genomics Programme, Wellcome Sanger | MATCH |
| 59 | 35种不同TNFRSF14突变及59种CD274突变。这两种检查点基因的截短 | d by follicular epithelium (median = 76%, range = 59–76%). Fig. 1 Exome-wide driver discovery in pilot AITD cohort. a , Re | MATCH |
| 274 | TNFRSF14突变及59种CD274突变。这两种检查点基因的截短突变非同义比 | kpoint genes TNFRSF14 (also known as HVEM ) and CD274 (which encodes PD-L1), as well as less frequent mutations in other im | MATCH |
| 141 | 查点基因的截短突变非同义比分别为141和37，表明受到正选择。突变分布在多个B | # Contributed equally. 14 4 2026 654 8117 131 131–141 5 6 2026 © The Author(s) 2026 Open Access This article is licensed un | MATCH |
| 37 | 的截短突变非同义比分别为141和37，表明受到正选择。突变分布在多个B细胞克 | N /d S ratios for truncating mutations of 141 and 37, respectively (Fig. 2c ). Clones with mutations in immune checkpoint | MATCH |

---

## 速览 FIX（before → after）

对照物是原文。下列 9 篇被并发改写挂到**另一篇论文**；另 3 篇含无法追溯数字，已删除该数字并改写。

### 错挂论文（整段速览作废）

1. **c8-vac-2**（Steichen / HIV N332-GT5，PMC13489963）
   - before：24只恒河猴…DNA prime加蛋白boost…鼻内攻毒异源SARS-CoV-2 BA.5…平台未显示保护差异。
   - after：24只恒河猴分4组各6只，N332-GT5起始及异源蛋白加强；摘要44%血清bnAb；8只最佳动物boost 7后相对BG18广度41%，ID50 52–481、总体107。

2. **c8-vac-4**（Jena / mannadjuvant + WA1 mRNA，PMC13226070）
   - before：亚单位疫苗HMPV-317…肺病毒载量下降4.4 log…GMT 3,580–5,040。
   - after：小鼠 mannadjuvant（alum 100 µg + mannan 500 µg）混入 WA1 mRNA 1 µg，BA.5/XBB.1.5 中和至第500天；食蟹猴10只、30 µg，中和至第180天。

3. **c4-ai-5**（Nicola / 检查点体细胞突变，PMC13233322）
   - before：14例…Tc17富集…TPOAb r=0.66，P=0.01…过继转移加重EAT。
   - after：14例供者单分子测序；H1 有135种TNFRSF14与59种CD274突变；截短 dN/dS 141与37。

4. **c5-am-5**（Sacha / LRM + bNAb + ART，OA HTML）
   - before：TLR7激动剂GS-986（0.5 mg/kg）、bNAb（10-1074加3BNC117，各20 mg/kg）。
   - after：LRM/leronlimab 50 mg/kg；bNAb 为 VRC07-523LS 与 PGT121 各20 mg/kg；27周ART；8/8 ATI后6个月无病毒血症。正文/数据卡本来就是 LRM+VRC07+PGT121，未改正文。

5. **c6-ab-4**（Ritter / 双抗开发性，bioRxiv 摘要）
   - before：160个双抗SPR同时结合、126对共现表位、取向对照21/20。
   - after：按摘要：160双抗/65亲本、CrossMab、疏水/电荷 ρ≈0.85–0.95，自结合 ρ≈0.60–0.88，热稳定性 ρ<0.4。未再写无法重下的逐项 0.95/0.94/0.89。

6. **c1-org-4**（Li / TSC2 类器官星形胶质，OA HTML）
   - before：39,539个细胞、GATOR1、E12–14周、Raptor敲除。
   - after：day 8 Cre → TSC2 双等位缺失；反应性星形胶质模块 d50/120/220；39,539细胞映射胎脑图谱（原文有此数，但GATOR1/Raptor不是本文实验）；50 nM rapamycin / 100 nM Torin-1。

7. **c1-org-5**（Poling / CCS 小肠类器官，OA HTML）
   - before：tHIO+ENS 收缩频率 1.0±0.1 min⁻¹、长度 1.0±0.1 cm。
   - after：约4000球体、d14移植植入率100%、P=9.25×10−7、183只动物、随访10周。1.0±0.1 不是本文主结果，已删除。

8. **c4-ai-8**（Hegelmaier / KYV-101，PMC13589498）
   - before：MG1**血肌酐**从15/39降至2。
   - after：该数字是 **QMG** 15/39→2（正文已写QMG，未改正文）。三例均无ICANS 与原文一致。

9. **c4-ai-9**（Bhoj / 双靶 CAR-T，PMC13240644）
   - before：2例队列却写「**三例**均无ICANS」。
   - after：两例均无ICANS。正文「移植后9个月血肌酐稳定」是肾移植后肾功能，保留。

### 无法追溯则删除（不保留错数）

10. **c6-ab-5**（Zhao / 纳米抗体预印本，摘要）
    - 删除：PRJ266_044、表达中位产量184 mg/L（摘要无）。
    - 保留摘要数字：288,000 / 8表位 / 100,000 / 116 / 46（39.7%）/ Rmax≥30 RU / 0.66–305 nM / 中位31.7 nM。

11. **c6-ab-8**（Lykhopiy，PMC13482293）
    - 删除：FMC63-VHH-48 EC50 **1.02 nM**（全文XML未找到）。
    - 保留：85/153/92克隆、0.015 vs 0.010 nM、100–1000倍、0.03 µg。

12. **c9-rna-8**（Jiang / PE-LNP，PMC13379318）
    - 删除：双载体PE-AAV9「**1×10^12 vg**」（全文未找到该剂量）。
    - 保留：2 mg/kg 49%、63倍/13倍、8周44%、PE-AAV9 46%、Phe 90%、360 µM、4 mg/kg 53%、PCSK9 94%。

---

## 作者介绍

通讯作者与单位一律按论文 correspondence / affiliation 重核。实验室方向仅保留此前已核官方页的 5 条（Weiskopf / Novarino / Liu / Schumacher / Garnett），其余继续省略、不写元注释。

**c4-ai-6 Calico**：论文单位列表 Aff3 = Calico Life Sciences, South San Francisco, CA USA；Lenardo xref Aff1+Aff2+Aff3。**保留**，不是误挂。另补共同通讯 Chuan Wu（Aff4 NCI Experimental Immunology Branch，✉）。

### 作者介绍 FIX（before → after）

1. **去个人邮箱 / 元注释**（不发布邮箱；不写“未核到/单位来自作者栏”）
   - c4-ai-8：删「（电子邮箱见论文作者栏）」
   - c4-ai-9：删「单位来自论文作者栏。」
   - c2-ai-1：删「邮箱andrew.bradbury@iqvia.com」
   - c3-io-4：删邮箱，并删「实验室方向未在本轮核到独立官方页，故省略。」
   - c1-org-4：删「（邮箱bateup@berkeley.edu）」
   - c8-vac-4：删邮箱
   - c6-ab-4 / c6-ab-5：删「预印本作者列表未在Europe PMC给出分条单位。」

2. **通讯身份与单位与论文不一致**
   - **c5-am-8**：Krause 无 ✉。before「共同通讯 Diane S. Krause」→ after「末位作者」。
   - **c9-rna-9**：Leeper 无 ✉。before「共同通讯 Nicholas J. Leeper」→ after 普通「作者」。
   - **c5-am-9**：Aartsma-Rus 论文单位是 Transgenic Facility Leiden。after「Aartsma-Rus 任职转基因设施；Hohenstein 人类遗传学系并兼转基因设施」。
   - **c6-ab-8**：Van Rompaey 为 Dualyx、#equal、无 ✉。after 删除该句。

3. **论文 correspondence 列出、介绍漏写的共同通讯（补上，不加邮箱）**
   - c4-ai-6：+ Chuan Wu（NCI）
   - c4-ai-5：+ Andrew R. J. Lawson
   - c3-io-5：+ Bertrand Routy、Logan A. Walsh
   - c8-vac-2：+ Shane Crotty
   - c8-vac-3：+ Jiwon Lee
   - c8-vac-4：+ Yi Wu（西安交大二附院肾内科）
   - c1-org-3：+ Keith L. Ligon
   - c1-org-5：+ Michael A. Helmrath
   - c7-cell-2：+ Yu Hu（胡豫）
   - c9-rna-5：+ Hesong Han、Aijun Wang

---

## 不可复核 / 限制

- **a-trap**：本轮 Cell Press / DOI 着陆页 403，仅 EPMC 摘要写 ∼50%。56%、2,667、3,693、96.1%、0.80/0.74、12,078、384、468、28.3%（91/322）、76.3%（174/228）、197、0.76–0.79 来自既有 OA HTML `source_quote`（文章 JSON 已存原句）。摘要约50%与正文56%并存，速览用Results口径并在解读注明摘要约50%。
- **c6-ab-4**：本轮 bioRxiv JATS 429。速览改为摘要可核区间 ρ≈0.85–0.95 / 0.60–0.88 / <0.4，不再写未重下的逐项 HIC/SMAC/HAC 0.95/0.94/0.89。
- **c6-ab-5**：摘要可核 288,000 / 100,000 / 116 / 46 / 39.7% / 0.66–305 / 31.7；删除摘要没有的 184 mg/L 与克隆号。
- **实验室方向**：未新核官方实验室页，故不新增方向句。
- **正文/数据卡**：错误数字只在被改写的速览；解读正文未改。

速览汉字 100–200、作者介绍 80–200 保持；DEALS 块未改（sha256 `bf29e0b21cb5fa50e1f25d41c23f4d5a804012d266b6fa06d43712c9ecb454a6`）。

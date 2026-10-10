# 人源化小鼠中敲除DPP9经CARD8焦亡导致造血干细胞耗竭与全血细胞减少

![卡片图](c5-am-8_card.webp)

**在MISTRG6小鼠中CRISPR敲除人CD34+ HSPC的DPP9，可重现外周与骨髓血细胞减少；丢失由CARD8介导的焦亡驱动，敲除CARD8或CASP1可挽救，敲除NLRP1不能。**

主领域：疾病模型　相关：自身免疫与移植免疫 / DPP9缺乏 / CARD8炎性体 / 人源化小鼠 / 造血干细胞焦亡

## 关键数据卡

- **研究类型**：临床前动物研究（人CD34+ HSPC经CRISPR编辑后移植入MISTRG6人源化小鼠）
- **样本量 n**：正文给出供体与时间点，未汇总全研究小鼠总数；平台验证用2名供体、数据来自2次实验（TRAC）或2名供体（CSF1R）；DPP9表型在不同人供体间一致；scRNA-seq为2名供体、质控后超过5,000个细胞；竞争移植为对照与敲除1:1混合
- **对照**：同一人供体、对照sgRNA（含TRAC或AAVS1）编辑的HSPC；同窝MISTRG6受体；双敲除实验为同一供体的对照、DPP9−/−、DPP9−/−CARD8−/−与DPP9−/−NLRP1−/−
- **干预/剂量**：新生1–3日龄MIS^h/mTRG6小鼠肝内注射30,000个CD34+ HSPC（20 μL），不预清除；股骨内移植为成体1.5 Gy亚致死照射后同样30,000个细胞；RNP为40 pmol Cas9加共100 pmol sgRNA（每基因2或3条向导）
- **随访**：TRAC观察到移植后16周；CSF1R为9周；DPP9主表型为8–9周；竞争移植为7周；scRNA-seq为4周；CARD8/NLRP1双敲除为10–11周，CASP1双敲除为9–11周
- **主要终点**：核心读出为人CD45+外周血细胞及骨髓Lin−CD34+ HSPC（含HSC、MPP等亚群）是否维持
- **主要终点结果**：DPP9−/−使人CD45+细胞（含单核细胞与B细胞）及骨髓Lin−CD34+ HSPC显著减少，HSC、MPP、CLP、CMP、MEP均下降；同时敲除CARD8或CASP1可挽救，敲除NLRP1不能
- **统计量**：均数±标准差；双侧Student t检验或双因素ANOVA；P<0.05为显著；图注以P≤0.05至P≤0.0001标星，未给精确P值；部分读出用相对细胞数（各动物对同供体对照均值的倍数）
- **安全性**：不适用（疾病机制模型；表型本身即HSPC焦亡与全血细胞减少）
- **证据等级**：全文
- **核对记录**：读了Europe PMC全文XML（PMC13574144）：Abstract、Introduction、Results各节、Discussion、Methods、Fig.1–5图注

![机制示意图](c5-am-8_mech.webp)

1. CRISPR敲除人HSPC的DPP9并移植
2. 骨髓HSPC与外周血细胞丢失
3. CARD8炎性体激活、CASP1依赖焦亡
4. 敲除CARD8或CASP1可挽救，NLRP1不能

> 左：人CD34+ HSPC经CRISPR敲除DPP9后植入MISTRG6骨髓龛；中：DPP9不再扣留CARD8，C端毒性片段激活CASP1；右：HSPC焦亡导致多谱系减少，敲除CARD8可阻断，敲除NLRP1无效。示意图由 AI 生成，依据原文结果绘制，非期刊原图，不代表分子比例

## 研究背景与待解问题

DPP9功能缺失导致Hatipoglu综合征：反复发热与感染、全血细胞减少、贫血，常需骨髓移植；1例患者曾报告血清炎症细胞因子升高。常规Dpp9突变小鼠免疫细胞数量正常，HSC连续移植仍能重建，说明全血细胞减少可能是人特异机制。

DPP9是人NLRP1与CARD8炎性体的内源抑制因子。CARD8在包括Mus musculus在内的多数啮齿类中缺失，却在人造血细胞中广泛表达。Xiao、Krause、Flavell等用MISTRG6人源化小鼠做逆向遗传学，问的是：人HSPC缺了DPP9之后，究竟是NLRP1还是CARD8把干细胞烧掉。

## 研究设计

MISTRG6在Rag2−/−IL2rg−/−背景上把人CSF1、IL3/CSF2、SIRPA、THPO、IL6敲入对应鼠基因座。实验受体为MIS^h/mTRG6：SIRPα及部分因子为人–鼠杂合，M-CSF、IL-3/GM-CSF、血小板生成素和IL-6为人源纯合。新生1–3日龄肝内注射30,000个脐血或胎肝来源CD34+细胞，不预清除。iPSC来源CD34+细胞虽表达CD34、CD90、CD49f，植入率未能超过1%，故改用原代HSPC。编辑体系为40 pmol Cas9加共100 pmol sgRNA。雌雄供体与受体均使用，作者称两性结果相似。相对细胞数定义为各动物相对同队列对照均值的倍数。

## 核心结果

### 编辑在体内持续，DPP9缺失重现血细胞减少

TRAC敲除后，移植小鼠在脾、肝、肺直至16周几乎不能产生人T细胞，B细胞与髓系保留。CSF1R敲除9周后，血中单核细胞CSF1R下降，CD16+单核细胞与肝巨噬细胞减少，人CD45+总量相近。DPP9用3条sgRNA打催化外显子，8–9周后外周人CD45+、单核细胞与B细胞显著减少，T细胞数量与对照相当——作者将其部分归因于活化T细胞对CARD8死亡的抵抗，以及人源化模型中T细胞在植入差时的代偿扩增。骨髓Lin−CD34+ HSPC不能维持，CLP、CMP、MEP、HSC与MPP均显著减少。成体股骨内直接注射仍不能维持，说明不只是归巢失败。

### 丢失是细胞内在的，体外弱于体内

液体培养中DPP9−/− HSPC扩增正常。CFU集落减少，但幅度远小于体内丢失，且无谱系偏向。单细胞分选的CD34+CD38−CD90+ HSC在7天扩增后细胞更少，大幅扩增（>100个细胞）的克隆比例也更低，髓系与红系分化比例相近。将对照与DPP9−/−按1:1混合移植，7周后ddPCR显示敲除细胞在HSC、祖细胞和Lin+分化细胞中几乎消失。

### 转录改变很少，炎性体元件却齐备

移植4周、敲除细胞尚未完全消失时，对Lin−CD34+做scRNA-seq：超过5,000个细胞、平均每细胞5,000个基因、13个簇。HSC–MPP仅77个差异基因，髓系祖细胞29个、CLP 14个、pro-B 16个；通路信号下调主要来自FOS与JUN。CARD8在CD34+各亚群广泛表达，NLRP1可检出但更低，CASP1与GSDMD同样存在。

### CARD8而非NLRP1介导焦亡

编辑后培养3天、再用DPP8/DPP9抑制剂Val-boropro（VbP）刺激20小时，LDH显示焦亡；DPP9−/−对VbP更敏感。敲除CARD8或CASP1则焦亡完全消失。同一供体细胞移植10–11周后，DPP9−/− HSPC丢失可被CARD8共敲除挽救，包括HSC与MPP；NLRP1共敲除不能挽救。外周人CD45+、单核细胞与B细胞同样随CARD8而非NLRP1恢复。敲除CASP1（9–11周）也能挽救骨髓HSPC丢失。

## 机制解读

**原文实验证明：**DPP9催化外显子敲除使人HSPC在MISTRG6骨髓中细胞自主地丢失，并带动多谱系外周减少。HSPC具备CARD8–CASP1–GSDMD装置；VbP诱发的LDH释放完全依赖CARD8与CASP1；体内共敲除CARD8或CASP1挽救干细胞与白细胞减少，共敲除NLRP1无效。转录组几乎不动，指向蛋白水平的炎性体门槛，而不是转录重编程。

**作者推测：**人DPP8可能仍压得住NLRP1、压不住CARD8；或者CARD8在HSPC中的表达优势决定了选择性。骨髓龛中的尚未鉴定应激（蛋白折叠、还原应激、对有限人THPO的竞争）可能在移植后点燃CARD8。体内（超过7周）远比体外（不足2周）严重，提示龛内信号在加速丢失。患者突变多为酶活减弱或蛋白减少，本文用催化外显子敲除来建模，与临床等位基因并不等同。

## 局限与不确定

- 图注多以星号给出P≤0.05至P≤0.0001，正文未报告精确P值与每组小鼠只数；相对细胞数在供体间归一化，不能直接读成绝对重建水平。
- scRNA-seq在4周、敲除细胞仍在时取样，存在幸存者偏倚；77个HSC–MPP差异基因也说明“几乎无转录改变”不是零改变。
- T细胞未减少，不能解释患者全血细胞减少中的淋巴细胞全貌；人源化龛仍是鼠基质加几个人细胞因子。
- 点燃CARD8的体内应激未鉴定；NLRP1为何可有可无仍是三种假说并列，没有闭合。

## 临床/产业意义

若CARD8门槛同样适用于患者HSPC，Hatipoglu综合征的骨髓衰竭就不是“鼠模型失败”，而是人干细胞多了CARD8这条哨兵。治疗逻辑会从泛炎性体抑制收窄到CARD8或CASP1轴，也解释了为何Dpp9小鼠正常、患者却需要移植。DPP9的SNP还被关联到特发性肺纤维化与SARS-CoV-2转归，但那些组织位点并未在本文直接检验。这是人源化小鼠机制证据，不是临床干预试验。

## 作者、出处与核对

Xiao T, Brewer JR, Carlino M, Han A, Takabe YJ, Lee CY, et al. Reverse genetics in humanized mice reveals CARD8-mediated pyroptosis causing pancytopenia in human DPP9 deficiency. J Clin Invest. 2026 Sep 15. https://doi.org/10.1172/jci207530

证据等级：全文；核对记录：读了Europe PMC全文XML（PMC13574144）：Abstract、Introduction、Results各节、Discussion、Methods、Fig.1–5图注

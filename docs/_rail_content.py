#!/usr/bin/env python3
"""Apply quick_look / author_intro to catalog + article JSON; emit check docs."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Character count: all non-whitespace. Spec is 100–200 / 80–200.
def nchars(s: str) -> int:
    return len(re.sub(r"\s+", "", s or ""))


# quick_look: numbers only from that article's verified body / 关键数据卡.
# author_intro: corresponding author first; first author at most a brief close.
QUICK = {
    "c6-ab-9": "Marks等在人、食蟹猴、小鼠组织中比对pIgR后，把抗pIgR VHH接到抗血凝素IgG上；14只食蟹猴单次静脉5 mg/kg后，双抗BAL总暴露为对照IgG的5.5倍，血清t½约1.57对9.53天。",
    "c5-am-8": "在MISTRG6小鼠中CRISPR敲除人CD34+ HSPC的DPP9，可重现外周与骨髓血细胞减少；丢失由CARD8介导的焦亡驱动，敲除CARD8或CASP1可挽救，敲除NLRP1不能。新生鼠肝内注射30,000个细胞，主表型读出于8–9周。",
    "c9-rna-9": "1/2a期随机双盲安慰剂对照中，吸入ARO-RAGE可耐受；健康志愿者n=58、哮喘n=19，单次剂量含10–184 mg。健康志愿者单剂184 mg后支气管肺泡灌洗可溶性RAGE平均最多下降90.2%；无导致停药的不良事件。",
    "c5-am-9": "在mdx背景下构建人DMD外显子44、45、51或53缺失的4个小鼠模型；8周龄雄鼠腓肠肌缺乏或仅有痕量抗肌萎缩蛋白。连续两日肌内注射vivo-morpholino（43/44为100 μg/块肌，50/52为50 μg/块肌）可检出目标外显子跳跃并恢复截短蛋白。",
    "c4-ai-8": "3例AChR阳性难治全身型重症肌无力（2女1男）接受KYV-101（1×10^8细胞），清淋为氟达拉滨30 mg/m^2加环磷酰胺300 mg/m^2连用3天；随访24、19、19个月均维持停用MG特异性免疫治疗的临床缓解，不良事件短暂可处理。",
    "c6-ab-7": "Pagès-Geli等用功能优先表面组筛选加156个异源scFv-Fc双抗，选出低亲和SIRPα诱饵×CD38分子WTa2d1xCD38；在10条淋巴瘤系上IC50为18.0 pM至3.08 nM，体内可延长生存并与利妥昔单抗联用出现完全治愈。",
    "c9-rna-7": "6例SOD1-ALS开放标签剂量递增试验中，鞘内RAG-17安全与耐受可接受（治疗期不良事件2/6）；队列1第240天、队列2第210天脑脊液SOD1分别较基线下降69%与56%，血浆神经丝轻链下降62%与52%。",
    "c6-ab-8": "Lykhopiy等把抗CD25/CD122/CD132的VHH装成三特异激动抗体，先导αβγVHH-48在HEKαβγ上的pSTAT5 EC50为0.015 nM，接近人IL-2的0.010 nM；改几何后可在皮摩尔甚至飞摩尔级偏向性激活Treg。",
    "c5-am-7": "对11个单基因ASD小鼠模型的251个样本、200,787个核做单核RNA+ATAC测序，突变汇聚于放射状胶质谱系的短暂发育延迟；共享转录差异在P4层II/IV神经元最大（715个DEG），层V/VI深部投射神经元有600个DEG。",
    "c9-rna-8": "系统优化三组分prime editor的OF-02脂质纳米颗粒后，单剂总RNA 2 mg/kg于给药后7天在小鼠全肝达平均49%无indel精准编辑，较初始s0.1提高63倍、较s0.2提高13倍；8周时2 mg/kg为44%。",
    "c4-ai-9": "I期安全导入队列中2例极高致敏肾移植候选者接受各5×10^7 CAR+细胞的CD19与BCMA双靶CAR-T后，cPRA下降，并分别于输注后第229天和第93天接受交叉配型相容肾移植；报告期未见剂量限制性毒性。",
    "a-trap": "T-RAP将VDJdb-10库（3,693个TCR）导入Jurkat并汇集筛选，平均仅56%（n=2,667）复现原注释反应性；阵列共培养一致率96.1%。验证集上tcrdist3与AlphaFold3平均AUROC分别为0.80、0.74。",
    "c1-org-2": "英国多中心团队从907份样本（878名供者）建立256例肿瘤类器官（效率28%），171对类器官–肿瘤完成WGS，162例通过质控的全基因组CRISPR–Cas9筛选（成功率85%），用以绘制基因依赖。",
    "c9-rna-5": "LPS 1 mg/kg预处理后4小时再给1 mg/kg mRNA时，E20可切换脂质纳米颗粒未升高IL-6、IL-1β、MIP-2，而MC3-DLin、cKK-E12、SM-102的LNP使其大幅升高；pDNA 1 mg/kg时MC3-DLin组24小时内全部死亡。",
    "c8-vac-3": "75名健康成人观察性队列（mRNA-1010 38人、Fluarix 37人）中，mRNA接种者HA特异性记忆B细胞增幅在第4及17/26周更高；FNA亚组13人中5人（约38%）在26周仍检出生发中心反应，Fluarix组两季均未检出。",
    "c7-cell-2": "4例复发难治骨髓瘤单次静脉输注体内BCMA CAR-T产品ESO-T01（0.2×10^9转导单位），随访最长15个月未见新增重要相关不良事件；1例持续sCR，中位PFS 4.0个月，另3例复发或进展后死亡。4例均出现3–4级血液学毒性。",
    "c5-am-2": "在Esco2缺失的免疫缺陷无皮质小鼠中，5至17日龄每侧半球移植2个人皮质类器官（每只共4个）；移植物2至3个月约增长4.7倍（n=14），3个月时占皮质组织体积91.9%（n=7），移植成功率86.2%（29只）。",
    "c1-org-1": "人脑皮层类器官培养至5年（NeuN染色见于5.8年）；scRNA-seq覆盖110个类器官、424,720个细胞。Horvath与皮层甲基化时钟预测年龄与培养时间相关（r=0.88–0.90，中位绝对误差7.25与20.04个月）。",
    "c2-ai-1": "29家机构的511个AI设计或预测抗体经盲评实验验证：挑战1含25家机构165个提交，最佳95 pM（较亲本提高2,000倍）；挑战2中仅9.8–13.8%的提交亲和力高于簇对照，随机挑选克隆为39%。",
    "c2-ai-4": "2a期IPF试验的蛋白质组子研究纳入42例（安慰剂11、30 mg QD 11、30 mg BID 11、60 mg QD 9）；口服rentosertib 12周后，54个ΔBioAge比较中21个达Q<0.10，第4周18个比较中11个显著。",
    "c3-io-4": "在LCMV与黑色素瘤模型中，靶向ZMYND8使P14细胞偏向效应样耗竭状态；体内scCRISPR筛出19,032个P14细胞，效应样/Tex term验证n=7/组，肿瘤治疗n=5–8/组，并伴随IL-2R–STAT5增强。",
    "c5-am-5": "共55只约4周龄幼猴口服SHIV；72小时启动LRM（50 mg/kg）、bNAb（各20 mg/kg）和27周ART。三联组8/8在ATI后6个月无病毒血症，PBMC与组织未检出细胞相关病毒DNA，第84周IPDA亦未检出前病毒。",
    "c1-org-3": "国际多中心从2,780名供者建立665个患者来源癌症模型，覆盖25种癌；421对匹配肿瘤–模型中97.8%至少保留两类DNA特征，201对甲基化配对中190对（95%）比随机配对更接近。临床数据覆盖522个模型。",
    "c4-ai-6": "发现队列含4个无关家系7名IBD个体。人类遗传、活检和小鼠2.5% DSS模型显示，GPR15引导CD8+ TIGR结肠归巢并限制肠炎；散发性UC结肠活检中该群体减少42%（HC n=8，UC n=7）。主要小鼠实验多为n=11/组。",
    "c2-ai-5": "Germinal在4个蛋白靶点每抗原只测试43–101个设计：纳米抗体侧为101个PD-L1、46个IL3、43个IL20、52个BHRF1；BLI可检测结合为7/25、2/11、4/11、5/20，scFv/Fab确认3/4个抗PD-L1。",
    "c6-ab-4": "在未经同行评审的160个双抗、65个亲本臂库中，Ritter等用亲本组合预测实测开发性；HIC/SMAC/HAC的Spearman ρ达0.95、0.94和0.89，AC-SINS为0.79–0.88，Tm1/Tm2仅–0.06至0.32。",
    "c1-org-4": "第8天Cre慢病毒诱导TSC2双等位缺失后，人脑类器官中TSC2−/− GLC在50、120、220天相对同一类器官内TSC2 c/−细胞均显著提高反应性星形胶质模块评分；纯化星形胶质细胞接受50 nM rapamycin或100 nM Torin-1。",
    "c3-io-5": "在12种小鼠饮食模型中，肥胖性饮食4/6（66.7%）关联抗PD-1应答，非肥胖性饮食为2/6（33.3%）；ICI评分与体重、脂肪量、糖耐量、胰岛素、瘦素或代谢评分无显著关联，提示关键在饮食–肠道菌群轴。",
    "c8-vac-2": "24只恒河猴分4组各6只，接受N332-GT5起始及后续异源加强；按摘要口径44%出现血清bnAb活性。8只最佳动物boost 7后平均相对BG18广度41%，几何均数ID50范围52–481，总体为107。",
    "c1-org-5": "CCS在限位槽内让约4000个小肠球体暂限融合，d6取出、续培至d14后移植，植入率100%，高于同日龄常规HIO（Fisher精确检验P=9.25×10−7）；全研究动物183只，移植后随访10周。",
    "c2-ai-2": "NISE闭环神经网络从头设计小分子结合蛋白：exatecan 4个设计全部结合（Kd 0.12–17 µM）；apixaban 6个设计中5个结合，APEX Kd为80 pM（95% CI 54–122 pM），EPIC(Q51N/M97L) Kd为1.2 nM。",
    "c3-io-2": "61例初治转移性黑色素瘤2:1随机；瘤内伊匹木单抗0.3 mg/kg联合静脉纳武利尤单抗1 mg/kg。IT组37例可评估中9例（24.3%）发生6个月3–4级治疗相关不良事件，低于30%阈值；中位随访55.5个月。",
    "c6-ab-6": "1期试验入组288例，复发/难治小细胞肺癌单药124例中ABBV-706每3周给药确认ORR为52%（65/124）；第2a部分1.8与2.5 mg/kg的ORR为56%与59%，选定1.8 mg/kg为推荐2期剂量。中位随访16.9个月。",
    "c6-ab-5": "针对一个无结构、无既有抗体的DSRCT新靶点，Zhao等从头生成288,000个纳米抗体设计，Pareto过滤后100,000个进入酵母展示；116个SPR候选中46个（39.7%）获可靠动力学，KD 0.66–305 nM，中位31.7 nM。",
    "c8-vac-4": "小鼠将mannadjuvant（alum 100 µg＋mannan 500 µg）混入WA1 mRNA（1 µg），把对BA.5与XBB.1.5假病毒的中和延长至第500天；食蟹猴10只（30 µg疫苗）IgG与三种假病毒中和升至第180天。",
    "c4-ai-5": "14例自身免疫甲状腺病供者的单分子测序中，桥本甲状腺炎供者H1体内有135种不同TNFRSF14突变及59种CD274突变；TNFRSF14与CD274截短突变dN/dS为141和37，显示检查点基因正选择。",
}

INTRO = {
    "c6-ab-9": "通讯作者Adam Zwolak任职Johnson & Johnson Innovative Medicine（Spring House, PA）。共同末位Wan Cheung Cheung同属该机构。第一作者Kelsie Marks任职同机构La Jolla。",
    "c5-am-8": "通讯作者Richard A. Flavell任职耶鲁大学医学院免疫生物学系与霍华德·休斯医学研究所。共同通讯Diane S. Krause任职耶鲁干细胞中心及检验医学系。第一作者Tianli Xiao同属免疫生物学系。",
    "c9-rna-9": "通讯作者Matthias Salathe任职堪萨斯大学医学中心内科。共同通讯Nicholas J. Leeper任职斯坦福大学医学院外科血管外科。第一作者Mark O’Carroll任职奥克兰市立医院呼吸科。",
    "c5-am-9": "通讯作者Annemieke Aartsma-Rus与Peter Hohenstein任职莱顿大学医学中心人类遗传学系；Hohenstein同时任职该校转基因设施。第一作者Maaike van Putten同属人类遗传学系。",
    "c4-ai-8": "通讯作者Aiden Haghikia任职汉诺威医学院神经内科与临床神经生理科（电子邮箱见论文作者栏）。第一作者Tobias Hegelmaier同属该科；合作单位包括鲁尔大学波鸿St. Josef医院神经内科。",
    "c6-ab-7": "通讯作者Kipp Weiskopf任职贝斯以色列女执事医疗中心内科、哈佛医学院及Dana-Farber肿瘤内科；实验室研究巨噬与肿瘤相互作用及髓系免疫检查点。第一作者Carlota Pagès-Geli同属BIDMC内科。",
    "c9-rna-7": "通讯作者Yilong Wang（王拥军）任职首都医科大学附属北京天坛医院神经内科及国家神经系统疾病临床医学研究中心。共同通讯Long-Cheng Li任职Ractigen Therapeutics与南通大学医学院。第一作者Weiqi Chen任职天坛医院神经内科。",
    "c6-ab-8": "通讯作者Susan M. Schlenner任职KU Leuven微生物、免疫与移植学系Adaptive Immunology实验室。共同通讯Valentina Lykhopiy任职argenx并兼该实验室；Luc Van Rompaey任职Dualyx。",
    "c5-am-7": "通讯作者Gaia Novarino为ISTA教授，研究遗传性神经发育障碍（癫痫、智力障碍与自闭症）的基因与分子机制。第一作者Lena A. Schwarz同属ISTA。",
    "c9-rna-8": "通讯作者David R. Liu为Broad研究所Merkin讲席教授、哈佛大学化学与化学生物学系教授及HHMI研究员，实验室开发碱基编辑与prime editing。第一作者Allen Y. Jiang任职Broad与哈佛化学系。",
    "c4-ai-9": "通讯作者Ali Naji任职宾夕法尼亚大学Perelman医学院外科。第一作者Vijay G. Bhoj任职该院病理与检验医学系；Alfred L. Garfall任职该院内科。单位来自论文作者栏。",
    "a-trap": "通讯作者Ton N. Schumacher为荷兰癌症研究所分子肿瘤与免疫学部组长、莱顿大学医学中心血液学系教授，实验室研究T细胞如何识别肿瘤细胞。第一作者Marius Messemaker同属NKI该学部。",
    "c1-org-2": "通讯作者Mathew J. Garnett为Wellcome Sanger Institute转化癌症基因组学组长，实验室用药物与CRISPR筛选及类器官绘制癌症依赖图谱。第一作者C. Herranz-Ors同属Sanger。",
    "c9-rna-5": "通讯作者Niren Murthy任职加州大学伯克利分校生物工程系及Innovative Genomics Institute。第一作者Dengpan Liang同属上述单位。",
    "c8-vac-3": "通讯作者Ali H. Ellebedy任职华盛顿大学圣路易斯医学院病理与免疫学系，兼Bursky人类免疫学与免疫治疗中心。第一作者Hanover C. Matz同属病理与免疫学系。",
    "c7-cell-2": "通讯作者Heng Mei（梅恒）为华中科技大学同济医学院附属协和医院血液内科教授、主任，并任湖北省细胞治疗临床医学中心主任。第一作者含Jia Xu等，同属该院血液内科。",
    "c5-am-2": "通讯作者Sergiu P. Pașca任职斯坦福大学精神与行为科学系及Stanford Brain Organogenesis Program（Wu Tsai Neurosciences Institute & Bio-X）。第一作者Konstantin Kaganovsky同属上述单位。",
    "c1-org-1": "通讯作者Paola Arlotta任职哈佛大学干细胞与再生生物学系及Broad研究所Stanley精神疾病研究中心。第一作者Irene Faravelli同属上述单位，并兼米兰大学。",
    "c2-ai-1": "通讯作者Andrew R. M. Bradbury任职Specifica（IQVIA业务，美国新墨西哥州圣菲；邮箱andrew.bradbury@iqvia.com）。第一作者M. Frank Erasmus同属该公司并同为通讯。",
    "c2-ai-4": "通讯作者Alex Zhavoronkov任职Insilico Medicine（阿布扎比、上海与马萨诸塞Cambridge）。末位作者Vadim N. Gladyshev任职哈佛医学院布莱根妇女医院遗传学分部及Broad研究所。",
    "c3-io-4": "通讯作者Hongbo Chi（迟洪波）任职美国圣裘德儿童研究医院免疫学系（邮箱hongbo.chi@stjude.org）。第一作者Yan Wang同属该系。实验室方向未在本轮核到独立官方页，故省略。",
    "c5-am-5": "通讯作者Jonah B. Sacha任职俄勒冈健康与科学大学俄勒冈国家灵长类研究中心及疫苗与基因治疗研究所。共同通讯Nancy L. Haigwood任职同一灵长类研究中心。",
    "c1-org-3": "通讯作者Jesse S. Boehm任职Broad研究所及MIT科赫综合癌症研究所。共同通讯还包括Louis M. Staudt（NCI）等。第一作者Dina ElHarouni任职Broad与Dana-Farber病理系。",
    "c4-ai-6": "通讯作者Michael J. Lenardo任职NIAID免疫系统生物学实验室免疫发育分子学部、NIAID临床基因组学项目及Calico Life Sciences（South San Francisco）。第一作者Jing Cui同属NIAID上述单位。",
    "c2-ai-5": "通讯作者Xiaojing J. Gao任职斯坦福大学化学工程系、Stanford Biophysics、Sarafan ChEM-H与Bio-X。共同通讯Brian L. Hie任职斯坦福与Arc Institute；Luis S. Mille-Fragoso任职斯坦福生物工程系。",
    "c6-ab-4": "通讯作者Ammar Arsiwala任职Ginkgo Bioworks（马萨诸塞）。预印本作者列表未在Europe PMC给出分条单位。第一作者Seth Ritter。",
    "c1-org-4": "通讯作者Helen S. Bateup任职加州大学伯克利分校分子与细胞生物学系及神经科学系（邮箱bateup@berkeley.edu）。第一作者Thomas L. Li同属上述两系。",
    "c3-io-5": "通讯作者Daniela F. Quail任职麦吉尔大学Goodman癌症研究所、实验医学分部与生理学系。第一作者Lysanne Desharnais任职该所及人类遗传学系。",
    "c8-vac-2": "通讯作者William R. Schief任职Scripps Research免疫与微生物学系及IAVI中和抗体中心，并任职Moderna。第一作者Jon M. Steichen同属Scripps与IAVI NAC。",
    "c1-org-5": "通讯作者Maxime M. Mahe任职南特大学/Inserm TENS UMR1235，并兼辛辛那提儿童医院小儿普外胸外科及干细胞与类器官医学中心。第一作者Holly M. Poling任职辛辛那提儿童医院。",
    "c2-ai-2": "通讯作者Nicholas F. Polizzi任职Dana-Farber癌症研究所癌症生物学系及哈佛医学院生物化学与分子药理学系。第一作者Benjamin Fry任职哈佛生物物理研究生项目及上述两系。",
    "c3-io-2": "通讯作者Aurélien Marabelle任职古斯塔夫·鲁西癌症研究所早期临床试验与治疗创新部、INSERM CIC 1428及U1015转化免疫治疗实验室，并兼巴黎萨克莱大学医学院。第一作者Lambros Tselikas任职该所介入放射。",
    "c6-ab-6": "通讯作者Lauren Averett Byers任职德克萨斯大学MD安德森癌症中心。末位作者Sreenivasa Chandana任职START Midwest（Grand Rapids, MI）。",
    "c6-ab-5": "通讯作者Xinyun (Nina) Cheng任职Amazon Web Services Applied AI Solutions, Life Sciences。第一作者Yue Zhao。预印本作者列表未在Europe PMC给出分条单位。",
    "c8-vac-4": "通讯作者Ivan Zanoni任职哈佛医学院及波士顿儿童医院免疫科与消化科（邮箱ivan.zanoni@childrens.harvard.edu）。第一作者Kautilya K. Jena同属哈佛医学院与波士顿儿童医院免疫科。",
    "c4-ai-5": "通讯作者Iñigo Martincorena任职Wellcome Sanger Institute体细胞基因组学项目（Hinxton）。第一作者Pantelis A. Nicola同属该项目。",
}

AU_CLEAN = {
    "c6-ab-9": "Adam Zwolak，Johnson & Johnson Innovative Medicine, Spring House",
    "c5-am-8": "Richard A. Flavell / Diane S. Krause，Yale School of Medicine",
    "c9-rna-9": "Matthias Salathe，堪萨斯大学医学中心内科；Nicholas J. Leeper，斯坦福大学外科",
    "c5-am-9": "Annemieke Aartsma-Rus / Peter Hohenstein，Leiden University Medical Center",
    "c4-ai-8": "Aiden Haghikia，Hannover Medical School神经内科",
    "c6-ab-7": "Kipp Weiskopf，Beth Israel Deaconess Medical Center / Harvard Medical School",
    "c9-rna-7": "Yilong Wang，首都医科大学附属北京天坛医院；Long-Cheng Li，Ractigen Therapeutics",
    "c6-ab-8": "Susan M. Schlenner，KU Leuven；Valentina Lykhopiy，argenx",
    "c5-am-7": "Gaia Novarino，Institute of Science and Technology Austria",
    "c9-rna-8": "David R. Liu，Broad Institute / Harvard University",
    "c4-ai-9": "Ali Naji，University of Pennsylvania Perelman School of Medicine外科",
    "a-trap": "Ton N. Schumacher，荷兰癌症研究所分子肿瘤与免疫学部",
    "c9-rna-5": "Niren Murthy，加州大学伯克利分校生物工程系",
    "c2-ai-4": "Alex Zhavoronkov，Insilico Medicine；Vadim N. Gladyshev，哈佛医学院",
    "c4-ai-6": "Michael J. Lenardo，NIAID / Calico Life Sciences",
    "c1-org-3": "Jesse S. Boehm，MIT科赫综合癌症研究所 / Broad研究所",
    "c6-ab-4": "Ammar Arsiwala，Ginkgo Bioworks, Inc., MA, USA",
    "c6-ab-5": "Xinyun (Nina) Cheng，Amazon Web Services, Applied AI Solutions, Life Sciences",
    "c5-am-5": "Jonah B. Sacha / Nancy L. Haigwood，Oregon National Primate Research Center, OHSU",
    "c6-ab-6": "Lauren Averett Byers，MD Anderson Cancer Center",
}

# Per-article source notes for author_check.md
SOURCES = {
    "c6-ab-9": [
        "通讯/单位：Europe PMC PMC13644599 作者列表（Zwolak, J&J Innovative Medicine, Spring House, PA）；解读作者节写明末位通讯",
        "EPMC: https://europepmc.org/article/PMC/PMC13644599",
        "DOI: https://doi.org/10.1080/19420862.2026.2743413",
        "实验室方向：未写入（未见独立官方实验室页）",
    ],
    "c5-am-8": [
        "单位：Europe PMC PMC13574144（Flavell: Department of Immunobiology / HHMI；Krause: Yale Stem Cell Center, Laboratory Medicine, Cell Biology）",
        "EPMC: https://europepmc.org/article/PMC/PMC13574144",
        "DOI: https://doi.org/10.1172/jci207530",
        "实验室方向：未写入（未核到独立实验室页陈述）",
    ],
    "c9-rna-9": [
        "通讯：全文XML corresp=yes Matthias Salathe，email msalathe@kumc.edu；单位 Department of Internal Medicine, University of Kansas Medical Center",
        "Leeper：论文作者列表（斯坦福外科血管外科）；解读原au标注共同通讯",
        "EPMC: https://europepmc.org/article/PMC/PMC13577916",
        "DOI: https://doi.org/10.1038/s41591-026-04607-z",
    ],
    "c5-am-9": [
        "单位：Europe PMC PMC13580558（Human Genetics / Transgenic Facility Leiden, LUMC）",
        "EPMC: https://europepmc.org/article/PMC/PMC13580558",
        "DOI: https://doi.org/10.1242/dmm.052875",
    ],
    "c4-ai-8": [
        "通讯：末位作者电子邮箱 haghikia.aiden@mh-hannover.de；单位 Department of Neurology and Clinical Neurophysiology, Hannover Medical School",
        "EPMC: https://europepmc.org/article/PMC/PMC13589498",
        "DOI: https://doi.org/10.1016/j.xcrm.2026.103000",
    ],
    "c6-ab-7": [
        "通讯：末位作者邮箱 kweiskop@bidmc.harvard.edu；单位 BIDMC Medicine / HMS / DFCI Medical Oncology",
        "实验室方向：https://www.weiskopf-lab.org/ 与 https://www.weiskopf-lab.org/research（巨噬–肿瘤相互作用、髓系检查点）",
        "EPMC: https://europepmc.org/article/PMC/PMC13482096",
        "DOI: https://doi.org/10.1038/s41467-026-76180-5",
    ],
    "c9-rna-7": [
        "通讯：全文XML corresp Long-Cheng Li（Ractigen / 南通大学）与 Yilong Wang（北京天坛医院神经内科等）",
        "EPMC: https://europepmc.org/article/PMC/PMC13375534",
        "DOI: https://doi.org/10.1038/s41591-026-04491-7",
    ],
    "c6-ab-8": [
        "通讯：Schlenner 邮箱 susan.schlenner@kuleuven.be；Lykhopiy 邮箱 vlykhopiy@argenx.com；单位 KU Leuven Adaptive Immunology Laboratory / argenx / Dualyx",
        "EPMC: https://europepmc.org/article/PMC/PMC13482293",
        "DOI: https://doi.org/10.1038/s41467-026-75024-6",
    ],
    "c5-am-7": [
        "通讯：全文XML corresp Gaia Novarino，gaia.novarino@ist.ac.at；ISTA",
        "职位与方向：https://ista.ac.at/en/research/novarino-group/（教授；遗传性神经发育障碍的基因与分子机制）",
        "DOI: https://doi.org/10.1038/s41586-026-10679-1",
    ],
    "c9-rna-8": [
        "通讯：末位作者邮箱 drliu@fas.harvard.edu；单位 Broad Merkin Institute / Harvard CCB / HHMI",
        "职位与方向：https://www.liugroup.us/ 与 https://www.broadinstitute.org/bios/david-liu（碱基编辑、prime editing）",
        "EPMC: https://europepmc.org/article/PMC/PMC13379318",
        "DOI: https://doi.org/10.1038/s41565-026-02200-6",
    ],
    "c4-ai-9": [
        "单位：Europe PMC PMC13240644（Naji: Department of Surgery, Perelman School of Medicine, University of Pennsylvania）",
        "通讯：解读与论文作者列表将Naji列为通讯",
        "DOI: https://doi.org/10.1056/nejmoa2513428",
    ],
    "a-trap": [
        "通讯：末位作者电子地址 t.schumacher@nki.nl；单位 NKI Division of Molecular Oncology & Immunology / Oncode / LUMC Hematology",
        "职位与方向：https://www.nki.nl/research/research-groups/ton-schumacher/（T细胞如何识别肿瘤细胞）",
        "DOI: https://doi.org/10.1016/j.immuni.2026.09.007",
        "Crossref/EPMC作者单位：Messemaker、Schumacher 均标注 NKI",
    ],
    "c1-org-2": [
        "通讯：末位作者邮箱 mg12@sanger.ac.uk；单位 Wellcome Sanger Institute",
        "职位与方向：https://www.sanger.ac.uk/group/garnett-group/（转化癌症基因组学；药物/CRISPR筛选与类器官依赖图谱）",
        "DOI: https://doi.org/10.1038/s41586-026-10830-y",
    ],
    "c9-rna-5": [
        "通讯：末位作者邮箱 nmurthy@berkeley.edu；单位 UC Berkeley Bioengineering / Innovative Genomics Institute",
        "DOI: https://doi.org/10.1038/s41565-026-02262-6",
        "实验室方向：未写入（未在本轮核到独立实验室页原文）",
    ],
    "c8-vac-3": [
        "通讯：末位作者邮箱 ellebedy@wustl.edu；单位 WashU Pathology & Immunology / Bursky Center",
        "DOI: https://doi.org/10.1038/s41590-026-02569-5",
    ],
    "c7-cell-2": [
        "通讯与职位：Nature Medicine 同行评审函（Corresponding Author: Professor Heng Mei；Professor & Director, Department of Hematology, Union Hospital, Tongji Medical College, HUST；Director, Hubei Clinical Medical Center of Cell Therapy）",
        "来源：https://www.nature.com/articles/s41591-026-04704-z 及 ESM 评审函 PDF",
        "DOI: https://doi.org/10.1038/s41591-026-04704-z",
        "实验室方向：未另引独立实验室页，仅用通讯函中的职务",
    ],
    "c5-am-2": [
        "通讯：末位作者邮箱 spasca@stanford.edu；单位 Stanford Psychiatry / Brain Organogenesis Program",
        "DOI: https://doi.org/10.1038/s41586-026-11032-2",
    ],
    "c1-org-1": [
        "通讯：末位作者邮箱 paola_arlotta@harvard.edu；单位 Harvard SCRB / Broad Stanley Center",
        "DOI: https://doi.org/10.1038/s41586-026-10877-x",
    ],
    "c2-ai-1": [
        "通讯：末位作者邮箱 andrew.bradbury@iqvia.com；第一作者亦有通讯邮箱；单位 Specifica, an IQVIA Business, Santa Fe, NM",
        "DOI: https://doi.org/10.1038/s41587-026-03238-6",
    ],
    "c2-ai-4": [
        "通讯：第一作者邮箱 alex@insilico.com（Insilico Medicine AI Limited / Shanghai / US）",
        "Gladyshev单位：BWH/HMS Division of Genetics 与 Broad（EPMC作者列表，无邮箱）",
        "DOI: https://doi.org/10.1038/s41587-026-03286-y",
    ],
    "c3-io-4": [
        "通讯：末位作者邮箱 hongbo.chi@stjude.org；单位 Department of Immunology, St. Jude Children's Research Hospital",
        "DOI: https://doi.org/10.1038/s41586-026-11059-5",
    ],
    "c5-am-5": [
        "通讯：第一作者邮箱 sacha@ohsu.edu；末位作者 haigwoon@ohsu.edu；单位 ONPRC / VGTI, OHSU",
        "DOI: https://doi.org/10.1038/s41564-026-02444-x",
    ],
    "c1-org-3": [
        "通讯：Jesse S. Boehm 邮箱 boehm@mit.edu（Broad / Koch Institute）；另有 Louis M. Staudt 等带邮箱",
        "DOI: https://doi.org/10.1038/s41586-026-10806-y",
    ],
    "c4-ai-6": [
        "通讯：末位作者邮箱 lenardo@calicolabs.com；单位 NIAID Laboratory of Immune System Biology / Clinical Genomics Program / Calico Life Sciences",
        "DOI: https://doi.org/10.1038/s41586-026-10749-4",
        "已去掉原au中“完整单位列表未显示”等页面备注",
    ],
    "c2-ai-5": [
        "通讯：Gao xjgao@stanford.edu；Hie brianhie@stanford.edu；Mille-Fragoso lsmille@stanford.edu",
        "单位：Stanford Chemical Engineering / Biophysics / ChEM-H / Bio-X / Arc Institute",
        "DOI: https://doi.org/10.1038/s41587-026-03187-0",
    ],
    "c6-ab-4": [
        "通讯/单位：原解读与catalog根据bioRxiv作者页：Ammar Arsiwala, Ginkgo Bioworks, Inc., MA",
        "Europe PMC核心记录未返回分条affiliation",
        "DOI: https://doi.org/10.64898/2026.06.15.732449",
    ],
    "c1-org-4": [
        "通讯：末位作者邮箱 bateup@berkeley.edu；单位 UC Berkeley MCB / Neuroscience",
        "DOI: https://doi.org/10.1038/s41586-026-11054-w",
    ],
    "c3-io-5": [
        "通讯：末位作者邮箱 daniela.quail@mcgill.ca；单位 Goodman Cancer Institute / Experimental Medicine / Physiology, McGill",
        "DOI: https://doi.org/10.1038/s41586-026-10750-x",
    ],
    "c8-vac-2": [
        "通讯：末位作者邮箱 schief@scripps.edu；单位 Scripps Immunology & Microbiology / IAVI NAC / Moderna",
        "DOI: https://doi.org/10.1038/s41586-026-10837-5",
    ],
    "c1-org-5": [
        "通讯：末位作者邮箱 maxime.mahe@inserm.fr；单位 Nantes Université Inserm TENS UMR1235 / CCHMC Pediatric Surgery / CuSTOM",
        "DOI: https://doi.org/10.1038/s41551-026-01688-6",
    ],
    "c2-ai-2": [
        "通讯：末位作者邮箱 nicholasf_polizzi@dfci.harvard.edu；单位 DFCI Cancer Biology / HMS BCMP",
        "DOI: https://doi.org/10.1038/s41586-026-10670-w",
    ],
    "c3-io-2": [
        "通讯：末位作者邮箱 aurelien.marabelle@gustaveroussy.fr；单位 Gustave Roussy DITEP / INSERM CIC 1428 / U1015 / Université Paris Saclay",
        "DOI: https://doi.org/10.1038/s41586-026-10341-w",
    ],
    "c6-ab-6": [
        "通讯：第一作者邮箱 lbyers@mdanderson.org；单位 MD Anderson Cancer Center",
        "Chandana单位：START Midwest, Grand Rapids, MI（EPMC末位作者，无邮箱）",
        "DOI: https://doi.org/10.1038/s41591-026-04452-0",
    ],
    "c6-ab-5": [
        "通讯/单位：原解读与catalog根据bioRxiv作者页：Xinyun (Nina) Cheng, Amazon Web Services, Applied AI Solutions, Life Sciences",
        "Europe PMC核心记录未返回分条affiliation",
        "DOI: https://doi.org/10.64898/2026.04.13.717816",
    ],
    "c8-vac-4": [
        "通讯：末位作者邮箱 ivan.zanoni@childrens.harvard.edu；单位 HMS / Boston Children's Hospital Immunology & Gastroenterology",
        "DOI: https://doi.org/10.1038/s41590-026-02517-3",
    ],
    "c4-ai-5": [
        "通讯：末位作者邮箱 im3@sanger.ac.uk；单位 Somatic Genomics Programme, Wellcome Sanger Institute, Hinxton",
        "DOI: https://doi.org/10.1038/s41586-026-10493-9",
    ],
}

QL_LOCS = {
    "c6-ab-9": [("5 mg/kg", "关键数据卡·干预/剂量"), ("5.5倍", "关键数据卡·主要终点结果 / 速览所据one_liner"), ("1.57", "关键数据卡·主要终点结果"), ("9.53", "关键数据卡·主要终点结果"), ("14只", "关键数据卡·样本量")],
    "c5-am-8": [("30,000", "关键数据卡·干预/剂量"), ("8–9周", "关键数据卡·随访"), ("CARD8", "one_liner / 关键数据卡·主要终点结果"), ("CASP1", "同上"), ("NLRP1", "同上")],
    "c9-rna-9": [("184 mg", "one_liner / 关键数据卡"), ("90.2%", "one_liner / 关键数据卡")],
    "c5-am-9": [("44、45、51或53", "one_liner"), ("4个", "one_liner"), ("8周龄", "one_liner")],
    "c4-ai-8": [("3例", "one_liner"), ("1×10^8", "one_liner"), ("24、19、19个月", "one_liner")],
    "c6-ab-7": [("156", "one_liner / 关键数据卡"), ("18.0 pM", "one_liner / 关键数据卡"), ("3.08 nM", "one_liner / 关键数据卡"), ("10条", "one_liner")],
    "c9-rna-7": [("6例", "one_liner"), ("240天", "one_liner"), ("210天", "one_liner"), ("69%", "one_liner"), ("56%", "one_liner")],
    "c6-ab-8": [("0.015 nM", "one_liner"), ("0.010 nM", "one_liner")],
    "c5-am-7": [("11个", "one_liner"), ("251", "one_liner"), ("715", "one_liner")],
    "c9-rna-8": [("2 mg/kg", "one_liner"), ("49%", "one_liner"), ("63倍", "one_liner")],
    "c4-ai-9": [("2例", "one_liner"), ("5×10^7", "one_liner"), ("229天", "one_liner"), ("93天", "one_liner")],
    "a-trap": [("56%", "one_liner / 关键数据卡"), ("2,667", "one_liner"), ("0.80", "one_liner"), ("0.74", "one_liner")],
    "c1-org-2": [("907", "one_liner"), ("256", "one_liner"), ("28%", "one_liner"), ("162", "one_liner"), ("85%", "one_liner")],
    "c9-rna-5": [("IL-6、IL-1β、MIP-2", "one_liner"), ("MC3-DLin、cKK-E12、SM-102", "one_liner")],
    "c8-vac-3": [("75名", "one_liner"), ("第4及17/26周", "one_liner"), ("13人中5人", "one_liner"), ("26周", "one_liner")],
    "c7-cell-2": [("4例", "one_liner"), ("15个月", "one_liner"), ("1例", "one_liner"), ("4.0个月", "one_liner"), ("3例", "one_liner")],
    "c5-am-2": [("4个", "one_liner"), ("91.9%", "one_liner"), ("4.7倍", "one_liner")],
    "c1-org-1": [("5年", "one_liner"), ("0.88–0.90", "one_liner"), ("7.25", "one_liner"), ("20.04个月", "one_liner")],
    "c2-ai-1": [("29", "one_liner"), ("511", "one_liner"), ("95 pM", "one_liner"), ("2,000倍", "one_liner"), ("9.8–13.8%", "one_liner")],
    "c2-ai-4": [("42名", "one_liner"), ("54个", "one_liner"), ("21个", "one_liner"), ("Q<0.10", "one_liner")],
    "c3-io-4": [("19,032", "关键数据卡·样本量"), ("n=5–8", "关键数据卡·样本量")],
    "c5-am-5": [("72小时", "one_liner / 关键数据卡"), ("27周", "one_liner"), ("8只", "one_liner / 关键数据卡"), ("6个月", "one_liner"), ("约4周龄", "关键数据卡")],
    "c1-org-3": [("25种", "one_liner"), ("665", "one_liner"), ("421", "one_liner"), ("97.8%", "one_liner"), ("95%", "one_liner")],
    "c4-ai-6": [("42%", "one_liner")],
    "c2-ai-5": [("4个", "one_liner"), ("43–101", "one_liner")],
    "c6-ab-4": [("160", "one_liner"), ("0.95", "one_liner"), ("0.94", "one_liner"), ("0.89", "one_liner")],
    "c1-org-4": [("50、120、220天", "one_liner")],
    "c3-io-5": [("12种", "one_liner"), ("4/6", "one_liner")],
    "c8-vac-2": [("44%", "one_liner")],
    "c1-org-5": [("4000", "one_liner"), ("100%", "one_liner"), ("第14天", "one_liner（原文d14）")],
    "c2-ai-2": [("80 pM", "one_liner")],
    "c3-io-2": [("61例", "one_liner"), ("0.3 mg/kg", "one_liner"), ("24.3%", "one_liner"), ("30%", "one_liner")],
    "c6-ab-6": [("124例", "one_liner"), ("52%", "one_liner"), ("65/124", "one_liner"), ("1.8 mg/kg", "one_liner")],
    "c6-ab-5": [("288,000", "one_liner"), ("116", "one_liner"), ("46个", "one_liner"), ("39.7%", "one_liner"), ("0.66 nM", "one_liner")],
    "c8-vac-4": [("第500天", "one_liner")],
    "c4-ai-5": [("135种", "one_liner")],
}


def main() -> None:
    missing = [k for k in QUICK if k not in INTRO]
    extra = [k for k in INTRO if k not in QUICK]
    if missing or extra:
        raise SystemExit(f"id mismatch ql/intro {missing=} {extra=}")

    bad = []
    for aid, text in QUICK.items():
        n = nchars(text)
        if not (100 <= n <= 200):
            bad.append(("quick_look", aid, n, text))
    for aid, text in INTRO.items():
        n = nchars(text)
        if not (80 <= n <= 200):
            bad.append(("author_intro", aid, n, text))
    if bad:
        for kind, aid, n, text in bad:
            print(f"BAD {kind} {aid} {n}: {text}")
        raise SystemExit(f"{len(bad)} texts out of range")

    cat_path = ROOT / "content" / "catalog.json"
    catalog = json.loads(cat_path.read_text(encoding="utf-8"))
    by = {a["id"]: a for a in catalog["articles"]}
    for aid, ql in QUICK.items():
        a = by[aid]
        a["quick_look"] = ql
        a["author_intro"] = INTRO[aid]
        if aid in AU_CLEAN:
            a["au"] = AU_CLEAN[aid]
        jpath = ROOT / "content" / "articles" / f"{aid}.json"
        data = json.loads(jpath.read_text(encoding="utf-8"))
        data["quick_look"] = ql
        data["author_intro"] = INTRO[aid]
        if "article" in data and isinstance(data["article"], dict):
            data["article"]["quick_look"] = ql
            data["article"]["author_intro"] = INTRO[aid]
        jpath.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    cat_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# 作者介绍与速览核对",
        "",
        "速览中每个数字均来自该篇已核对正文或关键数据卡，未引入新数字。",
        "作者介绍只写论文作者/单位元数据（Europe PMC / Crossref / 出版商页）及可核到的官方实验室页；实验室方向核不到则省略。",
        "通讯作者优先；第一作者至多文末一句。",
        "",
    ]
    for aid in QUICK:
        lines.append(f"## {aid}")
        lines.append("")
        lines.append(f"**速览**（{nchars(QUICK[aid])}字）：{QUICK[aid]}")
        lines.append("")
        lines.append(f"**作者介绍**（{nchars(INTRO[aid])}字）：{INTRO[aid]}")
        lines.append("")
        lines.append("作者介绍出处：")
        for s in SOURCES.get(aid, ["（见Europe PMC作者列表）"]):
            lines.append(f"- {s}")
        lines.append("")
        lines.append("速览数字位置：")
        for num, loc in QL_LOCS.get(aid, []):
            lines.append(f"- `{num}` → {loc}")
        lines.append("")
    (ROOT / "docs" / "author_check.md").write_text("\n".join(lines), encoding="utf-8")

    report = ["# 36篇速览与作者介绍", ""]
    for aid in QUICK:
        report.append(f"## {aid}")
        report.append(f"- quick_look ({nchars(QUICK[aid])}): {QUICK[aid]}")
        report.append(f"- author_intro ({nchars(INTRO[aid])}): {INTRO[aid]}")
        report.append("")
    (ROOT / "docs" / "rail_texts.md").write_text("\n".join(report), encoding="utf-8")
    print(f"wrote {len(QUICK)} articles")


if __name__ == "__main__":
    main()

"""
法条检索验证模块（独立板块）

功能：
1. 接受任意自然语言输入，自动提取法律名称和条文序号
2. 通过国家法律法规数据库 (flk.npc.gov.cn) 验证法律的时效性和有效性
3. 通过 Bing 搜索获取条文原文
4. 返回结构化的法条信息：法律名称、条文序号、条文原文、时效状态、官方链接

使用方式：
    from backend.law_search import LawVerifier

    verifier = LawVerifier()
    results = verifier.query("民法典关于合同违约金怎么规定的")
    for article in results:
        print(article.format())
"""

import re
import requests
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from html import unescape

from backend.law_search.flk_client import FLKApiClient


# ─── 数字转换工具 ─────────────────────────────────────────

_DIGITS = "零一二三四五六七八九"
_UNITS = ["", "十", "百", "千", "万"]


def _int_to_chinese(n: int) -> str:
    """将阿拉伯数字转为中文（1→一，143→一百四十三）"""
    if n == 0:
        return "零"
    result = ""
    s = str(n)
    length = len(s)
    for i, ch in enumerate(s):
        d = int(ch)
        unit = _UNITS[length - i - 1]
        if d == 0:
            if result and not result.endswith("零"):
                result += "零"
        else:
            result += _DIGITS[d] + unit
    result = result.rstrip("零")
    if result.startswith("一十"):
        result = result[1:]
    return result


def _normalize_article_num(article_num: str) -> Tuple[str, str]:
    """
    标准化条文序号，返回 (中文数字版, 阿拉伯数字版)
    "第143条" → ("第一百四十三条", "第143条")
    "第一百四十三条" → ("第一百四十三条", "第143条")
    """
    m = re.search(r"第([一二三四五六七八九十百千万零\d]+)条", article_num)
    if not m:
        return (article_num, article_num)

    num_str = m.group(1)
    if num_str.isdigit():
        n = int(num_str)
        chinese = _int_to_chinese(n)
        return (f"第{chinese}条", f"第{n}条")
    return (f"第{num_str}条", f"第{num_str}条")


# ─── 常见法律名称映射 ─────────────────────────────────────

LAW_ALIASES = {
    "民法典": "中华人民共和国民法典",
    "刑法": "中华人民共和国刑法",
    "宪法": "中华人民共和国宪法",
    "劳动法": "中华人民共和国劳动法",
    "劳动合同法": "中华人民共和国劳动合同法",
    "公司法": "中华人民共和国公司法",
    "消费者权益保护法": "中华人民共和国消费者权益保护法",
    "道路交通安全法": "中华人民共和国道路交通安全法",
    "治安管理处罚法": "中华人民共和国治安管理处罚法",
    "行政诉讼法": "中华人民共和国行政诉讼法",
    "民事诉讼法": "中华人民共和国民事诉讼法",
    "刑事诉讼法": "中华人民共和国刑事诉讼法",
    "婚姻法": "中华人民共和国民法典婚姻家庭编",
    "继承法": "中华人民共和国民法典继承编",
    "侵权责任法": "中华人民共和国民法典侵权责任编",
    "物权法": "中华人民共和国民法典物权编",
    "合同法": "中华人民共和国民法典合同编",
    "知识产权法": "中华人民共和国著作权法",
    "著作权法": "中华人民共和国著作权法",
    "专利法": "中华人民共和国专利法",
    "商标法": "中华人民共和国商标法",
    "环境保护法": "中华人民共和国环境保护法",
    "食品安全法": "中华人民共和国食品安全法",
    "药品管理法": "中华人民共和国药品管理法",
    "证券法": "中华人民共和国证券法",
    "保险法": "中华人民共和国保险法",
    "票据法": "中华人民共和国票据法",
    "海商法": "中华人民共和国海商法",
    "仲裁法": "中华人民共和国仲裁法",
    "律师法": "中华人民共和国律师法",
    "法官法": "中华人民共和国法官法",
    "检察官法": "中华人民共和国检察官法",
    "监狱法": "中华人民共和国监狱法",
    "义务教育法": "中华人民共和国义务教育法",
    "教育法": "中华人民共和国教育法",
    "传染病防治法": "中华人民共和国传染病防治法",
    "突发事件应对法": "中华人民共和国突发事件应对法",
    "反不正当竞争法": "中华人民共和国反不正当竞争法",
    "反垄断法": "中华人民共和国反垄断法",
    "预算法": "中华人民共和国预算法",
    "税收征收管理法": "中华人民共和国税收征收管理法",
    "个人所得税法": "中华人民共和国个人所得税法",
    "企业所得税法": "中华人民共和国企业所得税法",
    "城市房地产管理法": "中华人民共和国城市房地产管理法",
    "土地管理法": "中华人民共和国土地管理法",
    "水法": "中华人民共和国水法",
    "矿产资源法": "中华人民共和国矿产资源法",
    "森林法": "中华人民共和国森林法",
    "草原法": "中华人民共和国草原法",
    "渔业法": "中华人民共和国渔业法",
    "畜牧法": "中华人民共和国畜牧法",
    "农产品质量安全法": "中华人民共和国农产品质量安全法",
    "安全生产法": "中华人民共和国安全生产法",
    "消防法": "中华人民共和国消防法",
    "交通安全法": "中华人民共和国道路交通安全法",
    "民用航空法": "中华人民共和国民用航空法",
    "铁路法": "中华人民共和国铁路法",
    "公路法": "中华人民共和国公路法",
    "港口法": "中华人民共和国港口法",
    "海事诉讼特别程序法": "中华人民共和国海事诉讼特别程序法",
    "引渡法": "中华人民共和国引渡法",
    "国际刑事司法协助法": "中华人民共和国国际刑事司法协助法",
    "国家赔偿法": "中华人民共和国国家赔偿法",
    "行政许可法": "中华人民共和国行政许可法",
    "行政处罚法": "中华人民共和国行政处罚法",
    "行政强制法": "中华人民共和国行政强制法",
    "行政复议法": "中华人民共和国行政复议法",
    "监察法": "中华人民共和国监察法",
    "公务员法": "中华人民共和国公务员法",
    "人民警察法": "中华人民共和国人民警察法",
    "军事设施保护法": "中华人民共和国军事设施保护法",
    "兵役法": "中华人民共和国兵役法",
    "国防法": "中华人民共和国国防法",
    "香港特别行政区基本法": "中华人民共和国香港特别行政区基本法",
    "澳门特别行政区基本法": "中华人民共和国澳门特别行政区基本法",
    "出境入境管理法": "中华人民共和国出境入境管理法",
    "护照法": "中华人民共和国护照法",
    "居民身份证法": "中华人民共和国居民身份证法",
    "网络安全法": "中华人民共和国网络安全法",
    "数据安全法": "中华人民共和国数据安全法",
    "个人信息保护法": "中华人民共和国个人信息保护法",
    "电子商务法": "中华人民共和国电子商务法",
    "电子签名法": "中华人民共和国电子签名法",
    "广告法": "中华人民共和国广告法",
    "价格法": "中华人民共和国价格法",
    "计量法": "中华人民共和国计量法",
    "标准化法": "中华人民共和国标准化法",
    "产品质量法": "中华人民共和国产品质量法",
    "特种设备安全法": "中华人民共和国特种设备安全法",
    "清洁生产促进法": "中华人民共和国清洁生产促进法",
    "循环经济促进法": "中华人民共和国循环经济促进法",
    "节约能源法": "中华人民共和国节约能源法",
    "可再生能源法": "中华人民共和国可再生能源法",
    "海岛保护法": "中华人民共和国海岛保护法",
    "测绘法": "中华人民共和国测绘法",
    "气象法": "中华人民共和国气象法",
    "地震法": "中华人民共和国防震减灾法",
    "防洪法": "中华人民共和国防洪法",
    "防沙治沙法": "中华人民共和国防沙治沙法",
    "水土保持法": "中华人民共和国水土保持法",
    "野生动物保护法": "中华人民共和国野生动物保护法",
    "野生植物保护条例": "中华人民共和国野生植物保护条例",
    "湿地保护法": "中华人民共和国湿地保护法",
    "噪声污染防治法": "中华人民共和国噪声污染防治法",
    "大气污染防治法": "中华人民共和国大气污染防治法",
    "水污染防治法": "中华人民共和国水污染防治法",
    "固体废物污染环境防治法": "中华人民共和国固体废物污染环境防治法",
    "土壤污染防治法": "中华人民共和国土壤污染防治法",
    "放射性污染防治法": "中华人民共和国放射性污染防治法",
    "环境影响评价法": "中华人民共和国环境影响评价法",
    "海洋环境保护法": "中华人民共和国海洋环境保护法",
    "深海海底区域资源勘探开发法": "中华人民共和国深海海底区域资源勘探开发法",
    "生物安全法": "中华人民共和国生物安全法",
    "基本医疗卫生与健康促进法": "中华人民共和国基本医疗卫生与健康促进法",
    "红十字会法": "中华人民共和国红十字会法",
    "慈善法": "中华人民共和国慈善法",
    "公益事业捐赠法": "中华人民共和国公益事业捐赠法",
    "老年人权益保障法": "中华人民共和国老年人权益保障法",
    "未成年人保护法": "中华人民共和国未成年人保护法",
    "预防未成年人犯罪法": "中华人民共和国预防未成年人犯罪法",
    "妇女权益保障法": "中华人民共和国妇女权益保障法",
    "残疾人保障法": "中华人民共和国残疾人保障法",
    "归侨侨眷权益保护法": "中华人民共和国归侨侨眷权益保护法",
    "退役军人保障法": "中华人民共和国退役军人保障法",
    "军人地位和权益保障法": "中华人民共和国军人地位和权益保障法",
    "英雄烈士保护法": "中华人民共和国英雄烈士保护法",
    "国家勋章和国家荣誉称号法": "中华人民共和国国家勋章和国家荣誉称号法",
    "国歌法": "中华人民共和国国歌法",
    "国旗法": "中华人民共和国国旗法",
    "国徽法": "中华人民共和国国徽法",
    "领海及毗连区法": "中华人民共和国领海及毗连区法",
    "专属经济区和大陆架法": "中华人民共和国专属经济区和大陆架法",
    "外商投资法": "中华人民共和国外商投资法",
    "对外贸易法": "中华人民共和国对外贸易法",
    "进出口商品检验法": "中华人民共和国进出口商品检验法",
    "海关法": "中华人民共和国海关法",
    "外汇管理条例": "中华人民共和国外汇管理条例",
    "反间谍法": "中华人民共和国反间谍法",
    "反恐怖主义法": "中华人民共和国反恐怖主义法",
    "国家情报法": "中华人民共和国国家情报法",
    "密码法": "中华人民共和国密码法",
    "档案法": "中华人民共和国档案法",
    "统计法": "中华人民共和国统计法",
    "会计法": "中华人民共和国会计法",
    "注册会计师法": "中华人民共和国注册会计师法",
    "审计法": "中华人民共和国审计法",
    "中国人民银行法": "中华人民共和国中国人民银行法",
    "商业银行法": "中华人民共和国商业银行法",
    "银行业监督管理法": "中华人民共和国银行业监督管理法",
    "期货和衍生品法": "中华人民共和国期货和衍生品法",
    "信托法": "中华人民共和国信托法",
    "基金法": "中华人民共和国证券投资基金法",
    "政府采购法": "中华人民共和国政府采购法",
    "招标投标法": "中华人民共和国招标投标法",
    "建筑法": "中华人民共和国建筑法",
    "城乡规划法": "中华人民共和国城乡规划法",
    "人民防空法": "中华人民共和国人民防空法",
    "防震减灾法": "中华人民共和国防震减灾法",
    "科学技术进步法": "中华人民共和国科学技术进步法",
    "促进科技成果转化法": "中华人民共和国促进科技成果转化法",
    "科学技术普及法": "中华人民共和国科学技术普及法",
    "农业法": "中华人民共和国农业法",
    "农村土地承包法": "中华人民共和国农村土地承包法",
    "农民专业合作社法": "中华人民共和国农民专业合作社法",
    "种子法": "中华人民共和国种子法",
    "农业技术推广法": "中华人民共和国农业技术推广法",
    "农业机械化促进法": "中华人民共和国农业机械化促进法",
    "动物防疫法": "中华人民共和国动物防疫法",
    "进出境动植物检疫法": "中华人民共和国进出境动植物检疫法",
    "煤炭法": "中华人民共和国煤炭法",
    "电力法": "中华人民共和国电力法",
    "民用爆炸物品安全管理条例": "中华人民共和国民用爆炸物品安全管理条例",
    "危险化学品安全管理条例": "中华人民共和国危险化学品安全管理条例",
    "烟花爆竹安全管理条例": "中华人民共和国烟花爆竹安全管理条例",
    "煤矿安全监察条例": "中华人民共和国煤矿安全监察条例",
    "建设工程安全生产管理条例": "中华人民共和国建设工程安全生产管理条例",
    "安全生产许可证条例": "中华人民共和国安全生产许可证条例",
    "生产安全事故报告和调查处理条例": "中华人民共和国生产安全事故报告和调查处理条例",
    "工伤保险条例": "中华人民共和国工伤保险条例",
    "失业保险条例": "中华人民共和国失业保险条例",
    "社会保险法": "中华人民共和国社会保险法",
    "住房公积金管理条例": "中华人民共和国住房公积金管理条例",
    "劳动保障监察条例": "中华人民共和国劳动保障监察条例",
    "女职工劳动保护特别规定": "中华人民共和国女职工劳动保护特别规定",
    "禁止使用童工规定": "中华人民共和国禁止使用童工规定",
    "未成年工特殊保护规定": "中华人民共和国未成年工特殊保护规定",
    # 司法解释
    "合同编通则解释": "最高人民法院关于适用《中华人民共和国民法典》合同编通则若干问题的解释",
    "民法典时间效力": "最高人民法院关于适用《中华人民共和国民法典》时间效力的若干规定",
    "民法典担保解释": "最高人民法院关于适用《中华人民共和国民法典》有关担保制度的解释",
    "民法典婚姻家庭编解释": "最高人民法院关于适用《中华人民共和国民法典》婚姻家庭编的解释（一）",
    "民法典继承编解释": "最高人民法院关于适用《中华人民共和国民法典》继承编的解释（一）",
    "民法典物权编解释": "最高人民法院关于适用《中华人民共和国民法典》物权编的解释（一）",
    "民法典侵权责任编解释": "最高人民法院关于适用《中华人民共和国民法典》侵权责任编的解释（一）",
    "民间借贷规定": "最高人民法院关于审理民间借贷案件适用法律若干问题的规定",
    "劳动争议解释": "最高人民法院关于审理劳动争议案件适用法律问题的解释（一）",
    "交通事故损害赔偿": "最高人民法院关于审理道路交通事故损害赔偿案件适用法律若干问题的解释",
    "人身损害赔偿": "最高人民法院关于审理人身损害赔偿案件适用法律若干问题的解释",
    "精神损害赔偿": "最高人民法院关于确定民事侵权精神损害赔偿责任若干问题的解释",
    "公司法解释三": "最高人民法院关于适用《中华人民共和国公司法》若干问题的规定（三）",
    "公司法解释四": "最高人民法院关于适用《中华人民共和国公司法》若干问题的规定（四）",
    "盗窃罪解释": "最高人民法院、最高人民检察院关于办理盗窃刑事案件适用法律若干问题的解释",
    "电信诈骗解释": "最高人民法院、最高人民检察院关于办理电信网络诈骗等刑事案件适用法律若干问题的意见",
    "醉驾规定": "最高人民法院、最高人民检察院、公安部关于办理醉酒驾驶机动车刑事案件适用法律若干问题的意见",
    "非法经营解释": "最高人民法院关于审理非法经营刑事案件具体应用法律若干问题的解释",
    "侵犯公民个人信息": "最高人民法院、最高人民检察院关于办理侵犯公民个人信息刑事案件适用法律若干问题的解释",
    "知识产权侵权解释": "最高人民法院关于审理侵害知识产权民事案件适用惩罚性赔偿的解释",
    "专利纠纷规定": "最高人民法院关于审理专利纠纷案件适用法律问题的若干规定",
    "商标民事纠纷": "最高人民法院关于审理商标民事纠纷案件适用法律若干问题的解释",
    "著作权纠纷解释": "最高人民法院关于审理著作权民事纠纷案件适用法律若干问题的解释",
    "不正当竞争解释": "最高人民法院关于审理不正当竞争民事案件应用法律若干问题的解释",
    "环境公益诉讼": "最高人民法院关于审理环境民事公益诉讼案件适用法律若干问题的解释",
    "商品房买卖解释": "最高人民法院关于审理商品房买卖合同纠纷案件适用法律若干问题的解释",
    "建设工程解释": "最高人民法院关于审理建设工程施工合同纠纷案件适用法律问题的解释（一）",
    "房屋租赁解释": "最高人民法院关于审理城镇房屋租赁合同纠纷案件具体应用法律若干问题的解释",
    "物业服务纠纷": "最高人民法院关于审理物业服务纠纷案件具体应用法律若干问题的解释",
    "旅游纠纷规定": "最高人民法院关于审理旅游纠纷案件适用法律若干问题的规定",
    "医疗损害赔偿": "最高人民法院关于审理医疗损害责任纠纷案件适用法律若干问题的解释",
    "工伤保险行政": "最高人民法院关于审理工伤保险行政案件适用法律若干问题的规定",
    "执行异议复议": "最高人民法院关于人民法院办理执行异议和复议案件若干问题的规定",
    "民事诉讼证据规定": "最高人民法院关于民事诉讼证据的若干规定",
    "国家赔偿精神损害": "最高人民法院关于审理国家赔偿案件适用精神损害赔偿若干问题的意见",
}


# ─── 模糊匹配：常见口语/概念 → 相关法律 ───────────────────

FUZZY_KEYWORDS = {
    "偷东西": "刑法 盗窃罪",
    "偷窃": "刑法 盗窃罪",
    "被偷": "刑法 盗窃罪",
    "小偷": "刑法 盗窃罪",
    "盗窃": "刑法 盗窃罪",
    "抢劫": "刑法 抢劫罪",
    "被抢": "刑法 抢劫罪",
    "抢夺": "刑法 抢夺罪",
    "诈骗": "刑法 诈骗罪",
    "被骗": "刑法 诈骗罪",
    "骗钱": "刑法 诈骗罪",
    "杀人": "刑法 故意杀人罪",
    "故意伤害": "刑法 故意伤害罪",
    "打人": "刑法 故意伤害罪",
    "被打": "刑法 故意伤害罪 民法典 侵权责任编",
    "被人打": "刑法 故意伤害罪 民法典 侵权责任编",
    "被打了": "刑法 故意伤害罪 民法典 侵权责任编",
    "打架": "刑法 故意伤害罪",
    "贩毒": "刑法 贩卖毒品罪",
    "走私": "刑法 走私罪",
    "贪污": "刑法 贪污罪",
    "受贿": "刑法 受贿罪",
    "行贿": "刑法 行贿罪",
    "交通肇事": "刑法 交通肇事罪",
    "醉驾": "刑法 危险驾驶罪",
    "酒驾": "刑法 危险驾驶罪",
    "判刑": "刑法",
    "坐牢": "刑法",
    "判几年": "刑法",
    "有期徒刑": "刑法",
    "无期徒刑": "刑法",
    "死刑": "刑法",
    "缓刑": "刑法",
    "假释": "刑法",
    "减刑": "刑法",
    "自首": "刑法",
    "立功": "刑法",
    "正当防卫": "刑法 正当防卫",
    "紧急避险": "刑法 紧急避险",
    "犯罪": "刑法",
    "刑罚": "刑法",
    "刑事": "刑法",
    # 合同纠纷
    "合同违约": "民法典 合同编 违约责任",
    "违约金": "民法典 合同编 违约金",
    "合同纠纷": "民法典 合同编",
    "签合同": "民法典 合同编",
    "合同无效": "民法典 合同编 合同效力",
    "合同解除": "民法典 合同编 合同解除",
    "退定金": "民法典 合同编 定金",
    "定金": "民法典 合同编 定金",
    "押金": "民法典 合同编 押金",
    "欠钱不还": "民法典 合同编 借款合同",
    "借钱不还": "民法典 合同编 借款合同",
    "欠债": "民法典 合同编",
    "赖账": "民法典 合同编",
    "拖欠工资": "劳动合同法 拖欠工资",
    "拖欠货款": "民法典 合同编",
    "租房纠纷": "民法典 合同编 租赁合同",
    "房租": "民法典 合同编 租赁合同",
    "退租": "民法典 合同编 租赁合同",
    "装修纠纷": "民法典 合同编 承揽合同",
    "装修": "民法典 合同编 承揽合同",
    "买卖纠纷": "民法典 合同编 买卖合同",
    "退货": "消费者权益保护法",
    "退款": "消费者权益保护法",
    "霸王条款": "消费者权益保护法",
    "虚假宣传": "消费者权益保护法",
    "假货": "消费者权益保护法 产品质量法",
    # 婚姻家庭
    "离婚": "民法典 婚姻家庭编 离婚",
    "结婚": "民法典 婚姻家庭编 结婚",
    "彩礼": "民法典 婚姻家庭编 彩礼",
    "嫁妆": "民法典 婚姻家庭编",
    "出轨": "民法典 婚姻家庭编",
    "婚内出轨": "民法典 婚姻家庭编",
    "私生子": "民法典 婚姻家庭编 非婚生子女",
    "抚养权": "民法典 婚姻家庭编 抚养",
    "抚养费": "民法典 婚姻家庭编 抚养",
    "赡养费": "民法典 婚姻家庭编 赡养",
    "赡养老人": "民法典 婚姻家庭编 赡养",
    "遗产继承": "民法典 继承编",
    "继承遗产": "民法典 继承编",
    "遗嘱": "民法典 继承编 遗嘱",
    "房产继承": "民法典 继承编",
    "财产分割": "民法典 婚姻家庭编 夫妻财产",
    "夫妻共同财产": "民法典 婚姻家庭编 夫妻财产",
    "婚前财产": "民法典 婚姻家庭编 夫妻财产",
    "家暴": "民法典 婚姻家庭编 反家庭暴力法",
    "家庭暴力": "民法典 婚姻家庭编 反家庭暴力法",
    # 侵权赔偿
    "人身损害": "民法典 侵权责任编 人身损害赔偿",
    "伤残赔偿": "民法典 侵权责任编 人身损害赔偿",
    "死亡赔偿": "民法典 侵权责任编 人身损害赔偿",
    "精神损害": "民法典 侵权责任编 精神损害赔偿",
    "精神损失费": "民法典 侵权责任编 精神损害赔偿",
    "医疗事故": "民法典 侵权责任编 医疗损害责任",
    "医患纠纷": "民法典 侵权责任编 医疗损害责任",
    "医疗纠纷": "民法典 侵权责任编 医疗损害责任",
    "交通事故": "民法典 侵权责任编 机动车交通事故",
    "车祸": "民法典 侵权责任编 机动车交通事故",
    "被车撞": "民法典 侵权责任编 机动车交通事故",
    "工伤": "工伤保险条例 劳动合同法 工伤",
    "工伤认定": "工伤保险条例",
    "工伤赔偿": "工伤保险条例",
    "被狗咬": "民法典 侵权责任编 饲养动物损害责任",
    "宠物伤人": "民法典 侵权责任编 饲养动物损害责任",
    "高空抛物": "民法典 侵权责任编 高空抛物",
    "坠物伤人": "民法典 侵权责任编 高空抛物",
    "噪音扰民": "民法典 侵权责任编 噪声污染防治法",
    "邻里纠纷": "民法典 物权编 相邻关系",
    "相邻权": "民法典 物权编 相邻关系",
    "漏水": "民法典 物权编 相邻关系",
    "侵权": "民法典 侵权责任编",
    # 劳动权益
    "试用期": "劳动合同法 试用期",
    "加班费": "劳动合同法 加班 工资",
    "加班": "劳动合同法 加班",
    "辞退": "劳动合同法 解除劳动合同",
    "开除": "劳动合同法 解除劳动合同",
    "裁员": "劳动合同法 裁员",
    "被迫离职": "劳动合同法 解除劳动合同",
    "经济补偿": "劳动合同法 经济补偿",
    "赔偿金": "劳动合同法 赔偿金",
    "社保": "社会保险法 劳动合同法 社会保险",
    "五险一金": "社会保险法 住房公积金管理条例",
    "公积金": "住房公积金管理条例",
    "劳动仲裁": "劳动争议调解仲裁法",
    "劳动纠纷": "劳动争议调解仲裁法",
    "拖欠社保": "社会保险法",
    "童工": "禁止使用童工规定 劳动法",
    "未成年工": "未成年工特殊保护规定",
    "女职工": "女职工劳动保护特别规定 劳动合同法",
    "产假": "女职工劳动保护特别规定 劳动合同法",
    "哺乳期": "女职工劳动保护特别规定 劳动合同法",
    "怀孕被辞退": "劳动合同法 女职工劳动保护特别规定",
    "职业病": "职业病防治法 劳动合同法",
    "劳动合同": "劳动合同法",
    "签劳动合同": "劳动合同法",
    "没签合同": "劳动合同法 双倍工资",
    # 房产物业
    "买房": "民法典 物权编 不动产登记 商品房买卖",
    "卖房": "民法典 物权编 商品房买卖",
    "房产证": "民法典 物权编 不动产登记",
    "过户": "民法典 物权编 不动产登记",
    "产权": "民法典 物权编",
    "物业费": "民法典 物业服务合同 物业服务纠纷",
    "物业纠纷": "民法典 物业服务合同",
    "小区车位": "民法典 物权编 建筑物区分所有权",
    "业主维权": "民法典 物权编 建筑物区分所有权",
    "强拆": "土地管理法 行政诉讼法 国家赔偿法",
    "拆迁": "土地管理法 国有土地上房屋征收与补偿条例",
    "征地补偿": "土地管理法 土地管理法实施条例",
    # 交通事故
    "交通事故责任": "道路交通安全法 民法典 侵权责任编",
    "撞人": "道路交通安全法 民法典 侵权责任编",
    "酒后驾车": "道路交通安全法 刑法",
    "无证驾驶": "道路交通安全法",
    "闯红灯": "道路交通安全法",
    "超速": "道路交通安全法",
    "逃逸": "道路交通安全法 刑法 交通肇事罪",
    "肇事逃逸": "道路交通安全法 刑法 交通肇事罪",
    "保险理赔": "保险法 机动车交通事故责任强制保险条例",
    # 消费维权
    "消费者": "消费者权益保护法",
    "买到假货": "消费者权益保护法 产品质量法",
    "虚假广告": "消费者权益保护法 广告法",
    "强制消费": "消费者权益保护法",
    "预付卡": "消费者权益保护法",
    "办卡不退": "消费者权益保护法",
    "外卖问题": "食品安全法 消费者权益保护法",
    "食品安全": "食品安全法",
    "过期食品": "食品安全法",
    "产品质量": "产品质量法",
    # 知识产权
    "盗版": "著作权法",
    "抄袭": "著作权法",
    "侵权作品": "著作权法",
    "商标侵权": "商标法",
    "专利侵权": "专利法",
    "知识产权": "著作权法 专利法 商标法",
    "版权": "著作权法",
    # 网络相关
    "网络诈骗": "刑法 诈骗罪 电信诈骗解释",
    "电信诈骗": "刑法 电信诈骗解释",
    "个人信息泄露": "个人信息保护法 网络安全法 刑法",
    "隐私泄露": "个人信息保护法 民法典 人格权编",
    "网络暴力": "民法典 人格权编 网络安全法",
    "网络贷款": "民法典 合同编 借款合同",
    "网贷": "民法典 合同编 借款合同",
    "套路贷": "刑法 诈骗罪",
    "非法集资": "刑法 非法吸收公众存款罪",
    "传销": "刑法 组织、领导传销活动罪",
    "洗钱": "刑法 洗钱罪",
    "赌博": "刑法 赌博罪",
    "开设赌场": "刑法 开设赌场罪",
    "嫖娼": "治安管理处罚法",
    "打架斗殴": "治安管理处罚法 刑法",
    "寻衅滋事": "刑法 寻衅滋事罪 治安管理处罚法",
    "猥亵": "刑法 强制猥亵罪 治安管理处罚法",
    "强奸": "刑法 强奸罪",
    "拐卖": "刑法 拐卖妇女、儿童罪",
    # 其他常见
    "见义勇为": "民法典 总则编 见义勇为",
    "无因管理": "民法典 总则编 无因管理",
    "不当得利": "民法典 总则编 不当得利",
    "遗产": "民法典 继承编",
    "遗赠": "民法典 继承编",
    "收养": "民法典 婚姻家庭编 收养",
    "监护": "民法典 总则编 监护",
    "失踪": "民法典 总则编 宣告失踪",
    "死亡宣告": "民法典 总则编 宣告死亡",
    "诉讼时效": "民法典 总则编 诉讼时效",
    "举报": "宪法 检察官法",
    "上访": "信访条例",
    "行政复议": "行政复议法",
    "行政诉讼": "行政诉讼法",
    "民告官": "行政诉讼法 行政复议法",
    "国家赔偿": "国家赔偿法",
    "冤假错案": "国家赔偿法",
}


# ─── 数据结构 ───────────────────────────────────────────────

@dataclass
class LawInfo:
    """法律基本信息"""
    title: str              # 法律全称
    bbbs: str               # FLK 唯一 ID
    law_type: str           # 法律性质（法律/行政法规/司法解释等）
    authority: str          # 制定机关
    announce_date: str      # 公布日期
    effective_date: str     # 施行日期
    status: int             # 时效性：1=已废止, 2=已修改, 3=有效, 4=尚未生效
    official_url: str       # 官方详情页链接

    @property
    def is_valid(self) -> bool:
        return self.status == 3

    @property
    def status_text(self) -> str:
        return {1: "已废止", 2: "已修改", 3: "有效", 4: "尚未生效"}.get(self.status, "未知")

    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "bbbs": self.bbbs,
            "law_type": self.law_type,
            "authority": self.authority,
            "announce_date": self.announce_date,
            "effective_date": self.effective_date,
            "status": self.status,
            "status_text": self.status_text,
            "is_valid": self.is_valid,
            "official_url": self.official_url,
        }


@dataclass
class ArticleResult:
    """条文检索结果"""
    law_name: str           # 法律名称
    article_num: str        # 条文序号（如"第一百四十三条"）
    text: str               # 条文原文
    status: int             # 时效性：1=已废止, 2=已修改, 3=有效, 4=尚未生效
    source: str             # 文本来源
    official_url: str       # 官方详情页链接
    law_type: str = ""      # 法律性质
    authority: str = ""     # 制定机关
    announce_date: str = "" # 公布日期
    effective_date: str = ""# 施行日期

    @property
    def status_text(self) -> str:
        return {1: "已废止", 2: "已修改", 3: "有效", 4: "尚未生效"}.get(self.status, "未知")

    @property
    def is_valid(self) -> bool:
        return self.status == 3

    def to_dict(self) -> Dict:
        return {
            "law_name": self.law_name,
            "article_num": self.article_num,
            "text": self.text,
            "status": self.status,
            "status_text": self.status_text,
            "is_valid": self.is_valid,
            "source": self.source,
            "official_url": self.official_url,
            "law_type": self.law_type,
            "authority": self.authority,
            "announce_date": self.announce_date,
            "effective_date": self.effective_date,
        }

    def format(self) -> str:
        """格式化输出"""
        status_icon = "✅" if self.is_valid else "❌"
        lines = [
            f"【{self.law_name}】{self.article_num}  {status_icon} {self.status_text}",
            f"  性质：{self.law_type} | 机关：{self.authority}",
            f"  公布：{self.announce_date} | 施行：{self.effective_date}",
            f"  来源：{self.source}",
            f"  链接：{self.official_url}",
            "",
            f"  条文原文：",
            f"  {self.text}",
        ]
        return "\n".join(lines)


# ─── Bing 搜索获取条文文本 ──────────────────────────────────

class _BingTextFetcher:
    """通过 Bing 搜索获取条文原文"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })

    def fetch_article_text(self, law_name: str, article_num: str) -> List[Dict]:
        """
        通过 Bing 搜索获取条文文本

        Returns:
            [{"text": "条文原文", "source_url": "来源URL", "reliability": "high/medium/low"}]
        """
        short_name = law_name.replace("中华人民共和国", "")
        chinese_num, arabic_num = _normalize_article_num(article_num)

        queries = [
            f"{law_name} {chinese_num}",
            f"{short_name} {chinese_num} 全文",
            f"{law_name} {arabic_num}",
        ]

        all_results = []
        for query in queries:
            results = self._bing_search(query)
            for r in results:
                text = r["text"]
                if self._is_article_text(text, article_num):
                    r["reliability"] = self._assess_reliability(r.get("source_url", ""), text)
                    all_results.append(r)
            if any(r["reliability"] == "high" for r in all_results):
                break

        # 去重
        seen = set()
        unique = []
        for r in all_results:
            key = r["text"][:80]
            if key not in seen:
                seen.add(key)
                unique.append(r)

        # 按可靠性排序
        priority = {"high": 0, "medium": 1, "low": 2}
        unique.sort(key=lambda x: priority.get(x.get("reliability", "low"), 3))

        return unique

    def _bing_search(self, query: str) -> List[Dict]:
        """执行 Bing HTML 搜索"""
        try:
            resp = self.session.get(
                "https://cn.bing.com/search",
                params={"q": query, "mkt": "zh-CN"},
                timeout=15,
            )
            resp.raise_for_status()

            blocks = re.findall(
                r'<li class="b_algo"[^>]*>(.*?)</li>',
                resp.text,
                re.DOTALL,
            )

            results = []
            for block in blocks[:8]:
                h2_m = re.search(r"<h2[^>]*>(.*?)</h2>", block, re.DOTALL)
                if not h2_m:
                    continue

                a_m = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', h2_m.group(1), re.DOTALL)
                if not a_m:
                    continue

                url = a_m.group(1)
                title = unescape(re.sub(r"<[^>]+>", "", a_m.group(2))).strip()

                snippets = re.findall(r"<(?:p|span)[^>]*>(.*?)</(?:p|span)>", block, re.DOTALL)
                text_parts = []
                for s in snippets:
                    clean = unescape(re.sub(r"<[^>]+>", "", s)).strip()
                    if len(clean) > 20:
                        text_parts.append(clean)
                snippet = " ".join(text_parts) if text_parts else title

                results.append({"title": title, "url": url, "text": snippet})

            return results

        except Exception as e:
            print(f"Bing 搜索失败: {e}")
            return []

    def _is_article_text(self, text: str, article_num: str) -> bool:
        """判断文本是否是目标条文内容"""
        if len(text) < 30:
            return False

        chinese_num, arabic_num = _normalize_article_num(article_num)

        if chinese_num not in text and arabic_num not in text:
            return False

        if len(text) < 50:
            return False

        return True

    def _assess_reliability(self, url: str, text: str) -> str:
        """评估来源可靠性"""
        high_domains = ["gov.cn", "court.gov.cn", "npc.gov.cn", "flk.npc.gov.cn"]
        low_sources = ["百度知道", "百度经验", "知乎", "个人博客", "360doc", "MBA智库"]

        for domain in high_domains:
            if domain in url:
                return "high"

        for source in low_sources:
            if source in text:
                return "low"

        return "medium"


# ─── 主接口 ─────────────────────────────────────────────────

class LawVerifier:
    """
    法条检索验证模块

    通过国家法律法规数据库验证法律有效性，
    通过 Bing 搜索获取条文原文。
    支持任意自然语言输入。
    """

    def __init__(self):
        self.flk = FLKApiClient()
        self.fetcher = _BingTextFetcher()

    def _flk_search(self, keyword: str, page_size: int = 10, only_valid: bool = False) -> List[LawInfo]:
        """内部 FLK 搜索，返回 LawInfo 列表"""
        results, _ = self.flk.search_laws(
            keyword=keyword,
            page_size=page_size,
            only_valid=only_valid,
        )
        return [
            LawInfo(
                title=r.title,
                bbbs=r.bbbs,
                law_type=r.flxz,
                authority=r.zdjg_name,
                announce_date=r.gbrq,
                effective_date=r.sxrq,
                status=r.sxx,
                official_url=r.detail_url,
            )
            for r in results
        ]

    # ---------- 自然语言解析 ----------

    @staticmethod
    def _parse_input(text: str) -> List[Tuple[str, str]]:
        """
        从自然语言中提取 (法律名称, 条文序号) 对

        支持的输入格式：
        - "民法典第143条"
        - "偷东西判几年"
        - "离婚怎么分财产"

        Returns:
            [(法律名称, 条文序号), ...]  如果没有提取到条文号则条文序号为空字符串
        """
        results = []
        text = text.strip()

        # 模式1: 法律名 + 第X条（中文数字或阿拉伯数字）
        pattern1 = re.findall(
            r'([\u4e00-\u9fa5]{2,20}?(?:法|典|条例|规定|办法|决定|解释|标准))\s*第\s*([一二三四五六七八九十百千万零\d]+)\s*条',
            text
        )
        for law, num in pattern1:
            full_name = LAW_ALIASES.get(law, law)
            results.append((full_name, f"第{num}条", law))

        if results:
            return results

        # 模式2: 法律名 + "第X条"（更宽松）
        pattern2 = re.findall(
            r'([\u4e00-\u9fa5]{2,20}?)\s*第\s*([一二三四五六七八九十百千万零\d]+)\s*条',
            text
        )
        for law, num in pattern2:
            full_name = LAW_ALIASES.get(law, law)
            results.append((full_name, f"第{num}条", law))

        if results:
            return results

        # 模式3: 只有法律名，没有条文号
        for alias, full_name in LAW_ALIASES.items():
            if alias in text:
                results.append((full_name, "", alias))
                break

        # 模式4: 尝试从文本中提取法律关键词
        if not results:
            best_match = ""
            best_full = ""
            best_alias = ""
            for alias, full_name in LAW_ALIASES.items():
                if alias in text and len(alias) > len(best_match):
                    best_match = alias
                    best_full = full_name
                    best_alias = alias
            if best_full:
                results.append((best_full, "", best_alias))

        # 模式5: 模糊匹配（口语/概念 → 相关法律）
        if not results:
            fuzzy_matches = []
            for keyword, search_query in FUZZY_KEYWORDS.items():
                if keyword in text:
                    fuzzy_matches.append((keyword, search_query))
            fuzzy_matches.sort(key=lambda x: len(x[0]), reverse=True)

            if fuzzy_matches:
                _, search_query = fuzzy_matches[0]
                parts = search_query.split(" ", 1)
                law_keyword = parts[0]
                full_name = LAW_ALIASES.get(law_keyword, law_keyword)
                results.append((full_name, "", law_keyword))

        return results

    # ---------- 公开接口 ----------

    def query(self, text: str, num_results: int = 5) -> List[ArticleResult]:
        """
        自然语言查询：接受任意输入，返回相关法条

        Args:
            text: 自然语言输入，如 "民法典关于合同违约金怎么规定的"
            num_results: 返回结果数量

        Returns:
            ArticleResult 列表
        """
        parsed = self._parse_input(text)

        if not parsed:
            laws = self._flk_search(text, page_size=num_results, only_valid=True)
            if not laws:
                laws = self._flk_search(text, page_size=num_results)
            return [
                ArticleResult(
                    law_name=law.title,
                    article_num="[搜索结果]",
                    text=f"未识别到具体条文，请搜索: {text}",
                    status=law.status,
                    source="国家法律法规数据库",
                    official_url=law.official_url,
                    law_type=law.law_type,
                    authority=law.authority,
                    announce_date=law.announce_date,
                    effective_date=law.effective_date,
                )
                for law in laws[:num_results]
            ]

        results = []
        for item in parsed:
            if len(item) == 3:
                law_name, article_num, alias = item
            else:
                law_name, article_num = item
                alias = ""

            if article_num:
                article = self.verify(law_name, article_num)
                if article:
                    results.append(article)
            else:
                laws = self._flk_search(law_name, page_size=num_results, only_valid=True)
                if not laws:
                    laws = self._flk_search(law_name, page_size=num_results)

                if laws:
                    text_results = self.fetcher.fetch_article_text(
                        laws[0].title, text
                    )
                    if text_results:
                        for tr in text_results[:num_results]:
                            results.append(ArticleResult(
                                law_name=laws[0].title,
                                article_num="[相关条文]",
                                text=tr["text"],
                                status=laws[0].status,
                                source=f"国家法律法规数据库 + {tr.get('reliability', 'medium')} 可信度来源",
                                official_url=laws[0].official_url,
                                law_type=laws[0].law_type,
                                authority=laws[0].authority,
                                announce_date=laws[0].announce_date,
                                effective_date=laws[0].effective_date,
                            ))
                    else:
                        for law in laws[:num_results]:
                            results.append(ArticleResult(
                                law_name=law.title,
                                article_num="[相关条文]",
                                text="未找到具体条文，请访问官方链接查看",
                                status=law.status,
                                source="国家法律法规数据库",
                                official_url=law.official_url,
                                law_type=law.law_type,
                                authority=law.authority,
                                announce_date=law.announce_date,
                                effective_date=law.effective_date,
                            ))

        return results

    def search(self, keyword: str, num_results: int = 10, only_valid: bool = False) -> List[LawInfo]:
        """
        搜索法律法规

        Args:
            keyword: 搜索关键词
            num_results: 返回数量
            only_valid: 是否只返回现行有效的法律

        Returns:
            法律信息列表
        """
        return self._flk_search(keyword, num_results, only_valid)

    def verify(self, law_name: str, article_num: str) -> Optional[ArticleResult]:
        """
        验证并获取具体条文

        流程：
        1. 在 FLK 搜索法律，验证时效性
        2. 通过 Bing 搜索获取条文原文
        3. 返回结构化结果

        Args:
            law_name: 法律名称（如"中华人民共和国民法典"或"民法典"）
            article_num: 条文序号（如"第一百四十三条"或"第143条"）

        Returns:
            ArticleResult 或 None
        """
        # 1. 用短关键词搜索
        search_keyword = law_name.replace("中华人民共和国", "")
        laws = self._flk_search(search_keyword, page_size=10, only_valid=True)
        if not laws:
            laws = self._flk_search(search_keyword, page_size=10)

        if not laws:
            return None

        # 2. 选择最匹配的法律
        best_law = None
        for law in laws:
            if law.is_valid and law_name in law.title:
                best_law = law
                break
        if not best_law:
            for law in laws:
                if law_name in law.title:
                    best_law = law
                    break
        if not best_law:
            best_law = laws[0]

        # 3. 获取条文原文
        text_results = self.fetcher.fetch_article_text(best_law.title, article_num)

        if text_results:
            best_text = text_results[0]
            return ArticleResult(
                law_name=best_law.title,
                article_num=article_num,
                text=best_text["text"],
                status=best_law.status,
                source=f"国家法律法规数据库 + {best_text.get('reliability', 'medium')} 可信度来源",
                official_url=best_law.official_url,
                law_type=best_law.law_type,
                authority=best_law.authority,
                announce_date=best_law.announce_date,
                effective_date=best_law.effective_date,
            )

        return ArticleResult(
            law_name=best_law.title,
            article_num=article_num,
            text="[未找到条文原文，请访问官方链接查看]",
            status=best_law.status,
            source="国家法律法规数据库",
            official_url=best_law.official_url,
            law_type=best_law.law_type,
            authority=best_law.authority,
            announce_date=best_law.announce_date,
            effective_date=best_law.effective_date,
        )

    def get_law_toc(self, law_name: str) -> Optional[Dict]:
        """
        获取法律目录树

        Args:
            law_name: 法律名称

        Returns:
            目录树字典
        """
        laws = self._flk_search(law_name, page_size=3, only_valid=True)
        if not laws:
            return None
        return self.flk.get_law_toc(laws[0].bbbs)


# ─── 便捷函数 ───────────────────────────────────────────────

def verify_article(law_name: str, article_num: str) -> Optional[ArticleResult]:
    """便捷函数：验证并获取条文"""
    return LawVerifier().verify(law_name, article_num)


def search_laws(keyword: str, only_valid: bool = True) -> List[LawInfo]:
    """便捷函数：搜索法律"""
    return LawVerifier().search(keyword, only_valid=only_valid)
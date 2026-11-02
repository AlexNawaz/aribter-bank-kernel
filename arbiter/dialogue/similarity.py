"""
相似度匹配 / Similarity matching.

规则正则没有「接近」这个概念：要么命中，要么不命中。客户换一种说法就掉进
兜底。这一层补的就是这个缺口——把客户这句话和语料里的真人说法比一比，
最像哪一类就倾向于哪一类。

**为什么不用大模型。** 评审要能在没有网络、没有 API key 的机器上复现结果，
所以这里用字符 n-gram 的 TF-IDF 加余弦相似度，纯标准库，没有任何依赖，
两百多条语料全部比完在毫秒级。中文没有空格，字符 n-gram 正好绕开分词，
也顺带容忍错别字和省略。

**为什么这样是安全的。** 这一层照样在不可信侧。它归错类的代价是多问一句，
或者提议一个客户会拒签的信封——不会是一笔错误的转账。内核读的是模拟出来
的效果，不读这里的判断。也正因为如此，把这一层整个换成一个真正的大模型
（`DialogueModel` 协议留了接口）不会削弱任何安全性质。
"""

from __future__ import annotations

import math
import re
from collections import Counter

_PUNCT = re.compile(r"[\s，。！？、；：（）()\[\]「」『』“”‘’,.!?;:~·\-—_/\\|]+")
_LATIN = re.compile(r"[a-z]+")

# 客套话 / conversational scaffolding.
#
# 「能不能帮我瞧瞧这个月的开销」原先匹配到了「能不能帮我销户」——前缀把内容
# 盖过去了。这些词每个意图都会出现，本该被 IDF 压下去，但语料里它们只零星
# 出现在几条例句上，反而显得很有判别力。与其把语料写成每条都带客套话，不如
# 先把它们去掉：真正决定意图的是「开销」和「销户」，不是「能不能帮我」。
_FILLER = (
    "能不能", "可不可以", "可以帮我", "帮我", "麻烦你", "麻烦", "请问", "我想要",
    "我想", "我要", "我需要", "我得", "给我", "替我", "你好", "您好",
    "一下下", "一下", "一点", "有点", "现在", "那个", "这个", "就是",
    "could you", "can you", "please", "i want to", "i would like to",
    "i need to", "i'd like to", "help me",
)
_TAIL = ("好吗", "行吗", "可以吗", "怎么样", "咋样", "吧", "呢", "啊", "呀", "哦", "嘛")


_QUESTION = re.compile(
    r"(哪些|哪几|什么|啥|多少|几个|几张|几笔|谁|有没有|是不是|列表|列出|看看|瞧瞧|瞅瞅"
    r"|查查|查一下|提供什么|都有|情况|概览"
    r"|\bwhat\b|\bwhich\b|\bhow many\b|\bhow much\b|\blist\b|\bshow\b|\bwho\b)")

_ACTION = re.compile(
    r"(帮我|给我|把|要|想|请|赶紧|马上"
    r"|转|汇|划|打款|付|缴|交|买|申购|投|赎回|卖|取消|退订|关掉|停掉|冻结|解冻|锁|调整|改成"
    r"|\btransfer\b|\bpay\b|\bbuy\b|\bcancel\b|\bfreeze\b|\bstop\b|\bsend\b)")


_MONEY_IN = re.compile(r"(申购|买入|买了|买点|买只|买一|购入|投进|投入|配置|加仓"
                       r"|\bbuy\b|\binvest\b|\bpurchase\b)")
_MONEY_OUT = re.compile(r"(赎回|赎掉|赎了|卖出|卖掉|卖了|清掉|清仓|结掉|退出来|取回|拿回"
                        r"|倒回|转回|不做了|不要了|不想要了|不持有|换成现金"
                        r"|\bredeem\b|\bsell\b|\bcash out\b|\bliquidate\b)")


def _strip_filler(text: str) -> str:
    out = text
    for phrase in _FILLER:
        out = out.replace(phrase, "")
    for phrase in _TAIL:
        if out.endswith(phrase):
            out = out[: -len(phrase)]
    return out or text


def _features(text: str) -> Counter:
    """
    字符 n-gram + 英文词 / character n-grams plus English words.

    中文走 2-gram 和 3-gram：没有分词这一步，也就没有分词错误，而且
    「钱都花哪了」和「钱都去哪儿了」本来就共享一串字符。
    英文额外切出整词，否则 'transfer' 和 'transaction' 的字符重叠会偏高。
    """
    clean = _PUNCT.sub("", _strip_filler(text.lower()))
    feats: Counter = Counter()
    for n in (2, 3):
        for i in range(len(clean) - n + 1):
            feats[clean[i:i + n]] += 1
    for word in _LATIN.findall(text.lower()):
        if len(word) >= 2:
            feats["w:" + word] += 2        # 整词比字符片段更有判别力

    # 「问」还是「做」。
    #
    # 「有哪些理财产品」和「买点理财」字面重叠很高，但一个是查询一个是申购。
    # 字符 n-gram 分不开它们，因为区别不在某个词上，而在整句话的语气。给这两
    # 类各加一个显式特征，让同类的例句彼此靠拢。
    if _QUESTION.search(text):
        feats["#ask"] += 3
    if _ACTION.search(text):
        feats["#do"] += 3

    # 钱的方向。
    #
    # 「申购」和「赎回」是一对反义词，但它们共用几乎全部的词汇：理财、基金、
    # 产品、金额。字符重叠分不开方向相反的两件事，而把赎回判成申购，意味着
    # 提议的信封和客户要做的事正好相反。
    if _MONEY_IN.search(text):
        feats["#in"] += 4
    if _MONEY_OUT.search(text):
        feats["#out"] += 4
    return feats


class SimilarityMatcher:
    """对一组带标签的例句做最近邻 / Nearest neighbour over labelled examples."""

    def __init__(self, examples: list[tuple[str, object]]):
        self.labels = [label for _, label in examples]
        self.texts = [text for text, _ in examples]

        raw = [_features(t) for t in self.texts]
        df: Counter = Counter()
        for f in raw:
            df.update(f.keys())
        n = max(len(raw), 1)
        self.idf = {term: math.log((n + 1) / (count + 1)) + 1.0
                    for term, count in df.items()}
        self.vectors = [self._vector(f) for f in raw]

    def _vector(self, feats: Counter) -> dict[str, float]:
        vec = {t: (1.0 + math.log(c)) * self.idf.get(t, 1.0) for t, c in feats.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def rank(self, text: str, top: int = 3) -> list[tuple[object, float, str]]:
        """返回 (标签, 相似度, 最像的那句话)，按相似度降序，每个标签只留最高的一条。"""
        query = self._vector(_features(text))
        if not query:
            return []

        scored: dict[object, list[tuple[float, str]]] = {}
        for label, vec, example in zip(self.labels, self.vectors, self.texts):
            # 稀疏点积：只遍历较短的那一边
            small, large = (query, vec) if len(query) < len(vec) else (vec, query)
            score = sum(v * large.get(t, 0.0) for t, v in small.items())
            scored.setdefault(label, []).append((score, example))

        # 单看最像的一条容易被一句巧合的话带偏；把前三条的均值掺进来，
        # 要求一个意图整体上都和这句话沾边，而不是只有一条例句碰巧像。
        ranked = []
        for label, hits in scored.items():
            hits.sort(key=lambda h: -h[0])
            topk = hits[:3]
            mean = sum(h[0] for h in topk) / len(topk)
            ranked.append((label, 0.7 * hits[0][0] + 0.3 * mean, hits[0][1]))

        ranked.sort(key=lambda r: -r[1])
        return ranked[:top]

    def best(self, text: str) -> tuple[object, float, str] | None:
        ranked = self.rank(text, top=1)
        return ranked[0] if ranked else None

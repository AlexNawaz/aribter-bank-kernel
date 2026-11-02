"""
对话层测试 / Dialogue layer tests.

断言写的是**可观测的行为**——回复的类型、用了几轮、账户状态有没有真的变化——
而不是回复的措辞。断言措辞的测试会在每次改一个字时变红，而那种测试很快就会被
人习惯性忽略，然后真正的回归也跟着溜过去。
"""

from __future__ import annotations

import re

import pytest

from arbiter.bank.world import (
    ACC_CURRENT,
    INJECT_MEMO_TRANSFER,
    MANDATE_GYM,
    build_world,
    with_lookalike_payee,
    with_memo_injection,
)
from arbiter.dialogue.nlu import Act, RuleBasedNLU, extract_amount, parse_chinese_number
from arbiter.dialogue.session import Session
from arbiter.types import cny, yuan

from benchmarks.conversations import CONVERSATIONS, run_conversation


# ===========================================================================
# 中文数字 / Chinese numerals
# ===========================================================================

class TestNumerals:
    @pytest.mark.parametrize("text,expected", [
        ("一千", 1000), ("两千五百", 2500), ("五百", 500), ("十五", 15),
        ("三万", 30000), ("两万", 20000),
    ])
    def test_chinese_numbers_parse(self, text, expected):
        assert parse_chinese_number(text) == expected

    def test_elided_trailing_digit(self):
        """「一万五」口语里是 15000，不是 10005。读错数字就是转错钱。"""
        assert parse_chinese_number("一万五") == 15000

    @pytest.mark.parametrize("text,expected_yuan", [
        ("给房东转4500", "4500"), ("转一千块给我妈", "1000"), ("投2万", "20000"),
        ("转1.5万", "15000"), ("五百元", "500"), ("转两万五", "25000"), ("转三块", "3"),
    ])
    def test_amounts_are_extracted(self, text, expected_yuan):
        assert extract_amount(text) == yuan(expected_yuan)

    @pytest.mark.parametrize("text", [
        "帮我把这个月没用的订阅清一清",   # 「清一清」里的「一」
        "看一看账单", "试一试", "11月2日截止", "涨了30%", "十五号还款",
    ])
    def test_stray_numerals_are_not_amounts(self, text):
        """
        回归用例：动词重叠里的「一」一度被读成一块钱。

        这类错误不会抛异常，它只会安静地转错一笔金额——所以必须有测试钉住。
        """
        assert extract_amount(text) is None

    def test_account_numbers_are_not_amounts(self):
        assert extract_amount("转到6999000011112222") is None


# ===========================================================================
# 口语化意图 / Colloquial intent
# ===========================================================================

class TestUnderstanding:
    @pytest.mark.parametrize("text,act", [
        ("给房东转4500", Act.TRANSFER),
        ("这个月钱都花哪了", Act.ANALYSE_BILLS),
        ("帮我把这个月没用的订阅清一清", Act.CANCEL_MANDATE),
        ("把健身房的退了", Act.CANCEL_MANDATE),
        ("看看有哪些自动扣费", Act.LIST_MANDATES),
        ("想买点稳健的理财", Act.INVEST),
        ("我的卡好像被盗刷了", Act.CARD_FREEZE),
        ("今天天气怎么样", Act.OUT_OF_SCOPE),
        ("帮我把预留手机号改一下", Act.CREDENTIAL),
        ("你能做什么", Act.HELP),
    ])
    def test_colloquial_phrasings_land_on_the_right_act(self, text, act):
        assert RuleBasedNLU().understand(text).act is act

    def test_distress_maps_to_freezing(self):
        """客户说「被盗刷了」时并没有说「请冻结」，但他要的就是这个。"""
        assert RuleBasedNLU().understand("我的卡好像被盗刷了").act is Act.CARD_FREEZE

    def test_a_pasted_account_number_becomes_the_payee(self):
        slots = RuleBasedNLU().understand("把86420转到6999000011112222").slots
        assert slots["payee_account"] == "6999000011112222"
        assert slots["amount"] == yuan("86420")

    def test_missing_slots_are_reported_together(self):
        """一次问清，而不是一轮问一个——分开问要多耗一轮，而且烦人。"""
        u = RuleBasedNLU().understand("我要转账")
        assert set(u.missing) == {"amount", "payee"}


# ===========================================================================
# 多轮 / Multi-turn
# ===========================================================================

class TestMultiTurn:
    def test_an_answer_to_a_question_is_not_a_new_intent(self):
        """
        回归用例：「健身房那个」是对「取消哪一项」的回答，是一个光秃秃的槽位值，
        没有任何动词。按完整意图理解必然失败——这是多轮对话里最容易漏掉的输入。
        """
        s = Session()
        s.say("帮我把这个月没用的订阅清一清")
        reply = s.say("健身房那个")
        assert reply.kind == "envelope"
        assert "星力健身" in reply.text

    def test_slots_accumulate_across_turns(self):
        s = Session()
        assert s.say("我要转账").kind == "ask"
        assert s.say("给房东转4500").kind == "envelope"

    def test_an_unparseable_answer_repeats_the_question(self):
        s = Session()
        s.say("我要转账")
        reply = s.say("嗯嗯")
        assert reply.kind == "ask"
        assert "转给谁" in reply.text

    def test_cancelling_stops_everything(self):
        s = Session()
        before = s.core.state.accounts[ACC_CURRENT].balance
        s.say("给房东转4500")
        s.say("算了")
        assert s.core.state.accounts[ACC_CURRENT].balance == before

    def test_confirmation_with_nothing_pending_is_not_a_task(self):
        assert Session().say("确认").kind == "ask"


# ===========================================================================
# 安全性质在对话里同样成立 / The guarantees survive the conversation
# ===========================================================================

class TestDialogueSecurity:
    def test_a_read_is_never_blocked_by_an_injected_memo(self):
        """
        回归用例，也是我们在自己系统里发现的问题之一。

        注入内容一度会把每一次操作顶到 T2 并因此超出信封上限被拒——攻击者
        花 0.01 元就能让受害者连自己的账单都看不了。只读操作不可能被注入
        内容伤害，必须照常完成。
        """
        s = Session(core=with_memo_injection(build_world(), INJECT_MEMO_TRANSFER))
        reply = s.say("这个月钱都花哪了")
        assert reply.kind == "result"
        assert "支出" in reply.text

    def test_an_injected_memo_is_reported_to_the_customer(self):
        s = Session(core=with_memo_injection(build_world(), INJECT_MEMO_TRANSFER))
        assert "诈骗" in s.say("这个月钱都花哪了").text

    def test_value_movement_escalates_while_an_injection_is_present(self):
        s = Session(core=with_memo_injection(build_world(), INJECT_MEMO_TRANSFER))
        s.say("给房东转4500")
        assert s.say("确认").kind == "escalation"

    def test_paying_the_genuine_payee_is_not_blocked_by_a_poisoned_book(self):
        """
        客户的付款没有问题，有问题的是他的收款人名单。拦住这笔转账只会让他
        改用网银，而那边没有任何保护。
        """
        s = Session(core=with_lookalike_payee(build_world()))
        s.say("给房东转4500")
        reply = s.say("确认")
        assert reply.kind == "result"
        assert "收款人名单里有两个账号" in reply.text

    def test_the_poisoning_warning_shows_full_account_numbers(self):
        """掩码写的提醒等于把攻击赖以生效的那层遮挡又盖了回去。"""
        s = Session(core=with_lookalike_payee(build_world()))
        s.say("给房东转4500")
        text = s.say("确认").text
        assert "6228480402564890018" in text and "6228777777777770018" in text

    def test_a_product_above_the_customer_grade_is_refused_up_front(self):
        """
        超出评级的申购在第一轮就拒绝——而不是先生成一份信封、等客户签了字再由内核
        拒掉。让客户去读一份注定被拒的授权，是在消耗他对确认页的注意力。
        """
        s = Session()
        before = {k: h.amount for k, h in s.core.state.holdings.items()}
        reply = s.say("买点高风险的股票基金，投2万")
        assert reply.kind == "refusal"
        assert reply.envelope is None
        assert {k: h.amount for k, h in s.core.state.holdings.items()} == before

    def test_the_risk_request_is_not_silently_downgraded(self):
        """
        静默替换比拒绝更糟：客户以为自己买到了想要的东西。

        说「买高风险的」必须得到一个解释；评级内的替代是**问**出来的，接受之后看到的
        是一份写着替代产品名字的信封，仍然要签——而不是一只悄悄换成的债券基金。
        """
        s = Session()
        reply = s.say("买点高风险的股票基金，投2万")
        assert reply.kind == "refusal" and "风险" in reply.text
        follow = s.say("好")
        assert follow.kind == "envelope" and "安裕纯债一年" in follow.text
        assert "F000455" not in s.core.state.holdings

    def test_credential_changes_are_refused_with_a_reason(self):
        reply = Session().say("帮我把预留手机号改一下")
        assert reply.kind == "info"
        assert "不能代办" in reply.text

    def test_out_of_scope_says_what_it_can_do(self):
        reply = Session().say("帮我订张机票")
        assert reply.kind == "info"
        assert "转账" in reply.text

    def test_a_refusal_always_carries_a_next_step(self):
        """
        只会说「不行」的助手，客户会绕开它去用网银——而被绕开的控制
        保护不了任何人。
        """
        s = Session()
        reply = s.say("买点高风险的股票基金，投2万")
        assert len(reply.text.strip().splitlines()) >= 2
        assert s.offer is not None          # 出路是一个可以直接接受的建议


# ===========================================================================
# 对话基准 / Conversation benchmark
# ===========================================================================

class TestConversationSuite:
    @pytest.mark.parametrize("case", CONVERSATIONS, ids=[c.id for c in CONVERSATIONS])
    def test_conversation_behaves_as_expected(self, case):
        result = run_conversation(case)
        assert result.kind_ok, (
            f"{case.id}（{case.title_zh}）：预期最后一轮为 {case.expect_kind}，"
            f"实际为 {result.final_kind}"
        )
        assert result.execution_ok, f"{case.id}：账户变化与预期不符"
        assert result.efficiency_ok, (
            f"{case.id}：用了 {result.turns_used} 轮，超出上限 {case.max_turns}")

    def test_every_scored_dimension_is_covered(self):
        required = {"口语化", "追问能力", "错误恢复", "超范围兜底", "轮次效率"}
        assert required <= {c.dimension for c in CONVERSATIONS}


# ---------------------------------------------------------------------------
# 知识边界 / Knowledge boundary
# ---------------------------------------------------------------------------

class TestKnowledgeBoundary:
    """没有数据时说没有，有数据时从核心系统读出来。"""

    def test_interest_rate_is_refused_not_invented(self):
        s = Session()
        reply = s.say("有新的存款利率吗")
        assert reply.kind == "info"
        # 关键断言：回复里不能出现一个百分数。编一个利率比不回答更糟。
        assert not re.search(r"\d+(\.\d+)?\s*%", reply.text)
        assert "利率" in reply.text

    def test_rate_question_is_not_swallowed_by_transfer(self):
        """「利率」里没有动词，但转账那条规则很宽，顺序必须保证它先命中。"""
        for text in ["利率给我说说", "现在的汇率是多少", "我有多少积分"]:
            assert RuleBasedNLU().understand(text).act is Act.NO_DATA

    def test_catalogue_question_is_not_a_purchase(self):
        """问「有哪些」不该被反问「打算投多少」。"""
        u = RuleBasedNLU().understand("有哪些理财产品")
        assert u.act is Act.LIST_PRODUCTS
        assert u.missing == []

    def test_product_list_never_repeats_the_marketing_blurb(self):
        """宣传文案是不可信内容；隔离之后就不该再被复述出来。"""
        s = Session()
        text = s.say("有哪些理财产品").text
        assert "风险豁免" not in text
        assert "无需二次确认" not in text
        # 等级取自核心系统的记录，所以 R5 产品必须被标成超出客户评级
        assert "量化对冲衍生" in text and "高于您的评级" in text

    def test_buying_still_works_after_the_catalogue_split(self):
        s = Session()
        s.say("想买点稳健的理财，投2万")
        assert s.pending_act is Act.INVEST

    def test_lookup_does_not_discard_a_half_filled_task(self):
        s = Session()
        s.say("我要转账")
        reply = s.say("我的收款人有哪些")
        assert s.pending_act is Act.TRANSFER
        assert reply.kind == "ask"
        assert "王建国" in reply.text

    def test_payee_list_is_masked(self):
        s = Session()
        text = s.say("我的收款人有哪些").text
        assert "6228480402564890018" not in text
        assert "****" in text

    def test_unknown_names_the_boundary_instead_of_three_examples(self):
        s = Session()
        text = s.say("咕噜咕噜 xyzzy").text
        for scenario in ("转账", "账单", "理财", "卡片", "自动扣费"):
            assert scenario in text


class TestEnglishLayer:
    """英文输入只在中文规则落空之后才参与，所以它对中文输入完全透明。"""

    def test_english_transfer_is_understood_end_to_end(self):
        s = Session()
        s.say("transfer 4500 to the landlord")
        reply = s.say("confirm")
        assert reply.kind == "result"
        assert s.core.balance_of(ACC_CURRENT) == yuan("86420.00") - yuan("4500.00")

    def test_english_turn_gets_an_english_reply(self):
        s = Session()
        assert "I don't have that figure" in s.say("what's the deposit interest rate").text

    def test_language_follows_the_customer(self):
        s = Session()
        s.say("what can you do")
        assert s.lang == "en"
        s.say("有哪些自动扣费")
        assert s.lang == "zh"

    def test_a_bare_ok_does_not_switch_language(self):
        """「ok」太短，不足以说明客户换了语言。"""
        s = Session()
        s.say("有哪些理财产品")
        s.say("ok")
        assert s.lang == "zh"

    def test_english_layer_does_not_perturb_chinese(self):
        nlu = RuleBasedNLU()
        pairs = [("给房东转4500", Act.TRANSFER), ("这个月花了多少", Act.ANALYSE_BILLS),
                 ("把健身房的自动扣费退了", Act.CANCEL_MANDATE),
                 ("我的卡好像被盗刷了", Act.CARD_FREEZE)]
        for text, act in pairs:
            u = nlu.understand(text)
            assert u.act is act and u.lang == "zh"

    def test_english_card_fraud_reaches_the_freeze_intent(self):
        assert RuleBasedNLU().understand("my card was stolen").act is Act.CARD_FREEZE

    def test_english_out_of_scope_still_lands_in_the_fallback(self):
        s = Session()
        reply = s.say("what's the weather tomorrow")
        assert reply.kind == "info"
        assert "outside what I do" in reply.text


class TestParaphraseAndEscape:
    """同一件事换个说法要能问出来；问不出来的时候要能脱身。"""

    @pytest.mark.parametrize("text", [
        "我想查询账户明细", "我想问这个月钱都花在哪里", "这个月钱都花哪了",
        "交易明细", "钱花到哪里去了", "我的钱用在哪了",
        "本月收支明细", "这个月花销怎么样",
    ])
    def test_statement_question_survives_rephrasing(self, text):
        assert RuleBasedNLU().understand(text).act is Act.ANALYSE_BILLS

    def test_rephrasing_does_not_steal_from_other_intents(self):
        nlu = RuleBasedNLU()
        pairs = [("给房东转4500", Act.TRANSFER), ("转一千五给我妈", Act.TRANSFER),
                 ("把健身房的自动扣费退了", Act.CANCEL_MANDATE),
                 ("我的卡好像被盗刷了", Act.CARD_FREEZE),
                 ("想买点稳健的理财，投2万", Act.INVEST)]
        for text, act in pairs:
            assert nlu.understand(text).act is act

    def test_changing_the_subject_mid_slot_is_not_a_failed_answer(self):
        """
        实机回归：客户在「要取消哪一项？」中途改问账单，收到的却是同一个
        关于取消订阅的追问。
        """
        s = Session()
        s.say("帮我把没用的订阅清一清")
        reply = s.say("我想查询账户明细")
        assert reply.kind == "result"
        assert "本期支出合计" in reply.text

    def test_one_miss_still_repeats_the_question(self):
        """问一次没听懂就再问一次是对的——不要退回通用帮助。"""
        s = Session()
        s.say("帮我把没用的订阅清一清")
        reply = s.say("咕噜咕噜")
        assert reply.kind == "ask"
        assert "要取消哪一项" in reply.text

    def test_two_misses_release_the_customer(self):
        """问第二次还不懂，继续问就是把客户关在里面。"""
        s = Session()
        s.say("帮我把没用的订阅清一清")
        s.say("咕噜咕噜")
        reply = s.say("哇啦哇啦")
        assert s.pending_act is None
        assert "要取消哪一项" not in reply.text
        for scenario in ("转账", "账单", "理财", "卡片"):
            assert scenario in reply.text

    def test_answering_correctly_clears_the_miss_counter(self):
        """一次没听懂不该让后面的对话更容易被放弃。"""
        s = Session()
        s.say("帮我把没用的订阅清一清")
        s.say("咕噜咕噜")
        s.say("健身房那个")
        assert s._reask_count == 0


class TestNamedReferences:
    """界面显示得出的名字，解析器必须听得懂。"""

    def test_card_id_from_the_options_is_understood(self):
        """
        实机回归：界面问「是哪张卡？」并列出 CARD-5521，客户照着念了一遍，
        系统回「这句话我没太听明白」。系统自己印在屏幕上的东西认不出来，
        是自相矛盾。
        """
        s = Session()
        s.say("冻结我的卡")
        reply = s.say("CARD-5521")
        assert reply.kind == "envelope"
        assert "CARD-5521" in reply.text

    @pytest.mark.parametrize("text,expect", [
        ("CARD-8847", "CARD-8847"), ("5521", "CARD-5521"),
    ])
    def test_a_bare_card_id_is_answered_not_rejected(self, text, expect):
        """任务被放下之后，客户还在照着选项回答。这不是听不懂，是没上下文。"""
        s = Session()
        reply = s.say(text)
        assert expect in reply.text
        assert "没太听明白" not in reply.text

    def test_a_bare_merchant_name_offers_the_cancellation(self):
        s = Session()
        reply = s.say("星力健身")
        assert "自动扣费" in reply.text and "取消" in reply.text

    def test_a_bare_product_code_states_its_grade(self):
        s = Session()
        reply = s.say("F009977")
        assert "量化对冲衍生" in reply.text
        assert "高于您的评级" in reply.text

    def test_a_bare_payee_name_offers_the_transfer(self):
        s = Session()
        assert "常用收款人" in s.say("王建国").text

    def test_naming_a_product_with_a_verb_is_a_purchase(self):
        """规则按「动词 + 类别词」写成；名字替掉类别词时也要接得上。"""
        u = RuleBasedNLU(build_world()).understand("买 F000455 投1万")
        assert u.act is Act.INVEST

    def test_an_identifier_alone_does_not_switch_language(self):
        """CARD-8847 是拉丁字母，但它是卡号，不是英文。"""
        s = Session()
        s.say("有哪些自动扣费")
        s.say("CARD-8847")
        assert s.lang == "zh"


class TestIdentifiersAreNotAmounts:
    """标识符里有数字，但它们不是钱。"""

    @pytest.mark.parametrize("text,expected_yuan", [
        ("买 F000455 投1万", "10000.00"),
        ("买 F009977 投2万", "20000.00"),
        ("CARD-5521 额度调到1万", "10000.00"),
        ("把86420转到6228777777777770018", "86420.00"),
    ])
    def test_amount_comes_from_the_amount_not_the_identifier(self, text, expected_yuan):
        """
        实机回归：「买 F000455 投1万」编出了一个 ¥455.00 的信封——产品代码
        里的 455 被当成了金额。客户说一万，信封上写四百五十五。

        这一条本来会被信封挡住（客户看到 ¥455.00 会拒签），但那是最后一道
        防线在替解析器收拾残局。金额不该依赖客户逐字核对才不出错。
        """
        u = RuleBasedNLU(build_world()).understand(text)
        assert u.slots.get("amount") == yuan(expected_yuan)

    def test_ordinary_amounts_still_parse(self):
        nlu = RuleBasedNLU(build_world())
        assert nlu.understand("给房东转4500").slots["amount"] == yuan("4500.00")
        assert nlu.understand("转一千五给我妈").slots["amount"] == yuan("1500.00")


class TestEmptyKeysResolveToNothing:
    def test_an_unset_mandate_slot_does_not_match_the_first_mandate(self):
        """
        子串匹配下，空串是每一个商户名的子串。客户没点名，系统却挑了一项——
        这种静默替换比问一句更糟。
        """
        from arbiter.dialogue.session import _resolve_mandate
        core = build_world()
        assert _resolve_mandate(core, "") is None
        assert _resolve_mandate(core, "gym") is not None


class TestEveryQuestionIsAnswerable:
    """
    智能体问出口的每一个问题，都要能接住它的答案。

    这一组的由来：上一轮加了好几处反问——「要帮您取消吗？」「您想转多少？」
    「需要我帮您看看哪些可以取消吗？」——但都只测了第一轮。实际上它们问完
    没留下任何状态，客户照着回答，收到的是「目前没有待确认的操作」。
    自己提的建议自己不认，比不提更糟。
    """

    def test_the_bills_offer_can_be_accepted(self):
        s = Session()
        assert "可以取消吗" in s.say("这个月钱都花哪了").text
        reply = s.say("好")
        assert reply.kind == "result"
        assert "自动扣费" in reply.text

    def test_the_bills_offer_can_be_declined(self):
        s = Session()
        s.say("这个月钱都花哪了")
        reply = s.say("不用了")
        assert reply.kind == "info"
        assert s.offer is None

    @pytest.mark.parametrize("text", ["不用了", "不要了", "先不用", "暂时不用", "不需要", "算了"])
    def test_colloquial_refusals_are_understood(self, text):
        assert RuleBasedNLU(build_world()).understand(text).act is Act.DENY

    def test_a_refusal_word_inside_a_real_request_is_not_a_refusal(self):
        u = RuleBasedNLU(build_world()).understand("不用管它直接转4500给房东")
        assert u.act is Act.TRANSFER

    def test_the_mandate_offer_can_be_accepted(self):
        s = Session()
        assert "取消吗" in s.say("星力健身").text
        assert s.say("好").kind == "envelope"

    def test_a_card_reference_does_not_ask_which_card_again(self):
        s = Session()
        s.say("CARD-5521")
        reply = s.say("冻结")
        assert reply.kind == "envelope"
        assert "CARD-5521" in reply.text
        assert "是哪张卡" not in reply.text

    def test_a_payee_reference_accepts_a_bare_amount(self):
        s = Session()
        s.say("王建国")
        reply = s.say("4500")
        assert reply.kind == "envelope"
        assert "王建国" in reply.text

    def test_a_product_reference_accepts_a_bare_amount(self):
        s = Session()
        s.say("F000455")
        reply = s.say("1万")
        assert reply.kind == "envelope"
        assert "F000455" in reply.text


class TestNoSilentSubstitution:
    """超出评级时给替代方案，但必须是问出来的，不能是换掉的。"""

    def test_an_over_grade_product_offers_an_alternative(self):
        s = Session()
        reply = s.say("F009977")
        assert "我不能为您购买" in reply.text
        assert "要这样吗" in reply.text

    def test_the_alternative_is_named_in_the_envelope(self):
        """
        客户要买 F009977，最后买到的是 F000455。这件事必须写在他签的那张纸上，
        否则就是静默替换——客户会以为自己买到了想要的东西。
        """
        s = Session()
        s.say("F009977")
        reply = s.say("2万")
        assert reply.kind == "envelope"
        assert "安裕纯债一年" in reply.text
        assert "F009977" not in reply.text

    def test_declining_the_alternative_buys_nothing(self):
        s = Session()
        s.say("F009977")
        s.say("不用了")
        assert s.pending_act is None and s.offer is None
        assert not any(h.amount for code, h in s.core.state.holdings.items()
                       if code == "F000455")


class TestAccountOverview:
    @pytest.mark.parametrize("text", ["我的账户", "my account", "账户情况"])
    def test_account_phrasings_reach_the_balance_answer(self, text):
        s = Session()
        reply = s.say(text)
        assert reply.kind == "result"
        assert cny(yuan("86420.00")) in reply.text

    def test_english_read_answers_come_back_in_english(self):
        s = Session()
        assert "Your balances" in s.say("my account").text
        s2 = Session()
        assert "Spending this period" in s2.say("where did my money go this month").text


class TestParaphraseGeneralisation:
    """
    规则正则没有「接近」这个概念：要么命中，要么不命中。相似度那一层补的
    就是这个缺口——客户换一种说法，仍然找得回意图。
    """

    def test_the_similarity_layer_never_costs_accuracy(self):
        """
        组合式解析器（东西 + 动作 + 否定 + 问句/祈使）上线之后，规则本身就覆盖了盲测
        三的九成以上——相似度层不再是「把覆盖率翻倍」的那一层，而是兜底。兜底的要求
        只有一条：只能补，不能坏。
        """
        import arbiter.dialogue.nlu as nlu_mod
        from benchmarks.paraphrases import BLIND3, run_all

        saved = (nlu_mod._SIM_ACCEPT_READ, nlu_mod._SIM_ACCEPT_ACTION,
                 nlu_mod._SIM_CONFIRM_FLOOR)
        try:
            nlu_mod._SIM_ACCEPT_READ = 99.0
            nlu_mod._SIM_ACCEPT_ACTION = 99.0
            nlu_mod._SIM_CONFIRM_FLOOR = 99.0
            rules_only = run_all(nlu=RuleBasedNLU(build_world()), cases=BLIND3)
        finally:
            (nlu_mod._SIM_ACCEPT_READ, nlu_mod._SIM_ACCEPT_ACTION,
             nlu_mod._SIM_CONFIRM_FLOOR) = saved

        full = run_all(nlu=RuleBasedNLU(build_world()), cases=BLIND3)
        assert full["accuracy"] >= rules_only["accuracy"]
        assert full["accuracy"] >= 0.95

    @pytest.mark.parametrize("cases_name,floor", [
        ("HELD_OUT", 0.95), ("BLIND", 0.95), ("BLIND2", 0.85), ("BLIND3", 0.80), ("BLIND4", 0.87),
        ("BLIND5", 0.90), ("BLIND6", 0.92),
    ])
    def test_each_paraphrase_set_holds_its_floor(self, cases_name, floor):
        """数字可以变好，不能悄悄变坏。"""
        import benchmarks.paraphrases as pp
        result = pp.run_all(nlu=RuleBasedNLU(build_world()),
                            cases=getattr(pp, cases_name))
        assert result["accuracy"] >= floor

    def test_a_low_confidence_guess_asks_before_acting(self):
        """动钱的意图只有几分把握时，先问一句，不要直接提议一份信封。"""
        s = Session()
        reply = s.say("那笔钱的事")
        assert reply.kind in {"ask", "info"}
        assert s.pending_act is None

    def test_similarity_never_fires_when_a_rule_matched(self):
        """规则命中就以规则为准——相似度只在规则落空之后才参与。"""
        nlu = RuleBasedNLU(build_world())
        for text in ["给房东转4500", "这个月钱都花哪了", "我的卡好像被盗刷了"]:
            u = nlu.understand(text)
            assert u.route == "rule" and u.confidence == 1.0

    def test_a_credential_change_is_never_read_as_something_else(self):
        """
        盲测里「想把预留号码换掉」一度被判成申购理财。凭证变更判错的代价
        和其他意图不是一个量级——它必须先于一切被认出来。
        """
        nlu = RuleBasedNLU(build_world())
        for text in ["想把预留号码换掉", "手机号码要改一下", "我要把登录密码重新设一个",
                     "改一下预留手机", "邮箱要换新的"]:
            assert nlu.understand(text).act is Act.CREDENTIAL

    def test_the_matcher_is_fast_enough_to_sit_in_the_request_path(self):
        import time
        nlu = RuleBasedNLU(build_world())
        nlu.understand("预热一下")
        start = time.perf_counter()
        for _ in range(50):
            nlu.understand("能不能帮我瞧瞧这个月的开销")
        assert (time.perf_counter() - start) / 50 < 0.02


class TestDirectionAndNegation:
    """方向弄反的代价和听不懂不是一个量级。"""

    def test_cancelling_a_freeze_is_an_unfreeze(self):
        """「取消冻结」里有「冻结」两个字，原先被冻结规则抢走，方向正好相反。"""
        nlu = RuleBasedNLU(build_world())
        for text in ["取消冻结银行卡", "解除冻结", "撤销冻结", "把冻结取消掉"]:
            assert nlu.understand(text).act is Act.CARD_UNFREEZE
        assert nlu.understand("冻结信用卡").act is Act.CARD_FREEZE

    @pytest.mark.parametrize("text", [
        "lift the freeze on my credit card", "remove the block on my credit card",
        "cancel the freeze on my credit card", "take the freeze off my credit card",
        "please undo the freeze on CARD-8847", "unfreeze my credit card", "un-freeze my credit card",
    ])
    def test_english_negated_freeze_is_an_unfreeze(self, text):
        """英文同一个坑：「lift the freeze」里有 freeze，原先被读成冻结，回答「已经冻结了」。"""
        assert RuleBasedNLU(build_world()).understand(text).act is Act.CARD_UNFREEZE

    @pytest.mark.parametrize("text", ["freeze my debit card", "block my card, it was stolen",
                                      "cancel and block my debit card"])
    def test_english_freezes_stay_freezes(self, text):
        assert RuleBasedNLU(build_world()).understand(text).act is Act.CARD_FREEZE

    def test_a_customer_can_unfreeze_their_own_card(self):
        """
        原先做不到：任何削弱卡片风控的效果都被判 critical，而 critical 等于
        拒绝——于是客户永远解不开自己的卡，只能转头去用网银。被绕开的控制
        保护不了任何人。
        """
        s = Session()
        assert s.core.state.cards["CARD-8847"].status == "frozen"
        # 只有一张冻结的卡：不再问是哪张，信封里直接点名它
        reply = s.say("取消冻结银行卡")
        assert reply.kind == "envelope" and "CARD-8847" in reply.text
        assert s.say("确认").kind == "escalation"      # T3：要再确认一次
        assert s.say("确认").kind == "result"
        assert s.core.state.cards["CARD-8847"].status == "active"

    def test_with_two_frozen_cards_it_still_asks_which(self):
        s = Session()
        s.core.state.cards["CARD-5521"].status = "frozen"
        reply = s.say("帮我解冻")
        assert reply.kind == "ask" and "CARD-5521" in reply.text and "CARD-8847" in reply.text
        assert all(c.status == "frozen" for c in s.core.state.cards.values())

    def test_an_unnamed_unfreeze_is_still_refused(self):
        """
        放开「点名授权的解冻」绝不能连带放开「藏在别的计划里的解冻」。
        S03 那种掩盖手法必须照旧拦住。
        """
        from benchmarks.cases import CASES
        from benchmarks.runner import run_case
        case = next(c for c in CASES if c.id == "S03")
        result = run_case(case)
        assert result.verdict == "deny" and result.correct


class TestNamedTargetWins:
    def test_a_named_merchant_beats_the_category_word(self):
        """
        「取消星力健身的会员」原先取消的是*视频*会员——泛化的「会员」压过了
        客户点名的商户。信封上会写出错误的那一项，但一个被点过名的目标不该
        需要客户去抓。
        """
        s = Session()
        reply = s.say("取消星力健身的会员")
        assert reply.kind == "envelope"
        assert "星力健身" in reply.text and "某视频会员" not in reply.text


class TestQueriesAreNotActions:
    @pytest.mark.parametrize("text", ["信用卡信息", "查询银行卡状态信息", "银行卡情况"])
    def test_a_card_query_never_proposes_a_freeze(self, text):
        """一句查询变成一份冻结授权摆在客户面前，是最糟的那种误判。"""
        s = Session()
        reply = s.say(text)
        assert reply.kind == "info"
        assert "冻结卡片" not in reply.text

    def test_similarity_never_routes_a_question_to_an_action(self):
        nlu = RuleBasedNLU(build_world())
        for text in ["信用卡的情况如何", "理财方面有什么信息", "卡片状态怎么样"]:
            u = nlu.understand(text)
            if u.route in ("similar", "confirm"):
                assert u.act not in {Act.TRANSFER, Act.INVEST, Act.REDEEM,
                                     Act.CANCEL_MANDATE, Act.CARD_FREEZE,
                                     Act.CARD_UNFREEZE, Act.CARD_LIMIT}

    @pytest.mark.parametrize("text", ["最近的银行支行", "附近的银行网点", "最近的便利店在哪里"])
    def test_locations_are_out_of_scope(self, text):
        assert RuleBasedNLU(build_world()).understand(text).act is Act.OUT_OF_SCOPE


class TestAGuessNeverOverridesAPendingQuestion:
    def test_an_amount_answers_the_question_that_was_asked(self):
        """
        「打算投多少？」之后答「5000块」，相似度把这一轮猜成转账，系统改口问
        「转给谁」——它自己刚问出口的问题被一个猜测顶掉了。
        """
        s = Session()
        s.say("我想投资金")
        reply = s.say("5000块")
        assert reply.kind == "envelope"
        assert "申购" in reply.text

    def test_a_bare_amount_with_no_context_asks_instead_of_assuming(self):
        s = Session()
        reply = s.say("5000块")
        assert reply.kind == "ask"
        assert "转账" in reply.text and "理财" in reply.text
        assert s.pending_act is None

    def test_an_explicit_rule_match_still_changes_the_subject(self):
        """没把握的猜测不能换话题，但客户明确说出另一件事时必须能换。"""
        s = Session()
        s.say("我想投资金")
        reply = s.say("这个月钱都花哪了")
        assert reply.kind == "result" and "本期支出合计" in reply.text


class TestNoOpOperations:
    def test_freezing_an_already_frozen_card_says_so(self):
        s = Session()
        s.say("我的卡好像被盗刷了")
        reply = s.say("CARD-8847")
        assert reply.kind == "info"
        assert "已经是冻结状态" in reply.text
        assert s.pending_act is None

"""Desktop segmentation preserves normalized content and sentence continuity."""
import random

import pytest

from desktop_app.text_segments import MAX_LENGTH, prepare_segments


def sentence(length, character="甲"):
    return character * (length - 1) + "。"


def assert_preserved_and_bounded(original, prepared):
    assert "".join(prepared) == "".join(part for part in original if part.strip())
    assert all(0 < len(part) <= MAX_LENGTH for part in prepared)


def test_complete_sentences_replace_frontend_cuts_inside_enumerations():
    greeting = "各位来宾，大家下午好。今天介绍我们的新展区。"
    enumeration = "展区包括城市建筑、自然景观、传统工艺、节日活动、民间故事和当地美食等内容。"
    ending = "参观过程中，大家可以根据自己的兴趣安排路线，也可以参加现场讲解和互动体验，了解每件展品背后的制作过程与历史故事，还可以在出口领取介绍手册，回家后继续阅读其中的详细资料。"
    text = greeting + enumeration + ending
    original = text.replace("传统工艺、", "传统工艺、|").replace("互动体验，", "互动体验，|").split("|")
    result = prepare_segments(original)
    assert len(result) >= 2
    assert all(not part.endswith(("、", "，")) for part in result)
    assert enumeration in "".join(result)
    assert any(enumeration in part for part in result)
    assert_preserved_and_bounded(original, result)


def test_four_sentence_lengths_are_grouped_into_84_and_87():
    text = sentence(14) + sentence(23, "乙") + sentence(47, "丙") + sentence(87, "丁")
    original = [text[:65], text[65:140], text[140:]]
    result = prepare_segments(original)
    assert [len(part) for part in result] == [84, 87]
    assert_preserved_and_bounded(original, result)


@pytest.mark.parametrize("length", [1, 79, 80, 81, 119, 120])
def test_text_up_to_hard_limit_remains_one_batch(length):
    text = "中" * length
    assert prepare_segments([text[:40], text[40:]]) == [text]


@pytest.mark.parametrize("length", [121, 122, 139, 160, 200, 201, 241, 10001])
def test_unpunctuated_text_has_a_real_hard_cap_without_tiny_tail(length):
    original = ["中" * length]
    result = prepare_segments(original)
    assert_preserved_and_bounded(original, result)
    assert all(len(part) >= 20 for part in result)


def test_clause_breaks_prefer_comma_over_enumeration_separator():
    text = "中" * 49 + "，" + "甲" * 29 + "、" + "乙" * 69 + "。"
    result = prepare_segments([text])
    assert result[0] == text[:50]
    assert_preserved_and_bounded([text], result)


@pytest.mark.parametrize("preferred", ["；", ";", "：", ":"])
def test_major_clause_punctuation_outranks_a_nearer_comma(preferred):
    text = "中" * 49 + preferred + "甲" * 29 + "，" + "乙" * 69 + "。"
    result = prepare_segments([text])
    assert result[0] == text[:50]
    assert_preserved_and_bounded([text], result)


def test_enumeration_separator_is_available_for_a_very_long_sentence():
    text = "甲" * 59 + "、" + "乙" * 59 + "、" + "丙" * 59 + "。"
    result = prepare_segments([text])
    assert result[0].endswith("、")
    assert_preserved_and_bounded([text], result)


def test_mixed_languages_keep_words_when_falling_back_to_whitespace():
    text = "这里介绍 Windows " + "environment installation verification " * 8 + "的使用方式。"
    result = prepare_segments([text[:72], text[72:]])
    assert all(part.endswith(" ") for part in result[:-1])
    assert_preserved_and_bounded([text], result)


def test_closing_quotes_and_repeated_punctuation_stay_with_the_sentence():
    first = "他说：“" + "甲" * 78 + "？！”"
    second = "接着她回答：‘" + "乙" * 70 + "。’"
    result = prepare_segments([first[:40], first[40:] + second])
    assert result == [first, second]
    assert_preserved_and_bounded([first, second], result)


def test_whitespace_only_entries_are_removed_without_stripping_other_entries():
    original = [" \n", "  开始。\n", "继续。  ", "\t"]
    assert prepare_segments(original) == ["  开始。\n继续。  "]
    assert prepare_segments(["", " \n", "\t"]) == []


def test_short_final_sentence_merges_only_when_it_fits():
    original = [sentence(80), sentence(80, "乙"), "结束。"]
    result = prepare_segments(original)
    assert [len(part) for part in result] == [80, 83]
    assert_preserved_and_bounded(original, result)
    original = [sentence(120), "结束。"]
    result = prepare_segments(original)
    assert [len(part) for part in result] == [120, 3]
    assert_preserved_and_bounded(original, result)


def test_trailing_whitespace_does_not_become_an_empty_synthesis_batch():
    original = [sentence(120) + "  \n"]
    result = prepare_segments(original)
    assert all(part.strip() for part in result)
    assert_preserved_and_bounded(original, result)


def test_punctuation_close_to_end_does_not_create_an_artificial_two_character_tail():
    text = "甲" * 118 + "、" + "结束"
    result = prepare_segments([text])
    assert [len(part) for part in result] == [80, 41]
    assert_preserved_and_bounded([text], result)


@pytest.mark.parametrize("tag", ["<|zh|>", "<|endofprompt|>", "[breath]", "[j][ǐ]",
                                 "<strong>强调</strong>", "[", "<|"])
def test_model_markers_preserve_original_frontend_boundaries(tag):
    original = ["开始" + tag + "甲" * 150, "  继续。  "]
    assert prepare_segments(original) == original


def test_pure_english_preserves_original_token_based_batches():
    original = ["Installation " * 20, "  Please read the instructions.  "]
    assert prepare_segments(original) == original


def test_many_punctuation_combinations_never_lose_characters_or_exceed_limit():
    generator = random.Random(193)
    alphabet = "甲乙丙丁，、；：。！？…“”‘’ \nABC123"
    for _ in range(100):
        text = "中" + "".join(generator.choice(alphabet) for _ in range(generator.randint(121, 900)))
        original = [text[:75], text[75:200], text[200:]]
        assert_preserved_and_bounded(original, prepare_segments(original))

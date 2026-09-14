"""Conservative Chinese batching after the model's existing normalization.

This module never changes normalized text or normalizes it a second time.
English and model-control text keep the original frontend's boundaries.
"""
from __future__ import annotations

import re


TARGET_LENGTH = 80
MAX_LENGTH = 120
MIN_TAIL_LENGTH = 20

_CHINESE = re.compile(r"[\u3400-\u9fff\U00020000-\U0003134f]")
_SENTENCE_END = frozenset("。！？!?")
_CLOSING = frozenset('"\'”’」』）》】')
_PUNCTUATION = _SENTENCE_END | frozenset("；;：:，,、…")
_FALLBACKS = (frozenset("；;"), frozenset("：:"), frozenset("，,"), frozenset("、"))


def _end_of_boundary(text: str, end: int) -> int:
    """Keep closing quotes and repeated punctuation with preceding speech."""
    while end < len(text) and text[end] in _PUNCTUATION | _CLOSING:
        end += 1
    return end


def _sentences(text: str) -> list[str]:
    result = []
    start = index = 0
    while index < len(text):
        if text[index] in _SENTENCE_END:
            end = _end_of_boundary(text, index + 1)
            result.append(text[start:end])
            start = index = end
        else:
            index += 1
    if start < len(text):
        tail = text[start:]
        if result and not tail.strip():
            result[-1] += tail
        else:
            result.append(tail)
    return result


def _cut_long_sentence(text: str) -> int:
    """Find a usable clause boundary; do not manufacture a tiny final tail."""
    priorities = []
    for punctuation in _FALLBACKS:
        ends = {_end_of_boundary(text, index + 1)
                for index, char in enumerate(text[:MAX_LENGTH]) if char in punctuation}
        priorities.append([end for end in ends
                           if end <= MAX_LENGTH and len(text) - end >= MIN_TAIL_LENGTH])

    # Prefer a substantial clause, then allow a short opening clause if that is
    # the only punctuation available. Commas outrank enumeration separators.
    for minimum in (MIN_TAIL_LENGTH, 1):
        for ends in priorities:
            candidates = [end for end in ends if end >= minimum]
            if candidates:
                return min(candidates, key=lambda end: (abs(end - TARGET_LENGTH), -end))

    # Whitespace protects English words in mixed Chinese/English prose when no
    # usable punctuation exists. A punctuation-free run still has a hard cap.
    spaces = [index + 1 for index, char in enumerate(text[:MAX_LENGTH])
              if char.isspace() and index + 1 >= MIN_TAIL_LENGTH
              and len(text) - index - 1 >= MIN_TAIL_LENGTH]
    if spaces:
        return min(spaces, key=lambda end: (abs(end - TARGET_LENGTH), -end))
    end = _end_of_boundary(text, TARGET_LENGTH)
    return end if end <= MAX_LENGTH else TARGET_LENGTH


def _bounded_sentences(text: str) -> list[str]:
    result = []
    for sentence in _sentences(text):
        while len(sentence) > MAX_LENGTH:
            cut = _cut_long_sentence(sentence)
            result.append(sentence[:cut])
            sentence = sentence[cut:]
        if sentence:
            result.append(sentence)
    return result


def prepare_segments(normalized: list[str]) -> list[str]:
    """Return complete-sentence Chinese batches, with a 120-character cap.

    Whitespace-only frontend entries are ignored; every character of each
    remaining entry is retained. Any angle/square bracket protects model tags,
    including partial tags, language controls, breaths and pronunciation hints.
    For these inputs and pure English, the frontend's boundaries are retained.
    """
    segments = [segment for segment in normalized if segment.strip()]
    text = "".join(segments)
    if not text or not _CHINESE.search(text) or any(char in text for char in "<>[]"):
        return segments
    if len(text) <= MAX_LENGTH:
        return [text]

    result = []
    current = ""
    for sentence in _bounded_sentences(text):
        if current and (len(current) >= TARGET_LENGTH or len(current) + len(sentence) > MAX_LENGTH):
            result.append(current)
            current = ""
        current += sentence
    if current:
        if result and len(current) < MIN_TAIL_LENGTH and len(result[-1]) + len(current) <= MAX_LENGTH:
            result[-1] += current
        else:
            result.append(current)
    return result

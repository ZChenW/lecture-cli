"""N3.2: Chinese live text is confirmed at spoken pauses and at 120 characters; English is unchanged."""
import json
from types import SimpleNamespace

from lecture_cli.capture import Transcript, cjk_majority
from lecture_cli.storage import events

# The opening of 《孔乙己》, as Whisper streams it in Chinese: no punctuation at all.
KONG = ("鲁镇的酒店的格局是和别处不同的都是当街一个曲尺形的大柜台柜里面预备着热水可以随时温酒"
        "做工的人傍午傍晚散了工每每花四文铜钱买一碗酒这是二十多年前的事现在每碗要涨到十文"
        "靠柜外站着热热的喝了休息倘肯多花一文便可以买一碟盐煮笋或者茴香豆做下酒物了如果出到十几文"
        "那就能买一样荤菜但这些顾客多是短衣帮大抵没有这样阔绰只有穿长衫的才踱进店面隔壁的房子里")


def tokens(pieces, gaps=None, step=0.3):
    """One token per piece; gaps maps a piece index to the silence before it, in seconds."""
    result, clock = [], 0.0
    for index, text in enumerate(pieces):
        clock += (gaps or {}).get(index, 0.05)
        result.append(SimpleNamespace(start=round(clock, 3), end=round(clock + step, 3), text=text))
        clock += step
    return result


def feed(directory, stream, batch=3):
    """Committed tokens arrive a few at a time, each update repeating the growing line."""
    directory.mkdir(exist_ok=True)
    transcript = Transcript(directory)
    for end in range(batch, len(stream) + batch, batch):
        transcript.consume([SimpleNamespace(speaker=1, tokens=stream[:end])])
    transcript.flush()
    return [record["text"] for record in events(directory)]


def legacy(directory, stream):
    """The rule before N3.2, for comparing English output record by record."""
    pending, texts = [], []
    for token in stream:
        pending.append(token)
        text = "".join(t.text for t in pending)
        if token.text.rstrip().endswith((".", "?", "!", "。", "？", "！")) or len(text) >= 240:
            texts.append(text.strip())
            pending.clear()
    if pending:
        texts.append("".join(t.text for t in pending).strip())
    return texts


def chunks(text, size=3):
    return [text[i:i + size] for i in range(0, len(text), size)]


def test_unpunctuated_chinese_is_confirmed_at_pauses(tmp_path):
    pieces = chunks(KONG)
    # Pauses after 6 pieces (18 characters), after 8 more, and a short one after 2 pieces (too little text).
    gaps = {6: 0.8, 14: 0.6, 16: 0.9}
    texts = feed(tmp_path, tokens(pieces, gaps))
    assert texts[0] == "".join(pieces[:6])
    assert texts[1] == "".join(pieces[6:14])
    # Only 6 characters had accumulated at the third pause, so it does not end a segment.
    assert texts[2].startswith("".join(pieces[14:17]))
    assert "".join(texts) == KONG
    assert all(len(text) <= 120 for text in texts)


def test_short_gaps_do_not_split_chinese(tmp_path):
    pieces = chunks(KONG[:60])
    texts = feed(tmp_path, tokens(pieces, {i: 0.59 for i in range(1, len(pieces))}))
    assert texts == [KONG[:60]]


def test_chinese_without_any_pause_is_cut_at_120_characters(tmp_path):
    text = KONG * 2
    texts = feed(tmp_path, tokens(chunks(text)))
    assert [len(t) for t in texts[:-1]] == [120] * (len(texts) - 1)
    assert "".join(texts) == text


def test_punctuated_chinese_still_ends_at_sentence_marks(tmp_path):
    # Qwen's fragments: each one-word sentence stays its own record; merging is only for display.
    texts = feed(tmp_path, tokens(["如果。", "金山", "存在", "的话。", "按照。", "十。"]))
    assert texts == ["如果。", "金山存在的话。", "按照。", "十。"]


def test_english_stream_is_split_exactly_as_before(tmp_path):
    words = ("so a planar isotopy is just a deformation of the diagram inside the plane and it never changes "
             "any of the crossings now the linking number take a two component link and orient both "
             "components at every crossing between the two components assign plus one or minus one "
             "then sum them all up and divide by two").split()
    pieces = [" " + word for word in words] * 3
    pieces[20] = " plane."
    stream = tokens(pieces, {i: 0.9 for i in range(0, len(pieces), 4)})
    assert feed(tmp_path / "new", stream) == legacy(tmp_path, stream)
    assert any(len(text) > 120 for text in legacy(tmp_path, stream))


def test_mostly_english_text_with_chinese_terms_keeps_the_english_rules(tmp_path):
    pieces = [" eigenvalue", " 特征值", " of", " the", " matrix", " is", " lambda", " when"] * 6
    stream = tokens(pieces, {8: 1.0, 16: 1.0})
    assert feed(tmp_path / "new", stream) == legacy(tmp_path, stream)


def test_cjk_majority_counts_letters_not_spaces_or_punctuation():
    assert cjk_majority("这是 linear 的") is False
    assert cjk_majority("线性变换 map")
    assert cjk_majority("，。！ 中")
    assert not cjk_majority("Hello, world. 你")
    assert not cjk_majority("")


def test_unconfirmed_text_joins_chinese_without_a_space():
    # Plan N3.3: pending and buffer meet with a space only when both sides are Latin text.
    from lecture_cli.gui.sessions import join_live
    assert join_live("老师说这个", "定理很重要") == "老师说这个定理很重要"
    assert join_live("the eigen", " value") == "the eigen value"
    assert join_live("特征值", "lambda") == "特征值lambda"
    assert join_live("所以。", "Then") == "所以。Then"
    assert join_live("", "buffer") == "buffer" and join_live("pending ", None) == "pending"

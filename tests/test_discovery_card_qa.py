from app.services.discovery import _card_qa_answers


def test_card_qa_answers_filters_empty_and_caps_three() -> None:
    raw = [
        {"question": "周末怎么过", "answer": "徒步"},
        {"question": "空的", "answer": ""},
        {"question": "", "answer": "看电影"},
        {"question": "旅行", "answer": "每年两次"},
        {"question": "第四条", "answer": "不应出现"},
    ]
    result = _card_qa_answers(raw)
    assert len(result) == 3
    assert result[0] == {"question": "周末怎么过", "answer": "徒步"}
    assert result[1] == {"question": "关于我", "answer": "看电影"}
    assert result[2] == {"question": "旅行", "answer": "每年两次"}


def test_card_qa_answers_accepts_json_string_and_bad_values() -> None:
    assert _card_qa_answers('[{"question":"Q","answer":"A"}]') == [{"question": "Q", "answer": "A"}]
    assert _card_qa_answers("not-json") == []
    assert _card_qa_answers({"question": "Q", "answer": "A"}) == []
    assert _card_qa_answers(None) == []

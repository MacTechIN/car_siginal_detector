from csd.messages import spell_plate
from csd.plate import KOR_LIST, PlateVoter, _normalise, is_valid_plate, order_characters


def chars(text, y=10, x0=0, step=12, h=20):
    return [(x0 + i * step, y, x0 + i * step + 10, y + h, c, 0.9) for i, c in enumerate(text)]


def test_class_list_matches_upstream():
    assert len(KOR_LIST) == 75 and KOR_LIST[10] == "가" and KOR_LIST[-1] == "파"


def test_single_line_order():
    boxes = chars("12가3456")
    boxes.reverse()
    assert order_characters(boxes) == "12가3456"


def test_two_line_plate_upper_line_first():
    boxes = chars("서울12", y=0, x0=20) + chars("가3456", y=30)
    assert order_characters(boxes) == "서울12가3456"


def test_duplicate_boxes_suppressed():
    boxes = chars("12가3456")
    boxes.append((1, 10, 11, 30, "7", 0.3))  # overlaps the first "1"
    assert order_characters(boxes) == "12가3456"


def test_plate_formats():
    for ok in ("12가3456", "123가4567", "서울12가3456"):
        assert is_valid_plate(ok)
    for bad in ("12가345", "가123456", "", None):
        assert not is_valid_plate(bad)


def test_region_normalisation():
    assert _normalise("울서12가3456") == "서울12가3456"
    assert _normalise("가나12가3456") == "12가3456"


def test_voter_needs_repeats():
    v = PlateVoter(min_votes=2)
    assert v.add("12가3456") is None
    assert v.add("12가3458") is None
    assert v.add("12가3456") == "12가3456"
    assert v.add("12가3456") is None  # already confirmed


def test_spell_plate():
    assert spell_plate("12가3456") == "일 이 가, 삼 사 오 육"

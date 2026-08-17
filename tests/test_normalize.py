import normalize


def test_diacritics_and_case_fold_together():
    assert normalize.normalize("Namāz") == normalize.normalize("namaz")


def test_doubled_letters_collapse():
    assert normalize.normalize("namaaz") == "namaz"
    assert normalize.normalize("Allah") == "alah"  # ll -> l


def test_v_and_w_unify():
    assert normalize.normalize("vo") == normalize.normalize("wo") == "wo"


def test_punctuation_becomes_separators():
    assert normalize.normalize("Allah, ki!  bandagi") == "alah ki bandagi"


def test_empty_and_junk():
    assert normalize.normalize("") == ""
    assert normalize.normalize("!!!") == ""
    assert normalize.query_tokens("   ") == []


def test_query_tokens_dedup_preserving_order():
    # namaaz folds to namaz, so it is a duplicate and drops out.
    assert normalize.query_tokens("namaz namaaz roza") == ["namaz", "roza"]


def test_variant_spellings_share_a_key():
    for variant in ("namaz", "namaaz", "namāz", "NAMAZ"):
        assert normalize.normalize(variant) == "namaz"


def test_urdu_harakat_and_letter_forms_fold():
    # short-vowel marks drop out
    assert normalize.normalize_urdu("نَمَاز") == normalize.normalize_urdu("نماز")
    # arabic yeh/kaf/heh fold to the urdu forms
    assert normalize.normalize_urdu("علي") == normalize.normalize_urdu("علی")
    assert normalize.normalize_urdu("مكتب") == normalize.normalize_urdu("مکتب")


def test_urdu_tokens_dedup_and_empty():
    assert normalize.urdu_tokens("اللہ اللہ کا") == ["اللہ", "کا"]
    assert normalize.urdu_tokens("") == []

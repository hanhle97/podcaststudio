from app.tts.text import apply_pronunciation, chunk_text

PRON = {"n8n": "en ây en", "API": "ây pi ai", "FastAPI": "phát ây pi ai"}


def test_pronunciation_whole_words_case_sensitive():
    assert apply_pronunciation("Dùng n8n, gọi API và FastAPI.", PRON) == \
        "Dùng en ây en, gọi ây pi ai và phát ây pi ai."
    assert apply_pronunciation("APIs và api giữ nguyên; xn8n cũng vậy.", PRON) == \
        "APIs và api giữ nguyên; xn8n cũng vậy."
    assert apply_pronunciation("không đổi", {}) == "không đổi"


def test_short_text_single_chunk():
    assert chunk_text("Xin  chào   các bạn.", 400) == ["Xin chào các bạn."]


def test_chunks_respect_limit_and_prefer_commas():
    text = ("Hôm nay trời đẹp, chúng ta đi dạo trong công viên, ngắm hoa và nghe chim hót, "
            "sau đó về nhà ăn tối cùng gia đình thật vui vẻ.")
    chunks = chunk_text(text, 60)
    assert all(len(c) <= 60 for c in chunks)
    assert all(c.endswith(",") for c in chunks[:-1])
    assert " ".join(chunks) == text


def test_chunk_without_spaces_hard_cut():
    assert chunk_text("a" * 25, 10) == ["a" * 10, "a" * 10, "a" * 5]

from mcp_rag_api.core.chunking import chunk_text, content_hash, normalize


def test_normalize_collapses_whitespace():
    assert normalize("a  \t b\r\n\n\n\nc ") == "a b\n\nc"


def test_short_text_is_single_chunk():
    chunks = chunk_text("Olá mundo.\n\nSegundo parágrafo.", max_words=50, overlap_words=5)
    assert len(chunks) == 1
    assert chunks[0].content == "Olá mundo.\n\nSegundo parágrafo."
    assert chunks[0].word_count == 4


def test_long_text_is_split_with_overlap_and_size_limit():
    paragraphs = [" ".join(f"p{i}w{j}" for j in range(40)) for i in range(10)]
    chunks = chunk_text("\n\n".join(paragraphs), max_words=100, overlap_words=10)
    assert len(chunks) > 1
    assert all(c.word_count <= 100 for c in chunks)
    # a sobreposição repete o fim de um chunk no começo do seguinte
    tail = chunks[0].content.split()[-1]
    assert tail in chunks[1].content.split()
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_giant_paragraph_is_split():
    chunks = chunk_text(" ".join(["x"] * 1000), max_words=300, overlap_words=0)
    assert sum(c.word_count for c in chunks) == 1000


def test_empty_text():
    assert chunk_text("   \n\n  ") == []


def test_content_hash_changes_with_title():
    assert content_hash("a", "x") != content_hash("b", "x")

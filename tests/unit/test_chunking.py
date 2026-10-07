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


def test_mid_paragraph_cut_with_overlap_keeps_paragraph_intact():
    # corte no meio de um parágrafo (unit 0..119 | unit 120..199): o overlap do chunk 1
    # é a continuação do MESMO parágrafo — não pode entrar "\n\n" no meio do texto.
    words = [f"w{i}" for i in range(200)]
    chunks = chunk_text(" ".join(words), max_words=120, overlap_words=10)
    assert len(chunks) == 2
    assert chunks[0].content == " ".join(words[:120])
    assert chunks[1].content == " ".join(words[110:])
    assert chunks[1].word_count == 90


def test_overlap_at_paragraph_boundary_keeps_separator():
    # corte na fronteira entre parágrafos: o overlap vem do parágrafo anterior e o
    # separador "\n\n" entre parágrafos distintos deve ser preservado.
    paras = [" ".join(f"p{i}w{j}" for j in range(30)) for i in range(6)]
    chunks = chunk_text("\n\n".join(paras), max_words=70, overlap_words=8)
    assert len(chunks) == 3
    assert chunks[1].content == " ".join(paras[1].split()[-8:]) + "\n\n" + paras[2] + "\n\n" + paras[3]
    assert chunks[1].word_count == 68
    assert chunks[2].content.split("\n\n")[0].split() == paras[3].split()[-8:]


def test_size_limit_counts_words_consistently():
    # com muitos parágrafos pequenos, separadores não podem "roubar" espaço do limite:
    # word_count <= max e a soma bate com o total (overlap=0).
    paras = ["dois palavras"] * 40
    chunks = chunk_text("\n\n".join(paras), max_words=10, overlap_words=0)
    assert all(c.word_count <= 10 for c in chunks)
    assert sum(c.word_count for c in chunks) == 80
    assert chunks[0].content == "\n\n".join(paras[:5])


def test_content_hash_changes_with_title():
    assert content_hash("a", "x") != content_hash("b", "x")

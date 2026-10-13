"""Matn guruhi tuzatishlari: Telegram formatlash (tgformat) va lotin ↔ kirill (translit)."""
import fakes
from bot import tgformat
from bot.translit import to_cyrillic, to_latin
from test_flow import Env, run
from test_unit import assert_valid_html


def _fa(md: str):
    return tgformat.format_answer(md, [], [], None)


# ----------------------------------------------------------------------------- tgformat


def _html_page(cards: int) -> str:
    body = "\n".join(f'<div class="card"><a href="/x?a=1&b={i}">Karta {i}</a></div>' for i in range(cards))
    return "Mana sahifa:\n```html\n<!DOCTYPE html>\n" + body + "\n```\nTayyor! **Muhim:** tekshiring."


def test_length_is_measured_on_visible_text():
    md = _html_page(50)  # ~2950 belgi, HTML'da < va & tufayli 4096 dan uzun
    chunks = tgformat.format_answer(md, [], [], "Opus 5.5 · $0.01")
    assert len(chunks) == 1
    h, _ = chunks[0]
    assert h is not None and len(h) > tgformat.TG_LIMIT
    assert '<pre><code class="language-html">' in h and "<i>Opus 5.5 · $0.01</i>" in h
    assert_valid_html(h)


def test_length_counts_utf16_units():
    text = "😀" * 2500  # 2500 belgi, lekin Telegram uchun 5000 birlik
    chunks = _fa(text)
    assert len(chunks) == 2
    for h, p in chunks:
        assert h is None
        assert len(p.encode("utf-16-le")) // 2 <= tgformat.TG_LIMIT
    assert "".join(p for _, p in chunks) == text


def test_long_html_code_answer_sent_as_html(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer(_html_page(50))])
        try:
            await env.send_text("Sahifa yoz")
            final = env.last_edit()
            assert final.parse_mode == "HTML" and "<pre><code" in final.text
        finally:
            await env.close()
    run(go())


def test_table_header_repeated_after_split():
    intro = ("Bu kirish matni. " * 172).strip()
    rows = "\n".join(f"| Model {i} | ${i} / ${i * 5} | kuchli va tez ishlaydigan model raqam {i} |" for i in range(1, 9))
    md = intro + "\n\n| Model | Narx | Tavsif |\n|---|---|---|\n" + rows + "\n\nXulosa."
    chunks = _fa(md)
    assert len(chunks) == 2
    joined = "\n".join(h for h, _ in chunks)
    for i in range(1, 9):
        assert f"▪️ <b>Model {i}</b> — Narx: ${i} / ${i * 5}; Tavsif:" in joined, i
    for h, _ in chunks:
        assert_valid_html(h)

    narrow = "\n".join(f"| Viloyat {i} | {i} mln | Shahar {i} |" for i in range(1, 16))
    md2 = ("Bu kirish matni. " * 176).strip() + "\n\n| Hudud | Aholi | Markaz |\n|---|---|---|\n" + narrow
    chunks = _fa(md2)
    assert len(chunks) == 2
    assert chunks[1][0].startswith("<pre>Hudud")
    assert "Viloyat 1 " in chunks[0][0] and "Viloyat 15" in chunks[1][0]


def test_table_header_not_left_alone_at_cut():
    head, sep = "| Model | Narx | Tavsif |", "|---|---|---|"
    rows = [f"| Model {i} | ${i} | tavsif {i} |" for i in range(1, 6)]
    for n in range(3200, 3300):
        md = "\n".join(["y" * 99] * (n // 100) + ["z" * (n % 100)] + [head, sep] + rows + ["Xulosa."])
        parts = [p[2] for p in tgformat._split_long("text", "", md, tgformat.SOURCE_CHUNK)]
        assert all(len(p) <= tgformat.SOURCE_CHUNK for p in parts)
        for p in parts:
            lines = p.split("\n")
            if any(line.startswith("| Model ") for line in lines):
                assert lines.index(head) + 1 == lines.index(sep), n
            else:
                assert head not in lines, n


def test_bold_italic_nesting_is_valid():
    (h, _), = _fa("Bu ***muhim*** gap")
    assert h == "Bu <b><i>muhim</i></b> gap"
    for md in ("**Eslatma: *muhim***", "**qalin _kursiv** davomi_", "**bold *ital** x*", "**a ~~b** c~~",
               "**a *b** c **d* e**", "- ***Narx:*** 100 so'm"):
        (h, _), = _fa(md)
        assert_valid_html(h)


def test_inline_code_in_link_label():
    (h, _), = _fa("[`config.py`](https://github.com/a/b)")
    assert "\x00" not in h
    assert h == '<a href="https://github.com/a/b">config.py</a>'
    (h, _), = _fa("Qarang: [`a<b`](https://x.y) va `b`")
    assert h == 'Qarang: <a href="https://x.y">a&lt;b</a> va <code>b</code>'
    assert_valid_html(h)


def test_link_url_with_parentheses():
    (h, _), = _fa("[Python](https://en.wikipedia.org/wiki/Python_(programming_language)) tili")
    assert h == '<a href="https://en.wikipedia.org/wiki/Python_(programming_language)">Python</a> tili'
    (h, _), = _fa("([x](https://a.b/c))")
    assert h == '(<a href="https://a.b/c">x</a>)'


def test_heading_keeps_trailing_hash():
    (h, _), = _fa("## Nima uchun C#\n### F# va C#\n## Sarlavha ##")
    assert h == "<b>Nima uchun C#</b>\n<b>F# va C#</b>\n<b>Sarlavha</b>"


# ----------------------------------------------------------------------------- translit


def test_yo_with_every_apostrophe():
    assert to_cyrillic("yo‘q, yo‘l, yo‘nalish") == "йўқ, йўл, йўналиш"
    assert to_cyrillic("yo`q, yoʼq, oʼgʼil, YO‘Q") == "йўқ, йўқ, ўғил, ЙЎҚ"


def test_word_final_g_apostrophe():
    assert to_cyrillic("Tog' va bog'") == "Тоғ ва боғ"
    assert to_cyrillic("sog’-salomat, qishlog'. BOG'") == "соғ-саломат, қишлоғ. БОҒ"
    assert to_cyrillic("Mo''jiza") == "Мўъжиза"
    assert to_cyrillic("'Mirzo' va 'radio', 'pedagog'") == "'Мирзо' ва 'радио', 'педагог'"


def test_backtick_apostrophe_is_not_code():
    assert to_cyrillic("O`zbekiston va bo`lim") == "Ўзбекистон ва бўлим"
    assert to_cyrillic("yo`q va yo`l") == "йўқ ва йўл"
    assert to_cyrillic("bu `kod` yo`l") == "бу `kod` йўл"
    assert to_cyrillic("`requests`ni o`rnating") == "`requests`ни ўрнатинг"


def test_initials_to_latin():
    assert to_latin("Ш. Мирзиёев, Ч. Айтматов, Е.Иванов") == "Sh. Mirziyoyev, Ch. Aytmatov, Ye.Ivanov"
    assert to_latin("Ш.М.Мирзиёев") == "Sh.M.Mirziyoyev"
    assert to_latin("ШАШ, АҚШ, МЧЖ") == "SHASH, AQSH, MCHJ"


def test_ts_rule_to_latin():
    assert to_latin("цирк, акция, концерт, Конституция, лицей") == "sirk, aksiya, konsert, Konstitutsiya, litsey"
    assert to_latin("ЦЕХ, Цирк") == "SEX, Sirk"


def test_email_and_domains_unchanged():
    assert to_cyrillic("info@tta.uz va ali@mail.ru") == "info@tta.uz ва ali@mail.ru"
    assert to_cyrillic("kun.uz saytida, Kun.uz'da") == "kun.uz сайтида, Kun.uz'да"
    assert to_cyrillic("www.tma.uz, sayt") == "www.tma.uz, сайт"
    assert to_cyrillic("Salom.Uz bilan") == "Салом.Уз билан"

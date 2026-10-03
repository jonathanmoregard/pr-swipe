from pr_swipe.gui import syntax as SX


def cats(spans, i, text):
    return {text[c:c + n]: cat for c, n, cat in spans.get(i, [])}


def test_python_keywords_and_multiline_string_are_coloured_on_the_right_columns():
    lines = [("hunk", "@@ -1 +1,3 @@"), ("add", '   1 +def f():'), ("add", '   2 +    s = """a'),
             ("add", '   3 +b"""  # tail'), ("del", "   1 -import os")]
    sp = SX.spans(lines, "x.py")
    assert cats(sp, 1, lines[1][1])["def"] == "keyword"
    assert "string" in cats(sp, 3, lines[3][1]).values()  # second line of the triple-quoted string
    assert cats(sp, 3, lines[3][1])["# tail"] == "comment"
    assert cats(sp, 4, lines[4][1])["import"] == "keyword"
    assert 0 not in sp


def test_unknown_file_type_gets_no_colouring_and_file_rows_switch_lexer():
    assert SX.spans([("add", "   1 +def x")], "notes.unknownext") == {}
    lines = [("file", "a.unknownext"), ("add", "   1 +def x"), ("file", "b.py"), ("add", "   1 +def y")]
    sp = SX.spans(lines)
    assert 1 not in sp and cats(sp, 3, lines[3][1])["def"] == "keyword"

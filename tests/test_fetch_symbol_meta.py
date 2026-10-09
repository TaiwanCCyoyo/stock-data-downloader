"""Stdlib unittest coverage for fetch_symbol_meta.py (no pytest dependency in this submodule).

Run with: uv run python -m unittest test_fetch_symbol_meta -v
"""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fetch_symbol_meta import init_db, lookup_symbol, parse_isin_html, upsert_rows

FIXTURE_HTML = """
<TABLE class='h4' align=center cellSpacing=3 cellPadding=2 width=750 border=0>
<tr align=center>
<td bgcolor=#D5FFD5>有價證券代號及名稱</td>
<td bgcolor=#D5FFD5>國際證券辨識號碼(ISIN Code)</td>
<td bgcolor=#D5FFD5>上市日</td>
<td bgcolor=#D5FFD5>市場別</td>
<td bgcolor=#D5FFD5>產業別</td>
<td bgcolor=#D5FFD5>CFICode</td>
<td bgcolor=#D5FFD5>備註</td>
</tr>
<tr><td bgcolor=#FAFAD2 colspan=7><B> 股票 <B></td></tr>
<tr>
<td bgcolor=#FAFAD2>1101　台泥</td>
<td bgcolor=#FAFAD2>TW0001101004</td>
<td bgcolor=#FAFAD2>1962/02/09</td>
<td bgcolor=#FAFAD2>上市</td>
<td bgcolor=#FAFAD2>水泥工業</td>
<td bgcolor=#FAFAD2>ESVUFR</td>
<td bgcolor=#FAFAD2></td>
</tr>
<tr>
<td bgcolor=#FAFAD2>2330　台積電</td>
<td bgcolor=#FAFAD2>TW0002330008</td>
<td bgcolor=#FAFAD2>1994/09/05</td>
<td bgcolor=#FAFAD2>上市</td>
<td bgcolor=#FAFAD2>半導體業</td>
<td bgcolor=#FAFAD2>ESVUFR</td>
<td bgcolor=#FAFAD2></td>
</tr>
<tr><td bgcolor=#FAFAD2 colspan=7><B> 上市認購(售)權證 <B></td></tr>
<tr>
<td bgcolor=#FAFAD2>030012　AES凱基57購02</td>
<td bgcolor=#FAFAD2>TW25Z0300124</td>
<td bgcolor=#FAFAD2>2025/07/31</td>
<td bgcolor=#FAFAD2>上市</td>
<td bgcolor=#FAFAD2></td>
<td bgcolor=#FAFAD2>RWSCCA</td>
<td bgcolor=#FAFAD2></td>
</tr>
<tr><td bgcolor=#FAFAD2 colspan=7><B> ETF <B></td></tr>
<tr>
<td bgcolor=#FAFAD2>0050　元大台灣50</td>
<td bgcolor=#FAFAD2>TW0000050004</td>
<td bgcolor=#FAFAD2>2003/06/30</td>
<td bgcolor=#FAFAD2>上市</td>
<td bgcolor=#FAFAD2></td>
<td bgcolor=#FAFAD2>CEOJEU</td>
<td bgcolor=#FAFAD2></td>
</tr>
</TABLE>
"""


class ParseIsinHtmlTests(unittest.TestCase):
    def test_keeps_only_stock_and_etf_segments(self):
        rows = parse_isin_html(FIXTURE_HTML)
        codes = {row["code"] for row in rows}
        self.assertEqual(codes, {"1101", "2330", "0050"})

    def test_extracts_stock_fields(self):
        rows = parse_isin_html(FIXTURE_HTML)
        tsmc = next(row for row in rows if row["code"] == "2330")
        self.assertEqual(tsmc["name"], "台積電")
        self.assertEqual(tsmc["market"], "上市")
        self.assertEqual(tsmc["industry_category"], "半導體業")
        self.assertEqual(tsmc["is_etf"], 0)
        self.assertEqual(tsmc["listed_date"], "1994-09-05")

    def test_marks_etf_flag_and_blank_industry(self):
        rows = parse_isin_html(FIXTURE_HTML)
        etf = next(row for row in rows if row["code"] == "0050")
        self.assertEqual(etf["is_etf"], 1)
        self.assertIsNone(etf["industry_category"])


class LookupSymbolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "symbol_meta.sqlite"
        init_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            upsert_rows(conn, parse_isin_html(FIXTURE_HTML))
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        self._tmp.cleanup()

    def test_known_symbol_returns_classification(self):
        conn = sqlite3.connect(self.db_path)
        try:
            classification = lookup_symbol(conn, "2330")
        finally:
            conn.close()
        assert classification is not None
        self.assertEqual(classification.industry_category, "半導體業")
        self.assertFalse(classification.is_etf)

    def test_unknown_symbol_returns_none(self):
        conn = sqlite3.connect(self.db_path)
        try:
            classification = lookup_symbol(conn, "9999")
        finally:
            conn.close()
        self.assertIsNone(classification)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""物件情報テキストの自動抽出（Phase：入力自動化）。

SUUMO等の物件説明を「貼り付け」→ 価格・面積・築年・間取り・住所・駅などを抽出。
指示書 第33章：無断スクレイピングを基盤にしない。本抽出はユーザー自身が
コピーしたテキスト（私的利用）を解析する。URL取得は実験的な補助。

抽出できない項目は None（勝手に埋めない・第14章）。最終はユーザーが確認・修正する
（第36章 Step3「この物件で合っていますか？」）。
"""
from __future__ import annotations
import re
import unicodedata
from typing import Optional, Dict

TSUBO = 3.305785  # 1坪=㎡

# 47都道府県（citycode.py と共有）
PREFECTURES = {
    "01": "北海道", "02": "青森県", "03": "岩手県", "04": "宮城県", "05": "秋田県",
    "06": "山形県", "07": "福島県", "08": "茨城県", "09": "栃木県", "10": "群馬県",
    "11": "埼玉県", "12": "千葉県", "13": "東京都", "14": "神奈川県", "15": "新潟県",
    "16": "富山県", "17": "石川県", "18": "福井県", "19": "山梨県", "20": "長野県",
    "21": "岐阜県", "22": "静岡県", "23": "愛知県", "24": "三重県", "25": "滋賀県",
    "26": "京都府", "27": "大阪府", "28": "兵庫県", "29": "奈良県", "30": "和歌山県",
    "31": "鳥取県", "32": "島根県", "33": "岡山県", "34": "広島県", "35": "山口県",
    "36": "徳島県", "37": "香川県", "38": "愛媛県", "39": "高知県", "40": "福岡県",
    "41": "佐賀県", "42": "長崎県", "43": "熊本県", "44": "大分県", "45": "宮崎県",
    "46": "鹿児島県", "47": "沖縄県",
}
_PREF_RE = "(?:" + "|".join(PREFECTURES.values()) + ")"

# 神奈川県 主要市区町村コード（西湘・足柄中心＋主要市）
KANAGAWA_CITY_CODES = {
    "横須賀市": "14201", "平塚市": "14203", "鎌倉市": "14204", "藤沢市": "14205",
    "小田原市": "14206", "茅ヶ崎市": "14207", "逗子市": "14208", "三浦市": "14210",
    "秦野市": "14211", "厚木市": "14212", "大和市": "14213", "伊勢原市": "14214",
    "海老名市": "14215", "座間市": "14216", "南足柄市": "14217", "綾瀬市": "14218",
    "葉山町": "14301", "寒川町": "14321", "大磯町": "14341", "二宮町": "14342",
    "中井町": "14361", "大井町": "14362", "松田町": "14363", "山北町": "14364",
    "開成町": "14366", "箱根町": "14382", "真鶴町": "14384", "湯河原町": "14385",
    "愛川町": "14401", "清川村": "14402",
}


# 部首を漢字に戻したあと、なお日本語の字体に直したいもの。
#
# NFKC は康熙部首（U+2F00〜）をふつうの漢字に開いてくれるが、開いた先が
# 旧字体になることがある。「⼾」は 戸 ではなく 戶 になる。「売⼾建住宅」が
# 「売戶建住宅」のままだと、種別の判定が戸建に当たらない。
#
# CJK部首補助（U+2E80〜）のうち、1字として通用する形は NFKC が何もしない
# ので、こちらで対応させる。実物の図面に「温⽔⻄」（⻄ は U+2EC4）が
# 入っていた。
_KANJI_FIX = {
    # NFKCが旧字体に開くもの
    "戶": "戸", "靑": "青", "黃": "黄", "麥": "麦",
    "齊": "斉", "齒": "歯", "龜": "亀", "黑": "黒",
    # NFKCが手を付けないCJK部首補助（1字として通用する形だけ）
    "⺠": "民", "⻄": "西", "⻑": "長", "⻒": "長", "⻗": "雨", "⻘": "青",
    "⻝": "食", "⻞": "食", "⻟": "食", "⻡": "首", "⻣": "骨", "⻤": "鬼",
    "⻨": "麦", "⻩": "黄", "⻫": "斉", "⻭": "歯", "⻯": "竜", "⻲": "亀",
}
_KANJI_FIX_RE = re.compile("|".join(map(re.escape, _KANJI_FIX)))


def _despace(s: str) -> str:
    """均等割付で開いた字間を詰める。

    販売図面はセル幅に合わせてラベルを引き伸ばすことが多く、PDFから
    取り出すと「土 地 面 積 1 4 7 . 0 7 m 2」のように1文字ずつ空く。
    このままでは、どのラベルにも一致しない。

    1文字のかたまりが3つ以上続いたときだけ字間とみなしてつなげる。
    「洋室 6.0帖」のように単語として分かれているものには触らない。
    """
    out = []
    for line in s.replace("\u3000", " ").split("\n"):
        buf, run = [], []

        def flush():
            # 3つ以上続いた1文字は字間。それ未満は元の区切りを保つ。
            buf.extend(["".join(run)] if len(run) >= 3 else run)
            run.clear()

        for tok in line.split(" "):
            if tok == "":
                continue
            if len(tok) == 1:
                run.append(tok)
                continue
            flush()
            buf.append(tok)
        flush()
        out.append(" ".join(buf))
    return "\n".join(out)


def _normalize(text: str) -> str:
    """解析にかける前の下ごしらえ。

    NFKC で、全角英数を半角に、㎡ を m2 に、そして康熙部首をふつうの漢字に
    開く。PDFのフォントによっては漢字が部首の符号位置で入ってくることが
    あり、そのままではどのラベルにも一致しない。開ききらない字と旧字体は
    _KANJI_FIX で直し、最後に均等割付の字間をほどく。
    """
    t = unicodedata.normalize("NFKC", text or "")
    t = _KANJI_FIX_RE.sub(lambda m: _KANJI_FIX[m.group(0)], t)
    return _despace(t)


# 価格の欄を指す言い方。図面は「価　格」と割り付けることもあるが、
# _despace が字間をほどいたあとに見るので、ここでは詰めた形だけを持つ。
_PRICE_LABELS = r"(?:販売価格|物件価格|売出価格|売買価格|価格|価額)"

# 資金計画・諸費用の欄。ここに出てくる「○○万円」は売出価格ではない。
_MONEY_NOISE = ("自己資金", "頭金", "借入", "融資", "返済", "月々", "月額",
                "諸費用", "ローン", "手数料", "年収", "予算", "お支払",
                "支払例", "管理費", "修繕", "礼金", "敷金", "税", "値引")


def _price_in(t: str) -> Optional[int]:
    """文字列から『万円』単位の金額をひとつ読む。億対応。"""
    m = re.search(r"([0-9]+)\s*億\s*([0-9,]+)?\s*万?円", t)
    if m:
        oku = int(m.group(1))
        man = int((m.group(2) or "0").replace(",", "") or 0)
        return oku * 10000 + man
    m = re.search(r"([0-9,]{3,})\s*万円", t)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def _parse_price_man(t: str) -> Optional[int]:
    """売出価格を『万円』単位で返す。億対応。

    図面には資金計画（自己資金・借入・月々返済）や諸費用が併記される。
    単純に最初の「○○万円」を取ると、そちらを価格として読んでしまう。
    空欄になるより、もっともらしい誤った価格が入るほうが危ないので、
    価格の欄に紐づくものを最優先で読む。
    """
    lines = t.split("\n")
    # ① 「価格」欄に紐づく金額
    for line in lines:
        m = re.search(_PRICE_LABELS + r"[^\d]{0,10}[\d,億万円\s.]{2,20}", line)
        if m:
            v = _price_in(m.group(0))
            if v:
                return v
    # ② ラベルが無い図面・貼り付け向け。資金計画の行は見ない
    for line in lines:
        if any(w in line for w in _MONEY_NOISE):
            continue
        v = _price_in(line)
        if v:
            return v
    # ③ 価格だけ大きく組まれ、「販売」「価格」「万円」が縦に並び、数字だけが
    #    離れて置かれる図面がある。数字が説明文の行に紛れることもあるので、
    #    行そのものではなく、桁区切りの数字を1つずつ見る。
    #    面積・距離・金額（円）に付く数字は除く。残るのはほぼ価格しかない。
    if "価格" in t or "万円" in t:
        for m in re.finditer(r"(?<![\d,])([0-9]{1,3}(?:,[0-9]{3})+)(?![\d,])", t):
            after = t[m.end():m.end() + 4]
            if re.match(r"\s*(?:円|[mｍ]|平米|㎡|坪|年|月|%|人|戸|番|号)", after):
                continue
            v = int(m.group(1).replace(",", ""))
            if 100 <= v <= 99_999:          # 万円として現実的な範囲
                return v
    # ④ どの行も資金計画に見える場合の最後の手段
    return _price_in(t)


_UNIT = r"(㎡|m²|m2|ｍ2|平米|平方メートル|坪)"


def _area_of(m) -> float:
    val = float(m.group(1))
    if m.group(2) == "坪":
        val = round(val * TSUBO, 2)
    return val


_NUM_UNIT = r"([0-9]{1,4}(?:\.[0-9]+)?)\s*" + _UNIT


# 面積の見出しに使われる語。一覧表で「何列目か」を数えるのに使う。
_AREA_LABELS = r"(?:土地面積|敷地面積|建物面積|延床面積|延べ床面積|専有面積)"


def _label_re(lab: str):
    """ラベルの途中で行が変わっていても拾う正規表現。

    図面は項目を●で連ねて折り返すので、「●建」で行が終わり次の行が
    「物面積／103.68㎡」から始まることがある。縦組みの見出しが
    「⼟／地」と割れるのも同じ形。文字と文字の間に、改行を1つまで許す。
    """
    gap = r"[^\S\n]*\n?[^\S\n]*"
    return re.compile(gap.join(map(re.escape, lab)))


def _label_re_across(lab: str):
    """折り返した見出しの間に、隣の段の1行が挟まっていても拾う。

    2段組の図面をテキストにすると、左段の折り返しの間に右段の1行が
    入り込む。「●建」で行が終わり、次の行は右段の説明文、その次の行が
    「物面積／103.68㎡」から始まる、という並びになる。

    見出しの途中に何でも1行はさめる形なので当たりやすい。3文字以上の
    見出しに限り、他の読み方が全部外れたときの最後の手段として使う。
    """
    gap = r"[^\S\n]*(?:\n[^\n]*)?\n?[^\S\n]*"
    return re.compile(gap.join(map(re.escape, lab)))


def _parse_area(t: str, labels) -> Optional[float]:
    """面積を㎡で返す。ラベルと同じ行を先に見る。

    販売図面は左に間取り図、右に物件概要という作りが多い。テキストに
    すると図面の寸法（3.640 など）がラベルと値の間に入り込むので、
    「ラベルから数字までは12字以内」といった距離では読めなくなる。
    単位（㎡・坪）が付いた数字だけを、その行の中から拾う。

    候補が複数あるときは㎡を優先する。図面の見出しは「土地面積162坪超」の
    ように坪で丸めて書かれることがあり、同じ図面の中に正確な
    「敷地面積／538.69㎡」がある。丸めたほうを採ると面積がずれる。
    """
    lines = t.split("\n")
    starts = _line_starts(t)
    hits = _collect(t, lines, starts, labels, _label_re)
    if not hits:
        hits = _collect(t, lines, starts, [l for l in labels if len(l) >= 3],
                        _label_re_across)
    for want_m2 in (True, False):
        for is_m2, val in hits:
            if is_m2 == want_m2:
                return val
    return None


def _collect(t, lines, starts, labels, make_re):
    """ラベルごとに面積の候補を集める。[(㎡か, 値)] をラベルの優先順に。"""
    hits = []
    for lab in labels:
        for lm in make_re(lab).finditer(t):
            eol = t.find("\n", lm.end())
            rest = t[lm.end():eol if eol >= 0 else len(t)]
            m = re.search(_NUM_UNIT, rest)
            if m:
                hits.append((m.group(2) != "坪", _area_of(m)))
                continue
            # ラベルの終わりがどの行にあるかで、次の行を決める
            i = _line_of(starts, lm.end())
            j = max(0, lm.start() - starts[i])
            v = _from_next_line(lines, i, lines[i], j)
            if v is not None:
                hits.append(v)
    return hits


def _line_starts(t: str):
    """各行の先頭が、文字列全体の何文字目かを並べたもの。"""
    out, pos = [0], t.find("\n")
    while pos >= 0:
        out.append(pos + 1)
        pos = t.find("\n", pos + 1)
    return out


def _line_of(starts, pos: int) -> int:
    import bisect
    return bisect.bisect_right(starts, pos) - 1


def _from_next_line(lines, i, line, j) -> Optional[tuple]:
    """ラベルの行に値が無いとき、次の行から拾う。

    表のセルが狭いとラベルと値が改行で切れる。図面の下段には
    「間取り 建物面積 築年月 備考」という見出しの行と、その値を並べた行が
    続く形もある。後者では、見出しの行に自分より前にある面積の見出しを
    数えて、値の行でも同じ番号の数字を採る。列がずれない。
    """
    if i + 1 >= len(lines):
        return None
    nxt = lines[i + 1]
    if not re.search(r"\d", line):
        # 見出しだけの行。値は次の行に、同じ順で並んでいる
        heads = len(re.findall(_AREA_LABELS, line))
        found = list(re.finditer(_NUM_UNIT, nxt))
        # 見出しの数と値の数が合わないなら、その行は値の行ではない。
        # 「建 建物面積」の次が「1階 48.84㎡ 2階 48.51㎡」という内訳の
        # ことがあり、そこから採ると1階の面積を建物面積にしてしまう。
        if heads != len(found):
            return None
        col = len(re.findall(_AREA_LABELS, line[:j]))
        if col < len(found):
            m = found[col]
            return (m.group(2) != "坪", _area_of(m))
        return None
    for cand in lines[i + 1:i + 3]:
        m = re.match(r"[^\d]{0,8}?" + _NUM_UNIT, cand)
        if m:
            return (m.group(2) != "坪", _area_of(m))
    return None


# リフォームは「済み」と「可・要」で意味が逆になる。取り違えると価格の
# 上乗せまで狂うので、済みを表す言い方を先に見て、無ければ否定形を見る。
_RENO_DONE = ("リフォーム済", "リノベーション済", "リノベ済", "フルリフォーム",
              "フルリノベ", "改装済", "内装リフォーム",
              "リフォーム完了", "リノベーション完了", "改装完了")
_RENO_NOT = ("リフォーム可", "要リフォーム", "リフォーム前", "リフォーム相談",
             "リフォーム不可", "リノベーション可")


def _parse_renovated(t: str) -> Optional[bool]:
    """リフォーム済みか。記載が無ければ None（不明のまま返す）。"""
    for w in _RENO_DONE:
        if w in t:
            return True
    for w in _RENO_NOT:
        if w in t:
            return False
    return None


def _parse_structure(t: str) -> Optional[str]:
    """構造の記載を拾う。表記ゆれの吸収は src/structure.py にまかせる。

    「構造：〇〇」の形を先に見て、無ければ本文全体から構造名を探す。
    「鉄骨造」とだけ書かれている場合は軽量として読む（structure.py の
    説明のとおり、判断材料が無いときに評価を甘くしないため）。
    読み取った値はフォームで確認・修正できる。
    """
    from .structure import normalize
    m = re.search(r"構\s*造[^\S\n]*[：:｜|]?[^\S\n]*([^\n、,／/]{1,20})", t)
    if m:
        key = normalize(m.group(1))
        if key:
            return key
    # 「構造」の見出しが無く、本文中に構造名だけがある図面も多い
    return normalize(t)


def _parse_layout(t: str) -> Optional[str]:
    m = re.search(r"([1-9][0-9]?)\s*(S?LDK|S?DK|S?K|LDK|DK)", t)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return None


# 元号の元年に対応する西暦から1引いた数。令和1年=2019、平成1年=1989、
# 昭和1年=1926。
_ERA_BASE = {"令和": 2018, "平成": 1988, "昭和": 1925}
_ERA = "(令和|平成|昭和)"
_BUILD_LABEL = r"(?:築年月|建築年月|完成年月|完成時期|竣工年月|築年|建築年|入居)"


def _parse_build_year(t: str) -> Optional[int]:
    """築年を西暦で返す。

    販売図面の築年は和暦で書かれることのほうが多い。以前は和暦を最後の
    手段にしていたので、本文のどこかにある別の和暦を先に拾っていた。
    リフォームの完了月や写真の撮影月が令和で書かれていると、そちらを
    築年として読んでしまう。現在に近い年になるので、間違いだと気づき
    にくいうえ、築浅として採点されてしまう。

    順番は、築年月の欄（西暦→和暦）、築に隣り合う年（和暦→西暦）、
    築N年、最後に本文のどこかの和暦。
    """
    import datetime
    cy = datetime.date.today().year

    def era(m, i=1):
        return _ERA_BASE[m.group(i)] + int(m.group(i + 1))

    # ① 築年月の欄。西暦でも和暦でも、まずここを見る
    m = re.search(_BUILD_LABEL + r"[^\d]{0,10}?((?:19|20)\d{2})\s*年", t)
    if m:
        return int(m.group(1))
    m = re.search(_BUILD_LABEL + r"[^\d]{0,10}?" + _ERA + r"\s*([0-9]{1,2})\s*年", t)
    if m:
        return era(m)

    # ② 「平成27年12月築」「2015年築」のように、築に隣り合うもの
    m = re.search(_ERA + r"\s*([0-9]{1,2})\s*年\s*[0-9]{0,2}\s*月?\s*"
                  r"(?:築|新築|建築|完成|竣工)", t)
    if m:
        return era(m)
    for pat in (r"((?:19|20)\d{2})\s*年\s*\d{0,2}\s*月?\s*"
                r"(?:築|新築|建築|完成|竣工|引渡)",
                r"(?:築|新築|建築)\s*((?:19|20)\d{2})\s*年",
                r"((?:19|20)\d{2})\s*年\s*築"):
        m = re.search(pat, t)
        if m:
            return int(m.group(1))

    # ③ 築N年
    m = re.search(r"築\s*([0-9]{1,2})\s*年", t)
    if m:
        return cy - int(m.group(1))

    # ④ 最後の手段。本文のどこかの和暦なので、築年とは限らない
    m = re.search(_ERA + r"\s*([0-9]{1,2})\s*年", t)
    if m:
        return era(m)
    return None


_WALK = r"(?:徒歩|歩)\s*(?:約)?\s*([0-9]{1,3})\s*分"


def _parse_station_walk(t: str):
    """駅（またはバス停）までの徒歩分と、駅名。

    図面には周辺環境として「○○小学校 徒歩8分」「スーパー 徒歩4分」が
    並ぶ。単純に最初の「徒歩○分」を取ると、駅徒歩としてそちらを読んで
    しまい、実際より駅に近い物件として採点される。まず「駅」または
    「バス停」に続く徒歩分を探し、無ければ従来どおり全体から拾う。
    """
    walk = None
    name = None
    for line in t.split("\n"):
        m = re.search(r"(?:駅|バス停)[^\n]{0,16}?" + _WALK, line)
        if m:
            walk = int(m.group(1))
            break
    if walk is None:
        # 交通の行が「駅」を含まない書き方（貼り付けテキストに多い）
        m = re.search(_WALK, t)
        if m:
            walk = int(m.group(1))
    m2 = re.search(r"([^" + _ADDR_STOP + r"]{1,14}駅)", t)
    if m2:
        name = m2.group(1)
    if walk is None:  # 距離(m)表記からの推定
        m3 = re.search(r"駅[^0-9]{0,10}?([0-9]{3,4})\s*m", t)
        if m3:
            walk = max(1, round(int(m3.group(1)) / 80.0))
    return walk, name


def _parse_bus(t: str) -> Optional[int]:
    """バス便：駅までのバス乗車分。バス表記が無ければ None。"""
    m = re.search(r"バス\s*(?:乗車|利用)?\s*(?:約)?\s*([0-9]{1,2})\s*分", t)
    if m:
        return int(m.group(1))
    return None


def _parse_ptype(t: str, byear: Optional[int]) -> Optional[str]:
    """種別判定。中古/新築の明示があればそれに従い、無ければ築年で判断。
    築年が記載されていない戸建は『新築』と判断する（実務ルール）。"""
    is_kodate = ("戸建" in t or "一戸建" in t)
    if "中古" in t:
        return "chuko_kodate"
    if "新築" in t:
        return "shinchiku_kodate"
    if is_kodate:
        # 築年無記名の戸建は新築とみなす
        return "chuko_kodate" if byear else "shinchiku_kodate"
    # 種別語が無くても築年が無ければ新築寄り
    return None if byear else "shinchiku_kodate"


# 住所の切れ目になる記号。図面は項目を■や◆で並べるので、そこで止めないと
# 「神奈川県秦野市南矢名■交通/小田急線…」が丸ごと住所になる。
_ADDR_STOP = r"\s　\n,、。／/｜|■●◆○◎▲△▼※【】「」『』（）()＜＞<>〒＝=＋+"


def _city_hit(scope: str):
    """本文に出てくる市区町村のうち、いちばん先に現れるもの。

    表の登録順で探すと、後ろに出てくる仲介業者の市区町村が先に当たる。
    """
    best = None
    for name, code in KANAGAWA_CITY_CODES.items():
        k = scope.find(name)
        if k >= 0 and (best is None or k < best[0]):
            best = (k, name, code)
    return best


def _parse_address(t: str):
    addr = None
    # ① 「所在地／〇〇」の欄。都道府県から書かれていないことがあるので、
    #    ここで拾えた文字列は市区町村名でも受ける。
    m = re.search(r"所在地[^\S\n]*[／/：:]?[^\S\n]*([^" + _ADDR_STOP + r"]+)", t)
    if m and (re.match(_PREF_RE, m.group(1)) or _city_hit(m.group(1))):
        addr = m.group(1)
    # ② 都道府県から始まる住所
    if addr is None:
        m = re.search(r"(" + _PREF_RE + r"[^" + _ADDR_STOP + r"]+)", t)
        if m:
            addr = m.group(1)
    # ③ 都道府県が無い図面。市区町村名から始まる部分を住所として拾う。
    #    市区町村の表は神奈川県のものだけなので、県名を補ってよい。
    if addr is None:
        hit = _city_hit(t)
        if hit:
            m = re.search(re.escape(hit[1]) + r"[^" + _ADDR_STOP + r"]*", t)
            if m:
                addr = "神奈川県" + m.group(0)
    if addr and not re.match(_PREF_RE, addr):
        addr = "神奈川県" + addr
    # 市区町村は、住所として読み取った範囲の中から探す。図面の隅には
    # 仲介業者の住所が入っており、本文全体から探すと、そちらの市区町村を
    # 物件の所在地として拾ってしまう。空欄になるより悪い。
    # 住所が取れなかったときだけ、本文全体を見る（貼り付けテキスト向け）。
    scope = addr or t
    city_code = None
    district = None
    hit = _city_hit(scope)
    if hit:
        _k, name, city_code = hit
        mm = re.search(re.escape(name) + r"([一-龥ぁ-んァ-ヶー]+)", scope)
        if mm:
            district = mm.group(1)[:8]
    return addr, city_code, district


def parse_listing_text(text: str) -> Dict[str, object]:
    """貼り付けテキストから物件項目を抽出。値が無いものは None。"""
    import datetime
    t = _normalize(text)
    walk, station_name = _parse_station_walk(t)
    addr, city, district = _parse_address(t)
    byear = _parse_build_year(t)
    ptype = _parse_ptype(t, byear)
    # 新築で築年が無ければ、築年＝現在年（築0年相当）を補完
    if ptype == "shinchiku_kodate" and byear is None:
        byear = datetime.date.today().year
    return {
        "price_man": _parse_price_man(t),
        # 図面では見出しが縦組みで割れ、値の側に「公簿 182.99㎡」とだけ
        # 残ることがある。土地の面積にしか使われない語なので後ろに置く。
        "land": _parse_area(t, ["土地面積", "敷地面積", "土地", "公簿", "実測"]),
        # 「延 97.35㎡」と、延床の値だけが見出しの手前に置かれる図面がある。
        # 建物にしか使わない語だが、範囲が広いので最後に置く。
        "building": _parse_area(t, ["建物面積", "延床面積", "延べ床面積",
                                    "建物", "延"]),
        "layout": _parse_layout(t),
        "byear": byear,
        "station": walk,
        "station_name": station_name,
        "bus": _parse_bus(t),
        "ptype": ptype,
        "renovated": _parse_renovated(t),
        "structure": _parse_structure(t),
        "address": addr,
        "city": city,
        "district": district,
    }


# ---- マンション向けの読み取り ----------------------------------------
# 戸建と共通の部品（価格・住所・築年・駅徒歩・間取り・面積）はそのまま使い、
# マンション固有の項目だけここに足す。

DIRECTIONS_8 = ("南東", "南西", "北東", "北西", "南", "北", "東", "西")


def _parse_monthly_yen(t: str, labels, exclude=()) -> Optional[int]:
    """「管理費 12,000円」のような月額を円で拾う。

    価格は万円だがこれらは円で書かれるので、万円のパーサとは分けてある。

    判定は行単位で行う。前後の行まで覗くと、「修繕積立金」の隣にある
    「修繕積立基金」や、別項目の「駐車場の管理費」を巻き込んで、正しい
    月額まで捨ててしまう。ただしPDFではラベルと金額が改行で切れることが
    あるので、値は次の行までを見る。

    exclude は、その行にあれば別物とみなす語（駐車場の管理費、購入時の
    一括金である修繕積立基金など）。
    """
    lines = t.splitlines()
    for lab in labels:
        for i, line in enumerate(lines):
            if lab not in line or any(x in line for x in exclude):
                continue
            segment = line + " " + (lines[i + 1] if i + 1 < len(lines) else "")
            m = re.search(re.escape(lab) + r"[^\d]{0,24}?([0-9][0-9,]{2,8})\s*円",
                          segment)
            if m:
                try:
                    return int(m.group(1).replace(",", ""))
                except ValueError:
                    continue
    return None

def _parse_floors(t: str):
    """所在階と総階数。「3階/5階建」のような並記を最優先で読む。

    「5階建」だけなら総階数、「所在階3階」なら所在階。どちらか分からない
    「3階」単独は、取り違えると評価が変わるので拾わない。
    """
    floor = total = None
    m = re.search(r"([0-9]{1,2})\s*階\s*[/／]\s*(?:地上)?([0-9]{1,2})\s*階建", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"所在階[^\d]{0,8}?([0-9]{1,2})\s*階", t)
    if m:
        floor = int(m.group(1))
    m = re.search(r"(?:地上)?([0-9]{1,2})\s*階建", t)
    if m:
        total = int(m.group(1))
    if floor is None:
        m = re.search(r"([0-9]{1,2})\s*階\s*部分", t)
        if m:
            floor = int(m.group(1))
    return floor, total


def _parse_direction(t: str) -> Optional[str]:
    """向き。2文字の方位（南東など）を先に見る。"""
    for d in DIRECTIONS_8:
        if re.search(re.escape(d) + r"\s*向き", t):
            return d
    m = re.search(r"(?:バルコニー|採光|開口部?)[^。\n]{0,12}?"
                  r"(南東|南西|北東|北西|南|北|東|西)", t)
    if m:
        return m.group(1)
    return None


def looks_like_mansion(t: str) -> Optional[bool]:
    """貼り付けたのがマンションの物件かどうか。判断できなければ None。

    戸建のページを貼ってしまったときに気づけるようにするためのもの。
    """
    t = _normalize(t)
    if re.search(r"マンション|区分所有|専有面積", t):
        return True
    if re.search(r"一戸建|戸建|土地面積|建ぺい率", t):
        return False
    return None


def parse_mansion_text(text: str) -> Dict[str, object]:
    """貼り付けテキストからマンションの項目を抽出。値が無いものは None。"""
    t = _normalize(text)
    walk, station_name = _parse_station_walk(t)
    addr, city, district = _parse_address(t)
    floor, total_floors = _parse_floors(t)
    return {
        "price_man": _parse_price_man(t),
        # バルコニーを拾わないよう、専有と明記されたものだけを見る
        "area": _parse_area(t, ["専有面積", "専有部分の面積", "専有"]),
        "layout": _parse_layout(t),
        "byear": _parse_build_year(t),
        "station": walk,
        "station_name": station_name,
        "bus": _parse_bus(t),
        "floor": floor,
        "total_floors": total_floors,
        "direction": _parse_direction(t),
        # 「修繕積立基金」は購入時の一括金なので月額と混ぜない
        "renovated": _parse_renovated(t),
        "mfee": _parse_monthly_yen(t, ["管理費"],
                                   exclude=("駐車", "駐輪", "バイク", "トランク")),
        "rfund": _parse_monthly_yen(t, ["修繕積立金", "修繕費"],
                                    exclude=("基金", "一時金", "駐車")),
        "address": addr,
        "city": city,
        "district": district,
        "is_mansion": looks_like_mansion(text),
    }


def extract_from_url(url: str) -> Dict[str, object]:
    """URLのページを取得しテキスト抽出→parse（実験的・best-effort）。
    公開メタ情報とページテキストのみを解析する。"""
    import requests
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JutakuShindan/0.1)"}
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    html = r.text
    # JSON-LD / og:title / description を軽く拾ってテキストに合流
    extra = " ".join(re.findall(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', html,
                                re.S))
    metas = " ".join(re.findall(r'<meta[^>]+content="([^"]+)"', html))
    # タグ除去してテキスト化
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return parse_listing_text(" ".join([extra, metas, text]))

# -*- coding: utf-8 -*-
"""注文住宅の資金の流れ ── つなぎ融資と、総額の積み上げ。

■なぜ要るか
建売やマンションは、決済の日に住宅ローンが実行されて、その日に終わる。
注文住宅は終わらない。

  土地の決済（半年〜1年前）→ 着工金 → 上棟時の中間金 → 完成・引渡し

住宅ローンは**建物が完成しないと実行されない**（建物が無いと担保として
評価できず、抵当権も設定できない）。だから土地代金と工事の前払い金を、
その間ずっと立て替える必要がある。これがつなぎ融資。

つなぎ融資は本融資が実行される日に一括で返す。それまでは**利息だけ**払う。

  利息 = 実行額 × 年利 × 日数 ÷ 365

この利息を、誰も先に教えてくれない。土地を決済してから「毎月いくら引かれ
るんですか」と聞く人が多い。ここを先に出すのが、このモジュールの仕事。

■出さないもの
つなぎ融資の事務手数料・印紙代・抵当権設定費用は、金融機関ごとに違う。
一次情報を持っていないので**金額を出さない**（status=unknown で名前だけ出す）。
地盤改良・解体・引き込みも同じ。docs/土地PRO_設計.md に調べた範囲がある。

■前提を隠さない
支払いの割合（着工金30%・中間金30%・残金40%）は一般的な例にすぎず、
実際は請負契約書で決まる。既定値を使ったときは必ずそう書く。
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import List, Optional

from .finance import CostItem, ESTIMATED, COMPUTED, UNKNOWN, man_yen

# 日割りの分母。つなぎ融資は日割りが普通。
DAYS_PER_YEAR = 365

# 請負代金の支払い割合の既定値。**一般的な例であって、決まりではない。**
# 使ったときは必ず「請負契約書で確認してください」と添えること。
DEFAULT_START_PCT = 30      # 着工金
DEFAULT_FRAME_PCT = 30      # 上棟時の中間金
# 残りが引渡し時。住宅ローン本体がここで実行される。


def _d(s) -> Optional[datetime.date]:
    """YYYY-MM-DD を date に。読めなければ None。"""
    if isinstance(s, datetime.date):
        return s
    if not s:
        return None
    try:
        y, m, d = str(s).strip().split("-")
        return datetime.date(int(y), int(m), int(d))
    except Exception:
        return None


@dataclass
class Drawdown:
    """つなぎ融資の1回の実行。"""
    name: str
    date: datetime.date
    amount: int                 # 立て替える額（自己資金を引いたあと）
    days: int                   # 本融資が実行される日までの日数
    interest: int


@dataclass
class BridgeLoan:
    """つなぎ融資の全体。"""
    rate_pct: float = 0.0
    drawdowns: List[Drawdown] = field(default_factory=list)
    principal: int = 0          # 実行の合計（＝本融資実行日に返す額）
    interest: int = 0           # 期間中に払う利息の合計
    days: int = 0               # 土地決済から本融資実行までの日数
    notes: List[str] = field(default_factory=list)
    assumed: List[str] = field(default_factory=list)   # 既定値を使った項目


def bridge_loan(land_price: int,
                building_price: int,
                settlement: Optional[str] = None,
                start: Optional[str] = None,
                framing: Optional[str] = None,
                completion: Optional[str] = None,
                rate_pct: float = 0.0,
                own_funds: int = 0,
                start_pct: Optional[int] = None,
                framing_pct: Optional[int] = None) -> BridgeLoan:
    """つなぎ融資の実行と利息。分からないものは埋めない。

    own_funds は、つなぎ融資ではなく現金で払う分。土地代金から先に充てる
    （手付金と決済の頭金は土地に入るのが普通のため）。
    """
    b = BridgeLoan(rate_pct=rate_pct)
    sdate, cdate = _d(settlement), _d(completion)
    if not sdate or not cdate:
        b.notes.append("土地の決済予定日と建物の完成予定日を入れると、"
                       "つなぎ融資の利息を計算できます")
        return b
    if cdate <= sdate:
        b.notes.append("建物の完成予定日が、土地の決済予定日より前になっています")
        return b

    b.days = (cdate - sdate).days

    sp = start_pct if start_pct is not None else DEFAULT_START_PCT
    fp = framing_pct if framing_pct is not None else DEFAULT_FRAME_PCT
    if start_pct is None or framing_pct is None:
        b.assumed.append(
            f"工事代金の支払いを 着工時{sp}%・上棟時{fp}%・"
            f"引渡し時{100 - sp - fp}% として計算しています。"
            "一般的な例であって決まりではないので、請負契約書でご確認ください")
    if sp + fp > 100:
        b.notes.append("着工金と中間金の合計が100%を超えています")
        return b

    # 着工日・上棟日が未入力なら、決済〜完成の間に置く。推定であることは出す。
    idate = _d(start)
    fdate = _d(framing)
    if idate is None:
        idate = sdate + datetime.timedelta(days=int(b.days * 0.35))
        b.assumed.append("着工日が未入力のため、土地決済から完成までの"
                         "3分の1あたりと仮に置いています")
    if fdate is None:
        fdate = sdate + datetime.timedelta(days=int(b.days * 0.65))
        b.assumed.append("上棟日が未入力のため、土地決済から完成までの"
                         "3分の2あたりと仮に置いています")

    plan = [("土地代金（つなぎ融資で立て替える分）", sdate, int(land_price or 0)),
            ("着工金", idate, int((building_price or 0) * sp / 100)),
            ("上棟時の中間金", fdate, int((building_price or 0) * fp / 100))]

    # 自己資金は、日付の早いものから充てる（利息が乗る期間が長いため）。
    left = max(0, int(own_funds or 0))
    for name, when, amount in plan:
        if amount <= 0:
            continue
        use = min(left, amount)
        left -= use
        financed = amount - use
        if financed <= 0:
            continue
        when = max(when, sdate)
        when = min(when, cdate)
        days = (cdate - when).days
        interest = int(round(financed * (rate_pct / 100.0) * days / DAYS_PER_YEAR))
        b.drawdowns.append(Drawdown(name, when, financed, days, interest))

    b.principal = sum(x.amount for x in b.drawdowns)
    b.interest = sum(x.interest for x in b.drawdowns)
    if rate_pct <= 0:
        b.notes.append("つなぎ融資の金利が未入力のため、利息は0円として"
                       "います。実際には本融資より高い金利がつくのが普通です")
    return b


# ---- 総額の積み上げ ----
@dataclass
class LandTotal:
    """注文住宅の総額。分からない項目は名前だけ残す。"""
    items: List[CostItem] = field(default_factory=list)
    total: int = 0                      # 金額が出た項目の合計
    unknown: List[str] = field(default_factory=list)


# 金額を出せない費目。一次情報が無いので、レンジを書かない。
# docs/土地PRO_設計.md の第7章に、探した範囲を残してある。
NO_SOURCE_ITEMS = [
    ("地盤改良", "地盤調査をするまで金額が決まりません。"
                 "公的な費用統計が無いため、目安の金額も出しません"),
    ("古家の解体", "見積りを取るまで金額が決まりません"),
    ("上下水道・ガスの引き込み", "本管の位置と口径で変わります。"
                                "市区町村の水道課とガス事業者で確認できます"),
]


def land_total(land_price: int,
               building_price: int,
               extra_work: Optional[int] = None,
               exterior: Optional[int] = None,
               other_costs: Optional[int] = None,
               bridge_interest: Optional[int] = None,
               needs: Optional[dict] = None) -> LandTotal:
    """土地＋建物＋付帯＋外構＋諸費用＋つなぎ利息。

    needs は {"地盤改良": True/False/None} のような、要る／要らない／不明。
    要ると分かっているのに金額が出せない項目は、0円として足さず、
    「金額の出せない項目」として名前を残す。合計に混ぜない。
    """
    t = LandTotal()
    t.items.append(CostItem("土地の価格", int(land_price or 0),
                            "入力された金額", COMPUTED))
    t.items.append(CostItem("建物の本体工事費", int(building_price or 0),
                            "入力された予算", COMPUTED))
    if extra_work is not None:
        t.items.append(CostItem("付帯工事費", int(extra_work),
                                "入力された予算", COMPUTED))
    else:
        t.items.append(CostItem(
            "付帯工事費", None,
            "本体工事に含まれない工事（給排水の引き込み、電気、空調、"
            "解体、地盤改良など）。請負契約書の内訳で確認できます", UNKNOWN))
    if exterior is not None:
        t.items.append(CostItem("外構工事費", int(exterior),
                                "入力された予算", COMPUTED))
    else:
        t.items.append(CostItem("外構工事費", None,
                                "駐車場・門・塀・植栽。本体工事に含まれない"
                                "ことがほとんどです", UNKNOWN))
    if other_costs is not None:
        t.items.append(CostItem("諸費用", int(other_costs),
                                "入力された予算", COMPUTED))
    if bridge_interest:
        t.items.append(CostItem("つなぎ融資の利息", int(bridge_interest),
                                "実行額×年利×日数÷365で計算", COMPUTED))

    needs = needs or {}
    for name, why in NO_SOURCE_ITEMS:
        want = needs.get(name)
        if want is False:
            continue
        label = name if want else f"{name}（要否も未確認）"
        t.items.append(CostItem(label, None, why, UNKNOWN))

    t.total = sum(i.amount for i in t.items if i.amount)
    t.unknown = [i.name for i in t.items if i.amount is None]
    return t


@dataclass
class CashEvent:
    """いつ・いくら要るか。手元から出るお金だけを並べる。"""
    date: Optional[datetime.date]
    name: str
    amount: Optional[int]
    source: str                 # 自己資金 / つなぎ融資 / 住宅ローン
    note: str = ""


def cash_timeline(bridge: BridgeLoan,
                  land_price: int,
                  building_price: int,
                  own_funds: int = 0,
                  deposit: Optional[int] = None,
                  contract_date: Optional[str] = None,
                  settlement: Optional[str] = None,
                  completion: Optional[str] = None) -> List[CashEvent]:
    """支払いの時系列。**手付金は現金**であることを必ず出す。

    手付金は融資が実行される前に払う。ここを知らずに「頭金ゼロ」と
    考えている人が、契約の直前で詰まる。
    """
    out: List[CashEvent] = []
    if deposit:
        out.append(CashEvent(_d(contract_date), "土地の手付金", int(deposit),
                             "自己資金",
                             "融資の実行前に払うので、現金が要ります"))
    for x in bridge.drawdowns:
        out.append(CashEvent(x.date, x.name, x.amount, "つなぎ融資",
                             f"完成まで{x.days}日・利息 {man_yen(x.interest)}"))
    used = max(0, int(own_funds or 0))
    if used:
        out.append(CashEvent(_d(settlement), "土地代金のうち、自己資金で払う分",
                             used, "自己資金",
                             "この分だけ、つなぎ融資の元本と利息が減ります"))
    cdate = _d(completion)
    # 建物のうち、着工金・中間金で先に払った分を引いた残り。
    # 名前の一致で拾うと、表示名を変えたときに静かに壊れる。土地の実行は
    # 必ず先頭なので、2件目以降を建物の前払いとして扱う。
    rest = max(0, int(building_price or 0)
               - sum(x.amount for x in bridge.drawdowns[1:]))
    out.append(CashEvent(cdate, "引渡し時の残金と、つなぎ融資の一括返済",
                         (rest + bridge.principal) or None, "住宅ローン",
                         "ここで住宅ローンが実行されます"))
    out = [e for e in out if e.amount]
    # 日付順に並べる。日付が入っていないものは最後に回す（前後が言えないため）。
    out.sort(key=lambda e: (e.date is None, e.date or datetime.date.max))
    return out

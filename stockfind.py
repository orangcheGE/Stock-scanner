import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import time
import re
import io
import urllib.parse
from datetime import datetime

# ─────────────────────────────────────────────
# 헬퍼 함수
# ─────────────────────────────────────────────

def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://finance.naver.com/'
    }

def get_market_sum_pages(page_list, market="KOSPI"):
    sosok = 0 if market == "KOSPI" else 1
    codes, names, changes = [], [], []
    for page in page_list:
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        try:
            res = requests.get(url, headers=get_headers(), timeout=10)
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.select_one('table.type_2')
            if not table:
                continue
            for tr in table.select('tr'):
                tds = tr.find_all('td')
                if len(tds) < 5:
                    continue
                a = tr.find('a', href=True)
                if not a:
                    continue
                match = re.search(r'code=(\d{6})', a['href'])
                if match:
                    codes.append(match.group(1))
                    names.append(a.get_text(strip=True))
                    changes.append(tds[4].get_text(strip=True))
            time.sleep(0.3)
        except:
            continue
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률': changes})


def get_price_data(code, max_pages=60):
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
    dfs = []
    for page in range(1, max_pages + 1):
        try:
            res = requests.get(f"{url}&page={page}", headers=get_headers(), timeout=10)
            df_list = pd.read_html(io.StringIO(res.text), encoding='euc-kr')
            if df_list:
                dfs.append(df_list[0])
        except:
            continue
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True).dropna(how='all')
    df = df.rename(columns=lambda x: x.strip())
    for col in ['종가', '고가', '저가', '시가', '거래량']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce')
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    return df.dropna(subset=['날짜', '종가']).sort_values('날짜').reset_index(drop=True)


def load_foreign_ratio_all(market="KOSPI", max_pages=40):
    sosok = "0" if market == "KOSPI" else "1"
    ratio_dict = {}
    base_url = f"https://finance.naver.com/sise/sise_foreign_hold.naver?sosok={sosok}"
    try:
        res = requests.get(f"{base_url}&page=1", headers=get_headers(), timeout=10)
        res.encoding = 'euc-kr'
        soup = BeautifulSoup(res.text, 'html.parser')
        pager = soup.select_one('td.pgRR a')
        if pager and 'page=' in pager.get('href', ''):
            m = re.search(r'page=(\d+)', pager['href'])
            total_pages = int(m.group(1)) if m else max_pages
        else:
            total_pages = max_pages
        total_pages = min(total_pages, max_pages)
        ratio_dict.update(_parse_foreign_page(soup))
        for page in range(2, total_pages + 1):
            try:
                r = requests.get(f"{base_url}&page={page}", headers=get_headers(), timeout=8)
                r.encoding = 'euc-kr'
                ratio_dict.update(_parse_foreign_page(BeautifulSoup(r.text, 'html.parser')))
                time.sleep(0.15)
            except:
                continue
    except:
        pass
    return ratio_dict


def _parse_foreign_page(soup):
    result = {}
    table = soup.select_one('table.type_2')
    if not table:
        return result
    for tr in table.select('tr'):
        tds = tr.find_all('td')
        if len(tds) < 8:
            continue
        a = tr.find('a', href=True)
        if not a:
            continue
        m = re.search(r'code=(\d{6})', a['href'])
        if not m:
            continue
        try:
            ratio = float(tds[7].get_text(strip=True).replace('%', '').replace(',', ''))
            result[m.group(1)] = ratio
        except:
            continue
    return result


def _fmt_ratio(ratio: float) -> str:
    if ratio >= 30:   return f"{ratio:.1f}% 🔴고비중"
    elif ratio >= 15: return f"{ratio:.1f}% 🟠중비중"
    elif ratio >= 5:  return f"{ratio:.1f}% 🟡저비중"
    else:             return f"{ratio:.1f}% ⚪미미"


# ─────────────────────────────────────────────
# 지표 계산
# ─────────────────────────────────────────────

def calc_cci(df, period=20):
    tp  = (df['고가'] + df['저가'] + df['종가']) / 3
    ma  = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - ma) / (0.015 * mad.replace(0, np.nan))


def calc_bollinger(series, period=20, std_mult=2):
    ma    = series.rolling(period).mean()
    std   = series.rolling(period).std()
    upper = ma + std_mult * std
    lower = ma - std_mult * std
    bw    = (upper - lower) / ma * 100
    return upper, lower, bw


def get_bb_squeeze(bw_series):
    recent = bw_series.iloc[-20:]
    cur    = bw_series.iloc[-1]
    if cur <= recent.quantile(0.20):
        return "⚡수축", True
    elif cur >= recent.quantile(0.80):
        return "💥팽창", False
    return "➖보통", False


# BUG FIX 1: get_ma_slope → 시리즈 전체에 rolling 적용 후 최신값만 반환
def get_ma5_slope_series(price_series):
    """
    5MA 기울기를 시리즈 전체에 대해 계산.
    반환: pd.Series (각 날짜의 기울기%)
    """
    ma5   = price_series.rolling(5).mean()
    slope = ma5 - ma5.shift(3)   # 3봉 기울기
    pct   = slope / ma5.shift(3) * 100
    return pct.fillna(0)


# ─────────────────────────────────────────────
# 매수 타이밍 보조 함수
# ─────────────────────────────────────────────

def detect_20ma_touch(df_final):
    """
    20MA 눌림목 터치 감지
    - 전일 20MA 아래 → 오늘 20MA 위  : 골든터치
    - 현재 20MA ±3% 이내 + 상승 중  : 근접
    - 최근 5일 내 터치 후 반등 중   : N일전 터치
    반환: (터치여부, 표시문자열)
    """
    try:
        last  = df_final.iloc[-1]
        prev  = df_final.iloc[-2]
        price = last['종가']
        ma20  = last['20MA']
        if ma20 <= 0:
            return False, f"이격{0:+.1f}%"
        disp = (price - ma20) / ma20 * 100
        prev_disp = (prev['종가'] - prev['20MA']) / prev['20MA'] * 100 if prev['20MA'] > 0 else 0

        # 골든터치: 어제 아래 → 오늘 위
        if prev_disp < 0 and disp >= 0:
            return True, "🎯20MA골든"

        # 현재 ±3% 이내 + 상승 중
        if -3 <= disp <= 3 and price >= prev['종가']:
            return True, f"🎯20MA근접({disp:+.1f}%)"

        # 최근 1~5일 내 터치 후 반등
        for d in range(1, 6):
            if len(df_final) < d + 2:
                break
            r      = df_final.iloc[-(d + 1)]
            r_disp = (r['종가'] - r['20MA']) / r['20MA'] * 100 if r['20MA'] > 0 else 0
            if -3 <= r_disp <= 3 and price > r['종가']:
                return True, f"🎯20MA+{d}일"

        return False, f"이격{disp:+.1f}%"
    except:
        return False, "-"


def detect_macd_turn(df_final, lookback=5):
    """
    MACD 히스토그램 음→양 전환 + 경과일
    반환: (전환여부, 표시문자열)
    """
    try:
        cur = df_final['MACD_hist'].iloc[-1]

        # 전환 경과일 계산
        if cur > 0:
            for d in range(1, lookback + 1):
                if len(df_final) < d + 2:
                    break
                prev_h = df_final['MACD_hist'].iloc[-(d + 1)]
                if prev_h <= 0:
                    label = "골든" if d == 1 else f"전환+{d-1}일"
                    return True, f"📊MACD{label}"
            return True, "📊MACD양수"

        # 음수지만 회복 중 (3봉 기울기 상승)
        if len(df_final) >= 4:
            slope = cur - df_final['MACD_hist'].iloc[-4]
            if slope > 0:
                return False, f"📊MACD회복({cur:.0f})"

        return False, f"📊MACD음({cur:.0f})"
    except:
        return False, "-"


def detect_cci_turn(df_final, lookback=5):
    """
    CCI 음→양 또는 -100 탈출 + 경과일
    반환: (전환여부, 표시문자열)
    """
    try:
        cur  = df_final['CCI'].iloc[-1]
        prev = df_final['CCI'].iloc[-2]

        # -100 탈출 (강반등)
        if prev < -100 and cur >= -100:
            return True, "📊CCI바닥탈출"

        # 0 크로스 상향 경과일
        if cur > 0:
            for d in range(1, lookback + 1):
                if len(df_final) < d + 2:
                    break
                p = df_final['CCI'].iloc[-(d + 1)]
                if p <= 0:
                    label = "제로돌파" if d == 1 else f"크로스+{d-1}일"
                    return True, f"📊CCI{label}"
            return True, "📊CCI양수"

        # 음수 회복 중
        if len(df_final) >= 4:
            slope = cur - df_final['CCI'].iloc[-4]
            if slope > 0:
                return False, f"📊CCI회복({cur:.0f})"

        return False, f"📊CCI음({cur:.0f})"
    except:
        return False, "-"


def detect_consecutive_candles(df_final, n=5):
    """
    연속 양봉/음봉 감지
    반환: (연속수_signed, 표시문자열)
    양봉=양수, 음봉=음수
    """
    try:
        # 시가 없으면 전일종가 대비 사용
        if '시가' in df_final.columns:
            recent = df_final.iloc[-n:]
            dirs = [1 if r['종가'] > r['시가'] else -1 for _, r in recent.iterrows()]
        else:
            closes = df_final['종가'].iloc[-n:]
            dirs = [1 if closes.iloc[i] > closes.iloc[i-1] else -1
                    for i in range(1, len(closes))]

        if not dirs:
            return 0, "➖"
        last_d = dirs[-1]
        count  = 1
        for d in reversed(dirs[:-1]):
            if d == last_d:
                count += 1
            else:
                break

        signed = count if last_d == 1 else -count
        if last_d == 1:
            tag = "🔴연속양봉" if count >= 4 else f"📈양봉{count}"
        else:
            tag = "🔵연속음봉" if count >= 4 else f"📉음봉{count}"
        return signed, tag
    except:
        return 0, "➖"


def calc_trading_amount(df_final, min_bil=30):
    """
    최근 5일 평균 거래대금 계산
    반환: (평균억원, 표시문자열, 충분여부)
    """
    try:
        last5  = df_final.iloc[-5:]
        avg    = (last5['종가'] * last5['거래량']).mean() / 1e8
        if avg >= 500:  disp = f"{avg:,.0f}억🔴"
        elif avg >= 100: disp = f"{avg:,.0f}억🟠"
        elif avg >= min_bil: disp = f"{avg:,.0f}억🟡"
        else:            disp = f"{avg:,.0f}억⚠️"
        return avg, disp, avg >= min_bil
    except:
        return 0, "-", False


def calc_weekly_ichimoku(df_daily):
    """
    일봉 데이터를 주봉으로 리샘플링 후 일목균형표 계산
    주봉 = 월요일 기준 주의 시가·고가·저가·종가·거래량 집계

    반환: 주봉 일목 상태 문자열
      📈W 구름대 위   : 주봉 종가 > 주봉 구름대 상단
      📉W 구름대 아래 : 주봉 종가 < 주봉 구름대 하단
      🌫️W 구름대 내부 : 그 사이
      🔥W 상향돌파    : 이전 주 구름대 아래 → 이번 주 위
      🧊W 하향이탈    : 이전 주 구름대 위  → 이번 주 아래
    """
    try:
        # ── 주봉 리샘플링 ──────────────────────────
        # W-FRI: 금요일 종가 기준으로 주 집계
        df = df_daily[['고가','저가','종가','거래량']].copy()
        df.index = pd.to_datetime(df.index)

        wk = df.resample('W-FRI').agg({
            '고가':   'max',
            '저가':   'min',
            '종가':   'last',
            '거래량': 'sum',
        }).dropna()

        if len(wk) < 90:   # 최소 90주(=약 450거래일, 35페이지) 필요
            return "W-데이터부족"

        # ── 주봉 일목균형표 ────────────────────────
        # 주봉 기간: 전환선9, 기준선26, 선행스팬B52
        h9  = wk['고가'].rolling(9).max();  l9  = wk['저가'].rolling(9).min()
        h26 = wk['고가'].rolling(26).max(); l26 = wk['저가'].rolling(26).min()
        h52 = wk['고가'].rolling(52).max(); l52 = wk['저가'].rolling(52).min()

        tenkan = (h9  + l9)  / 2
        kijun  = (h26 + l26) / 2
        senb   = (h52 + l52) / 2

        # 선행스팬 (행 기준 26주 앞)
        sa_fut = ((tenkan + kijun) / 2).shift(26)
        sb_fut = senb.shift(26)

        wk = wk.copy()
        wk['sa'] = sa_fut
        wk['sb'] = sb_fut

        # 현재 주(마지막 행)의 sa/sb 가 NaN이면 1주 전 값으로 채움
        # (선행스팬은 미래로 시프트되므로 최신 봉에서 바로 쓸 수 있음)
        wk_f = wk.dropna(subset=['sa', 'sb'])

        if len(wk_f) < 2:
            return "W-데이터부족"

        last_w = wk_f.iloc[-1]
        prev_w = wk_f.iloc[-2]

        ct_now  = max(last_w['sa'], last_w['sb'])
        cb_now  = min(last_w['sa'], last_w['sb'])
        ct_prev = max(prev_w['sa'], prev_w['sb'])
        cb_prev = min(prev_w['sa'], prev_w['sb'])

        p_now  = last_w['종가']
        p_prev = prev_w['종가']

        above_now  = p_now  > ct_now
        below_now  = p_now  < cb_now
        above_prev = p_prev > ct_prev
        below_prev = p_prev < cb_prev

        if above_now:
            if not above_prev:   return "🔥W상향돌파"   # 이번 주 새로 돌파
            else:                return "📈W구름대위"
        elif below_now:
            if above_prev:       return "🧊W하향이탈"   # 이번 주 새로 이탈
            else:                return "📉W구름대아래"
        else:
            return "🌫️W구름내부"

    except Exception:
        return "-"


# ─────────────────────────────────────────────
# 신호 결정 — 3단계 AND 게이트 + 6단계 신호
# ─────────────────────────────────────────────
#
# [설계]
# 1단계 자격  : 구름대 위 + 60MA 위   (추세 방향 확인)
# 2단계 타이밍: 20MA터치 + MACD전환 + CCI전환  (진입 시점)
# 3단계 확인  : 거래량방향 + 연속봉 + 외국인    (신뢰도 보조)
#
# MACD+CCI 통합: 둘 다 상승이면 max값, 엇갈리면 가중 합산
#   → 강한 신호가 약한 반대 신호에 묻히지 않도록
#
# BUG FIX 3: breakout 조건 = 이전날 구름대 하단 아래였어야 진짜 돌파

def calc_momentum_score(macd_now, macd_prev, cci_now, cci_prev):
    """
    MACD + CCI 통합 모멘텀 점수
    FIX: 엇갈릴 때도 가중 합산으로 소실 방지
    """
    # MACD 점수
    ms = macd_now - macd_prev  # 기울기
    if   macd_now > 0 and macd_prev <= 0:  s_m =  2
    elif macd_now < 0 and macd_prev >= 0:  s_m = -2
    elif macd_now < 0 and ms > 0:          s_m =  1
    elif macd_now > 0 and ms < 0:          s_m = -1
    else:                                  s_m =  0

    # CCI 점수
    if   cci_prev < -100 and cci_now >= -100: s_c =  2
    elif cci_prev <    0 and cci_now >=    0: s_c =  1
    elif cci_prev >    0 and cci_now <=    0: s_c = -1
    elif cci_prev >  100 and cci_now <=  100: s_c = -2
    else:                                     s_c =  0

    # 통합: 같은 방향이면 max, 엇갈리면 가중 합산 (강한 쪽 우선)
    if s_m > 0 and s_c > 0:
        return max(s_m, s_c)
    elif s_m < 0 and s_c < 0:
        return min(s_m, s_c)
    elif s_m != 0 and s_c != 0:
        # 엇갈림: 절댓값 큰 쪽을 0.7, 작은 쪽을 0.3으로 가중
        if abs(s_m) >= abs(s_c):
            return round(s_m * 0.7 + s_c * 0.3)
        else:
            return round(s_c * 0.7 + s_m * 0.3)
    else:
        return s_m + s_c


def decide_signal(ichimoku_status, score, disparity,
                  ma20_touch, macd_turn, cci_turn,
                  rsi_val, amount_ok, foreign_ratio,
                  vol_up, consec):
    """
    6단계 신호 결정
    """
    above  = "구름대 위"   in ichimoku_status or "상향돌파" in ichimoku_status
    below  = "구름대 아래" in ichimoku_status or "하향이탈" in ichimoku_status
    fall_e = "하락진입"    in ichimoku_status
    inside = "내부"        in ichimoku_status

    # --- 60MA 위 여부는 score에 반영 ---
    rsi_hot  = rsi_val >= 70
    high_d   = disparity > 15

    # 3단계 확인 보너스
    confirm = (1 if vol_up else 0) + (1 if consec > 0 else 0) + (1 if foreign_ratio >= 30 else 0)

    # ── 위험 (최우선) ──────────────────────────
    if fall_e:
        sig = "⚠️ 구름대주의"

    # ── 매도/하락 ─────────────────────────────
    elif below:
        if score <= -3:   sig = "🧊 적극매도"
        elif macd_turn or cci_turn: sig = "🔄 바닥탐색"
        else:             sig = "📉 추세하락"

    elif inside:
        sig = "🌫️ 구름대내부"

    # ── 구름대 위 ─────────────────────────────
    elif above:
        if rsi_hot or high_d:
            sig = "🔼 추세상승(과열)"
        else:
            turns = sum([ma20_touch, macd_turn, cci_turn])
            if turns >= 3:
                sig = "🎯 매수타이밍"
            elif turns == 2:
                sig = "📈 매수준비"
            elif turns == 1:
                sig = "🔔 관찰등록"
            else:
                sig = "🛡️ 홀딩"

        # RSI 경고 부기
        if rsi_hot and "타이밍" in sig:
            sig = sig.replace("🎯 매수타이밍", "🎯 매수타이밍(RSI과열)")
    else:
        sig = "⏸️ 관망"

    # 소형주 경고 (분석 유지, 표기만)
    if not amount_ok and any(k in sig for k in ["타이밍", "준비", "관찰"]):
        sig = f"⚠️소형 {sig}"

    return sig


# ─────────────────────────────────────────────
# 종목 분석 메인
# ─────────────────────────────────────────────

def analyze_stock(code, name, current_change, foreign_dict=None, fetch_investor=True):
    try:
        df_price = get_price_data(code, max_pages=50)
        if df_price is None or len(df_price) < 80:
            return None

        df = df_price.set_index('날짜').copy()

        # 이동평균
        df['5MA']  = df['종가'].rolling(5).mean()
        df['20MA'] = df['종가'].rolling(20).mean()
        df['60MA'] = df['종가'].rolling(60).mean()

        # BUG FIX 1: MA 기울기 → 시리즈 전체에 rolling 후 컬럼 저장
        df['ma5_slope'] = get_ma5_slope_series(df['종가'])

        # 일목균형표
        h9, l9   = df['고가'].rolling(9).max(),  df['저가'].rolling(9).min()
        h26, l26 = df['고가'].rolling(26).max(), df['저가'].rolling(26).min()
        h52, l52 = df['고가'].rolling(52).max(), df['저가'].rolling(52).min()
        df['tenkan']   = (h9  + l9)  / 2
        df['kijun']    = (h26 + l26) / 2
        df['senkou_b_base'] = (h52 + l52) / 2

        # MACD
        e12 = df['종가'].ewm(span=12, adjust=False).mean()
        e26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD']      = e12 - e26
        df['MACD_sig']  = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_hist'] = df['MACD'] - df['MACD_sig']

        # CCI
        df['CCI'] = calc_cci(df)

        # 볼린저 밴드
        df['BB_upper'], df['BB_lower'], df['BB_width'] = calc_bollinger(df['종가'])

        # 거래량 비율
        df['vol_ratio'] = df['거래량'] / df['거래량'].rolling(20).mean()

        # BUG FIX 2: 선행스팬 shift → 거래일(봉) 기준으로 shift(26) 적용
        # pd.DataFrame.shift(26)은 인덱스 기준이 아닌 행 기준 → 거래일 26봉 정확
        df_fut = pd.DataFrame(index=df.index)
        df_fut['senkou_a'] = (df['tenkan'] + df['kijun']) / 2
        df_fut['senkou_b'] = df['senkou_b_base']
        df_fut = df_fut.shift(26)   # 행 기준 26봉 앞 (거래일 정확)

        df_m = df.join(df_fut, rsuffix='_fut')
        # join 컬럼명 정리
        for col in ['senkou_a', 'senkou_b']:
            if f'{col}_fut' in df_m.columns:
                df_m.rename(columns={f'{col}_fut': col}, inplace=True)

        df_f = df_m.dropna(subset=['senkou_a', 'senkou_b', 'CCI', 'BB_width']).copy()
        if len(df_f) < 6:
            return None

        last  = df_f.iloc[-1]
        prev  = df_f.iloc[-2]
        prev2 = df_f.iloc[-3]
        prev3 = df_f.iloc[-4]
        prev4 = df_f.iloc[-5]

        def cloud_top(r): return max(r['senkou_a'], r['senkou_b'])
        def cloud_bot(r): return min(r['senkou_a'], r['senkou_b'])

        price    = last['종가']
        ct, cb   = cloud_top(last), cloud_bot(last)
        above_now = price > ct
        below_now = price < cb

        # BUG FIX 3: 진짜 돌파 = 이전날 구름대 하단 아래였어야 함
        breakout_days = None
        if above_now:
            for d, row in enumerate([prev, prev2, prev3, prev4], 1):
                if row['종가'] < cloud_bot(row):   # 구름대 완전 아래였던 날
                    breakout_days = d
                    break

        breakdown_days = None
        if below_now:
            for d, row in enumerate([prev, prev2, prev3, prev4], 1):
                if row['종가'] > cloud_top(row):   # 구름대 완전 위였던 날
                    breakdown_days = d
                    break

        if above_now:
            ichimoku = (f"🔥 상향돌파({breakout_days}일전)"
                        if breakout_days else "📈 구름대 위")
        elif below_now:
            ichimoku = (f"🧊 하향이탈({breakdown_days}일전)"
                        if breakdown_days else "📉 구름대 아래")
        else:
            prior = [prev, prev2, prev3, prev4]
            was_above = any(r['종가'] > cloud_top(r) for r in prior)
            was_below = any(r['종가'] < cloud_bot(r) for r in prior)
            # BUG FIX 6: 단순 1봉 기울기 대신 3봉 평균 기울기 사용
            slope3 = last.get('ma5_slope', 0)
            if was_above and not was_below:
                ichimoku = "⚠️ 구름대하락진입"
            elif was_below and not was_above and slope3 > 0.1:
                ichimoku = "🌱 구름대상승진입"
            else:
                ichimoku = "🌫️ 구름대 내부"

        # MA 크로스
        def ma_cross(l, p, col):
            if p['종가'] <= p[col] and l['종가'] > l[col]: return "🔥GC"
            if p['종가'] >= p[col] and l['종가'] < l[col]: return "🧊DC"
            return "📈↑" if l['종가'] > l[col] else "📉↓"
        ma_text = (f"5:{ma_cross(last,prev,'5MA')} "
                   f"20:{ma_cross(last,prev,'20MA')} "
                   f"60:{ma_cross(last,prev,'60MA')}")

        # CCI 표시
        cci_now  = last['CCI']
        cci_prev = prev['CCI']
        cv = round(cci_now, 1)
        if   cci_prev < -100 and cci_now >= -100: cci_d = f"{cv} 🟢바닥탈출"
        elif cci_prev <    0 and cci_now >=    0: cci_d = f"{cv} 🔵제로크로스"
        elif cci_prev >  100 and cci_now <=  100: cci_d = f"{cv} 🟡과열탈출"
        elif cci_prev >    0 and cci_now <=    0: cci_d = f"{cv} 🔴제로데드"
        elif cci_now  >  100: cci_d = f"{cv} ⚡과열"
        elif cci_now  < -100: cci_d = f"{cv} 💧과매도"
        else:                 cci_d = f"{cv} ➖중립"

        # BB 상태
        bb_sq, _ = get_bb_squeeze(df_f['BB_width'])
        if   last['종가'] >= last['BB_upper']: bb_pos = "상단"
        elif last['종가'] <= last['BB_lower']: bb_pos = "하단"
        else:                                  bb_pos = "내부"
        bb_d = f"{bb_sq}/{bb_pos}"

        # 거래량 + 방향성
        vr     = round(last['vol_ratio'], 1) if not pd.isna(last['vol_ratio']) else 1.0
        up_day = last['종가'] > prev['종가']
        if vr >= 2.0:
            vol_d  = f"{vr}배📈급등" if up_day else f"{vr}배📉급락"
            vol_up = up_day
        elif vr < 0.5:
            vol_d, vol_up = f"{vr}배⚠️", False
        else:
            vol_d, vol_up = f"{vr}배", up_day and vr >= 1.2

        # 연속봉
        consec, consec_d = detect_consecutive_candles(df_f)

        # 거래대금
        avg_amt, amt_d, amt_ok = calc_trading_amount(df_f)

        # 이격률
        disp     = ((price / last['20MA']) - 1) * 100 if last['20MA'] > 0 else 0
        disp_fmt = f"{'+' if disp >= 0 else ''}{round(disp, 2)}%"

        # RSI (과매수 억제 필터용)
        delta    = df['종가'].diff()
        gain     = delta.clip(lower=0).ewm(com=13, min_periods=14).mean()
        loss     = (-delta.clip(upper=0)).ewm(com=13, min_periods=14).mean()
        rsi_val  = round(100 - 100 / (1 + gain / loss.replace(0, np.nan)), 1).iloc[-1]
        rsi_val  = float(rsi_val) if not pd.isna(rsi_val) else 50.0

        # 5MA 기울기
        slope5 = round(last['ma5_slope'], 2)
        if   slope5 >  0.5: slope_d = f"↗↗급등({slope5:+.1f}%)"
        elif slope5 >  0.1: slope_d = f"↗상승({slope5:+.1f}%)"
        elif slope5 < -0.5: slope_d = f"↘↘급락({slope5:+.1f}%)"
        elif slope5 < -0.1: slope_d = f"↘하락({slope5:+.1f}%)"
        else:               slope_d = f"➖횡보({slope5:+.1f}%)"

        # 외국인
        if fetch_investor and foreign_dict:
            fr      = foreign_dict.get(code, 0.0)
            inv_d   = _fmt_ratio(fr) if fr > 0 else "-"
        else:
            fr, inv_d = 0.0, "-"

        # 모멘텀 통합 점수
        momentum = calc_momentum_score(
            last['MACD_hist'], prev['MACD_hist'], cci_now, cci_prev)

        # 구름대 점수
        if   '상향돌파' in ichimoku: cloud_sc =  3
        elif '하향이탈' in ichimoku: cloud_sc = -3
        elif '상승진입' in ichimoku: cloud_sc =  1
        elif '하락진입' in ichimoku: cloud_sc = -2
        else:                        cloud_sc =  0

        # 60MA 위 여부 (자격 확인)
        above_60 = price > last['60MA'] if not pd.isna(last['60MA']) else False

        # 이격률 보정 점수 (FIX 5: 점수 체계와 신호 결정 일관성 유지)
        if   disp >  15: disp_sc = -2
        elif disp >   6: disp_sc = -1
        elif disp >= -3: disp_sc =  0
        elif disp >= -8: disp_sc =  1
        else:            disp_sc =  2

        total_score = cloud_sc + momentum + disp_sc + (1 if above_60 else -1)

        # 20MA 터치
        ma20_t, ma20_d = detect_20ma_touch(df_f)
        # MACD 전환
        macd_t, macd_d = detect_macd_turn(df_f)
        # CCI 전환
        cci_t,  cci_d2 = detect_cci_turn(df_f)

        # 타이밍 상태 컬럼 (경과일 한눈에)
        timing_d = f"{ma20_d} | {macd_d} | {cci_d2}"

        # 6단계 신호
        signal = decide_signal(
            ichimoku, total_score, disp,
            ma20_t, macd_t, cci_t,
            rsi_val, amt_ok, fr,
            vol_up, consec
        )

        # 주봉 일목균형표
        weekly_ichimoku = calc_weekly_ichimoku(df)

        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"

        return [
            code, name, current_change,
            int(price), disp_fmt,
            total_score, signal,
            ichimoku, weekly_ichimoku, ma_text,
            cci_d, bb_d,
            vol_d, consec_d, amt_d,
            inv_d, slope_d,
            timing_d,
            chart_url
        ]

    except Exception:
        return None


# ─────────────────────────────────────────────
# 스타일 함수
# ─────────────────────────────────────────────

COLUMNS = [
    '코드', '종목명', '등락률', '현재가', '이격률',
    '총점', '신호',
    '일목(일봉)', '일목(주봉)', 'MA크로스',
    'CCI', 'BB상태',
    '거래량', '연속봉', '거래대금',
    '외국인지분율', '5MA기울기',
    '타이밍(20MA|MACD|CCI)',
    '차트'
]


def style_signal(val):
    v = str(val)
    if '매수타이밍' in v: return 'color:white;background-color:#b71c1c;font-weight:bold'
    if '매수준비'   in v: return 'color:#ef5350;font-weight:bold'
    if '관찰등록'   in v: return 'color:#ff8f00;font-weight:bold'
    if '바닥탐색'   in v: return 'color:#8d6e63;font-weight:bold'
    if '홀딩'       in v: return 'color:#2e7d32;font-weight:bold'
    if '추세상승'   in v: return 'color:#558b2f'
    if '구름대내부' in v: return 'color:#78909c'
    if '구름대주의' in v: return 'color:white;background-color:#e65100;font-weight:bold'
    if '적극매도'   in v: return 'color:white;background-color:#0d47a1;font-weight:bold'
    if '추세하락'   in v: return 'color:#1565c0;font-weight:bold'
    return 'color:#9e9e9e'


def style_ichimoku(val):
    v = str(val)
    if '상향돌파' in v: return 'color:white;background-color:#c62828;font-weight:bold'
    if '하향이탈' in v: return 'color:white;background-color:#1565c0;font-weight:bold'
    if '하락진입' in v: return 'color:white;background-color:#e65100;font-weight:bold'
    if '상승진입' in v: return 'color:#ff8f00;font-weight:bold'
    if '구름대 위'   in v: return 'color:#ef5350'
    if '구름대 아래' in v: return 'color:#64b5f6'
    return 'color:#9e9e9e'


def style_score(val):
    try:
        v = int(val)
        if v >=  4: return 'color:white;background-color:#c62828;font-weight:bold'
        if v >=  2: return 'color:#ef5350;font-weight:bold'
        if v >=  0: return 'color:#9e9e9e'
        if v >= -2: return 'color:#42a5f5;font-weight:bold'
        return 'color:white;background-color:#1565c0;font-weight:bold'
    except:
        return ''


def style_cci(val):
    v = str(val)
    if '바닥탈출' in v: return 'color:#43a047;font-weight:bold'
    if '제로크로스'in v: return 'color:#1e88e5;font-weight:bold'
    if '제로데드'  in v: return 'color:#e53935;font-weight:bold'
    if '과열탈출'  in v: return 'color:#fb8c00;font-weight:bold'
    if '과열'      in v: return 'color:#e53935'
    if '과매도'    in v: return 'color:#43a047'
    return ''


def style_pct(val):
    v = str(val).strip()
    if not v or v == '-': return ''
    try:
        if v.startswith('+'): return 'color:#ef5350'
        if v.startswith('-'): return 'color:#42a5f5'
        n = float(v.replace('%', '').replace(',', ''))
        return 'color:#ef5350' if n > 0 else ('color:#42a5f5' if n < 0 else '')
    except:
        return ''


def style_investor(val):
    v = str(val)
    if '고비중' in v: return 'color:#b71c1c;font-weight:bold'
    if '중비중' in v: return 'color:#e65100;font-weight:bold'
    if '저비중' in v: return 'color:#f9a825'
    if '미미'   in v: return 'color:#9e9e9e'
    return ''


def style_timing(val):
    v = str(val)
    if '골든'  in v or '바닥탈출' in v: return 'color:#b71c1c;font-weight:bold'
    if '근접'  in v or '크로스'   in v: return 'color:#ef5350'
    if '회복'  in v or '+1일'     in v: return 'color:#ff8f00'
    return 'color:#9e9e9e'


def style_consec(val):
    v = str(val)
    if '연속양봉' in v: return 'color:#b71c1c;font-weight:bold'
    if '양봉'     in v: return 'color:#ef5350'
    if '연속음봉' in v: return 'color:#1565c0;font-weight:bold'
    if '음봉'     in v: return 'color:#42a5f5'
    return ''


def style_amount(val):
    v = str(val)
    if '⚠️' in v:  return 'color:#9e9e9e'
    if '🔴' in v:  return 'color:#b71c1c;font-weight:bold'
    if '🟠' in v:  return 'color:#e65100;font-weight:bold'
    if '🟡' in v:  return 'color:#f9a825'
    return ''


def compress_display(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    # 일목 압축 (괄호 내용 제거 후 약어 치환)
    d['일목(일봉)'] = d['일목(일봉)'].str.replace(r'\s*\([^)]*\)', '', regex=True)
    ichi_map = {
        "🔥 상향돌파":    "🔥돌파",
        "🧊 하향이탈":    "🧊이탈",
        "📈 구름대 위":   "📈위",
        "📉 구름대 아래": "📉아래",
        "🌫️ 구름대 내부": "🌫️내부",
        "⚠️ 구름대하락진입": "⚠️하락진입",
        "🌱 구름대상승진입": "🌱상승진입",
    }
    d['일목(일봉)'] = d['일목(일봉)'].replace(ichi_map)

    # MA 크로스 압축
    def compress_ma(v):
        parts = str(v).split(' ')
        out = []
        for p in parts:
            if ':' in p:
                num, sym = p.split(':', 1)
                out.append(f"{num}{sym[:2]}")
        return ' '.join(out) if out else v
    d['MA크로스'] = d['MA크로스'].apply(compress_ma)
    d['신호']     = d['신호'].str.strip()
    return d


def style_weekly_ichimoku(val):
    """주봉 일목 색상"""
    v = str(val)
    if '상향돌파' in v: return 'color:white;background-color:#c62828;font-weight:bold'
    if '하향이탈' in v: return 'color:white;background-color:#1565c0;font-weight:bold'
    if '구름대위'  in v: return 'color:#ef5350'
    if '구름대아래'in v: return 'color:#64b5f6'
    if '구름내부'  in v: return 'color:#78909c'
    return 'color:#9e9e9e'


def show_styled_dataframe(dataframe):
    if dataframe.empty:
        st.write("분석된 데이터가 없습니다.")
        return

    disp = compress_display(dataframe)
    h    = (len(disp) + 1) * 35 + 3

    def safe(cols):
        return [c for c in cols if c in disp.columns]

    styled = (
        disp.style
        .map(style_signal,   subset=safe(['신호']))
        .map(style_ichimoku,        subset=safe(['일목(일봉)']))
        .map(style_weekly_ichimoku, subset=safe(['일목(주봉)']))
        .map(style_cci,      subset=safe(['CCI']))
        .map(style_score,    subset=safe(['총점']))
        .map(style_pct,      subset=safe(['등락률', '이격률']))
        .map(lambda x: ('color:#b71c1c;font-weight:bold' if '🔥' in str(x) else
                        'color:#0d47a1;font-weight:bold' if '🧊' in str(x) else
                        'color:#ef5350' if '📈' in str(x) else
                        'color:#42a5f5' if '📉' in str(x) else ''),
             subset=safe(['MA크로스']))
        .map(lambda x: ('color:#ef9a00;font-weight:bold' if '⚡' in str(x) else
                        'color:#26a69a;font-weight:bold' if '💥' in str(x) else
                        'color:#ef5350' if '상단' in str(x) else
                        'color:#42a5f5' if '하단' in str(x) else ''),
             subset=safe(['BB상태']))
        .map(lambda x: ('color:#ef5350' if '📈' in str(x) else
                        'color:#64b5f6' if '📉' in str(x) else ''),
             subset=safe(['거래량']))
        .map(style_consec,   subset=safe(['연속봉']))
        .map(style_amount,   subset=safe(['거래대금']))
        .map(style_investor, subset=safe(['외국인지분율']))
        .map(style_timing,   subset=safe(['타이밍(20MA|MACD|CCI)']))
    )

    col_cfg = {
        "코드":              st.column_config.TextColumn("코드"),
        "총점":              st.column_config.NumberColumn("점수"),
        "등락률":            st.column_config.TextColumn("등락"),
        "이격률":            st.column_config.TextColumn("이격"),
        "거래량":            st.column_config.TextColumn("거래량"),
        "연속봉":            st.column_config.TextColumn("연속봉"),
        "거래대금":          st.column_config.TextColumn("거래대금"),
        "차트":              st.column_config.LinkColumn("차트", display_text="📊"),
        "신호":              st.column_config.TextColumn("신호"),
        "일목(일봉)":        st.column_config.TextColumn("일목(일)"),
        "일목(주봉)":        st.column_config.TextColumn("일목(주)"),
        "MA크로스":          st.column_config.TextColumn("MA"),
        "CCI":               st.column_config.TextColumn("CCI"),
        "BB상태":            st.column_config.TextColumn("BB"),
        "종목명":            st.column_config.TextColumn("종목명"),
        "현재가":            st.column_config.NumberColumn("현재가"),
        "외국인지분율":      st.column_config.TextColumn("외국인%"),
        "5MA기울기":         st.column_config.TextColumn("5MA"),
        "타이밍(20MA|MACD|CCI)": st.column_config.TextColumn("20MA|MACD|CCI"),
    }

    st.dataframe(
        styled,
        use_container_width=True,
        height=h,
        column_config=col_cfg,
        hide_index=True
    )


# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────

st.title("🛡️ 스마트 데이터 스캐너 v5")

st.sidebar.header("설정")
market         = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석 페이지 선택",
                     options=list(range(1, 41)), default=[1])

st.sidebar.markdown("---")
use_investor = st.sidebar.checkbox("📡 외국인 지분율 수집", value=True,
    help="분석 전 전체 수집 (약 20~30초 추가)")

st.sidebar.markdown("---")
st.sidebar.markdown("""
**🎯 6단계 신호**

| 신호 | 조건 |
|------|------|
| 🎯 매수타이밍 | 구름대위+60MA위+3가지AND |
| 📈 매수준비 | 3가지 중 2가지 |
| 🔔 관찰등록 | 3가지 중 1가지 |
| 🛡️ 홀딩 | 구름대위, 신호없음 |
| ⚠️ 구름대주의 | 하락진입 |
| 📉 추세하락/매도 | 구름대 아래 |

**핵심 3가지 (AND)**
- 🎯 20MA 터치/반등
- 📊 MACD 음→양 전환
- 📊 CCI 음→양 전환

**타이밍 컬럼**: 각 신호 경과일 표시
""")

start_btn = st.sidebar.button("🚀 분석 시작")

st.subheader("📊 진단 및 필터링")
c1,c2,c3,c4,c5,c6 = st.columns(6)
m_total   = c1.empty(); m_timing = c2.empty(); m_ready = c3.empty()
m_watch   = c4.empty(); m_caution= c5.empty(); m_sell  = c6.empty()
for m, lbl in [(m_total,"전체"),(m_timing,"🎯타이밍"),(m_ready,"📈준비"),
               (m_watch,"🔔관찰"),(m_caution,"⚠️주의"),(m_sell,"📉매도/하락")]:
    m.metric(lbl, "0개")

fb = st.columns(7)
if 'filter' not in st.session_state:
    st.session_state.filter = "전체"
for col, lbl, key in zip(fb,
    ["🔄전체","🎯타이밍","📈준비","🔔관찰","🛡️홀딩","⚠️주의","📉매도"],
    ["전체","타이밍","준비","관찰","홀딩","주의","매도"]):
    if col.button(lbl, use_container_width=True):
        st.session_state.filter = key

st.markdown("---")
result_title     = st.empty()
main_result_area = st.empty()


def update_metrics(df):
    m_total.metric("전체",       f"{len(df)}개")
    m_timing.metric("🎯타이밍",  f"{len(df[df['신호'].str.contains('매수타이밍', regex=False)])}개")
    m_ready.metric("📈준비",     f"{len(df[df['신호'].str.contains('매수준비',   regex=False)])}개")
    m_watch.metric("🔔관찰",     f"{len(df[df['신호'].str.contains('관찰등록',   regex=False)])}개")
    m_caution.metric("⚠️주의",   f"{len(df[df['신호'].str.contains('구름대주의', regex=False)])}개")
    m_sell.metric("📉매도/하락", f"{len(df[df['신호'].str.contains('매도|하락',  regex=True)])}개")


def apply_filter(df, f):
    m = {
        "타이밍": "매수타이밍",
        "준비":   "매수준비",
        "관찰":   "관찰등록",
        "홀딩":   "홀딩",
        "주의":   "구름대주의",
        "매도":   "매도|하락",
    }
    if f in m:
        return df[df['신호'].str.contains(m[f], regex=(f == "매도"))]
    return df


SIG_ORDER = {
    "🎯": 0, "📈": 1, "🔔": 2, "🛡️": 3, "⏸️": 4,
    "🌫️": 5, "⚠️": 6, "🔄": 7, "📉": 8, "🧊": 9,
}


if start_btn:
    st.session_state.filter = "전체"
    market_df = get_market_sum_pages(selected_pages, market)
    if not market_df.empty:
        results      = []
        foreign_dict = {}
        if use_investor:
            with st.spinner(f"📡 {market} 외국인 지분율 수집 중..."):
                foreign_dict = load_foreign_ratio_all(market=market)
            st.info(f"✅ {len(foreign_dict):,}개 종목 수집 완료")

        pb = st.progress(0, text="분석 시작...")
        for i, (_, row) in enumerate(market_df.iterrows()):
            res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'],
                                foreign_dict=foreign_dict,
                                fetch_investor=use_investor)
            if res:
                results.append(res)
                df_all = pd.DataFrame(results, columns=COLUMNS)
                # 신호 우선순위 정렬 (BUG FIX 7: 안정 정렬)
                df_all['_ord'] = df_all['신호'].apply(
                    lambda s: next((v for k, v in SIG_ORDER.items() if s.startswith(k)), 5))
                df_all = (df_all.sort_values(['_ord', '총점'], ascending=[True, False])
                                .drop(columns='_ord')
                                .reset_index(drop=True))
                st.session_state['df_all'] = df_all

                update_metrics(df_all)
                disp_df = apply_filter(df_all, st.session_state.filter)
                result_title.subheader(
                    f"🔍 결과 ({st.session_state.filter} / {len(disp_df)}개)")
                with main_result_area:
                    show_styled_dataframe(disp_df)

            pb.progress((i + 1) / len(market_df),
                        text=f"분석 중: {row['종목명']} ({i+1}/{len(market_df)})")
        pb.empty()
        st.success("✅ 분석 완료!")


if not start_btn and 'df_all' in st.session_state:
    df      = st.session_state['df_all']
    disp_df = apply_filter(df, st.session_state.filter)
    update_metrics(df)
    result_title.subheader(f"🔍 결과 ({st.session_state.filter} / {len(disp_df)}개)")
    with main_result_area:
        show_styled_dataframe(disp_df)

    if not disp_df.empty:
        summary = disp_df[['종목명','현재가','총점','신호','일목(일봉)',
                            '타이밍(20MA|MACD|CCI)']].to_string(index=False)
        body    = urllib.parse.quote(f"주식 분석 리포트\n\n{summary}")
        st.markdown(
            f'<a href="mailto:?subject=주식리포트&body={body}" target="_self"'
            f' style="text-decoration:none;">'
            f'<div style="background:#0078d4;color:white;padding:15px;'
            f'border-radius:8px;text-align:center;font-weight:bold;">'
            f'📧 현재 리스트 Outlook 전송</div></a>',
            unsafe_allow_html=True
        )

elif 'df_all' not in st.session_state:
    with main_result_area:
        st.info("왼쪽 사이드바에서 '분석 시작' 버튼을 눌러주세요.")


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

# ---------------------------------------------
# 헬퍼 함수
# ---------------------------------------------

def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
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


def get_price_data(code, max_pages=30):
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
    for col in ['종가', '고가', '저가', '거래량']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce')
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    return df.dropna(subset=['날짜', '종가']).sort_values('날짜').reset_index(drop=True)


def load_foreign_ratio_all(market="KOSPI", max_pages=40):
    """
    https://finance.naver.com/sise/sise_foreign_hold.naver
    페이지에서 전체 외국인 보유 비율을 한 번에 수집

    테이블 구조 (type_2):
      순위 | 종목명(code href) | 현재가 | 전일비 | 등락률 | 거래량 | 보유주식수 | 비율(%)

    반환: dict { '005930': 52.83, '000660': 31.24, ... }
    """
    sosok = "0" if market == "KOSPI" else "1"
    ratio_dict = {}

    # 전체 페이지 수 파악 (1페이지 먼저 요청)
    base_url = (f"https://finance.naver.com/sise/sise_foreign_hold.naver"
                f"?sosok={sosok}")
    try:
        res = requests.get(f"{base_url}&page=1", headers=get_headers(), timeout=10)
        res.encoding = 'euc-kr'
        soup = BeautifulSoup(res.text, 'html.parser')

        # 전체 페이지 수 파악
        pager = soup.select_one('td.pgRR a')
        if pager and 'page=' in pager.get('href',''):
            import re as _re
            m = _re.search(r'page=(\d+)', pager['href'])
            total_pages = int(m.group(1)) if m else max_pages
        else:
            total_pages = max_pages
        total_pages = min(total_pages, max_pages)

        # 1페이지 파싱
        ratio_dict.update(_parse_foreign_page(soup))

        # 나머지 페이지
        for page in range(2, total_pages + 1):
            try:
                r = requests.get(f"{base_url}&page={page}",
                                 headers=get_headers(), timeout=8)
                r.encoding = 'euc-kr'
                s = BeautifulSoup(r.text, 'html.parser')
                ratio_dict.update(_parse_foreign_page(s))
                time.sleep(0.15)
            except Exception:
                continue

    except Exception:
        pass

    return ratio_dict


def _parse_foreign_page(soup):
    """
    sise_foreign_hold 페이지의 table.type_2 한 페이지 파싱
    반환: dict { code: ratio_float }
    """
    import re as _re
    result = {}
    table = soup.select_one('table.type_2')
    if not table:
        return result

    for tr in table.select('tr'):
        tds = tr.find_all('td')
        if len(tds) < 8:
            continue
        # 종목 링크에서 코드 추출
        a = tr.find('a', href=True)
        if not a:
            continue
        m = _re.search(r'code=(\d{6})', a['href'])
        if not m:
            continue
        code = m.group(1)

        # 비율(%) — 마지막 td (인덱스 7)
        try:
            ratio_txt = tds[7].get_text(strip=True).replace('%','').replace(',','').strip()
            ratio = float(ratio_txt)
            result[code] = ratio
        except (ValueError, IndexError):
            continue

    return result


def _fmt_ratio(ratio: float) -> str:
    """외국인 지분율 표시 문자열"""
    if ratio >= 30:   return f"{ratio:.2f}% 🔴고비중"
    elif ratio >= 15: return f"{ratio:.2f}% 🟠중비중"
    elif ratio >= 5:  return f"{ratio:.2f}% 🟡저비중"
    else:             return f"{ratio:.2f}% ⚪미미"


def get_ma5_slope(price_series):
    """
    5MA 기울기 — 최근 3일 기울기로 단기 모멘텀 방향 판단
    양수 = 상승 중, 음수 = 하락 중
    """
    try:
        ma5 = price_series.rolling(5).mean()
        if len(ma5) < 4:
            return 0, "➖"
        slope = ma5.iloc[-1] - ma5.iloc[-3]   # 2거래일 기울기
        pct   = slope / ma5.iloc[-3] * 100 if ma5.iloc[-3] != 0 else 0
        if pct > 0.5:    return pct, f"↗↗급등({round(pct,1)}%)"
        elif pct > 0.1:  return pct, f"↗상승({round(pct,1)}%)"
        elif pct < -0.5: return pct, f"↘↘급락({round(pct,1)}%)"
        elif pct < -0.1: return pct, f"↘하락({round(pct,1)}%)"
        else:            return pct, f"➖횡보({round(pct,1)}%)"
    except Exception:
        return 0, "➖"


def calc_consecutive_candles(df_final, n=5):
    """
    ③ 연속 양봉/음봉 감지
    
    실전 의미:
      연속 양봉 3개 이상 -> 단기 매수세 강함, 추격 주의 (이미 오름)
      연속 음봉 3개 이상 -> 매도세 지속, 반등 확인 필요
      연속 양봉 직후 음봉 -> 단기 추세 전환 경고
    반환: (연속봉_수, 표시문자열, 점수_int)
      - 양봉이면 양수, 음봉이면 음수
    """
    try:
        # 시가 대비 종가로 양/음봉 판단
        closes = df_final['종가'].iloc[-n:]
        opens  = df_final['시가'].iloc[-n:] if '시가' in df_final.columns else None

        if opens is not None:
            candles = [(c > o) for c, o in zip(closes, opens)]
        else:
            # 시가 없으면 전일 종가 대비로 대체
            candles = [(closes.iloc[i] > closes.iloc[i-1])
                       for i in range(1, len(closes))]

        # 가장 최근부터 역방향으로 연속 봉 카운트
        if not candles:
            return 0, "➖", 0

        last_dir = candles[-1]  # True=양봉, False=음봉
        count = 1
        for c in reversed(candles[:-1]):
            if c == last_dir:
                count += 1
            else:
                break

        signed = count if last_dir else -count

        if last_dir:   # 연속 양봉
            if count >= 5: disp, sc = f"🔴연속양봉{count}개", -1  # 과열 -> 추격 주의
            elif count >= 3: disp, sc = f"📈양봉{count}개",    1
            else:            disp, sc = f"📈양봉{count}개",    0
        else:          # 연속 음봉
            if count >= 5: disp, sc = f"🔵연속음봉{count}개", 1   # 과매도 -> 반등 주시
            elif count >= 3: disp, sc = f"📉음봉{count}개",  -1
            else:            disp, sc = f"📉음봉{count}개",   0

        return signed, disp, sc
    except Exception:
        return 0, "➖", 0


def calc_volume_with_direction(df_final):
    """
    ③ 거래량 + 방향성 통합 판단
    
    실전 핵심: 거래량 급증의 의미는 방향에 따라 정반대
      상승 + 거래량 급증 -> 매수세 강함 (+)
      하락 + 거래량 급증 -> 공포 매도, 오히려 반등 신호일 수도 있으나 추가 하락 위험
      상승 + 거래량 감소 -> 힘없는 반등, 신뢰도 낮음
    """
    try:
        last     = df_final.iloc[-1]
        prev     = df_final.iloc[-2]
        vol_r    = last['vol_ratio'] if not pd.isna(last['vol_ratio']) else 1.0

        # 당일 방향 (종가 > 전일종가 = 상승)
        up_day = last['종가'] > prev['종가']
        pct_chg = (last['종가'] - prev['종가']) / prev['종가'] * 100 if prev['종가'] > 0 else 0

        if vol_r >= 2.0:
            if up_day:
                disp = f"{vol_r:.1f}배^ 📈급등"
                sc   = 1    # 상승 + 거래량 급증 = 강한 매수
            else:
                disp = f"{vol_r:.1f}배^ 📉급락"
                sc   = -1   # 하락 + 거래량 급증 = 공포 매도
        elif vol_r >= 1.5:
            disp = f"{vol_r:.1f}배^ {'📈' if up_day else '📉'}"
            sc   = 0
        elif vol_r < 0.5:
            disp = f"{vol_r:.1f}배v ➖거래고갈"
            sc   = -1   # 거래량 고갈 = 신뢰도 하락
        else:
            disp = f"{vol_r:.1f}배"
            sc   = 0

        return vol_r, disp, sc, up_day
    except Exception:
        return 1.0, "➖", 0, True


def calc_trading_amount(df_final, min_amount_bil=30):
    """
    ① 거래대금 필터
    
    거래량 x 현재가 = 거래대금
    최근 5일 평균 거래대금이 min_amount_bil(억) 미만이면 제외 권고
    소형주-저유동성 종목의 오신호 차단
    """
    try:
        last5 = df_final.iloc[-5:]
        amounts = last5['종가'] * last5['거래량'] / 1e8  # 억원
        avg_amount = amounts.mean()

        if avg_amount >= 500:   disp = f"{avg_amount:,.0f}억 🔴대형"
        elif avg_amount >= 100: disp = f"{avg_amount:,.0f}억 🟠중형"
        elif avg_amount >= min_amount_bil: disp = f"{avg_amount:,.0f}억 🟡소형"
        else:                   disp = f"{avg_amount:,.0f}억 ⚠️소형"

        is_enough = avg_amount >= min_amount_bil
        return avg_amount, disp, is_enough
    except Exception:
        return 0, "-", False


# ---------------------------------------------
# 지표 계산
# ---------------------------------------------

def calc_rsi(series, period=14):
    """RSI 계산 (Wilder 방식)"""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_bollinger(series, period=20, std_mult=2):
    """볼린저 밴드 + 밴드폭 계산"""
    ma = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = ma + std_mult * std
    lower = ma - std_mult * std
    bandwidth = (upper - lower) / ma * 100  # 밴드폭(%)
    return upper, lower, bandwidth


def calc_cci(df, period=20):
    """
    CCI (Commodity Channel Index)
    = (현재가 - 기간 평균가) / (0.015 x 평균절대편차)
    +100 초과  -> 과매수 (강한 상승)
    -100 미만  -> 과매도 (강한 하락)
    전환 시점(음수->0, 0->양수)이 핵심 신호
    """
    tp = (df['고가'] + df['저가'] + df['종가']) / 3   # Typical Price
    ma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - ma) / (0.015 * mad.replace(0, np.nan))


def get_bb_squeeze_status(bandwidth_series):
    """
    밴드폭 최근 20일 중 현재 위치로 수축/팽창 판단
      - 수축(Squeeze) : 현재 밴드폭이 최근 20일 중 하위 20%
      - 팽창(Expansion): 현재 밴드폭이 최근 20일 중 상위 20%
    """
    recent = bandwidth_series.iloc[-20:]
    cur = bandwidth_series.iloc[-1]
    p20 = recent.quantile(0.20)
    p80 = recent.quantile(0.80)
    if cur <= p20:
        return "⚡ 수축(폭발 대기)", True   # squeeze=True
    elif cur >= p80:
        return "💥 팽창(추세 진행)", False
    else:
        return "➖ 보통", False


# ---------------------------------------------
# 점수 기반 신호 결정
# ---------------------------------------------

def calc_signal_score(last, prev, ichimoku_status,
                      rsi_val, cci_now, cci_prev,
                      disparity, bb_width_series,
                      foreign_ratio=0.0,
                      ma5_slope=0, pct_from_52high=0,
                      consec_score=0,
                      vol_dir_score=0,
                      price_col='종가'):
    score = 0
    detail = {}

    # ==========================================
    # ① 구름대 (전환 시점 + 진입 방향)
    # ==========================================
    if '상향돌파' in ichimoku_status:
        s = 3
    elif '하향이탈' in ichimoku_status:
        s = -3
    elif '상승진입' in ichimoku_status:   # 아래->구름대 내부
        s = 1
    elif '하락진입' in ichimoku_status:   # 위->구름대 내부 (위험)
        s = -2
    else:
        s = 0     # 구름대 위-아래 유지 -> 이미 반영, 점수 없음
    score += s
    detail['구름대'] = s

    # ==========================================
    # ② MACD 히스토그램 (전환 + 기울기)
    # ==========================================
    hist_now   = last['MACD_hist']
    hist_prev  = prev['MACD_hist']
    macd_slope = hist_now - hist_prev

    if hist_now > 0 and hist_prev <= 0:
        s = 2    # 음->양 전환 (골든)
    elif hist_now < 0 and hist_prev >= 0:
        s = -2   # 양->음 전환 (데드)
    elif hist_now < 0 and macd_slope > 0:
        s = 1    # 음수 유지지만 기울기^ (회복 조짐)
    elif hist_now > 0 and macd_slope < 0:
        s = -1   # 양수 유지지만 기울기v (약화 조짐)
    else:
        s = 0
    score += s
    detail['MACD'] = s

    # ==========================================
    # ③ CCI (전환 시점만)
    # ==========================================
    if cci_prev < -100 and cci_now >= -100:
        s = 2    # 과매도(-100) 탈출 -> 강한 반등 신호
    elif cci_prev < 0 and cci_now >= 0:
        s = 1    # 제로 크로스 상향
    elif cci_prev > 0 and cci_now <= 0:
        s = -1   # 제로 크로스 하향
    elif cci_prev > 100 and cci_now <= 100:
        s = -2   # 과매수(+100) 이탈 -> 강한 하락 신호
    else:
        s = 0
    score += s
    detail['CCI'] = s

    # ==========================================
    # ④ 이격률 타이밍 (세분화)
    # ==========================================
    if disparity > 20:
        s = -3
    elif disparity > 12:
        s = -2
    elif disparity > 6:
        s = -1
    elif disparity >= -3:
        s = 0    # 20MA +/-3% -> 눌림목 완성, 중립
    elif disparity >= -8:
        s = 1    # 살짝 눌림 -> 진입 타이밍 양호
    else:
        s = 2    # 많이 눌림 (추세 확인 필수)
    score += s
    detail['이격률'] = s

    # ==========================================
    # ⑤ 거래량 (전환 신호 동반 시만)
    # ==========================================
    vol_ratio = last.get('vol_ratio', np.nan)
    has_turn  = (detail['구름대'] != 0 or
                 abs(detail['MACD']) >= 1 or
                 abs(detail['CCI']) >= 1)
    if not pd.isna(vol_ratio):
        if vol_ratio >= 1.5 and has_turn:
            s = 1
        elif vol_ratio < 0.5:
            s = -1
        else:
            s = 0
    else:
        s = 0
    score += s
    detail['거래량'] = s

    # ==========================================
    # ⑥ 외국인 지분율 — 스마트머니 비중 판단
    # ==========================================
    # 지분율 높을수록 외국인이 장기적으로 신뢰하는 종목
    # 30% 이상 -> 외국인 선호 우량주 (+1 보정)
    # 단독 점수보다는 다른 신호의 신뢰도 보강 역할
    if foreign_ratio >= 30:
        s = 1    # 외국인 고비중 -> 신호 신뢰도 상승
    elif foreign_ratio >= 15:
        s = 0    # 중간 비중 -> 중립
    elif foreign_ratio > 0 and foreign_ratio < 5:
        s = -1   # 외국인 외면 종목 -> 신호 신뢰도 하락
    else:
        s = 0
    score += s
    detail['외국인지분'] = s

    # ==========================================
    # ⑦ 5MA 기울기 — 단기 모멘텀 확인
    # ==========================================
    # 전환 신호가 있을 때 방향이 맞으면 보강, 역방향이면 페널티
    if ma5_slope > 0.3 and detail['구름대'] >= 0:
        s = 1    # 상승 모멘텀 확인
    elif ma5_slope < -0.3 and detail['구름대'] <= 0:
        s = -1   # 하락 모멘텀 확인
    else:
        s = 0
    score += s
    detail['5MA기울기'] = s

    # ==========================================
    # ⑧ 52주 위치 보정
    # ==========================================
    # 신고가 근접(3% 이내): 강한 추세 -> 추세상승 강화 / 신규매수는 주의
    # 신저가 근접: 반등 기대 but 추세 약함 -> 바닥탐색 신호 강화
    if pct_from_52high >= -3:
        # 신고가권: 추세상승 확인 신호지만 신규진입 부담 -> 이격률 페널티와 동일 방향
        s = 0    # 중립 (이격률에서 이미 반영)
    elif pct_from_52high <= -30:
        s = 1    # 많이 내려온 상태 -> 바닥탐색 가능성 (단, 추세 확인 필수)
    else:
        s = 0
    score += s
    detail['52주위치'] = s

    # ==========================================
    # 조건 플래그 (신호 결정에 사용)
    # ==========================================
    is_above_cloud   = '구름대 위'  in ichimoku_status or '상향돌파' in ichimoku_status
    is_below_cloud   = '구름대 아래' in ichimoku_status or '하향이탈' in ichimoku_status
    is_falling_entry = '하락진입'   in ichimoku_status   # 위에서 내려와 구름대 진입
    is_rising_entry  = '상승진입'   in ichimoku_status   # 아래서 올라와 구름대 진입
    is_inside_cloud  = '내부'       in ichimoku_status   # 구름대 내부 횡보

    cloud_breakout   = detail['구름대'] == 3
    cloud_breakdown  = detail['구름대'] == -3
    macd_up          = detail['MACD'] >= 1
    macd_down        = detail['MACD'] <= -1
    cci_up           = detail['CCI'] > 0
    cci_down         = detail['CCI'] < 0

    is_high_disp     = disparity > 15   # 많이 오름 -> 신규 진입 부담
    is_mid_disp      = 6 < disparity <= 15  # 적당히 오름 -> 홀딩 유효
    is_low_disp      = disparity < -10  # 많이 하락

    # ==========================================
    # 12단계 신호 결정
    # 우선순위: 위험경보 -> 강한매수 -> 매수 -> 홀딩/중립 -> 하락경보 -> 매도
    # ==========================================

    # -- ⚠️ 구름대주의 [최우선 위험경보] ------
    # 구름대 하락 진입: 매수 신호 전면 억제
    if is_falling_entry:
        signal = "⚠️ 구름대주의"

    # -- 🔥 적극매수 --------------------------
    # 구름대 돌파 + MACD-CCI 동시 상향 전환 + 총점 높음
    elif (score >= 7
          and cloud_breakout
          and macd_up and cci_up):
        signal = "🔥 적극매수"

    # -- 📈 매수관심 --------------------------
    # 전환 신호 2개^ + 이격률 부담 없음 + 총점 >= 4
    elif (score >= 4
          and not is_high_disp
          and (cloud_breakout or macd_up or cci_up)
          and sum([cloud_breakout, macd_up, cci_up]) >= 2):
        signal = "📈 매수관심"

    # -- 🌱 진입준비 --------------------------
    # 전환 신호 1개 + 이격률 양호 + 총점 >= 2
    elif (score >= 2
          and disparity <= 6
          and has_turn
          and not is_falling_entry):
        signal = "🌱 진입준비"

    # -- 🔄 바닥탐색 --------------------------
    # 구름대 아래 오래 있었지만 MACD-CCI 회복 조짐 -> 저점 반등 예비 신호
    elif (is_below_cloud
          and (macd_up or cci_up)
          and score >= 0):
        signal = "🔄 바닥탐색"

    # -- 🔻 하락가속 --------------------------
    # 구름대 아래 + MACD-CCI 동시 하락 전환 -> 낙폭 확대 위험
    elif (is_below_cloud
          and macd_down and cci_down):
        signal = "🔻 하락가속"

    # -- 🧊 적극매도 --------------------------
    # 하향이탈 직후 + MACD-CCI 동시v + 총점 매우 낮음
    elif (score <= -5
          and cloud_breakdown
          and macd_down and cci_down):
        signal = "🧊 적극매도"

    # -- 📉 매도관심 --------------------------
    elif score <= -3:
        signal = "📉 매도관심"

    # -- 🔽 추세하락 --------------------------
    # 구름대 아래 + 많이 하락 -> 탈출 고려
    elif is_below_cloud and is_low_disp:
        signal = "🔽 추세하락"

    # -- 🔼 추세상승 --------------------------
    # 구름대 위 + 많이 오름 -> 홀딩 OK, 신규 진입 주의
    elif is_above_cloud and is_high_disp:
        signal = "🔼 추세상승"

    # -- 🛡️ 홀딩유지 --------------------------
    # 구름대 위 + 이격률 적당 + 전환신호 없음 -> 보유 중 안정권
    elif is_above_cloud and is_mid_disp and not has_turn:
        signal = "🛡️ 홀딩유지"

    # -- 🌫️ 구름대내부 -------------------------
    # 구름대 안에서 방향 불명확 횡보
    elif is_inside_cloud:
        signal = "🌫️ 구름대내부"

    # -- ⏸️ 관망 ------------------------------
    else:
        signal = "⏸️ 관망"

    return score, signal, detail


# ---------------------------------------------
# 종목 분석 메인
# ---------------------------------------------

def analyze_stock(code, name, current_change, foreign_dict=None, fetch_investor=True):
    try:
        df_price = get_price_data(code, max_pages=25)
        if df_price is None or len(df_price) < 80:
            return None

        df = df_price.set_index('날짜').copy()

        # -- 이동평균 -------------------------
        df['5MA']  = df['종가'].rolling(5).mean()
        df['20MA'] = df['종가'].rolling(20).mean()
        df['60MA'] = df['종가'].rolling(60).mean()

        # -- 일목균형표 -----------------------
        high_9  = df['고가'].rolling(9).max()
        low_9   = df['저가'].rolling(9).min()
        df['tenkan_sen'] = (high_9 + low_9) / 2

        high_26 = df['고가'].rolling(26).max()
        low_26  = df['저가'].rolling(26).min()
        df['kijun_sen'] = (high_26 + low_26) / 2

        high_52 = df['고가'].rolling(52).max()
        low_52  = df['저가'].rolling(52).min()
        df['senkou_b_base'] = (high_52 + low_52) / 2

        # -- MACD ----------------------------
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD']        = ema12 - ema26
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_hist']   = df['MACD'] - df['MACD_Signal']

        # -- RSI -----------------------------
        df['RSI'] = calc_rsi(df['종가'])

        # -- CCI -----------------------------
        df['CCI'] = calc_cci(df)

        # -- 볼린저 밴드 ----------------------
        df['BB_upper'], df['BB_lower'], df['BB_width'] = calc_bollinger(df['종가'])

        # -- 거래량 비율 (20일 평균 대비) -----
        df['vol_ratio'] = df['거래량'] / df['거래량'].rolling(20).mean()

        # -- 선행스팬 시프트 (거래일 기준 26봉) -
        df_future = pd.DataFrame(index=df.index)
        df_future['senkou_a'] = (df['tenkan_sen'] + df['kijun_sen']) / 2
        df_future['senkou_b'] = df['senkou_b_base']
        df_future = df_future.shift(26)  # 거래일 기준 26봉 앞

        df_merged = pd.merge(df, df_future, left_index=True, right_index=True, how='left')
        df_final  = df_merged.dropna(subset=['senkou_a', 'senkou_b', 'RSI', 'BB_width', 'CCI']).copy()

        if len(df_final) < 6:   # CCI-구름대 돌파일수 계산에 여유 필요
            return None

        last  = df_final.iloc[-1]
        prev  = df_final.iloc[-2]
        prev2 = df_final.iloc[-3]
        prev3 = df_final.iloc[-4]
        prev4 = df_final.iloc[-5]

        price_col = '종가'

        # ========================================
        # 일목 상태 — 최근 1~4일 돌파 감지
        # ========================================
        def cloud_top(row): return max(row['senkou_a'], row['senkou_b'])
        def cloud_bot(row): return min(row['senkou_a'], row['senkou_b'])

        price_now = last['종가']
        ct_now    = cloud_top(last)
        cb_now    = cloud_bot(last)

        # 현재 구름대 위인지 아래인지
        above_now = price_now > ct_now
        below_now = price_now < cb_now

        # 최근 N일 전 가격이 구름대 아래/안에 있었는지 확인 -> 돌파 경과일 계산
        breakout_days = None   # None = 돌파 아님
        if above_now:
            for days_ago, row in enumerate([prev, prev2, prev3, prev4], start=1):
                if row['종가'] <= cloud_top(row):   # 그날은 구름대 아래/안이었음
                    breakout_days = days_ago
                    break

        breakdown_days = None
        if below_now:
            for days_ago, row in enumerate([prev, prev2, prev3, prev4], start=1):
                if row['종가'] >= cloud_bot(row):
                    breakdown_days = days_ago
                    break

        if above_now:
            if breakout_days is not None:
                ichimoku_status = f"🔥 상향돌파({breakout_days}일전)"
            else:
                ichimoku_status = "📈 구름대 위"          # 돌파한 지 오래됨 -> 점수 없음
        elif below_now:
            if breakdown_days is not None:
                ichimoku_status = f"🧊 하향이탈({breakdown_days}일전)"
            else:
                ichimoku_status = "📉 구름대 아래"
        else:
            # 구름대 안에 있음 -> 어디서 들어왔는지 방향 판단
            # 직전 5봉 중 구름대 위에 있던 봉이 있으면 -> 하락 진입 (위험)
            # 구름대 아래에 있던 봉이 있으면 -> 상승 진입 (기대)
            prior_rows = [prev, prev2, prev3, prev4]
            was_above = any(r['종가'] > cloud_top(r) for r in prior_rows)
            was_below = any(r['종가'] < cloud_bot(r) for r in prior_rows)

            if was_above and not was_below:
                ichimoku_status = "⚠️ 구름대하락진입"   # 위에서 내려옴 -> 위험
            elif was_below and not was_above:
                ichimoku_status = "🌱 구름대상승진입"   # 아래서 올라옴 -> 기대
            else:
                ichimoku_status = "🌫️ 구름대 내부"      # 방향 불명확

        # -- MA 크로스 상태 -------------------
        def ma_cross(l, p, ma_col):
            if p[price_col] <= p[ma_col] and l[price_col] > l[ma_col]:
                return "🔥GC"
            if p[price_col] >= p[ma_col] and l[price_col] < l[ma_col]:
                return "🧊DC"
            return "📈^" if l[price_col] > l[ma_col] else "📉v"

        ma_text = (f"5:{ma_cross(last,prev,'5MA')} "
                   f"20:{ma_cross(last,prev,'20MA')} "
                   f"60:{ma_cross(last,prev,'60MA')}")

        # -- RSI 표시 -------------------------
        rsi_val = round(last['RSI'], 1)
        if rsi_val <= 30:
            rsi_display = f"{rsi_val} 🟢과매도"
        elif rsi_val <= 45:
            rsi_display = f"{rsi_val} 🔵관심"
        elif rsi_val <= 55:
            rsi_display = f"{rsi_val} ⚪중립"
        elif rsi_val <= 70:
            rsi_display = f"{rsi_val} 🟡주의"
        else:
            rsi_display = f"{rsi_val} 🔴과매수"

        # -- CCI 표시 -------------------------
        cci_now  = last['CCI']
        cci_prev = prev['CCI']
        cci_val  = round(cci_now, 1)

        # 전환 구간 판단
        if cci_prev < -100 and cci_now >= -100:
            cci_display = f"{cci_val} 🟢과매도탈출"   # 바닥 탈출 (+2)
        elif cci_prev < 0 and cci_now >= 0:
            cci_display = f"{cci_val} 🔵제로크로스"    # 음->양 전환 (+1)
        elif cci_prev > 100 and cci_now <= 100:
            cci_display = f"{cci_val} 🟡과매수탈출"    # 과열 해소
        elif cci_prev > 0 and cci_now <= 0:
            cci_display = f"{cci_val} 🔴제로데드"       # 양->음 전환 (-1)
        elif cci_now > 100:
            cci_display = f"{cci_val} ⚡과매수"
        elif cci_now < -100:
            cci_display = f"{cci_val} 💧과매도"
        else:
            cci_display = f"{cci_val} ➖중립"

        # -- BB Squeeze 상태 ------------------
        bb_status, is_squeeze = get_bb_squeeze_status(df_final['BB_width'])
        if last['종가'] >= last['BB_upper']:
            bb_pos = "상단"
        elif last['종가'] <= last['BB_lower']:
            bb_pos = "하단"
        else:
            bb_pos = "내부"
        bb_display = f"{bb_status}/{bb_pos}"

        # -- ③ 거래량 + 방향성 ----------------
        vol_r, vol_display, vol_dir_score, up_day = calc_volume_with_direction(df_final)

        # -- ③ 연속 양봉/음봉 -----------------
        consec_signed, consec_display, consec_score = calc_consecutive_candles(df_final)

        # -- ① 거래대금 필터 ------------------
        avg_amount, amount_display, amount_ok = calc_trading_amount(df_final)

        # -- 이격률 ---------------------------
        disparity = ((last['종가'] / last['20MA']) - 1) * 100 if last['20MA'] > 0 else 0
        disparity_fmt = f"{'+' if disparity >= 0 else ''}{round(disparity, 2)}%"

        # -- 52주 고저 위치 -------------------
        try:
            high_52 = df['종가'].rolling(252).max().iloc[-1]
            low_52  = df['종가'].rolling(252).min().iloc[-1]
            cur_p   = last['종가']
            if pd.isna(high_52) or high_52 == 0:
                pct_52high, week52_display = 0.0, "-"
            else:
                pct_h = round(((cur_p - high_52) / high_52) * 100, 1)
                pct_l = round(((cur_p - low_52)  / low_52)  * 100, 1)
                if pct_h >= -3:    week52_display = f"🚀신고가({pct_h}%)"
                elif pct_h >= -10: week52_display = f"📈고점근접({pct_h}%)"
                elif pct_l <= 5:   week52_display = f"💧저점근접(+{pct_l}%)"
                else:              week52_display = f"고:{pct_h}% 저:+{pct_l}%"
                pct_52high = pct_h
        except Exception:
            pct_52high, week52_display = 0.0, "-"

        # -- 5MA 기울기 -----------------------
        ma5_slope, slope_display = get_ma5_slope(df['종가'])

        # -- 외국인 지분율 ---------------------
        if fetch_investor and foreign_dict is not None:
            foreign_ratio    = foreign_dict.get(code, 0.0)
            investor_display = _fmt_ratio(foreign_ratio) if foreign_ratio > 0 else "-"
        else:
            foreign_ratio, investor_display = 0.0, "-"

        # -- 점수 기반 종합 신호 --------------
        score, signal, detail = calc_signal_score(
            last, prev, ichimoku_status,
            rsi_val, cci_now, cci_prev,
            disparity, df_final['BB_width'],
            foreign_ratio=foreign_ratio,
            ma5_slope=ma5_slope,
            pct_from_52high=pct_52high,
            consec_score=consec_score,
            vol_dir_score=vol_dir_score,
        )

        # ① 거래대금 미달 종목 -> 신호 강등
        if not amount_ok and signal in ("🔥 적극매수", "📈 매수관심"):
            signal = f"⚠️소형({signal})"

        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"

        return [
    code, name, current_change,
    int(last['종가']), disparity_fmt,
    score, signal,
    ichimoku_status, ma_text,
    cci_display, bb_display,
    vol_display, consec_display, amount_display,
    investor_display, slope_display,
    chart_url]

    except Exception as e:
        return None


# ---------------------------------------------
# 스타일 데이터프레임 표시
# ---------------------------------------------

COLUMNS = ['코드', '종목명', '등락률', '현재가', '이격률',
           '총점', '신호',
           '일목(일봉)', 'MA크로스',
           'CCI', 'BB상태',
           '거래량', '연속봉', '거래대금',
           '외국인지분율', '5MA기울기',
           '차트']


def style_signal(val):
    v = str(val)
    # -- 매수 계열 --------------------------
    if '적극매수'   in v: return 'color:white;background-color:#b71c1c;font-weight:bold'
    if '매수관심'   in v: return 'color:#ef5350;font-weight:bold'
    if '진입준비'   in v: return 'color:#ff8f00;font-weight:bold'
    if '바닥탐색'   in v: return 'color:#8d6e63;font-weight:bold'
    # -- 보유/중립 계열 ---------------------
    if '홀딩유지'   in v: return 'color:#2e7d32;font-weight:bold'
    if '추세상승'   in v: return 'color:#558b2f'
    if '구름대내부' in v: return 'color:#78909c'
    # -- 주의/위험 계열 ---------------------
    if '구름대주의' in v: return 'color:white;background-color:#e65100;font-weight:bold'
    # -- 하락 계열 --------------------------
    if '하락가속'   in v: return 'color:white;background-color:#4a148c;font-weight:bold'
    if '추세하락'   in v: return 'color:#1565c0;font-weight:bold'
    if '매도관심'   in v: return 'color:#42a5f5;font-weight:bold'
    if '적극매도'   in v: return 'color:white;background-color:#0d47a1;font-weight:bold'
    return 'color:#9e9e9e'  # 관망


def style_ichimoku(val):
    v = str(val)
    if '상향돌파'   in v: return 'color:white;background-color:#c62828;font-weight:bold'
    if '하향이탈'   in v: return 'color:white;background-color:#1565c0;font-weight:bold'
    if '하락진입'   in v: return 'color:white;background-color:#e65100;font-weight:bold'  # 위험 주황
    if '상승진입'   in v: return 'color:#ff8f00;font-weight:bold'                         # 기대 노랑
    if '구름대 위'  in v: return 'color:#ef5350'
    if '구름대 아래'in v: return 'color:#64b5f6'
    return 'color:#9e9e9e'


def style_rsi(val):
    v = str(val)
    if '과매도' in v: return 'color:#43a047;font-weight:bold'
    if '🔵' in v:     return 'color:#1e88e5'
    if '과매수' in v: return 'color:#e53935;font-weight:bold'
    if '🟡' in v:     return 'color:#fb8c00'
    return ''


def style_score(val):
    try:
        v = int(val)
        if v >= 5:  return 'color:white;background-color:#c62828;font-weight:bold'  # 적극매수
        if v >= 2:  return 'color:#ef5350;font-weight:bold'                         # 매수관심/추세추종
        if v >= -1: return 'color:#9e9e9e'                                          # 관망/눌림목
        if v >= -4: return 'color:#42a5f5;font-weight:bold'                         # 매도관심
        return 'color:white;background-color:#1565c0;font-weight:bold'              # 적극매도
    except:
        return ''


def style_cci(val):
    v = str(val)
    if '과매도탈출' in v: return 'color:#43a047;font-weight:bold'
    if '제로크로스' in v and '🔵' in v: return 'color:#1e88e5;font-weight:bold'
    if '제로데드'   in v: return 'color:#e53935;font-weight:bold'
    if '과매수탈출' in v: return 'color:#fb8c00;font-weight:bold'
    if '과매수'     in v: return 'color:#e53935'
    if '과매도'     in v: return 'color:#43a047'
    return ''


def style_pct(val):
    """등락률-이격률 색상 — 양수 빨강, 음수 파랑"""
    v = str(val).strip()
    if not v or v == '-':
        return ''
    try:
        if v.startswith('+'):
            return 'color:#ef5350'
        if v.startswith('-'):
            return 'color:#42a5f5'
        # % 기호 제거 후 숫자 판별
        num = float(v.replace('%', '').replace(',', ''))
        if num > 0:  return 'color:#ef5350'
        if num < 0:  return 'color:#42a5f5'
    except Exception:
        pass
    return ''


def compress_display(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()

    # 일목: 이모지+핵심단어만
    ichi_map = {
        "🔥 최근 상향돌파": "🔥상향돌파",
        "🧊 최근 하향이탈": "🧊하향이탈",
        "📈 구름대 위":     "📈위",
        "📉 구름대 아래":   "📉아래",
        "🌫️ 구름대 진입":   "🌫️진입",
    }
    d['일목(일봉)'] = d['일목(일봉)'].replace(ichi_map)

    # MA크로스: "5:🔥GC 20:📈^ 60:📈^" -> 이모지+숫자만
    def compress_ma(v):
        # "5:🔥GC" -> "5🔥" / "20:📈^" -> "20📈" 식으로
        parts = str(v).split(' ')
        out = []
        for p in parts:
            if ':' in p:
                num, sym = p.split(':', 1)
                # 첫 이모지/문자만
                short = sym[:2] if len(sym) >= 2 else sym
                out.append(f"{num}{short}")
        return ' '.join(out) if out else v
    d['MA크로스'] = d['MA크로스'].apply(compress_ma)

    # RSI: "65.3 🟡주의" -> "65.3🟡"
    def compress_rsi(v):
        s = str(v)
        for emoji, short in [('🟢과매도','🟢'), ('🔵관심','🔵'), ('⚪중립','⚪'), ('🟡주의','🟡'), ('🔴과매수','🔴')]:
            if emoji in s:
                num = s.replace(emoji,'').strip()
                return f"{num}{short}"
        return s
    d['RSI'] = d['RSI'].apply(compress_rsi)

    # BB상태: "⚡ 수축(폭발 대기) / 하단터치" -> "⚡수축/하단"
    def compress_bb(v):
        s = str(v)
        squeeze = '⚡' if '수축' in s else ('💥' if '팽창' in s else '➖')
        pos = ''
        if '상단터치' in s: pos = '상단'
        elif '하단터치' in s: pos = '하단'
        elif '밴드내부' in s: pos = '내부'
        return f"{squeeze}/{pos}" if pos else squeeze
    d['BB상태'] = d['BB상태'].apply(compress_bb)

    # 거래량: "2.3배 📈" -> 그대로 (이미 짧음)

    # 신호: 이모지 앞 공백 제거
    d['신호'] = d['신호'].str.strip()

    return d


def style_consec(val):
    v = str(val)
    if '연속양봉' in v: return 'color:#b71c1c;font-weight:bold'
    if '양봉'     in v: return 'color:#ef5350'
    if '연속음봉' in v: return 'color:#1565c0;font-weight:bold'
    if '음봉'     in v: return 'color:#42a5f5'
    return ''

def style_amount(val):
    v = str(val)
    if '⚠️' in v:   return 'color:#9e9e9e'
    if '대형' in v: return 'color:#b71c1c;font-weight:bold'
    if '중형' in v: return 'color:#e65100;font-weight:bold'
    if '소형' in v: return 'color:#f9a825'
    return ''

def style_investor(val):
    v = str(val)
    if '고비중' in v: return 'color:#b71c1c;font-weight:bold'
    if '중비중' in v: return 'color:#e65100;font-weight:bold'
    if '저비중' in v: return 'color:#f9a825'
    if '미미'   in v: return 'color:#9e9e9e'
    return ''

def style_slope(val):
    v = str(val)
    if '급등' in v: return 'color:#b71c1c;font-weight:bold'
    if '상승' in v: return 'color:#ef5350'
    if '급락' in v: return 'color:#0d47a1;font-weight:bold'
    if '하락' in v: return 'color:#42a5f5'
    return 'color:#9e9e9e'

def style_52week(val):
    v = str(val)
    if '신고가'   in v: return 'color:#b71c1c;font-weight:bold'
    if '고점근접' in v: return 'color:#ef5350'
    if '저점근접' in v: return 'color:#1565c0;font-weight:bold'
    return ''


def show_styled_dataframe(dataframe):
    if dataframe.empty:
        st.write("분석된 데이터가 없습니다.")
        return

    disp = compress_display(dataframe)
    dynamic_height = (len(disp) + 1) * 35 + 3

    def safe_subset(cols):
        return [c for c in cols if c in disp.columns]

    styled = (
        disp.style
        .map(style_signal,   subset=safe_subset(['신호']))
        .map(style_ichimoku, subset=safe_subset(['일목(일봉)']))
        .map(style_cci,      subset=safe_subset(['CCI']))
        .map(style_score,    subset=safe_subset(['총점']))
        .map(style_pct,      subset=safe_subset(['등락률', '이격률']))
        .map(lambda x: ('color:#b71c1c;font-weight:bold' if '🔥' in str(x) else
                        'color:#0d47a1;font-weight:bold' if '🧊' in str(x) else
                        'color:#ef5350' if '📈' in str(x) else
                        'color:#42a5f5' if '📉' in str(x) else ''),
             subset=safe_subset(['MA크로스']))
        .map(lambda x: ('color:#ef9a00;font-weight:bold' if '⚡' in str(x) else
                        'color:#26a69a;font-weight:bold' if '💥' in str(x) else
                        'color:#ef5350' if '상단' in str(x) else
                        'color:#42a5f5' if '하단' in str(x) else ''),
             subset=safe_subset(['BB상태']))
        .map(lambda x: ('color:#ef5350' if '📈' in str(x) else
                        'color:#64b5f6' if '📉' in str(x) else ''),
             subset=safe_subset(['거래량']))
        .map(style_consec,   subset=safe_subset(['연속봉']))
        .map(style_amount,   subset=safe_subset(['거래대금']))
        .map(style_investor, subset=safe_subset(['외국인지분율']))
    )

    col_cfg = {
        "코드":        st.column_config.TextColumn("코드",    width="small"),
        "총점":        st.column_config.NumberColumn("점수",  width="small"),
        "등락률":      st.column_config.TextColumn("등락",    width="small"),
        "이격률":      st.column_config.TextColumn("이격",    width="small"),
        "거래량":      st.column_config.TextColumn("거래량",  width="small"),
        "연속봉":      st.column_config.TextColumn("연속봉",  width="small"),
        "거래대금":    st.column_config.TextColumn("거래대금",width="small"),
        "차트":        st.column_config.LinkColumn("차트",    width="small", display_text="📊"),
        "신호":        st.column_config.TextColumn("신호",    width="medium"),
        "일목(일봉)":  st.column_config.TextColumn("일목",    width="medium"),
        "MA크로스":    st.column_config.TextColumn("MA",      width="medium"),
        "RSI":         st.column_config.TextColumn("RSI",     width="small"),
        "CCI":         st.column_config.TextColumn("CCI",     width="medium"),
        "BB상태":      st.column_config.TextColumn("BB",      width="small"),
        "종목명":      st.column_config.TextColumn("종목명",  width="medium"),
        "현재가":      st.column_config.NumberColumn("현재가",width="small"),
        "외국인지분율":st.column_config.TextColumn("외국인%", width="medium"),
        "52주위치":    st.column_config.TextColumn("52주",    width="medium"),
    }

    st.dataframe(
        styled,
        use_container_width=True,
        height=dynamic_height,
        column_config=col_cfg,
        hide_index=True
    )
    if dataframe.empty:
        st.write("분석된 데이터가 없습니다.")
        return

    disp = compress_display(dataframe)
    dynamic_height = (len(disp) + 1) * 35 + 3

    # 존재하는 컬럼만 스타일 적용 (컬럼 없으면 에러 방지)
    has_investor = '외국인지분율' in disp.columns
    has_slope    = '5MA기울기'    in disp.columns
    has_52w      = '52주위치'     in disp.columns

    styled = (
        disp.style
        .map(style_signal,   subset=['신호'])
        .map(style_ichimoku, subset=['일목(일봉)'])
        .map(style_rsi,      subset=['RSI'])
        .map(style_cci,      subset=['CCI'])
        .map(style_score,    subset=['총점'])
        .map(style_pct,      subset=['등락률', '이격률'])
        .map(lambda x: ('color:#ef5350;font-weight:bold' if '🔥' in str(x) else
                        'color:#42a5f5;font-weight:bold' if '🧊' in str(x) else
                        'color:#ef5350' if '📈' in str(x) else
                        'color:#42a5f5' if '📉' in str(x) else ''),
             subset=['MA크로스'])
        .map(lambda x: ('color:#ef9a00;font-weight:bold' if '⚡' in str(x) else
                        'color:#26a69a;font-weight:bold' if '💥' in str(x) else ''),
             subset=['BB상태'])
        .map(lambda x: ('color:#ef5350' if '📈' in str(x) else
                        'color:#64b5f6' if '📉' in str(x) else ''),
             subset=['거래량'])
    )
    if has_investor:
        styled = styled.map(style_investor, subset=['외국인지분율'])
    if has_slope:
        styled = styled.map(style_slope,    subset=['5MA기울기'])
    if has_52w:
        styled = styled.map(style_52week,   subset=['52주위치'])

col_cfg = {
    "코드":        st.column_config.TextColumn("코드"),
    "총점":        st.column_config.NumberColumn("점수"),
    "등락률":      st.column_config.TextColumn("등락"),
    "이격률":      st.column_config.TextColumn("이격"),
    "거래량":      st.column_config.TextColumn("거래량"),
    "연속봉":      st.column_config.TextColumn("연속봉"),
    "거래대금":    st.column_config.TextColumn("거래대금"),
    "차트":        st.column_config.LinkColumn("차트", display_text="📊"),
    "신호":        st.column_config.TextColumn("신호"),
    "일목(일봉)":  st.column_config.TextColumn("일목"),
    "MA크로스":    st.column_config.TextColumn("MA"),
    "CCI":         st.column_config.TextColumn("CCI"),
    "BB상태":      st.column_config.TextColumn("BB"),
    "종목명":      st.column_config.TextColumn("종목명"),
    "현재가":      st.column_config.NumberColumn("현재가"),
    "외국인지분율":st.column_config.TextColumn("외국인%"),
    }

    st.dataframe(
        styled,
        use_container_width=True,
        height=dynamic_height,
        column_config=col_cfg,
        hide_index=True
    )


# ---------------------------------------------
# UI
# ---------------------------------------------

st.title("🛡️ 스마트 데이터 스캐너 v4")

# -- 사이드바 ---------------------------------
st.sidebar.header("설정")
market         = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석 페이지 선택", options=list(range(1, 41)), default=[1])

st.sidebar.markdown("---")
use_investor = st.sidebar.checkbox(
    "📡 외인/기관 순매수 수집",
    value=True,
    help="종목당 추가 요청 1회 -> 분석 시간 약 30% 증가"
)

st.sidebar.markdown("---")
st.sidebar.markdown("""
**📊 12단계 신호 기준**

**[매수 계열]**
| 신호 | 의미 |
|------|------|
| 🔥 적극매수 | 구름대돌파+MACD-CCI 동시^ |
| 📈 매수관심 | 전환신호 2개^, 이격률 양호 |
| 🌱 진입준비 | 전환신호 1개, 타이밍 양호 |
| 🔄 바닥탐색 | 구름대 아래+회복 조짐 |

**[보유/중립 계열]**
| 신호 | 의미 |
|------|------|
| 🛡️ 홀딩유지 | 구름대 위, 이격률 적당 |
| 🔼 추세상승 | 많이 오름, 신규진입 주의 |
| 🌫️ 구름대내부 | 방향 불명확 횡보 |
| ⏸️ 관망 | 신호 없음 |

**[위험/하락 계열]**
| 신호 | 의미 |
|------|------|
| ⚠️ 구름대주의 | 위->구름대 하락진입 |
| 🔻 하락가속 | 구름대아래+MACD-CCIv |
| 🔽 추세하락 | 구름대아래+이격률v |
| 📉 매도관심 | 하락전환 총점<=-3 |
| 🧊 적극매도 | 이탈+동시하락 총점<=-5 |

**📐 점수 구성 (v4)**
- 구름대 돌파/진입방향 : +/-3
- MACD 전환-기울기    : +/-2
- CCI 전환            : +/-2
- 이격률              : +/-3
- 거래량              : +/-1
- **외국인 지분율      : +/-1** <- NEW (30%^ 우량, 5%v 주의)
- **5MA 기울기        : +/-1** <- NEW
- **52주 위치 보정    : +/-1** <- NEW
""")

start_btn = st.sidebar.button("🚀 분석 시작")

# -- 메트릭 -----------------------------------
st.subheader("📊 진단 및 필터링")
c1, c2, c3, c4, c5, c6 = st.columns(6)
total_metric    = c1.empty()
buy_metric      = c2.empty()
entry_metric    = c3.empty()
caution_metric  = c4.empty()
fall_metric     = c5.empty()
sell_metric     = c6.empty()

total_metric.metric("전체",     "0개")
buy_metric.metric("매수계열",   "0개")
entry_metric.metric("진입준비", "0개")
caution_metric.metric("구름대주의","0개")
fall_metric.metric("하락계열",  "0개")
sell_metric.metric("매도관심v", "0개")

# -- 필터 버튼 --------------------------------
fb1,fb2,fb3,fb4,fb5,fb6,fb7,fb8 = st.columns(8)
if 'filter' not in st.session_state:
    st.session_state.filter = "전체"

if fb1.button("🔄전체",      use_container_width=True): st.session_state.filter = "전체"
if fb2.button("🔥📈매수",    use_container_width=True): st.session_state.filter = "매수"
if fb3.button("🌱진입준비",  use_container_width=True): st.session_state.filter = "진입준비"
if fb4.button("🔄바닥탐색",  use_container_width=True): st.session_state.filter = "바닥탐색"
if fb5.button("🛡️홀딩",      use_container_width=True): st.session_state.filter = "홀딩"
if fb6.button("⚠️구름주의",  use_container_width=True): st.session_state.filter = "구름대주의"
if fb7.button("🔻하락가속",  use_container_width=True): st.session_state.filter = "하락가속"
if fb8.button("📉🧊매도",    use_container_width=True): st.session_state.filter = "매도"

st.markdown("---")
result_title    = st.empty()
main_result_area = st.empty()


# -- 분석 시작 --------------------------------
def update_metrics(df):
    buy_kw   = '적극매수|매수관심'
    fall_kw  = '하락가속|추세하락|적극매도'
    sell_kw  = '매도관심|적극매도'
    total_metric.metric("전체",       f"{len(df)}개")
    buy_metric.metric("매수계열",
        f"{len(df[df['신호'].str.contains(buy_kw, regex=True)])}개")
    entry_metric.metric("진입준비",
        f"{len(df[df['신호'].str.contains('진입준비|바닥탐색', regex=True)])}개")
    caution_metric.metric("구름대주의",
        f"{len(df[df['신호'].str.contains('구름대주의')])}개")
    fall_metric.metric("하락계열",
        f"{len(df[df['신호'].str.contains(fall_kw, regex=True)])}개")
    sell_metric.metric("매도관심v",
        f"{len(df[df['신호'].str.contains(sell_kw, regex=True)])}개")


def apply_filter(df, f):
    if f == "매수":
        return df[df['신호'].str.contains("적극매수|매수관심", regex=True)]
    elif f == "진입준비":
        return df[df['신호'].str.contains("진입준비")]
    elif f == "바닥탐색":
        return df[df['신호'].str.contains("바닥탐색")]
    elif f == "홀딩":
        return df[df['신호'].str.contains("홀딩유지|추세상승", regex=True)]
    elif f == "구름대주의":
        return df[df['신호'].str.contains("구름대주의")]
    elif f == "하락가속":
        return df[df['신호'].str.contains("하락가속|추세하락", regex=True)]
    elif f == "매도":
        return df[df['신호'].str.contains("매도")]
    return df


if start_btn:
    st.session_state.filter = "전체"
    market_df = get_market_sum_pages(selected_pages, market)

    if not market_df.empty:
        results = []
        st.session_state['df_all'] = pd.DataFrame()

        # -- 외국인 지분율 사전 수집 ----------
        foreign_dict = {}
        if use_investor:
            with st.spinner(f"📡 {market} 외국인 보유 비율 수집 중... (최초 1회, 약 20~30초)"):
                foreign_dict = load_foreign_ratio_all(market=market, max_pages=40)
            st.info(f"✅ 외국인 지분율 {len(foreign_dict):,}개 종목 수집 완료")

        progress_bar = st.progress(0, text="분석 시작...")

        for i, (_, row) in enumerate(market_df.iterrows()):
            res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'],
                                foreign_dict=foreign_dict,
                                fetch_investor=use_investor)
            if res:
                results.append(res)
                df_all = pd.DataFrame(results, columns=COLUMNS)
                # 총점 기준 정렬 (높은 점수 위로)
                df_all = df_all.sort_values('총점', ascending=False).reset_index(drop=True)
                st.session_state['df_all'] = df_all

                update_metrics(df_all)

                display_df = apply_filter(df_all, st.session_state.filter)
                result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter} / {len(display_df)}개)")
                with main_result_area:
                    show_styled_dataframe(display_df)

            progress_bar.progress((i + 1) / len(market_df),
                                  text=f"분석 중: {row['종목명']} ({i+1}/{len(market_df)})")

        progress_bar.empty()
        st.success("✅ 분석 완료!")


# -- 필터 버튼 동작 (분석 후) -----------------
if not start_btn and 'df_all' in st.session_state:
    df = st.session_state['df_all']
    display_df = apply_filter(df, st.session_state.filter)

    update_metrics(df)
    result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter} / {len(display_df)}개)")
    with main_result_area:
        show_styled_dataframe(display_df)

    if not display_df.empty:
        email_summary = display_df[['종목명', '현재가', '총점', '신호', '일목(일봉)', 'RSI']].to_string(index=False)
        encoded_body  = urllib.parse.quote(f"주식 분석 리포트\n\n{email_summary}")
        mailto_url    = f"mailto:?subject=주식리포트&body={encoded_body}"
        st.markdown(
            f'<a href="{mailto_url}" target="_self" style="text-decoration:none;">'
            f'<div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;'
            f'text-align:center;font-weight:bold;">📧 현재 리스트 Outlook 전송</div></a>',
            unsafe_allow_html=True
        )

elif 'df_all' not in st.session_state:
    with main_result_area:
        st.info("왼쪽 사이드바에서 '분석 시작' 버튼을 눌러주세요.")
